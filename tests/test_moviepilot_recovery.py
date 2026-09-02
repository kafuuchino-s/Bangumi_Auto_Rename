from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.moviepilot import MoviePilotAPIError
from src.moviepilot.recovery import scan_recovery_candidates, source_evidence


class _Client:
    def __init__(
        self,
        histories: list[dict[str, Any]],
        live: dict[str, dict[str, Any] | None],
    ) -> None:
        self.histories = histories
        self.live = live
        self.lookups: list[str] = []

    def list_download_history(self, *, page: int = 1, count: int = 100):
        return list(self.histories)

    def get_download_task(self, download_hash: str):
        self.lookups.append(download_hash)
        return self.live.get(download_hash)


def _history(
    history_id: int,
    path: Path,
    download_hash: str,
    *,
    date: str = "2026-09-02 12:00:00",
) -> dict[str, Any]:
    return {
        "id": history_id,
        "path": str(path),
        "type": "电视剧",
        "title": f"Title {history_id}",
        "year": "2026",
        "tmdbid": history_id,
        "seasons": "S01",
        "episodes": "E01-E12",
        "download_hash": download_hash,
        "torrent_name": f"Release {history_id}",
        "torrent_site": "Site",
        "date": date,
    }


def test_scan_deduplicates_and_classifies_without_semantic_hints(
    tmp_path: Path,
) -> None:
    task_path = tmp_path / "tasks"
    record_path = tmp_path / "records"
    task_path.mkdir()
    record_path.mkdir()
    processed = tmp_path / "processed"
    downloading = tmp_path / "downloading"
    recoverable = tmp_path / "recoverable"
    queued = tmp_path / "queued"
    for path in (processed, downloading, recoverable, queued):
        path.mkdir()
        (path / "01.mkv").write_bytes(b"video")

    (record_path / "done-task.json").write_text(
        '{"%s": "/library/Title/S01E01.mkv"}'
        % str(processed / "01.mkv").replace("\\", "\\\\"),
        encoding="utf-8",
    )
    histories = [
        _history(1, processed, "done-hash", date="2026-09-01 10:00:00"),
        _history(2, processed, "done-hash", date="2026-09-02 10:00:00"),
        _history(3, downloading, "active-hash"),
        _history(4, recoverable, "removed-hash"),
        _history(5, queued, "queued-hash"),
        _history(6, tmp_path / "missing", "missing-hash"),
    ]
    client = _Client(
        histories,
        {
            "active-hash": {
                "hash": "active-hash",
                "progress": "36.9%",
                "state": "downloading",
            },
            "removed-hash": None,
        },
    )

    report = scan_recovery_candidates(
        client,  # type: ignore[arg-type]
        path_converter=lambda value: value,
        task_path=task_path,
        record_path=record_path,
        is_queued=lambda value: value == str(queued),
    )

    items = {item["history_id"]: item for item in report["items"]}
    assert set(items) == {2, 3, 4, 5, 6}
    assert items[2]["status"] == "processed"
    assert items[2]["linked_task_uuid"] == "done-task"
    assert items[3]["status"] == "downloading"
    assert items[4]["status"] == "recoverable"
    assert items[4]["completion_state"] == "history_only"
    assert items[5]["status"] == "queued"
    assert items[6]["status"] == "unavailable"
    assert set(client.lookups) == {"active-hash", "removed-hash"}
    assert report["summary"] == {
        "history_count": 6,
        "deduplicated_count": 5,
        "shown_count": 5,
        "recoverable_count": 1,
        "processed_count": 1,
        "queued_count": 1,
        "downloading_count": 1,
        "status_unavailable_count": 0,
        "unavailable_count": 1,
    }


def test_download_status_failure_is_not_recoverable(tmp_path: Path) -> None:
    task_path = tmp_path / "tasks"
    record_path = tmp_path / "records"
    source = tmp_path / "source"
    task_path.mkdir()
    record_path.mkdir()
    source.mkdir()
    (source / "01.mkv").write_bytes(b"video")

    class _FailingClient(_Client):
        def get_download_task(self, download_hash: str):
            raise MoviePilotAPIError("lookup failed")

    report = scan_recovery_candidates(
        _FailingClient([_history(1, source, "hash")], {}),  # type: ignore[arg-type]
        path_converter=lambda value: value,
        task_path=task_path,
        record_path=record_path,
        is_queued=lambda _value: False,
    )

    assert report["items"][0]["status"] == "status_unavailable"
    assert report["summary"]["recoverable_count"] == 0
    assert report["summary"]["status_unavailable_count"] == 1
    assert report["warnings"] == {"download_status_lookup_failed": 1}


def test_partial_record_coverage_remains_recoverable(tmp_path: Path) -> None:
    task_path = tmp_path / "tasks"
    record_path = tmp_path / "records"
    source = tmp_path / "multi-work"
    task_path.mkdir()
    record_path.mkdir()
    source.mkdir()
    first = source / "01.mkv"
    second = source / "02.mkv"
    first.write_bytes(b"video")
    second.write_bytes(b"video")
    (record_path / "partial.json").write_text(
        '{"%s": "/library/Title/S01E01.mkv"}'
        % str(first).replace("\\", "\\\\"),
        encoding="utf-8",
    )
    (task_path / "partial.json").write_text(
        json.dumps(
            {
                "path": str(first),
                "transferred_file_count": 1,
                "source_evidence": {
                    "provider": "moviepilot",
                    "download_hash": "partial-hash",
                    "local_path": str(source),
                },
            }
        ),
        encoding="utf-8",
    )
    client = _Client([_history(1, source, "partial-hash")], {"partial-hash": None})

    report = scan_recovery_candidates(
        client,  # type: ignore[arg-type]
        path_converter=lambda value: value,
        task_path=task_path,
        record_path=record_path,
        is_queued=lambda _value: False,
    )

    assert report["items"][0]["status"] == "recoverable"


def test_source_evidence_is_bounded_and_keeps_media_type_advisory() -> None:
    evidence = source_evidence(
        {
            "history_id": 7,
            "source_path": "H:/Anime/Title",
            "local_path": "/media/Anime/Title",
            "title": "Title",
            "media_type": "电视剧",
            "tmdb_id": 42,
            "download_hash": "abc",
            "completion_state": "completed",
            "cookie": "must-not-survive",
            "is_anime": True,
            "is_movie": False,
        }
    )

    assert evidence["provider"] == "moviepilot"
    assert evidence["media_type"] == "电视剧"
    assert evidence["completion_evidence"] == "completed"
    assert "cookie" not in evidence
    assert "is_anime" not in evidence
    assert "is_movie" not in evidence
