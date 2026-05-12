from __future__ import annotations

from collections import OrderedDict
from typing import Any

from .dossier import build_bounded_case_dossier
from .models import CaseDossier
from .workspace import CaseEvidenceWorkspace


def _dedupe(values: list[str]) -> list[str]:
    return list(OrderedDict.fromkeys(v for v in values if v))


def _sample(values: list[str], *, limit: int = 8) -> list[str]:
    values = _dedupe(values)
    if len(values) <= limit:
        return values
    head = values[: max(1, limit // 2)]
    tail = values[-max(1, limit // 2):]
    return _dedupe([*head, *tail])


def _ref_summary(values: list[str], *, limit: int = 4) -> dict[str, Any]:
    values = _dedupe(values)
    return {
        'count': len(values),
        'sample_refs': _sample(values, limit=limit),
        'truncated': len(values) > limit,
    }


def _coerce_dossier(source: CaseEvidenceWorkspace | CaseDossier):
    return source.to_dossier() if isinstance(source, CaseEvidenceWorkspace) else source


def _ref_kind(dossier: CaseDossier, ref: str) -> str:
    visible = dossier.visible_refs
    if ref in visible.local_file_refs:
        return 'local_file'
    if ref in visible.local_cluster_refs:
        return 'local_cluster'
    if ref in visible.bangumi_subject_refs:
        return 'bangumi_subject'
    if ref in visible.bangumi_relation_refs:
        return 'bangumi_relation'
    if ref in visible.bangumi_group_refs:
        return 'bangumi_group'
    if ref in visible.bangumi_item_refs:
        return 'bangumi_item'
    if ref in visible.query_refs:
        return 'query'
    if ref in visible.target_refs:
        return 'target'
    if any(card.ref == ref for card in getattr(dossier, 'provenance_cards', []) or []):
        return 'provenance'
    return 'unknown'


def build_surface_ledger(source: CaseEvidenceWorkspace | CaseDossier) -> dict[str, Any]:
    dossier = _coerce_dossier(source)
    bounded = source if hasattr(source, 'target_overview') and hasattr(source, 'counts') else build_bounded_case_dossier(dossier)
    visible = dossier.visible_refs
    catalog_visible = _dedupe([
        *visible.local_file_refs,
        *visible.local_cluster_refs,
        *visible.bangumi_subject_refs,
        *visible.bangumi_relation_refs,
        *visible.bangumi_group_refs,
        *visible.bangumi_item_refs,
        *visible.query_refs,
        *visible.target_refs,
    ])
    readable = _dedupe([
        *(getattr(bounded, 'primary_title_cues', []) or []),
        *(getattr(bounded, 'release_group_cues', []) or []),
        *(getattr(bounded, 'verifier_issue_summary', []) or []),
        *(getattr(dossier, 'verifier_issue_summary', []) or []),
    ])
    seen_detail = _dedupe([*(getattr(dossier, 'seen_detail_refs', []) or []), *(getattr(bounded, 'seen_detail_refs', []) or [])])
    assignable = _dedupe([*(getattr(dossier, 'assignable_target_refs', []) or []), *(getattr(bounded, 'assignable_target_refs', []) or [])])
    ref_kind = {
        ref: _ref_kind(dossier, ref)
        for ref in _dedupe([
            *catalog_visible,
            *seen_detail,
            *assignable,
            *[card.ref for card in getattr(dossier, 'provenance_cards', []) or []],
        ])
    }
    source_request_refs = _dedupe([card.ref for card in getattr(dossier, 'query_cards', []) or []])
    return {
        'case_id': dossier.header.case_id,
        'catalog_visible': _ref_summary(catalog_visible),
        'readable': _ref_summary(readable),
        'seen_detail': _ref_summary(seen_detail),
        'assignable': _ref_summary(assignable),
        'ref_kind_counts': {kind: list(ref_kind.values()).count(kind) for kind in sorted(set(ref_kind.values()))},
        'ref_kind_samples': {kind: _sample([ref for ref, ref_kind_value in ref_kind.items() if ref_kind_value == kind], limit=4) for kind in sorted(set(ref_kind.values()))},
        'source_request_refs': _ref_summary(source_request_refs),
        'summary': {
            'catalog_visible_count': len(catalog_visible),
            'readable_count': len(readable),
            'seen_detail_count': len(seen_detail),
            'assignable_count': len(assignable),
            'be_ref_opaque': True,
            'no_continuous_gap_inference': True,
        },
    }
