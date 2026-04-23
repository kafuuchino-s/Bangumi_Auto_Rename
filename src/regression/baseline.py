from __future__ import annotations

import json
from pathlib import Path

from .models import BaselineRecord


def baseline_file_path(baseline_root: Path, sample_id: str) -> Path:
    return baseline_root / f'{sample_id}.json'


def load_baseline_record(
    baseline_root: Path,
    *,
    sample_id: str,
) -> BaselineRecord | None:
    path = baseline_file_path(baseline_root, sample_id)
    if not path.exists():
        return None
    with open(path, 'r', encoding='utf-8') as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError(f'Invalid baseline file: {path}')
    return BaselineRecord(
        sample_id=str(payload.get('sample_id') or sample_id),
        schema_version=int(payload.get('schema_version') or 1),
        anchor=bool(payload.get('anchor')),
        captured_at=str(payload.get('captured_at') or ''),
        runtime_signature=dict(payload.get('runtime_signature') or {}),
        expected=dict(payload.get('expected') or {}),
        notes=[str(item) for item in payload.get('notes') or []],
    )


def save_baseline_record(baseline_root: Path, record: BaselineRecord) -> Path:
    path = baseline_file_path(baseline_root, record.sample_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as file:
        json.dump(record.to_dict(), file, indent=2, ensure_ascii=False)
        file.write('\n')
    return path
