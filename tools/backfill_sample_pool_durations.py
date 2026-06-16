from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.rename.local_evidence import LocalEvidence, LocalFileEvidence
from src.rename.local_supplemental_filter import classify_local_video_supplemental
from src.rename.utils import VIDEO_SUFFIX


VIDEO_SUFFIXES = {suffix.casefold() for suffix in VIDEO_SUFFIX}


def _norm_rel(value: object) -> str:
    return str(value or "").replace("\\", "/").strip()


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"sample must be a JSON object: {path}")
    return payload


def _sample_to_evidence(sample_path: Path, payload: dict[str, Any]) -> LocalEvidence:
    root_name = str(payload.get("root_name") or sample_path.stem)
    files: list[LocalFileEvidence] = []
    directories: set[str] = set()
    for index, item in enumerate(payload.get("files") or [], start=1):
        if not isinstance(item, dict):
            continue
        relative_path = _norm_rel(item.get("path") or item.get("relative_path"))
        if not relative_path:
            continue
        suffix = Path(relative_path).suffix.casefold()
        is_video = suffix in VIDEO_SUFFIXES
        supplemental = classify_local_video_supplemental(relative_path, is_video=is_video)
        directories.update(part for part in Path(relative_path).parts[:-1] if part)
        size = item.get("size", item.get("size_bytes"))
        files.append(
            LocalFileEvidence(
                file_id=f"file_{index:03d}",
                relative_path=relative_path,
                name=Path(relative_path).name,
                suffix=suffix,
                is_video=is_video,
                is_supplemental_candidate=bool(supplemental.is_supplemental),
                is_main_video_candidate=is_video and not supplemental.is_supplemental,
                size_bytes=int(size) if isinstance(size, int) else None,
            )
        )
    return LocalEvidence(
        root_name=root_name,
        root_path=str(sample_path),
        files=files,
        video_count=sum(1 for item in files if item.is_video),
        main_video_count=sum(1 for item in files if item.is_main_video_candidate),
        supplemental_candidate_count=sum(1 for item in files if item.is_supplemental_candidate),
        directory_structure=sorted(directories),
    )


def _walk_media_root(media_root: Path) -> tuple[dict[str, list[Path]], dict[tuple[int, str], list[Path]], int]:
    dirs_by_name: dict[str, list[Path]] = defaultdict(list)
    files_by_size_name: dict[tuple[int, str], list[Path]] = defaultdict(list)
    file_count = 0
    for current, dirnames, filenames in os.walk(media_root):
        current_path = Path(current)
        for dirname in dirnames:
            dirs_by_name[dirname.casefold()].append(current_path / dirname)
        for filename in filenames:
            file_path = current_path / filename
            try:
                size = int(file_path.stat().st_size)
            except OSError:
                continue
            files_by_size_name[(size, filename.casefold())].append(file_path)
            file_count += 1
    return dirs_by_name, files_by_size_name, file_count


def _is_file_like_root(root_name: str) -> bool:
    return Path(root_name).suffix.casefold() in VIDEO_SUFFIXES


def _candidate_score(path: Path, *, sample_root_name: str, relative_path: str) -> tuple[int, int]:
    text = path.as_posix().casefold()
    root = sample_root_name.casefold()
    rel = relative_path.replace("\\", "/").casefold()
    score = 0
    if root and root in text:
        score += 20
    if rel and text.endswith(rel):
        score += 10
    if path.name.casefold() == Path(relative_path).name.casefold():
        score += 5
    return score, -len(text)


def _resolve_actual_paths(
    evidence: LocalEvidence,
    *,
    dirs_by_name: dict[str, list[Path]],
    files_by_size_name: dict[tuple[int, str], list[Path]],
) -> tuple[dict[str, Path], dict[str, int]]:
    actual_paths: dict[str, Path] = {}
    stats = Counter()
    root_name = evidence.root_name
    root_dirs = list(dirs_by_name.get(root_name.casefold(), []))

    for file in evidence.files:
        rel = _norm_rel(file.relative_path)
        basename = Path(rel).name
        size = int(file.size_bytes or 0)
        matched: Path | None = None

        for root_dir in root_dirs:
            candidate = root_dir / Path(rel)
            if candidate.exists():
                try:
                    if not size or candidate.stat().st_size == size:
                        matched = candidate
                        stats["matched_by_root_path"] += 1
                        break
                except OSError:
                    pass

        if matched is None and size:
            candidates = list(files_by_size_name.get((size, basename.casefold()), []))
            if candidates:
                candidates.sort(key=lambda item: _candidate_score(item, sample_root_name=root_name, relative_path=rel), reverse=True)
                matched = candidates[0]
                stats["matched_by_size_name"] += 1

        if matched is not None:
            actual_paths[file.file_id] = matched
            actual_paths[rel] = matched
        else:
            stats["unmatched"] += 1
    return actual_paths, dict(stats)


def _load_probe_cache(cache_path: Path) -> dict[str, dict[str, Any]]:
    if not cache_path.exists():
        return {}
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_probe_cache(cache_path: Path, cache: dict[str, dict[str, Any]]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(cache_path)


def _probe_one(path: Path) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                "-show_chapters",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
        )
    except Exception as exc:
        return {"ok": False, "probe_status": "probe_error", "probe_error_class": exc.__class__.__name__}
    if completed.returncode != 0:
        stderr = " ".join((completed.stderr or "").split())
        return {"ok": False, "probe_status": "probe_error", "probe_error_class": f"ffprobe_exit_{completed.returncode}:{stderr[:120]}"}
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "probe_status": "probe_error", "probe_error_class": "ffprobe_invalid_json"}
    streams = [item for item in payload.get("streams") or [] if isinstance(item, dict)]
    chapters = [item for item in payload.get("chapters") or [] if isinstance(item, dict)]
    format_info = payload.get("format") if isinstance(payload.get("format"), dict) else {}
    video_streams = [item for item in streams if item.get("codec_type") == "video"]
    audio_streams = [item for item in streams if item.get("codec_type") == "audio"]
    subtitle_streams = [item for item in streams if item.get("codec_type") == "subtitle"]
    first_video = video_streams[0] if video_streams else {}
    duration = _float_or_none(format_info.get("duration"))
    if duration is None:
        duration = _float_or_none(first_video.get("duration"))
    width = _int_or_none(first_video.get("width"))
    height = _int_or_none(first_video.get("height"))
    return {
        "ok": True,
        "probe_status": "available",
        "duration_seconds": round(duration, 3) if duration is not None else None,
        "container_format": str(format_info.get("format_name") or path.suffix.lstrip(".")),
        "video_stream_count": len(video_streams),
        "audio_stream_count": len(audio_streams),
        "subtitle_stream_count": len(subtitle_streams),
        "chapter_count": len(chapters),
        "chapter_durations_seconds": _chapter_durations(chapters),
        "resolution": f"{width}x{height}" if width and height else "",
        "probe_error_class": "",
    }


def _chapter_durations(chapters: list[dict[str, Any]]) -> list[float]:
    durations: list[float] = []
    for chapter in chapters:
        start = _float_or_none(chapter.get("start_time"))
        end = _float_or_none(chapter.get("end_time"))
        if start is None or end is None or end < start:
            continue
        durations.append(round(end - start, 3))
    return durations


def _float_or_none(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _probe_paths(paths: list[Path], *, workers: int, cache_path: Path) -> dict[str, dict[str, Any]]:
    cache = _load_probe_cache(cache_path)
    pending = [path for path in paths if str(path) not in cache]
    if pending:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            future_map = {executor.submit(_probe_one, path): path for path in pending}
            completed_count = 0
            for future in as_completed(future_map):
                path = future_map[future]
                try:
                    cache[str(path)] = future.result()
                except Exception as exc:
                    cache[str(path)] = {"ok": False, "probe_status": "probe_error", "probe_error_class": exc.__class__.__name__}
                completed_count += 1
                if completed_count % 200 == 0:
                    _write_probe_cache(cache_path, cache)
                    print(f"probed {completed_count}/{len(pending)} new media files", flush=True)
        _write_probe_cache(cache_path, cache)
    return {str(path): cache.get(str(path), {}) for path in paths}


def _container_facts_from_probe(
    relative_path: str,
    actual_path: Path | None,
    probe_results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if actual_path is None:
        return {
            "probe_status": "missing_file",
            "duration_seconds": None,
            "container_format": Path(relative_path).suffix.lstrip("."),
            "video_stream_count": None,
            "audio_stream_count": None,
            "subtitle_stream_count": None,
            "chapter_count": None,
            "chapter_durations_seconds": [],
            "resolution": "",
            "probe_error_class": "sample_file_not_found_under_media_root",
        }

    result = probe_results.get(str(actual_path), {})
    if result.get("ok"):
        return {
            "probe_status": "available",
            "duration_seconds": result.get("duration_seconds"),
            "container_format": result.get("container_format") or Path(relative_path).suffix.lstrip("."),
            "video_stream_count": result.get("video_stream_count"),
            "audio_stream_count": result.get("audio_stream_count"),
            "subtitle_stream_count": result.get("subtitle_stream_count"),
            "chapter_count": result.get("chapter_count"),
            "chapter_durations_seconds": result.get("chapter_durations_seconds") or [],
            "resolution": result.get("resolution") or "",
            "probe_error_class": "",
        }

    return {
        "probe_status": "probe_error",
        "duration_seconds": None,
        "container_format": Path(relative_path).suffix.lstrip("."),
        "video_stream_count": None,
        "audio_stream_count": None,
        "subtitle_stream_count": None,
        "chapter_count": None,
        "chapter_durations_seconds": [],
        "resolution": "",
        "probe_error_class": str(result.get("probe_error_class") or "probe_returned_no_metadata"),
    }


def _container_fact_summary(files: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = Counter()
    durations: list[float] = []
    for item in files:
        container = item.get("container_facts") if isinstance(item.get("container_facts"), dict) else {}
        if not container:
            continue
        status_counts[str(container.get("probe_status") or "unknown")] += 1
        duration = _float_or_none(container.get("duration_seconds"))
        if duration is not None:
            durations.append(round(duration, 3))
    return {
        "container_fact_count": sum(status_counts.values()),
        "probe_status_counts": dict(sorted(status_counts.items())),
        "duration_seconds_range": [min(durations), max(durations)] if durations else [],
        "duration_seconds_samples": durations[:6],
    }


def _drop_generated_fact_surface(payload: dict[str, Any]) -> None:
    source = payload.get("local_fact_surface_source")
    if isinstance(source, dict) and source.get("kind") == "real_local_media_duration_backfill":
        payload.pop("local_fact_surface", None)
        payload.pop("local_fact_surface_summary", None)
        payload.pop("local_fact_surface_source", None)


def _backfill_payload_container_facts(
    payload: dict[str, Any],
    evidence: LocalEvidence,
    *,
    actual_paths: dict[str, Path],
    probe_results: dict[str, dict[str, Any]],
    media_root: Path,
    match_stats: dict[str, int],
) -> None:
    _drop_generated_fact_surface(payload)
    raw_files = payload.get("files")
    if not isinstance(raw_files, list):
        return

    evidence_by_rel = {_norm_rel(file.relative_path): file for file in evidence.files}
    evidence_by_id = {file.file_id: file for file in evidence.files}
    matched_video_count = 0
    missing_actual_video_count = 0
    for index, item in enumerate(raw_files, start=1):
        if not isinstance(item, dict):
            continue
        relative_path = _norm_rel(item.get("path") or item.get("relative_path"))
        file = evidence_by_rel.get(relative_path) or evidence_by_id.get(f"file_{index:03d}")
        if file is None or not file.is_video:
            continue
        actual_path = actual_paths.get(file.file_id) or actual_paths.get(relative_path)
        if actual_path is None:
            missing_actual_video_count += 1
        else:
            matched_video_count += 1
        item["container_facts"] = _container_facts_from_probe(relative_path, actual_path, probe_results)

    payload["local_container_fact_summary"] = _container_fact_summary(
        [item for item in raw_files if isinstance(item, dict)]
    )
    payload["local_container_fact_source"] = {
        "kind": "real_local_media_duration_backfill",
        "media_root": str(media_root),
        "probe_media": True,
        "container_probe": "ffprobe",
        "embedded_in": "files[].container_facts",
        "matched_file_count": len(actual_paths) // 2,
        "matched_video_count": matched_video_count,
        "missing_actual_video_count": missing_actual_video_count,
        "match_stats": dict(sorted(match_stats.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill sample-pool local video durations from a real media root.")
    parser.add_argument("--raw-root", type=Path, default=Path("tests/sample_pool/raw"))
    parser.add_argument("--media-root", type=Path, default=Path(r"H:\Anime"))
    parser.add_argument("--cache-path", type=Path, default=Path("data/pi_case_agent/sample_duration_probe_cache.json"))
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sample_paths = sorted(args.raw_root.rglob("*.json"), key=lambda item: item.as_posix().casefold())
    if args.limit:
        sample_paths = sample_paths[: max(0, args.limit)]
    print(f"indexing media root: {args.media_root}", flush=True)
    dirs_by_name, files_by_size_name, indexed_file_count = _walk_media_root(args.media_root)
    print(f"indexed {len(dirs_by_name)} directory names and {indexed_file_count} files", flush=True)

    loaded: list[tuple[Path, dict[str, Any], LocalEvidence, dict[str, Path], dict[str, int]]] = []
    video_paths: dict[str, Path] = {}
    aggregate = Counter()
    for sample_path in sample_paths:
        payload = _load_json(sample_path)
        evidence = _sample_to_evidence(sample_path, payload)
        actual_paths, match_stats = _resolve_actual_paths(evidence, dirs_by_name=dirs_by_name, files_by_size_name=files_by_size_name)
        loaded.append((sample_path, payload, evidence, actual_paths, match_stats))
        aggregate.update(match_stats)
        for file in evidence.files:
            if not file.is_video:
                continue
            actual_path = actual_paths.get(file.file_id) or actual_paths.get(file.relative_path)
            if actual_path is not None:
                video_paths[str(actual_path)] = actual_path

    print(
        json.dumps(
            {
                "sample_count": len(loaded),
                "unique_matched_video_path_count": len(video_paths),
                "match_stats": dict(sorted(aggregate.items())),
                "dry_run": bool(args.dry_run),
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    if args.dry_run:
        return 0

    probe_results = _probe_paths(list(video_paths.values()), workers=args.workers, cache_path=args.cache_path)

    changed = 0
    for sample_path, payload, evidence, actual_paths, match_stats in loaded:
        before = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        _backfill_payload_container_facts(
            payload,
            evidence,
            actual_paths=actual_paths,
            probe_results=probe_results,
            media_root=args.media_root,
            match_stats=match_stats,
        )
        if before != json.dumps(payload, ensure_ascii=False, sort_keys=True):
            sample_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            changed += 1
    print(json.dumps({"changed_sample_count": changed, "sample_count": len(loaded)}, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
