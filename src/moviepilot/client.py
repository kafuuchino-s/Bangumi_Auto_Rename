from __future__ import annotations

import json
import ntpath
import re
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, unquote, urldefrag, urlparse

import requests


class MoviePilotAPIError(RuntimeError):
    """MoviePilot request or response contract failure."""


class MoviePilotClient:
    """Small authenticated client for MoviePilot's stable v1 API."""

    _DOWNLOAD_HISTORY_FIELDS = (
        "id",
        "path",
        "type",
        "title",
        "year",
        "tmdbid",
        "seasons",
        "episodes",
        "download_hash",
        "torrent_name",
        "torrent_site",
        "date",
    )
    _DOWNLOAD_TASK_FIELDS = (
        "downloader",
        "hash",
        "progress",
    )

    _SUBTITLE_FIELDS = (
        "site",
        "site_name",
        "site_order",
        "title",
        "description",
        "enclosure",
        "page_url",
        "language",
        "language_icon",
        "size",
        "pubdate",
        "date_elapsed",
        "grabs",
        "uploader",
        "report_url",
        "torrent_id",
        "subtitle_id",
        "file_name",
        "season_episode",
        "episode_list",
    )

    def __init__(
        self,
        *,
        base_url: str,
        api_token: str,
        timeout: int = 30,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.api_token = str(api_token or "").strip()
        self.timeout = max(1, int(timeout))
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "Bangumi-Auto-Rename/1.0",
            }
        )
        if self.api_token:
            self.session.headers["X-API-KEY"] = self.api_token

    @classmethod
    def configured(cls, *, timeout: int = 30) -> "MoviePilotClient":
        from ..config.config_manager import cm

        return cls(
            base_url=str(cm.get_config("moviepilot_base_url") or ""),
            api_token=str(cm.get_config("moviepilot_api_token") or ""),
            timeout=timeout,
        )

    def list_downloaders(self) -> list[dict[str, Any]]:
        payload = self._request_json("GET", "/api/v1/download/clients")
        if not isinstance(payload, list):
            return []
        return [
            {key: item[key] for key in ("name", "type") if key in item}
            for item in payload
            if isinstance(item, Mapping)
        ]

    def list_download_history(
        self,
        *,
        page: int = 1,
        count: int = 100,
    ) -> list[dict[str, Any]]:
        payload = self._request_json(
            "GET",
            "/api/v1/history/download",
            params={
                "page": max(1, int(page)),
                "count": max(1, min(500, int(count))),
            },
        )
        if isinstance(payload, Mapping):
            payload = payload.get("data")
        if not isinstance(payload, list):
            raise MoviePilotAPIError(
                "MoviePilot download history returned invalid rows"
            )
        return [
            {
                key: row[key]
                for key in self._DOWNLOAD_HISTORY_FIELDS
                if key in row
            }
            for row in payload
            if isinstance(row, Mapping)
        ]

    def get_download_task(self, download_hash: str) -> dict[str, Any] | None:
        normalized_hash = str(download_hash or "").strip().lower()
        if not normalized_hash:
            return None
        payload = self._request_json(
            "POST",
            "/api/v1/mcp/tools/call",
            json={
                "tool_name": "query_download_tasks",
                "arguments": {
                    "hash": normalized_hash,
                    "include_all_tags": True,
                },
            },
        )
        if not isinstance(payload, Mapping) or payload.get("success") is False:
            raise MoviePilotAPIError(
                "MoviePilot download task lookup failed"
            )
        result = payload.get("result")
        if not isinstance(result, str):
            return None
        start = result.find("[")
        if start < 0:
            if "未找到" in result or "not found" in result.casefold():
                return None
            raise MoviePilotAPIError(
                "MoviePilot download task lookup returned invalid rows"
            )
        try:
            rows, _ = json.JSONDecoder().raw_decode(result[start:])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise MoviePilotAPIError(
                "MoviePilot download task lookup returned invalid rows"
            ) from exc
        if not isinstance(rows, list):
            return None
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            if str(row.get("hash") or "").strip().lower() != normalized_hash:
                continue
            return {
                key: row[key]
                for key in self._DOWNLOAD_TASK_FIELDS
                if key in row
            }
        if rows:
            raise MoviePilotAPIError(
                "MoviePilot download task lookup returned mismatched hash"
            )
        return None

    def search_subtitles_by_title(
        self,
        keyword: str,
        *,
        page: int = 0,
        sites: str = "",
        include_download_auth: bool = False,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"keyword": keyword, "page": page}
        if sites:
            params["sites"] = sites
        payload = self._request_json(
            "GET",
            "/api/v1/search/subtitle/title",
            params=params,
        )
        return self._subtitle_rows(
            payload,
            include_download_auth=include_download_auth,
        )

    def search_subtitles_by_media(
        self,
        media_id: str,
        *,
        media_type: str,
        title: str = "",
        year: int | None = None,
        season: int | None = None,
        episode: int | None = None,
        include_download_auth: bool = False,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"mtype": media_type}
        if title:
            params["title"] = title
        if year is not None:
            params["year"] = year
        if season is not None:
            params["season"] = season
        if episode is not None:
            params["episode"] = episode
        payload = self._request_json(
            "GET",
            f"/api/v1/search/subtitle/media/{quote(media_id, safe=':')}",
            params=params,
        )
        return self._subtitle_rows(
            payload,
            include_download_auth=include_download_auth,
        )

    @staticmethod
    def can_download_subtitle_direct(
        subtitle: Mapping[str, Any],
    ) -> bool:
        parsed = urlparse(str(subtitle.get("enclosure") or ""))
        return (
            parsed.scheme in {"http", "https"}
            and bool(parsed.netloc)
            and not bool(subtitle.get("site_proxy"))
        )

    def download_subtitle_direct(
        self,
        subtitle: Mapping[str, Any],
        *,
        destination_dir: Path,
    ) -> Path:
        """Download one MP search result without persisting site secrets."""
        if not self.can_download_subtitle_direct(subtitle):
            raise MoviePilotAPIError(
                "MoviePilot subtitle requires server-side download"
            )

        url = urldefrag(str(subtitle.get("enclosure") or "")).url
        headers = {
            "Accept": "*/*",
            "User-Agent": str(subtitle.get("site_ua") or "")
            or "Bangumi-Auto-Rename/1.0",
        }
        cookie = str(subtitle.get("site_cookie") or "")
        if cookie:
            headers["Cookie"] = cookie

        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise MoviePilotAPIError(
                "MoviePilot authenticated subtitle download failed: "
                f"{type(exc).__name__}"
            ) from exc

        content = response.content
        if not content:
            raise MoviePilotAPIError(
                "MoviePilot authenticated subtitle download returned no data"
            )
        filename = self._direct_download_filename(
            subtitle,
            response.headers,
            url,
            content,
        )
        destination_dir.mkdir(parents=True, exist_ok=True)
        target = destination_dir / filename
        partial = target.with_name(f".{target.name}.part")
        try:
            partial.write_bytes(content)
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)
        return target

    def download_subtitle(
        self,
        subtitle: Mapping[str, Any],
        *,
        tmdb_id: int | None,
        save_path: str,
    ) -> list[str]:
        body = {
            "subtitle_in": self.sanitize_subtitle(subtitle),
            "tmdbid": tmdb_id,
            "save_path": save_path,
        }
        payload = self._request_json(
            "POST",
            "/api/v1/download/subtitle",
            json=body,
        )
        if (
            not isinstance(payload, Mapping)
            or payload.get("success") is not True
        ):
            message = (
                payload.get("message") if isinstance(payload, Mapping) else ""
            )
            raise MoviePilotAPIError(
                f"MoviePilot subtitle download failed: "
                f"{message or 'invalid response'}"
            )
        data = payload.get("data")
        files = data.get("files") if isinstance(data, Mapping) else None
        result = [str(item) for item in files or [] if str(item).strip()]
        if not result:
            raise MoviePilotAPIError(
                "MoviePilot subtitle download returned no saved files"
            )
        return result

    @classmethod
    def sanitize_subtitle(cls, row: Mapping[str, Any]) -> dict[str, Any]:
        """Allowlist subtitle fields; MP search rows include site secrets."""
        return {key: row[key] for key in cls._SUBTITLE_FIELDS if key in row}

    @staticmethod
    def _direct_download_filename(
        subtitle: Mapping[str, Any],
        headers: Mapping[str, Any],
        url: str,
        content: bytes,
    ) -> str:
        content_disposition = str(
            headers.get("Content-Disposition")
            or headers.get("content-disposition")
            or ""
        )
        match = re.search(
            r"filename\*=utf-8''([^;]+)",
            content_disposition,
            re.IGNORECASE,
        ) or re.search(
            r'filename="?([^";]+)"?',
            content_disposition,
            re.IGNORECASE,
        )
        name = (
            unquote(match.group(1)).strip()
            if match
            else str(subtitle.get("file_name") or "").strip()
        )
        if not name:
            name = unquote(urlparse(url).path.rsplit("/", 1)[-1]).strip()
        name = ntpath.basename(name.replace("/", "\\"))
        name = re.sub(r'[\\/:*?"<>|]+', "_", name).strip("._ ")
        name = name or "moviepilot-subtitle"
        if not Path(name).suffix:
            if content.startswith(b"PK"):
                name += ".zip"
            elif content.startswith(b"Rar!\x1a\x07"):
                name += ".rar"
            elif content.startswith(b"7z\xbc\xaf\x27\x1c"):
                name += ".7z"
            elif b"[Script Info]" in content[:4096]:
                name += ".ass"
            else:
                name += ".srt"
        return name

    def _subtitle_rows(
        self,
        payload: object,
        *,
        include_download_auth: bool = False,
    ) -> list[dict[str, Any]]:
        if not isinstance(payload, Mapping):
            raise MoviePilotAPIError(
                "MoviePilot subtitle search returned invalid JSON"
            )
        if payload.get("success") is False:
            message = str(payload.get("message") or "")
            if "未搜索到" in message or "no subtitle" in message.lower():
                return []
            raise MoviePilotAPIError(
                "MoviePilot subtitle search failed: "
                f"{message or 'unknown error'}"
            )
        rows = payload.get("data")
        if rows is None:
            return []
        if not isinstance(rows, list):
            raise MoviePilotAPIError(
                "MoviePilot subtitle search returned invalid result rows"
            )
        return [
            {
                **self.sanitize_subtitle(row),
                **{
                    key: row[key]
                    for key in ("site_cookie", "site_ua", "site_proxy")
                    if key in row
                },
            }
            if include_download_auth
            else self.sanitize_subtitle(row)
            for row in rows
            if isinstance(row, Mapping)
        ]

    def _request_json(self, method: str, path: str, **kwargs: Any) -> object:
        if not self.base_url:
            raise MoviePilotAPIError("MoviePilot base URL is not configured")
        if not self.api_token:
            raise MoviePilotAPIError("MoviePilot API token is not configured")
        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                timeout=self.timeout,
                **kwargs,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise MoviePilotAPIError(
                f"MoviePilot request failed ({method} {path}): {exc}"
            ) from exc
        try:
            return response.json()
        except ValueError as exc:
            raise MoviePilotAPIError(
                f"MoviePilot returned non-JSON data ({method} {path})"
            ) from exc
