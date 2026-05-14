from __future__ import annotations

import json

from src.rename.case_agent.models import (
    AssignmentIntent,
    BangumiItemCard,
    BangumiSpanCard,
    BangumiSubjectCard,
    CaseBudget,
    CaseContract,
    CaseJudgeOutput,
    Finding,
    CaseHeader,
    LocalFileCard,
    LocalSpanCard,
    MappingIntent,
    MappingDraft,
    MappingDraftRow,
    NotebookUpdate,
    QueryCandidate,
)
from src.rename.case_agent.orchestrator import run_local_bangumi_case_agent, _compact_final_assignment_support_refs, _run_orchestrator_materialize_queries_tool, _run_orchestrator_propose_mapping_intents_tool
from src.rename.case_agent.orchestrator import _refresh_mapping_draft_candidates
from src.rename.case_agent.orchestrator_agent import (
    MaterializeQueriesToolArgs,
    OrchestratorAgentSession,
    ProposeMappingIntentsToolArgs,
    build_orchestrator_agent_input,
    call_orchestrator_agent,
    orchestrator_session_audit,
    orchestrator_tool_definitions,
    record_orchestrator_tool_output,
)
from src.rename.case_agent.verifier import verify_judge_output
from src.rename.case_agent.workspace import CaseEvidenceWorkspace


class _ToolAgentClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def call_responses_tool_agent(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class _RunClient(_ToolAgentClient):
    def __init__(self, responses):
        super().__init__(responses)
        self.mapping_editor_calls = 0
        self.case_judge_calls = 0

    def call_mapping_draft_editor(self, prompt, schema):
        self.mapping_editor_calls += 1
        raise AssertionError('legacy mapping draft editor should not be called by OrchestratorAgent main path')

    def call_case_judge(self, prompt, schema):
        self.case_judge_calls += 1
        raise AssertionError('legacy case judge loop should not be called by OrchestratorAgent main path')


def _workspace() -> CaseEvidenceWorkspace:
    return CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-ORCH'),
        budget=CaseBudget(max_evidence_batches=3, max_requests_per_batch=2),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1']),
        local_files=[LocalFileCard(ref='LF1', path='pkg/Title 01.mkv', is_main=True, label='Title 01.mkv')],
    )


def _mapping_workspace() -> CaseEvidenceWorkspace:
    return CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-MAP'),
        budget=CaseBudget(max_judge_rounds=4, max_evidence_batches=2, max_requests_per_batch=2),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']),
        local_files=[LocalFileCard(ref='LF1', path='Title 01.mkv', is_main=True, label='Title 01.mkv')],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='token_segment', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1'], episode_token_start=1, episode_token_end=1, episode_token_count=1)],
        bangumi_items=[BangumiItemCard(ref='BE1', subject_ref='BS1', sort=1, ep=1, item_kind='episode')],
    )


def _regular_span_workspace() -> CaseEvidenceWorkspace:
    file_refs = ['LF1', 'LF2']
    return CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-SPAN'),
        budget=CaseBudget(max_judge_rounds=4, max_evidence_batches=2, max_requests_per_batch=2),
        contract=CaseContract(main_file_refs=file_refs, allowed_file_refs=file_refs, visible_target_refs=['BE1', 'BE2']),
        local_files=[
            LocalFileCard(ref='LF1', path='Title 01.mkv', is_main=True, label='Title 01.mkv'),
            LocalFileCard(ref='LF2', path='Title 02.mkv', is_main=True, label='Title 02.mkv'),
        ],
        local_span_cards=[
            LocalSpanCard(
                ref='LS1',
                span_scope='token_segment',
                file_refs=file_refs,
                file_ref_count=2,
                file_ref_samples=file_refs,
                episode_token_start=1,
                episode_token_end=2,
                episode_token_count=2,
            )
        ],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', title='Title')],
        bangumi_items=[
            BangumiItemCard(ref='BE1', subject_ref='BS1', sort=1, ep=1, item_kind='episode'),
            BangumiItemCard(ref='BE2', subject_ref='BS1', sort=2, ep=2, item_kind='episode'),
        ],
    )


def _session_with_function_call(session: OrchestratorAgentSession, call_id: str, name: str) -> OrchestratorAgentSession:
    return OrchestratorAgentSession(
        case_id=session.case_id,
        turn_count=session.turn_count,
        compact_count=session.compact_count,
        context_soft_limit_hit_count=session.context_soft_limit_hit_count,
        context_hard_limit_hit_count=session.context_hard_limit_hit_count,
        tool_rejection_count=session.tool_rejection_count,
        input_token_estimate=session.input_token_estimate,
        output_token_estimate=session.output_token_estimate,
        tool_sequence=list(session.tool_sequence),
        compacted_history_summary=session.compacted_history_summary,
        history_items=[
            *session.history_items,
            {'type': 'function_call', 'call_id': call_id, 'name': name, 'arguments': '{}'},
        ],
    )


def test_target_absent_row_reopens_when_later_special_targets_become_visible():
    file_refs = [f'LF{i}' for i in range(1, 7)]
    target_refs = [f'BE{i}' for i in range(1, 7)]
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-TARGET-ABSENT-REOPEN'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=file_refs, allowed_file_refs=file_refs, visible_target_refs=target_refs),
        local_files=[
            LocalFileCard(ref=ref, path=f'Title SP{i:02d}.mkv', is_main=True, file_kind='video')
            for i, ref in enumerate(file_refs, start=1)
        ],
        local_span_cards=[
            LocalSpanCard(
                ref='LS_SP',
                span_scope='token_segment',
                file_refs=file_refs,
                file_ref_count=len(file_refs),
                file_ref_samples=file_refs[:3],
                episode_token_start=1,
                episode_token_end=6,
                episode_token_count=6,
                title_cues=['SP'],
            )
        ],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', title='Title')],
        bangumi_items=[
            BangumiItemCard(ref=ref, subject_ref='BS1', item_kind='special', sort=i)
            for i, ref in enumerate(target_refs)
        ],
        mapping_draft=MappingDraft(rows=[
            MappingDraftRow(
                row_ref='MDR1',
                local_ref='LS_SP',
                local_ref_kind='span',
                disposition='non_bangumi_or_supplemental',
                reason_kind='bangumi_target_absent',
                support_refs=['LS_SP'],
            )
        ], version=1),
    )

    updated = _refresh_mapping_draft_candidates(workspace)

    row = updated.mapping_draft.rows[0]
    assert row.disposition == 'open'
    assert row.status == 'open'
    assert row.reason_kind == ''
    assert row.candidate_target_refs == target_refs


def test_orchestrator_agent_uses_native_tool_call_with_local_history():
    client = _ToolAgentClient([
        {
            'id': 'resp_1',
            'tool_calls': [
                    {
                        'call_id': 'call_1',
                        'name': 'materialize_queries',
                        'arguments': json.dumps({'reason': 'need subject', 'query_hints': ['Title']}),
                    }
            ],
            'usage': {'input_tokens': 123, 'output_tokens': 12},
        }
    ])

    result = call_orchestrator_agent(client, _workspace(), OrchestratorAgentSession(case_id='CASE-ORCH'))

    assert result.ok is True
    assert result.tool_call is not None
    assert result.tool_call.tool_name == 'materialize_queries'
    assert result.session.turn_count == 1
    assert 'conversation_id' not in client.calls[0]
    assert client.calls[0]['parallel_tool_calls'] is False
    assert client.calls[0]['tool_choice'] == 'required'
    assert client.calls[0]['instructions']
    assert result.session.history_items[-1]['type'] == 'function_call'
    assert result.session.history_items[-1]['call_id'] == 'call_1'


def test_orchestrator_agent_sends_function_call_output_on_next_turn():
    client = _ToolAgentClient([
        {
            'id': 'resp_2',
            'tool_calls': [
                {
                    'call_id': 'call_2',
                    'name': 'finish_case',
                    'arguments': json.dumps({'status': 'fail_closed', 'reason': 'evidence exhausted'}),
                }
            ],
        }
    ])
    session = OrchestratorAgentSession(case_id='CASE-ORCH')
    session = _session_with_function_call(session, 'call_1', 'materialize_queries')
    session = record_orchestrator_tool_output(session, type('Call', (), {'call_id': 'call_1'})(), {'status': 'ok'})

    result = call_orchestrator_agent(client, _workspace(), session)

    assert result.ok is True
    assert client.calls[0]['input_items'][0]['type'] == 'function_call'
    assert client.calls[0]['input_items'][1]['type'] == 'function_call_output'
    assert client.calls[0]['input_items'][1]['call_id'] == 'call_1'
    assert result.session.history_items[-1]['type'] == 'function_call'
    assert result.tool_call is not None
    assert result.tool_call.tool_name == 'finish_case'


def test_orchestrator_agent_requires_conversation_transport():
    class NoConversationClient:
        pass

    result = call_orchestrator_agent(NoConversationClient(), _workspace(), OrchestratorAgentSession(case_id='CASE-ORCH'))

    assert result.ok is False
    assert result.error == 'orchestrator_agent_transport_unavailable'


def test_orchestrator_agent_compaction_trims_local_history():
    client = _ToolAgentClient([
        {
            'id': 'resp_new',
            'tool_calls': [
                {
                        'call_id': 'call_new',
                        'name': 'materialize_queries',
                        'arguments': json.dumps({'reason': 'new chain'}),
                }
            ],
        }
    ])
    session = OrchestratorAgentSession(
        case_id='CASE-ORCH',
        history_items=[
            {'type': 'function_call', 'call_id': 'old_call', 'name': 'materialize_queries', 'arguments': '{}'},
            {'type': 'function_call_output', 'call_id': 'old_call', 'output': '{"status":"ok"}'},
        ],
        tool_sequence=['materialize_queries'],
    )

    result = call_orchestrator_agent(client, _workspace(), session, soft_token_limit=1, hard_token_limit=2)

    assert result.ok is True
    assert client.calls[0]['input_items'][0]['role'] == 'user'
    assert 'Compacted prior OrchestratorAgent context' in client.calls[0]['input_items'][0]['content']
    assert result.session.compact_count == 1
    assert result.audit['compacted'] is True
    assert result.audit['compact_mode'] == 'local_history_trim_after_context_threshold'


def test_orchestrator_tool_definitions_are_strict_function_tools():
    tools = orchestrator_tool_definitions()

    tool_names = {tool['function']['name'] for tool in tools}
    assert tool_names >= {'materialize_queries', 'execute_evidence', 'propose_mapping_intents', 'finish_case'}
    assert 'apply_draft_patches' not in tool_names
    materialize = next(tool for tool in tools if tool['function']['name'] == 'materialize_queries')
    parameters = materialize['function']['parameters']
    assert parameters['additionalProperties'] is False
    assert 'reason' in parameters['required']


def test_small_case_agent_input_hides_reconsider_split_until_structurally_useful():
    prompt = build_orchestrator_agent_input(_mapping_workspace(), reason='small single work unit')
    payload = json.loads(prompt)

    assert 'reconsider_split' not in payload['available_tool_names']
    assert {'materialize_queries', 'execute_evidence', 'propose_mapping_intents'} <= set(payload['available_tool_names'])


def test_agent_input_exposes_special_span_candidate_for_multi_file_sp_row():
    file_refs = [f'LF{i}' for i in range(1, 7)]
    special_refs = [f'BE{i}' for i in range(1, 7)]
    wrong_refs = [f'BEW{i}' for i in range(1, 6)]
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-SPECIAL-SPAN-CANDIDATE'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=file_refs, allowed_file_refs=file_refs, visible_target_refs=[*special_refs, *wrong_refs]),
        local_files=[
            LocalFileCard(ref=ref, path=f'Title SP{i:02d}.mkv', is_main=True, file_kind='video')
            for i, ref in enumerate(file_refs, start=1)
        ],
        local_span_cards=[
            LocalSpanCard(
                ref='LS_SP',
                span_scope='token_segment',
                file_refs=file_refs,
                file_ref_count=len(file_refs),
                file_ref_samples=file_refs[:3],
                episode_token_start=1,
                episode_token_end=6,
                episode_token_count=6,
                title_cues=['SP'],
            )
        ],
        bangumi_subjects=[BangumiSubjectCard(ref='BS_RIGHT', title='Title')],
        bangumi_items=[
            *[
                BangumiItemCard(ref=ref, subject_ref='BS_WRONG', item_kind='special', sort=i)
                for i, ref in enumerate(wrong_refs, start=1)
            ],
            *[
                BangumiItemCard(ref=ref, subject_ref='BS_RIGHT', item_kind='special', sort=i - 1, title=f'SP {i}')
                for i, ref in enumerate(special_refs, start=1)
            ],
        ],
        bangumi_span_cards=[
            BangumiSpanCard(
                ref='BES_SPECIAL',
                subject_ref='BS_RIGHT',
                target_refs=special_refs,
                target_ref_count=len(special_refs),
                target_ref_samples=special_refs[:3],
                item_kind='special',
                sort_start=0,
                sort_end=5,
                detail_equivalent=True,
            )
        ],
        mapping_draft=MappingDraft(rows=[
            MappingDraftRow(
                row_ref='MDR1',
                local_ref='LS_SP',
                local_ref_kind='span',
                candidate_target_refs=[*wrong_refs, *special_refs, 'BES_SPECIAL'],
                subject_refs=['BS_RIGHT'],
            )
        ]),
    )

    payload = json.loads(build_orchestrator_agent_input(workspace, reason='inspect special span row'))
    row = payload['open_rows_requiring_agent_action'][0]

    assert any(brief.get('ref') == 'BES_SPECIAL' and brief.get('target_kind') == 'BES_span' for brief in row['candidate_target_briefs'])
    assert any(seq.get('sequence_kind') == 'special' and seq.get('matches_local_file_count') for seq in row['visible_subject_item_sequences'])
    assert 'chosen_span_ref' in row['recommended_exit']


def test_needs_more_evidence_is_rejected_when_visible_sequence_can_be_resolved():
    file_refs = [f'LF{i}' for i in range(1, 7)]
    special_refs = [f'BE{i}' for i in range(1, 7)]
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-NON-PROGRESS-NEEDS-EVIDENCE'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=file_refs, allowed_file_refs=file_refs, visible_target_refs=special_refs),
        local_files=[
            LocalFileCard(ref=ref, path=f'Title SP{i:02d}.mkv', is_main=True, file_kind='video')
            for i, ref in enumerate(file_refs, start=1)
        ],
        local_span_cards=[
            LocalSpanCard(
                ref='LS_SP',
                span_scope='token_segment',
                file_refs=file_refs,
                file_ref_count=len(file_refs),
                file_ref_samples=file_refs[:3],
                episode_token_start=1,
                episode_token_end=6,
                episode_token_count=6,
                title_cues=['SP'],
            )
        ],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', title='Title')],
        bangumi_items=[
            BangumiItemCard(ref=ref, subject_ref='BS1', item_kind='special', sort=i - 1, title=f'SP {i}')
            for i, ref in enumerate(special_refs, start=1)
        ],
        mapping_draft=MappingDraft(rows=[
            MappingDraftRow(
                row_ref='MDR1',
                local_ref='LS_SP',
                local_ref_kind='span',
                candidate_target_refs=special_refs,
                subject_refs=['BS1'],
            )
        ]),
    )
    args = ProposeMappingIntentsToolArgs(
        reason='preserve unresolved state',
        mapping_intents=[
            MappingIntent(
                intent_ref='I1',
                decision='needs_more_evidence',
                local_ref='LS_SP',
                row_ref='MDR1',
                support_refs=['LS_SP'],
                reason='candidate comparison remains ambiguous',
            )
        ],
    )

    _updated, observation = _run_orchestrator_propose_mapping_intents_tool(workspace, args)

    assert observation['status'] == 'patch_issues'
    assert 'non_progress_needs_more_evidence_with_visible_candidates' in observation['patch_issue_codes']
    assert observation['draft_accounting']['unresolved_count'] == len(file_refs)


def test_materialize_queries_adds_title_preserving_romanized_variants_and_drops_scope_noise():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-QUERY-VARIANTS'),
        budget=CaseBudget(max_evidence_batches=3),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1']),
        local_files=[LocalFileCard(ref='LF1', path='[Group][YuYuShiki][01].mkv', is_main=True)],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='directory', file_refs=['LF1'], file_ref_count=1)],
    )
    args = MaterializeQueriesToolArgs(
        reason='try clean romanized title forms',
        queries=[
            QueryCandidate(query_text='YuYuShiki', source_refs=['LS1'], reason='clean title cue', confidence='medium'),
            QueryCandidate(query_text='Subete ga F ni Naru', source_refs=['LS1'], reason='title beginning with Sub is not subtitle noise', confidence='medium'),
            QueryCandidate(query_text='main TV series 01-12', source_refs=['LS1'], reason='bad scope-only query', confidence='low'),
        ],
    )

    updated, observation = _run_orchestrator_materialize_queries_tool(workspace, args)

    query_texts = [card.query_text for card in updated.query_cards]
    assert observation['status'] == 'ok'
    assert 'YuYuShiki' in query_texts
    assert 'Yu Yu Shiki' in query_texts
    assert 'Yuyu Shiki' in query_texts
    assert 'Subete ga F ni Naru' in query_texts
    assert 'main TV series 01-12' not in query_texts
    assert any(item.get('reason') == 'metadata_only_query_text' for item in observation['dropped_queries'])


def test_final_mapping_draft_assignment_support_refs_are_compacted_before_verifier():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-SUPPORT-BUDGET'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']),
        local_files=[LocalFileCard(ref='LF1', path='Title 01.mkv', is_main=True)],
        bangumi_items=[BangumiItemCard(ref='BE1', subject_ref='BS1', sort=1, ep=1, item_kind='episode')],
    )
    assignment = AssignmentIntent(
        ref='A1',
        file_ref='LF1',
        target_ref='BE1',
        support_finding_refs=['F_MAP1'],
        support_card_refs=[*[f'LF{i}' for i in range(1, 13)], *[f'BE{i}' for i in range(1, 13)]],
        reason='mapping_draft:MD1',
    )
    compacted = _compact_final_assignment_support_refs([assignment])
    output = CaseJudgeOutput(
        action='submit_verdict',
        findings=[Finding(ref='F_MAP1', finding_kind='pass', description='mapping draft accounting accepted')],
        assignment_intents=compacted,
    )

    verifier = verify_judge_output(workspace.to_dossier(round_context='support_budget_test'), output)

    assert compacted[0].support_card_refs == ['LF1', 'BE1']
    assert verifier.passed is True


def test_orchestrator_session_audit_counts_tool_sequence():
    session = OrchestratorAgentSession(case_id='CASE-ORCH', tool_sequence=['materialize_queries', 'execute_evidence', 'materialize_queries'], compact_count=1)

    audit = orchestrator_session_audit(session)

    assert audit['orchestrator_tool_call_counts'] == {'materialize_queries': 2, 'execute_evidence': 1}
    assert audit['compact_count'] == 1
    assert audit['session_mode'] == 'local_explicit_history'


def test_run_requires_orchestrator_agent_transport_without_state_machine_fallback():
    class NoToolClient:
        pass

    result = run_local_bangumi_case_agent(_mapping_workspace(), NoToolClient(), object())

    assert result.ok is False
    assert result.status == 'error'
    assert result.summary == 'orchestrator agent transport unavailable'
    assert any('error_kind=orchestrator_agent_unavailable' in error for error in result.errors)
    assert not any(
        isinstance(audit, dict) and audit.get('note') == 'orchestrator_agent_fallback_to_state_machine'
        for audit in result.final_workspace.judge_request_audits
    )


def test_orchestrator_agent_finish_accepted_uses_editor_patch_without_legacy_judge_loop():
    client = _RunClient(
        [
                {
                    'id': 'resp_edit',
                    'tool_calls': [
                        {
                            'call_id': 'call_edit',
                            'name': 'propose_mapping_intents',
                            'arguments': json.dumps({
                                'reason': 'map visible singleton',
                                'mapping_intents': [
                                    {
                                        'decision': 'map_explicit_item',
                                        'local_ref': 'LS1',
                                        'chosen_item_ref': 'BE1',
                                        'support_refs': ['LS1', 'BE1'],
                                        'reason': 'visible singleton target',
                                    }
                                ],
                            }),
                        }
                    ],
                },
            {
                'id': 'resp_finish',
                'tool_calls': [
                    {
                        'call_id': 'call_finish',
                        'name': 'finish_case',
                        'arguments': json.dumps({'status': 'accepted', 'finish_kind': 'accepted', 'reason': 'accounting ready'}),
                    }
                ],
            },
        ],
    )

    result = run_local_bangumi_case_agent(_mapping_workspace(), client, object())

    assert result.ok is True
    assert result.status == 'accepted'
    assert client.mapping_editor_calls == 0
    assert client.case_judge_calls == 0
    assert [call['tools'][0]['function']['name'] for call in client.calls]
    assert [
        audit.get('tool_name')
        for audit in result.final_workspace.judge_request_audits
        if isinstance(audit, dict) and audit.get('note') == 'orchestrator_agent_called'
    ] == ['propose_mapping_intents', 'finish_case']


def test_orchestrator_agent_accepts_regular_span_from_agent_selected_items():
    client = _RunClient(
        [
            {
                'id': 'resp_span',
                'tool_calls': [
                    {
                        'call_id': 'call_span',
                        'name': 'propose_mapping_intents',
                        'arguments': json.dumps({
                            'reason': 'map visible regular run from explicit item sequence',
                            'mapping_intents': [
                                {
                                    'decision': 'map_regular_span',
                                    'local_ref': 'LS1',
                                    'chosen_subject_ref': 'BS1',
                                    'episode_start': 1,
                                    'episode_end': 2,
                                    'item_refs': ['BE1', 'BE2'],
                                    'support_refs': ['LS1', 'BS1', 'BE1', 'BE2'],
                                    'reason': 'visible episode list exactly covers the local span',
                                }
                            ],
                        }),
                    }
                ],
            },
            {
                'id': 'resp_finish',
                'tool_calls': [
                    {
                        'call_id': 'call_finish',
                        'name': 'finish_case',
                        'arguments': json.dumps({'status': 'accepted', 'finish_kind': 'accepted', 'reason': 'accounting ready'}),
                    }
                ],
            },
        ],
    )

    result = run_local_bangumi_case_agent(_regular_span_workspace(), client, object())

    assert result.ok is True
    assert result.status == 'accepted'
    assert any(
        isinstance(audit, dict)
        and audit.get('note') == 'orchestrator_mapping_intents_generated_spans'
        and audit.get('span_refs')
        for audit in result.final_workspace.judge_request_audits
    )


def test_terminal_fail_closed_rows_expose_finish_but_warn_about_target_absent_protocol():
    workspace = _mapping_workspace().with_mapping_draft(MappingDraft(rows=[
        MappingDraftRow(
            row_ref='MDR1',
            local_ref='LS1',
            local_ref_kind='span',
            status='unresolved',
            disposition='unaligned_fail_closed',
            reason_kind='insufficient_evidence',
            support_refs=['LS1'],
            reason='Bangumi target seems absent',
        )
    ]))
    client = _ToolAgentClient([
        {
            'id': 'resp_finish',
            'tool_calls': [
                {
                    'call_id': 'call_finish',
                    'name': 'finish_case',
                    'arguments': json.dumps({
                        'status': 'fail_closed',
                        'finish_kind': 'semantic_target_conflict',
                        'reason': 'true conflict',
                    }),
                }
            ],
        }
    ])

    result = call_orchestrator_agent(client, workspace, OrchestratorAgentSession(case_id='CASE-MAP'))
    tool_names = {tool['function']['name'] for tool in client.calls[0]['tools']}
    prompt = client.calls[0]['input_items'][-1]['content']

    assert result.ok is True
    assert 'finish_case' in tool_names
    assert 'mark_non_bangumi_or_supplemental(reason_kind=bangumi_target_absent)' in prompt
    assert 'will keep accounting unresolved' in prompt
    assert 'reject_candidate' in prompt


def test_orchestrator_agent_input_does_not_offer_no_legal_target_as_accepted_absence_exit():
    prompt = build_orchestrator_agent_input(_mapping_workspace(), reason='inspect protocol')
    payload = json.loads(prompt)

    assert 'no_legal_target' not in payload['allowed_unaligned_reason_kinds']
    assert 'bangumi_target_absent' in payload['allowed_supplemental_reason_kinds']
    assert 'target_absent_is_accepted_exclusion' in payload['finish_protocol']


def test_execute_evidence_tool_does_not_auto_call_mapping_editor():
    client = _RunClient([
        {
            'id': 'resp_execute',
            'tool_calls': [
                {
                    'call_id': 'call_execute',
                    'name': 'execute_evidence',
                    'arguments': json.dumps({
                        'reason': 'try visible evidence',
                        'selected_menu_request_ids': ['REQ_TARGET_SPAN_LS1'],
                    }),
                }
            ],
        }
    ])

    result = run_local_bangumi_case_agent(_mapping_workspace(), client, object(), max_rounds=1)

    assert result.status in {'fail_closed', 'error'}
    assert client.mapping_editor_calls == 0
    assert [
        audit.get('tool_name')
        for audit in result.final_workspace.judge_request_audits
        if isinstance(audit, dict) and audit.get('note') == 'orchestrator_agent_called'
    ] == ['execute_evidence']


def test_finish_case_accepted_rejected_when_accounting_not_ready():
    client = _RunClient([
        {
            'id': 'resp_finish',
            'tool_calls': [
                {
                    'call_id': 'call_finish',
                    'name': 'finish_case',
                    'arguments': json.dumps({'status': 'accepted', 'finish_kind': 'accepted', 'reason': 'premature'}),
                }
            ],
        }
    ])

    result = run_local_bangumi_case_agent(_mapping_workspace(), client, object(), max_rounds=1)

    assert result.status in {'fail_closed', 'error'}
    assert any(
        isinstance(audit, dict)
        and audit.get('note') == 'orchestrator_tool_selected'
        and audit.get('tool_name') == 'finish_case'
        and audit.get('accepted') is False
        and audit.get('reason') == 'tool_not_available_in_current_state'
        for audit in result.final_workspace.judge_request_audits
    )


def test_update_notebook_tool_is_not_counted_as_rejected_decision():
    client = _RunClient([
        {
            'id': 'resp_note',
            'tool_calls': [
                {
                    'call_id': 'call_note',
                    'name': 'update_notebook',
                    'arguments': json.dumps({
                        'reason': 'record local singleton hypothesis',
                        'notebook_updates': [
                            {
                                'update_kind': 'note',
                                'local_refs': ['LF1'],
                                'claim': 'LF1 is the current work unit',
                                'confidence': 'medium',
                                'reason': 'single visible local main file',
                            }
                        ],
                    }),
                }
            ],
        }
    ])

    result = run_local_bangumi_case_agent(_mapping_workspace(), client, object(), max_rounds=1)

    assert result.status in {'fail_closed', 'error'}
    assert client.case_judge_calls == 0
    assert any(
        isinstance(audit, dict)
        and audit.get('note') == 'orchestrator_tool_selected'
        and audit.get('tool_name') == 'update_notebook'
        and audit.get('accepted') is True
        for audit in result.final_workspace.judge_request_audits
    )
    session_summary = next(
        audit
        for audit in result.final_workspace.judge_request_audits
        if isinstance(audit, dict) and audit.get('note') == 'orchestrator_agent_session_summary'
    )
    assert session_summary['orchestrator_tool_sequence'] == ['update_notebook']
    assert session_summary['tool_rejection_count'] == 0


def test_reconsider_split_tool_returns_observation_without_legacy_judge():
    client = _RunClient([
        {
            'id': 'resp_split',
            'tool_calls': [
                {
                    'call_id': 'call_split',
                    'name': 'reconsider_split',
                    'arguments': json.dumps({
                        'reason': 'large package may need child cases',
                        'local_refs': ['LF1'],
                    }),
                }
            ],
        }
    ])

    result = run_local_bangumi_case_agent(_mapping_workspace(), client, object(), max_rounds=1)

    assert result.status in {'fail_closed', 'error'}
    assert client.case_judge_calls == 0
    assert any(
        isinstance(audit, dict)
        and audit.get('note') == 'orchestrator_reconsider_split_observation'
        and audit.get('local_refs') == ['LF1']
        for audit in result.final_workspace.judge_request_audits
    )
