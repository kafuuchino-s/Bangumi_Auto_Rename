from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.routes_moviepilot as routes_moviepilot
from src.api.routes_moviepilot import router


class _Queue:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def is_path_in_queue(self, _path: str) -> bool:
        return False

    def enqueue(self, **kwargs):
        self.calls.append(kwargs)
        return "recovery-task-id"


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


def _report(path: Path, status: str = "recoverable") -> dict[str, object]:
    return {
        "items": [
            {
                "history_id": 9,
                "source_path": "H:/Anime/Title",
                "local_path": str(path),
                "title": "Title",
                "year": "2026",
                "media_type": "电视剧",
                "tmdb_id": 42,
                "seasons": "S01",
                "episodes": "E01-E12",
                "download_hash": "abc123",
                "torrent_name": "Title S01",
                "torrent_site": "Site",
                "downloaded_at": "2026-09-02 12:00:00",
                "status": status,
                "completion_state": "completed",
            }
        ],
        "summary": {"recoverable_count": int(status == "recoverable")},
        "warnings": {},
    }


def test_recovery_enqueue_rechecks_server_item_and_keeps_media_hints_empty(
    tmp_path: Path,
    monkeypatch,
) -> None:
    queue = _Queue()
    monkeypatch.setattr(routes_moviepilot, "_scan", lambda _limit: _report(tmp_path))
    monkeypatch.setattr(routes_moviepilot, "get_queue_manager", lambda: queue)

    with _client() as client:
        response = client.post("/api/moviepilot/recovery/9/enqueue")

    assert response.status_code == 200
    assert response.json()["data"]["task_id"] == "recovery-task-id"
    assert len(queue.calls) == 1
    call = queue.calls[0]
    assert call["path"] == str(tmp_path)
    assert call["is_anime"] is None
    assert call["is_movie"] is None
    evidence = call["source_evidence"]
    assert isinstance(evidence, dict)
    assert evidence["download_hash"] == "abc123"
    assert evidence["media_type"] == "电视剧"


def test_recovery_enqueue_does_not_duplicate_queued_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    queue = _Queue()
    queue.is_path_in_queue = lambda _path: True  # type: ignore[method-assign]
    monkeypatch.setattr(routes_moviepilot, "_scan", lambda _limit: _report(tmp_path))
    monkeypatch.setattr(routes_moviepilot, "get_queue_manager", lambda: queue)

    with _client() as client:
        response = client.post("/api/moviepilot/recovery/9/enqueue")

    assert response.status_code == 409
    assert response.json()["detail"] == "该路径已在队列中"
    assert queue.calls == []


def test_recovery_enqueue_rejects_incomplete_download(
    tmp_path: Path,
    monkeypatch,
) -> None:
    queue = _Queue()
    monkeypatch.setattr(
        routes_moviepilot,
        "_scan",
        lambda _limit: _report(tmp_path, status="downloading"),
    )
    monkeypatch.setattr(routes_moviepilot, "get_queue_manager", lambda: queue)

    with _client() as client:
        response = client.post("/api/moviepilot/recovery/9/enqueue")

    assert response.status_code == 409
    assert queue.calls == []
