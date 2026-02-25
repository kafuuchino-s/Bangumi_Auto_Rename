import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..logger import logger
from .utils import VIDEO_SUFFIX

from ..ai.client import AIClient
from ..ai.models import AIAnalysisResult
from ..ai.video_analyzer import VideoAnalyzer
from .cleaner import extract_part, extract_video_format, is_promotional_content
from .filename_builder import FilenameBuilder, EpisodeMetadata
from ..subtitle.processor import SubtitleProcessor
from ..subtitle.extractor import SUBTITLE_EXTENSIONS


class AIProcessor:
    """AI辅助处理器，用于智能分析和重命名"""

    RESOURCE_TOKEN_MAP = [
        ("HEVC", "HEVC"),
        ("X265", "x265"),
        ("X264", "x264"),
        ("AV1", "AV1"),
        ("10BIT", "10bit"),
        ("8BIT", "8bit"),
        ("HDR10", "HDR10"),
        ("HDR", "HDR"),
        ("DOLBY VISION", "Dolby Vision"),
        ("DV", "DV"),
        ("FLAC", "FLAC"),
        ("AAC", "AAC"),
        ("DTS", "DTS"),
        ("DDP", "DDP"),
        ("AC3", "AC3"),
        ("WEBRIP", "WebRip"),
        ("WEB-DL", "WEB-DL"),
        ("BDRIP", "BDRip"),
        ("BLURAY", "BluRay"),
    ]

    def __init__(self):
        self.ai_client = AIClient()
        self.video_analyzer = VideoAnalyzer()
        self.subtitle_processor = SubtitleProcessor()

    def analyze_anime_files(
        self,
        path: Path,
        anime_info: Dict,
        video_files: Optional[List[Path]] = None,
        file_analysis: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[AIAnalysisResult]:
        """
        使用AI分析动漫文件的映射关系

        Args:
            path: 本地文件路径
            anime_info: TMDB动漫信息
            video_files: 可选，预收集的视频文件列表
            file_analysis: 可选，预分析的视频信息列表

        Returns:
            验证后的AI分析结果
        """
        if not self.ai_client.is_available():
            logger.info("[AI处理] AI功能未启用，跳过AI分析")
            return None

        # 收集视频文件（允许复用调用方已收集结果）
        current_video_files = video_files or self._collect_video_files(path)
        if not current_video_files:
            logger.warning("[AI处理] 未找到视频文件")
            return None

        # 分析视频文件（允许复用调用方已分析结果）
        current_file_analysis = file_analysis or self.video_analyzer.analyze_video_files(
            path, current_video_files
        )

        # 使用AI分析映射关系
        ai_result = self.ai_client.analyze_episode_mapping(
            anime_info, current_file_analysis
        )

        if ai_result:
            logger.info(f"[AI处理] AI分析完成，置信度: {ai_result.confidence}")

            # 记录低置信度结果到单独日志
            if ai_result.confidence == "Low":
                self._log_low_confidence_result(path, ai_result)

        return ai_result

    def validate_tv_result(
        self,
        ai_result: AIAnalysisResult,
        anime_info: Dict,
        base_path: Path,
        video_files: Optional[List[Path]] = None,
    ) -> Tuple[bool, Optional[str], str]:
        """验证 TV AI 结果可执行性。"""
        if not ai_result or not ai_result.file_mapping:
            return False, "ai_empty_mapping", "AI 未返回 file_mapping"

        # 统计 TMDB 可用剧集
        tmdb_episode_keys: set[Tuple[int, int]] = set()
        for season in anime_info.get("seasons", []):
            season_num = season.get("season_number", 0)
            episodes = season.get("episodes", [])
            if episodes:
                for ep in episodes:
                    ep_num = ep.get("episode_number")
                    if isinstance(ep_num, int) and ep_num > 0:
                        tmdb_episode_keys.add((season_num, ep_num))
            else:
                episode_count = season.get("episode_count", 0)
                if isinstance(episode_count, int) and episode_count > 0:
                    for ep_num in range(1, episode_count + 1):
                        tmdb_episode_keys.add((season_num, ep_num))

        seen_keys: set[Tuple[int, int]] = set()
        ai_reported_conflicts: List[str] = list(
            getattr(ai_result, "conflict_details", []) or []
        )
        strict_conflicts: List[str] = []
        mapped_files: set[str] = set()

        for mapping in ai_result.file_mapping:
            normalized_path = mapping.file_path.replace('\\', '/').lstrip('/')
            source_path = (base_path / normalized_path).resolve()

            if not source_path.exists():
                strict_conflicts.append(f"文件不存在:{normalized_path}")

            key = (mapping.tmdb_season, mapping.tmdb_episode)
            if key in seen_keys:
                strict_conflicts.append(f"重复映射:S{key[0]:02d}E{key[1]:02d}")
            seen_keys.add(key)

            if key not in tmdb_episode_keys:
                strict_conflicts.append(f"越界映射:S{key[0]:02d}E{key[1]:02d}")

            mapped_files.add(normalized_path)

        # 可观测字段回写
        all_video_files = video_files or self._collect_video_files(base_path)
        existing_rel_paths = {
            str(p.relative_to(base_path)).replace('\\', '/')
            for p in all_video_files
        }
        unmatched_files = sorted(existing_rel_paths - mapped_files)

        ai_result.unmatched_files = sorted(
            set((ai_result.unmatched_files or []) + unmatched_files)
        )
        ai_result.conflict_details = sorted(
            set(ai_reported_conflicts + strict_conflicts)
        )

        soft_conflicts = sorted(
            set(ai_result.conflict_details) - set(strict_conflicts)
        )
        if soft_conflicts:
            logger.warning(
                f"[AI处理] 检测到AI提示的不确定项(不阻断): {'; '.join(soft_conflicts[:5])}"
            )

        if strict_conflicts:
            return (
                False,
                "ai_invalid_mapping",
                '; '.join(sorted(set(strict_conflicts))[:5]),
            )

        if not seen_keys:
            return False, "ai_empty_mapping", "AI 未返回任何有效映射"

        return True, None, ''

    def apply_ai_mapping(
        self,
        ai_result: AIAnalysisResult | None,
        anime_info: Dict,
        base_path: Path,
        work_path: Path,
        all_local_files: Optional[List[Path]] = None,
    ) -> Dict[Path, Path]:
        """
        以 TMDB 为主应用AI分析结果生成文件映射。

        遍历 TMDB 的季度和集数，在 AI 映射中查找对应的本地文件，
        只处理 TMDB 中存在的集数，忽略 AI 返回的不存在于 TMDB 的集数。

        Args:
            ai_result: 验证后的AI分析结果
            anime_info: TMDB动漫信息（包含seasons列表）
            base_path: 媒体文件扫描的根目录
            work_path: 目标工作目录路径

        Returns:
            一个全新的文件映射字典
        """
        if not ai_result or not ai_result.file_mapping:
            logger.info("[AI处理] 无有效AI分析结果，返回空映射")
            return {}

        if not anime_info or "seasons" not in anime_info:
            logger.warning("[AI处理] 无有效TMDB信息，返回空映射")
            return {}

        new_mapping: Dict[Path, Path] = {}
        resolved_local_files = all_local_files or self._collect_all_local_files(base_path)
        associated_file_index = self._build_associated_file_index(resolved_local_files)

        # 构建 AI 映射的快速查找索引: (season, episode) -> mapping
        ai_mapping_index: Dict[tuple[int, int], object] = {}
        for mapping in ai_result.file_mapping:
            key = (mapping.tmdb_season, mapping.tmdb_episode)
            ai_mapping_index[key] = mapping

        # 记录季度映射信息，并提取 AI 识别的相关季度
        relevant_seasons: set[int] = set()
        if ai_result.season_mapping:
            logger.info("[AI处理] AI季度映射:")
            for season_map in ai_result.season_mapping:
                logger.info(
                    f"  {season_map.local_group_name} -> TMDB季度: {season_map.maps_to_tmdb_seasons}"
                )
                # 收集 AI 识别的相关季度
                for s in season_map.maps_to_tmdb_seasons:
                    relevant_seasons.add(s)

        # 无论 season_mapping 是否完整，都并入 file_mapping 中出现的季度
        for mapping in ai_result.file_mapping:
            relevant_seasons.add(mapping.tmdb_season)

        # 从 work_path 提取剧集标题
        series_title = FilenameBuilder.extract_title_from_folder(work_path.name)

        try:
            # 以 TMDB 季度为主遍历，但只处理 AI 识别的相关季度
            tmdb_seasons = anime_info.get("seasons", [])
            matched_count = 0
            missing_count = 0

            for season in tmdb_seasons:
                season_num = season.get("season_number", 0)
                # 优先使用 episodes 数组长度，因为 episode_count 可能为 0
                episodes = season.get("episodes", [])
                episode_count = len(episodes) if episodes else season.get("episode_count", 0)

                # 跳过 AI 未识别的季度
                if season_num not in relevant_seasons:
                    continue

                logger.info(
                    f"[AI处理] 处理 Season {season_num}，TMDB 共 {episode_count} 集"
                )

                # 遍历该季度的每一集
                for ep_num in range(1, episode_count + 1):
                    key = (season_num, ep_num)

                    # 在 AI 映射中查找对应的本地文件
                    if key not in ai_mapping_index:
                        logger.warning(
                            f"[AI处理] 缺失: S{season_num:02d}E{ep_num:02d} "
                            f"- 未在本地文件中找到匹配"
                        )
                        missing_count += 1
                        continue

                    mapping = ai_mapping_index[key]
                    relative_path_str = mapping.file_path
                    confidence = mapping.confidence

                    # 从相对路径还原绝对路径
                    source_path = (base_path / relative_path_str).resolve()

                    if not source_path.exists():
                        logger.warning(
                            f"[AI处理] S{season_num:02d}E{ep_num:02d} "
                            f"- 文件路径不存在: {source_path}"
                        )
                        missing_count += 1
                        continue

                    # 确定目标目录
                    target_dir = (
                        work_path / FilenameBuilder.build_season_folder(season_num)
                    )
                    target_dir.mkdir(parents=True, exist_ok=True)

                    # 提取分集信息
                    part = extract_part(source_path.name)
                    resource_term = self._extract_resource_term(source_path.name)
                    release_group = self._extract_release_group(source_path.name)

                    # 生成新的文件名
                    meta = EpisodeMetadata(
                        title=series_title,
                        season=season_num,
                        episode=ep_num,
                        part=part,
                        resource_term=resource_term,
                        release_group=release_group,
                        file_ext=source_path.suffix,
                    )
                    new_video_filename = FilenameBuilder.build_episode_filename(meta)

                    # 添加视频文件映射
                    target_video_path = target_dir / new_video_filename
                    new_mapping[source_path] = target_video_path
                    matched_count += 1

                    logger.info(
                        f"[AI处理] 匹配: {source_path.name} -> {new_video_filename} "
                        f"(置信度: {confidence})"
                    )

                    # 查找并添加关联文件的映射
                    self._add_associated_files(
                        source_path,
                        new_video_filename,
                        target_dir,
                        associated_file_index,
                        new_mapping,
                    )

            logger.info(
                f"[AI处理] TMDB匹配完成: 匹配 {matched_count} 集, 缺失 {missing_count} 集"
            )

        except Exception as e:
            logger.error(
                f"[AI处理] 应用AI映射时发生严重错误: {str(e)}", exc_info=True
            )
            return {}

        return new_mapping

    def _add_associated_files(
        self,
        source_path: Path,
        new_video_filename: str,
        target_dir: Path,
        associated_file_index: Dict[str, List[Path]],
        new_mapping: Dict[Path, Path],
    ) -> None:
        """
        查找并添加关联文件（如字幕）的映射

        Args:
            source_path: 源视频文件路径
            new_video_filename: 新视频文件名
            target_dir: 目标目录
            associated_file_index: 关联文件索引（video_stem -> files）
            new_mapping: 映射字典（会被修改）
        """
        video_filename = source_path.stem
        source_resolved = source_path.resolve()
        candidate_files = associated_file_index.get(video_filename, [])

        for other_file in candidate_files:
            if not other_file.is_file():
                continue

            # 跳过源文件本身
            if other_file.resolve() == source_resolved:
                continue

            # 跳过已经在映射中的文件
            if other_file in new_mapping:
                continue

            # 排除视频文件本身（如 E01.mkv 不应匹配 E01.mp4）
            if other_file.suffix.lower() in VIDEO_SUFFIX:
                continue

            # 仅处理字幕关联文件
            if other_file.suffix.lower() not in SUBTITLE_EXTENSIONS:
                continue

            suffix_part = other_file.name[len(video_filename):]
            new_video_stem = new_video_filename.rsplit(".", 1)[0]

            # 把 .sc/.tc/.chs/.cht 等转换为 Emby 语言码，并按字幕导入规则命名
            emby_lang, is_simplified = self.subtitle_processor._normalize_language(
                self.subtitle_processor._extract_language_from_suffix_part(suffix_part)
            )

            subtitle_ext = other_file.suffix.lower()
            if is_simplified:
                new_associated_filename = (
                    f"{new_video_stem}.{emby_lang}.default{subtitle_ext}"
                )
            else:
                new_associated_filename = f"{new_video_stem}.{emby_lang}{subtitle_ext}"

            target_associated_path = target_dir / new_associated_filename
            new_mapping[other_file] = target_associated_path
            logger.info(
                f"[AI处理] 关联文件: {other_file.name} -> {new_associated_filename}"
            )

    def _extract_release_group(self, filename: str) -> str:
        """从文件名提取字幕组/发布组（如 [LoliHouse]）。"""
        if not filename:
            return ""

        match = re.match(r"^\s*\[([^\]]+)\]", filename)
        if not match:
            return ""

        return match.group(1).strip()

    def _extract_resource_term(self, filename: str) -> str:
        """从文件名提取质量信息（分辨率 + 编码/音频标签）。"""
        if not filename:
            return ""

        parts: List[str] = []
        video_format = extract_video_format(filename)
        if video_format:
            parts.append(video_format)

        upper_name = filename.upper()
        for token, display in self.RESOURCE_TOKEN_MAP:
            if token in upper_name and display not in parts:
                parts.append(display)

        return " ".join(parts)

    def _collect_all_local_files(self, base_path: Path) -> List[Path]:
        """收集基础路径下所有本地文件（包含视频与关联文件）。"""
        if base_path.is_dir():
            return [item for item in base_path.rglob("*") if item.is_file()]

        parent = base_path.parent
        if not parent.exists():
            return [base_path] if base_path.is_file() else []

        return [item for item in parent.iterdir() if item.is_file()]

    def _build_associated_file_index(
        self,
        all_local_files: List[Path],
    ) -> Dict[str, List[Path]]:
        """按 video_stem 建立关联文件索引，避免 O(n²) 扫描。"""
        index: Dict[str, List[Path]] = {}

        for file_path in all_local_files:
            if not file_path.is_file():
                continue

            if file_path.suffix.lower() in VIDEO_SUFFIX:
                continue

            name = file_path.name
            dot_index = name.find(".")
            if dot_index <= 0:
                continue

            video_stem = name[:dot_index]
            if not video_stem:
                continue

            index.setdefault(video_stem, []).append(file_path)

        return index

    def _collect_video_files(self, path: Path) -> List[Path]:
        """收集指定路径下的所有视频文件（过滤宣传内容）"""
        video_files = []
        skipped_promo = 0

        if path.is_file():
            if path.suffix.lower() in VIDEO_SUFFIX:
                if is_promotional_content(path.name):
                    logger.debug(f"[AI处理] 跳过宣传内容: {path.name}")
                else:
                    video_files.append(path)
        else:
            for item in path.rglob("*"):
                if item.is_file() and item.suffix.lower() in VIDEO_SUFFIX:
                    if is_promotional_content(item.name):
                        logger.debug(f"[AI处理] 跳过宣传内容: {item.name}")
                        skipped_promo += 1
                    else:
                        video_files.append(item)

        if skipped_promo > 0:
            logger.info(f"[AI处理] 跳过 {skipped_promo} 个宣传内容文件")

        return sorted(video_files)

    def _log_low_confidence_result(self, path: Path, ai_result: AIAnalysisResult):
        """记录低置信度结果到单独日志"""
        confidence = ai_result.confidence
        reason = ai_result.reason

        logger.warning(
            f"[AI低置信度] 路径: {path} | 置信度: {confidence} | "
            f"理由: {reason} | 映射数量: {len(ai_result.file_mapping)}"
        )

        # 记录季度映射
        if ai_result.season_mapping:
            for season_map in ai_result.season_mapping:
                logger.warning(
                    f"[AI低置信度] 季度映射: {season_map.local_group_name} -> {season_map.maps_to_tmdb_seasons}"
                )

        # 详细记录每个映射的置信度
        for mapping in ai_result.file_mapping:
            if mapping.confidence == "Low":
                logger.warning(
                    f"[AI低置信度文件] {mapping.file_path} -> "
                    f"S{mapping.tmdb_season:02d}E{mapping.tmdb_episode:02d} "
                    f"(类型: {mapping.episode_type}, 置信度: {mapping.confidence})"
                )

        # 记录额外说明
        if ai_result.extra_notes:
            logger.warning(f"[AI低置信度] 额外说明: {ai_result.extra_notes}")
