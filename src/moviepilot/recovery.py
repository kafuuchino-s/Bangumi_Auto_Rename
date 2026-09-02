from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Mapping

from ..logger import logger
from ..rename.utils import VIDEO_SUFFIX
from .client import MoviePilotAPIError, MoviePilotClient

_HISTORY_SCAN_LIMIT = 500


def scan_recovery_candidates(
    client: MoviePilotClient,
    *,
    path_converter: Callable[[str], str],
    task_path: Path,
    record_path: Path,
    is_queued: Callable[[str], bool],
    limit: int = 100,
) -> dict[str, Any]:
    histories = client.list_download_history(count=_HISTORY_SCAN_LIMIT)
    rows = sorted(
        _deduplicate_histories(histories),
        key=lambda row: (str(row.get("date") or ""), _int(row.get("id"))),
        reverse=True,
    )[: max(1, min(200, int(limit)))]
    processed_paths, processed_hashes = _processed_index(task_path, record_path)
    items: list[dict[str, Any]] = []
    warnings: Counter[str] = Counter()

    for row in rows:
        item = _base_item(row, path_converter)
        local_path = Path(item["local_path"])
        path_key = _path_key(item["local_path"])
        download_hash = _hash_key(item.get("download_hash"))
        video_paths = _video_paths(local_path)

        if not local_path.exists() or not video_paths:
            item["status"] = "unavailable"
        elif is_queued(str(local_path)):
            item["status"] = "queued"
        else:
            linked_uuid = _processed_uuid(
                path_key,
                [_path_key(path) for path in video_paths],
                download_hash,
                processed_paths,
                processed_hashes,
            )
            if linked_uuid:
                item["status"] = "processed"
                item["linked_task_uuid"] = linked_uuid
            else:
                item["status"] = "recoverable"
                item["completion_state"] = "history_only"
                if download_hash:
                    try:
                        live = client.get_download_task(download_hash)
                    except MoviePilotAPIError:
                        warnings["download_status_lookup_failed"] += 1
                        item["status"] = "status_unavailable"
                        item["completion_state"] = "unknown"
                    else:
                        if live is not None:
                            item["completion_state"] = "completed"
                            item["progress"] = live.get("progress")
                            item["downloader"] = live.get("downloader")
                            if not _download_complete(live):
                                item["status"] = "downloading"
                                item["completion_state"] = "downloading"
        items.append(item)

    status_counts = Counter(str(item["status"]) for item in items)
    return {
        "items": items,
        "summary": {
            "history_count": len(histories),
            "deduplicated_count": len(_deduplicate_histories(histories)),
            "shown_count": len(items),
            "recoverable_count": status_counts["recoverable"],
            "processed_count": status_counts["processed"],
            "queued_count": status_counts["queued"],
            "downloading_count": status_counts["downloading"],
            "status_unavailable_count": status_counts["status_unavailable"],
            "unavailable_count": status_counts["unavailable"],
        },
        "warnings": dict(warnings),
    }


def source_evidence(item: Mapping[str, Any]) -> dict[str, object]:
    return {
        "provider": "moviepilot",
        "history_id": _int(item.get("history_id")),
        "download_hash": str(item.get("download_hash") or ""),
        "torrent_name": str(item.get("torrent_name") or ""),
        "torrent_site": str(item.get("torrent_site") or ""),
        "source_path": str(item.get("source_path") or ""),
        "local_path": str(item.get("local_path") or ""),
        "title": str(item.get("title") or ""),
        "year": str(item.get("year") or ""),
        "media_type": str(item.get("media_type") or ""),
        "tmdb_id": _int(item.get("tmdb_id")) or None,
        "seasons": str(item.get("seasons") or ""),
        "episodes": str(item.get("episodes") or ""),
        "downloaded_at": str(item.get("downloaded_at") or ""),
        "completion_evidence": str(item.get("completion_state") or "history_only"),
    }


def _deduplicate_histories(
    histories: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in histories:
        key = _hash_key(row.get("download_hash"))
        if not key:
            key = f"path:{_path_key(row.get('path'))}"
        if not key or key == "path:":
            key = f"id:{_int(row.get('id'))}"
        current = latest.get(key)
        if current is None or (
            str(row.get("date") or ""), _int(row.get("id"))
        ) > (
            str(current.get("date") or ""),
            _int(current.get("id")),
        ):
            latest[key] = row
    return list(latest.values())


def _base_item(
    row: Mapping[str, Any],
    path_converter: Callable[[str], str],
) -> dict[str, Any]:
    source_path = str(row.get("path") or "").strip()
    return {
        "history_id": _int(row.get("id")),
        "source_path": source_path,
        "local_path": path_converter(source_path) if source_path else "",
        "title": str(row.get("title") or ""),
        "year": str(row.get("year") or ""),
        "media_type": str(row.get("type") or ""),
        "tmdb_id": _int(row.get("tmdbid")) or None,
        "seasons": str(row.get("seasons") or ""),
        "episodes": str(row.get("episodes") or ""),
        "download_hash": str(row.get("download_hash") or ""),
        "torrent_name": str(row.get("torrent_name") or ""),
        "torrent_site": str(row.get("torrent_site") or ""),
        "downloaded_at": str(row.get("date") or ""),
        "status": "unavailable",
        "completion_state": "unknown",
    }


def _processed_index(
    task_path: Path,
    record_path: Path,
) -> tuple[dict[str, str], dict[str, str]]:
    record_uuids = {
        path.stem for path in record_path.glob("*.json") if path.is_file()
    }
    processed_paths: dict[str, str] = {}
    processed_hashes: dict[str, str] = {}

    for path in task_path.glob("*.json"):
        data = _read_mapping(path)
        if not data or data.get("type") == "subtitle":
            continue
        task_uuid = path.stem.removesuffix(".subtitle_fetch")
        successful = (
            task_uuid in record_uuids
            or _int(data.get("transferred_file_count")) > 0
        ) and not str(data.get("error") or "").strip()
        if not successful:
            continue
        task_source = _path_key(data.get("path"))
        if task_source:
            processed_paths[task_source] = task_uuid
        evidence = data.get("source_evidence")
        if isinstance(evidence, Mapping):
            evidence_hash = _hash_key(evidence.get("download_hash"))
            evidence_path = _path_key(evidence.get("local_path"))
            if evidence_hash and evidence_path == task_source:
                processed_hashes[evidence_hash] = task_uuid

    for path in record_path.glob("*.json"):
        data = _read_mapping(path)
        if not data:
            continue
        for source in data:
            source_key = _path_key(source)
            if source_key and ("/" in source_key or "\\" in source_key):
                processed_paths[source_key] = path.stem
    return processed_paths, processed_hashes


def _processed_uuid(
    local_path: str,
    video_paths: list[str],
    download_hash: str,
    processed_paths: Mapping[str, str],
    processed_hashes: Mapping[str, str],
) -> str | None:
    if download_hash and download_hash in processed_hashes:
        return processed_hashes[download_hash]
    if local_path in processed_paths:
        return processed_paths[local_path]
    matched = [processed_paths.get(path) for path in video_paths]
    if matched and all(matched):
        return next(task_uuid for task_uuid in matched if task_uuid)
    return None


def _video_paths(path: Path) -> list[Path]:
    if not path.exists():
        return []
    if path.is_file():
        return [path] if path.suffix.lower() in VIDEO_SUFFIX else []
    try:
        return [
            child
            for child in path.rglob("*")
            if child.is_file() and child.suffix.lower() in VIDEO_SUFFIX
        ]
    except OSError as exc:
        logger.warning(
            "[MoviePilot恢复] 无法扫描路径",
            path=str(path),
            error=str(exc),
        )
        return []


def _download_complete(row: Mapping[str, Any]) -> bool:
    progress = str(row.get("progress") or "").strip().rstrip("%")
    try:
        return float(progress) >= 100.0
    except ValueError:
        return False


def _read_mapping(path: Path) -> Mapping[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, Mapping) else {}


def _path_key(value: object) -> str:
    return str(value or "").strip().replace("\\", "/").rstrip("/").casefold()


def _hash_key(value: object) -> str:
    return str(value or "").strip().lower()


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
