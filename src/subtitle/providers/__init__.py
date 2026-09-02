from ...config.config_manager import cm
from ...logger import logger
from .acgrip import ACGRIPProvider
from .base import (
    SubtitleCandidate,
    SubtitleDownloadResult,
    SubtitleProvider,
    SubtitleThreadPackage,
    SubtitleThreadPackageLink,
)
from .combined import CombinedSubtitleProvider
from .moviepilot import MoviePilotProvider


def build_subtitle_provider() -> SubtitleProvider:
    provider_id = str(
        cm.get_config("subtitle_auto_fetch_provider") or "acgrip"
    ).strip()
    if provider_id == "moviepilot":
        return MoviePilotProvider()
    if provider_id == "acgrip_moviepilot":
        return CombinedSubtitleProvider(
            [ACGRIPProvider(), MoviePilotProvider()]
        )
    if provider_id != "acgrip":
        logger.warning(
            f"[字幕抓取] 未知 provider={provider_id}，回退到 acgrip"
        )
    return ACGRIPProvider()


__all__ = [
    "ACGRIPProvider",
    "CombinedSubtitleProvider",
    "MoviePilotProvider",
    "SubtitleCandidate",
    "SubtitleDownloadResult",
    "SubtitleProvider",
    "SubtitleThreadPackage",
    "SubtitleThreadPackageLink",
    "build_subtitle_provider",
]
