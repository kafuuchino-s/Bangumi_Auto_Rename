from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import ManifestSnapshot, RenameSample


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST_PATH = (
    PROJECT_ROOT / 'tests' / 'sample_pool' / 'manifest' / 'manifest.json'
)


def _load_json(path: Path) -> dict[str, Any]:
    with open(path, 'r', encoding='utf-8') as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f'Invalid manifest root: {path}')
    return data


def load_manifest(path: Path | None = None) -> tuple[str, list[RenameSample]]:
    manifest_path = path or DEFAULT_MANIFEST_PATH
    payload = _load_json(manifest_path)
    version = str(payload.get('manifest_version') or payload.get('version') or '1')
    raw_entries = payload.get('entries') or []
    if not isinstance(raw_entries, list):
        raise ValueError(f'Invalid manifest entries: {manifest_path}')
    entries = [RenameSample(**entry) for entry in raw_entries if isinstance(entry, dict)]
    return version, entries


def filter_manifest_entries(
    entries: list[RenameSample],
    *,
    mode: str,
    sample_id: str | None = None,
    max_samples: int | None = None,
) -> tuple[list[RenameSample], list[str]]:
    notes: list[str] = []
    filtered = list(entries)

    if sample_id:
        filtered = [entry for entry in filtered if entry.sample_id == sample_id]
        notes.append(f'sample_id filter applied: {sample_id}')

    if mode == 'check':
        filtered = [entry for entry in filtered if entry.check]
        notes.append('check filter applied')
    elif mode == 'full':
        notes.append('full selection applied')
    elif mode == 'update-baseline':
        notes.append('update-baseline selection applied')

    if max_samples is not None and max_samples >= 0:
        filtered = filtered[:max_samples]
        notes.append(f'max_samples limited to {max_samples}')

    return filtered, notes


def build_snapshot(
    *,
    manifest_version: str,
    mode: str,
    entries: list[RenameSample],
    selection_notes: list[str],
) -> ManifestSnapshot:
    return ManifestSnapshot(
        manifest_version=manifest_version,
        mode=mode,
        selected_count=len(entries),
        selected_sample_ids=[entry.sample_id for entry in entries],
        samples=[entry.to_dict() for entry in entries],
        selection_notes=selection_notes,
    )
