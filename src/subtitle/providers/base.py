from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class SubtitleThreadPackageLink:
    url: str
    kind: str
    label: str = ""
    filename_hint: str = ""
    is_direct_download: bool = False


@dataclass
class SubtitleThreadPackage:
    package_id: str
    page_number: int
    floor_label: str = ""
    post_author: Optional[str] = None
    post_time: Optional[str] = None
    post_text: str = ""
    context_text: str = ""
    links: List[SubtitleThreadPackageLink] = field(default_factory=list)
    has_direct_download: bool = False
    package_flags: List[str] = field(default_factory=list)


@dataclass
class SubtitleCandidate:
    title: str
    detail_url: str
    source: str
    publish_time: Optional[str] = None
    author: Optional[str] = None
    forum: Optional[str] = None
    snippet: Optional[str] = None
    attachment_urls: List[str] = field(default_factory=list)
    external_urls: List[str] = field(default_factory=list)
    metadata: Dict[str, str] = field(default_factory=dict)
    thread_packages: List[SubtitleThreadPackage] = field(default_factory=list)
    pages_scanned: int = 0
    pagination_truncated: bool = False


@dataclass
class SubtitleDownloadResult:
    candidate: SubtitleCandidate
    downloaded_path: Optional[Path]
    download_url: Optional[str]
    status: str
    error: Optional[str] = None
    selected_package: Optional[SubtitleThreadPackage] = None
    # 下载实际尝试次数（含网络瞬时错误重试）；1 = 一次成功，>1 = 重试过
    download_attempts: int = 1


class SubtitleProvider(ABC):
    provider_id = "unknown"

    def configure_context(
        self,
        task_data: Dict[str, Any],
        missing_videos: List[Path],
    ) -> None:
        """绑定结构化搜索来源需要的任务事实。"""

    def prepare_candidate(
        self, candidate: SubtitleCandidate
    ) -> SubtitleCandidate:
        return candidate

    def load_thread_packages(
        self, candidate: SubtitleCandidate
    ) -> SubtitleCandidate:
        return self.prepare_candidate(candidate)

    @abstractmethod
    def search(self, keyword: str, limit: int = 10) -> List[SubtitleCandidate]:
        raise NotImplementedError

    @abstractmethod
    def download(
        self,
        candidate: SubtitleCandidate,
        destination_dir: Path,
        package: Optional[SubtitleThreadPackage] = None,
        download_url: Optional[str] = None,
    ) -> SubtitleDownloadResult:
        raise NotImplementedError
