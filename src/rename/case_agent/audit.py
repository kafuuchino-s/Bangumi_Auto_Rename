from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .models import CaseAuditManifest


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _ref_range(refs: list[str]) -> str:
    if not refs:
        return ''
    if len(refs) == 1:
        return refs[0]
    return f'{refs[0]}..{refs[-1]}'


def _ref_summary(refs: list[str], *, sample_size: int = 8) -> dict[str, Any]:
    unique_refs = [str(ref) for ref in refs if ref]
    return {
        'count': len(unique_refs),
        'ref_range': _ref_range(unique_refs),
        'ref_samples': unique_refs[:sample_size],
    }


def stable_snapshot_ref(kind: str, case_id: str, round_index: int, suffix: str = "") -> str:
    ref = f"{kind}:{case_id}:r{round_index}"
    if suffix:
        ref = f"{ref}:{suffix}"
    return ref


def serialize_case_agent_artifact(artifact: BaseModel | dict | list) -> object:
    if isinstance(artifact, BaseModel):
        return artifact.model_dump(mode="json")
    if isinstance(artifact, dict):
        return artifact
    if isinstance(artifact, list):
        return artifact
    raise TypeError(f"Unsupported artifact type: {type(artifact)!r}")


def extract_case_agent_snapshot(raw_snapshot_or_payload: object) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []

    def _as_dict(value: object) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    current = _as_dict(raw_snapshot_or_payload)
    if isinstance(current.get('payload'), dict):
        current = current['payload']
    elif isinstance(current.get('snapshot'), dict):
        current = current['snapshot']
    nested_snapshot = current.get('snapshot') if isinstance(current.get('snapshot'), dict) else None
    if nested_snapshot is not None:
        if 'status' in current and current.get('status') != nested_snapshot.get('status'):
            warnings.append('top-level status differs from nested snapshot status; using nested snapshot')
        if isinstance(current.get('result'), str):
            warnings.append('ignored stringified result in favor of nested snapshot')
        return nested_snapshot, warnings

    result = current.get('result')
    if isinstance(result, dict):
        warnings.append('using payload.result as fallback snapshot')
        return result, warnings
    if isinstance(current.get('result'), str) and isinstance(current.get('snapshot'), dict):
        warnings.append('ignored stringified result in favor of nested snapshot')

    return current, warnings


def classify_case_agent_snapshot(snapshot: dict[str, Any]) -> str:
    status = str(snapshot.get('status') or '').casefold()
    error_kind = str(snapshot.get('error_kind') or '').casefold()
    if error_kind == 'context_overflow':
        return 'local_bangumi_mapping_context_overflow'
    if error_kind in {'provider_no_response', 'no_response', 'case_judge_no_response'}:
        return 'local_bangumi_mapping_no_response'
    if status == 'error' and error_kind:
        return 'local_bangumi_mapping_infra_error'
    if status == 'invalid':
        return 'local_bangumi_mapping_invalid'
    if status == 'accepted':
        return 'local_bangumi_mapping_accepted'
    if status == 'fail_closed':
        return 'local_bangumi_mapping_fail_closed'
    return 'local_bangumi_mapping_unknown'


def artifact_hash(artifact: object) -> str:
    payload = json.dumps(artifact, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_case_audit_manifest(
    case_id: str,
    status: str,
    dossier_refs: list[str],
    judge_output_refs: list[str],
    evidence_result_refs: list[str],
    verifier_result_refs: list[str],
    snapshot_refs: list[str],
    notes: list[str],
) -> CaseAuditManifest:
    issue_refs = [*judge_output_refs, *evidence_result_refs, *snapshot_refs]
    summary_parts = [f"status={status}"]
    if notes:
        summary_parts.append(f"notes={len(notes)}")
    if dossier_refs:
        summary_parts.append(f"dossier={len(dossier_refs)}")
    return CaseAuditManifest(
        case_id=case_id,
        audit_round=len(snapshot_refs),
        verifier_refs=verifier_result_refs,
        issue_refs=issue_refs,
        summary="; ".join(summary_parts),
    )


def summarize_case_agent_process(snapshot: dict[str, Any]) -> dict[str, Any]:
    evidence_batches = snapshot.get('evidence_batches') if isinstance(snapshot.get('evidence_batches'), list) else []
    judge_actions = [str(v) for v in _as_list(snapshot.get('judge_round_actions')) if str(v)]
    judge_kinds = [str(v) for v in _as_list(snapshot.get('judge_round_kinds')) if str(v)]
    requests: list[dict[str, Any]] = []
    response_ref_count = 0
    response_ref_samples: list[str] = []
    for batch in evidence_batches:
        if not isinstance(batch, dict):
            continue
        for rr in _as_list(batch.get('request_results')):
            if isinstance(rr, dict):
                request_type = str(rr.get('request_type') or rr.get('request_ref') or '')
                requests.append({'request_type': request_type})
                refs = [str(ref) for ref in _as_list(rr.get('response_refs')) if ref]
                response_ref_count += len(refs)
                response_ref_samples.extend(refs[:8])
    error_kind = str(snapshot.get('error_kind') or '')
    verifier_issues = _as_list(snapshot.get('verifier_issues'))
    case_judge_audits = snapshot.get('case_judge_request_audits') if isinstance(snapshot.get('case_judge_request_audits'), list) else []
    evidence_menu_request_ids: list[str] = []
    evidence_menu_span_request_ids: list[str] = []
    selected_menu_request_ids: list[str] = []
    planner_selected_menu_request_ids: list[str] = []
    planner_plan_kind = ''
    planner_selected_menu_request_count = 0
    unknown_menu_request_ids: list[str] = []
    resolved_menu_request_count = 0
    legacy_raw_request_count = 0
    normalized_legacy_request_count = 0
    for audit in case_judge_audits:
        if not isinstance(audit, dict):
            continue
        if str(audit.get('round_kind') or '') not in {'initial', 'policy_retry', 'evidence_rejudge', 'issue_response'}:
            continue
        for key in ('evidence_menu_request_ids', 'selected_menu_request_ids', 'unknown_menu_request_ids'):
            values = [str(v) for v in _as_list(audit.get(key)) if str(v)]
            if key == 'evidence_menu_request_ids':
                evidence_menu_request_ids.extend(values)
            elif key == 'selected_menu_request_ids':
                selected_menu_request_ids.extend(values)
            else:
                unknown_menu_request_ids.extend(values)
        planner_selected_menu_request_ids.extend([str(v) for v in _as_list(audit.get('planner_selected_menu_request_ids')) if str(v)])
        planner_plan_kind = planner_plan_kind or str(audit.get('planner_plan_kind') or '')
        planner_selected_menu_request_count += int(audit.get('planner_selected_menu_request_count') or 0)
        span_ids = [str(v) for v in _as_list(audit.get('evidence_menu_span_request_ids')) if str(v)]
        evidence_menu_span_request_ids.extend(span_ids)
        resolved_menu_request_count += int(audit.get('resolved_menu_request_count') or audit.get('menu_request_count') or 0)
        legacy_raw_request_count += int(audit.get('legacy_raw_request_count') or 0)
        normalized_legacy_request_count += int(audit.get('normalized_legacy_request_count') or 0)
    case_judge_round_kinds = [str(a.get('round_kind') or '') for a in case_judge_audits if isinstance(a, dict) and str(a.get('round_kind') or '') in {'initial', 'policy_retry', 'evidence_rejudge', 'issue_response'}]
    if case_judge_round_kinds:
        judge_kinds = case_judge_round_kinds
        judge_actions = [
            str(a.get('action_actual') or a.get('action') or a.get('action_name') or a.get('call_name') or '')
            for a in case_judge_audits
            if isinstance(a, dict) and str(a.get('round_kind') or '') in {'initial', 'policy_retry', 'evidence_rejudge', 'issue_response'}
        ]
    hidden_ref_violation_count = 0
    span_alignment_claim_count = int(snapshot.get('span_alignment_claim_count') or 0)
    recommended_target_span_request_count = int(snapshot.get('recommended_target_span_request_count') or 0)
    actual_target_span_request_count = int(snapshot.get('actual_target_span_request_count') or snapshot.get('target_span_request_count') or 0)
    accepted_target_span_request_count = int(snapshot.get('accepted_target_span_request_count') or 0)
    bulk_assignment_intent_count = int(snapshot.get('bulk_assignment_intent_count') or 0)
    expanded_assignment_count = int(snapshot.get('expanded_assignment_count') or 0)
    mapping_draft_row_count = int(snapshot.get('mapping_draft_row_count') or 0)
    mapping_draft_local_coverage_count = int(snapshot.get('mapping_draft_local_coverage_count') or 0)
    mapping_draft_missing_main_count = int(snapshot.get('mapping_draft_missing_main_count') or 0)
    mapping_draft_open_count = int(snapshot.get('mapping_draft_open_count') or 0)
    mapping_draft_proposed_count = int(snapshot.get('mapping_draft_proposed_count') or 0)
    mapping_draft_verified_count = int(snapshot.get('mapping_draft_verified_count') or 0)
    mapping_draft_unresolved_count = int(snapshot.get('mapping_draft_unresolved_count') or 0)
    mapping_draft_patch_count = int(snapshot.get('mapping_draft_patch_count') or 0)
    span_mapping_patch_count = int(snapshot.get('span_mapping_patch_count') or 0)
    candidate_comparison_count = int(snapshot.get('candidate_comparison_count') or 0)
    local_child_span_count = int(snapshot.get('local_child_span_count') or 0)
    local_span_covered_main_count = int(snapshot.get('local_span_covered_main_count') or 0)
    local_span_missing_main_count = int(snapshot.get('local_span_missing_main_count') or 0)
    local_span_overlap_count = int(snapshot.get('local_span_overlap_count') or 0)
    local_span_partition_complete = bool(snapshot.get('local_span_partition_complete', False))
    span_rows_with_candidates = int(snapshot.get('span_rows_with_candidates') or 0)
    span_rows_without_candidates = int(snapshot.get('span_rows_without_candidates') or 0)
    planned_span_request_count = int(snapshot.get('planned_span_request_count') or 0)
    selected_span_request_count = int(snapshot.get('selected_span_request_count') or 0)
    completed_span_request_count = int(snapshot.get('completed_span_request_count') or 0)
    for issue in verifier_issues:
        if isinstance(issue, dict) and str(issue.get('issue_code') or '') in {'unknown_ref', 'invalid_target'}:
            hidden_ref_violation_count += 1
    salience_flags = snapshot.get('salience_risk_flags') if isinstance(snapshot.get('salience_risk_flags'), dict) else {}
    return {
        'judge_round_actions': judge_actions,
        'judge_round_kinds': judge_kinds,
        'evidence_request_count': len(requests),
        'evidence_request_types': [r['request_type'] for r in requests],
        'evidence_request_count_actual': int(snapshot.get('evidence_request_count_actual') or len(requests)),
        'evidence_request_types_actual': list(snapshot.get('evidence_request_types_actual') or [r['request_type'] for r in requests]),
        'evidence_batch_count': len(evidence_batches),
        'evidence_response_ref_count': response_ref_count,
        'evidence_response_ref_samples': response_ref_samples[:8],
        'requested_detail_ref_count': response_ref_count,
        'requested_detail_ref_sample': response_ref_samples[:8],
        'requested_detailed_card_count': len(response_ref_samples),
        'verifier_issue_count': len(verifier_issues),
        'bounded_payload_bytes': int(snapshot.get('bounded_payload_bytes') or 0),
        'initial_projection_bytes': int(snapshot.get('initial_projection_bytes') or 0),
        'rendered_prompt_bytes': int(snapshot.get('rendered_prompt_bytes') or 0),
        'request_body_bytes_estimate': int(snapshot.get('request_body_bytes_estimate') or 0),
        'initial_be_ref_occurrences': int(snapshot.get('initial_be_ref_occurrences') or 0),
        'initial_file_ref_occurrences': int(snapshot.get('initial_file_ref_occurrences') or 0),
        'detailed_visible_card_count': int(snapshot.get('detailed_visible_card_count') or 0),
        'assignable_target_count': len(_as_list(snapshot.get('assignable_target_refs'))),
        'seen_detail_ref_count': len(_as_list(snapshot.get('seen_detail_refs'))),
        'salience_risk_flags': salience_flags,
        'error_kind': error_kind,
        'hidden_ref_violation_count': hidden_ref_violation_count,
        'case_judge_configured_interface': str(snapshot.get('case_judge_configured_interface') or 'unknown'),
        'case_judge_actual_interface': str(snapshot.get('case_judge_actual_interface') or 'unknown'),
        'case_judge_streaming': snapshot.get('case_judge_streaming', 'unknown'),
        'case_agent_status': str(snapshot.get('case_agent_status') or snapshot.get('status') or ''),
        'case_agent_ok': bool(snapshot.get('case_agent_ok', snapshot.get('ok', False))),
        'product_result_kind': str(snapshot.get('product_result_kind') or snapshot.get('status') or ''),
        'case_agent_error_kind': str(snapshot.get('case_agent_error_kind') or snapshot.get('error_kind') or ''),
        'policy_decision_rows': _as_list(snapshot.get('policy_decision_rows')),
        'policy_decision_row_count': int(snapshot.get('policy_decision_row_count') or len(_as_list(snapshot.get('policy_decision_rows')))),
        'premature_reason_histogram': snapshot.get('premature_reason_histogram') if isinstance(snapshot.get('premature_reason_histogram'), dict) else {},
        'recommended_neutral_requests_count': int(snapshot.get('recommended_neutral_requests_count') or 0),
        'recommended_neutral_request_types': list(snapshot.get('recommended_neutral_request_types') or []),
        'recommended_neutral_request_samples': list(snapshot.get('recommended_neutral_request_samples') or []),
        'assignable_target_surface': snapshot.get('assignable_target_surface') if isinstance(snapshot.get('assignable_target_surface'), dict) else {},
        'surface_ledger_count': int(snapshot.get('surface_ledger_count') or 0),
        'plan_status': str(snapshot.get('plan_status') or 'idle'),
        'plan_completed_count': int(snapshot.get('plan_completed_count') or 0),
        'plan_failed_count': int(snapshot.get('plan_failed_count') or 0),
        'plan_selected_count': int(snapshot.get('plan_selected_count') or 0),
        'evidence_menu_count': int(snapshot.get('evidence_menu_count') or 0),
        'evidence_menu_request_count': int(snapshot.get('evidence_menu_request_count') or len(evidence_menu_request_ids)),
        'evidence_menu_span_request_count': int(snapshot.get('evidence_menu_span_request_count') or len(evidence_menu_span_request_ids)),
        'planner_selected_menu_request_ids': planner_selected_menu_request_ids,
        'planner_plan_kind': planner_plan_kind,
        'planner_selected_menu_request_count': planner_selected_menu_request_count,
        'selected_menu_request_ids': list(snapshot.get('selected_menu_request_ids') or selected_menu_request_ids),
        'unknown_menu_request_ids': list(snapshot.get('unknown_menu_request_ids') or unknown_menu_request_ids),
        'resolved_menu_request_count': int(snapshot.get('resolved_menu_request_count') or resolved_menu_request_count),
        'legacy_raw_request_count': int(snapshot.get('legacy_raw_request_count') or legacy_raw_request_count),
        'normalized_legacy_request_count': int(snapshot.get('normalized_legacy_request_count') or normalized_legacy_request_count),
        'action_policy_allowed': list(snapshot.get('action_policy_allowed') or []),
        'action_policy_disallowed': list(snapshot.get('action_policy_disallowed') or []),
        'action_policy_final_opportunity': bool(snapshot.get('action_policy_final_opportunity', False)),
        'notebook_compact_counts': snapshot.get('notebook_compact_counts') if isinstance(snapshot.get('notebook_compact_counts'), dict) else {},
        'issue_router_issue_counts': snapshot.get('issue_router_issue_counts') if isinstance(snapshot.get('issue_router_issue_counts'), dict) else {},
        'local_span_count': int(snapshot.get('local_span_count') or 0),
        'local_child_span_count': local_child_span_count,
        'local_span_covered_main_count': local_span_covered_main_count,
        'local_span_missing_main_count': local_span_missing_main_count,
        'local_span_overlap_count': local_span_overlap_count,
        'local_span_partition_complete': local_span_partition_complete,
        'local_span_main_file_count': int(snapshot.get('local_span_main_file_count') or 0),
        'bangumi_span_count': int(snapshot.get('bangumi_span_count') or 0),
        'detail_equivalent_target_span_count': int(snapshot.get('detail_equivalent_target_span_count') or 0),
        'span_alignment_claim_count': span_alignment_claim_count,
        'bulk_assignment_intent_count': bulk_assignment_intent_count,
        'expanded_assignment_count': expanded_assignment_count,
        'mapping_draft_row_count': mapping_draft_row_count,
        'mapping_draft_local_coverage_count': mapping_draft_local_coverage_count,
        'mapping_draft_missing_main_count': mapping_draft_missing_main_count,
        'mapping_draft_open_count': mapping_draft_open_count,
        'mapping_draft_proposed_count': mapping_draft_proposed_count,
        'mapping_draft_verified_count': mapping_draft_verified_count,
        'mapping_draft_unresolved_count': mapping_draft_unresolved_count,
        'mapping_draft_patch_count': mapping_draft_patch_count,
        'span_mapping_patch_count': span_mapping_patch_count,
        'candidate_comparison_count': candidate_comparison_count,
        'span_rows_with_candidates': span_rows_with_candidates,
        'span_rows_without_candidates': span_rows_without_candidates,
        'planned_span_request_count': planned_span_request_count,
        'selected_span_request_count': selected_span_request_count,
        'completed_span_request_count': completed_span_request_count,
        'recommended_target_span_request_count': recommended_target_span_request_count,
        'actual_target_span_request_count': actual_target_span_request_count,
        'accepted_target_span_request_count': accepted_target_span_request_count,
        'target_span_request_count': actual_target_span_request_count,
    }


def summarize_case_agent_snapshot_refs(snapshot: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    bounded_counts = snapshot.get('bounded_payload_counts') if isinstance(snapshot.get('bounded_payload_counts'), dict) else {}
    for key in (
        'main_file_count',
        'mapped_file_count',
        'excluded_file_count',
        'needs_more_evidence_file_count',
        'unaligned_file_count',
        'open_file_count',
        'accounted_for_count',
        'unresolved_count',
    ):
        summary[key] = int(snapshot.get(key) or 0)
    summary['accepted_accounting_ready'] = bool(snapshot.get('accepted_accounting_ready', False))
    for key in ('contract_main_file_refs', 'final_output_main_file_refs', 'visible_target_refs'):
        refs = [str(ref) for ref in _as_list(snapshot.get(key)) if ref]
        compact = _ref_summary(refs)
        base = key[:-5] if key.endswith('_refs') else key
        count_value = compact['count']
        if count_value == 0 and key in {'contract_main_file_refs', 'final_output_main_file_refs'}:
            fallback_keys = (
                f'{base}_count',
                'main_file_count' if 'main_file' in base else '',
            )
            for fallback_key in fallback_keys:
                if fallback_key and isinstance(snapshot.get(fallback_key), int):
                    count_value = int(snapshot[fallback_key])
                    break
            if count_value == 0 and isinstance(bounded_counts.get('main_file_count'), int):
                count_value = int(bounded_counts['main_file_count'])
        summary[f'{base}_count'] = count_value
        summary[f'{base}_range'] = compact['ref_range']
        summary[f'{base}_samples'] = compact['ref_samples']
    query_cards = _as_list(snapshot.get('query_card_sample'))
    compact_cards: list[dict[str, Any]] = []
    for card in query_cards:
        if not isinstance(card, dict):
            continue
        refs = [str(ref) for ref in _as_list(card.get('source_refs')) if ref]
        compact_card = dict(card)
        compact_card.pop('source_refs', None)
        compact_card['source_ref_count'] = len(refs)
        compact_card['source_ref_samples'] = refs[:8]
        if refs:
            compact_card['source_ref_range'] = _ref_range(refs)
        compact_cards.append(compact_card)
    if compact_cards:
        summary['query_card_sample'] = compact_cards
    for key in ('local_span_count', 'local_child_span_count', 'local_span_covered_main_count', 'local_span_missing_main_count', 'local_span_overlap_count', 'local_span_partition_complete', 'local_span_main_file_count', 'bangumi_span_count', 'detail_equivalent_target_span_count', 'span_alignment_claim_count', 'bulk_assignment_intent_count', 'expanded_assignment_count', 'recommended_target_span_request_count', 'actual_target_span_request_count', 'accepted_target_span_request_count', 'target_span_request_count', 'mapping_draft_row_count', 'mapping_draft_local_coverage_count', 'mapping_draft_missing_main_count', 'mapping_draft_open_count', 'mapping_draft_proposed_count', 'mapping_draft_verified_count', 'mapping_draft_unresolved_count', 'mapping_draft_patch_count', 'span_mapping_patch_count', 'candidate_comparison_count', 'span_rows_with_candidates', 'span_rows_without_candidates', 'planned_span_request_count', 'selected_span_request_count', 'completed_span_request_count', 'main_file_count', 'mapped_file_count', 'excluded_file_count', 'needs_more_evidence_file_count', 'unaligned_file_count', 'open_file_count', 'accounted_for_count', 'unresolved_count'):
        summary[key] = int(snapshot.get(key) or 0)
    summary['accepted_accounting_ready'] = bool(snapshot.get('accepted_accounting_ready', False))
    return summary


def write_case_agent_json(path: str | Path, artifact: BaseModel | dict | list) -> None:
    serialized = serialize_case_agent_artifact(artifact)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(serialized, ensure_ascii=False, indent=2), encoding="utf-8")
