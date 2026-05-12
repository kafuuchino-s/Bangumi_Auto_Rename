from __future__ import annotations

from typing import Any

from .models import EvidenceRequest


def _as_dict(value: object) -> dict[str, object]:
    if hasattr(value, 'model_dump'):
        return dict(value.model_dump(mode='json'))
    if isinstance(value, dict):
        return dict(value)
    return {}


def _get_span_cards(workspace_or_dossier: object) -> list[object]:
    return list(getattr(workspace_or_dossier, 'local_span_cards', []) or [])


def _get_budget_max_requests(workspace_or_dossier: object) -> int:
    budget = getattr(workspace_or_dossier, 'budget', None)
    return int(getattr(budget, 'max_requests_per_batch', 0) or 0)


def normalize_evidence_requests(workspace_or_dossier, requests, *, max_requests: int | None = None) -> tuple[list[EvidenceRequest], list[dict[str, object]]]:
    span_cards = _get_span_cards(workspace_or_dossier)
    request_list = [req if isinstance(req, EvidenceRequest) else EvidenceRequest(**_as_dict(req)) for req in list(requests or [])]
    cap = max_requests if max_requests is not None else _get_budget_max_requests(workspace_or_dossier)
    normalized: list[EvidenceRequest] = []
    audits: list[dict[str, object]] = []

    package_children = [card for card in span_cards if str(getattr(card, 'ref', '')) != 'LS_PACKAGE' and str(getattr(card, 'span_scope', '')) != 'package']

    for request in request_list:
        if request.request_type == 'target_span' and request.local_span_ref == 'LS_PACKAGE' and package_children:
            base = request.model_dump(mode='json')
            children = package_children
            if cap and cap > 0:
                children = children[:max(0, cap)]
            truncated = len(children) < len(package_children)
            for child in children:
                normalized.append(EvidenceRequest(**{
                    **base,
                    'request_ref': f"{request.request_ref}_{getattr(child, 'ref', '')}" if request.request_ref else str(getattr(child, 'ref', '')),
                    'local_span_ref': str(getattr(child, 'ref', '')),
                    'expected_count': int(getattr(child, 'file_ref_count', 0) or 0),
                    'anchor_file_refs': list(getattr(child, 'file_ref_samples', []) or []),
                }))
            audits.append({
                'note': 'package_span_request_split_to_child_spans',
                'from_local_span_ref': 'LS_PACKAGE',
                'child_span_count': len(children),
                'child_span_total': len(package_children),
                'truncated': truncated,
                'request_ref': request.request_ref,
            })
            continue
        normalized.append(request)
    return normalized, audits
