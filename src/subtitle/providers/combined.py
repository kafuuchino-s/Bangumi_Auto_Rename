from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...logger import logger
from .base import (
    SubtitleCandidate,
    SubtitleDownloadResult,
    SubtitleProvider,
    SubtitleThreadPackage,
)


class CombinedSubtitleProvider(SubtitleProvider):
    """Fan out searches, then dispatch candidate operations by source."""

    provider_id = "acgrip_moviepilot"

    def __init__(self, providers: List[SubtitleProvider]) -> None:
        self.providers = providers
        self._providers_by_id = {
            provider.provider_id: provider for provider in providers
        }

    def configure_context(
        self,
        task_data: Dict[str, Any],
        missing_videos: List[Path],
    ) -> None:
        for provider in self.providers:
            provider.configure_context(task_data, missing_videos)

    def search(self, keyword: str, limit: int = 10) -> List[SubtitleCandidate]:
        if not self.providers:
            return []
        with ThreadPoolExecutor(max_workers=len(self.providers)) as executor:
            futures = [
                executor.submit(provider.search, keyword, limit)
                for provider in self.providers
            ]
            batches: list[List[SubtitleCandidate]] = []
            errors: list[Exception] = []
            for provider, future in zip(self.providers, futures):
                try:
                    batches.append(future.result())
                except Exception as exc:
                    logger.warning(
                        f"[字幕抓取][{provider.provider_id}] "
                        f"组合搜索失败: {exc}"
                    )
                    errors.append(exc)
                    batches.append([])

        result: List[SubtitleCandidate] = []
        seen: set[str] = set()
        for batch in batches:
            for candidate in batch:
                key = candidate.detail_url or (
                    f"{candidate.source}:{candidate.title}"
                )
                if key in seen:
                    continue
                seen.add(key)
                result.append(candidate)
        if not result and errors:
            raise errors[0]
        return result

    def prepare_candidate(
        self, candidate: SubtitleCandidate
    ) -> SubtitleCandidate:
        provider = self._providers_by_id.get(candidate.source)
        return provider.prepare_candidate(candidate) if provider else candidate

    def load_thread_packages(
        self, candidate: SubtitleCandidate
    ) -> SubtitleCandidate:
        provider = self._providers_by_id.get(candidate.source)
        if provider:
            return provider.load_thread_packages(candidate)
        return candidate

    def download(
        self,
        candidate: SubtitleCandidate,
        destination_dir: Path,
        package: Optional[SubtitleThreadPackage] = None,
        download_url: Optional[str] = None,
    ) -> SubtitleDownloadResult:
        provider = self._providers_by_id.get(candidate.source)
        if provider:
            return provider.download(
                candidate,
                destination_dir,
                package=package,
                download_url=download_url,
            )
        return SubtitleDownloadResult(
            candidate=candidate,
            downloaded_path=None,
            download_url=download_url,
            status="failed",
            error=f"Unknown subtitle provider source: {candidate.source}",
            selected_package=package,
        )
