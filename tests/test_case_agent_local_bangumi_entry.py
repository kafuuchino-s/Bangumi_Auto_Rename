from __future__ import annotations

from types import SimpleNamespace

from src.rename.case_agent.local_bangumi_entry import _build_workspace, run_local_bangumi_case_agent_mapping


class _File:
    def __init__(self, file_id: str, name: str, relative_path: str, is_main_video_candidate: bool = True, is_video: bool = True, suffix: str = '.mkv'):
        self.file_id = file_id
        self.name = name
        self.relative_path = relative_path
        self.is_main_video_candidate = is_main_video_candidate
        self.is_video = is_video
        self.suffix = suffix


def _local_evidence():
    return SimpleNamespace(source_path='tests/sample', files=[_File('f1', 'ep1.mkv', 'ep1.mkv'), _File('f2', 'ep2.mkv', 'ep2.mkv')])


def _bangumi_contexts():
    return [{
        'context': {
            'episode_structure': {
                'title': 'Test Subject',
                'name': 'Test Subject',
                'name_cn': '娴嬭瘯鏉＄洰',
                'source_role': 'source_cue',
                'episodes': [
                    {'title': 'Episode 1', 'sort': 1, 'ep': 1, 'kind': 'regular'},
                    {'title': 'Episode 2', 'sort': 2, 'ep': 2, 'kind': 'regular'},
                ],
            },
        },
    }]


def _visible_refs():
    return SimpleNamespace(
        local_file_refs=[],
        local_cluster_refs=[],
        bangumi_subject_refs=[],
        bangumi_relation_refs=[],
        bangumi_group_refs=[],
        bangumi_item_refs=[],
        query_refs=[],
        target_refs=[],
    )


def test_local_bangumi_workspace_extracts_visible_cards():
    workspace = _build_workspace(local_evidence=_local_evidence(), bangumi_contexts=_bangumi_contexts())

    assert len(workspace.bangumi_subjects) >= 1
    assert len(workspace.bangumi_groups) >= 1
    assert len(workspace.bangumi_items) >= 1
    assert workspace.contract.visible_target_refs == [card.ref for card in workspace.bangumi_items]
    assert all(ref.startswith('BE') for ref in workspace.contract.visible_target_refs)

    query_cards = [card for card in workspace.query_cards if card.ref.startswith('SQ')]
    assert query_cards
    assert all(card.query_text.strip() for card in query_cards)
    assert all(card.result_refs == [] for card in query_cards)


def test_local_bangumi_workspace_filters_flagged_video_from_main_contract():
    local_evidence = SimpleNamespace(source_path='tests/sample', files=[
        _File('f1', 'ep1.mkv', 'ep1.mkv'),
        _File('f2', 'NCOP.mkv', 'SPs/NCOP.mkv', is_main_video_candidate=False, is_video=True),
    ])

    workspace = _build_workspace(local_evidence=local_evidence, bangumi_contexts=[])

    assert workspace.contract.main_file_refs == ['LF1']
    assert workspace.contract.supplemental_file_refs == []
    assert [card.path for card in workspace.local_files if card.is_main] == ['ep1.mkv']
    assert all('NCOP' not in card.path for card in workspace.local_files)
    assert workspace.judge_request_audits[-1]['note'] == 'deterministic_local_supplemental_projection'
    assert workspace.judge_request_audits[-1]['filtered_video_count'] == 1


def test_local_bangumi_workspace_does_not_filter_special_like_main_videos():
    local_evidence = SimpleNamespace(source_path='tests/sample', files=[
        _File('f1', 'show #00.mkv', 'show #00.mkv'),
        _File('f2', 'show #12DC.mkv', 'show #12DC.mkv'),
        _File('f3', 'show OVA.mkv', 'show OVA.mkv'),
        _File('f4', 'show SP.mkv', 'show SP.mkv'),
    ])

    workspace = _build_workspace(local_evidence=local_evidence, bangumi_contexts=[])

    assert workspace.contract.main_file_refs == ['LF1', 'LF2', 'LF3', 'LF4']
    assert [card.path for card in workspace.local_files] == ['show #00.mkv', 'show #12DC.mkv', 'show OVA.mkv', 'show SP.mkv']


def test_local_bangumi_workspace_enforces_investigation_batch_floor(monkeypatch):
    monkeypatch.setattr(
        'src.rename.case_agent.local_bangumi_entry.cm.get_config',
        lambda key: 2 if key == 'rename_local_bangumi_case_agent_max_evidence_batches' else None,
    )

    workspace = _build_workspace(local_evidence=_local_evidence(), bangumi_contexts=[])

    assert workspace.budget.max_evidence_batches == 12


def test_local_bangumi_workspace_enforces_round_safety_cap_floor(monkeypatch):
    monkeypatch.setattr(
        'src.rename.case_agent.local_bangumi_entry.cm.get_config',
        lambda key: 3 if key == 'rename_local_bangumi_case_agent_max_rounds' else None,
    )

    workspace = _build_workspace(local_evidence=_local_evidence(), bangumi_contexts=[])

    assert workspace.header.max_rounds == 6
    assert workspace.budget.max_judge_rounds == 6


def test_local_bangumi_entry_accepted(monkeypatch):
    seen = {}

    def fake_run(workspace, ai_client, bangumi_client, max_rounds=None, **kwargs):
        seen['bangumi_client'] = bangumi_client
        return SimpleNamespace(
            ok=True,
            status='accepted',
            case_id=workspace.header.case_id,
            summary='accepted',
            final_action='submit_verdict',
            errors=[],
            evidence_batches=[SimpleNamespace(batch_ref='EB1')],
            judge_outputs=[SimpleNamespace(action='submit_verdict')],
            final_workspace=workspace,
        )

    monkeypatch.setattr('src.rename.case_agent.local_bangumi_entry.run_local_bangumi_case_agent', fake_run)
    monkeypatch.setattr('src.rename.case_agent.local_bangumi_entry.BangumiClient', lambda: object())
    monkeypatch.setattr('src.rename.case_agent.local_bangumi_entry.cm.get_config', lambda key: False)

    result = run_local_bangumi_case_agent_mapping(local_evidence=_local_evidence(), bangumi_contexts=[], ai_client=object(), source_path='tests/sample')

    assert result['ok'] is True
    assert result['status'] == 'accepted'
    assert result['snapshot']['case_id']
    assert result['snapshot']['evidence_batch_count'] == 1
    assert result['snapshot']['verifier_issue_count'] == 0
    assert 'result' in result and isinstance(result['result'], dict)
    assert 'assignment_intent_count' in result['snapshot']
    assert result['snapshot']['final_output_assignment_count'] == 0
    assert result['snapshot']['contract_main_file_count'] >= 0
    assert 'contract_main_file_refs' not in result['snapshot']
    assert 'final_output_main_file_refs' not in result['snapshot']
    assert 'contract_main_file_samples' in result['snapshot']
    assert 'final_output_main_file_samples' in result['snapshot']
    assert 'visible_target_count' in result['snapshot']
    assert 'visible_target_range' in result['snapshot']
    assert 'visible_target_samples' in result['snapshot']
    assert result['snapshot']['final_verifier_passed'] is True or result['snapshot']['final_verifier_passed'] is False
    assert 'visible_target_count' in result['snapshot']
    assert 'duplicate_visible_target_refs' in result['snapshot']
    assert 'visible_target_sample' in result['snapshot']
    assert 'main_file_sample' in result['snapshot']
    assert 'judge_round_actions' in result['snapshot']
    assert 'case_agent_status' in result['snapshot']
    assert 'case_agent_ok' in result['snapshot']
    assert 'product_result_kind' in result['snapshot']
    assert 'case_agent_error_kind' in result['snapshot']
    assert 'query_card_sample' in result['snapshot']
    assert all('source_refs' not in card for card in result['snapshot']['query_card_sample'])
    assert all('source_ref_count' in card for card in result['snapshot']['query_card_sample'])
    assert all('source_ref_samples' in card for card in result['snapshot']['query_card_sample'])
    assert 'contract_main_file_refs' not in result['snapshot']
    assert 'final_output_main_file_refs' not in result['snapshot']
    assert 'visible_target_refs' not in result['snapshot']
    assert result['snapshot']['bounded_prompt_enabled'] is True
    assert 'bounded_payload_counts' in result['snapshot']
    assert 'detailed_visible_card_count' in result['snapshot']
    assert 'evidence_request_count' in result['snapshot']
    assert 'evidence_request_types' in result['snapshot']
    assert 'evidence_response_ref_count' in result['snapshot']
    assert 'evidence_menu_request_count' in result['snapshot']
    assert 'evidence_menu_span_request_count' in result['snapshot']
    assert 'selected_menu_request_ids' in result['snapshot']
    assert 'plan_status' in result['snapshot']
    assert 'plan_completed_count' in result['snapshot']
    assert 'plan_failed_count' in result['snapshot']
    assert 'plan_selected_count' in result['snapshot']
    assert 'unknown_menu_request_ids' in result['snapshot']
    assert 'resolved_menu_request_count' in result['snapshot']
    assert 'legacy_raw_request_count' in result['snapshot']
    assert 'normalized_legacy_request_count' in result['snapshot']
    assert 'judge_round_kinds' in result['snapshot']
    assert 'salience_risk_flags' in result['snapshot']
    assert 'salience_large_case' in result['snapshot']
    assert 'initial_projection_bytes' in result['snapshot']
    assert 'request_body_bytes_estimate' in result['snapshot']
    assert 'initial_be_ref_occurrences' in result['snapshot']
    assert 'initial_file_ref_occurrences' in result['snapshot']
    assert 'case_judge_configured_interface' in result['snapshot']
    assert 'case_judge_actual_interface' in result['snapshot']
    assert 'case_judge_streaming' in result['snapshot']
    assert 'case_judge_request_audits' in result['snapshot']
    assert 'case_judge_request_audit_count' in result['snapshot']
    assert result['snapshot']['case_judge_configured_interface'] in {'responses_api', 'chat_completions', 'unknown'}
    assert 'seen_detail_ref_source' in result['snapshot']
    assert 'requested_detail_ref_count' in result['snapshot']
    assert 'requested_detailed_card_count' in result['snapshot']
    assert 'local_span_count' in result['snapshot']
    assert 'local_child_span_count' in result['snapshot']
    assert 'local_span_covered_main_count' in result['snapshot']
    assert 'local_span_missing_main_count' in result['snapshot']
    assert 'local_span_overlap_count' in result['snapshot']
    assert 'local_span_partition_complete' in result['snapshot']
    assert result['snapshot']['mapping_draft_row_count'] == result['snapshot']['mapping_draft_local_coverage_count'] or result['snapshot']['mapping_draft_row_count'] >= 0
    assert 'bangumi_span_count' in result['snapshot']
    assert 'detail_equivalent_target_span_count' in result['snapshot']
    assert 'span_alignment_claim_count' in result['snapshot']
    assert 'bulk_assignment_intent_count' in result['snapshot']
    assert 'expanded_assignment_count' in result['snapshot']
    assert 'mapping_draft_local_coverage_count' in result['snapshot']
    assert 'mapping_draft_missing_main_count' in result['snapshot']
    assert 'span_rows_with_candidates' in result['snapshot']
    assert 'span_rows_without_candidates' in result['snapshot']
    assert 'planned_span_request_count' in result['snapshot']
    assert 'selected_span_request_count' in result['snapshot']
    assert 'completed_span_request_count' in result['snapshot']
    assert 'recommended_target_span_request_count' in result['snapshot']
    assert 'actual_target_span_request_count' in result['snapshot']
    assert 'accepted_target_span_request_count' in result['snapshot']
    assert result['snapshot']['target_span_request_count'] == result['snapshot']['actual_target_span_request_count']
    assert 'primary_title_cues' in result['snapshot']
    assert 'release_group_cues' in result['snapshot']
    assert result['snapshot']['search_seed_source'] == 'agent_composed_query_cards'
    assert seen['bangumi_client'] is not None


def test_local_bangumi_entry_accepted_keeps_main_file_counts_visible_with_compact_snapshot(monkeypatch):
    def fake_run(workspace, ai_client, bangumi_client, max_rounds=None, **kwargs):
        return SimpleNamespace(
            ok=True,
            status='accepted',
            case_id=workspace.header.case_id,
            summary='accepted',
            final_action='submit_verdict',
            errors=[],
            evidence_batches=[],
            judge_outputs=[SimpleNamespace(action='submit_verdict', assignment_intents=[object()])],
            final_workspace=workspace,
            final_output=SimpleNamespace(assignment_intents=[object()]),
            final_verifier_result=SimpleNamespace(passed=True, issues=[]),
        )

    monkeypatch.setattr('src.rename.case_agent.local_bangumi_entry.run_local_bangumi_case_agent', fake_run)
    monkeypatch.setattr('src.rename.case_agent.local_bangumi_entry.cm.get_config', lambda key: False)

    result = run_local_bangumi_case_agent_mapping(local_evidence=_local_evidence(), bangumi_contexts=[], ai_client=object(), source_path='tests/sample')

    assert result['snapshot']['assignment_intent_count'] == 1
    assert result['snapshot']['contract_main_file_count'] == result['snapshot']['bounded_payload_counts']['main_file_count']
    assert result['snapshot']['final_output_main_file_count'] == result['snapshot']['bounded_payload_counts']['main_file_count']
    assert 'contract_main_file_refs' not in result['snapshot']
    assert 'final_output_main_file_refs' not in result['snapshot']


def test_local_bangumi_entry_accounting_snapshot_counts(monkeypatch):
    def fake_run(workspace, ai_client, bangumi_client, max_rounds=None, **kwargs):
        workspace = workspace.with_mapping_draft(None)
        object.__setattr__(workspace, 'visible_refs', _visible_refs())
        return SimpleNamespace(
            ok=True,
            status='fail_closed',
            case_id=workspace.header.case_id,
            summary='fail_closed',
            final_action='fail_closed',
            errors=[],
            evidence_batches=[],
            judge_outputs=[],
            final_workspace=workspace,
        )

    monkeypatch.setattr('src.rename.case_agent.local_bangumi_entry.run_local_bangumi_case_agent', fake_run)
    monkeypatch.setattr('src.rename.case_agent.local_bangumi_entry.cm.get_config', lambda key: False)

    result = run_local_bangumi_case_agent_mapping(local_evidence=_local_evidence(), bangumi_contexts=[], ai_client=object(), source_path='tests/sample')

    assert 'main_file_count' in result['snapshot']
    assert 'accounted_for_count' in result['snapshot']
    assert 'unresolved_count' in result['snapshot']
    assert 'contract_main_file_refs' not in result['snapshot']


def test_local_bangumi_entry_snapshot_filters_non_judge_audits(monkeypatch):
    def fake_run(workspace, ai_client, bangumi_client, max_rounds=None, **kwargs):
        return SimpleNamespace(
            ok=True,
            status='accepted',
            case_id=workspace.header.case_id,
            summary='accepted',
            final_action='submit_verdict',
            errors=[],
            evidence_batches=[],
            judge_outputs=[SimpleNamespace(action='submit_verdict', assignment_intents=[object()])],
            final_workspace=SimpleNamespace(
                header=workspace.header,
                budget=workspace.budget,
                local_files=workspace.local_files,
                local_clusters=[],
                bangumi_subjects=workspace.bangumi_subjects,
                bangumi_relations=[],
                bangumi_groups=workspace.bangumi_groups,
                bangumi_items=workspace.bangumi_items,
                contract=workspace.contract,
                query_cards=workspace.query_cards,
                visible_refs=_visible_refs,
                previous_hypotheses=[],
                previous_evidence_results=[],
                provenance_cards=[],
                seen_detail_refs=[],
                verifier_issues=[],
                diagnostics=[],
                judge_request_audits=[
                    {'round_kind': 'initial', 'action_expected': 'request_evidence', 'action': 'request_evidence', 'call_name': 'call_case_judge'},
                    {'round_kind': 'policy_retry', 'action_expected': 'submit_verdict_or_fail_closed_only', 'action': 'submit_verdict', 'call_name': 'call_case_judge'},
                    {'round_kind': 'policy_check', 'action_expected': 'policy_check', 'call_name': 'policy_check'},
                ],
            ),
            final_output=SimpleNamespace(assignment_intents=[object()]),
            final_verifier_result=SimpleNamespace(passed=True, issues=[]),
        )

    monkeypatch.setattr('src.rename.case_agent.local_bangumi_entry.run_local_bangumi_case_agent', fake_run)
    monkeypatch.setattr('src.rename.case_agent.local_bangumi_entry.cm.get_config', lambda key: False)

    result = run_local_bangumi_case_agent_mapping(local_evidence=_local_evidence(), bangumi_contexts=[], ai_client=object(), source_path='tests/sample')

    assert result['snapshot']['judge_round_kinds'] == ['initial', 'policy_retry']
    assert result['snapshot']['judge_round_actions'] == ['request_evidence', 'submit_verdict']


def test_no_zero_top_level_when_nested_draft_exists(monkeypatch):
    def fake_run(workspace, ai_client, bangumi_client, max_rounds=None, **kwargs):
        draft = workspace.mapping_draft
        return SimpleNamespace(
            ok=True,
            status='accepted',
            case_id=workspace.header.case_id,
            summary='accepted',
            final_action='submit_verdict',
            errors=[],
            evidence_batches=[],
            judge_outputs=[SimpleNamespace(action='submit_verdict', assignment_intents=[object()])],
            final_workspace=workspace.with_mapping_draft(draft),
            final_output=SimpleNamespace(assignment_intents=[object()]),
            final_verifier_result=SimpleNamespace(passed=True, issues=[]),
        )

    monkeypatch.setattr('src.rename.case_agent.local_bangumi_entry.run_local_bangumi_case_agent', fake_run)
    monkeypatch.setattr('src.rename.case_agent.local_bangumi_entry.cm.get_config', lambda key: False)

    result = run_local_bangumi_case_agent_mapping(local_evidence=_local_evidence(), bangumi_contexts=[], ai_client=object(), source_path='tests/sample')
    assert result['snapshot']['mapping_draft_row_count'] >= result['snapshot']['mapping_draft_local_coverage_count']


def test_local_bangumi_entry_accepted_keeps_main_file_counts_visible_with_compact_snapshot(monkeypatch):
    def fake_run(workspace, ai_client, bangumi_client, max_rounds=None, **kwargs):
        return SimpleNamespace(
            ok=True,
            status='accepted',
            case_id=workspace.header.case_id,
            summary='accepted',
            final_action='submit_verdict',
            errors=[],
            evidence_batches=[],
            judge_outputs=[SimpleNamespace(action='submit_verdict', assignment_intents=[object()])],
            final_workspace=workspace,
            final_output=SimpleNamespace(assignment_intents=[object()]),
            final_verifier_result=SimpleNamespace(passed=True, issues=[]),
        )

    monkeypatch.setattr('src.rename.case_agent.local_bangumi_entry.run_local_bangumi_case_agent', fake_run)
    monkeypatch.setattr('src.rename.case_agent.local_bangumi_entry.cm.get_config', lambda key: False)

    result = run_local_bangumi_case_agent_mapping(local_evidence=_local_evidence(), bangumi_contexts=[], ai_client=object(), source_path='tests/sample')

    assert result['snapshot']['assignment_intent_count'] == 1
    assert result['snapshot']['contract_main_file_count'] == result['snapshot']['bounded_payload_counts']['main_file_count']
    assert result['snapshot']['final_output_main_file_count'] == result['snapshot']['bounded_payload_counts']['main_file_count']
    assert 'contract_main_file_refs' not in result['snapshot']
    assert 'final_output_main_file_refs' not in result['snapshot']


def test_local_bangumi_entry_exports_all_case_judge_audits(monkeypatch):
    from src.rename.case_agent.models import AssignmentIntent, CaseJudgeOutput, EvidenceRequest, Finding
    def fake_run(workspace, ai_client, bangumi_client, max_rounds=None, **kwargs):
        object.__setattr__(workspace, 'judge_request_audits', [
            {'round_kind': 'initial', 'input_projection_bytes': 1, 'output_bytes_estimate': 2, 'cache_mode': 'planned', 'configured_interface': 'responses_api', 'actual_interface': 'responses_api', 'streaming': False, 'elapsed_ms': 1},
            {'round_kind': 'evidence_rejudge', 'input_projection_bytes': 1, 'output_bytes_estimate': 2, 'cache_mode': 'planned', 'configured_interface': 'responses_api', 'actual_interface': 'responses_api', 'streaming': False, 'elapsed_ms': 1},
            {'round_kind': 'issue_response', 'input_projection_bytes': 1, 'output_bytes_estimate': 2, 'cache_mode': 'planned', 'configured_interface': 'responses_api', 'actual_interface': 'responses_api', 'streaming': False, 'elapsed_ms': 1},
        ])
        return SimpleNamespace(ok=True, status='accepted', case_id=workspace.header.case_id, summary='accepted', final_action='submit_verdict', errors=[], evidence_batches=[], judge_outputs=[CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='target_detail', item_refs=['BE1'])]), CaseJudgeOutput(action='submit_verdict', findings=[Finding(ref='F2', finding_kind='pass', description='ok')], assignment_intents=[AssignmentIntent(ref='A2', file_ref='LF1', target_ref='BE1', support_finding_refs=['F2'], support_card_refs=['LF1', 'BE1'], reason='r2')]), CaseJudgeOutput(action='issue_response', issue_responses=[{'ref': 'IR1', 'issue_kind': 'clarify_scope', 'message': 'fixed', 'related_refs': []}], findings=[Finding(ref='F3', finding_kind='pass', description='ok')], assignment_intents=[AssignmentIntent(ref='A3', file_ref='LF1', target_ref='BE1', support_finding_refs=['F3'], support_card_refs=['LF1', 'BE1'], reason='r3')])], final_workspace=SimpleNamespace(header=workspace.header, budget=workspace.budget, local_files=workspace.local_files, local_clusters=[], bangumi_subjects=workspace.bangumi_subjects, bangumi_relations=[], bangumi_groups=workspace.bangumi_groups, bangumi_items=workspace.bangumi_items, contract=workspace.contract, query_cards=workspace.query_cards, visible_refs=_visible_refs, previous_hypotheses=[], previous_evidence_results=[], provenance_cards=[], seen_detail_refs=[], verifier_issues=[], diagnostics=[], judge_request_audits=workspace.judge_request_audits))

    monkeypatch.setattr('src.rename.case_agent.local_bangumi_entry.run_local_bangumi_case_agent', fake_run)
    monkeypatch.setattr('src.rename.case_agent.local_bangumi_entry.cm.get_config', lambda key: False)

    result = run_local_bangumi_case_agent_mapping(local_evidence=_local_evidence(), bangumi_contexts=[], ai_client=object(), source_path='tests/sample')

    assert result['snapshot']['case_judge_request_audit_count'] == 3
    assert result['snapshot']['case_judge_request_audit_round_kinds'] == ['initial', 'evidence_rejudge', 'issue_response']


def test_local_bangumi_entry_marks_local_package_analysis_skipped(monkeypatch):
    def fake_run(workspace, ai_client, bangumi_client, max_rounds=None, **kwargs):
        return SimpleNamespace(
            ok=True,
            status='accepted',
            case_id=workspace.header.case_id,
            summary='accepted',
            final_action='submit_verdict',
            errors=[],
            evidence_batches=[],
            judge_outputs=[],
            final_workspace=workspace,
        )

    monkeypatch.setattr('src.rename.case_agent.local_bangumi_entry.run_local_bangumi_case_agent', fake_run)
    monkeypatch.setattr('src.rename.case_agent.local_bangumi_entry.cm.get_config', lambda key: False)

    ai_client = SimpleNamespace(
        analyze_local_package=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('LPA should not run')),
        get_last_ai_call_audit=lambda: {'call_name': 'LocalPackageAnalysis', 'unexpected': True},
    )

    result = run_local_bangumi_case_agent_mapping(local_evidence=_local_evidence(), bangumi_contexts=[], ai_client=ai_client, source_path='tests/sample')

    audit = result['snapshot']['local_package_analysis_audit']
    assert audit['call_name'] == 'LocalPackageAnalysis'
    assert audit['skipped'] is True
    assert audit['reason'] == 'query_composer_orchestrated_main_path'


def test_local_bangumi_entry_fail_closed(monkeypatch):
    def fake_run(workspace, ai_client, bangumi_client, max_rounds=None, **kwargs):
        return SimpleNamespace(
            ok=True,
            status='fail_closed',
            case_id=workspace.header.case_id,
            summary='fail closed',
            final_action='fail_closed',
            errors=[],
            evidence_batches=[],
            judge_outputs=[SimpleNamespace(action='fail_closed')],
            final_workspace=workspace,
        )

    monkeypatch.setattr('src.rename.case_agent.local_bangumi_entry.run_local_bangumi_case_agent', fake_run)
    monkeypatch.setattr('src.rename.case_agent.local_bangumi_entry.cm.get_config', lambda key: False)

    result = run_local_bangumi_case_agent_mapping(local_evidence=_local_evidence(), bangumi_contexts=[], ai_client=object(), source_path='tests/sample')

    assert result['status'] == 'fail_closed'
    assert result['snapshot']['summary'] == 'fail closed'


def test_local_bangumi_entry_uses_injected_bangumi_client(monkeypatch):
    injected = object()
    seen = {}

    def fake_run(workspace, ai_client, bangumi_client, max_rounds=None, **kwargs):
        seen['bangumi_client'] = bangumi_client
        return SimpleNamespace(
            ok=True,
            status='accepted',
            case_id=workspace.header.case_id,
            summary='accepted',
            final_action='submit_verdict',
            errors=[],
            evidence_batches=[],
            judge_outputs=[],
            final_workspace=workspace,
        )

    monkeypatch.setattr('src.rename.case_agent.local_bangumi_entry.run_local_bangumi_case_agent', fake_run)
    monkeypatch.setattr('src.rename.case_agent.local_bangumi_entry.cm.get_config', lambda key: False)

    run_local_bangumi_case_agent_mapping(local_evidence=_local_evidence(), bangumi_contexts=[], ai_client=object(), source_path='tests/sample', bangumi_client=injected)

    assert seen['bangumi_client'] is injected


def test_local_bangumi_entry_error(monkeypatch):
    def fake_run(workspace, ai_client, bangumi_client, max_rounds=None, **kwargs):
        return SimpleNamespace(
            ok=False,
            status='error',
            case_id=workspace.header.case_id,
            summary='error',
            final_action='',
            errors=['boom'],
            evidence_batches=[],
            judge_outputs=[],
            final_workspace=workspace,
        )

    monkeypatch.setattr('src.rename.case_agent.local_bangumi_entry.run_local_bangumi_case_agent', fake_run)
    monkeypatch.setattr('src.rename.case_agent.local_bangumi_entry.cm.get_config', lambda key: False)

    result = run_local_bangumi_case_agent_mapping(local_evidence=_local_evidence(), bangumi_contexts=[], ai_client=object(), source_path='tests/sample')

    assert result['ok'] is False
    assert result['status'] == 'error'
    assert result['snapshot']['errors'] == ['boom']
