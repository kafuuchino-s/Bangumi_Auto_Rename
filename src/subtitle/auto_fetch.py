from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..ai.client import AIClient
from ..logger import logger
from ..rename.utils import VIDEO_SUFFIX
from ..utils.path import SUBTITLE_UPLOAD_PATH, TASK_PATH
from ..utils.utils import get_record, get_task, write_task
from .auto_fetch_case_agent import (
    AutoFetchCaseWorkspace,
    build_auto_fetch_case_workspace,
    build_deterministic_keyword_cards,
    build_missing_video_cards,
    build_scan_scope_card,
    run_auto_fetch_case_agent,
)
from .extractor import SUBTITLE_EXTENSIONS
from .processor import SubtitleProcessor
from .providers import ACGRIPProvider, SubtitleCandidate, SubtitleThreadPackage


class SubtitleAutoFetcher:
    def __init__(self) -> None:
        self.provider = ACGRIPProvider()
        self.processor = SubtitleProcessor()
        self.ai_client = AIClient()

    def process_task(self, task_uuid: str) -> Dict[str, Any]:
        task_data = get_task(task_uuid)
        record_data = get_record(task_uuid)

        if not task_data:
            return {"status": "skipped", "reason": "task_not_found"}
        if not record_data:
            return self._persist_status(
                task_uuid,
                {
                    "status": "skipped",
                    "reason": "record_not_found",
                },
            )

        scan_scope = self._resolve_scan_scope(task_data, record_data)
        missing_videos = self._collect_videos_missing_subtitles(scan_scope, record_data)
        if not missing_videos:
            return self._persist_status(
                task_uuid,
                {
                    "status": "skipped",
                    "reason": "subtitle_already_exists",
                    "scan_scope_type": scan_scope["type"],
                    "scan_scope_root": scan_scope.get("root"),
                    "missing_video_count": 0,
                },
            )

        task_context = dict(task_data)
        task_context["subtitle_auto_fetch_preferred_language"] = cm_get(
            "subtitle_auto_fetch_preferred_language", "zh-CN"
        )
        task_context["subtitle_auto_fetch_search_mode"] = cm_get(
            "subtitle_auto_fetch_search_mode", "auto"
        )
        task_context["missing_video_count"] = len(missing_videos)

        source_path_value = str(task_data.get("path") or "").strip()
        task_context["subtitle_auto_fetch_source_path_basename"] = (
            Path(source_path_value).name if source_path_value else ""
        )
        task_context["subtitle_auto_fetch_source_title_hint"] = (
            self._extract_title_from_source_path(source_path_value)
            if source_path_value
            else ""
        )
        task_context["subtitle_auto_fetch_source_video_names"] = [
            Path(str(source)).name for source in record_data.keys()
        ]
        task_context["subtitle_auto_fetch_missing_target_video_names"] = [
            path.name for path in missing_videos
        ]
        task_context["subtitle_auto_fetch_scan_scope_root"] = scan_scope.get("root")
        task_context["subtitle_auto_fetch_is_season_zero_tv"] = bool(
            not task_data.get("is_movie") and task_data.get("season_id") == 0
        )

        keywords = self._build_search_keywords(task_context, missing_videos)
        if not keywords:
            return self._persist_status(
                task_uuid,
                {
                    "status": "failed",
                    "reason": "empty_search_keyword",
                    "scan_scope_type": scan_scope["type"],
                    "scan_scope_root": scan_scope.get("root"),
                    "missing_video_count": len(missing_videos),
                },
            )

        # Case Agent 事实卡工作区（Phase 2：轻 gate + 单轮 AI 适配）
        case_agent_enabled = bool(
            cm_get("subtitle_auto_fetch_case_agent_primary_enabled", True)
        )
        case_agent_workspace = self._build_case_agent_workspace(
            task_uuid=task_uuid,
            task_data=task_data,
            record_data=record_data,
            scan_scope=scan_scope,
            missing_videos=missing_videos,
            keywords=keywords,
        )

        limit = int(cm_get("subtitle_auto_fetch_candidate_limit", 10) or 10)
        last_result: Optional[Dict[str, Any]] = None
        ai_queries_attempted = False
        pending_keywords = list(keywords)
        tried_keywords: List[str] = []

        while pending_keywords:
            keyword = pending_keywords.pop(0)
            tried_keywords.append(keyword)
            task_context["subtitle_auto_fetch_active_search_keyword"] = keyword
            candidates = self.provider.search(keyword, limit=limit)
            if not candidates:
                last_result = {
                    "status": "failed",
                    "reason": "no_candidates",
                    "search_keyword": keyword,
                    "missing_videos": [str(p) for p in missing_videos],
                    "scan_scope_type": scan_scope["type"],
                    "scan_scope_root": scan_scope.get("root"),
                    "missing_video_count": len(missing_videos),
                }
                if not pending_keywords and not ai_queries_attempted:
                    pending_keywords.extend(
                        self._build_ai_search_keywords(task_context, tried_keywords)
                    )
                    ai_queries_attempted = True
                continue

            candidates = [
                self.provider.prepare_candidate(candidate) for candidate in candidates
            ]
            candidates = [
                self.provider.load_thread_packages(candidate) for candidate in candidates
            ]
            candidate_summaries = self._candidate_summaries(candidates)

            # Phase 2：Case Agent 入口分发选帖/选包（轻 gate + 单轮 AI）
            case_agent_status: Optional[str] = None
            case_agent_snapshot: Any = None
            case_select = self._select_via_case_agent(
                task_context=task_context,
                candidates=candidates,
                candidate_summaries=candidate_summaries,
                workspace=case_agent_workspace if case_agent_enabled else None,
            )
            if case_select.get("legacy_fallback"):
                # Case Agent 不可用 / 关闭：回退旧单轮 AI 选帖/选包
                selected_candidate, ai_result = self._select_candidate(
                    task_context,
                    candidates,
                    candidate_summaries,
                )
                if selected_candidate is None:
                    rejection_by_ai = bool((ai_result or {}).get("should_use") is False)
                    last_result = {
                        "status": "skipped" if rejection_by_ai else "failed",
                        "reason": (
                            "candidate_ai_rejected"
                            if rejection_by_ai
                            else "no_usable_candidate"
                        ),
                        "search_keyword": keyword,
                        "candidate_summaries": candidate_summaries,
                        "ranked_candidates": candidate_summaries,
                        "ai_used": ai_result is not None,
                        "ai_rerank_result": ai_result,
                        "pipeline_mode": "auto_fetch_legacy_compat",
                        "missing_videos": [str(p) for p in missing_videos],
                        "scan_scope_type": scan_scope["type"],
                        "scan_scope_root": scan_scope.get("root"),
                        "missing_video_count": len(missing_videos),
                    }
                    if not pending_keywords and not ai_queries_attempted:
                        pending_keywords.extend(
                            self._build_ai_search_keywords(task_context, tried_keywords)
                        )
                        ai_queries_attempted = True
                    continue

                package_summaries = self._thread_package_summaries(
                    selected_candidate.thread_packages
                )
                selected_package, package_ai_result = self._select_thread_package(
                    task_context,
                    selected_candidate,
                    selected_candidate.thread_packages,
                    package_summaries,
                )
            else:
                # Case Agent 主路径
                case_agent_status = str(case_select.get("case_agent_status") or "")
                case_agent_snapshot = case_select.get("case_agent_snapshot")
                ai_result = case_select.get("ai_result")
                package_ai_result = case_select.get("package_ai_result")
                package_summaries = case_select.get("package_summaries") or []
                selected_candidate = case_select.get("selected_candidate")
                selected_package = case_select.get("selected_package")
                if selected_candidate is None:
                    # 候选/选包被拒或无候选：合格 fail_closed，按旧 reason 映射
                    skip_reason = str(case_select.get("skip_reason") or "no_usable_candidate")
                    rejection_by_ai = skip_reason in {
                        "candidate_ai_rejected",
                        "package_ai_rejected",
                    }
                    last_result = {
                        "status": "skipped" if rejection_by_ai else "failed",
                        "reason": skip_reason,
                        "search_keyword": keyword,
                        "candidate_summaries": candidate_summaries,
                        "ranked_candidates": candidate_summaries,
                        "ai_used": ai_result is not None,
                        "ai_rerank_result": ai_result,
                        "package_ai_result": package_ai_result,
                        "pipeline_mode": "auto_fetch_case_agent_primary",
                        "case_agent_status": case_agent_status,
                        "case_agent_snapshot": case_agent_snapshot,
                        "missing_videos": [str(p) for p in missing_videos],
                        "scan_scope_type": scan_scope["type"],
                        "scan_scope_root": scan_scope.get("root"),
                        "missing_video_count": len(missing_videos),
                    }
                    if not pending_keywords and not ai_queries_attempted:
                        pending_keywords.extend(
                            self._build_ai_search_keywords(task_context, tried_keywords)
                        )
                        ai_queries_attempted = True
                    continue

            if (
                selected_package is None
                and (package_ai_result or {}).get("should_use") is False
            ):
                last_result = {
                    "status": "skipped",
                    "reason": "package_ai_rejected",
                    "search_keyword": keyword,
                    "selected_candidate": self._candidate_to_dict(selected_candidate),
                    "selected_package": None,
                    "selected_language": (ai_result or {}).get(
                        "language_assessment"
                    ),
                    "rule_score": None,
                    "ai_used": ai_result is not None,
                    "ai_rerank_result": ai_result,
                    "package_ai_result": package_ai_result,
                    "candidate_summaries": candidate_summaries,
                    "ranked_candidates": candidate_summaries,
                    "package_summaries": package_summaries,
                    "pipeline_mode": (
                        "auto_fetch_case_agent_primary"
                        if case_agent_status
                        else "auto_fetch_legacy_compat"
                    ),
                    "case_agent_status": case_agent_status,
                    "case_agent_snapshot": case_agent_snapshot,
                    "missing_videos": [str(p) for p in missing_videos],
                    "scan_scope_type": scan_scope["type"],
                    "scan_scope_root": scan_scope.get("root"),
                    "missing_video_count": len(missing_videos),
                    "pages_scanned": selected_candidate.pages_scanned,
                    "pagination_truncated": selected_candidate.pagination_truncated,
                }
                if not pending_keywords and not ai_queries_attempted:
                    pending_keywords.extend(
                        self._build_ai_search_keywords(task_context, tried_keywords)
                    )
                    ai_queries_attempted = True
                continue

            download_dir = SUBTITLE_UPLOAD_PATH / "auto_fetch" / task_uuid
            download_result = self.provider.download(
                selected_candidate,
                download_dir,
                package=selected_package,
            )
            selected_package = download_result.selected_package or selected_package
            if (
                download_result.status != "success"
                or not download_result.downloaded_path
            ):
                return self._persist_status(
                    task_uuid,
                    {
                        "status": "failed",
                        "reason": download_result.error or download_result.status,
                        "search_keyword": keyword,
                        "selected_candidate": self._candidate_to_dict(
                            selected_candidate
                        ),
                        "selected_package": self._package_to_dict(selected_package),
                        "selected_language": (ai_result or {}).get(
                            "language_assessment"
                        ),
                        "rule_score": None,
                        "ai_used": ai_result is not None,
                        "ai_rerank_result": ai_result,
                        "package_ai_result": package_ai_result,
                        "candidate_summaries": candidate_summaries,
                        "ranked_candidates": candidate_summaries,
                        "package_summaries": package_summaries,
                        "download_url": download_result.download_url,
                        "missing_videos": [str(p) for p in missing_videos],
                        "scan_scope_type": scan_scope["type"],
                        "scan_scope_root": scan_scope.get("root"),
                        "missing_video_count": len(missing_videos),
                        "pages_scanned": selected_candidate.pages_scanned,
                        "pagination_truncated": (
                            selected_candidate.pagination_truncated
                        ),
                    },
                )

            processor_result = self.processor.process(
                download_result.downloaded_path,
                target_task_uuid=task_uuid,
            )
            # fail_closed 解读对齐：processor 的 fail_closed 对外映射 need_confirm +
            # case_agent_status 审计；auto_fetch 视为"该包未配对成功"的合格可重试结果。
            processor_case_agent_status = str(
                processor_result.get("case_agent_status") or ""
            )
            if processor_result.get("status") == "success":
                return self._persist_status(
                    task_uuid,
                    {
                        "status": "success",
                        "reason": None,
                        "search_keyword": keyword,
                        "candidate_summaries": candidate_summaries,
                        "ranked_candidates": candidate_summaries,
                        "package_summaries": package_summaries,
                        "selected_candidate": self._candidate_to_dict(
                            selected_candidate
                        ),
                        "selected_package": self._package_to_dict(selected_package),
                        "selected_language": (ai_result or {}).get(
                            "language_assessment"
                        ),
                        "rule_score": None,
                        "ai_used": ai_result is not None,
                        "ai_rerank_result": ai_result,
                        "package_ai_result": package_ai_result,
                        "download_url": download_result.download_url,
                        "downloaded_path": str(download_result.downloaded_path),
                        "processor_result": processor_result,
                        "pipeline_mode": (
                            "auto_fetch_case_agent_primary"
                            if case_agent_status
                            else "auto_fetch_legacy_compat"
                        ),
                        "case_agent_status": case_agent_status,
                        "case_agent_snapshot": case_agent_snapshot,
                        "missing_videos": [str(p) for p in missing_videos],
                        "scan_scope_type": scan_scope["type"],
                        "scan_scope_root": scan_scope.get("root"),
                        "missing_video_count": len(missing_videos),
                        "pages_scanned": selected_candidate.pages_scanned,
                        "pagination_truncated": selected_candidate.pagination_truncated,
                    },
                )

            last_result = {
                "status": "failed",
                "reason": processor_result.get("error"),
                "search_keyword": keyword,
                "candidate_summaries": candidate_summaries,
                "ranked_candidates": candidate_summaries,
                "package_summaries": package_summaries,
                "selected_candidate": self._candidate_to_dict(selected_candidate),
                "selected_package": self._package_to_dict(selected_package),
                "selected_language": (ai_result or {}).get("language_assessment"),
                "rule_score": None,
                "ai_used": ai_result is not None,
                "ai_rerank_result": ai_result,
                "package_ai_result": package_ai_result,
                "download_url": download_result.download_url,
                "downloaded_path": str(download_result.downloaded_path),
                "processor_result": processor_result,
                "pipeline_mode": (
                    "auto_fetch_case_agent_primary"
                    if case_agent_status
                    else "auto_fetch_legacy_compat"
                ),
                "case_agent_status": case_agent_status,
                "case_agent_snapshot": case_agent_snapshot,
                # 透传 processor 侧的 case_agent_status（fail_closed 等）供审计
                "processor_case_agent_status": processor_case_agent_status or None,
                "failure_reason": (
                    "processor_fail_closed"
                    if processor_case_agent_status == "fail_closed"
                    else processor_result.get("error")
                ),
                "missing_videos": [str(p) for p in missing_videos],
                "scan_scope_type": scan_scope["type"],
                "scan_scope_root": scan_scope.get("root"),
                "missing_video_count": len(missing_videos),
                "pages_scanned": selected_candidate.pages_scanned,
                "pagination_truncated": selected_candidate.pagination_truncated,
            }
            # fail_closed 对齐：processor fail_closed / need_confirm 均视为该包未配对成功
            # 的合格可重试结果，触发换关键词重试。
            should_retry_with_next_keyword = (
                processor_result.get("status") == "need_confirm"
                or processor_case_agent_status == "fail_closed"
                or processor_result.get("error")
                == "AI 无法确定匹配的动漫，请手动选择"
            )
            if should_retry_with_next_keyword:
                if not pending_keywords and not ai_queries_attempted:
                    pending_keywords.extend(
                        self._build_ai_search_keywords(task_context, tried_keywords)
                    )
                    ai_queries_attempted = True
                continue
            return self._persist_status(task_uuid, last_result)

        if last_result is None:
            last_result = {
                "status": "failed",
                "reason": "no_candidates",
                "search_keyword": keywords[-1],
                "missing_videos": [str(p) for p in missing_videos],
                "scan_scope_type": scan_scope["type"],
                "scan_scope_root": scan_scope.get("root"),
                "missing_video_count": len(missing_videos),
            }
        return self._persist_status(task_uuid, last_result)


    def _build_ai_search_keywords(
        self,
        task_data: Dict[str, Any],
        tried_keywords: List[str],
    ) -> List[str]:
        use_ai_rerank = bool(cm_get("subtitle_auto_fetch_use_ai_rerank", True))
        if not use_ai_rerank or not self.ai_client.is_available():
            return []

        ai_task_data = dict(task_data)
        ai_task_data["subtitle_auto_fetch_existing_keywords"] = list(tried_keywords)
        ai_queries = self.ai_client.generate_subtitle_search_queries(ai_task_data)
        if not ai_queries:
            return []

        keywords: List[str] = []
        for query in ai_queries:
            self._append_search_keyword_variants(keywords, query)

        filtered = self._filter_ai_search_keywords(task_data, keywords, tried_keywords)
        if filtered:
            logger.info(f"[字幕自动抓取] AI补充搜索词: {filtered}")
        return filtered

    def _filter_ai_search_keywords(
        self,
        task_data: Dict[str, Any],
        ai_keywords: List[str],
        existing_keywords: List[str],
    ) -> List[str]:
        existing_folded = {
            str(item).strip().casefold() for item in existing_keywords if str(item).strip()
        }
        source_title_hint = str(
            task_data.get("subtitle_auto_fetch_source_title_hint") or ""
        ).strip()
        is_specific_special = bool(
            task_data.get("subtitle_auto_fetch_is_season_zero_tv") and source_title_hint
        )
        title_tokens = self._tokenize_search_text(
            str(task_data.get("tmdb_name") or task_data.get("name") or "")
        )
        source_tokens = self._tokenize_search_text(source_title_hint)

        filtered: List[str] = []
        seen = set(existing_folded)
        for keyword in ai_keywords:
            candidate = str(keyword or "").strip()
            if not candidate:
                continue
            folded = candidate.casefold()
            if folded in seen:
                continue
            candidate_tokens = self._tokenize_search_text(candidate)
            if is_specific_special and candidate_tokens:
                has_source_specific_token = bool(source_tokens & candidate_tokens)
                only_franchise_level = bool(
                    title_tokens
                    and candidate_tokens <= title_tokens
                    and not has_source_specific_token
                )
                if only_franchise_level:
                    continue
            filtered.append(candidate)
            seen.add(folded)
        return filtered

    @staticmethod
    def _tokenize_search_text(value: str) -> set[str]:
        return {
            token.casefold()
            for token in re.findall(r"[A-Za-z0-9一-龥ぁ-んァ-ヶ]+", str(value or ""))
            if token.strip()
        }

    def _resolve_scan_scope(
        self,
        task_data: Dict[str, Any],
        record_data: dict[str, object],
    ) -> Dict[str, Any]:
        if bool(task_data.get("is_mixed_parent")):
            return {
                "type": "task",
                "root": None,
                "source": "mixed_parent_record",
            }

        is_movie = bool(task_data.get("is_movie"))
        target_root = str(task_data.get("target_root") or "").strip()
        if target_root:
            return {
                "type": "movie" if is_movie else "series",
                "root": target_root,
                "source": "task_data",
            }

        inferred_root: Optional[Path] = None
        if is_movie:
            inferred_root = self._infer_movie_root_from_record(record_data)
        else:
            inferred_root = self._infer_series_root_from_record(record_data)

        if inferred_root:
            return {
                "type": "movie" if is_movie else "series",
                "root": str(inferred_root),
                "source": "record_inferred",
            }

        return {
            "type": "movie" if is_movie else "task",
            "root": None,
            "source": "record_only",
        }

    def _collect_videos_missing_subtitles(
        self,
        scan_scope: Dict[str, Any],
        record_data: dict[str, object],
    ) -> List[Path]:
        scope_type = str(scan_scope.get("type") or "task")
        root_value = str(scan_scope.get("root") or "").strip()
        root_path = Path(root_value) if root_value else None

        if scope_type == "series" and root_path:
            return self._collect_series_videos_missing_subtitles(root_path)
        if scope_type == "movie" and root_path:
            return self._collect_movie_videos_missing_subtitles(root_path, record_data)
        return self._collect_task_target_videos_missing_subtitles(record_data)

    def _collect_task_target_videos_missing_subtitles(
        self,
        record_data: dict[str, object],
    ) -> List[Path]:
        missing: List[Path] = []
        for target in record_data.values():
            if not isinstance(target, str):
                continue
            video_path = Path(target)
            if not self._is_candidate_video(video_path):
                continue
            if self._has_sidecar_subtitle(video_path):
                continue
            missing.append(video_path)
        return missing

    def _collect_series_videos_missing_subtitles(self, series_root: Path) -> List[Path]:
        if not series_root.exists() or not series_root.is_dir():
            return []

        missing: List[Path] = []
        season_dirs = [
            path
            for path in series_root.iterdir()
            if path.is_dir() and self._is_series_season_dir(path)
        ]
        for season_dir in sorted(season_dirs):
            for video_path in sorted(season_dir.iterdir()):
                if not self._is_candidate_video(video_path):
                    continue
                if self._has_sidecar_subtitle(video_path):
                    continue
                missing.append(video_path)
        return missing

    def _collect_movie_videos_missing_subtitles(
        self,
        movie_root: Path,
        record_data: dict[str, object],
    ) -> List[Path]:
        if not movie_root.exists() or not movie_root.is_dir():
            return self._collect_task_target_videos_missing_subtitles(record_data)

        target_paths = {
            Path(target).resolve()
            for target in record_data.values()
            if isinstance(target, str)
            if self._is_candidate_video(Path(target))
        }
        missing: List[Path] = []
        for video_path in sorted(movie_root.iterdir()):
            if not self._is_candidate_video(video_path):
                continue
            if target_paths and video_path.resolve() not in target_paths:
                continue
            if self._has_sidecar_subtitle(video_path):
                continue
            missing.append(video_path)
        return missing

    def _infer_series_root_from_record(
        self,
        record_data: dict[str, object],
    ) -> Optional[Path]:
        for target in record_data.values():
            if not isinstance(target, str):
                continue
            target_path = Path(target)
            if not self._is_candidate_video(target_path):
                continue
            parent = target_path.parent
            if self._is_series_season_dir(parent):
                return parent.parent
        return None

    def _infer_movie_root_from_record(
        self,
        record_data: dict[str, object],
    ) -> Optional[Path]:
        for target in record_data.values():
            if not isinstance(target, str):
                continue
            target_path = Path(target)
            if self._is_candidate_video(target_path):
                return target_path.parent
        return None

    def _is_series_season_dir(self, path: Path) -> bool:
        if not path.exists() or not path.is_dir():
            return False
        name = path.name.strip()
        if name.lower() == "extra":
            return False
        return bool(re.fullmatch(r"Season\s*\d+", name, flags=re.IGNORECASE))

    def _is_candidate_video(self, video_path: Path) -> bool:
        return video_path.exists() and video_path.suffix.lower() in VIDEO_SUFFIX

    def _has_sidecar_subtitle(self, video_path: Path) -> bool:
        for ext in SUBTITLE_EXTENSIONS:
            matches = list(video_path.parent.glob(f"{video_path.stem}*{ext}"))
            if matches:
                return True
        return False

    def _build_search_keywords(
        self,
        task_data: Dict[str, Any],
        missing_videos: List[Path],
    ) -> List[str]:
        fallback_videos = list(missing_videos)
        del missing_videos
        search_mode = str(
            task_data.get("subtitle_auto_fetch_search_mode") or "auto"
        ).strip()
        if search_mode != "auto":
            logger.info(f"[字幕自动抓取] 当前搜索模式: {search_mode}")

        keywords: List[str] = []
        for key in ("tmdb_name", "name", "original_name"):
            self._append_search_keyword_variants(keywords, task_data.get(key))

        source_path = str(task_data.get("path") or "").strip()
        if source_path:
            self._append_search_keyword_variants(
                keywords,
                self._extract_title_from_source_path(source_path),
            )

        if not keywords and fallback_videos:
            self._append_search_keyword_variants(keywords, fallback_videos[0].stem)
        return keywords

    @staticmethod
    def _append_search_keyword_variants(
        keywords: List[str],
        value: Any,
    ) -> None:
        raw = str(value or "").strip()
        if not raw:
            return

        variants = [raw]
        normalized = re.sub(r"[：:~～_/]+", " ", raw)
        normalized = re.sub(r"\s+", " ", normalized).strip(" -")
        if normalized:
            variants.append(normalized)

        digit_spaced = re.sub(r"(?<=\D)(\d+)", r" \1", normalized or raw)
        digit_spaced = re.sub(r"\s+", " ", digit_spaced).strip()
        if digit_spaced:
            variants.append(digit_spaced)

        ascii_only = " ".join(re.findall(r"[A-Za-z0-9]+", normalized or raw))
        if ascii_only:
            variants.append(ascii_only)

        seen = {item.casefold() for item in keywords}
        for variant in variants:
            candidate = str(variant or "").strip()
            if not candidate:
                continue
            folded = candidate.casefold()
            if folded in seen:
                continue
            keywords.append(candidate)
            seen.add(folded)

    @staticmethod
    def _extract_title_from_source_path(source_path: str) -> str:
        name = Path(source_path).name.strip()
        if not name:
            return ""

        title = name
        while True:
            stripped = re.sub(r"^\[[^\]]+\]\s*", "", title).strip()
            if stripped == title:
                break
            title = stripped

        while True:
            stripped = re.sub(r"\s*\[[^\]]+\]\s*$", "", title).strip()
            stripped = re.sub(r"\s*\([^)]*\)\s*$", "", stripped).strip()
            if stripped == title:
                break
            title = stripped

        title = re.sub(r"^(劇場版|劇場アニメ|TVアニメ)\s*", "", title).strip()
        return title

    def _build_case_agent_workspace(
        self,
        *,
        task_uuid: str,
        task_data: Dict[str, Any],
        record_data: dict[str, object],
        scan_scope: Dict[str, Any],
        missing_videos: List[Path],
        keywords: List[str],
    ) -> Optional[AutoFetchCaseWorkspace]:
        """构建 auto_fetch Case Agent 事实卡工作区（Phase 2）。"""
        try:
            scope_card = build_scan_scope_card(scan_scope)
            missing_cards = build_missing_video_cards(
                task_data=task_data,
                record_data=record_data,
                missing_videos=missing_videos,
            )
            keyword_cards = build_deterministic_keyword_cards(keywords)
            return build_auto_fetch_case_workspace(
                task_uuid=task_uuid,
                scan_scope=scope_card,
                missing_videos=missing_cards,
                keywords=keyword_cards,
            )
        except Exception as exc:
            logger.warning(f"[字幕自动抓取] 构建 Case Agent 工作区失败: {exc}")
            return None

    def _select_via_case_agent(
        self,
        *,
        task_context: Dict[str, Any],
        candidates: List[SubtitleCandidate],
        candidate_summaries: List[Dict[str, Any]],
        workspace: Optional[AutoFetchCaseWorkspace],
    ) -> Dict[str, Any]:
        """通过 Case Agent 入口做选帖/选包决策（Phase 2 单轮 + 轻 gate）。

        返回统一 dict 供主循环消费：
        - accepted：selected_candidate / selected_package 非空，case_agent_status=accepted
        - fail_closed（候选/包被拒/无候选）：selected_* 为 None，case_agent_status=fail_closed，
          ``skip_reason`` 映射旧 reason（candidate_ai_rejected/no_usable_candidate/
          package_ai_rejected/candidate_gate_rejected/package_gate_rejected）
        - need_confirm / invalid：selected_* 为 None，对应 case_agent_status

        Case Agent 不可用（workspace 为 None）时回退旧链路（caller 据返回的
        ``legacy_fallback`` 标记走旧 ``_select_candidate``/``_select_thread_package``）。
        """
        result: Dict[str, Any] = {
            "selected_candidate": None,
            "ai_result": None,
            "selected_package": None,
            "package_ai_result": None,
            "package_summaries": [],
            "case_agent_status": None,
            "case_agent_snapshot": None,
            "skip_reason": None,
            "legacy_fallback": False,
        }
        if workspace is None:
            result["legacy_fallback"] = True
            return result

        # 每次调用重置候选事实卡：CD/PK ref 从 1 开始，与本批 candidates 对齐
        # （workspace 在 process_task 里只建一次，跨关键词复用，候选必须按本次搜索重置）
        workspace.candidates = []

        entry_result = run_auto_fetch_case_agent(
            workspace=workspace,
            candidates=candidates,
            task_data=task_context,
            ai_client=self.ai_client,
            candidate_summaries=candidate_summaries,
            backend=str(cm_get("subtitle_auto_fetch_case_agent_backend", "single_shot") or "single_shot"),
            provider=self.provider,
        )
        status = str(entry_result.get("status") or "invalid")
        snapshot = entry_result.get("snapshot") or {}
        result["case_agent_status"] = status
        result["case_agent_snapshot"] = snapshot
        result["ai_result"] = entry_result.get("ai_rerank_result")
        result["package_ai_result"] = entry_result.get("package_ai_result")

        if status != "accepted":
            # 映射 fail_closed reason_kind -> 旧 reason（供主循环 skip/failed 分支）
            reason_kind = str(snapshot.get("reason_kind") or "")
            # 显式 AI 拒绝 / 轻 gate 拒绝：保持 fail_closed（合格 skip/failed），不回退规则
            explicit_rejection = reason_kind in {
                "candidate_ai_rejected",
                "package_ai_rejected",
                "candidate_gate_rejected",
                "package_gate_rejected",
            }
            if explicit_rejection:
                skip_map = {
                    "candidate_ai_rejected": "candidate_ai_rejected",
                    "package_ai_rejected": "package_ai_rejected",
                    "candidate_gate_rejected": "no_usable_candidate",
                    "package_gate_rejected": "no_usable_candidate",
                }
                result["skip_reason"] = skip_map.get(reason_kind, "no_usable_candidate")
                # package_ai_rejected：候选已被接受，恢复 selected_candidate 供主循环
                # package-reject 分支的审计字段（pages_scanned 等）使用。
                if reason_kind == "package_ai_rejected":
                    selected_ref = str(entry_result.get("selected_candidate_ref") or "")
                    candidate_index = self._index_candidates_by_ref(candidates, workspace)
                    selected_candidate = candidate_index.get(selected_ref)
                    if selected_candidate is not None:
                        result["selected_candidate"] = selected_candidate
                        result["package_summaries"] = self._thread_package_summaries(
                            selected_candidate.thread_packages
                        )
                return result
            # AI 未给出可用选择（no_usable_candidate / no_candidates / need_confirm /
            # invalid）：single_shot 兼容兜底——回退旧单轮 AI + 规则选帖/选包
            # （对齐 plan：保留 _pick_best_package_by_rules 作 single_shot 兼容兜底）。
            result["legacy_fallback"] = True
            return result

        selected_ref = str(entry_result.get("selected_candidate_ref") or "")
        package_ref = str(entry_result.get("selected_package_ref") or "")
        candidate_index = self._index_candidates_by_ref(candidates, workspace)
        selected_candidate = candidate_index.get(selected_ref)
        if selected_candidate is None:
            result["case_agent_status"] = "invalid"
            result["skip_reason"] = "no_usable_candidate"
            return result
        result["selected_candidate"] = selected_candidate
        package_summaries = self._thread_package_summaries(
            selected_candidate.thread_packages
        )
        result["package_summaries"] = package_summaries
        # 从选中候选的 thread_packages 按 package_ref 取回 provider 对象
        result["selected_package"] = self._find_provider_package_by_ref(
            selected_candidate, workspace, package_ref
        )
        return result

    @staticmethod
    def _index_candidates_by_ref(
        candidates: List[SubtitleCandidate],
        workspace: AutoFetchCaseWorkspace,
    ) -> Dict[str, SubtitleCandidate]:
        """CD ref -> provider SubtitleCandidate（按 workspace.candidates 顺序对齐）。"""
        index: Dict[str, SubtitleCandidate] = {}
        ws_refs = workspace.candidate_refs
        for idx, candidate in enumerate(candidates):
            if idx < len(ws_refs):
                index[ws_refs[idx]] = candidate
        return index

    @staticmethod
    def _find_provider_package_by_ref(
        candidate: SubtitleCandidate,
        workspace: AutoFetchCaseWorkspace,
        package_ref: str,
    ) -> Optional[SubtitleThreadPackage]:
        """PK ref -> provider SubtitleThreadPackage（按 candidate.packages 顺序对齐）。"""
        ws_candidate = workspace.candidate_by_ref()
        # 通过 candidate 的 packages 顺序与 workspace card 顺序对齐
        ws_pkg_refs = []
        for cd in workspace.candidates:
            if cd.detail_url == candidate.detail_url:
                ws_pkg_refs = [pkg.ref for pkg in cd.packages]
                break
        for idx, pkg in enumerate(candidate.thread_packages):
            ref = ws_pkg_refs[idx] if idx < len(ws_pkg_refs) else ''
            if ref == package_ref:
                return pkg
        return None

    def _candidate_summaries(
        self,
        candidates: List[SubtitleCandidate],
    ) -> List[Dict[str, Any]]:
        return [
            {
                "index": index,
                "title": candidate.title,
                "detail_url": candidate.detail_url,
                "source": candidate.source,
                "snippet": candidate.snippet,
                "attachment_count": len(candidate.attachment_urls),
                "external_count": len(candidate.external_urls),
                "metadata": candidate.metadata,
                "pages_scanned": candidate.pages_scanned,
                "pagination_truncated": candidate.pagination_truncated,
                "package_count": len(candidate.thread_packages),
                "package_summaries": self._thread_package_summaries(
                    candidate.thread_packages
                ),
            }
            for index, candidate in enumerate(candidates)
        ]

    def _select_candidate(
        self,
        task_data: Dict[str, Any],
        candidates: List[SubtitleCandidate],
        candidate_summaries: List[Dict[str, Any]],
    ) -> tuple[Optional[SubtitleCandidate], Optional[Dict[str, Any]]]:
        if not candidates:
            return None, None

        use_ai_rerank = bool(cm_get("subtitle_auto_fetch_use_ai_rerank", True))
        if use_ai_rerank and self.ai_client.is_available():
            ai_choice = self.ai_client.choose_subtitle_candidate(
                task_data,
                candidate_summaries,
            )
            if ai_choice:
                if ai_choice.should_use:
                    selected_index = ai_choice.selected_index
                    if 0 <= selected_index < len(candidates):
                        return candidates[selected_index], ai_choice.model_dump()
                else:
                    return None, ai_choice.model_dump()

        return candidates[0], None

    def _thread_package_summaries(
        self,
        packages: List[SubtitleThreadPackage],
    ) -> List[Dict[str, Any]]:
        summaries: List[Dict[str, Any]] = []
        for index, package in enumerate(packages):
            link_summary = " | ".join(
                filter(
                    None,
                    [
                        link.filename_hint or link.label or link.url
                        for link in package.links[:5]
                    ],
                )
            )
            summaries.append(
                {
                    "index": index,
                    "package_id": package.package_id,
                    "page_number": package.page_number,
                    "floor_label": package.floor_label,
                    "post_author": package.post_author,
                    "post_time": package.post_time,
                    "has_direct_download": package.has_direct_download,
                    "package_flags": package.package_flags,
                    "post_text": package.post_text,
                    "context_text": package.context_text,
                    "link_summary": link_summary,
                    "link_count": len(package.links),
                }
            )
        return summaries

    def _select_thread_package(
        self,
        task_data: Dict[str, Any],
        candidate: SubtitleCandidate,
        packages: List[SubtitleThreadPackage],
        package_summaries: List[Dict[str, Any]],
    ) -> tuple[Optional[SubtitleThreadPackage], Optional[Dict[str, Any]]]:
        if not packages:
            return None, None

        ai_choice = None
        if self.ai_client.is_available() and package_summaries:
            ai_choice = self.ai_client.choose_subtitle_thread_package(
                task_data,
                self._candidate_to_dict(candidate),
                package_summaries,
            )
            if ai_choice:
                if ai_choice.should_use:
                    selected_index = ai_choice.selected_index
                    if 0 <= selected_index < len(packages):
                        return packages[selected_index], ai_choice.model_dump()
                else:
                    return None, ai_choice.model_dump()

        fallback = self._pick_best_package_by_rules(packages)
        return fallback, ai_choice.model_dump() if ai_choice else None

    def _pick_best_package_by_rules(
        self,
        packages: List[SubtitleThreadPackage],
    ) -> Optional[SubtitleThreadPackage]:
        scored = []
        for index, package in enumerate(packages):
            if not package.links:
                continue
            scored.append((self._score_package(package), -index, package))
        if not scored:
            return None
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return scored[0][2]

    def _score_package(self, package: SubtitleThreadPackage) -> int:
        flags = set(package.package_flags or [])
        score = 0
        if package.has_direct_download:
            score += 100
        if "batch" in flags:
            score += 60
        if "simplified" in flags:
            score += 40
        if "bilingual" in flags:
            score += 20
        if "revision" in flags:
            score += 15
        if "traditional" in flags and "simplified" not in flags:
            score -= 10
        if "patch" in flags:
            score -= 50
        if "special" in flags:
            score -= 45
        if "font" in flags:
            score -= 90
        score += min(len(package.links), 5) * 3
        if package.page_number > 1 and "revision" in flags:
            score += 10
        return score

    def _candidate_to_dict(self, candidate: SubtitleCandidate) -> Dict[str, Any]:
        return {
            "title": candidate.title,
            "detail_url": candidate.detail_url,
            "source": candidate.source,
            "publish_time": candidate.publish_time,
            "author": candidate.author,
            "forum": candidate.forum,
            "snippet": candidate.snippet,
            "attachment_urls": candidate.attachment_urls,
            "external_urls": candidate.external_urls,
            "metadata": candidate.metadata,
            "pages_scanned": candidate.pages_scanned,
            "pagination_truncated": candidate.pagination_truncated,
            "thread_packages": [
                self._package_to_dict(package) for package in candidate.thread_packages
            ],
        }

    def _package_to_dict(
        self,
        package: Optional[SubtitleThreadPackage],
    ) -> Optional[Dict[str, Any]]:
        if package is None:
            return None
        return {
            "package_id": package.package_id,
            "page_number": package.page_number,
            "floor_label": package.floor_label,
            "post_author": package.post_author,
            "post_time": package.post_time,
            "post_text": package.post_text,
            "context_text": package.context_text,
            "has_direct_download": package.has_direct_download,
            "package_flags": package.package_flags,
            "links": [
                {
                    "url": link.url,
                    "kind": link.kind,
                    "label": link.label,
                    "filename_hint": link.filename_hint,
                    "is_direct_download": link.is_direct_download,
                }
                for link in package.links
            ],
        }

    def _persist_status(self, task_uuid: str, result: Dict[str, Any]) -> Dict[str, Any]:
        task_data = get_task(task_uuid)
        if task_data:
            task_data["subtitle_fetch_attempted"] = True
            task_data["subtitle_fetch_status"] = result.get("status")
            task_data["subtitle_fetch_provider"] = "acgrip"
            task_data["subtitle_fetch_error"] = result.get("reason")
            task_data["subtitle_fetch_search_keyword"] = result.get("search_keyword")
            task_data["subtitle_fetch_selected_candidate"] = result.get(
                "selected_candidate"
            )
            task_data["subtitle_fetch_language"] = result.get("selected_language")
            task_data["subtitle_fetch_ai_used"] = result.get("ai_used", False)
            ai_result = result.get("ai_rerank_result") or {}
            task_data["subtitle_fetch_ai_confidence"] = ai_result.get("confidence")
            task_data["subtitle_fetch_rule_score"] = result.get("rule_score")
            task_data["subtitle_fetch_scope_type"] = result.get("scan_scope_type")
            task_data["subtitle_fetch_scope_root"] = result.get("scan_scope_root")
            task_data["subtitle_fetch_missing_video_count"] = result.get(
                "missing_video_count"
            )
            task_data["subtitle_fetch_selected_package"] = result.get(
                "selected_package"
            )
            package_ai_result = result.get("package_ai_result") or {}
            task_data["subtitle_fetch_package_ai_confidence"] = package_ai_result.get(
                "confidence"
            )
            task_data["subtitle_fetch_pages_scanned"] = result.get("pages_scanned")
            task_data["subtitle_fetch_pagination_truncated"] = result.get(
                "pagination_truncated"
            )
            # Case Agent 审计透传（对齐 rename / 字幕导入）
            task_data["subtitle_fetch_pipeline_mode"] = result.get("pipeline_mode")
            task_data["subtitle_fetch_case_agent_status"] = result.get(
                "case_agent_status"
            )
            task_data["subtitle_fetch_failure_reason"] = result.get("failure_reason")
            task_data["subtitle_fetch_processor_case_agent_status"] = result.get(
                "processor_case_agent_status"
            )
            write_task(task_uuid, task_data)

        persisted_result = {
            "parent_task_uuid": task_uuid,
            **result,
        }
        record_path = TASK_PATH / f"{task_uuid}.subtitle_fetch.json"
        with open(record_path, "w", encoding="utf-8") as f:
            json.dump(persisted_result, f, ensure_ascii=False, indent=4)

        logger.info(
            f"[字幕自动抓取] 任务 {task_uuid} 结果: {result.get('status')}"
        )
        return result


def cm_get(key: str, default: Any = None) -> Any:
    from ..config.config_manager import cm

    value = cm.get_config(key)
    return default if value in (None, "") else value
