from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.moviepilot import MoviePilotAPIError, MoviePilotClient


class _Response:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self.payload


class _Session:
    def __init__(self, payloads: list[object]) -> None:
        self.headers: dict[str, str] = {}
        self.payloads = list(payloads)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> _Response:
        self.calls.append((method, url, kwargs))
        return _Response(self.payloads.pop(0))


def _subtitle_row() -> dict[str, Any]:
    return {
        "site": 7,
        "site_name": "Test Site",
        "title": "Series S01 ASS",
        "description": "S01E01-E13",
        "enclosure": "https://example.test/subtitle.zip#mp_sig=signed",
        "page_url": "https://example.test/topic/1",
        "language": "简体中文",
        "size": 1234,
        "torrent_id": "t1",
        "subtitle_id": "s1",
        "file_name": "subtitle.zip",
        "season_episode": "S01",
        "episode_list": list(range(1, 14)),
        "site_cookie": "secret-cookie",
        "site_ua": "secret-user-agent",
        "site_proxy": True,
    }


def test_media_search_uses_api_key_and_strips_site_credentials() -> None:
    session = _Session([{"success": True, "data": [_subtitle_row()]}])
    client = MoviePilotClient(
        base_url="http://moviepilot.test/",
        api_token="token-value",
        session=session,  # type: ignore[arg-type]
    )

    rows = client.search_subtitles_by_media(
        "tmdb:45968",
        media_type="tv",
        title="贫乏神来了！",
        season=1,
    )

    assert session.headers["X-API-KEY"] == "token-value"
    assert session.calls[0][1].endswith(
        "/api/v1/search/subtitle/media/tmdb:45968"
    )
    assert session.calls[0][2]["params"]["season"] == 1
    assert "site_cookie" not in rows[0]
    assert "site_ua" not in rows[0]
    assert "site_proxy" not in rows[0]


def test_media_search_can_retain_auth_for_private_provider_cache() -> None:
    row = _subtitle_row()
    row["site_internal_token"] = "must-stay-private"
    session = _Session([{"success": True, "data": [row]}])
    client = MoviePilotClient(
        base_url="http://moviepilot.test/",
        api_token="token-value",
        session=session,  # type: ignore[arg-type]
    )

    rows = client.search_subtitles_by_media(
        "tmdb:45968",
        media_type="tv",
        include_download_auth=True,
    )

    assert rows[0]["site_cookie"] == "secret-cookie"
    assert rows[0]["site_ua"] == "secret-user-agent"
    assert rows[0]["site_proxy"] is True
    assert "site_internal_token" not in rows[0]


def test_direct_download_uses_site_auth_without_leaking_mp_key(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    class _DownloadResponse:
        content = b"Rar!\x1a\x07payload"
        headers = {
            "Content-Disposition": 'attachment; filename="bundle.rar"'
        }

        def raise_for_status(self) -> None:
            return None

    def fake_get(url: str, **kwargs: Any) -> _DownloadResponse:
        captured["url"] = url
        captured.update(kwargs)
        return _DownloadResponse()

    monkeypatch.setattr("src.moviepilot.client.requests.get", fake_get)
    client = MoviePilotClient(
        base_url="http://moviepilot.test",
        api_token="token-value",
        session=_Session([]),  # type: ignore[arg-type]
    )
    row = _subtitle_row()
    row["site_proxy"] = False

    target = client.download_subtitle_direct(
        row,
        destination_dir=tmp_path,
    )

    assert target == tmp_path / "bundle.rar"
    assert target.read_bytes() == b"Rar!\x1a\x07payload"
    assert captured["url"] == "https://example.test/subtitle.zip"
    assert captured["headers"]["Cookie"] == "secret-cookie"
    assert captured["headers"]["User-Agent"] == "secret-user-agent"
    assert "X-API-KEY" not in captured["headers"]
    assert list(tmp_path.glob("*.part")) == []


def test_direct_download_error_does_not_include_site_secrets(
    monkeypatch,
    tmp_path: Path,
) -> None:
    def fail_get(*_args: Any, **_kwargs: Any) -> None:
        raise __import__("requests").ConnectionError(
            "secret-cookie https://example.test/private"
        )

    monkeypatch.setattr("src.moviepilot.client.requests.get", fail_get)
    client = MoviePilotClient(
        base_url="http://moviepilot.test",
        api_token="token-value",
        session=_Session([]),  # type: ignore[arg-type]
    )
    row = _subtitle_row()
    row["site_proxy"] = False

    try:
        client.download_subtitle_direct(row, destination_dir=tmp_path)
    except MoviePilotAPIError as exc:
        assert "secret-cookie" not in str(exc)
        assert "example.test" not in str(exc)
    else:
        raise AssertionError("direct download should fail")


def test_download_history_and_task_lookup_are_allowlisted() -> None:
    history = {
        "id": 9,
        "path": "H:/Anime/Series/Title",
        "type": "电视剧",
        "title": "Title",
        "tmdbid": 42,
        "download_hash": "ABC123",
        "torrent_name": "Title S01",
        "date": "2026-09-02 12:00:00",
        "userid": "private-user",
        "note": {"cookie": "private"},
    }
    task = {
        "hash": "abc123",
        "progress": "100.0%",
        "state": "completed",
        "content_path": "H:/Anime/Series/Title",
        "trackers": ["https://tracker.test/secret"],
    }
    session = _Session(
        [
            [history],
            {
                "success": True,
                "result": "找到下载任务：\n" + json.dumps([task]),
            },
        ]
    )
    client = MoviePilotClient(
        base_url="http://moviepilot.test",
        api_token="token-value",
        session=session,  # type: ignore[arg-type]
    )

    rows = client.list_download_history(count=25)
    live = client.get_download_task("ABC123")

    assert rows == [
        {
            "id": 9,
            "path": "H:/Anime/Series/Title",
            "type": "电视剧",
            "title": "Title",
            "tmdbid": 42,
            "download_hash": "ABC123",
            "torrent_name": "Title S01",
            "date": "2026-09-02 12:00:00",
        }
    ]
    assert live == {
        "hash": "abc123",
        "progress": "100.0%",
    }
    assert session.calls[0][2]["params"] == {"page": 1, "count": 25}
    assert session.calls[1][2]["json"]["arguments"]["hash"] == "abc123"


def test_downloaders_are_allowlisted() -> None:
    session = _Session(
        [[{"name": "qBittorrent", "type": "qbittorrent", "host": "private"}]]
    )
    client = MoviePilotClient(
        base_url="http://moviepilot.test",
        api_token="token-value",
        session=session,  # type: ignore[arg-type]
    )

    assert client.list_downloaders() == [
        {"name": "qBittorrent", "type": "qbittorrent"}
    ]


def test_download_task_lookup_handles_removed_task() -> None:
    session = _Session(
        [{"success": True, "result": "未找到下载任务"}]
    )
    client = MoviePilotClient(
        base_url="http://moviepilot.test",
        api_token="token-value",
        session=session,  # type: ignore[arg-type]
    )

    assert client.get_download_task("abc123") is None


def test_download_task_lookup_rejects_ambiguous_non_json_response() -> None:
    session = _Session([{"success": True, "result": "查询暂时失败"}])
    client = MoviePilotClient(
        base_url="http://moviepilot.test",
        api_token="token-value",
        session=session,  # type: ignore[arg-type]
    )

    try:
        client.get_download_task("abc123")
    except MoviePilotAPIError as exc:
        assert "invalid rows" in str(exc)
    else:
        raise AssertionError("ambiguous task response should fail closed")


def test_title_search_treats_no_subtitles_as_empty() -> None:
    session = _Session(
        [
            {
                "success": False,
                "message": "未搜索到任何字幕信息",
                "data": None,
            }
        ]
    )
    client = MoviePilotClient(
        base_url="http://moviepilot.test",
        api_token="token-value",
        session=session,  # type: ignore[arg-type]
    )

    assert client.search_subtitles_by_title("missing") == []
    assert session.calls[0][2]["params"] == {
        "keyword": "missing",
        "page": 0,
    }


def test_download_posts_sanitized_row_and_returns_saved_files() -> None:
    session = _Session(
        [
            {
                "success": True,
                "message": "字幕文件保存成功",
                "data": {"files": ["H:/Subtitle Staging/01.ass"]},
            }
        ]
    )
    client = MoviePilotClient(
        base_url="http://moviepilot.test",
        api_token="token-value",
        session=session,  # type: ignore[arg-type]
    )

    files = client.download_subtitle(
        _subtitle_row(),
        tmdb_id=45968,
        save_path="H:/Subtitle Staging/run",
    )

    assert files == ["H:/Subtitle Staging/01.ass"]
    body = session.calls[0][2]["json"]
    assert body["tmdbid"] == 45968
    assert body["save_path"] == "H:/Subtitle Staging/run"
    assert "site_cookie" not in body["subtitle_in"]
    assert "site_ua" not in body["subtitle_in"]
    assert "site_proxy" not in body["subtitle_in"]
