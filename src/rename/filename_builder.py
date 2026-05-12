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
    resource_term: Optional[str] = None
    release_group: Optional[str] = None
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
    resource_term: Optional[str] = None
    release_group: Optional[str] = None
    file_ext: str = ".mkv"


class FilenameBuilder:
    """
    文件名构建器

    电影格式:
        {title} ({year}){-part} - {resourceTerm} - {releaseGroup}{fileExt}
        示例: 空之境界 (2007)-Part1 - BluRay 1080p HEVC - VCB-Studio.mkv

    电视剧格式:
        {title} ({year})/Season {season:02d}/{title} - S{ss}E{ee}{-part} - {resourceTerm} - {releaseGroup}{fileExt}
        示例: 葬送的芙莉莲 (2023)/Season 01/葬送的芙莉莲 - S01E01 - WEB-DL 1080p HEVC - LoliHouse.mkv
    """

    @staticmethod
    def sanitize_path_component(value: str) -> str:
        """清理 Windows/POSIX 路径组件中的非法字符，保留标题语义。"""

        cleaned = re.sub(r'[<>:"/\\|?*]', ' ', value or '')
        cleaned = re.sub(r'\s+', ' ', cleaned).strip().rstrip(' .')
        return cleaned or 'Unknown'

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
        safe_title = FilenameBuilder.sanitize_path_component(title)
        if year:
            return f"{safe_title} ({year})"
        return safe_title

    @staticmethod
    def build_season_folder(season: int) -> str:
        """
        构建季度目录名

        Args:
            season: 季度号

        Returns:
            "Season {season:02d}"
        """
        return f"Season {season:02d}"

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

        格式:
            {title} ({year}){-part} - {resourceTerm} - {releaseGroup}{fileExt}

        示例:
            - 空之境界 (2007) - BluRay 1080p HEVC - VCB-Studio.mkv
            - 空之境界 (2007)-Part1 - BluRay 1080p - VCB-Studio.mkv
            - 空之境界 (2007).mkv
        """
        head = FilenameBuilder.build_title_with_year(meta.title, meta.year)
        if meta.part:
            head = f"{head}-{meta.part}"

        detail = meta.resource_term or meta.video_format
        release_group = (meta.release_group or "").strip()

        parts = [head]
        if detail:
            parts.append(detail)
        if release_group:
            parts.append(release_group)

        return FilenameBuilder.sanitize_path_component(" - ".join(parts)) + meta.file_ext

    @staticmethod
    def build_episode_filename(meta: EpisodeMetadata) -> str:
        """
        构建剧集文件名

        格式:
            {title} - S{ss}E{ee}{-part} - {resourceTerm} - {releaseGroup}{fileExt}

        示例:
            - 葬送的芙莉莲 - S01E01 - WEB-DL 1080p HEVC - LoliHouse.mkv
            - 葬送的芙莉莲 - S01E01-Part1 - WEB-DL 1080p - LoliHouse.mkv
            - 葬送的芙莉莲 - S01E01.mkv
        """
        season_str = f"{meta.season:02d}"
        episode_str = f"{meta.episode:02d}"

        season_episode = f"S{season_str}E{episode_str}"
        if meta.part:
            season_episode = f"{season_episode}-{meta.part}"

        detail = meta.resource_term
        release_group = (meta.release_group or "").strip()

        parts = [FilenameBuilder.sanitize_path_component(meta.title), season_episode]
        if detail:
            parts.append(detail)
        if release_group:
            parts.append(release_group)

        return FilenameBuilder.sanitize_path_component(" - ".join(parts)) + meta.file_ext

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
