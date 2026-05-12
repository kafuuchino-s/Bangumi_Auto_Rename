from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from ..logger import logger


def decision_snapshot_root() -> Path | None:
    """Return the active AI decision snapshot root.

    Snapshots are opt-in.  The regression lane sets BAR_AI_DECISION_SNAPSHOT_DIR
    per sample so production runs do not write large prompt/debug artifacts unless
    explicitly requested.
    """

    configured = os.environ.get('BAR_AI_DECISION_SNAPSHOT_DIR', '').strip()
    if not configured:
        return None
    return Path(configured)


def decision_snapshot_enabled() -> bool:
    return decision_snapshot_root() is not None


def write_decision_snapshot(
    stage: str,
    payload: dict[str, Any],
    *,
    source_path: Path | str | None = None,
) -> str | None:
    root = decision_snapshot_root()
    if root is None:
        return None

    try:
        root.mkdir(parents=True, exist_ok=True)
        safe_stage = re.sub(r'[^a-zA-Z0-9_.-]+', '_', stage).strip('_') or 'decision'
        file_name = f'{time.time_ns()}_{safe_stage}_{uuid.uuid4().hex[:8]}.json'
        path = root / file_name
        snapshot_payload: dict[str, Any] = {
            'artifact_type': 'ai_decision_snapshot',
            'schema_version': 1,
            'stage': stage,
            'source_path': str(source_path) if source_path is not None else None,
            'summary': _decision_snapshot_summary(payload),
            'payload': payload,
        }
        temp_path = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
        temp_path.write_text(
            json.dumps(snapshot_payload, ensure_ascii=False, indent=2, default=str),
            encoding='utf-8',
        )
        os.replace(temp_path, path)
        return str(path)
    except Exception as exc:
        logger.debug(f'[AI决策快照] 写入失败: {exc}')
        return None


def _decision_snapshot_summary(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        raw = json.dumps(payload, ensure_ascii=False, default=str, separators=(',', ':'))
    except Exception:
        raw = ''
    summary: dict[str, Any] = {
        'payload_bytes_utf8': len(raw.encode('utf-8')) if raw else None,
        'top_level_keys': sorted(str(key) for key in payload.keys())[:80],
    }
    prompt = payload.get('prompt')
    if isinstance(prompt, str):
        summary['prompt_chars'] = len(prompt)
        summary['prompt_bytes_utf8'] = len(prompt.encode('utf-8'))
    for key in ('input', 'output', 'validation'):
        value = payload.get(key)
        if value is None:
            continue
        try:
            summary[f'{key}_bytes_utf8'] = len(json.dumps(value, ensure_ascii=False, default=str, separators=(',', ':')).encode('utf-8'))
        except Exception:
            pass
    return summary
