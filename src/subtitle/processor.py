"""
字幕处理器主模块

协调解压、扫描任务记录、调用 AI、文件传输的全流程。
支持多季度/多任务的字幕压缩包处理。
"""

import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, TypedDict

from ..ai.client import AIClient
from ..config.config_manager import cm
from ..logger import logger
from ..rename.trans import Trans
from ..utils.path import RECORD_PATH, TASK_PATH
from .extractor import ExtractedSubtitle, SubtitleExtractor
from .syncer import FFsubsyncRunner

# 语言代码映射：字幕组常用标签 -> (Emby标准代码, 是否简体)
LANGUAGE_MAP: Dict[str, Tuple[str, bool]] = {
    # 简体中文
    "chs": ("zh-CN", True),
    "sc": ("zh-CN", True),
    "gb": ("zh-CN", True),
    "简": ("zh-CN", True),
    "简体": ("zh-CN", True),
    "简中": ("zh-CN", True),
    "zh-hans": ("zh-CN", True),
    "zh-cn": ("zh-CN", True),
    "cn": ("zh-CN", True),
    "chinese": ("zh-CN", True),  # 默认简体
    # 繁体中文
    "cht": ("zh-TW", False),
    "tc": ("zh-TW", False),
    "big5": ("zh-TW", False),
    "繁": ("zh-TW", False),
    "繁体": ("zh-TW", False),
    "繁中": ("zh-TW", False),
    "zh-hant": ("zh-TW", False),
    "zh-tw": ("zh-TW", False),
    "tw": ("zh-TW", False),
    "zh-hk": ("zh-HK", False),
    "hk": ("zh-HK", False),
    # 日语
    "jp": ("ja", False),
    "jpn": ("ja", False),
    "ja": ("ja", False),
    "japanese": ("ja", False),
    "日": ("ja", False),
    "日语": ("ja", False),
    # 英语
    "en": ("en", False),
    "eng": ("en", False),
    "english": ("en", False),
    # 韩语
    "ko": ("ko", False),
    "kor": ("ko", False),
    "korean": ("ko", False),
}


class ProcessedTask(TypedDict):
    uuid: str
    title: str
    year: int | None
    season: int | None
    target_dir: str
    target_root: str
    videos: list[str]
    video_targets: dict[str, str]
    # 目标文件名 -> 重命名前 local 原始文件名（AI 匹配证据，非合法落点）。
    source_videos: dict[str, str]
    is_movie: bool
    # 目标文件名 -> 该视频所属 BGM subject 的 arc 名 (name, name_cn)，来自
    # task_data.bgm_video_subject_map + bgm_subjects。多季同 episode 配对的关键
    # 证据（S02E01 vs S03E01 靠 arc 名 無限列車編 / 遊郭編 区分）。
    video_arc_names: dict[str, tuple[str, str]]


class SyncSummary(TypedDict):
    enabled: bool
    mode: str
    attempted: int
    success: int
    fallback: int
    skipped: int
    disabled: int
    failed: int
    strict_failed: bool
    strict_error: str


class SubtitleProcessor:
    """字幕处理器主类"""

    def __init__(self):
        self.extractor = SubtitleExtractor()
        self.ai_client = AIClient()
        self.syncer = FFsubsyncRunner()

    def _normalize_language(self, lang: Optional[str]) -> Tuple[str, bool]:
        """
        将字幕组语言标签转换为 Emby 标准格式

        Args:
            lang: 原始语言标签（如 chs, sc, cht, tc 等）

        Returns:
            (Emby标准语言代码, 是否为简体中文)
        """
        if not lang:
            # 未检测到语言标记：归为中文但不指定区域、不加 .default，
            # 避免日文/英文/未标记字幕被误标简体中文默认被 Emby 优先选中。
            return ("zh", False)

        lang_lower = lang.lower().strip()

        if lang_lower in LANGUAGE_MAP:
            return LANGUAGE_MAP[lang_lower]

        # 未知语言，保持原样，不标记为默认
        return (lang, False)

    def process(
        self,
        archive_path: Path,
        target_task_uuid: Optional[str] = None,
        *,
        mapping_only: bool = False,
    ) -> Dict[str, Any]:
        """
        处理字幕压缩包（支持多季度/多任务）——薄入口。

        对齐 rename 的 ``Rename.process()``：固定层抽事实（解压 + 任务记录）→
        调 Case Agent 入口做 evidence-driven 映射 + Verifier 合同校验 →
        accepted 落盘 / fail_closed 或 need_confirm 写合格结果 / invalid 记录错误。

        Args:
            archive_path: 压缩包路径
            target_task_uuid: 手动指定的目标任务UUID（可选，用于单任务模式）
            mapping_only: True 时 accepted 不落盘到媒体库，直接返回字幕→视频映射
                产物（mappings/unmatched/compiled_plan）。用于 auto_fetch 映射模式
                等不碰媒体库的场景。默认 False（生产落盘行为）。

        Returns:
            处理结果字典（status: success | need_confirm | error）
        """
        _uuid = str(uuid.uuid4())
        logger.info(f"[字幕处理] 开始处理: {archive_path.name}")

        # Step 1: 解压压缩包（事实抽取）
        subtitle_files = self.extractor.extract(archive_path)
        if not subtitle_files:
            return self._error_result(_uuid, "解压失败", archive_path)

        logger.info(f"[字幕处理] 解压成功，找到 {len(subtitle_files)} 个字幕文件")

        # Step 2: 读取已处理任务记录 + 目标任务作用域收窄
        processed_tasks, resolve_error = self._resolve_processed_tasks(
            target_task_uuid=target_task_uuid,
        )
        if processed_tasks is None:
            self.extractor.cleanup(archive_path)
            return self._error_result(_uuid, resolve_error, archive_path)

        logger.info(f"[字幕处理] 读取到 {len(processed_tasks)} 个已处理任务")

        # Step 3: 压缩包结构（事实，喂 AI）
        archive_structure = self.extractor.get_archive_structure(subtitle_files)
        logger.info(f"[字幕处理] 压缩包结构: {list(archive_structure.keys())}")

        case_agent_enabled = bool(
            cm.get_config("subtitle_case_agent_primary_enabled")
        )

        if case_agent_enabled:
            return self._process_case_agent(
                _uuid=_uuid,
                archive_path=archive_path,
                subtitle_files=subtitle_files,
                processed_tasks=processed_tasks,
                archive_structure=archive_structure,
                mapping_only=mapping_only,
            )
        return self._process_legacy_compat(
            _uuid=_uuid,
            archive_path=archive_path,
            subtitle_files=subtitle_files,
            processed_tasks=processed_tasks,
            archive_structure=archive_structure,
            mapping_only=mapping_only,
        )

    def process_mapping(
        self,
        archive_path: Path,
        target_task_uuid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """映射模式入口：accepted 不落盘到媒体库，返回字幕→视频映射产物。

        等价于 ``process(archive_path, target_task_uuid, mapping_only=True)``。
        供 auto_fetch 映射模式等不碰媒体库的场景使用：解压→Case Agent 映射→
        Verifier 合同→accepted 返回 mappings/unmatched/compiled_plan，跳过
        ffsubsync + trans_file 落盘。四态语义不变（accepted/fail_closed/
        need_confirm/invalid）。
        """
        return self.process(archive_path, target_task_uuid, mapping_only=True)

    # ------------------------------------------------------------------
    # 目标任务作用域
    # ------------------------------------------------------------------

    def _resolve_processed_tasks(
        self,
        *,
        target_task_uuid: Optional[str],
    ) -> Tuple[Optional[List[ProcessedTask]], str]:
        """读取并按 target_task_uuid 收窄任务作用域。

        返回 ``(tasks_or_None, error_message)``：tasks 为 None 时 error_message
        给出原因（无任务记录 / 指定任务不存在）。
        """
        if target_task_uuid:
            processed_tasks = self._load_processed_tasks_for_target_uuid(
                target_task_uuid
            )
        else:
            processed_tasks = self._load_processed_tasks(max_tasks=10)
        if not processed_tasks:
            return None, "无已处理的任务记录"

        if not target_task_uuid:
            return processed_tasks, ""

        target_task = next(
            (t for t in processed_tasks if t["uuid"] == target_task_uuid),
            None,
        )
        if not target_task:
            return None, f"指定的任务不存在: {target_task_uuid}"

        if target_task.get("is_movie", False):
            return [target_task], ""

        target_root = str(target_task.get("target_root") or "").strip()
        if target_root:
            related_tasks = self._load_processed_tasks(
                max_tasks=None,
                target_root=target_root,
            )
            return (related_tasks or [target_task]), ""
        return [target_task], ""

    # ------------------------------------------------------------------
    # Case Agent 主路径（合同校验）
    # ------------------------------------------------------------------

    def _process_case_agent(
        self,
        *,
        _uuid: str,
        archive_path: Path,
        subtitle_files: List[ExtractedSubtitle],
        processed_tasks: List[ProcessedTask],
        archive_structure: Dict[str, List[str]],
        mapping_only: bool = False,
    ) -> Dict[str, Any]:
        """Case Agent 主路径：entry → Verifier 合同 → compiled_plan 落盘。

        ``mapping_only=True`` 时 accepted 不落盘，返回映射产物（见 _land_compiled_plan）。
        """
        from .case_agent import run_subtitle_case_agent_mapping

        entry_result = run_subtitle_case_agent_mapping(
            subtitle_files=subtitle_files,
            processed_tasks=processed_tasks,
            ai_client=self.ai_client,
            source_path=archive_path,
            language_resolver=self._normalize_language,
            archive_name=archive_path.name,
            archive_structure=archive_structure,
        )

        status = str(entry_result.get("status") or "invalid")
        snapshot = entry_result.get("snapshot") or {}
        compiled_plan = entry_result.get("compiled_plan")

        if status == "accepted" and compiled_plan is not None:
            return self._land_compiled_plan(
                _uuid=_uuid,
                archive_path=archive_path,
                subtitle_files=subtitle_files,
                processed_tasks=processed_tasks,
                compiled_plan=compiled_plan,
                snapshot=snapshot,
                confidence=str(snapshot.get("draft", {}).get("confidence", "Medium"))
                if isinstance(snapshot.get("draft"), dict)
                else "Medium",
                mapping_only=mapping_only,
            )

        if status == "need_confirm":
            self.extractor.cleanup(archive_path)
            return self._need_confirm_result(
                _uuid=_uuid,
                archive_path=archive_path,
                subtitle_files=subtitle_files,
                processed_tasks=processed_tasks,
                snapshot=snapshot,
                error="AI 无法确定匹配的动漫，请手动选择",
            )

        if status == "fail_closed":
            # 合格业务结果：合同不通过，不强行落盘部分匹配
            self.extractor.cleanup(archive_path)
            summary = str(entry_result.get("summary") or "字幕映射合同校验未通过")
            return self._fail_closed_result(
                _uuid=_uuid,
                archive_path=archive_path,
                subtitle_files=subtitle_files,
                processed_tasks=processed_tasks,
                snapshot=snapshot,
                summary=summary,
            )

        # invalid：实现/合同错误
        self.extractor.cleanup(archive_path)
        return self._error_result(
            _uuid,
            str(entry_result.get("summary") or "字幕 Case Agent 实现错误"),
            archive_path,
            {"case_agent_snapshot": snapshot, "pipeline_mode": "subtitle_case_agent_primary"},
        )

    # ------------------------------------------------------------------
    # 兼容路径（Case Agent 关闭时）：单轮 AI + 精确匹配，无合同校验
    # ------------------------------------------------------------------

    def _process_legacy_compat(
        self,
        *,
        _uuid: str,
        archive_path: Path,
        subtitle_files: List[ExtractedSubtitle],
        processed_tasks: List[ProcessedTask],
        archive_structure: Dict[str, List[str]],
        mapping_only: bool = False,
    ) -> Dict[str, Any]:
        """旧兼容路径：直接用 AI 结果精确匹配落盘，无 Verifier 合同。

        保留 analyze_subtitle_mapping 旧路径作为 Case Agent 关闭时兼容；不再做
        suffix 模糊匹配 / split(" - ") 集数规则匹配（对齐 AI-first 改造）。
        """
        from .case_agent import build_subtitle_file_cards, build_target_video_cards
        from .case_agent import build_subtitle_case_workspace
        from .case_agent.local_subtitle_entry import (
            _build_subtitle_path_index,
            _build_target_index,
            _resolve_subtitle_ref,
            _resolve_target_ref,
        )
        from .case_agent.models import CompiledSubtitleMapping, CompiledSubtitlePlan
        ai_result = self.ai_client.analyze_subtitle_mapping(
            archive_name=archive_path.name,
            archive_structure=archive_structure,
            processed_tasks=processed_tasks,
        )

        if not ai_result or not ai_result.mappings:
            self.extractor.cleanup(archive_path)
            return self._need_confirm_result(
                _uuid=_uuid,
                archive_path=archive_path,
                subtitle_files=subtitle_files,
                processed_tasks=processed_tasks,
                snapshot=None,
                error="AI 无法确定匹配的动漫，请手动选择",
            )

        # 复用 entry 的精确 ref 解析，构造 compiled_plan 后走统一落盘
        subtitle_cards = build_subtitle_file_cards(subtitle_files)
        target_cards = build_target_video_cards(processed_tasks)
        workspace = build_subtitle_case_workspace(
            archive_name=archive_path.name,
            subtitle_files=subtitle_cards,
            target_videos=target_cards,
        )
        sub_index = _build_subtitle_path_index(workspace.subtitle_files)
        target_index = _build_target_index(workspace.target_videos)
        sub_by_ref = workspace.subtitle_card_by_ref()
        target_by_ref = workspace.target_card_by_ref()

        compiled_mappings: List[CompiledSubtitleMapping] = []
        for mapping in ai_result.mappings:
            sub_ref = _resolve_subtitle_ref(
                str(getattr(mapping, "subtitle_path", "") or ""), sub_index
            )
            target_ref = _resolve_target_ref(
                str(getattr(mapping, "task_uuid", "") or ""),
                str(getattr(mapping, "video", "") or ""),
                target_index,
            )
            if not sub_ref or not target_ref:
                logger.warning(
                    f"[字幕处理] 兼容路径无法精确解析映射: "
                    f"{getattr(mapping, 'subtitle_path', '')} / "
                    f"{getattr(mapping, 'task_uuid', '')}+{getattr(mapping, 'video', '')}"
                )
                continue
            sub_card = sub_by_ref.get(sub_ref)
            target_card = target_by_ref.get(target_ref)
            if sub_card is None or target_card is None:
                continue
            emby_lang, is_simplified = self._normalize_language(
                getattr(mapping, "language", None)
            )
            compiled_mappings.append(
                CompiledSubtitleMapping(
                    subtitle_ref=sub_ref,
                    subtitle_archive_path=sub_card.archive_path,
                    target_ref=target_ref,
                    task_uuid=target_card.task_uuid,
                    video=target_card.video,
                    target_dir=target_card.target_dir,
                    emby_lang=emby_lang,
                    is_simplified=is_simplified,
                    is_movie=target_card.is_movie,
                )
            )

        if not compiled_mappings:
            self.extractor.cleanup(archive_path)
            return self._error_result(_uuid, "无法建立字幕映射", archive_path)

        compiled_plan = CompiledSubtitlePlan(
            mappings=compiled_mappings,
            unmatched=[],
            summary=str(getattr(ai_result, "reason", "") or "legacy compat plan"),
        )
        return self._land_compiled_plan(
            _uuid=_uuid,
            archive_path=archive_path,
            subtitle_files=subtitle_files,
            processed_tasks=processed_tasks,
            compiled_plan=compiled_plan,
            snapshot=None,
            confidence=str(getattr(ai_result, "confidence", "Medium") or "Medium"),
            pipeline_mode="subtitle_legacy_compat",
            mapping_only=mapping_only,
        )

    # ------------------------------------------------------------------
    # 落盘（accepted / legacy 共用）
    # ------------------------------------------------------------------

    def _land_compiled_plan(
        self,
        *,
        _uuid: str,
        archive_path: Path,
        subtitle_files: List[ExtractedSubtitle],
        processed_tasks: List[ProcessedTask],
        compiled_plan: Any,
        snapshot: Any,
        confidence: str,
        pipeline_mode: str = "subtitle_case_agent_primary",
        mapping_only: bool = False,
    ) -> Dict[str, Any]:
        """用 CompiledSubtitlePlan 生成 Emby 文件名 → ffsubsync → 复制落盘。

        部分字幕 unmatched 时：落盘已匹配部分，unmatched 写进任务 JSON 作为
        待人工子项（用户已确认），整体 status=success。

        ``mapping_only=True`` 时：在 file_mapping/mapping_details 构造完成后分叉，
        跳过 ffsubsync + trans_file 落盘，直接返回字幕→视频映射产物（不写媒体库、
        不写生产 task JSON）。四态语义不变，accepted 仍是 accepted，只是不落盘。
        供 auto_fetch 映射模式等不碰媒体库的场景使用。
        """
        # subtitle archive_path -> ExtractedSubtitle（精确匹配，固定层事实）
        sub_by_archive = {
            self._normalize_card_path(sub.archive_path): sub for sub in subtitle_files
        }
        task_by_uuid: Dict[str, ProcessedTask] = {
            t["uuid"]: t for t in processed_tasks
        }

        file_mapping: Dict[Path, Path] = {}
        mapping_details: List[Dict[str, Any]] = []
        sync_items: List[Dict[str, Any]] = []

        matched_task_uuids: Set[str] = set()

        for compiled in compiled_plan.mappings:
            sub = sub_by_archive.get(
                self._normalize_card_path(compiled.subtitle_archive_path)
            )
            if sub is None:
                logger.warning(
                    f"[字幕处理] 落盘时找不到字幕事实: {compiled.subtitle_archive_path}"
                )
                continue
            task = task_by_uuid.get(compiled.task_uuid)
            if task is None:
                logger.warning(
                    f"[字幕处理] 落盘时找不到任务: {compiled.task_uuid}"
                )
                continue

            video_name = compiled.video
            video_stem = Path(video_name).stem

            # 目标目录：video_targets 优先（电影合集每部电影不同目录），否则 task target_dir
            video_targets = task.get("video_targets", {})
            video_target = video_targets.get(video_name)
            if video_target:
                target_dir = Path(video_target).parent
                video_path = Path(video_target)
            else:
                target_dir = Path(task["target_dir"])
                video_path = target_dir / video_name

            emby_lang = compiled.emby_lang
            is_simplified = compiled.is_simplified
            subtitle_ext = sub.temp_path.suffix.lower()
            if is_simplified:
                target_name = f"{video_stem}.{emby_lang}.default{subtitle_ext}"
            else:
                target_name = f"{video_stem}.{emby_lang}{subtitle_ext}"
            target_path = target_dir / target_name

            file_mapping[sub.temp_path] = target_path
            matched_task_uuids.add(compiled.task_uuid)
            mapping_detail = {
                "subtitle": compiled.subtitle_archive_path,
                "video": video_name,
                "target": target_name,
                "task_uuid": compiled.task_uuid,
                "task_title": task.get("title", ""),
                "language": emby_lang,
                "sync_status": "disabled",
            }
            mapping_details.append(mapping_detail)
            sync_items.append(
                {
                    "source_path": sub.temp_path,
                    "target_path": target_path,
                    "video_path": video_path,
                    "detail": mapping_detail,
                }
            )
            logger.info(
                f"[字幕处理] 映射: {compiled.subtitle_archive_path} -> "
                f"{task.get('title', '')} / {target_name}"
            )

        if not file_mapping:
            # Case Agent accepted 但 0 mappings：区分两种情况。
            # (a) compiled_plan 有 unmatched（全 no_target_video / 全真不确定）→
            #     合格业务结果（accepted + 0 matched + unmatched 详情），不应报 error。
            #     例：0045 sel#1 字幕包全是 S1正片/S2 Try/special，落地视频只有
            #     movie + S00 Battlogue specials，53 字幕全部 no_target_video，
            #     Case Agent 正确判 accepted，但旧代码这里误判成 error。
            # (b) compiled_plan 既无 mappings 又无 unmatched → 真实现错误 → error。
            if not getattr(compiled_plan, "unmatched", None):
                self.extractor.cleanup(archive_path)
                return self._error_result(_uuid, "无法建立字幕映射", archive_path)
            # (a) 走 accepted + 0 matched + unmatched 路径：先算 unmatched 分类，
            # 再按 mapping_only 分叉返回（跳过 ffsubsync/trans_file，因为没文件要落盘）
            logger.info(
                "[字幕处理] Case Agent accepted 但 0 mappings，"
                f"{len(compiled_plan.unmatched)} 个 unmatched（无目标/待人工），按 accepted 返回"
            )

        # unmatched 分类：no_target_video（无目标，信息性）vs unmatched（待人工）
        unmatched_details, no_target_details = self._build_unmatched_details(
            compiled_plan=compiled_plan,
            subtitle_files=subtitle_files,
            sub_by_archive=sub_by_archive,
        )

        # mapping_only 分叉：不落盘到媒体库，直接返回映射产物。
        # 跳过 ffsubsync + trans_file + 生产 task JSON 写入。cleanup 临时解压目录。
        if mapping_only:
            self.extractor.cleanup(archive_path)
            compiled_plan_dump: Any
            compiled_plan_dump = (
                compiled_plan.model_dump(mode="json")
                if hasattr(compiled_plan, "model_dump")
                else None
            )
            result = {
                "status": "success",
                "mapping_only": True,
                "uuid": _uuid,
                "archive_path": str(archive_path),
                "matched_tasks": list(matched_task_uuids),
                "matched_count": len(file_mapping),
                "total_subtitles": len(subtitle_files),
                "mappings": mapping_details,
                "unmatched": unmatched_details,
                "no_target_videos": no_target_details,
                "compiled_plan": compiled_plan_dump,
                "pipeline_mode": pipeline_mode,
                "case_agent_snapshot": snapshot,
                "confidence": confidence,
            }
            logger.info(
                f"[字幕处理] mapping_only 完成! 映射 {len(file_mapping)} 个字幕 "
                f"到 {len(matched_task_uuids)} 个任务（不落盘）"
                + (f"，{len(unmatched_details)} 个待人工" if unmatched_details else "")
                + (f"，{len(no_target_details)} 个无目标视频（已过滤）" if no_target_details else "")
            )
            return result

        # ffsubsync（保持现有接线）
        sync_summary = {
            "enabled": False,
            "mode": cm.get_config("subtitle_sync_mode") or "best_effort",
            "attempted": 0,
            "success": 0,
            "fallback": 0,
            "skipped": len(sync_items),
            "disabled": len(sync_items),
            "failed": 0,
            "strict_failed": False,
            "strict_error": "",
        }

        final_mapping = dict(file_mapping)
        if cm.get_config("subtitle_sync_enabled"):
            sync_summary, final_mapping = self._apply_subtitle_sync(
                archive_path=archive_path,
                sync_items=sync_items,
                original_mapping=file_mapping,
            )
            if sync_summary.get("strict_failed"):
                self.extractor.cleanup(archive_path)
                return self._error_result(
                    _uuid,
                    sync_summary.get("strict_error", "字幕对齐失败"),
                    archive_path,
                    {
                        "sync_summary": sync_summary,
                        "mappings": mapping_details,
                    },
                )

        # 执行文件传输（字幕强制使用复制模式）
        force_overwrite = self._resolve_sync_overwrite_policy()
        trans = Trans(
            final_mapping,
            _uuid,
            force_mode="复制",
            force_overwrite=force_overwrite,
        )
        trans_result = trans.trans_file()

        self.extractor.cleanup(archive_path)

        if isinstance(trans_result, str):
            return self._error_result(_uuid, trans_result, archive_path)

        # 任务记录
        matched_tasks_info = self._matched_tasks_info(
            matched_task_uuids=matched_task_uuids,
            processed_tasks=processed_tasks,
        )
        result = {
            "status": "success",
            "uuid": _uuid,
            "archive_path": str(archive_path),
            "matched_tasks": list(matched_task_uuids),
            "matched_task": ", ".join(matched_tasks_info),
            "confidence": confidence,
            "matched_count": len(final_mapping),
            "total_subtitles": len(subtitle_files),
            "mappings": mapping_details,
            "sync_summary": sync_summary,
            "unmatched": unmatched_details,
            "no_target_videos": no_target_details,
            "pipeline_mode": pipeline_mode,
            "case_agent_snapshot": snapshot,
        }

        task_path = TASK_PATH / f"{_uuid}.json"
        with open(task_path, "w", encoding="UTF-8") as f:
            json.dump(
                {
                    "type": "subtitle",
                    **result,
                },
                f,
                indent=4,
                ensure_ascii=False,
            )

        logger.info(
            f"[字幕处理] 完成! 成功匹配 {len(final_mapping)} 个字幕文件 "
            f"到 {len(matched_task_uuids)} 个任务"
            + (f"，{len(unmatched_details)} 个待人工" if unmatched_details else "")
            + (f"，{len(no_target_details)} 个无目标视频（已过滤）" if no_target_details else "")
        )

        return result

    # ------------------------------------------------------------------
    # 合格非落盘结果：need_confirm / fail_closed
    # ------------------------------------------------------------------

    def _need_confirm_result(
        self,
        *,
        _uuid: str,
        archive_path: Path,
        subtitle_files: List[ExtractedSubtitle],
        processed_tasks: List[ProcessedTask],
        snapshot: Any,
        error: str,
    ) -> Dict[str, Any]:
        """need_confirm：AI 无法确定匹配，需人工选择目标任务。写合格任务记录。"""
        result = {
            "status": "need_confirm",
            "uuid": _uuid,
            "archive_path": str(archive_path),
            "subtitle_count": len(subtitle_files),
            "available_tasks": [
                {
                    "uuid": t["uuid"],
                    "title": t.get("title", ""),
                    "season": t.get("season", 1),
                }
                for t in processed_tasks[:10]
            ],
            "error": error,
            "pipeline_mode": "subtitle_case_agent_primary",
            "case_agent_snapshot": snapshot,
        }
        self._write_subtitle_task_json(_uuid, result)
        return result

    def _fail_closed_result(
        self,
        *,
        _uuid: str,
        archive_path: Path,
        subtitle_files: List[ExtractedSubtitle],
        processed_tasks: List[ProcessedTask],
        snapshot: Any,
        summary: str,
    ) -> Dict[str, Any]:
        """fail_closed：合同校验未通过，合格业务结果，不落盘部分匹配。

        映射到对外 need_confirm 语义（UI/auto_fetch 已识别该状态触发人工/重试），
        但保留 fail_closed 标记与合同 issue 供审计。
        """
        result = {
            "status": "need_confirm",
            "uuid": _uuid,
            "archive_path": str(archive_path),
            "subtitle_count": len(subtitle_files),
            "available_tasks": [
                {
                    "uuid": t["uuid"],
                    "title": t.get("title", ""),
                    "season": t.get("season", 1),
                }
                for t in processed_tasks[:10]
            ],
            "error": summary,
            "pipeline_mode": "subtitle_case_agent_primary",
            "case_agent_status": "fail_closed",
            "case_agent_snapshot": snapshot,
        }
        self._write_subtitle_task_json(_uuid, result)
        return result

    # ------------------------------------------------------------------
    # 小工具
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_card_path(path: str) -> str:
        """归一化字幕 archive_path，与 case_agent.normalize_subtitle_archive_path 同口径。"""
        text = str(path or "").strip().replace("\\", "/")
        while text.startswith("./"):
            text = text[2:]
        return text.strip("/")

    @staticmethod
    def _matched_tasks_info(
        *,
        matched_task_uuids: Set[str],
        processed_tasks: List[ProcessedTask],
    ) -> List[str]:
        info: List[str] = []
        for task_uuid in matched_task_uuids:
            task = next(
                (t for t in processed_tasks if t["uuid"] == task_uuid),
                None,
            )
            if not task:
                continue
            if task.get("is_movie", False):
                info.append(f"{task.get('title', '')} (电影)")
            else:
                info.append(
                    f"{task.get('title', '')} (Season {task.get('season', 1)})"
                )
        return info

    def _build_unmatched_details(
        self,
        *,
        compiled_plan: Any,
        subtitle_files: List[ExtractedSubtitle],
        sub_by_archive: Dict[str, ExtractedSubtitle],
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """从 compiled_plan.unmatched（结构化）构建分类子项。

        按 ``unmatched_reason_kind`` 分类：
        - ``no_target_video`` → no_target_details（确定的"无目标"，非待人工，
          如 PV/TV-Spot/Picture Drama/OAD/special 等字幕，源视频无 TMDB 落点）。
        - 其余（duplicate_language / no_confident_match / unknown）→ unmatched
          details（待人工）。

        返回 (unmatched_details, no_target_details)。
        """
        unmatched_entries = list(getattr(compiled_plan, "unmatched", []) or [])
        if not unmatched_entries:
            return [], []
        # ref -> archive_path（从 subtitle_files 顺序对齐 SF 分配）
        ref_to_archive: Dict[str, str] = {}
        for idx, sub in enumerate(subtitle_files, start=1):
            ref_to_archive[f"SF{idx}"] = self._normalize_card_path(sub.archive_path)
        unmatched_details: List[Dict[str, Any]] = []
        no_target_details: List[Dict[str, Any]] = []
        for entry in unmatched_entries:
            ref = getattr(entry, "ref", "") or ""
            if not ref:
                continue
            archive_path = ref_to_archive.get(ref, "")
            if not archive_path:
                continue
            reason_kind = str(getattr(entry, "reason_kind", "unknown") or "unknown")
            reason = str(getattr(entry, "reason", "") or "")
            item = {
                "ref": ref,
                "archive_path": archive_path,
                "reason_kind": reason_kind,
                "reason": reason,
            }
            if reason_kind == "no_target_video":
                no_target_details.append(item)
            else:
                unmatched_details.append(item)
        return unmatched_details, no_target_details

    def _write_subtitle_task_json(self, _uuid: str, result: Dict[str, Any]) -> None:
        task_path = TASK_PATH / f"{_uuid}.json"
        with open(task_path, "w", encoding="UTF-8") as f:
            json.dump(
                {
                    "type": "subtitle",
                    **result,
                },
                f,
                indent=4,
                ensure_ascii=False,
            )

    def _load_processed_tasks_for_target_uuid(
        self,
        target_task_uuid: str,
    ) -> List[ProcessedTask]:
        """按目标任务 UUID 精确加载任务，并补充同剧相关任务。"""
        target_task_uuid = str(target_task_uuid or "").strip()
        if not target_task_uuid:
            return []

        target_task = self._load_single_processed_task(target_task_uuid)
        if not target_task:
            return []
        if target_task.get("is_movie", False):
            return [target_task]

        target_root = str(target_task.get("target_root") or "").strip()
        if not target_root:
            return [target_task]

        related_tasks = self._load_processed_tasks(max_tasks=None, target_root=target_root)
        if not related_tasks:
            return [target_task]

        if not any(task.get("uuid") == target_task_uuid for task in related_tasks):
            related_tasks.insert(0, target_task)
        return related_tasks

    def _load_single_processed_task(self, task_uuid: str) -> Optional[ProcessedTask]:
        task_uuid = str(task_uuid or "").strip()
        if not task_uuid:
            return None

        task_file = TASK_PATH / f"{task_uuid}.json"
        if not task_file.exists():
            return None

        return self._build_processed_task_from_file(task_file)

    def _build_processed_task_from_file(
        self,
        task_file: Path,
        target_root: Optional[str] = None,
    ) -> Optional[ProcessedTask]:
        try:
            with open(task_file, "r", encoding="UTF-8") as f:
                task_data = json.load(f)

            if task_data.get("type") == "subtitle":
                return None
            if task_data.get("error"):
                return None

            is_movie = task_data.get("is_movie", False)
            task_uuid = task_data.get("uuid", task_file.stem)
            normalized_target_root = self._normalize_target_root(
                task_data.get("target_root")
            )
            if target_root and normalized_target_root != self._normalize_target_root(
                target_root
            ):
                return None

            record_file = RECORD_PATH / f"{task_uuid}.json"
            if not record_file.exists():
                return None

            with open(record_file, "r", encoding="UTF-8") as f:
                record_data = json.load(f)

            if not isinstance(record_data, dict) or not record_data:
                return None

            videos: list[str] = []
            video_targets: dict[str, str] = {}
            # 目标文件名 -> 重命名前 local 原始文件名（record 的 key 是 local 源路径）。
            # 仅作 AI 匹配证据，不参与合同裁决；合法落点仍以 video（目标名）为准。
            source_videos: dict[str, str] = {}
            target_dir = None
            for source, target in record_data.items():
                if not isinstance(target, str):
                    continue
                target_path = Path(target)
                target_name = target_path.name
                videos.append(target_name)
                video_targets[target_name] = str(target_path)
                if isinstance(source, str) and source:
                    source_name = Path(source).name
                    if source_name and source_name not in source_videos.values():
                        source_videos.setdefault(target_name, source_name)
                if target_dir is None:
                    target_dir = str(target_path.parent)

            # per-video arc 名：从 task_data.bgm_video_subject_map + bgm_subjects
            # 反查每个 video 的 subject name/name_cn（多季同 episode 配对关键证据）。
            video_arc_names: dict[str, tuple[str, str]] = {}
            bgm_video_subject_map = task_data.get("bgm_video_subject_map") or {}
            if isinstance(bgm_video_subject_map, dict) and bgm_video_subject_map:
                bgm_subjects = task_data.get("bgm_subjects") or []
                subject_meta: dict[int, tuple[str, str]] = {}
                if isinstance(bgm_subjects, list):
                    for entry in bgm_subjects:
                        if not isinstance(entry, dict):
                            continue
                        sid = entry.get("id")
                        if sid is None:
                            continue
                        try:
                            sid_int = int(sid)
                        except (TypeError, ValueError):
                            continue
                        subject_meta[sid_int] = (
                            str(entry.get("name") or ""),
                            str(entry.get("name_cn") or ""),
                        )
                for vname in videos:
                    sid = bgm_video_subject_map.get(vname)
                    if sid is None:
                        continue
                    try:
                        sid_int = int(sid)
                    except (TypeError, ValueError):
                        continue
                    meta = subject_meta.get(sid_int)
                    if meta is not None:
                        video_arc_names[vname] = meta

            if not videos or not target_dir:
                return None

            year = None
            target_dir_path = Path(target_dir)
            if is_movie:
                year_match = re.search(r"\((\d{4})\)", target_dir_path.name)
            else:
                parent_name = target_dir_path.parent.name
                year_match = re.search(r"\((\d{4})\)", parent_name)

            if year_match:
                year = int(year_match.group(1))

            season_value = task_data.get("season_id", 1)
            season = season_value if isinstance(season_value, int) else 1

            return {
                "uuid": task_uuid,
                "title": task_data.get("name", ""),
                "year": year,
                "season": season if not is_movie else None,
                "target_dir": target_dir,
                "target_root": normalized_target_root,
                "videos": sorted(videos),
                "video_targets": video_targets,
                "source_videos": source_videos,
                "is_movie": is_movie,
                "video_arc_names": video_arc_names,
            }
        except Exception as e:
            logger.warning(f"[字幕处理] 读取任务文件失败: {task_file}, {e}")
            return None

    def _apply_subtitle_sync(
        self,
        archive_path: Path,
        sync_items: List[Dict[str, Any]],
        original_mapping: Dict[Path, Path],
    ) -> Tuple[SyncSummary, Dict[Path, Path]]:
        """执行字幕对齐步骤，返回统计信息和最终映射"""
        sync_mode = cm.get_config("subtitle_sync_mode") or "best_effort"
        if sync_mode not in {"best_effort", "strict"}:
            sync_mode = "best_effort"

        summary: SyncSummary = {
            "enabled": True,
            "mode": sync_mode,
            "attempted": 0,
            "success": 0,
            "fallback": 0,
            "skipped": 0,
            "disabled": 0,
            "failed": 0,
            "strict_failed": False,
            "strict_error": "",
        }

        final_mapping = dict(original_mapping)

        for item in sync_items:
            summary["attempted"] += 1
            source_path: Path = item["source_path"]
            target_path: Path = item["target_path"]
            video_path: Path = item["video_path"]
            detail: Dict[str, Any] = item["detail"]

            if not video_path.exists():
                message = (
                    f"参考视频不存在: {video_path} (字幕: {detail.get('subtitle', '')})"
                )
                logger.warning(f"[字幕同步] {message}")
                if sync_mode == "strict":
                    summary["failed"] += 1
                    summary["strict_failed"] = True
                    summary["strict_error"] = message
                    detail["sync_status"] = "skipped"
                    break
                summary["skipped"] += 1
                detail["sync_status"] = "skipped"
                continue

            sync_output_dir = (
                self.extractor.get_extract_dir(archive_path) / ".ffsubsync"
            )
            sync_output_dir.mkdir(parents=True, exist_ok=True)

            sync_result = self.syncer.sync_subtitle(
                video_path=video_path,
                subtitle_path=source_path,
                output_dir=sync_output_dir,
            )

            if sync_result.success and sync_result.output_path:
                final_mapping.pop(source_path, None)
                final_mapping[sync_result.output_path] = target_path
                detail["sync_status"] = "synced"
                summary["success"] += 1
                logger.info(
                    f"[字幕同步] 对齐成功: {detail.get('subtitle', '')} -> "
                    f"{sync_result.output_path.name}"
                )
                continue

            if sync_mode == "strict":
                summary["failed"] += 1
                summary["strict_failed"] = True
                summary["strict_error"] = (
                    f"字幕对齐失败: {detail.get('subtitle', '')} ({sync_result.reason})"
                )
                detail["sync_status"] = "fallback"
                logger.error(f"[字幕同步] {summary['strict_error']}")
                break

            summary["fallback"] += 1
            detail["sync_status"] = "fallback"
            logger.warning(
                f"[字幕同步] 对齐失败，回退原字幕: {detail.get('subtitle', '')} "
                f"({sync_result.reason})"
            )

        return summary, final_mapping

    def _resolve_sync_overwrite_policy(self) -> Optional[bool]:
        """解析字幕同步覆盖策略"""
        policy = cm.get_config("subtitle_sync_overwrite_policy")
        if policy == "overwrite":
            return True
        if policy == "skip":
            return False
        return None

    def _load_processed_tasks(
        self,
        max_tasks: Optional[int] = 10,
        target_root: Optional[str] = None,
    ) -> List[ProcessedTask]:
        """
        从 data/task 和 data/record 读取已处理的任务记录

        Args:
            max_tasks: 最多加载的任务数量，按时间倒序取最近的任务；None 表示不限制
            target_root: 可选，仅加载属于同一目标根目录的任务

        Returns:
            任务列表，每个包含 uuid, title, season, target_dir, videos
        """
        tasks: list[ProcessedTask] = []

        if not TASK_PATH.exists():
            return tasks

        # 按文件修改时间倒序排序，优先加载最近的任务
        task_files = sorted(
            TASK_PATH.glob("*.json"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )

        for task_file in task_files:
            task = self._build_processed_task_from_file(
                task_file,
                target_root=target_root,
            )
            if not task:
                continue

            tasks.append(task)

            # 达到最大数量限制
            if max_tasks is not None and len(tasks) >= max_tasks:
                logger.info(f"[字幕处理] 已加载 {max_tasks} 个任务，跳过更早的任务")
                break

        return tasks

    @staticmethod
    def _normalize_target_root(target_root: Optional[str]) -> str:
        if not target_root:
            return ""
        try:
            return str(Path(target_root).resolve())
        except OSError:
            return str(Path(target_root))

    def _error_result(
        self,
        _uuid: str,
        error: str,
        archive_path: Path,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """生成错误结果"""
        logger.error(f"[字幕处理] {error}")

        result = {
            "status": "error",
            "uuid": _uuid,
            "archive_path": str(archive_path),
            "error": error,
        }
        if extra:
            result.update(extra)

        task_path = TASK_PATH / f"{_uuid}.json"
        with open(task_path, "w", encoding="UTF-8") as f:
            json.dump(
                {
                    "type": "subtitle",
                    **result,
                },
                f,
                indent=4,
                ensure_ascii=False,
            )

        return result
