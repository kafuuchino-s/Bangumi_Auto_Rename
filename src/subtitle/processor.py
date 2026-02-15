"""
字幕处理器主模块

协调解压、扫描任务记录、调用 AI、文件传输的全流程。
支持多季度/多任务的字幕压缩包处理。
"""

import json
import re
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from ..ai.client import AIClient
from ..logger import logger
from ..rename.trans import Trans
from ..utils.path import RECORD_PATH, TASK_PATH
from .extractor import ExtractedSubtitle, SubtitleExtractor

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


class SubtitleProcessor:
    """字幕处理器主类"""

    def __init__(self):
        self.extractor = SubtitleExtractor()
        self.ai_client = AIClient()

    @staticmethod
    def _extract_language_from_suffix_part(suffix_part: str) -> Optional[str]:
        """从文件名后缀片段中提取语言标签。

        例如：
            ".sc.ass" -> "sc"
            ".chs.forced.ass" -> "chs"
            ".ass" -> None

        Args:
            suffix_part: 从 "{video_stem}" 之后截取的字符串（含前导点）
        """
        parts = [p for p in suffix_part.lower().split(".") if p]
        for p in parts:
            if p in LANGUAGE_MAP:
                return p
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

    def process(
        self,
        archive_path: Path,
        target_task_uuid: Optional[str] = None,
    ) -> Dict:
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
        processed_tasks = self._load_processed_tasks()
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
            # 简化任务列表，只包含目标任务
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
        # 创建字幕文件路径到对象的映射
        subtitle_by_path: Dict[str, ExtractedSubtitle] = {
            sub.archive_path: sub for sub in subtitle_files
        }

        # 创建任务UUID到任务信息的映射
        task_by_uuid: Dict[str, Dict] = {t["uuid"]: t for t in processed_tasks}

        file_mapping: Dict[Path, Path] = {}
        mapping_details = []

        for mapping in ai_result.mappings:
            # 找到对应的字幕文件
            subtitle_file = subtitle_by_path.get(mapping.subtitle_path)
            if not subtitle_file:
                # 尝试用文件名匹配（兼容旧格式）
                subtitle_file = next(
                    (
                        sub
                        for sub in subtitle_files
                        if sub.filename == mapping.subtitle_path
                        or sub.archive_path == mapping.subtitle_path
                    ),
                    None,
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
            if video_name in video_targets:
                target_dir = Path(video_targets[video_name]).parent
            else:
                target_dir = Path(task["target_dir"])

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
            mapping_details.append(
                {
                    "subtitle": mapping.subtitle_path,
                    "video": video_name,
                    "target": target_name,
                    "task_uuid": mapping.task_uuid,
                    "task_title": task.get("title", ""),
                    "language": emby_lang,
                }
            )
            logger.info(
                f"[字幕处理] 映射: {mapping.subtitle_path} -> "
                f"{task.get('title', '')} / {target_name}"
            )

        if not file_mapping:
            self.extractor.cleanup(archive_path)
            return self._error_result(_uuid, "无法建立字幕映射", archive_path)

        # Step 7: 执行文件传输（字幕强制使用复制模式）
        trans = Trans(file_mapping, _uuid, force_mode="复制")
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
            "matched_count": len(file_mapping),
            "total_subtitles": len(subtitle_files),
            "mappings": mapping_details,
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
            f"[字幕处理] 完成! 成功匹配 {len(file_mapping)} 个字幕文件 "
            f"到 {len(matched_task_uuids)} 个任务"
        )

        return result

    def _load_processed_tasks(self, max_tasks: int = 10) -> List[Dict]:
        """
        从 data/task 和 data/record 读取已处理的任务记录

        Args:
            max_tasks: 最多加载的任务数量，按时间倒序取最近的任务

        Returns:
            任务列表，每个包含 uuid, title, season, target_dir, videos
        """
        tasks = []

        if not TASK_PATH.exists():
            return tasks

        # 按文件修改时间倒序排序，优先加载最近的任务
        task_files = sorted(
            TASK_PATH.glob("*.json"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )

        for task_file in task_files:
            try:
                with open(task_file, "r", encoding="UTF-8") as f:
                    task_data = json.load(f)

                # 跳过字幕任务和错误任务
                if task_data.get("type") == "subtitle":
                    continue
                if task_data.get("error"):
                    continue

                is_movie = task_data.get("is_movie", False)

                task_uuid = task_data.get("uuid", task_file.stem)

                # 读取对应的 record 文件获取视频映射
                record_file = RECORD_PATH / f"{task_uuid}.json"
                if not record_file.exists():
                    continue

                with open(record_file, "r", encoding="UTF-8") as f:
                    record_data = json.load(f)

                if not record_data:
                    continue

                # 提取视频文件名和目标目录
                videos = []
                video_targets = {}  # 视频文件名 -> 完整目标路径
                target_dir = None
                for source, target in record_data.items():
                    target_path = Path(target)
                    videos.append(target_path.name)
                    video_targets[target_path.name] = str(target_path)
                    if target_dir is None:
                        target_dir = str(target_path.parent)

                if not videos or not target_dir:
                    continue

                # 从目标目录路径提取年份
                year = None
                target_dir_path = Path(target_dir)
                # 电影的目录结构: Movie Name (2023)/Movie Name (2023).mkv
                # 剧集的目录结构: Show Name (2023)/Season 01/Show Name - S01E01.mkv
                if is_movie:
                    # 电影直接从目标目录名提取年份
                    year_match = re.search(r"\((\d{4})\)", target_dir_path.name)
                else:
                    # 剧集从父目录（show目录）提取年份
                    parent_name = target_dir_path.parent.name
                    year_match = re.search(r"\((\d{4})\)", parent_name)

                if year_match:
                    year = int(year_match.group(1))

                tasks.append(
                    {
                        "uuid": task_uuid,
                        "title": task_data.get("name", ""),
                        "year": year,
                        "season": task_data.get("season_id", 1) if not is_movie else None,
                        "target_dir": target_dir,
                        "videos": sorted(videos),
                        "video_targets": video_targets,  # 每个视频的完整目标路径
                        "is_movie": is_movie,
                    }
                )

                # 达到最大数量限制
                if len(tasks) >= max_tasks:
                    logger.info(f"[字幕处理] 已加载 {max_tasks} 个任务，跳过更早的任务")
                    break

            except Exception as e:
                logger.warning(f"[字幕处理] 读取任务文件失败: {task_file}, {e}")
                continue

        return tasks

    def _error_result(self, _uuid: str, error: str, archive_path: Path) -> Dict:
        """生成错误结果"""
        logger.error(f"[字幕处理] {error}")

        result = {
            "status": "error",
            "uuid": _uuid,
            "archive_path": str(archive_path),
            "error": error,
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

        return result
