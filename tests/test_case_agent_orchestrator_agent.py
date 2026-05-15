from __future__ import annotations

import json

from src.bangumi.models import BangumiEpisode, BangumiSubject
from src.rename.case_agent.models import (
    AssignmentIntent,
    BangumiItemCard,
    BangumiSpanCard,
    BangumiSubjectCard,
    CaseBudget,
    CaseBriefingOutput,
    CaseBriefingWorkUnit,
    CaseContract,
    CaseJudgeOutput,
    Finding,
    CaseHeader,
    LocalFileCard,
    LocalSpanCard,
    MappingIntent,
    MappingDraft,
    MappingDraftRow,
    InvestigationNotebook,
    NotebookUpdate,
    NotebookOpenQuestion,
    NotebookNextAction,
    QueryCandidate,
    VerifierIssue,
)
from src.rename.case_agent.orchestrator import run_local_bangumi_case_agent, _compact_final_assignment_support_refs, _compile_case_understanding, _prepare_workspace_for_orchestrator_agent_turn, _reopen_mapping_draft_issue_rows, _run_orchestrator_execute_evidence_tool, _run_orchestrator_materialize_queries_tool, _run_orchestrator_propose_mapping_intents_tool, _run_orchestrator_reconsider_split_tool
from src.rename.case_agent.orchestrator import _decision_from_orchestrator_tool_call
from src.rename.case_agent.orchestrator import _refresh_mapping_draft_candidates
from src.rename.case_agent.orchestrator_agent import (
    ExecuteEvidenceToolArgs,
    MaterializeQueriesToolArgs,
    OrchestratorAgentSession,
    OrchestratorAgentToolCall,
    ProposeMappingIntentsToolArgs,
    ProposeCaseUnderstandingToolArgs,
    ReconsiderSplitToolArgs,
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
        case_briefing=CaseBriefingOutput(
            package_shape='single episode',
            work_units=[CaseBriefingWorkUnit(work_unit_ref='WU1', label='single file', local_refs=['LS1'], file_refs=['LF1'], span_refs=['LS1'])],
        ),
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
        case_briefing=CaseBriefingOutput(
            package_shape='two regular episodes',
            work_units=[CaseBriefingWorkUnit(work_unit_ref='WU1', label='regular span', local_refs=['LS1'], file_refs=file_refs, span_refs=['LS1'])],
        ),
    )


def _raw_mapping_workspace_without_understanding() -> CaseEvidenceWorkspace:
    return CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-RAW-MAP'),
        budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_requests_per_batch=2),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']),
        local_files=[LocalFileCard(ref='LF1', path='Title 01.mkv', is_main=True, label='Title 01.mkv')],
        bangumi_items=[BangumiItemCard(ref='BE1', subject_ref='BS1', sort=1, ep=1, item_kind='episode')],
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


def test_case_understanding_tool_compiles_work_units_to_spans_and_draft():
    workspace = _raw_mapping_workspace_without_understanding()
    args = ProposeCaseUnderstandingToolArgs(
        reason='understand single file',
        package_shape='single episode',
        work_units=[
            CaseBriefingWorkUnit(
                work_unit_ref='WU1',
                label='episode 1',
                local_refs=['LF1'],
                file_refs=['LF1'],
                title_hints=['Title'],
                reason='single visible main file',
            )
        ],
        summary='single visible main file',
    )

    updated, observation = _compile_case_understanding(workspace, args)

    assert observation['status'] == 'ok'
    assert observation['case_understanding_applied'] is True
    assert [card.ref for card in updated.local_span_cards] == ['LS_PACKAGE', 'LS1']
    assert updated.mapping_draft is not None
    assert updated.mapping_draft.rows[0].local_ref == 'LS1'
    assert updated.case_briefing is not None


def test_case_understanding_revision_closes_repartition_agenda():
    workspace = _regular_span_workspace()
    object.__setattr__(workspace, 'investigation_notebook', InvestigationNotebook(
        open_questions=[
            NotebookOpenQuestion(
                question_ref='NQ_REPARTITION',
                question_kind='work_unit_repartition',
                question='Revise work-unit boundaries.',
                local_refs=['LS1'],
                status='open',
            )
        ],
        next_actions=[
            NotebookNextAction(
                action_ref='NA_REPARTITION',
                action_type='work_unit_repartition',
                local_refs=['LS1'],
                status='open',
            )
        ],
    ))
    object.__setattr__(workspace, 'judge_request_audits', [
        {'note': 'orchestrator_reconsider_split_observation', 'status': 'ok'},
    ])
    args = ProposeCaseUnderstandingToolArgs(
        reason='revise partition after repartition request',
        package_shape='two regular episodes',
        work_units=[
            CaseBriefingWorkUnit(
                work_unit_ref='WU1',
                label='regular span',
                local_refs=['LS1'],
                file_refs=['LF1', 'LF2'],
                span_refs=['LS1'],
            )
        ],
        summary='same regular span remains valid',
    )

    updated, observation = _compile_case_understanding(workspace, args)

    assert observation['status'] == 'ok'
    notebook = updated.investigation_notebook
    assert notebook is not None
    assert all(question.question_kind != 'work_unit_repartition' for question in notebook.open_questions)
    assert all(action.action_type != 'work_unit_repartition' for action in notebook.next_actions)


def test_case_understanding_rejects_duplicate_main_file_ownership():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-BAD-UNDERSTANDING'),
        budget=CaseBudget(max_judge_rounds=5),
        contract=CaseContract(main_file_refs=['LF1', 'LF2'], allowed_file_refs=['LF1', 'LF2']),
        local_files=[
            LocalFileCard(ref='LF1', path='Title 01.mkv', is_main=True),
            LocalFileCard(ref='LF2', path='Title 02.mkv', is_main=True),
        ],
    )
    args = ProposeCaseUnderstandingToolArgs(
        reason='bad duplicate',
        work_units=[
            CaseBriefingWorkUnit(work_unit_ref='WU1', local_refs=['LF1', 'LF2'], file_refs=['LF1', 'LF2']),
            CaseBriefingWorkUnit(work_unit_ref='WU2', local_refs=['LF2'], file_refs=['LF2']),
        ],
    )

    _updated, observation = _compile_case_understanding(workspace, args)

    assert observation['status'] == 'rejected'
    assert 'case_understanding_duplicate_main_refs' in observation['issue_codes']


def test_case_understanding_treats_context_span_as_support_when_file_refs_are_explicit():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-CONTEXT-SPAN'),
        budget=CaseBudget(max_judge_rounds=5),
        contract=CaseContract(main_file_refs=['LF1', 'LF2'], allowed_file_refs=['LF1', 'LF2']),
        local_files=[
            LocalFileCard(ref='LF1', path='Special.mkv', is_main=True),
            LocalFileCard(ref='LF2', path='01.mkv', is_main=True),
        ],
        local_span_cards=[
            LocalSpanCard(ref='LS1', span_scope='unpartitioned', file_refs=['LF1', 'LF2'], file_ref_count=2),
        ],
    )
    args = ProposeCaseUnderstandingToolArgs(
        reason='split with package context',
        work_units=[
            CaseBriefingWorkUnit(work_unit_ref='WU1', local_refs=['LS1'], file_refs=['LF1'], title_hints=['special']),
            CaseBriefingWorkUnit(work_unit_ref='WU2', local_refs=['LS1'], file_refs=['LF2'], title_hints=['regular']),
        ],
    )

    updated, observation = _compile_case_understanding(workspace, args)

    assert observation['status'] == 'ok'
    assert [row.local_ref for row in updated.mapping_draft.rows] == ['LS1', 'LS2']
    assert [card.file_refs for card in updated.local_span_cards if card.ref in {'LS1', 'LS2'}] == [['LF1'], ['LF2']]


def test_case_understanding_revision_resets_draft_after_reconsider_split():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-UNDERSTANDING-REVISION'),
        budget=CaseBudget(max_judge_rounds=5),
        contract=CaseContract(main_file_refs=['LF1', 'LF2', 'LF3'], allowed_file_refs=['LF1', 'LF2', 'LF3']),
        local_files=[
            LocalFileCard(ref='LF1', path='Title 01.mkv', is_main=True),
            LocalFileCard(ref='LF2', path='Title 02.mkv', is_main=True),
            LocalFileCard(ref='LF3', path='Title OVA.mkv', is_main=True),
        ],
    )
    first_args = ProposeCaseUnderstandingToolArgs(
        reason='initial broad unit',
        package_shape='mixed package',
        work_units=[
            CaseBriefingWorkUnit(work_unit_ref='WU1', label='too broad', local_refs=['LF1', 'LF2', 'LF3'], file_refs=['LF1', 'LF2', 'LF3']),
        ],
    )
    workspace, first_observation = _compile_case_understanding(workspace, first_args)
    workspace = workspace.with_mapping_draft(MappingDraft(rows=[
        MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span', disposition='needs_more_evidence')
    ], version=1))

    workspace, reconsider_observation = _run_orchestrator_reconsider_split_tool(
        workspace,
        ReconsiderSplitToolArgs(reason='broad unit did not map', local_refs=['LS1']),
    )
    revision_payload = json.loads(build_orchestrator_agent_input(workspace, reason='after split reconsideration'))
    revise_args = ProposeCaseUnderstandingToolArgs(
        reason='revise into regular plus ova',
        package_shape='regular episodes plus OVA',
        work_units=[
            CaseBriefingWorkUnit(work_unit_ref='WU_REG', label='regular 1-2', local_refs=['LS1'], file_refs=['LF1', 'LF2'], title_hints=['Title']),
            CaseBriefingWorkUnit(work_unit_ref='WU_OVA', label='OVA', local_refs=['LS1'], file_refs=['LF3'], title_hints=['Title OVA'], source_form_hints=['OVA']),
        ],
    )
    updated, revision_observation = _compile_case_understanding(workspace, revise_args)

    assert first_observation['status'] == 'ok'
    assert reconsider_observation['status'] == 'ok'
    assert 'propose_case_understanding' in revision_payload['available_tool_names']
    assert revision_observation['status'] == 'ok'
    assert revision_observation['case_understanding_revised'] is True
    assert [row.local_ref for row in updated.mapping_draft.rows] == ['LS1', 'LS2']
    assert [card.file_refs for card in updated.local_span_cards if card.ref in {'LS1', 'LS2'}] == [['LF1', 'LF2'], ['LF3']]
    assert any(
        isinstance(audit, dict) and audit.get('note') == 'case_understanding_revised'
        for audit in updated.judge_request_audits
    )


def test_case_understanding_revision_preserves_existing_mapping_draft_and_notebook():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-UNDERSTANDING-PRESERVE'),
        budget=CaseBudget(max_judge_rounds=5),
        contract=CaseContract(main_file_refs=['LF1', 'LF2'], allowed_file_refs=['LF1', 'LF2']),
        local_files=[
            LocalFileCard(ref='LF1', path='Title 01.mkv', is_main=True),
            LocalFileCard(ref='LF2', path='Title 02.mkv', is_main=True),
        ],
    )
    initial_args = ProposeCaseUnderstandingToolArgs(
        reason='initial understanding',
        package_shape='two files',
        work_units=[
            CaseBriefingWorkUnit(work_unit_ref='WU1', label='first', local_refs=['LF1'], file_refs=['LF1']),
            CaseBriefingWorkUnit(work_unit_ref='WU2', label='second', local_refs=['LF2'], file_refs=['LF2']),
        ],
    )
    workspace, _ = _compile_case_understanding(workspace, initial_args)
    object.__setattr__(workspace, 'mapping_draft', MappingDraft(rows=[
        MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span', disposition='map_to_bangumi', selected_target_ref='BE1', selected_target_kind='item', mapping_mode='explicit'),
        MappingDraftRow(row_ref='MDR2', local_ref='LS2', local_ref_kind='span', disposition='open', status='open'),
    ], version=3))
    object.__setattr__(workspace, 'investigation_notebook', InvestigationNotebook(
        open_questions=[NotebookOpenQuestion(question_ref='NQ1', question_kind='title_identity', question='keep this question', local_refs=['LF2'], requested_request_types=['subject_search'])],
        next_actions=[NotebookNextAction(action_ref='NA1', action_type='subject_recall', requested_request_types=['subject_search'], local_refs=['LF2'])],
    ))

    revise_args = ProposeCaseUnderstandingToolArgs(
        reason='revise without losing state',
        package_shape='same package revised',
        work_units=[
            CaseBriefingWorkUnit(work_unit_ref='WU1', label='first', local_refs=['LF1'], file_refs=['LF1']),
            CaseBriefingWorkUnit(work_unit_ref='WU2', label='second', local_refs=['LF2'], file_refs=['LF2']),
        ],
    )
    updated, observation = _compile_case_understanding(workspace, revise_args)

    assert observation['case_understanding_revised'] is True
    assert [row.row_ref for row in updated.mapping_draft.rows] == ['MDR1', 'MDR2']
    assert any(q.question_ref == 'NQ1' for q in updated.investigation_notebook.open_questions)


def test_run_starts_with_case_understanding_before_mapping_intents():
    client = _RunClient(
        [
            {
                'id': 'resp_understand',
                'tool_calls': [
                    {
                        'call_id': 'call_understand',
                        'name': 'propose_case_understanding',
                        'arguments': json.dumps({
                            'reason': 'understand single file first',
                            'package_shape': 'single episode',
                            'work_units': [
                                {
                                    'work_unit_ref': 'WU1',
                                    'label': 'episode 1',
                                    'local_refs': ['LF1'],
                                    'file_refs': ['LF1'],
                                    'title_hints': ['Title'],
                                    'reason': 'single visible main file',
                                }
                            ],
                            'summary': 'single visible main file',
                        }),
                    }
                ],
            },
            {
                'id': 'resp_map',
                'tool_calls': [
                    {
                        'call_id': 'call_map',
                        'name': 'propose_mapping_intents',
                        'arguments': json.dumps({
                            'reason': 'map visible singleton after understanding',
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

    result = run_local_bangumi_case_agent(_raw_mapping_workspace_without_understanding(), client, object())

    assert result.ok is True
    assert result.status == 'accepted'
    assert client.mapping_editor_calls == 0
    assert client.case_judge_calls == 0
    assert [
        audit.get('tool_name')
        for audit in result.final_workspace.judge_request_audits
        if isinstance(audit, dict) and audit.get('note') == 'orchestrator_agent_called'
    ] == ['propose_case_understanding', 'propose_mapping_intents', 'finish_case']
    assert any(
        isinstance(audit, dict) and audit.get('note') == 'case_understanding_applied'
        for audit in result.final_workspace.judge_request_audits
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


def test_generated_intent_span_does_not_leak_to_unrelated_open_row():
    file_refs = [f'LF{i}' for i in range(1, 32)]
    items = [
        BangumiItemCard(ref=f'BE{i}', subject_ref='BS1', item_kind='episode', sort=i, ep=i)
        for i in range(1, 14)
    ]
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-GENERATED-SPAN-BOUNDARY'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=file_refs, allowed_file_refs=file_refs, visible_target_refs=[item.ref for item in items]),
        local_files=[
            LocalFileCard(ref=ref, path=f'Title {index:02d}.mkv', is_main=True, file_kind='video')
            for index, ref in enumerate(file_refs, start=1)
        ],
        local_span_cards=[
            LocalSpanCard(
                ref='LS1',
                span_scope='token_segment',
                file_refs=file_refs[:13],
                file_ref_count=13,
                file_ref_samples=['LF1', 'LF2', 'LF12', 'LF13'],
                episode_token_start=1,
                episode_token_end=13,
                episode_token_count=13,
            ),
            LocalSpanCard(
                ref='LS2',
                span_scope='residual',
                file_refs=file_refs[13:],
                file_ref_count=18,
                file_ref_samples=['LF14', 'LF15', 'LF30', 'LF31'],
            ),
        ],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', title='Title')],
        bangumi_items=items,
        bangumi_span_cards=[
            BangumiSpanCard(
                ref='BES_INTENT_MDR1_1',
                subject_ref='BS1',
                target_refs=[item.ref for item in items],
                target_ref_count=13,
                sort_start=1,
                sort_end=13,
                ep_start=1,
                ep_end=13,
                item_kind='regular',
                detail_equivalent=True,
                source_request_ref='INTENT_LS1',
            )
        ],
        mapping_draft=MappingDraft(rows=[
            MappingDraftRow(
                row_ref='MDR1',
                local_ref='LS1',
                local_ref_kind='span',
                disposition='map_to_bangumi',
                status='proposed',
                selected_target_ref='BES_INTENT_MDR1_1',
                selected_target_kind='span',
                mapping_mode='span_by_index',
                support_refs=['LS1', 'BES_INTENT_MDR1_1'],
            ),
            MappingDraftRow(row_ref='MDR2', local_ref='LS2', local_ref_kind='span'),
        ]),
    )

    updated = _refresh_mapping_draft_candidates(workspace)

    rows = {row.row_ref: row for row in updated.mapping_draft.rows}
    assert rows['MDR2'].candidate_target_refs == []


def test_orchestrator_turn_preparation_preserves_accepted_ready_draft_for_finish():
    workspace = _mapping_workspace().with_mapping_draft(MappingDraft(rows=[
        MappingDraftRow(
            row_ref='MDR1',
            local_ref='LS1',
            local_ref_kind='span',
            disposition='non_bangumi_or_supplemental',
            status='proposed',
            reason_kind='bangumi_target_absent',
            support_refs=['LS1'],
            reason='agent determined Bangumi has no target for this local unit',
        )
    ], version=1))

    prepared = _prepare_workspace_for_orchestrator_agent_turn(workspace)
    prompt = build_orchestrator_agent_input(prepared, reason='after accepted-ready mapping intents')
    payload = json.loads(prompt)

    row = prepared.mapping_draft.rows[0]
    assert row.disposition == 'non_bangumi_or_supplemental'
    assert row.status == 'proposed'
    assert row.reason_kind == 'bangumi_target_absent'
    assert payload['draft_accounting']['accepted_accounting_ready'] is True
    assert payload['finish_protocol']['accepted_finish_allowed_now'] is True
    assert payload['available_tool_names'] == ['finish_case']


def test_orchestrator_agent_uses_native_tool_call_with_local_history():
    client = _ToolAgentClient([
        {
            'id': 'resp_1',
            'tool_calls': [
                    {
                        'call_id': 'call_1',
                        'name': 'propose_case_understanding',
                        'arguments': json.dumps({
                            'reason': 'understand first',
                            'package_shape': 'single file',
                            'work_units': [{'work_unit_ref': 'WU1', 'label': 'main file', 'local_refs': ['LF1'], 'file_refs': ['LF1']}],
                            'title_hypotheses': [{'title': 'Title', 'source_refs': ['LF1'], 'confidence': 'medium'}],
                            'summary': 'single visible main file',
                        }),
                    }
            ],
            'usage': {'input_tokens': 123, 'output_tokens': 12},
        }
    ])

    result = call_orchestrator_agent(client, _workspace(), OrchestratorAgentSession(case_id='CASE-ORCH'))

    assert result.ok is True
    assert result.tool_call is not None
    assert result.tool_call.tool_name == 'propose_case_understanding'
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
    assert tool_names >= {'propose_case_understanding', 'materialize_queries', 'execute_evidence', 'propose_mapping_intents', 'finish_case'}
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


def test_actionable_open_row_surface_prefers_mapping_intents_over_more_evidence_tools():
    workspace = _regular_span_workspace()
    object.__setattr__(workspace, 'mapping_draft', MappingDraft(rows=[
        MappingDraftRow(
            row_ref='MDR1',
            local_ref='LS1',
            local_ref_kind='span',
            disposition='needs_more_evidence',
            status='open',
            candidate_target_refs=['BE1', 'BE2'],
            subject_refs=['BS1'],
            requested_request_types=['subject_lookup'],
        )
    ], version=2))

    payload = json.loads(build_orchestrator_agent_input(workspace, reason='actionable mapping surface already visible'))

    assert payload['open_rows_have_actionable_mapping_surface'] is True
    assert 'propose_mapping_intents' in payload['available_tool_names']
    assert 'materialize_queries' not in payload['available_tool_names']
    assert 'execute_evidence' not in payload['available_tool_names']
    assert 'update_notebook' not in payload['available_tool_names']


def test_mixed_open_rows_keep_evidence_available_for_rows_requesting_it():
    workspace = _regular_span_workspace()
    object.__setattr__(workspace, 'bangumi_subjects', [
        BangumiSubjectCard(ref='BS1', title='Title Movie'),
        BangumiSubjectCard(ref='BS2', title='Title TV'),
    ])
    object.__setattr__(workspace, 'bangumi_items', [
        BangumiItemCard(ref='BE1', subject_ref='BS1', sort=1, ep=1, item_kind='movie'),
    ])
    object.__setattr__(workspace, 'mapping_draft', MappingDraft(rows=[
        MappingDraftRow(
            row_ref='MDR1',
            local_ref='LF1',
            local_ref_kind='file',
            disposition='open',
            status='open',
            candidate_target_refs=['BE1'],
            subject_refs=['BS1'],
        ),
        MappingDraftRow(
            row_ref='MDR2',
            local_ref='LS1',
            local_ref_kind='span',
            disposition='needs_more_evidence',
            status='unresolved',
            subject_refs=['BS2'],
            requested_request_types=['episode_list'],
            reason_kind='ambiguous_candidate',
        ),
    ], version=2))

    payload = json.loads(build_orchestrator_agent_input(workspace, reason='mixed rows need evidence for unresolved row'))

    assert payload['open_rows_have_actionable_mapping_surface'] is True
    assert payload['available_tool_names'] == ['execute_evidence']


def test_actionable_surface_can_still_reopen_case_understanding_for_structural_repartition():
    workspace = _regular_span_workspace()
    object.__setattr__(workspace, 'mapping_draft', MappingDraft(rows=[
        MappingDraftRow(
            row_ref='MDR1',
            local_ref='LS1',
            local_ref_kind='span',
            disposition='needs_more_evidence',
            status='open',
            candidate_target_refs=['BE1', 'BE2'],
            subject_refs=['BS1'],
            requested_request_types=['subject_lookup'],
        )
    ], version=2))
    object.__setattr__(workspace, 'judge_request_audits', [
        {
            'note': 'orchestrator_mapping_intents_result',
            'status': 'blocked_intents',
            'blocked_intent_issue_codes': ['item_ref_count_mismatch'],
            'blocked_intent_count': 1,
        }
    ])

    payload = json.loads(build_orchestrator_agent_input(workspace, reason='structural repartition still needed'))

    assert payload['open_rows_have_actionable_mapping_surface'] is True
    assert 'propose_case_understanding' in payload['available_tool_names']
    assert 'propose_mapping_intents' in payload['available_tool_names']


def test_finish_case_hidden_when_budget_exhausted_before_latest_semantic_intent():
    workspace = _mapping_workspace()
    object.__setattr__(
        workspace,
        'budget',
        CaseBudget(max_judge_rounds=48, max_evidence_batches=1, used_evidence_batches=1, max_requests_per_batch=2),
    )
    object.__setattr__(workspace, 'judge_request_audits', [
        {'note': 'orchestrator_execute_evidence_menu_resolution', 'request_ids': ['REQ_TARGET_SPAN_LS1']},
    ])

    prepared = _prepare_workspace_for_orchestrator_agent_turn(workspace)
    prompt = build_orchestrator_agent_input(prepared, reason='budget exhausted after new evidence')
    payload = json.loads(prompt)

    assert 'finish_case' not in payload['available_tool_names']
    assert 'execute_evidence' not in payload['available_tool_names']
    assert 'propose_mapping_intents' in payload['available_tool_names']
    assert payload['finish_protocol']['accepted_finish_allowed_now'] is False


def test_finish_case_available_when_budget_exhausted_after_latest_semantic_intent():
    workspace = _mapping_workspace()
    object.__setattr__(
        workspace,
        'budget',
        CaseBudget(max_judge_rounds=48, max_evidence_batches=1, used_evidence_batches=1, max_requests_per_batch=2),
    )
    object.__setattr__(workspace, 'judge_request_audits', [
        {'note': 'orchestrator_execute_evidence_menu_resolution', 'request_ids': ['REQ_TARGET_SPAN_LS1']},
        {'note': 'orchestrator_mapping_intents_result', 'status': 'ok', 'compiled_patch_count': 0},
    ])

    prompt = build_orchestrator_agent_input(workspace, reason='budget exhausted after semantic intent')
    payload = json.loads(prompt)

    assert 'finish_case' in payload['available_tool_names']
    assert 'execute_evidence' not in payload['available_tool_names']


def test_case_understanding_prompt_exposes_all_main_files_for_human_partition():
    file_refs = [f'LF{i}' for i in range(1, 19)]
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-MAIN-FILE-OVERVIEW'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=file_refs, allowed_file_refs=file_refs),
        local_files=[
            *[
                LocalFileCard(ref=f'LF{i}', path=f'[Group][Title][{i:02d}].mkv', label=f'[Group][Title][{i:02d}].mkv', is_main=True, size_bytes=500_000_000)
                for i in range(1, 13)
            ],
            *[
                LocalFileCard(ref=f'LF{i}', path=f'[Group][Title][SP{i - 12:02d}].mkv', label=f'[Group][Title][SP{i - 12:02d}].mkv', is_main=True, size_bytes=40_000_000)
                for i in range(13, 19)
            ],
        ],
    )

    prompt = build_orchestrator_agent_input(workspace, reason='first understanding turn')
    payload = json.loads(prompt)

    overview = payload['local_main_file_overview']
    assert len(overview) == 18
    assert overview[0]['ref'] == 'LF1'
    assert overview[-1]['ref'] == 'LF18'
    assert any('SP01' in row['visible_tokens'] for row in overview if row['ref'] == 'LF13')
    assert any('disjoint file_refs' in rule for rule in payload['rules'])


def test_large_matching_sequence_exposes_full_item_ref_list_for_agent_intent():
    file_refs = [f'LF{i}' for i in range(1, 53)]
    items = [
        BangumiItemCard(ref=f'BE{i}', subject_ref='BS1', item_kind='episode', sort=i, ep=i)
        for i in range(1, 53)
    ]
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-LARGE-SEQUENCE'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=file_refs, allowed_file_refs=file_refs, visible_target_refs=[item.ref for item in items]),
        local_files=[
            LocalFileCard(ref=ref, path=f'Title {index:02d}.mkv', is_main=True, file_kind='video')
            for index, ref in enumerate(file_refs, start=1)
        ],
        local_span_cards=[
            LocalSpanCard(
                ref='LS1',
                span_scope='directory',
                file_refs=file_refs,
                file_ref_count=len(file_refs),
                file_ref_samples=['LF1', 'LF2', 'LF51', 'LF52'],
                title_cues=['Title'],
            )
        ],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', title='Title')],
        bangumi_items=items,
        case_briefing=CaseBriefingOutput(
            package_shape='large regular season',
            work_units=[CaseBriefingWorkUnit(work_unit_ref='WU1', label='large row', local_refs=['LS1'], file_refs=file_refs, span_refs=['LS1'])],
        ),
        mapping_draft=MappingDraft(rows=[
            MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span', subject_refs=['BS1']),
        ]),
    )

    payload = json.loads(build_orchestrator_agent_input(workspace, reason='large sequence surface'))
    sequence = payload['open_rows_requiring_agent_action'][0]['visible_subject_item_sequences'][0]

    assert sequence['matches_local_file_count'] is True
    assert sequence['item_ref_count'] == 52
    assert len(sequence['item_refs']) == 52
    assert sequence['item_refs'][0] == 'BE1'
    assert sequence['item_refs'][-1] == 'BE52'
    assert sequence['item_refs_truncated'] is False


def test_agent_mapping_intent_can_revise_prior_mapped_row_to_target_absent():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-REVISION'),
        budget=CaseBudget(max_judge_rounds=4, max_evidence_batches=2, max_requests_per_batch=2),
        contract=CaseContract(main_file_refs=['LF1', 'LF2'], allowed_file_refs=['LF1', 'LF2'], visible_target_refs=['BE1']),
        local_files=[
            LocalFileCard(ref='LF1', path='Title 01.mkv', is_main=True, label='Title 01.mkv'),
            LocalFileCard(ref='LF2', path='Title SP.mkv', is_main=True, label='Title SP.mkv'),
        ],
        local_span_cards=[
            LocalSpanCard(ref='LS1', span_scope='token_segment', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1']),
            LocalSpanCard(ref='LS2', span_scope='residual', file_refs=['LF2'], file_ref_count=1, file_ref_samples=['LF2']),
        ],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', title='Title')],
        bangumi_items=[BangumiItemCard(ref='BE1', subject_ref='BS1', sort=1, ep=1, item_kind='episode')],
        case_briefing=CaseBriefingOutput(
            package_shape='two rows',
            work_units=[
                CaseBriefingWorkUnit(work_unit_ref='WU1', label='episode', local_refs=['LS1'], file_refs=['LF1'], span_refs=['LS1']),
                CaseBriefingWorkUnit(work_unit_ref='WU2', label='extra', local_refs=['LS2'], file_refs=['LF2'], span_refs=['LS2']),
            ],
        ),
        mapping_draft=MappingDraft(rows=[
            MappingDraftRow(
                row_ref='MDR1',
                local_ref='LS1',
                local_ref_kind='span',
            disposition='map_to_bangumi',
            status='proposed',
            selected_target_ref='BES_OLD',
            selected_target_kind='span',
                mapping_mode='span_by_index',
                support_refs=['LS1', 'BES_OLD'],
            ),
            MappingDraftRow(
                row_ref='MDR2',
                local_ref='LS2',
                local_ref_kind='span',
                disposition='open',
                status='open',
            ),
        ], version=2),
    )
    args = ProposeMappingIntentsToolArgs(
        reason='revise prior semantic decision',
        mapping_intents=[
            MappingIntent(
                decision='mark_non_bangumi_or_supplemental',
                local_ref='LS1',
                support_refs=['LS1'],
                reason_kind='bangumi_target_absent',
                reason='agent revised the prior mapping after later evidence',
            ),
            MappingIntent(
                decision='mark_non_bangumi_or_supplemental',
                local_ref='LS2',
                support_refs=['LS2'],
                reason_kind='bangumi_target_absent',
                reason='agent handles the remaining open row',
            ),
        ],
    )

    updated, observation = _run_orchestrator_propose_mapping_intents_tool(workspace, args)

    row = updated.mapping_draft.rows[0]
    assert observation['compiled_patch_count'] == 2
    assert row.disposition == 'non_bangumi_or_supplemental'
    assert row.selected_target_ref == ''
    assert row.reason_kind == 'bangumi_target_absent'


def test_structural_intent_issue_reopens_case_understanding_tool():
    workspace = _regular_span_workspace()
    object.__setattr__(workspace, 'judge_request_audits', [
        {
            'note': 'orchestrator_mapping_intents_result',
            'status': 'blocked_intents',
            'blocked_intent_issue_codes': ['item_ref_count_mismatch'],
            'blocked_intent_count': 1,
        }
    ])

    prepared = _prepare_workspace_for_orchestrator_agent_turn(workspace)
    prompt = build_orchestrator_agent_input(prepared, reason='structural mismatch')
    payload = json.loads(prompt)

    assert 'propose_case_understanding' in payload['available_tool_names']
    assert any('item_ref_count_mismatch' in rule and 'propose_case_understanding' in rule for rule in payload['rules'])


def test_notebook_repartition_agenda_reopens_case_understanding_after_prior_revision():
    workspace = _regular_span_workspace()
    object.__setattr__(workspace, 'mapping_draft', MappingDraft(rows=[
        MappingDraftRow(
            row_ref='MDR1',
            local_ref='LS1',
            local_ref_kind='span',
            disposition='needs_more_evidence',
            requested_request_types=['target_span'],
        )
    ], version=2))
    object.__setattr__(workspace, 'investigation_notebook', InvestigationNotebook(
        open_questions=[
            NotebookOpenQuestion(
                question_ref='RQ_REPARTITION',
                question_kind='work_unit_repartition',
                question='The current local row may be too broad and should be partitioned again.',
                local_refs=['LS1'],
                requested_request_types=['target_span'],
                status='open',
            )
        ],
        next_actions=[
            NotebookNextAction(
                action_ref='NA_REPARTITION',
                action_type='work_unit_repartition',
                local_refs=['LS1'],
                status='open',
                reason='The Agent wants to revise work-unit boundaries after compiler feedback.',
            )
        ],
    ))
    object.__setattr__(workspace, 'judge_request_audits', [
        {'note': 'orchestrator_reconsider_split_observation', 'status': 'ok'},
        {'note': 'case_understanding_revised', 'work_unit_count': 1},
        {
            'note': 'orchestrator_mapping_intents_result',
            'status': 'ok',
            'blocked_intent_issue_codes': [],
            'blocked_intent_count': 0,
            'compiled_patch_count': 1,
        },
    ])

    prompt = build_orchestrator_agent_input(workspace, reason='notebook requests repartition after prior revision')
    payload = json.loads(prompt)

    assert 'propose_case_understanding' in payload['available_tool_names']
    assert any('work_unit_repartition' in rule for rule in payload['rules'])


def test_requested_evidence_does_not_force_execute_when_no_matching_fresh_request():
    workspace = _regular_span_workspace()
    object.__setattr__(workspace, 'mapping_draft', MappingDraft(rows=[
        MappingDraftRow(
            row_ref='MDR1',
            local_ref='LS1',
            local_ref_kind='span',
            disposition='needs_more_evidence',
            requested_request_types=['target_span'],
            subject_refs=['BS1'],
        )
    ], version=2))
    object.__setattr__(workspace, 'judge_request_audits', [
        {
            'note': 'orchestrator_mapping_intents_result',
            'status': 'ok',
            'requested_evidence': ['target_span'],
            'blocked_intent_count': 0,
            'compiled_patch_count': 1,
        },
    ])
    object.__setattr__(workspace.plan_state, 'completed_menu_request_ids', ['REQ_TARGET_SPAN_LS1'])

    payload = json.loads(build_orchestrator_agent_input(workspace, reason='requested evidence is stale'))

    assert payload['available_tool_names'] != ['execute_evidence']
    assert 'propose_mapping_intents' in payload['available_tool_names']


def test_execute_evidence_reuses_latest_blocked_subject_agenda_when_agent_omits_args():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-EVIDENCE-AGENDA'),
        budget=CaseBudget(max_requests_per_batch=8),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1']),
        local_files=[LocalFileCard(ref='LF1', path='Title 01.mkv', is_main=True)],
        bangumi_subjects=[
            BangumiSubjectCard(ref='BS1', title='Wrong'),
            BangumiSubjectCard(ref='BS5', title='Right'),
        ],
        query_cards=[],
    )
    object.__setattr__(workspace, 'judge_request_audits', [
        {
            'note': 'orchestrator_mapping_intents_result',
            'requested_evidence': ['episode_list', 'target_span'],
            'blocked_intents': [{'subject_refs': ['BS5']}],
        }
    ])
    tool_call = OrchestratorAgentToolCall(
        tool_name='execute_evidence',
        arguments=ExecuteEvidenceToolArgs(reason='continue requested evidence'),
        raw_arguments={'reason': 'continue requested evidence'},
        call_id='call_execute',
    )

    _workspace, decision, acceptance = _decision_from_orchestrator_tool_call(workspace, tool_call)

    assert decision is not None
    assert decision.action == 'execute_evidence'
    assert acceptance['agenda_subject_refs'] == ['BS5']
    assert acceptance['prioritized_subject_refs'] == ['BS5']
    assert acceptance['selected_menu_request_ids'] == ['REQ_EPISODE_LIST_BS5']


def test_execute_evidence_executes_augmented_agent_subject_request_id():
    class FakeBangumiClient:
        def get_subject(self, subject_id):
            return BangumiSubject(id=subject_id, name='Title', name_cn='Title', platform='TV', eps=2, total_episodes=2)

        def get_episodes(self, subject_id):
            return [
                BangumiEpisode(id=101, subject_id=subject_id, sort=1, ep=1, type=0, name='Episode 1'),
                BangumiEpisode(id=102, subject_id=subject_id, sort=2, ep=2, type=0, name='Episode 2'),
            ]

    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-AUGMENTED-EVIDENCE'),
        budget=CaseBudget(max_evidence_batches=2, max_requests_per_batch=4, max_api_calls_per_case=4),
        contract=CaseContract(main_file_refs=['LF1', 'LF2'], allowed_file_refs=['LF1', 'LF2']),
        local_files=[
            LocalFileCard(ref='LF1', path='Title 01.mkv', is_main=True),
            LocalFileCard(ref='LF2', path='Title 02.mkv', is_main=True),
        ],
        bangumi_subjects=[BangumiSubjectCard(ref='BS5', subject_id=5, title='Title')],
    )
    object.__setattr__(workspace, 'judge_request_audits', [
        {
            'note': 'orchestrator_mapping_intents_result',
            'requested_evidence': ['episode_list'],
            'blocked_intents': [{'subject_refs': ['BS5']}],
        }
    ])
    tool_call = OrchestratorAgentToolCall(
        tool_name='execute_evidence',
        arguments=ExecuteEvidenceToolArgs(reason='continue requested evidence'),
        raw_arguments={'reason': 'continue requested evidence'},
        call_id='call_execute',
    )
    _workspace, decision, acceptance = _decision_from_orchestrator_tool_call(workspace, tool_call)

    evidence_batches = []
    updated, observation = _run_orchestrator_execute_evidence_tool(_workspace, decision.planner_output, FakeBangumiClient(), evidence_batches)

    assert 'REQ_EPISODE_LIST_BS5' in acceptance['selected_menu_request_ids']
    assert 'REQ_EPISODE_LIST_BS5' in observation['executed_menu_request_ids']
    assert any(item['request_ref'] == 'REQ_EPISODE_LIST_BS5' and item['accepted'] is True for item in observation['request_statuses'])
    assert [item.ref for item in updated.bangumi_items] == ['BE1', 'BE2']
    assert any(
        audit.get('note') == 'orchestrator_execute_evidence_menu_resolution'
        and audit.get('unknown_menu_request_ids') == []
        and 'REQ_EPISODE_LIST_BS5' in audit.get('dynamic_subject_request_refs', [])
        for audit in updated.judge_request_audits
        if isinstance(audit, dict)
    )


def test_execute_evidence_rejected_after_budget_exhausted():
    workspace = _mapping_workspace()
    object.__setattr__(
        workspace,
        'budget',
        CaseBudget(max_evidence_batches=1, used_evidence_batches=1, max_requests_per_batch=4),
    )
    tool_call = OrchestratorAgentToolCall(
        tool_name='execute_evidence',
        arguments=ExecuteEvidenceToolArgs(
            reason='try after budget',
            selected_menu_request_ids=['REQ_TARGET_SPAN_LS1'],
        ),
        raw_arguments={'reason': 'try after budget', 'selected_menu_request_ids': ['REQ_TARGET_SPAN_LS1']},
        call_id='call_execute',
    )

    _workspace, decision, acceptance = _decision_from_orchestrator_tool_call(workspace, tool_call)

    assert decision is None
    assert acceptance['accepted'] is False
    assert acceptance['reason'] == 'evidence_budget_exhausted'
    assert 'propose_mapping_intents' in acceptance['recommended_next_observation'].casefold()


def test_open_row_sequences_include_latest_blocked_subject_ref():
    file_refs = [f'LF{i}' for i in range(1, 4)]
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-BLOCKED-SUBJECT-SEQUENCE'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=file_refs, allowed_file_refs=file_refs, visible_target_refs=['BE1', 'BE2', 'BE3']),
        local_files=[
            LocalFileCard(ref=ref, path=f'Title {index:02d}.mkv', is_main=True)
            for index, ref in enumerate(file_refs, start=1)
        ],
        local_span_cards=[
            LocalSpanCard(ref='LS1', span_scope='token_segment', file_refs=file_refs, file_ref_count=3, file_ref_samples=file_refs)
        ],
        bangumi_subjects=[BangumiSubjectCard(ref='BS5', title='Title')],
        bangumi_items=[
            BangumiItemCard(ref=f'BE{i}', subject_ref='BS5', item_kind='episode', ep=i, sort=i)
            for i in range(1, 4)
        ],
        mapping_draft=MappingDraft(rows=[
            MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span')
        ]),
    )
    object.__setattr__(workspace, 'judge_request_audits', [
        {
            'note': 'orchestrator_mapping_intents_result',
            'blocked_intents': [{'local_ref': 'LS1', 'subject_refs': ['BS5']}],
        }
    ])

    payload = json.loads(build_orchestrator_agent_input(workspace, reason='after blocked subject intent'))
    row = payload['open_rows_requiring_agent_action'][0]

    assert row['latest_blocked_subject_refs'] == ['BS5']
    assert any(
        seq.get('subject_ref') == 'BS5'
        and seq.get('item_refs') == ['BE1', 'BE2', 'BE3']
        and seq.get('matches_local_file_count') is True
        for seq in row['visible_subject_item_sequences']
    )


def test_open_row_sequences_include_other_visible_subjects_after_wrong_blocked_subject():
    file_refs = [f'LF{i}' for i in range(1, 4)]
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-ALL-VISIBLE-SUBJECT-SEQUENCES'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=file_refs, allowed_file_refs=file_refs),
        local_files=[
            LocalFileCard(ref=ref, path=f'Title {index:02d}.mkv', is_main=True)
            for index, ref in enumerate(file_refs, start=1)
        ],
        local_span_cards=[
            LocalSpanCard(ref='LS1', span_scope='token_segment', file_refs=file_refs, file_ref_count=3, file_ref_samples=file_refs)
        ],
        bangumi_subjects=[
            BangumiSubjectCard(ref='BS_WRONG', title='Wrong title'),
            BangumiSubjectCard(ref='BS_RIGHT', title='Right title'),
        ],
        bangumi_items=[
            *[
                BangumiItemCard(ref=f'BEW{i}', subject_ref='BS_WRONG', item_kind='episode', ep=i, sort=i)
                for i in range(1, 4)
            ],
            *[
                BangumiItemCard(ref=f'BER{i}', subject_ref='BS_RIGHT', item_kind='episode', ep=i, sort=i)
                for i in range(1, 4)
            ],
        ],
        mapping_draft=MappingDraft(rows=[
            MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span')
        ]),
    )
    object.__setattr__(workspace, 'judge_request_audits', [
        {
            'note': 'orchestrator_mapping_intents_result',
            'blocked_intents': [{'local_ref': 'LS1', 'subject_refs': ['BS_WRONG']}],
        }
    ])

    payload = json.loads(build_orchestrator_agent_input(workspace, reason='after wrong blocked subject intent'))
    row = payload['open_rows_requiring_agent_action'][0]

    sequence_subjects = {seq.get('subject_ref') for seq in row['visible_subject_item_sequences']}
    assert {'BS_WRONG', 'BS_RIGHT'} <= sequence_subjects
    assert any(
        seq.get('subject_ref') == 'BS_RIGHT'
        and seq.get('item_refs') == ['BER1', 'BER2', 'BER3']
        and seq.get('matches_local_file_count') is True
        for seq in row['visible_subject_item_sequences']
    )


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


def test_needs_more_evidence_is_rejected_when_row_candidate_can_be_resolved():
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
                candidate_target_refs=['BES_SPECIAL'],
                subject_refs=['BS1'],
            )
        ]),
        bangumi_span_cards=[
            BangumiSpanCard(
                ref='BES_SPECIAL',
                subject_ref='BS1',
                target_refs=special_refs,
                target_ref_count=len(special_refs),
                target_ref_samples=special_refs[:3],
                item_kind='special',
                sort_start=0,
                sort_end=5,
                detail_equivalent=True,
                source_request_ref='REQ_TARGET_SPAN_LS_SP',
            )
        ],
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


def test_target_absent_is_allowed_when_only_same_count_sequence_is_visible():
    file_refs = [f'LF{i}' for i in range(1, 7)]
    special_refs = [f'BE{i}' for i in range(1, 7)]
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-TARGET-ABSENT-VISIBLE-SEQUENCE'),
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
                subject_refs=['BS1'],
            )
        ]),
    )
    args = ProposeMappingIntentsToolArgs(
        reason='agent judges same-count visible SP sequence is not the row target',
        mapping_intents=[
            MappingIntent(
                intent_ref='I1',
                decision='mark_non_bangumi_or_supplemental',
                local_ref='LS_SP',
                row_ref='MDR1',
                reason_kind='bangumi_target_absent',
                subject_refs=['BS1'],
                support_refs=['LS_SP'],
                reason='after investigation the visible same-count sequence is not the corresponding target',
            )
        ],
    )

    _updated, observation = _run_orchestrator_propose_mapping_intents_tool(workspace, args)

    assert observation['status'] == 'ok'
    assert 'bangumi_target_absent_has_visible_sequence' not in observation['patch_issue_codes']
    assert observation['draft_accounting']['unresolved_count'] == 0


def test_supplemental_is_allowed_with_visible_candidates_when_agent_decides_supplemental():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-SUPPLEMENTAL-CANDIDATES'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']),
        local_files=[
            LocalFileCard(ref='LF1', path='Title Special.mkv', is_main=True, file_kind='video')
        ],
        local_span_cards=[
            LocalSpanCard(
                ref='LS1',
                span_scope='token_segment',
                file_refs=['LF1'],
                file_ref_count=1,
                file_ref_samples=['LF1'],
                title_cues=['Title Special'],
            )
        ],
        bangumi_items=[
            BangumiItemCard(ref='BE1', subject_ref='BS1', item_kind='special', sort=1, title='Special')
        ],
        mapping_draft=MappingDraft(rows=[
            MappingDraftRow(
                row_ref='MDR1',
                local_ref='LS1',
                local_ref_kind='span',
                candidate_target_refs=['BE1'],
                subject_refs=['BS1'],
            )
        ]),
    )
    args = ProposeMappingIntentsToolArgs(
        reason='close as supplemental without resolving candidate',
        mapping_intents=[
            MappingIntent(
                intent_ref='I1',
                decision='mark_non_bangumi_or_supplemental',
                local_ref='LS1',
                row_ref='MDR1',
                reason_kind='other_supplemental',
                support_refs=['LS1'],
                reason='not part of Bangumi mapping',
            )
        ],
    )

    _updated, observation = _run_orchestrator_propose_mapping_intents_tool(workspace, args)

    assert observation['status'] == 'ok'
    assert 'supplemental_has_unrejected_candidates' not in observation['patch_issue_codes']
    assert observation['draft_accounting']['unresolved_count'] == 0


def test_open_row_sequences_do_not_fallback_to_unrelated_subjects_when_row_has_pending_evidence():
    file_refs = [f'LF{i}' for i in range(1, 19)]
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-ROW-SURFACE-BOUNDARY'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=file_refs, allowed_file_refs=file_refs),
        local_files=[
            LocalFileCard(ref=ref, path=f'Title CM{i:02d}.mkv', is_main=True, file_kind='video')
            for i, ref in enumerate(file_refs, start=1)
        ],
        local_span_cards=[
            LocalSpanCard(
                ref='LS_CM',
                span_scope='residual',
                file_refs=file_refs,
                file_ref_count=len(file_refs),
                file_ref_samples=file_refs[:3],
                title_cues=['Title'],
            )
        ],
        bangumi_subjects=[
            BangumiSubjectCard(ref='BS_UNRELATED', title='Unrelated 18 Episode Show')
        ],
        bangumi_items=[
            BangumiItemCard(ref=f'BE{i}', subject_ref='BS_UNRELATED', item_kind='episode', sort=i, ep=i)
            for i in range(1, 19)
        ],
        mapping_draft=MappingDraft(rows=[
            MappingDraftRow(
                row_ref='MDR1',
                local_ref='LS_CM',
                local_ref_kind='span',
                disposition='needs_more_evidence',
                status='unresolved',
                requested_request_types=['episode_list'],
                query_hints=['Title CM'],
            )
        ]),
    )

    payload = json.loads(build_orchestrator_agent_input(workspace, reason='inspect pending row'))
    row = payload['open_rows_requiring_agent_action'][0]

    assert row['visible_subject_item_sequences'] == []


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
            QueryCandidate(query_text='Use title-preserving aliases; avoid codec/group/resolution terms.', source_refs=['LS1'], reason='instruction, not a title', confidence='low'),
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
    assert 'Use title-preserving aliases; avoid codec/group/resolution terms.' not in query_texts
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


def test_terminal_fail_closed_rows_hide_finish_until_fail_closed_preconditions_hold():
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
    assert 'finish_case' not in tool_names
    assert 'propose_mapping_intents' in tool_names
    assert 'mark_non_bangumi_or_supplemental(reason_kind=bangumi_target_absent)' in prompt
    assert 'will keep accounting unresolved' in prompt
    assert 'reject_candidate' in prompt


def test_reopen_mapping_draft_issue_rows_keeps_unrelated_completed_rows_closed():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-REOPEN-MINIMAL'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=['LF1', 'LF2', 'LF3'], allowed_file_refs=['LF1', 'LF2', 'LF3']),
        local_files=[
            LocalFileCard(ref='LF1', path='Title 01.mkv', is_main=True),
            LocalFileCard(ref='LF2', path='Title 02.mkv', is_main=True),
            LocalFileCard(ref='LF3', path='Title Extra.mkv', is_main=True),
        ],
        local_span_cards=[
            LocalSpanCard(ref='LS1', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1']),
            LocalSpanCard(ref='LS2', file_refs=['LF2'], file_ref_count=1, file_ref_samples=['LF2']),
            LocalSpanCard(ref='LS3', file_refs=['LF3'], file_ref_count=1, file_ref_samples=['LF3']),
        ],
        bangumi_items=[
            BangumiItemCard(ref='BE1', subject_ref='BS1', item_kind='episode', ep=1, sort=1),
        ],
        mapping_draft=MappingDraft(rows=[
            MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span', disposition='map_to_bangumi', status='proposed', selected_target_ref='BE1', selected_target_kind='item', mapping_mode='explicit', support_refs=['LS1', 'BE1']),
            MappingDraftRow(row_ref='MDR2', local_ref='LS2', local_ref_kind='span', disposition='map_to_bangumi', status='proposed', selected_target_ref='BE1', selected_target_kind='item', mapping_mode='explicit', support_refs=['LS2', 'BE1']),
            MappingDraftRow(row_ref='MDR3', local_ref='LS3', local_ref_kind='span', disposition='non_bangumi_or_supplemental', status='proposed', reason_kind='other_supplemental', support_refs=['LS3']),
        ]),
    )
    issue = VerifierIssue(
        ref='MDR2',
        issue_code='duplicate_target',
        severity='blocked',
        message='duplicate mapped target',
        related_refs=['MDR1', 'MDR2', 'BE1'],
    )

    updated = _reopen_mapping_draft_issue_rows(workspace, [issue])
    rows = {row.row_ref: row for row in updated.mapping_draft.rows}

    assert rows['MDR1'].disposition == 'map_to_bangumi'
    assert rows['MDR1'].selected_target_ref == 'BE1'
    assert rows['MDR2'].disposition == 'open'
    assert rows['MDR2'].selected_target_ref == ''
    assert rows['MDR3'].disposition == 'non_bangumi_or_supplemental'


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
