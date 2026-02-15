from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..logger import logger
from .utils import VIDEO_SUFFIX

from ..ai.client import AIClient
from ..ai.models import AIAnalysisResult
from ..ai.video_analyzer import VideoAnalyzer
from .cleaner import extract_part, is_promotional_content
from .filename_builder import FilenameBuilder, EpisodeMetadata
from ..subtitle.processor import SubtitleProcessor


class AIProcessor:
    """AI辅助处理器，用于智能分析和重命名"""

    def __init__(self):
        self.ai_client = AIClient()
        self.video_analyzer = VideoAnalyzer()
        self.subtitle_processor = SubtitleProcessor()

    def analyze_anime_files(
        self, path: Path, anime_info: Dict
    ) -> Optional[AIAnalysisResult]:
        """
        使用AI分析动漫文件的映射关系

        Args:
            path: 本地文件路径
            anime_info: TMDB动漫信息
            season_info: 特定季度信息（可选）

        Returns:
            验证后的AI分析结果
        """
        if not self.ai_client.is_available():
            logger.info("[AI处理] AI功能未启用，跳过AI分析")
            return None

        # 收集视频文件
        video_files = self._collect_video_files(path)
        if not video_files:
            logger.warning("[AI处理] 未找到视频文件")
            return None

        # 分析视频文件
        file_analysis = self.video_analyzer.analyze_video_files(path, video_files)

        # 使用AI分析映射关系
        ai_result = self.ai_client.analyze_episode_mapping(anime_info, file_analysis)

        if ai_result:
            logger.info(f"[AI处理] AI分析完成，置信度: {ai_result.confidence}")

            # 记录低置信度结果到单独日志
            if ai_result.confidence == "Low":
                self._log_low_confidence_result(path, ai_result)

        return ai_result

    def apply_ai_mapping(
        self,
        ai_result: AIAnalysisResult | None,
        anime_info: Dict,
        base_path: Path,
        work_path: Path,
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
        all_local_files = (
            list(base_path.rglob("*"))
            if base_path.is_dir()
            else list(base_path.parent.iterdir())
        )

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

        # 如果 AI 没有返回 season_mapping，从 file_mapping 中提取相关季度
        if not relevant_seasons:
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
                    episode_type = mapping.episode_type
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

                    # 生成新的文件名
                    meta = EpisodeMetadata(
                        title=series_title,
                        season=season_num,
                        episode=ep_num,
                        part=part,
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
                        all_local_files,
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
        all_local_files: List[Path],
        new_mapping: Dict[Path, Path],
    ) -> None:
        """
        查找并添加关联文件（如字幕）的映射

        Args:
            source_path: 源视频文件路径
            new_video_filename: 新视频文件名
            target_dir: 目标目录
            all_local_files: 所有本地文件列表
            new_mapping: 映射字典（会被修改）
        """
        video_filename = source_path.stem
        source_resolved = source_path.resolve()

        for other_file in all_local_files:
            if not other_file.is_file():
                continue

            # 跳过源文件本身
            if other_file.resolve() == source_resolved:
                continue

            # 跳过已经在映射中的文件
            if other_file in new_mapping:
                continue

            # 检查是否为关联文件（如 E01.chs.ass, E01.jpn.srt）
            # 关联文件的格式: {video_stem}.{extra}.{ext}
            if other_file.name.startswith(f"{video_filename}."):
                # 排除视频文件本身（如 E01.mkv 不应匹配 E01.mp4）
                from .utils import VIDEO_SUFFIX
                if other_file.suffix.lower() in VIDEO_SUFFIX:
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
