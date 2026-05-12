from __future__ import annotations

from typing import Any

from .models import CaseDossier
from .mapping_draft import compute_mapping_draft_accounting
from .workspace import CaseEvidenceWorkspace


def _coerce_dossier(source: CaseEvidenceWorkspace | CaseDossier):
    return source.to_dossier() if isinstance(source, CaseEvidenceWorkspace) else source


def build_notebook(source: CaseEvidenceWorkspace | CaseDossier) -> dict[str, Any]:
    dossier = _coerce_dossier(source)
    plan_state = getattr(dossier, 'plan_state', None)
    mapping_draft = getattr(dossier, 'mapping_draft', None)
    mapping_draft_patches = list(getattr(dossier, 'mapping_draft_patches', []) or [])
    accounting = compute_mapping_draft_accounting(mapping_draft, dossier) if mapping_draft is not None else None
    return {
        'case_id': dossier.header.case_id,
        'rounds': dossier.header.round_index,
        'evidence_requests': len(dossier.previous_evidence_results),
        'results': [
            {'batch_ref': batch.batch_ref, 'status': batch.status, 'request_count': len(batch.request_results)}
            for batch in (dossier.previous_evidence_results or [])[:5]
        ],
        'verifier_issues': [issue.message for issue in (dossier.verifier_issues or [])[:10]],
        'fail_closed_reasons': [],
        'judge_summaries': [getattr(item, 'summary', '') for item in (getattr(dossier, 'previous_hypotheses', []) or [])[:5]],
        'assignment_draft_counts': {
            'main_files': len(dossier.contract.main_file_refs),
            'assignable_targets': len(dossier.assignable_target_refs),
        },
        'plan_state': {
            'active_plan_id': getattr(plan_state, 'plan_id', '') if plan_state else '',
            'plan_kind': getattr(plan_state, 'plan_kind', '') if plan_state else '',
            'plan_status': getattr(plan_state, 'plan_status', 'idle') if plan_state else 'idle',
            'selected_menu_request_ids': list(getattr(plan_state, 'selected_menu_request_ids', []) or [])[:12] if plan_state else [],
            'completed_menu_request_ids': list(getattr(plan_state, 'completed_menu_request_ids', []) or [])[:12] if plan_state else [],
            'failed_menu_request_ids': list(getattr(plan_state, 'failed_menu_request_ids', []) or [])[:12] if plan_state else [],
            'ready_span_refs': list(getattr(plan_state, 'ready_span_refs', []) or [])[:12] if plan_state else [],
        },
        'mapping_draft_summary': {
            'has_mapping_draft': bool(mapping_draft),
            'row_count': len(getattr(mapping_draft, 'rows', []) or []) if mapping_draft else 0,
            'patch_count': len(mapping_draft_patches),
            'main_file_count': int(getattr(accounting, 'main_file_count', 0) or 0),
            'mapped_file_count': int(getattr(accounting, 'mapped_file_count', 0) or 0),
            'excluded_file_count': int(getattr(accounting, 'excluded_file_count', 0) or 0),
            'needs_more_evidence_file_count': int(getattr(accounting, 'needs_more_evidence_file_count', 0) or 0),
            'unaligned_file_count': int(getattr(accounting, 'unaligned_file_count', 0) or 0),
            'open_file_count': int(getattr(accounting, 'open_file_count', 0) or 0),
            'accounted_for_count': int(getattr(accounting, 'accounted_for_count', 0) or 0),
            'unresolved_count': int(getattr(accounting, 'unresolved_count', 0) or 0),
            'accepted_accounting_ready': bool(getattr(accounting, 'accepted_accounting_ready', False)),
        },
        'compact': True,
        'no_full_prompt': True,
        'no_full_raw_output': True,
        'no_full_catalog': True,
    }
