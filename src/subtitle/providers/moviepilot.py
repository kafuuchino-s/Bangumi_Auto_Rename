from __future__ import annotations

import hashlib
import ntpath
import os
import posixpath
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import urlparse
from uuid import uuid4

from ...config.config_manager import cm
from ...logger import logger
from ...moviepilot import MoviePilotAPIError, MoviePilotClient
from .base import (
    SubtitleCandidate,
    SubtitleDownloadResult,
    SubtitleProvider,
    SubtitleThreadPackage,
    SubtitleThreadPackageLink,
)


@dataclass(frozen=True)
class _MoviePilotSearchTarget:
    tmdb_id: int
    media_type: str
    season: int | None = None
    episode: int | None = None


class MoviePilotProvider(SubtitleProvider):
    """MoviePilot subtitle adapter; semantic selection remains Agent-owned."""

    provider_id = "moviepilot"
    _STAGING_NAME_RE = re.compile(r"^bar-auto-fetch-[0-9a-f]{32}$")

    def __init__(
        self,
        *,
        client: MoviePilotClient | None = None,
        save_path: str | None = None,
    ) -> None:
        timeout = int(
            cm.get_config("subtitle_auto_fetch_timeout_seconds") or 30
        )
        self.client = client or MoviePilotClient(
            base_url=str(
                cm.get_config("subtitle_auto_fetch_moviepilot_base_url") or ""
            ),
            api_token=str(
                cm.get_config("subtitle_auto_fetch_moviepilot_api_token") or ""
            ),
            timeout=timeout,
        )
        self.save_path = str(
            save_path
            if save_path is not None
            else (
                cm.get_config("subtitle_auto_fetch_moviepilot_save_path") or ""
            )
        ).strip()
        self._title = ""
        self._year: int | None = None
        self._targets: list[_MoviePilotSearchTarget] = []
        self._exact_cache: list[
            tuple[dict[str, Any], int | None]
        ] | None = None
        self._title_cache: dict[
            str, list[tuple[dict[str, Any], int | None]]
        ] = {}
        self._payloads_by_identity: dict[str, dict[str, Any]] = {}
        self._cache_lock = Lock()

    def configure_context(
        self,
        task_data: Dict[str, Any],
        missing_videos: List[Path],
    ) -> None:
        self._title = str(
            task_data.get("tmdb_name")
            or task_data.get("name")
            or task_data.get("bgm_subject_name_cn")
            or task_data.get("bgm_subject_name")
            or ""
        ).strip()
        self._year = self._optional_int(
            task_data.get("tmdb_year") or task_data.get("year")
        )
        refs = self._tmdb_refs(task_data)
        seasons = self._missing_seasons(task_data, missing_videos)
        episode = self._single_missing_episode(missing_videos)

        targets: list[_MoviePilotSearchTarget] = []
        for media_type, tmdb_id in refs:
            target_seasons = (
                seasons if media_type == "tv" and seasons else [None]
            )
            for season in target_seasons:
                targets.append(
                    _MoviePilotSearchTarget(
                        tmdb_id=tmdb_id,
                        media_type=media_type,
                        season=season,
                        episode=episode if len(target_seasons) == 1 else None,
                    )
                )
                if len(targets) >= 8:
                    break
            if len(targets) >= 8:
                break

        with self._cache_lock:
            self._targets = targets
            self._exact_cache = None
            self._title_cache.clear()
            self._payloads_by_identity.clear()

    def search(self, keyword: str, limit: int = 10) -> List[SubtitleCandidate]:
        keyword = str(keyword or "").strip()
        if not keyword:
            return []

        exact_rows = self._exact_rows()
        if exact_rows:
            return self._rows_to_candidates(exact_rows)[:limit]

        title_rows = self._title_rows(keyword)
        return self._rows_to_candidates(title_rows)[:limit]

    def prepare_candidate(
        self, candidate: SubtitleCandidate
    ) -> SubtitleCandidate:
        return candidate

    def load_thread_packages(
        self, candidate: SubtitleCandidate
    ) -> SubtitleCandidate:
        if candidate.thread_packages:
            return candidate
        payload = self._candidate_payload(candidate)
        provider_url = self._provider_download_url(candidate)
        if not payload or not payload.get("enclosure") or not provider_url:
            return candidate

        site_name = str(payload.get("site_name") or "MoviePilot")
        language = str(payload.get("language") or "未知")
        package = SubtitleThreadPackage(
            package_id=f"moviepilot:{self._candidate_identity(payload)}",
            page_number=1,
            floor_label=f"{site_name} / {language}",
            post_author=str(payload.get("uploader") or "") or None,
            post_time=str(payload.get("pubdate") or "") or None,
            post_text=self._fact_text(payload),
            context_text=str(payload.get("description") or ""),
            links=[
                SubtitleThreadPackageLink(
                    url=provider_url,
                    kind="attachment",
                    label=str(payload.get("file_name") or candidate.title),
                    filename_hint=str(
                        payload.get("file_name") or candidate.title
                    ),
                    is_direct_download=True,
                )
            ],
            has_direct_download=True,
            package_flags=[],
        )
        candidate.thread_packages = [package]
        candidate.attachment_urls = [provider_url]
        candidate.pages_scanned = 1
        return candidate

    def download(
        self,
        candidate: SubtitleCandidate,
        destination_dir: Path,
        package: Optional[SubtitleThreadPackage] = None,
        download_url: Optional[str] = None,
    ) -> SubtitleDownloadResult:
        selected_url = download_url or self._package_url(package)
        payload = self._candidate_payload(candidate)
        provider_url = self._provider_download_url(candidate)
        if selected_url != provider_url:
            return self._failed_result(
                candidate,
                package,
                selected_url,
                "MoviePilot selected link does not belong to the candidate",
            )
        if not payload.get("enclosure"):
            return self._failed_result(
                candidate,
                package,
                selected_url,
                "MoviePilot candidate has no download link",
            )
        if not self.save_path:
            return self._failed_result(
                candidate,
                package,
                selected_url,
                "MoviePilot subtitle staging path is not configured",
            )

        remote_staging = self._new_remote_staging_path()
        tmdb_id = self._optional_int(
            candidate.metadata.get("moviepilot_tmdb_id")
        )
        try:
            remote_files = self.client.download_subtitle(
                payload,
                tmdb_id=tmdb_id,
                save_path=remote_staging,
            )
            local_staging = self._localize_path(remote_staging)
            local_files = self._resolve_downloaded_files(
                remote_files,
                remote_staging,
            )
            destination_dir.mkdir(parents=True, exist_ok=True)
            output = self._copy_downloaded_files(
                local_files,
                destination_dir,
                local_staging,
            )
            self._cleanup_staging(local_staging)
            return SubtitleDownloadResult(
                candidate=candidate,
                downloaded_path=output,
                download_url=selected_url,
                status="success",
                selected_package=package,
                download_attempts=1,
            )
        except Exception as exc:
            logger.warning(f"[字幕抓取][MoviePilot] 下载失败: {exc}")
            return self._failed_result(
                candidate,
                package,
                selected_url,
                str(exc),
            )

    def _exact_rows(self) -> list[tuple[dict[str, Any], int | None]]:
        with self._cache_lock:
            if self._exact_cache is not None:
                return list(self._exact_cache)
            rows: list[tuple[dict[str, Any], int | None]] = []
            for target in self._targets:
                try:
                    found = self.client.search_subtitles_by_media(
                        f"tmdb:{target.tmdb_id}",
                        media_type=target.media_type,
                        title=self._title,
                        year=self._year,
                        season=target.season,
                        episode=target.episode,
                    )
                except MoviePilotAPIError as exc:
                    logger.warning(
                        "[字幕抓取][MoviePilot] 精确媒体搜索失败: "
                        f"tmdb:{target.tmdb_id} - {exc}"
                    )
                    continue
                rows.extend((row, target.tmdb_id) for row in found)
            usable_rows = self._usable_rows(rows)
            ignored_count = len(rows) - len(usable_rows)
            if ignored_count:
                logger.warning(
                    "[字幕抓取][MoviePilot] 忽略 "
                    f"{ignored_count} 条下载 API 不支持的字幕链接"
                )
            self._exact_cache = self._dedupe_rows(usable_rows)
            return list(self._exact_cache)

    def _title_rows(
        self, keyword: str
    ) -> list[tuple[dict[str, Any], int | None]]:
        folded = keyword.casefold()
        with self._cache_lock:
            cached = self._title_cache.get(folded)
        if cached is not None:
            return list(cached)

        found = self.client.search_subtitles_by_title(keyword)
        tmdb_id = self._targets[0].tmdb_id if self._targets else None
        rows = self._dedupe_rows(
            self._usable_rows([(row, tmdb_id) for row in found])
        )
        with self._cache_lock:
            cached = self._title_cache.setdefault(folded, rows)
        return list(cached)

    def _rows_to_candidates(
        self, rows: list[tuple[dict[str, Any], int | None]]
    ) -> list[SubtitleCandidate]:
        candidates: list[SubtitleCandidate] = []
        for row, tmdb_id in rows:
            payload = MoviePilotClient.sanitize_subtitle(row)
            identity = self._candidate_identity(payload)
            metadata = {
                "moviepilot_identity": identity,
                "moviepilot_tmdb_id": str(tmdb_id or ""),
                "moviepilot_page_url": str(payload.get("page_url") or ""),
                "moviepilot_site_name": str(payload.get("site_name") or ""),
            }
            provider_url = f"moviepilot://download/{identity}"
            with self._cache_lock:
                self._payloads_by_identity[identity] = payload
            candidates.append(
                SubtitleCandidate(
                    title=str(payload.get("title") or "MoviePilot subtitle"),
                    detail_url=f"moviepilot://subtitle/{identity}",
                    source=self.provider_id,
                    publish_time=str(payload.get("pubdate") or "") or None,
                    author=str(payload.get("uploader") or "") or None,
                    forum=str(payload.get("site_name") or "") or None,
                    snippet=self._fact_text(payload),
                    attachment_urls=[provider_url],
                    metadata=metadata,
                )
            )
        return candidates

    @staticmethod
    def _usable_rows(
        rows: list[tuple[dict[str, Any], int | None]],
    ) -> list[tuple[dict[str, Any], int | None]]:
        result: list[tuple[dict[str, Any], int | None]] = []
        for row in rows:
            parsed = urlparse(str(row[0].get("enclosure") or ""))
            if parsed.scheme in {"http", "https"} and parsed.netloc:
                result.append(row)
        return result

    @classmethod
    def _dedupe_rows(
        cls,
        rows: list[tuple[dict[str, Any], int | None]],
    ) -> list[tuple[dict[str, Any], int | None]]:
        result: list[tuple[dict[str, Any], int | None]] = []
        seen: set[str] = set()
        for row, tmdb_id in rows:
            identity = cls._candidate_identity(row)
            if identity in seen:
                continue
            seen.add(identity)
            result.append((row, tmdb_id))
        return result

    @staticmethod
    def _candidate_identity(payload: Mapping[str, Any]) -> str:
        parts = [
            str(payload.get(key) or "")
            for key in (
                "site",
                "torrent_id",
                "subtitle_id",
                "title",
                "file_name",
            )
        ]
        digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
        return digest[:20]

    @staticmethod
    def _fact_text(payload: Mapping[str, Any]) -> str:
        facts = [
            "来源=MoviePilot",
            f"站点={payload.get('site_name') or '未知'}",
            f"语言字段={payload.get('language') or '未知'}",
            f"季集={payload.get('season_episode') or '未知'}",
            f"集数={payload.get('episode_list') or '未知'}",
            f"大小={payload.get('size') or '未知'}",
            f"下载数={payload.get('grabs') or '未知'}",
        ]
        description = re.sub(
            r"\s+", " ", str(payload.get("description") or "")
        ).strip()[:500]
        if description:
            facts.append(f"描述={description}")
        return " | ".join(facts)

    def _candidate_payload(self, candidate: SubtitleCandidate) -> dict[str, Any]:
        identity = candidate.metadata.get("moviepilot_identity") or ""
        with self._cache_lock:
            return dict(self._payloads_by_identity.get(identity) or {})

    @staticmethod
    def _provider_download_url(candidate: SubtitleCandidate) -> str:
        identity = candidate.metadata.get("moviepilot_identity") or ""
        return f"moviepilot://download/{identity}" if identity else ""

    @staticmethod
    def _package_url(package: Optional[SubtitleThreadPackage]) -> str | None:
        if not package:
            return None
        for link in package.links:
            if link.is_direct_download and link.url:
                return link.url
        return None

    def _new_remote_staging_path(self) -> str:
        root = self.save_path.replace("\\", "/").rstrip("/")
        return f"{root}/bar-auto-fetch-{uuid4().hex}"

    def _resolve_downloaded_files(
        self,
        remote_files: list[str],
        remote_staging: str,
    ) -> list[Path]:
        local_files: list[Path] = []
        for remote_file in remote_files:
            if not self._remote_path_contains(remote_staging, remote_file):
                raise MoviePilotAPIError(
                    "MoviePilot returned a file outside the requested "
                    "staging path"
                )
            local_file = self._localize_path(remote_file)
            if not local_file.is_file():
                raise MoviePilotAPIError(
                    f"MoviePilot file is not visible to BAR: {local_file}"
                )
            local_files.append(local_file)
        if not local_files:
            raise MoviePilotAPIError(
                "MoviePilot returned no readable subtitle files"
            )
        return local_files

    @staticmethod
    def _remote_path_contains(root: str, path: str) -> bool:
        root_slash = root.replace("\\", "/")
        path_slash = path.replace("\\", "/")
        try:
            if re.match(r"^[A-Za-z]:/", root_slash):
                common = ntpath.commonpath([root_slash, path_slash])
                return ntpath.normcase(common) == ntpath.normcase(
                    ntpath.normpath(root_slash)
                )
            common = posixpath.commonpath([root_slash, path_slash])
            return common == posixpath.normpath(root_slash)
        except ValueError:
            return False

    @staticmethod
    def _localize_path(remote_path: str) -> Path:
        normalized = remote_path.replace("\\", "/")
        if os.name == "nt" or normalized.startswith("/"):
            return Path(normalized)

        docker_mnt = str(cm.get_config("docker_mnt") or "/media").rstrip("/")
        host_prefix = str(cm.get_config("host_path_prefix") or "")
        normalized_prefix = host_prefix.replace("\\", "/").rstrip("/")
        if normalized_prefix:
            folded = normalized.casefold()
            folded_prefix = normalized_prefix.casefold()
            prefix_matches = folded == folded_prefix or folded.startswith(
                f"{folded_prefix}/"
            )
        else:
            prefix_matches = False
        if prefix_matches:
            relative = normalized[len(normalized_prefix) :]
        elif re.match(r"^[A-Za-z]:/", normalized):
            relative = normalized[2:]
        else:
            raise MoviePilotAPIError(
                f"Cannot map MoviePilot path into BAR container: {remote_path}"
            )
        return Path(docker_mnt) / relative.lstrip("/")

    @staticmethod
    def _copy_downloaded_files(
        files: list[Path],
        destination_dir: Path,
        staging_root: Path,
    ) -> Path:
        if len(files) == 1:
            target = destination_dir / files[0].name
            shutil.copy2(files[0], target)
            return target

        target = destination_dir / "moviepilot-subtitles.zip"
        with zipfile.ZipFile(
            target, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            for subtitle_file in files:
                relative = subtitle_file.relative_to(staging_root)
                archive.write(subtitle_file, arcname=relative.as_posix())
        return target

    @classmethod
    def _cleanup_staging(cls, local_staging: Path) -> None:
        if not (
            local_staging.is_dir()
            and cls._STAGING_NAME_RE.fullmatch(local_staging.name)
        ):
            return
        try:
            shutil.rmtree(local_staging)
        except OSError as exc:
            logger.warning(
                "[字幕抓取][MoviePilot] 暂存目录清理失败: "
                f"{local_staging} - {exc}"
            )

    @staticmethod
    def _failed_result(
        candidate: SubtitleCandidate,
        package: Optional[SubtitleThreadPackage],
        download_url: Optional[str],
        error: str,
    ) -> SubtitleDownloadResult:
        return SubtitleDownloadResult(
            candidate=candidate,
            downloaded_path=None,
            download_url=download_url,
            status="failed",
            error=error,
            selected_package=package,
            download_attempts=1,
        )

    @staticmethod
    def _optional_int(value: object) -> int | None:
        if isinstance(value, bool) or value in (None, ""):
            return None
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    @classmethod
    def _tmdb_refs(cls, task_data: Mapping[str, Any]) -> list[tuple[str, int]]:
        refs: list[tuple[str, int]] = []
        raw_refs = task_data.get("bgm_to_tmdb_tmdb_refs")
        if isinstance(raw_refs, list):
            for raw in raw_refs:
                match = re.fullmatch(r"(tv|movie):(\d+)", str(raw or ""))
                if match:
                    item = (match.group(1), int(match.group(2)))
                    if item not in refs:
                        refs.append(item)

        tmdb_id = cls._optional_int(task_data.get("tmdb_id"))
        if tmdb_id is not None:
            media_type = str(task_data.get("tmdb_media_type") or "").lower()
            if media_type not in {"tv", "movie"}:
                media_type = "movie" if task_data.get("is_movie") else "tv"
            item = (media_type, tmdb_id)
            if item not in refs:
                refs.insert(0, item)
        return refs

    @classmethod
    def _missing_seasons(
        cls,
        task_data: Mapping[str, Any],
        missing_videos: List[Path],
    ) -> list[int]:
        seasons = {
            int(match.group(1))
            for path in missing_videos
            if (match := re.search(r"(?i)S(\d{1,2})E\d+", path.name))
        }
        if not seasons:
            season = cls._optional_int(task_data.get("season_id"))
            if season is not None:
                seasons.add(season)
        return sorted(seasons)

    @staticmethod
    def _single_missing_episode(missing_videos: List[Path]) -> int | None:
        if len(missing_videos) != 1:
            return None
        match = re.search(r"(?i)S\d{1,2}E(\d+)", missing_videos[0].name)
        return int(match.group(1)) if match else None
