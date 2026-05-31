from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..case_agent.recipe import CompiledOrganizePlan


def load_accepted_compiled_plan_artifact(path: str | Path) -> CompiledOrganizePlan:
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    plan_payload = extract_accepted_compiled_plan_payload(payload)
    return CompiledOrganizePlan.model_validate(plan_payload)


def extract_accepted_compiled_plan_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError('sample artifact payload must be a JSON object')
    status = str(payload.get('status') or '').strip()
    snapshot = payload.get('snapshot') if isinstance(payload.get('snapshot'), dict) else payload
    snapshot_status = str(snapshot.get('status') or snapshot.get('case_agent_status') or '').strip()
    accepted_contract_ok = payload.get('accepted_contract_ok')
    if accepted_contract_ok is None:
        accepted_contract_ok = snapshot.get('accepted_contract_ok')
    if status != 'accepted' and snapshot_status != 'accepted':
        raise ValueError('sample artifact is not an accepted Local->Bangumi result')
    if accepted_contract_ok is False:
        raise ValueError('sample artifact was accepted but failed accepted_contract_ok')
    compiled_plan = snapshot.get('compiled_plan')
    if not isinstance(compiled_plan, dict):
        raise ValueError('accepted sample artifact is missing snapshot.compiled_plan')
    return compiled_plan


def iter_accepted_compiled_plan_artifacts(root: str | Path) -> list[Path]:
    root_path = Path(root)
    if root_path.is_file():
        return [root_path]
    candidates = sorted(path for path in root_path.glob('*.json') if path.name != 'summary.json')
    accepted: list[Path] = []
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
            extract_accepted_compiled_plan_payload(payload)
        except Exception:
            continue
        accepted.append(path)
    return accepted
