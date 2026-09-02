from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import quote

import requests


class MoviePilotAPIError(RuntimeError):
    """MoviePilot request or response contract failure."""


class MoviePilotClient:
    """Small authenticated client for MoviePilot's stable v1 API."""

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

    def list_downloaders(self) -> list[dict[str, Any]]:
        payload = self._request_json("GET", "/api/v1/download/clients")
        if not isinstance(payload, list):
            return []
        return [dict(item) for item in payload if isinstance(item, Mapping)]

    def search_subtitles_by_title(
        self,
        keyword: str,
        *,
        page: int = 0,
        sites: str = "",
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"keyword": keyword, "page": page}
        if sites:
            params["sites"] = sites
        payload = self._request_json(
            "GET",
            "/api/v1/search/subtitle/title",
            params=params,
        )
        return self._subtitle_rows(payload)

    def search_subtitles_by_media(
        self,
        media_id: str,
        *,
        media_type: str,
        title: str = "",
        year: int | None = None,
        season: int | None = None,
        episode: int | None = None,
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
        return self._subtitle_rows(payload)

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

    def _subtitle_rows(self, payload: object) -> list[dict[str, Any]]:
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
            self.sanitize_subtitle(row)
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
