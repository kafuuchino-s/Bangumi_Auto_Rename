from __future__ import annotations

import json
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from src.moviepilot import MoviePilotAPIError, MoviePilotClient
from src.subtitle.providers import (
    CombinedSubtitleProvider,
    MoviePilotProvider,
    SubtitleCandidate,
    SubtitleDownloadResult,
    SubtitleProvider,
    SubtitleThreadPackage,
)


def _row(**updates: Any) -> dict[str, Any]:
    row = {
        "site": 7,
        "site_name": "幼儿园",
        "title": "[贫乏神来了][S01E01-E13][ASS][简繁]",
        "description": "全 13 集字幕",
        "enclosure": "https://example.test/subtitle.zip#mp_sig=signed",
        "page_url": "https://example.test/topic/1",
        "language": "简体中文",
        "size": 362000,
        "grabs": 230,
        "torrent_id": "t1",
        "subtitle_id": "s1",
        "file_name": "subtitle.zip",
        "season_episode": "S01",
        "episode_list": list(range(1, 14)),
        "site_cookie": "must-not-survive",
        "site_ua": "must-not-survive",
    }
    row.update(updates)
    return row


class _Client:
    def __init__(
        self,
        *,
        media_rows: list[dict[str, Any]] | None = None,
        title_rows: list[dict[str, Any]] | None = None,
        direct_enabled: bool = False,
        direct_fails: bool = False,
    ) -> None:
        self.media_rows = media_rows or []
        self.title_rows = title_rows or []
        self.media_calls: list[tuple[str, dict[str, Any]]] = []
        self.title_calls: list[str] = []
        self.direct_enabled = direct_enabled
        self.direct_fails = direct_fails
        self.direct_calls: list[Mapping[str, Any]] = []
        self.download_calls: list[
            tuple[Mapping[str, Any], int | None, str]
        ] = []

    def search_subtitles_by_media(
        self, media_id: str, **kwargs: Any
    ) -> list[dict[str, Any]]:
        self.media_calls.append((media_id, kwargs))
        return list(self.media_rows)

    def search_subtitles_by_title(
        self,
        keyword: str,
        **_kwargs: Any,
    ) -> list[dict[str, Any]]:
        self.title_calls.append(keyword)
        return list(self.title_rows)

    def can_download_subtitle_direct(
        self,
        subtitle: Mapping[str, Any],
    ) -> bool:
        return self.direct_enabled and not bool(subtitle.get("site_proxy"))

    def download_subtitle_direct(
        self,
        subtitle: Mapping[str, Any],
        *,
        destination_dir: Path,
    ) -> Path:
        self.direct_calls.append(subtitle)
        if self.direct_fails:
            raise MoviePilotAPIError("direct failed")
        destination_dir.mkdir(parents=True, exist_ok=True)
        target = destination_dir / "direct-subtitle.rar"
        target.write_bytes(b"Rar!\x1a\x07direct")
        return target

    def download_subtitle(
        self,
        subtitle: Mapping[str, Any],
        *,
        tmdb_id: int | None,
        save_path: str,
    ) -> list[str]:
        self.download_calls.append((subtitle, tmdb_id, save_path))
        staging = Path(save_path)
        staging.mkdir(parents=True, exist_ok=True)
        files = [staging / "chs" / "01.ass", staging / "cht" / "01.ass"]
        for index, path in enumerate(files, start=1):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"Dialogue: episode {index}", encoding="utf-8")
        return [str(path) for path in files]


def _configured_provider(
    client: _Client, tmp_path: Path
) -> MoviePilotProvider:
    provider = MoviePilotProvider(
        client=client,  # type: ignore[arg-type]
        save_path=str(tmp_path / "moviepilot-staging"),
    )
    provider.configure_context(
        {
            "name": "贫乏神来了！",
            "tmdb_name": "贫乏神来了！",
            "tmdb_id": 45968,
            "tmdb_media_type": "tv",
            "season_id": 1,
        },
        [
            tmp_path / f"贫乏神来了！ - S01E{i:02d}.mkv"
            for i in range(1, 14)
        ],
    )
    return provider


def test_provider_uses_exact_media_search_once_and_projects_package(
    tmp_path: Path,
) -> None:
    client = _Client(media_rows=[_row()])
    provider = _configured_provider(client, tmp_path)

    first = provider.search("贫乏神来了！")
    second = provider.search("貧乏神が！")

    assert len(client.media_calls) == 1
    assert client.media_calls[0] == (
        "tmdb:45968",
        {
            "media_type": "tv",
            "title": "贫乏神来了！",
            "year": None,
            "season": 1,
            "episode": None,
            "include_download_auth": True,
        },
    )
    assert client.title_calls == []
    assert first[0].detail_url == second[0].detail_url
    assert first[0].source == "moviepilot"
    assert "站点=幼儿园" in str(first[0].snippet)
    assert "moviepilot_payload" not in first[0].metadata
    assert "must-not-survive" not in str(first[0].metadata)
    assert first[0].attachment_urls[0].startswith("moviepilot://download/")
    assert "example.test" not in first[0].attachment_urls[0]

    loaded = provider.load_thread_packages(first[0])
    assert loaded.pages_scanned == 1
    assert len(loaded.thread_packages) == 1
    assert loaded.thread_packages[0].has_direct_download is True
    assert "语言字段=简体中文" in loaded.thread_packages[0].post_text


def test_provider_falls_back_to_title_when_exact_search_is_empty(
    tmp_path: Path,
) -> None:
    client = _Client(title_rows=[_row()])
    provider = _configured_provider(client, tmp_path)

    candidates = provider.search("贫乏神")

    assert len(candidates) == 1
    assert client.title_calls == ["贫乏神"]


def test_provider_falls_back_to_title_when_exact_links_are_unsupported(
    tmp_path: Path,
) -> None:
    client = _Client(
        media_rows=[_row(enclosure="javascript:void(0);")],
        title_rows=[_row(site=10, site_name="猫站")],
    )
    provider = _configured_provider(client, tmp_path)

    candidates = provider.search("贫乏神")

    assert len(candidates) == 1
    assert candidates[0].forum == "猫站"
    assert client.title_calls == ["贫乏神"]


def test_provider_does_not_expose_unsupported_download_links(
    tmp_path: Path,
) -> None:
    client = _Client(
        media_rows=[_row(enclosure="javascript:void(0);")],
        title_rows=[_row(enclosure="javascript:void(0);")],
    )
    provider = _configured_provider(client, tmp_path)

    assert provider.search("贫乏神") == []


def test_provider_direct_download_keeps_credentials_out_of_candidate(
    tmp_path: Path,
) -> None:
    client = _Client(media_rows=[_row()], direct_enabled=True)
    provider = _configured_provider(client, tmp_path)
    candidate = provider.load_thread_packages(provider.search("贫乏神")[0])
    package = candidate.thread_packages[0]

    visible = json.dumps(asdict(candidate), ensure_ascii=False)
    assert "must-not-survive" not in visible
    result = provider.download(
        candidate,
        tmp_path / "bar-download",
        package=package,
        download_url=package.links[0].url,
    )

    assert result.status == "success"
    assert result.downloaded_path == (
        tmp_path / "bar-download" / "direct-subtitle.rar"
    )
    assert client.download_calls == []
    assert client.direct_calls[0]["site_cookie"] == "must-not-survive"
    assert client.direct_calls[0]["site_ua"] == "must-not-survive"


def test_provider_direct_failure_falls_back_to_sanitized_mp_download(
    tmp_path: Path,
) -> None:
    client = _Client(
        media_rows=[_row()],
        direct_enabled=True,
        direct_fails=True,
    )
    provider = _configured_provider(client, tmp_path)
    candidate = provider.load_thread_packages(provider.search("贫乏神")[0])
    package = candidate.thread_packages[0]

    result = provider.download(
        candidate,
        tmp_path / "bar-download",
        package=package,
        download_url=package.links[0].url,
    )

    assert result.status == "success"
    assert len(client.direct_calls) == 1
    assert len(client.download_calls) == 1
    fallback_payload = client.download_calls[0][0]
    assert "site_cookie" not in fallback_payload
    assert "site_ua" not in fallback_payload


def test_provider_repackages_multiple_files_and_cleans_unique_staging(
    tmp_path: Path,
) -> None:
    client = _Client(media_rows=[_row()])
    provider = _configured_provider(client, tmp_path)
    candidate = provider.load_thread_packages(provider.search("贫乏神")[0])
    package = candidate.thread_packages[0]

    result = provider.download(
        candidate,
        tmp_path / "bar-download",
        package=package,
        download_url=package.links[0].url,
    )

    assert result.status == "success"
    assert result.downloaded_path is not None
    assert result.downloaded_path.name == "moviepilot-subtitles.zip"
    with zipfile.ZipFile(result.downloaded_path) as archive:
        assert archive.namelist() == ["chs/01.ass", "cht/01.ass"]
    staging_children = list(
        (tmp_path / "moviepilot-staging").glob("bar-auto-fetch-*")
    )
    assert staging_children == []
    assert client.download_calls[0][1] == 45968
    downloaded_payload = client.download_calls[0][0]
    assert str(downloaded_payload["enclosure"]).startswith("https://example.test/")
    assert "site_cookie" not in downloaded_payload
    assert "site_ua" not in downloaded_payload
    assert result.download_url is not None
    assert result.download_url.startswith("moviepilot://download/")


class _Provider(SubtitleProvider):
    def __init__(self, provider_id: str, candidate: SubtitleCandidate) -> None:
        self.provider_id = provider_id
        self.candidate = candidate
        self.downloaded = False

    def search(self, keyword: str, limit: int = 10) -> list[SubtitleCandidate]:
        return [self.candidate]

    def download(
        self,
        candidate: SubtitleCandidate,
        destination_dir: Path,
        package: SubtitleThreadPackage | None = None,
        download_url: str | None = None,
    ) -> SubtitleDownloadResult:
        self.downloaded = True
        return SubtitleDownloadResult(
            candidate=candidate,
            downloaded_path=None,
            download_url=download_url,
            status="success",
        )


def test_combined_provider_keeps_both_sources_and_dispatches_download(
    tmp_path: Path,
) -> None:
    acgrip = _Provider(
        "acgrip",
        SubtitleCandidate("A", "https://a.test", "acgrip"),
    )
    moviepilot = _Provider(
        "moviepilot",
        SubtitleCandidate("B", "moviepilot://b", "moviepilot"),
    )
    provider = CombinedSubtitleProvider([acgrip, moviepilot])

    candidates = provider.search("series")
    provider.download(candidates[1], tmp_path)

    assert [candidate.source for candidate in candidates] == [
        "acgrip",
        "moviepilot",
    ]
    assert moviepilot.downloaded is True
    assert acgrip.downloaded is False


def test_client_allowlist_is_also_applied_to_provider_fake_rows() -> None:
    sanitized = MoviePilotClient.sanitize_subtitle(_row())
    assert "site_cookie" not in sanitized
    assert "site_ua" not in sanitized
