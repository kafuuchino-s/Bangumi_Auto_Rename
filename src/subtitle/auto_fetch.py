from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

        return self._execute_fetch(
            task_uuid=task_uuid,
            task_data=task_data,
            record_data=record_data,
            scan_scope=scan_scope,
            missing_videos=missing_videos,
            mapping_only=False,
        )

    def process_task_mapping(
        self,
        task_uuid: str,
        *,
        missing_videos_override: Optional[List[Path]] = None,
    ) -> Dict[str, Any]:
        """映射模式入口：搜帖→选帖→选包→下载真包到临时目录→字幕→视频配对映射，
        **不落盘到媒体库**。对齐 rename 链路 mapping-only 语义。

        与 ``process_task`` 共用选帖循环（``_execute_fetch``），差异：
        - missing_videos 来源：``missing_videos_override``（虚拟目标路径，不必 exists）
          优先；否则回退 ``_collect_videos_missing_subtitles``（生产采集，依赖落地视频）。
        - accepted 后下载到临时目录 ``data/subtitle_upload/auto_fetch_mapping/<uuid>/``，
          调 ``processor.process_mapping``（mapping_only=True，不落媒体库）。
        - 产物写 ``data/task/<uuid>.subtitle_fetch_mapping.json``（新后缀，区别于生产）。

        四态语义不变（accepted/fail_closed/need_confirm/invalid）。用于 auto_fetch
        映射模式 smoke 等不碰媒体库、不需真实落地视频的场景。
        """
        task_data = get_task(task_uuid)
        record_data = get_record(task_uuid)

        if not task_data:
            return {"status": "skipped", "reason": "task_not_found"}
        if not record_data:
            return self._persist_mapping_status(
                task_uuid,
                {"status": "skipped", "reason": "record_not_found"},
            )

        scan_scope = self._resolve_scan_scope(task_data, record_data)
        if missing_videos_override is not None:
            missing_videos = list(missing_videos_override)
        else:
            missing_videos = self._collect_videos_missing_subtitles(
                scan_scope, record_data
            )
        if not missing_videos:
            return self._persist_mapping_status(
                task_uuid,
                {
                    "status": "skipped",
                    "reason": "subtitle_already_exists",
                    "scan_scope_type": scan_scope["type"],
                    "scan_scope_root": scan_scope.get("root"),
                    "missing_video_count": 0,
                },
            )

        return self._execute_fetch(
            task_uuid=task_uuid,
            task_data=task_data,
            record_data=record_data,
            scan_scope=scan_scope,
            missing_videos=missing_videos,
            mapping_only=True,
        )

    def _execute_fetch(
        self,
        *,
        task_uuid: str,
        task_data: Dict[str, Any],
        record_data: dict[str, object],
        scan_scope: Dict[str, Any],
        missing_videos: List[Path],
        mapping_only: bool = False,
    ) -> Dict[str, Any]:
        """Pi 驱动选帖/选包（process_task / process_task_mapping 共用，对齐重命名链路）。

        构造 task_context（含 BGM subject 名）+ workspace（MV/KW 事实卡含 BGM 名）->
        调 run_auto_fetch_case_agent（Pi 后端），Pi 自己
        search_candidates_batch(BGM 名) / load_candidate_packages_batch /
        inspect_package 多轮取证后 submit。不预爬。
        accepted -> 用 Pi 返回的 provider 对象下载 + processor。
        fail_closed/need_confirm/invalid -> 合格结果不落盘。
        """
        persist = (
            self._persist_mapping_status if mapping_only else self._persist_status
        )
        download_root = (
            SUBTITLE_UPLOAD_PATH / "auto_fetch_mapping" / task_uuid
            if mapping_only
            else SUBTITLE_UPLOAD_PATH / "auto_fetch" / task_uuid
        )
        # processor 配对串行（self.processor 单例，测试 mock 兼容 + 避免 Node sidecar
        # 资源爆炸 + extractor temp_dir 不撞）；下载并发（见 _execute_fetch 阶段1）。
        processor_fn = (
            self.processor.process_mapping if mapping_only else self.processor.process
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

        # 确定性搜索关键词（方向 A：BGM subject 名优先，不 AI 扩词）。
        # 喂给 Pi workspace 作 KW 事实卡，Pi 在 skill 里决定用哪些搜。
        keywords = self._build_search_keywords(task_context, missing_videos)
        if not keywords:
            return persist(
                task_uuid,
                {
                    "status": "failed",
                    "reason": "empty_search_keyword",
                    "scan_scope_type": scan_scope["type"],
                    "scan_scope_root": scan_scope.get("root"),
                    "missing_video_count": len(missing_videos),
                },
            )

        case_agent_workspace = self._build_case_agent_workspace(
            task_uuid=task_uuid,
            task_data=task_context,
            record_data=record_data,
            scan_scope=scan_scope,
            missing_videos=missing_videos,
            keywords=keywords,
        )
        if case_agent_workspace is None:
            return persist(
                task_uuid,
                {
                    "status": "failed",
                    "reason": "workspace_build_failed",
                    "scan_scope_type": scan_scope["type"],
                    "scan_scope_root": scan_scope.get("root"),
                    "missing_video_count": len(missing_videos),
                },
            )

        # Pi 驱动：不预爬，Pi 自己 search_candidates_batch/load/inspect/submit。
        entry_result = run_auto_fetch_case_agent(
            workspace=case_agent_workspace,
            candidates=[],
            task_data=task_context,
            ai_client=self.ai_client,
            candidate_summaries=[],
            backend="pi",
            provider=self.provider,
        )
        status = str(entry_result.get("status") or "invalid")
        case_agent_snapshot = entry_result.get("snapshot") or {}
        ai_result = entry_result.get("ai_rerank_result")
        package_ai_result = entry_result.get("package_ai_result")
        selected_candidate_ref = entry_result.get("selected_candidate_ref") or ""
        selected_package_ref = entry_result.get("selected_package_ref") or ""

        if status != "accepted":
            skip_reason = "no_usable_candidate"
            if status == "fail_closed":
                skip_reason = str(entry_result.get("reason_kind") or "pi_fail_closed")
            return persist(
                task_uuid,
                {
                    "status": "skipped" if status in ("fail_closed", "need_confirm") else "failed",
                    "reason": skip_reason,
                    "scan_scope_type": scan_scope["type"],
                    "scan_scope_root": scan_scope.get("root"),
                    "missing_video_count": len(missing_videos),
                    "pipeline_mode": "auto_fetch_case_agent_primary",
                    "case_agent_status": status,
                    "case_agent_snapshot": case_agent_snapshot,
                    "ai_used": ai_result is not None,
                    "ai_rerank_result": ai_result,
                    "package_ai_result": package_ai_result,
                    "selected_candidate_ref": selected_candidate_ref,
                    "selected_package_ref": selected_package_ref,
                    "missing_videos": [str(p) for p in missing_videos],
                },
            )

        # 多季覆盖：Pi 可能 submit_complete 多个 selection（每 subject 一帖一包）。
        # entry_result.selections / selections_provider 为多 selection 列表；旧单
        # submit_package 路径 selections 为空 → 回退到单 selected_candidate/package。
        selections_raw = entry_result.get("selections") or []
        selections_provider_raw = entry_result.get("selections_provider") or []
        selected_candidate = entry_result.get("selected_provider_candidate")
        selected_package = entry_result.get("selected_provider_package")
        selected_summary = entry_result.get("selected_candidate") or {}

        # 构造统一 selection 列表：[(candidate_obj, package_obj, language,
        # bangumi_subject_id, download_url), ...]。download_url 是 Pi 在
        # submit_package(link_url=...) 指定的具体附件（AI-first 附件选择），透传给
        # provider.download 按此 url 下，固定层不打分选附件。
        fetch_units: List[Tuple[Any, Any, str, int, str]] = []
        if isinstance(selections_raw, list) and selections_raw and isinstance(
            selections_provider_raw, list
        ) and len(selections_provider_raw) == len(selections_raw):
            for sel_dict, prov in zip(selections_raw, selections_provider_raw):
                if not isinstance(sel_dict, dict) or not isinstance(prov, dict):
                    continue
                cand_obj = prov.get("candidate")
                pkg_obj = prov.get("package")
                if cand_obj is None:
                    continue
                lang = str(sel_dict.get("language") or "")
                sid = int(sel_dict.get("bangumi_subject_id") or 0)
                dl_url = str(sel_dict.get("download_url") or "")
                fetch_units.append((cand_obj, pkg_obj, lang, sid, dl_url))
        # 兼容旧单 selection：selections 为空时用顶层 selected_candidate/package
        if not fetch_units and selected_candidate is not None:
            sel_summary = selected_summary or {}
            fetch_units.append(
                (
                    selected_candidate,
                    selected_package,
                    str(sel_summary.get("language") or ""),
                    int(sel_summary.get("bangumi_subject_id") or 0),
                    str(sel_summary.get("download_url") or ""),
                )
            )

        if not fetch_units:
            return persist(
                task_uuid,
                {
                    "status": "failed",
                    "reason": "no_selected_provider_candidate",
                    "pipeline_mode": "auto_fetch_case_agent_primary",
                    "case_agent_status": "invalid",
                    "case_agent_snapshot": case_agent_snapshot,
                    "missing_videos": [str(p) for p in missing_videos],
                    "scan_scope_type": scan_scope["type"],
                    "scan_scope_root": scan_scope.get("root"),
                    "missing_video_count": len(missing_videos),
                },
            )

        # 逐 selection 下载 + processor 配对，合并 mapping。
        # accepted = ≥1 unit 下载成功 + processor success（有映射）；全部失败才 failed。
        merged_mappings: List[Dict[str, Any]] = []
        merged_unmatched: List[Dict[str, Any]] = []
        merged_no_target: List[Dict[str, Any]] = []
        merged_matched_count = 0
        selection_summaries: List[Dict[str, Any]] = []
        any_success = False
        last_failure_reason: Optional[str] = None
        last_processor_case_agent_status = ""

        # 并发执行各 selection 的下载，processor 配对串行（字幕 Case Agent Pi sidecar
        # 是 Node 子进程，并发多个放大资源 + 共享 extractor temp_dir 会 stem 撞）。
        # 下载并发是确定性收益（acgrip 网络 160s → 并发后 ~50s）；processor 用
        # self.processor 单例串行调用（测试 mock 兼容 + 避免 5 个 Node sidecar 资源爆炸）。
        # 主线程串行合并结果，selection_summaries 按 idx 排序保序。
        concurrency = max(1, int(cm_get("subtitle_auto_fetch_selection_concurrency", 3) or 1))
        concurrency = min(concurrency, len(fetch_units)) if fetch_units else 1
        unit_results: List[Optional[Dict[str, Any]]] = [None] * len(fetch_units)

        def _download_one(idx_unit: Tuple[int, Tuple[Any, Any, str, int, str]]) -> Dict[str, Any]:
            idx, (cand_obj, pkg_obj, lang, sid, dl_url) = idx_unit
            unit_download_dir = (
                download_root / f"sel_{idx}" if len(fetch_units) > 1 else download_root
            )
            download_result = self.provider.download(
                cand_obj,
                unit_download_dir,
                package=pkg_obj,
                download_url=dl_url or None,
            )
            resolved_pkg = download_result.selected_package or pkg_obj
            return {
                "index": idx,
                "sid": sid,
                "cand_obj": cand_obj,
                "pkg_obj": resolved_pkg,
                "lang": lang,
                "download_result": download_result,
            }

        # 阶段1：并发下载（各 selection 独立 download_dir，无共享态冲突）
        downloaded: List[Optional[Dict[str, Any]]] = [None] * len(fetch_units)
        if concurrency <= 1 or len(fetch_units) <= 1:
            for idx, unit in enumerate(fetch_units):
                downloaded[idx] = _download_one((idx, unit))
        else:
            with ThreadPoolExecutor(max_workers=concurrency) as ex:
                futures = {
                    ex.submit(_download_one, (idx, unit)): idx
                    for idx, unit in enumerate(fetch_units)
                }
                for fut in as_completed(futures):
                    idx = futures[fut]
                    downloaded[idx] = fut.result()

        # 阶段2：串行 processor 配对（self.processor 单例，测试 mock 兼容 + 避免
        # Node sidecar 资源爆炸 + extractor temp_dir 不撞）
        for idx in range(len(fetch_units)):
            r = downloaded[idx]
            if r is None:
                continue
            cand_obj = r["cand_obj"]
            pkg_obj = r["pkg_obj"]
            lang = r["lang"]
            sid = r["sid"]
            download_result = r["download_result"]
            if (
                download_result.status != "success"
                or not download_result.downloaded_path
            ):
                unit_results[idx] = {
                    "index": idx, "sid": sid, "cand_obj": cand_obj, "pkg_obj": pkg_obj,
                    "lang": lang, "download_result": download_result,
                    "processor_result": None, "unit_status": "",
                    "unit_processor_case_agent_status": "",
                    "status_label": "download_failed",
                    "reason": download_result.error or download_result.status,
                }
                continue
            processor_result = processor_fn(
                download_result.downloaded_path,
                target_task_uuid=task_uuid,
            )
            unit_status = str(processor_result.get("status") or "")
            unit_cas = str(processor_result.get("case_agent_status") or "")
            status_label = "success" if unit_status == "success" else "processor_failed"
            unit_results[idx] = {
                "index": idx, "sid": sid, "cand_obj": cand_obj, "pkg_obj": pkg_obj,
                "lang": lang, "download_result": download_result,
                "processor_result": processor_result, "unit_status": unit_status,
                "unit_processor_case_agent_status": unit_cas,
                "status_label": status_label,
                "reason": (
                    "processor_fail_closed"
                    if (status_label != "success" and unit_cas == "fail_closed")
                    else (processor_result.get("error") if status_label != "success" else None)
                ),
            }

        # 串行合并（主线程，无并发写共享态）
        for idx in range(len(fetch_units)):
            r = unit_results[idx]
            if r is None:
                continue
            cand_obj = r["cand_obj"]
            pkg_obj = r["pkg_obj"]
            lang = r["lang"]
            sid = r["sid"]
            download_result = r["download_result"]
            processor_result = r["processor_result"]
            unit_status = r["unit_status"]
            unit_cas = r["unit_processor_case_agent_status"]
            status_label = r["status_label"]
            last_processor_case_agent_status = (
                unit_cas or last_processor_case_agent_status
            )
            if status_label == "download_failed":
                last_failure_reason = r["reason"]
                selection_summaries.append({
                    "index": idx,
                    "bangumi_subject_id": sid,
                    "status": "download_failed",
                    "reason": last_failure_reason,
                    "selected_candidate": self._candidate_to_dict(cand_obj),
                    "selected_package": self._package_to_dict(pkg_obj),
                    "selected_language": lang,
                    "download_url": download_result.download_url,
                    "download_attempts": getattr(
                        download_result, "download_attempts", 1
                    ),
                })
                continue
            if status_label == "success":
                any_success = True
                merged_mappings.extend(
                    list(processor_result.get("mappings") or [])
                )
                merged_unmatched.extend(
                    list(processor_result.get("unmatched") or [])
                )
                merged_no_target.extend(
                    list(processor_result.get("no_target_videos") or [])
                )
                merged_matched_count += int(
                    processor_result.get("matched_count") or 0
                )
                selection_summaries.append({
                    "index": idx,
                    "bangumi_subject_id": sid,
                    "status": "success",
                    "selected_candidate": self._candidate_to_dict(cand_obj),
                    "selected_package": self._package_to_dict(pkg_obj),
                    "selected_language": lang,
                    "download_url": download_result.download_url,
                    "downloaded_path": str(download_result.downloaded_path),
                    "processor_result": processor_result,
                    "matched_count": processor_result.get("matched_count"),
                    "download_attempts": getattr(
                        download_result, "download_attempts", 1
                    ),
                })
            else:
                # processor fail_closed 视为"该包未配对成功"合格结果，但本 unit 失败
                last_failure_reason = r["reason"]
                selection_summaries.append({
                    "index": idx,
                    "bangumi_subject_id": sid,
                    "status": "processor_failed",
                    "selected_candidate": self._candidate_to_dict(cand_obj),
                    "selected_package": self._package_to_dict(pkg_obj),
                    "selected_language": lang,
                    "download_url": download_result.download_url,
                    "downloaded_path": str(download_result.downloaded_path),
                    "processor_result": processor_result,
                    "processor_case_agent_status": unit_cas or None,
                    "reason": last_failure_reason,
                    "download_attempts": getattr(
                        download_result, "download_attempts", 1
                    ),
                })

        # 合并去重（修复多 subject 同帖重复配对）：一帖覆盖多 subject 时，多个
        # selection 各自下载同一帖/同附件，processor 各配对，合并后同一条字幕被配
        # 多次（0002 前後篇 05-08 各配 2 次）。按 (video, language) 去重保留一条，
        # matched_count 按去重后计。防御性去重——即使 Pi 侧 selection 已正确（B9
        # submit_package bangumi_subject_id 修正），processor 重复配对也兜底。
        if merged_mappings:
            seen_pairs: set[tuple[str, str]] = set()
            deduped_mappings: List[Dict[str, Any]] = []
            for m in merged_mappings:
                if not isinstance(m, dict):
                    deduped_mappings.append(m)
                    continue
                key = (str(m.get("video") or ""), str(m.get("language") or ""))
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                deduped_mappings.append(m)
            if len(deduped_mappings) != len(merged_mappings):
                logger.info(
                    f"[字幕自动抓取] 多 selection 合并去重：{len(merged_mappings)} → "
                    f"{len(deduped_mappings)} 条映射（同 video+language 重复配对已合并）"
                )
            merged_mappings = deduped_mappings
            merged_matched_count = len(merged_mappings)

        if any_success:
            return persist(
                task_uuid,
                {
                    "status": "success",
                    "reason": None,
                    "selections_count": len(fetch_units),
                    "selections": selection_summaries,
                    "ai_used": ai_result is not None,
                    "ai_rerank_result": ai_result,
                    "package_ai_result": package_ai_result,
                    "pipeline_mode": "auto_fetch_case_agent_primary",
                    "case_agent_status": status,
                    "case_agent_snapshot": case_agent_snapshot,
                    "missing_videos": [str(p) for p in missing_videos],
                    "scan_scope_type": scan_scope["type"],
                    "scan_scope_root": scan_scope.get("root"),
                    "missing_video_count": len(missing_videos),
                    # 合并的映射产物（多 selection 汇总）
                    "mappings": merged_mappings,
                    "unmatched": merged_unmatched,
                    "no_target_videos": merged_no_target,
                    "matched_count": merged_matched_count,
                },
            )

        # 全部 unit 失败
        return persist(
            task_uuid,
            {
                "status": "failed",
                "reason": last_failure_reason or "all_selections_failed",
                "selections_count": len(fetch_units),
                "selections": selection_summaries,
                "ai_used": ai_result is not None,
                "ai_rerank_result": ai_result,
                "package_ai_result": package_ai_result,
                "pipeline_mode": "auto_fetch_case_agent_primary",
                "case_agent_status": status,
                "case_agent_snapshot": case_agent_snapshot,
                "processor_case_agent_status": last_processor_case_agent_status or None,
                "failure_reason": (
                    "processor_fail_closed"
                    if last_processor_case_agent_status == "fail_closed"
                    else last_failure_reason
                ),
                "missing_videos": [str(p) for p in missing_videos],
                "scan_scope_type": scan_scope["type"],
                "scan_scope_root": scan_scope.get("root"),
                "missing_video_count": len(missing_videos),
                "mappings": merged_mappings,
                "unmatched": merged_unmatched,
                "no_target_videos": merged_no_target,
                "matched_count": merged_matched_count,
            },
        )

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
        """构造搜索关键词（方向 A：优先 Bangumi subject 名，不用 TMDB 名）。

        字幕组命名常按 Bangumi 中文名/日文名发帖，比 TMDB 本地化标题更贴合搜索。
        顺序：bgm_subject_name / bgm_subject_name_cn（重命名落盘字段）→ name →
        源目录标题 → 兜底 missing video 文件名。确定性变体由
        ``_append_search_keyword_variants`` 生成（空格/数字分隔/ascii-only），
        **不 AI 扩词**（避免引入不确定性）。
        """
        fallback_videos = list(missing_videos)
        del missing_videos
        search_mode = str(
            task_data.get("subtitle_auto_fetch_search_mode") or "auto"
        ).strip()
        if search_mode != "auto":
            logger.info(f"[字幕自动抓取] 当前搜索模式: {search_mode}")

        keywords: List[str] = []
        # 方向 A：BGM subject 名优先（auto_fetch 搜索词来源）
        for key in ("bgm_subject_name_cn", "bgm_subject_name", "name"):
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

    def _persist_mapping_status(
        self,
        task_uuid: str,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """映射模式产物持久化：写 ``.subtitle_fetch_mapping.json``，不污染生产
        task JSON 的 subtitle_fetch_* 字段。对齐 _persist_status 但独立后缀。

        mapping_only 模式是 smoke/验证场景，产物落 data/task/<uuid>.subtitle_fetch_mapping.json
        供复盘，不回写 task JSON（避免与生产字幕抓取状态字段混淆）。
        """
        persisted_result = {
            "parent_task_uuid": task_uuid,
            "mapping_only": True,
            **result,
        }
        record_path = TASK_PATH / f"{task_uuid}.subtitle_fetch_mapping.json"
        with open(record_path, "w", encoding="utf-8") as f:
            json.dump(persisted_result, f, ensure_ascii=False, indent=4)

        logger.info(
            f"[字幕自动抓取-映射] 任务 {task_uuid} 结果: {result.get('status')}"
        )
        return result


def cm_get(key: str, default: Any = None) -> Any:
    from ..config.config_manager import cm

    value = cm.get_config(key)
    return default if value in (None, "") else value
