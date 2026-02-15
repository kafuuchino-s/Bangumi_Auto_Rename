"""
文件名构建器模块

按照 Movie Pilot 格式生成电影和电视剧的文件名
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class MovieMetadata:
    """电影元数据"""

    title: str
    year: Optional[str] = None
    video_format: Optional[str] = None
    part: Optional[str] = None
    file_ext: str = ".mkv"


@dataclass
class EpisodeMetadata:
    """剧集元数据"""

    title: str
    season: int = 1
    episode: int = 1
    year: Optional[str] = None
    part: Optional[str] = None
    file_ext: str = ".mkv"


class FilenameBuilder:
    """
    文件名构建器

    电影格式:
        {title} ({year})/{title} ({year})-{part} - {videoFormat}{fileExt}
        示例: 空之境界 (2007)/空之境界 (2007) - 1080p.mkv

    电视剧格式:
        {title} ({year})/Season {season}/{title} - S{ss}E{ee}-{part} - 第 {episode} 集{fileExt}
        示例: 葬送的芙莉莲 (2023)/Season 1/葬送的芙莉莲 - S01E01 - 第 1 集.mkv
    """

    @staticmethod
    def build_title_with_year(title: str, year: Optional[str] = None) -> str:
        """
        构建带年份的标题

        Args:
            title: 标题
            year: 年份 (可选)

        Returns:
            "{title} ({year})" 或 "{title}"
        """
        if year:
            return f"{title} ({year})"
        return title

    @staticmethod
    def build_season_folder(season: int) -> str:
        """
        构建季度目录名

        Args:
            season: 季度号

        Returns:
            "Season {season}"
        """
        return f"Season {season}"

    @staticmethod
    def build_movie_folder(title: str, year: Optional[str] = None) -> str:
        """
        构建电影目录名

        Args:
            title: 电影标题
            year: 年份 (可选)

        Returns:
            "{title} ({year})" 或 "{title}"
        """
        return FilenameBuilder.build_title_with_year(title, year)

    @staticmethod
    def build_movie_filename(meta: MovieMetadata) -> str:
        """
        构建电影文件名

        格式: {title} ({year})-{part} - {videoFormat}{fileExt}

        示例:
            - 空之境界 (2007) - 1080p.mkv
            - 空之境界 (2007)-Part1 - 1080p.mkv
            - 空之境界 (2007).mkv (无格式信息)
        """
        parts = []

        # 基础: 标题 (年份)
        base = FilenameBuilder.build_title_with_year(meta.title, meta.year)

        # 添加分集信息 (直接连接，无空格)
        if meta.part:
            base = f"{base}-{meta.part}"

        parts.append(base)

        # 添加视频格式
        if meta.video_format:
            parts.append(meta.video_format)

        # 用 " - " 连接并添加扩展名
        return " - ".join(parts) + meta.file_ext

    @staticmethod
    def build_episode_filename(meta: EpisodeMetadata) -> str:
        """
        构建剧集文件名

        格式: {title} - S{ss}E{ee}-{part} - 第 {episode} 集{fileExt}

        示例:
            - 葬送的芙莉莲 - S01E01 - 第 1 集.mkv
            - 葬送的芙莉莲 - S01E01-Part1 - 第 1 集.mkv
        """
        season_str = f"{meta.season:02d}"
        episode_str = f"{meta.episode:02d}"

        # 季集代码
        season_episode = f"S{season_str}E{episode_str}"

        # 添加分集信息
        if meta.part:
            season_episode = f"{season_episode}-{meta.part}"

        # 集数标签
        episode_label = f"第 {meta.episode} 集"

        # 构建完整文件名
        return f"{meta.title} - {season_episode} - {episode_label}{meta.file_ext}"

    @staticmethod
    def build_tv_work_path(
        base_path: Path, title: str, year: Optional[str] = None
    ) -> Path:
        """
        构建电视剧工作路径

        格式: {base_path}/{title} ({year})
        """
        folder = FilenameBuilder.build_movie_folder(title, year)
        return base_path / folder

    @staticmethod
    def build_movie_work_path(
        base_path: Path, title: str, year: Optional[str] = None
    ) -> Path:
        """
        构建电影工作路径

        格式: {base_path}/{title} ({year})
        """
        return FilenameBuilder.build_tv_work_path(base_path, title, year)

    @staticmethod
    def extract_title_from_folder(folder_name: str) -> str:
        """
        从目录名中提取标题 (移除年份)

        示例:
            "葬送的芙莉莲 (2023)" -> "葬送的芙莉莲"
            "空之境界" -> "空之境界"
        """
        match = re.match(r'^(.+?)\s*\(\d{4}\)$', folder_name)
        if match:
            return match.group(1).strip()
        return folder_name
