from __future__ import annotations

from typing import Any

from .evidence_menu import build_executable_evidence_menu
from .models import EvidenceRequest
from .workspace import CaseEvidenceWorkspace


def _as_request(value: object) -> EvidenceRequest:
    if isinstance(value, EvidenceRequest):
        return value
    if hasattr(value, 'model_dump'):
        return EvidenceRequest(**dict(value.model_dump(mode='json')))
    if isinstance(value, dict):
        return EvidenceRequest(**dict(value))
    return EvidenceRequest()


def _request_key(request: EvidenceRequest) -> tuple[Any, ...]:
    return (
        str(request.request_type or ''),
        str(request.local_span_ref or ''),
        tuple(sorted(str(ref) for ref in (request.anchor_file_refs or []) if ref)),
        tuple(sorted(str(ref) for ref in (request.item_refs or []) if ref)),
        tuple(sorted(str(ref) for ref in (request.group_refs or []) if ref)),
        tuple(sorted(str(ref) for ref in (request.subject_refs or []) if ref)),
        tuple(sorted(str(ref) for ref in (request.query_refs or []) if ref)),
    )


def resolve_evidence_menu_requests(workspace: CaseEvidenceWorkspace, request_ids: list[str]) -> tuple[list[EvidenceRequest], list[str], list[str], int]:
    menu = build_executable_evidence_menu(workspace)
    registry = dict(menu.get('payload_registry') or {})
    resolved: list[EvidenceRequest] = []
    selected_ids: list[str] = []
    unknown_ids: list[str] = []
    seen: set[tuple[Any, ...]] = set()

    for request_id in [str(request_id or '') for request_id in request_ids if str(request_id or '')]:
        payload = registry.get(request_id)
        if payload is None:
            unknown_ids.append(request_id)
            continue
        selected_ids.append(request_id)
        request = _as_request(payload)
        key = _request_key(request)
        if key in seen:
            continue
        seen.add(key)
        resolved.append(request)

    return resolved, selected_ids, unknown_ids, len(registry)
