from __future__ import annotations

import json

from pydantic import BaseModel

from src.rename.case_agent.audit import (
    artifact_hash,
    build_case_audit_manifest,
    summarize_case_agent_process,
    summarize_case_agent_snapshot_refs,
    extract_case_agent_snapshot,
    classify_case_agent_snapshot,
    serialize_case_agent_artifact,
    stable_snapshot_ref,
    write_case_agent_json,
)


def test_summarize_case_agent_snapshot_includes_accounting_counts_and_defaults():
    summary = summarize_case_agent_snapshot_refs({
        'main_file_count': 2,
        'mapped_file_count': 1,
        'excluded_file_count': 1,
        'needs_more_evidence_file_count': 0,
        'unaligned_file_count': 0,
        'open_file_count': 0,
        'accounted_for_count': 2,
        'unresolved_count': 0,
        'accepted_accounting_ready': True,
    })

    assert summary['main_file_count'] == 2
    assert summary['mapped_file_count'] == 1
    assert summary['accounted_for_count'] == 2
    assert summary['accepted_accounting_ready'] is True


def test_summarize_case_agent_snapshot_defaults_old_input():
    summary = summarize_case_agent_snapshot_refs({})

    assert summary['main_file_count'] == 0
    assert summary['mapped_file_count'] == 0
    assert summary['excluded_file_count'] == 0
    assert summary['needs_more_evidence_file_count'] == 0
    assert summary['unaligned_file_count'] == 0
    assert summary['open_file_count'] == 0
    assert summary['accounted_for_count'] == 0
    assert summary['unresolved_count'] == 0
    assert summary['accepted_accounting_ready'] is False


class DemoModel(BaseModel):
    name: str
    value: int


def test_build_case_audit_manifest_fields() -> None:
    manifest = build_case_audit_manifest(
        case_id="case-001",
        status="open",
        dossier_refs=["d1"],
        judge_output_refs=["j1"],
        evidence_result_refs=["e1"],
        verifier_result_refs=["v1", "v2"],
        snapshot_refs=["s1", "s2"],
        notes=["n1"],
    )

    assert manifest.case_id == "case-001"
    assert manifest.audit_round == 2
    assert manifest.verifier_refs == ["v1", "v2"]
    assert manifest.issue_refs == ["j1", "e1", "s1", "s2"]
    assert "status=open" in manifest.summary


def test_stable_snapshot_ref_distinguishes_inputs() -> None:
    a = stable_snapshot_ref("manifest", "case-001", 1)
    b = stable_snapshot_ref("manifest", "case-001", 2)
    c = stable_snapshot_ref("manifest", "case-001", 1, "x")

    assert a == stable_snapshot_ref("manifest", "case-001", 1)
    assert a != b
    assert a != c


def test_serialize_case_agent_artifact_supports_models_dicts_lists() -> None:
    model = DemoModel(name="demo", value=1)
    dict_obj = {"b": 2, "a": 1}
    list_obj = [1, {"x": True}]

    assert serialize_case_agent_artifact(model) == {"name": "demo", "value": 1}
    assert serialize_case_agent_artifact(dict_obj) is dict_obj
    assert serialize_case_agent_artifact(list_obj) is list_obj


def test_artifact_hash_is_stable_and_content_sensitive() -> None:
    left = {"b": 2, "a": [1, 2]}
    right = {"a": [1, 2], "b": 2}
    different = {"a": [1, 3], "b": 2}

    assert artifact_hash(left) == artifact_hash(right)
    assert artifact_hash(left) != artifact_hash(different)


def test_write_case_agent_json_roundtrip(tmp_path) -> None:
    path = tmp_path / "nested" / "artifact.json"
    artifact = DemoModel(name="demo", value=7)

    write_case_agent_json(path, artifact)

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == {"name": "demo", "value": 7}


def test_extract_case_agent_snapshot_prefers_nested_snapshot_and_warns_on_conflict() -> None:
    raw = {
        'payload': {
            'status': 'accepted',
            'result': {'status': 'accepted', 'assignment_intent_count': 0},
            'snapshot': {'status': 'fail_closed', 'assignment_intent_count': 2},
        }
    }

    snapshot, warnings = extract_case_agent_snapshot(raw)

    assert snapshot['status'] == 'fail_closed'
    assert snapshot['assignment_intent_count'] == 2
    assert any('nested snapshot' in warning or 'stringified result' in warning for warning in warnings)


def test_extract_case_agent_snapshot_ignores_stringified_result_when_snapshot_present() -> None:
    raw = {
        'payload': {
            'result': 'CaseAgentRunResult(...)',
            'snapshot': {'status': 'accepted', 'assignment_intent_count': 3},
        }
    }

    snapshot, warnings = extract_case_agent_snapshot(raw)

    assert snapshot['status'] == 'accepted'
    assert snapshot['assignment_intent_count'] == 3
    assert any('stringified result' in warning or 'nested snapshot' in warning for warning in warnings)


def test_classify_case_agent_snapshot_no_response() -> None:
    assert classify_case_agent_snapshot({'status': 'error', 'error_kind': 'provider_no_response'}) == 'local_bangumi_mapping_no_response'


def test_summarize_case_agent_process_counts_and_flags() -> None:
    summary = summarize_case_agent_process({
        'judge_round_actions': ['request_evidence', 'submit_verdict'],
        'judge_round_kinds': ['initial', 'evidence_rejudge'],
        'evidence_batches': [{'request_results': [{'request_type': 'target_detail', 'response_refs': ['BE1']}, {'request_type': 'local_file_detail', 'response_refs': ['LF1']}]}],
        'verifier_issues': [{'issue_code': 'unknown_ref', 'message': 'bad'}],
        'salience_risk_flags': {'large_case': True, 'context_budget_risk': True},
        'bounded_payload_bytes': 123,
        'detailed_visible_card_count': 2,
        'assignable_target_refs': ['BE1'],
        'seen_detail_refs': ['BE1', 'LF1'],
        'error_kind': '',
    })

    assert summary['evidence_request_count'] == 2
    assert summary['evidence_response_ref_count'] == 2
    assert summary['hidden_ref_violation_count'] == 1
    assert summary['salience_risk_flags']['large_case'] is True


def test_summarize_case_agent_process_exposes_phase_g_counts() -> None:
    summary = summarize_case_agent_process({
        'judge_round_actions': ['request_evidence'],
        'judge_round_kinds': ['initial'],
        'evidence_batches': [],
        'verifier_issues': [],
        'salience_risk_flags': {},
        'error_kind': '',
        'surface_ledger_count': 3,
        'evidence_menu_count': 2,
        'action_policy_allowed': ['request_evidence'],
        'action_policy_disallowed': ['fail_closed'],
        'action_policy_final_opportunity': True,
        'notebook_compact_counts': {'rounds': 1},
        'issue_router_issue_counts': {'count': 0},
    })
    assert summary['surface_ledger_count'] == 3
    assert summary['evidence_menu_count'] == 2
    assert summary['action_policy_final_opportunity'] is True


def test_summarize_case_agent_process_exposes_compact_span_counts() -> None:
    summary = summarize_case_agent_process({
        'judge_round_actions': ['request_evidence'],
        'judge_round_kinds': ['initial'],
        'evidence_batches': [],
        'verifier_issues': [],
        'salience_risk_flags': {},
        'error_kind': '',
        'local_span_count': 4,
        'local_span_main_file_count': 2,
        'bangumi_span_count': 6,
        'detail_equivalent_target_span_count': 3,
        'span_alignment_claim_count': 5,
        'bulk_assignment_intent_count': 1,
        'expanded_assignment_count': 2,
        'recommended_target_span_request_count': 2,
        'actual_target_span_request_count': 7,
        'accepted_target_span_request_count': 7,
        'target_span_request_count': 7,
    })

    assert summary['local_span_count'] == 4
    assert summary['local_span_main_file_count'] == 2
    assert summary['bangumi_span_count'] == 6
    assert summary['detail_equivalent_target_span_count'] == 3
    assert summary['span_alignment_claim_count'] == 5
    assert summary['bulk_assignment_intent_count'] == 1
    assert summary['expanded_assignment_count'] == 2
    assert summary['recommended_target_span_request_count'] == 2
    assert summary['actual_target_span_request_count'] == 7
    assert summary['accepted_target_span_request_count'] == 7
    assert summary['target_span_request_count'] == 7


def test_summarize_case_agent_process_exposes_mapping_draft_counts() -> None:
    summary = summarize_case_agent_process({
        'judge_round_actions': ['request_evidence'],
        'judge_round_kinds': ['initial'],
        'evidence_batches': [],
        'verifier_issues': [],
        'salience_risk_flags': {},
        'error_kind': '',
        'mapping_draft_row_count': 4,
        'mapping_draft_open_count': 1,
        'mapping_draft_proposed_count': 1,
        'mapping_draft_verified_count': 1,
        'mapping_draft_unresolved_count': 1,
        'mapping_draft_patch_count': 2,
        'span_mapping_patch_count': 3,
        'candidate_comparison_count': 5,
        'expanded_assignment_count': 6,
    })

    assert summary['mapping_draft_row_count'] == 4
    assert summary['mapping_draft_open_count'] == 1
    assert summary['mapping_draft_proposed_count'] == 1
    assert summary['mapping_draft_verified_count'] == 1
    assert summary['mapping_draft_unresolved_count'] == 1
    assert summary['mapping_draft_patch_count'] == 2
    assert summary['span_mapping_patch_count'] == 3
    assert summary['candidate_comparison_count'] == 5
    assert summary['expanded_assignment_count'] == 6


def test_summarize_case_agent_process_defaults_new_span_and_mapping_fields_to_zero() -> None:
    summary = summarize_case_agent_process({
        'judge_round_actions': ['request_evidence'],
        'judge_round_kinds': ['initial'],
        'evidence_batches': [],
        'verifier_issues': [],
        'salience_risk_flags': {},
        'error_kind': '',
    })

    assert summary['local_child_span_count'] == 0
    assert summary['local_span_covered_main_count'] == 0
    assert summary['local_span_missing_main_count'] == 0
    assert summary['local_span_overlap_count'] == 0
    assert summary['local_span_partition_complete'] is False
    assert summary['mapping_draft_local_coverage_count'] == 0
    assert summary['mapping_draft_missing_main_count'] == 0
    assert summary['span_rows_with_candidates'] == 0
    assert summary['span_rows_without_candidates'] == 0
    assert summary['planned_span_request_count'] == 0
    assert summary['selected_span_request_count'] == 0
    assert summary['completed_span_request_count'] == 0


def test_summarize_case_agent_process_keeps_recommended_separate_from_actual() -> None:
    summary = summarize_case_agent_process({
        'judge_round_actions': ['request_evidence'],
        'judge_round_kinds': ['initial'],
        'evidence_batches': [],
        'verifier_issues': [],
        'salience_risk_flags': {},
        'error_kind': '',
        'recommended_target_span_request_count': 2,
        'actual_target_span_request_count': 0,
        'accepted_target_span_request_count': 0,
    })

    assert summary['recommended_target_span_request_count'] == 2
    assert summary['actual_target_span_request_count'] == 0
    assert summary['accepted_target_span_request_count'] == 0


def test_summarize_case_agent_process_request_types_not_empty() -> None:
    summary = summarize_case_agent_process({
        'judge_round_actions': ['request_evidence'],
        'judge_round_kinds': ['initial'],
        'evidence_batches': [{'request_results': [{'request_type': 'target_detail', 'response_refs': ['BE1']}]}],
        'verifier_issues': [],
        'salience_risk_flags': {},
        'error_kind': '',
    })
    assert summary['evidence_request_types'] == ['target_detail']


def test_summarize_case_agent_process_tracks_requested_refs_and_transport() -> None:
    summary = summarize_case_agent_process({
        'judge_round_actions': ['request_evidence'],
        'judge_round_kinds': ['initial'],
        'evidence_batches': [{'request_results': [{'request_type': 'target_detail', 'response_refs': ['BE10', 'BE11']}]}],
        'verifier_issues': [],
        'salience_risk_flags': {},
        'error_kind': '',
        'initial_be_ref_occurrences': 7,
        'initial_file_ref_occurrences': 9,
        'initial_projection_bytes': 111,
        'rendered_prompt_bytes': 222,
        'request_body_bytes_estimate': 333,
        'case_judge_configured_interface': 'call_case_judge',
        'case_judge_actual_interface': 'call_case_judge',
        'case_judge_streaming': False,
    })
    assert summary['requested_detail_ref_count'] == 2
    assert summary['requested_detail_ref_sample'] == ['BE10', 'BE11']
    assert summary['initial_be_ref_occurrences'] == 7
    assert summary['case_judge_configured_interface'] == 'call_case_judge'
    assert summary['case_judge_actual_interface'] == 'call_case_judge'


def test_summarize_case_agent_process_handles_request_audits_and_transport_unknown() -> None:
    summary = summarize_case_agent_process({
        'judge_round_actions': ['request_evidence', 'submit_verdict', 'fail_closed'],
        'judge_round_kinds': ['initial', 'evidence_rejudge', 'issue_response'],
        'evidence_batches': [{'request_results': [{'request_type': 'target_window', 'response_refs': ['BE9', 'BE10', 'BE11']}]}],
        'verifier_issues': [],
        'salience_risk_flags': {},
        'error_kind': '',
        'case_judge_configured_interface': 'responses_api',
        'case_judge_actual_interface': 'unavailable',
        'case_judge_streaming': False,
        'initial_be_ref_occurrences': 2,
        'initial_file_ref_occurrences': 2,
        'initial_projection_bytes': 100,
        'rendered_prompt_bytes': 90,
        'request_body_bytes_estimate': 120,
    })
    assert summary['case_judge_configured_interface'] == 'responses_api'
    assert summary['case_judge_actual_interface'] == 'unavailable'


def test_summarize_case_agent_process_reads_menu_audit_fields() -> None:
    summary = summarize_case_agent_process({
        'judge_round_actions': ['request_evidence'],
        'judge_round_kinds': ['initial'],
        'evidence_batches': [],
        'verifier_issues': [],
        'salience_risk_flags': {},
        'error_kind': '',
        'case_judge_request_audits': [
            {
                'round_kind': 'initial',
                'evidence_menu_request_ids': ['R1', 'R2'],
                'evidence_menu_span_request_ids': ['S1'],
                'selected_menu_request_ids': ['R1'],
                'unknown_menu_request_ids': ['UX'],
                'resolved_menu_request_count': 2,
                'legacy_raw_request_count': 3,
                'normalized_legacy_request_count': 1,
            }
        ],
    })

    assert summary['evidence_menu_request_count'] == 2
    assert summary['evidence_menu_span_request_count'] == 1
    assert summary['selected_menu_request_ids'] == ['R1']
    assert summary['unknown_menu_request_ids'] == ['UX']
    assert summary['resolved_menu_request_count'] == 2
    assert summary['legacy_raw_request_count'] == 3
    assert summary['normalized_legacy_request_count'] == 1


def test_summarize_case_agent_process_prefers_case_judge_request_audits_rounds() -> None:
    summary = summarize_case_agent_process({
        'judge_round_actions': ['submit_verdict'],
        'judge_round_kinds': ['initial'],
        'case_judge_request_audits': [
            {'round_kind': 'initial', 'action_actual': 'request_evidence'},
            {'round_kind': 'policy_retry', 'action_actual': 'submit_verdict'},
            {'round_kind': 'policy_check', 'action_expected': 'policy_check'},
        ],
        'evidence_batches': [],
        'verifier_issues': [],
        'salience_risk_flags': {},
        'error_kind': '',
    })

    assert summary['judge_round_kinds'] == ['initial', 'policy_retry']
    assert summary['judge_round_actions'] == ['request_evidence', 'submit_verdict']


def test_summarize_case_agent_process_does_not_emit_blank_action_rows() -> None:
    summary = summarize_case_agent_process({
        'judge_round_actions': [''],
        'judge_round_kinds': ['initial'],
        'case_judge_request_audits': [
            {'round_kind': 'initial', 'action_actual': 'request_evidence', 'action_expected': 'request_evidence'},
        ],
        'evidence_batches': [],
        'verifier_issues': [],
        'salience_risk_flags': {},
        'error_kind': '',
    })

    assert summary['judge_round_actions'] == ['request_evidence']


def test_summarize_case_agent_process_uses_actual_actions_and_counts() -> None:
    summary = summarize_case_agent_process({
        'judge_round_actions': [''],
        'judge_round_kinds': ['policy_retry'],
        'case_judge_request_audits': [
            {'round_kind': 'policy_retry', 'action_expected': 'submit_verdict_or_fail_closed_or_request_evidence', 'action_actual': 'request_evidence', 'evidence_request_count_actual': 1, 'evidence_request_types_actual': ['target_detail']},
        ],
        'evidence_batches': [{'request_results': [{'request_type': 'target_detail', 'response_refs': ['BE1']}]}],
        'verifier_issues': [],
        'salience_risk_flags': {},
        'error_kind': '',
    })

    assert summary['judge_round_actions'] == ['request_evidence']
    assert summary['evidence_request_count_actual'] == 1
    assert summary['evidence_request_types_actual'] == ['target_detail']


def test_summarize_case_agent_process_exposes_casejudge_audit_rows() -> None:
    summary = summarize_case_agent_process({
        'judge_round_actions': ['request_evidence'],
        'judge_round_kinds': ['initial'],
        'case_judge_request_audits': [
            {'round_kind': 'initial', 'call_name': 'call_case_judge', 'action_actual': 'request_evidence', 'evidence_request_count_actual': 1, 'evidence_request_types_actual': ['target_detail'], 'cache_event': 'hit', 'configured_interface': 'responses_api', 'actual_interface': 'responses_api', 'streaming': False, 'input_bytes': 10, 'output_bytes': 20},
        ],
        'evidence_batches': [],
        'verifier_issues': [],
        'salience_risk_flags': {},
        'error_kind': '',
    })

    assert summary['policy_decision_rows'] == []
    assert summary['policy_decision_row_count'] == 0


def test_summarize_case_agent_process_policy_retry_summary_has_recommended_requests() -> None:
    summary = summarize_case_agent_process({
        'judge_round_actions': ['fail_closed'],
        'judge_round_kinds': ['policy_retry'],
        'recommended_neutral_requests_count': 2,
        'recommended_neutral_request_types': ['target_detail', 'target_window'],
        'recommended_neutral_request_samples': [
            {'request_type': 'target_detail', 'item_refs': ['BE1', 'BE109'], 'reason': 'boundary/sample target refs'},
            {'request_type': 'target_window', 'item_refs': ['BE1', 'BE109'], 'reason': 'visible target boundary window'},
        ],
        'policy_decision_rows': [],
        'case_judge_request_audits': [],
        'evidence_batches': [],
        'verifier_issues': [],
        'salience_risk_flags': {},
        'error_kind': '',
    })

    assert summary['recommended_neutral_requests_count'] == 2
    assert summary['recommended_neutral_request_types'] == ['target_detail', 'target_window']


def test_summarize_case_agent_process_final_opportunity_fields() -> None:
    summary = summarize_case_agent_process({
        'judge_round_actions': ['request_evidence', 'fail_closed'],
        'judge_round_kinds': ['evidence_rejudge', 'evidence_rejudge'],
        'case_judge_request_audits': [
            {'round_kind': 'evidence_rejudge', 'action_actual': 'request_evidence', 'remaining_evidence_batches': 0, 'remaining_judge_rounds': 0, 'final_opportunity': True, 'evidence_request_count_actual': 1, 'evidence_request_types_actual': ['target_detail']},
        ],
        'evidence_batches': [],
        'verifier_issues': [],
        'salience_risk_flags': {},
        'error_kind': '',
    })

    assert summary['judge_round_actions'] == ['request_evidence']


def test_summarize_case_agent_process_status_taxonomy_fields() -> None:
    summary = summarize_case_agent_process({
        'judge_round_actions': ['fail_closed'],
        'judge_round_kinds': ['issue_response'],
        'case_agent_status': 'fail_closed',
        'case_agent_ok': True,
        'product_result_kind': 'fail_closed',
        'case_agent_error_kind': '',
        'evidence_batches': [],
        'verifier_issues': [],
        'salience_risk_flags': {},
        'error_kind': '',
    })
    assert summary['error_kind'] == ''


def test_summarize_case_agent_process_case_agent_taxonomy_fields() -> None:
    summary = summarize_case_agent_process({
        'case_agent_status': 'fail_closed',
        'case_agent_ok': True,
        'product_result_kind': 'fail_closed',
        'case_agent_error_kind': '',
        'judge_round_actions': ['fail_closed'],
        'judge_round_kinds': ['issue_response'],
        'evidence_batches': [],
        'verifier_issues': [],
        'salience_risk_flags': {},
        'error_kind': '',
    })
    assert summary['case_agent_status'] == 'fail_closed'
    assert summary['product_result_kind'] == 'fail_closed'


def test_summarize_case_agent_process_final_request_normalization_counts_as_fail_closed() -> None:
    summary = summarize_case_agent_process({
        'case_agent_status': 'fail_closed',
        'case_agent_ok': True,
        'product_result_kind': 'fail_closed',
        'final_request_evidence_normalized_to_fail_closed': True,
        'normalization_reason': 'evidence_budget_exhausted_after_prior_batches',
        'judge_round_actions': ['fail_closed'],
        'judge_round_kinds': ['evidence_rejudge'],
        'evidence_batches': [{'status': 'accepted', 'request_results': [{'request_type': 'target_detail', 'response_refs': ['BE1']}]}],
        'verifier_issues': [],
        'salience_risk_flags': {},
        'error_kind': '',
    })
    assert summary['case_agent_status'] == 'fail_closed'


def test_summarize_case_agent_process_status_taxonomy_fallback_and_canonical_ok() -> None:
    summary = summarize_case_agent_process({
        'status': 'invalid',
        'ok': False,
        'case_agent_status': '',
        'case_agent_ok': None,
        'product_result_kind': '',
        'judge_round_actions': [],
        'judge_round_kinds': [],
        'evidence_batches': [],
        'verifier_issues': [],
        'salience_risk_flags': {},
        'error_kind': '',
    })

    assert summary['case_agent_status'] == 'invalid'
    assert summary['case_agent_ok'] is False
    assert summary['product_result_kind'] == 'invalid'


def test_summarize_case_agent_process_treats_fail_closed_as_ok() -> None:
    summary = summarize_case_agent_process({
        'case_agent_status': 'fail_closed',
        'case_agent_ok': True,
        'product_result_kind': 'fail_closed',
        'judge_round_actions': ['fail_closed'],
        'judge_round_kinds': ['issue_response'],
        'evidence_batches': [],
        'verifier_issues': [],
        'salience_risk_flags': {},
        'error_kind': '',
    })

    assert summary['case_agent_status'] == 'fail_closed'
    assert summary['case_agent_ok'] is True


def test_summarize_case_agent_process_partial_batch_counts() -> None:
    summary = summarize_case_agent_process({
        'judge_round_actions': ['request_evidence'],
        'judge_round_kinds': ['initial'],
        'evidence_batches': [{'status': 'partial', 'request_results': [{'accepted': True, 'request_type': 'target_window', 'response_refs': ['BE1']}, {'accepted': False, 'request_type': 'local_file_detail', 'response_refs': []}]}],
        'verifier_issues': [],
        'salience_risk_flags': {},
        'error_kind': '',
    })
    assert summary['evidence_request_count'] == 2


def test_summarize_case_agent_process_assignable_surface_sparse_summary() -> None:
    summary = summarize_case_agent_process({
        'assignable_target_surface': {'count': 3, 'sample_refs': ['BE1', 'BE2', 'BE14'], 'is_sparse': True, 'missing_ref_gaps': ['BE2..BE14'], 'rule': 'only explicitly visible/seen/detailed/assignable refs are assignable; BE refs are opaque identifiers, not numeric episode sequences'},
        'judge_round_actions': ['submit_verdict'],
        'judge_round_kinds': ['initial'],
        'evidence_batches': [],
        'verifier_issues': [],
        'salience_risk_flags': {},
        'error_kind': '',
    })
    assert summary['assignable_target_surface']['is_sparse'] is True


def test_summarize_case_agent_process_policy_decision_rows_have_actual_action() -> None:
    summary = summarize_case_agent_process({
        'judge_round_actions': [''],
        'judge_round_kinds': ['policy_retry'],
        'case_judge_request_audits': [
            {'round_kind': 'policy_retry', 'action_actual': 'fail_closed', 'evidence_request_count_actual': 0, 'evidence_request_types_actual': [], 'fail_closed_reason_kinds': ['insufficient_evidence'], 'policy_retry_request_choices': ['local_file_detail', 'target_detail', 'target_window']},
        ],
        'evidence_batches': [],
        'verifier_issues': [],
        'salience_risk_flags': {},
        'error_kind': '',
    })

    assert summary['judge_round_actions'] == ['fail_closed']


def test_summarize_case_agent_process_exposes_guard_decision_fields() -> None:
    summary = summarize_case_agent_process({
        'judge_round_actions': ['request_evidence'],
        'judge_round_kinds': ['policy_retry'],
        'premature_guard_decisions': [
            {'triggered': True, 'allowed': False, 'reason': 'anchors_available_but_no_request', 'round_kind': 'policy_retry', 'budget_available': True, 'request_types_available': ['target_detail'], 'legal_anchor_available': True, 'anchor_count': 1, 'anchor_samples': ['BE1'], 'judge_no_request_reason': 'detail_sparse', 'fail_closed_reason_kinds': ['insufficient_evidence']},
        ],
        'case_judge_request_audits': [],
        'evidence_batches': [],
        'verifier_issues': [],
        'salience_risk_flags': {},
        'error_kind': '',
    })

    assert summary['judge_round_actions'] == ['request_evidence']


def test_summarize_case_agent_process_tracks_output_budget_fields() -> None:
    summary = summarize_case_agent_process({
        'judge_round_actions': ['fail_closed'],
        'judge_round_kinds': ['issue_response'],
        'evidence_batches': [],
        'verifier_issues': [],
        'salience_risk_flags': {},
        'error_kind': '',
        'output_bytes_estimate': 123,
        'output_ref_total_count': 12,
        'output_ref_list_max_length': 4,
        'oversized_output': True,
    })
    assert summary['error_kind'] == ''


def test_summarize_case_agent_snapshot_refs_compacts_full_lists() -> None:
    summary = summarize_case_agent_snapshot_refs({
        'contract_main_file_refs': [f'LF{i}' for i in range(1, 109)],
        'final_output_main_file_refs': [f'LF{i}' for i in range(1, 109)],
        'visible_target_refs': [f'BE{i}' for i in range(1, 9)],
        'query_card_sample': [
            {'ref': 'SQ4', 'query_text': 'q', 'source_refs': [f'F{i}' for i in range(1, 479)]},
        ],
    })

    assert summary['contract_main_file_count'] == 108
    assert summary['final_output_main_file_count'] == 108
    assert summary['contract_main_file_samples'] == ['LF1', 'LF2', 'LF3', 'LF4', 'LF5', 'LF6', 'LF7', 'LF8']
    assert summary['final_output_main_file_samples'] == ['LF1', 'LF2', 'LF3', 'LF4', 'LF5', 'LF6', 'LF7', 'LF8']
    assert summary['visible_target_count'] == 8
    assert summary['visible_target_range'] == 'BE1..BE8'
    assert summary['visible_target_samples'] == ['BE1', 'BE2', 'BE3', 'BE4', 'BE5', 'BE6', 'BE7', 'BE8']
    assert summary['query_card_sample'][0]['source_ref_count'] == 478
    assert summary['query_card_sample'][0]['source_ref_samples'] == ['F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F7', 'F8']
    assert 'source_refs' not in summary['query_card_sample'][0]


def test_summarize_case_agent_snapshot_refs_falls_back_to_bounded_counts_when_refs_are_compacted() -> None:
    summary = summarize_case_agent_snapshot_refs({
        'status': 'accepted',
        'assignment_intent_count': 13,
        'contract_main_file_count': 0,
        'final_output_main_file_count': 0,
        'bounded_payload_counts': {'main_file_count': 13},
        'contract_main_file_refs': [],
        'final_output_main_file_refs': [],
        'visible_target_refs': ['BE1', 'BE2'],
    })

    assert summary['contract_main_file_count'] == 13
    assert summary['final_output_main_file_count'] == 13
    assert summary['contract_main_file_samples'] == []
    assert summary['final_output_main_file_samples'] == []


def test_summarize_case_agent_snapshot_refs_reports_zero_main_counts_explicitly_when_real_zero() -> None:
    summary = summarize_case_agent_snapshot_refs({
        'status': 'accepted',
        'assignment_intent_count': 0,
        'contract_main_file_count': 0,
        'final_output_main_file_count': 0,
        'bounded_payload_counts': {'main_file_count': 0},
        'contract_main_file_refs': [],
        'final_output_main_file_refs': [],
    })

    assert summary['contract_main_file_count'] == 0
    assert summary['final_output_main_file_count'] == 0


def test_classify_case_agent_snapshot_context_overflow() -> None:
    assert classify_case_agent_snapshot({'status': 'error', 'error_kind': 'context_overflow'}) == 'local_bangumi_mapping_context_overflow'
