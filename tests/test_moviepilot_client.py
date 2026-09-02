from __future__ import annotations

from typing import Any

from src.moviepilot import MoviePilotClient


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
