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
    is_movie: bool


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

    @staticmethod
    def _normalize_archive_path(path: Optional[str]) -> str:
        """归一化压缩包内部路径，统一分隔符并去除首尾斜杠。"""
        if not path:
            return ""
        return str(path).replace("\\", "/").strip().strip("/")

    def _find_subtitle_file(
        self,
        subtitle_files: List[ExtractedSubtitle],
        subtitle_path: str,
    ) -> Optional[ExtractedSubtitle]:
        """根据 AI 返回的字幕路径查找解压后的字幕文件。"""
        normalized_target = self._normalize_archive_path(subtitle_path)
        if not normalized_target:
            return None

        exact_match = next(
            (
                sub
                for sub in subtitle_files
                if self._normalize_archive_path(sub.archive_path) == normalized_target
            ),
            None,
        )
        if exact_match:
            return exact_match

        filename_match = next(
            (
                sub
                for sub in subtitle_files
                if sub.filename == subtitle_path or sub.filename == Path(normalized_target).name
            ),
            None,
        )
        if filename_match:
            return filename_match

        suffix_match = next(
            (
                sub
                for sub in subtitle_files
                if self._normalize_archive_path(sub.archive_path).endswith(
                    f"/{normalized_target}"
                )
                or self._normalize_archive_path(sub.archive_path).endswith(
                    normalized_target
                )
            ),
            None,
        )
        if suffix_match:
            logger.info(
                f"[字幕处理] 字幕路径修正: {subtitle_path} -> {suffix_match.archive_path}"
            )
            return suffix_match

        return None

    def _normalize_language(self, lang: Optional[str]) -> Tuple[str, bool]:
        """
        将字幕组语言标签转换为 Emby 标准格式

        Args:
            lang: 原始语言标签（如 chs, sc, cht, tc 等）

        Returns:
            (Emby标准语言代码, 是否为简体中文)
        """
        if not lang:
            # 默认简体中文
            return ("zh-CN", True)

        lang_lower = lang.lower().strip()

        if lang_lower in LANGUAGE_MAP:
            return LANGUAGE_MAP[lang_lower]

        # 未知语言，保持原样，不标记为默认
        return (lang, False)

    def _extract_language_from_suffix_part(self, suffix_part: str) -> Optional[str]:
        """从同名后缀片段中提取语言标签（如 .chs.ass -> chs）。"""
        if not suffix_part:
            return None

        lowered = suffix_part.lower().strip()
        if not lowered:
            return None

        # 优先匹配更长的语言键，避免 zh 命中 zh-cn 的前缀
        for key in sorted(LANGUAGE_MAP.keys(), key=len, reverse=True):
            pattern = (
                rf"(^|[.\s_\-\[\]\(\)]){re.escape(key)}"
                rf"($|[.\s_\-\[\]\(\)])"
            )
            if re.search(pattern, lowered):
                return key

        return None

    def process(
        self,
        archive_path: Path,
        target_task_uuid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        处理字幕压缩包（支持多季度/多任务）

        Args:
            archive_path: 压缩包路径
            target_task_uuid: 手动指定的目标任务UUID（可选，用于单任务模式）

        Returns:
            处理结果字典
        """
        _uuid = str(uuid.uuid4())
        logger.info(f"[字幕处理] 开始处理: {archive_path.name}")

        # Step 1: 解压压缩包
        subtitle_files = self.extractor.extract(archive_path)
        if not subtitle_files:
            return self._error_result(_uuid, "解压失败", archive_path)

        logger.info(f"[字幕处理] 解压成功，找到 {len(subtitle_files)} 个字幕文件")

        # Step 2: 读取已处理任务记录
        if target_task_uuid:
            processed_tasks = self._load_processed_tasks_for_target_uuid(
                target_task_uuid
            )
        else:
            processed_tasks = self._load_processed_tasks(max_tasks=10)
        if not processed_tasks:
            self.extractor.cleanup(archive_path)
            return self._error_result(_uuid, "无已处理的任务记录", archive_path)

        logger.info(f"[字幕处理] 读取到 {len(processed_tasks)} 个已处理任务")

        # Step 3: 如果指定了目标任务，直接使用
        if target_task_uuid:
            target_task = next(
                (t for t in processed_tasks if t["uuid"] == target_task_uuid),
                None,
            )
            if not target_task:
                self.extractor.cleanup(archive_path)
                return self._error_result(
                    _uuid, f"指定的任务不存在: {target_task_uuid}", archive_path
                )

            if target_task.get("is_movie", False):
                processed_tasks = [target_task]
            else:
                target_root = str(target_task.get("target_root") or "").strip()
                if target_root:
                    related_tasks = self._load_processed_tasks(
                        max_tasks=None,
                        target_root=target_root,
                    )
                    processed_tasks = related_tasks or [target_task]
                else:
                    processed_tasks = [target_task]

        # Step 4: 获取压缩包结构并调用 AI 分析
        archive_structure = self.extractor.get_archive_structure(subtitle_files)
        logger.info(f"[字幕处理] 压缩包结构: {list(archive_structure.keys())}")

        ai_result = self.ai_client.analyze_subtitle_mapping(
            archive_name=archive_path.name,
            archive_structure=archive_structure,
            processed_tasks=processed_tasks,
        )

        if not ai_result or not ai_result.mappings:
            self.extractor.cleanup(archive_path)
            return {
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
                "error": "AI 无法确定匹配的动漫，请手动选择",
            }

        # Step 5: 统计匹配到的任务
        matched_task_uuids: Set[str] = set(m.task_uuid for m in ai_result.mappings)
        matched_tasks_info = []
        for task_uuid in matched_task_uuids:
            task = next(
                (t for t in processed_tasks if t["uuid"] == task_uuid),
                None,
            )
            if task:
                if task.get("is_movie", False):
                    matched_tasks_info.append(f"{task.get('title', '')} (电影)")
                else:
                    matched_tasks_info.append(
                        f"{task.get('title', '')} (Season {task.get('season', 1)})"
                    )

        logger.info(
            f"[字幕处理] AI 匹配到 {len(matched_task_uuids)} 个任务: "
            f"{', '.join(matched_tasks_info)} (置信度: {ai_result.confidence})"
        )

        # Step 6: 构建文件映射（按任务分组）
        # 创建任务UUID到任务信息的映射
        task_by_uuid: Dict[str, ProcessedTask] = {
            t["uuid"]: t for t in processed_tasks
        }

        file_mapping: Dict[Path, Path] = {}
        mapping_details: List[Dict[str, Any]] = []
        sync_items: List[Dict[str, Any]] = []

        for mapping in ai_result.mappings:
            # 找到对应的字幕文件
            subtitle_file = self._find_subtitle_file(
                subtitle_files,
                mapping.subtitle_path,
            )
            if not subtitle_file:
                logger.warning(
                    f"[字幕处理] 字幕文件不存在: {mapping.subtitle_path}"
                )
                continue

            # 找到对应的任务
            task = task_by_uuid.get(mapping.task_uuid)
            if not task:
                logger.warning(
                    f"[字幕处理] 任务不存在: {mapping.task_uuid}"
                )
                continue

            is_movie = task.get("is_movie", False)

            # 验证视频文件名是否存在于任务的视频列表中
            video_name = mapping.video
            task_videos = task.get("videos", [])
            if video_name not in task_videos:
                # AI 可能返回了不精确的文件名，尝试模糊匹配
                matched_video = None

                if is_movie and len(task_videos) == 1:
                    # 电影只有一个视频文件，直接使用
                    matched_video = task_videos[0]
                    logger.info(
                        f"[字幕处理] 电影视频自动匹配: {video_name} -> {matched_video}"
                    )
                else:
                    # 剧集：尝试通过集数匹配
                    for v in task_videos:
                        try:
                            # 提取集数进行匹配（格式：Title - S01E01 - Episode Name）
                            v_parts = Path(v).stem.split(" - ")
                            video_parts = Path(video_name).stem.split(" - ")
                            if len(v_parts) > 1 and len(video_parts) > 1:
                                if v_parts[1] == video_parts[1]:
                                    matched_video = v
                                    break
                        except (IndexError, AttributeError):
                            continue

                if matched_video:
                    if not is_movie:
                        logger.info(
                            f"[字幕处理] 视频文件名修正: {video_name} -> {matched_video}"
                        )
                    video_name = matched_video
                else:
                    logger.warning(
                        f"[字幕处理] 视频文件不存在于任务中: {video_name}"
                    )
                    continue

            video_stem = Path(video_name).stem

            # 获取该视频的目标目录（支持电影合集中每部电影不同目录）
            video_targets = task.get("video_targets", {})
            video_target = video_targets.get(video_name)
            if video_target:
                target_dir = Path(video_target).parent
                video_path = Path(video_target)
            else:
                target_dir = Path(task["target_dir"])
                video_path = target_dir / video_name

            # 转换语言代码为 Emby 标准格式
            emby_lang, is_simplified = self._normalize_language(mapping.language)

            # 构建 Emby 标准格式: video.lang[.default].ext
            # 简体中文自动添加 .default 标签
            subtitle_ext = subtitle_file.temp_path.suffix.lower()
            if is_simplified:
                target_name = f"{video_stem}.{emby_lang}.default{subtitle_ext}"
            else:
                target_name = f"{video_stem}.{emby_lang}{subtitle_ext}"
            target_path = target_dir / target_name

            file_mapping[subtitle_file.temp_path] = target_path
            mapping_detail = {
                "subtitle": mapping.subtitle_path,
                "video": video_name,
                "target": target_name,
                "task_uuid": mapping.task_uuid,
                "task_title": task.get("title", ""),
                "language": emby_lang,
                "sync_status": "disabled",
            }
            mapping_details.append(mapping_detail)
            sync_items.append(
                {
                    "source_path": subtitle_file.temp_path,
                    "target_path": target_path,
                    "video_path": video_path,
                    "detail": mapping_detail,
                }
            )
            logger.info(
                f"[字幕处理] 映射: {mapping.subtitle_path} -> "
                f"{task.get('title', '')} / {target_name}"
            )

        if not file_mapping:
            self.extractor.cleanup(archive_path)
            return self._error_result(_uuid, "无法建立字幕映射", archive_path)

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

        # Step 7: 执行文件传输（字幕强制使用复制模式）
        force_overwrite = self._resolve_sync_overwrite_policy()
        trans = Trans(
            final_mapping,
            _uuid,
            force_mode="复制",
            force_overwrite=force_overwrite,
        )
        trans_result = trans.trans_file()

        # Step 8: 清理临时文件
        self.extractor.cleanup(archive_path)

        if isinstance(trans_result, str):
            return self._error_result(_uuid, trans_result, archive_path)

        # Step 9: 保存任务记录
        result = {
            "status": "success",
            "uuid": _uuid,
            "archive_path": str(archive_path),
            "matched_tasks": list(matched_task_uuids),
            "matched_task": ", ".join(matched_tasks_info),
            "confidence": ai_result.confidence,
            "matched_count": len(final_mapping),
            "total_subtitles": len(subtitle_files),
            "mappings": mapping_details,
            "sync_summary": sync_summary,
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
        )

        return result

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
            target_dir = None
            for source, target in record_data.items():
                if not isinstance(target, str):
                    continue
                target_path = Path(target)
                videos.append(target_path.name)
                video_targets[target_path.name] = str(target_path)
                if target_dir is None:
                    target_dir = str(target_path.parent)

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
                "is_movie": is_movie,
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
