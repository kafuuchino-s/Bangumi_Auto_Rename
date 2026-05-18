from __future__ import annotations

import json
from dataclasses import replace

import pytest

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
    EvidenceBatchResult,
    EvidenceRequestResult,
    CaseJudgeOutput,
    CaseResolutionLedger,
    CaseResolutionLedgerRow,
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
    QueryCard,
    SplitCaseSpec,
    CaseVerifierResult,
    VerifierIssue,
)
from src.rename.case_agent.orchestrator import CaseAgentRunResult, run_local_bangumi_case_agent as run_primary_local_bangumi_case_agent, _compact_final_assignment_support_refs, _compile_case_understanding, _default_orchestrator_max_turns_for_workspace, _prepare_workspace_for_orchestrator_agent_turn, _reopen_mapping_draft_issue_rows, _run_orchestrator_agent_main_loop, _run_orchestrator_execute_evidence_tool, _run_orchestrator_materialize_queries_tool, _run_orchestrator_propose_case_resolution_ledger_tool, _run_orchestrator_propose_mapping_intents_tool, _run_orchestrator_reconsider_split_tool, _run_orchestrator_split_into_child_cases_tool
from src.rename.case_agent.orchestrator import _decision_from_orchestrator_tool_call
from src.rename.case_agent.orchestrator import _refresh_mapping_draft_candidates
from src.rename.case_agent.orchestrator_agent import (
    ExecuteEvidenceToolArgs,
    FinishCaseToolArgs,
    MaterializeQueriesToolArgs,
    OrchestratorAgentSession,
    OrchestratorAgentToolCall,
    ProposeMappingIntentsToolArgs,
    ProposeCaseResolutionLedgerToolArgs,
    ProposeCaseUnderstandingToolArgs,
    ReconsiderSplitToolArgs,
    SplitIntoChildCasesToolArgs,
    UpdateNotebookToolArgs,
    build_orchestrator_agent_input,
    build_orchestrator_agent_stable_prefix,
    build_orchestrator_agent_turn_tail,
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


def run_local_bangumi_case_agent(
    initial_workspace,
    ai_client,
    bangumi_client,
    *,
    planning_output=None,
    planning_evidence_batches=None,
    max_rounds=None,
    orchestrator_context_soft_token_limit=None,
    orchestrator_context_hard_token_limit=None,
    planning_depth=None,
    _planning_depth=0,
    allow_legacy_orchestrator_helper=True,
):
    """Exercise the legacy OrchestratorAgent helper in this test module.

    The production entry point is now HumanCaseAgent; these tests still cover
    the retained OrchestratorAgent helper/tool boundary without making it a
    product fallback path.
    """
    return _run_orchestrator_agent_main_loop(
        initial_workspace,
        ai_client,
        bangumi_client,
        planning_output=None,
        planning_evidence_batches=[],
        max_rounds=max_rounds,
        orchestrator_context_soft_token_limit=orchestrator_context_soft_token_limit,
        orchestrator_context_hard_token_limit=orchestrator_context_hard_token_limit,
        planning_depth=_planning_depth if planning_depth is None else planning_depth,
        allow_legacy_orchestrator_helper=allow_legacy_orchestrator_helper,
    )


def test_primary_entry_uses_human_case_agent_without_orchestrator_fallback():
    class NoToolClient:
        pass

    result = run_primary_local_bangumi_case_agent(_mapping_workspace(), NoToolClient(), object(), max_rounds=1)

    assert result.status == 'error'
    assert result.final_action == 'human_case_agent'
    assert any('human_case_agent_transport_unavailable' in item for item in result.errors)


def test_legacy_orchestrator_loop_is_explicitly_blocked_outside_legacy_harness():
    result = _run_orchestrator_agent_main_loop(
        _mapping_workspace(),
        object(),
        object(),
        planning_output=None,
        planning_evidence_batches=[],
        max_rounds=1,
        orchestrator_context_soft_token_limit=None,
        orchestrator_context_hard_token_limit=None,
    )

    assert result.status == 'error'
    assert result.final_verifier_result is not None
    assert any(issue.issue_code == 'legacy_orchestrator_helper_blocked' for issue in result.final_verifier_result.issues)
    assert any(
        isinstance(audit, dict) and audit.get('note') == 'legacy_orchestrator_helper_blocked'
        for audit in result.final_workspace.judge_request_audits
    )


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


def _accepted_finish_args(
    *,
    reason: str = 'accounting ready',
    mapped: int = 1,
    excluded: int = 0,
    open_count: int = 0,
    unresolved: int = 0,
    row_ref: str = 'MDR1',
    local_ref: str = 'LS1',
    outcome_kind: str = 'mapped',
    file_count: int | None = None,
    support_refs: list[str] | None = None,
) -> dict[str, object]:
    return {
        'status': 'accepted',
        'finish_kind': 'accepted',
        'reason': reason,
        'reviewed_outcome_projection': True,
        'acknowledged_mapped_file_count': mapped,
        'acknowledged_excluded_file_count': excluded,
        'acknowledged_open_file_count': open_count,
        'acknowledged_unresolved_count': unresolved,
        'work_unit_reviews': [
            {
                'row_ref': row_ref,
                'local_ref': local_ref,
                'outcome_kind': outcome_kind,
                'file_count': mapped + excluded if file_count is None else file_count,
                'support_refs': support_refs if support_refs is not None else [local_ref],
                'reason': 'test agent reviewed the current global outcome projection',
            }
        ],
        'final_case_review': 'Test agent reviewed mapped/excluded/open/unresolved counts and every current draft row before accepted finish.',
    }


def _large_target_absent_workspace() -> CaseEvidenceWorkspace:
    file_refs = [f'LF{i}' for i in range(1, 5)]
    span_refs = [f'LS{i}' for i in range(1, 5)]
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-LARGE-ABSENT'),
        budget=CaseBudget(max_judge_rounds=4, max_evidence_batches=2, max_requests_per_batch=4),
        contract=CaseContract(main_file_refs=file_refs, allowed_file_refs=file_refs, visible_target_refs=[]),
        local_files=[
            LocalFileCard(ref=file_ref, path=f'Package/Title {index:02d}.mkv', is_main=True, label=f'Title {index:02d}.mkv')
            for index, file_ref in enumerate(file_refs, start=1)
        ],
        local_span_cards=[
            LocalSpanCard(
                ref=span_ref,
                span_scope='token_segment',
                file_refs=[file_ref],
                file_ref_count=1,
                file_ref_samples=[file_ref],
                episode_token_start=index,
                episode_token_end=index,
                episode_token_count=1,
            )
            for index, (span_ref, file_ref) in enumerate(zip(span_refs, file_refs, strict=True), start=1)
        ],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', title='Title')],
        case_briefing=CaseBriefingOutput(
            package_shape='large mixed package',
            work_units=[
                CaseBriefingWorkUnit(
                    work_unit_ref=f'WU{i}',
                    label=f'unit {i}',
                    local_refs=[span_ref],
                    file_refs=[file_ref],
                    span_refs=[span_ref],
                )
                for i, (span_ref, file_ref) in enumerate(zip(span_refs, file_refs, strict=True), start=1)
            ],
        ),
    )
    object.__setattr__(workspace, 'mapping_draft', MappingDraft(rows=[
        MappingDraftRow(
            row_ref=f'MDR{i}',
            local_ref=span_ref,
            local_ref_kind='span',
            disposition='non_bangumi_or_supplemental',
            status='verified',
            reason_kind='bangumi_target_absent',
            support_refs=[span_ref, 'BS1'],
            reason='agent marked target absent after target-side review',
        )
        for i, span_ref in enumerate(span_refs, start=1)
    ], version=2))
    return workspace


def _accepted_finish_args_for_large_target_absent() -> dict[str, object]:
    return {
        'status': 'accepted',
        'finish_kind': 'accepted',
        'reason': 'all rows reviewed as target absent',
        'reviewed_outcome_projection': True,
        'acknowledged_mapped_file_count': 0,
        'acknowledged_excluded_file_count': 4,
        'acknowledged_open_file_count': 0,
        'acknowledged_unresolved_count': 0,
        'work_unit_reviews': [
            {
                'row_ref': f'MDR{i}',
                'local_ref': f'LS{i}',
                'outcome_kind': 'target_absent',
                'file_count': 1,
                'support_refs': [f'LS{i}', 'BS1'],
                'reason': 'test agent reviewed this row outcome',
            }
            for i in range(1, 5)
        ],
        'final_case_review': 'Test agent reviewed the all-unmapped outcome projection for every current draft row.',
    }


def test_payload_exposes_full_public_tool_set_without_phase_gating():
    public_tools = {tool['function']['name'] for tool in orchestrator_tool_definitions()}
    payload = json.loads(build_orchestrator_agent_input(_workspace(), reason='first turn'))

    assert set(payload['available_tool_names']) == public_tools
    assert payload['case_understanding']['recommended_before_mapping_when_missing'] is True
    assert 'required_before_other_tools' not in payload['case_understanding']


def test_payload_keeps_public_tools_visible_after_evidence_budget_exhausted():
    workspace = _mapping_workspace()
    object.__setattr__(
        workspace,
        'budget',
        CaseBudget(max_evidence_batches=1, used_evidence_batches=1, max_requests_per_batch=2),
    )
    public_tools = {tool['function']['name'] for tool in orchestrator_tool_definitions()}
    payload = json.loads(build_orchestrator_agent_input(workspace, reason='budget exhausted'))

    assert set(payload['available_tool_names']) == public_tools
    assert 'execute_evidence' in payload['available_tool_names']


def test_first_turn_non_understanding_tool_is_not_phase_rejected():
    tool_call = OrchestratorAgentToolCall(
        tool_name='materialize_queries',
        arguments=MaterializeQueriesToolArgs(
            reason='try query first',
            queries=[
                QueryCandidate(query_text='Title', source_refs=['LF1'])
            ],
        ),
        raw_arguments={'reason': 'try query first'},
        call_id='call_query_first',
    )

    _workspace_after, decision, acceptance = _decision_from_orchestrator_tool_call(_workspace(), tool_call)

    assert decision is not None
    assert decision.action == 'compose_queries'
    assert acceptance['accepted'] is True


def test_global_outcome_projection_exposes_agent_current_finish_consequences():
    workspace = _regular_span_workspace()
    object.__setattr__(workspace, 'mapping_draft', MappingDraft(rows=[
        MappingDraftRow(
            row_ref='MDR1',
            local_ref='LS1',
            local_ref_kind='span',
            disposition='non_bangumi_or_supplemental',
            status='verified',
            reason_kind='other_supplemental',
            support_refs=['LS1'],
            reason='agent marked the current row as supplemental',
        ),
    ], version=2))

    payload = json.loads(build_orchestrator_agent_input(workspace, reason='projection check'))
    projection = payload['global_outcome_projection']

    assert projection['draft_accounting']['excluded_file_count'] == 2
    assert projection['excluded_reason_file_counts']['other_supplemental'] == 2
    assert projection['terminal_row_summaries'][0]['row_ref'] == 'MDR1'
    assert projection['terminal_row_summaries'][0]['file_count'] == 2
    assert 'majority_of_main_files_are_accepted_exclusions' in projection['review_flags']


def test_work_unit_resolution_board_combines_rows_targets_ownership_and_agenda():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-BOARD'),
        budget=CaseBudget(max_requests_per_batch=8),
        contract=CaseContract(main_file_refs=['LF1', 'LF2'], allowed_file_refs=['LF1', 'LF2'], visible_target_refs=['BE1']),
        local_files=[
            LocalFileCard(ref='LF1', path='Title 01.mkv', label='Title 01.mkv', is_main=True),
            LocalFileCard(ref='LF2', path='Title special.mkv', label='Title special.mkv', is_main=True),
        ],
        local_span_cards=[
            LocalSpanCard(ref='LS1', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1'], title_cues=['Title']),
            LocalSpanCard(ref='LS2', file_refs=['LF2'], file_ref_count=1, file_ref_samples=['LF2'], title_cues=['Title special']),
        ],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', title='Title')],
        bangumi_items=[BangumiItemCard(ref='BE1', subject_ref='BS1', item_kind='episode', sort=1, ep=1, title='Episode 1')],
        case_briefing=CaseBriefingOutput(package_shape='mixed rows'),
    )
    object.__setattr__(workspace, 'mapping_draft', MappingDraft(rows=[
        MappingDraftRow(
            row_ref='MDR1',
            local_ref='LS1',
            local_ref_kind='span',
            disposition='map_to_bangumi',
            status='verified',
            selected_target_ref='BE1',
            mapping_mode='explicit',
            support_refs=['LS1', 'BE1'],
        ),
        MappingDraftRow(
            row_ref='MDR2',
            local_ref='LS2',
            local_ref_kind='span',
            disposition='needs_more_evidence',
            status='unresolved',
            candidate_target_refs=['BE1'],
            subject_refs=['BS1'],
            requested_request_types=['episode_list'],
        ),
    ]))
    object.__setattr__(workspace, 'judge_request_audits', [
        {
            'note': 'orchestrator_mapping_intents_result',
            'status': 'blocked_intents',
            'requested_evidence': ['episode_list'],
            'blocked_intents': [
                {
                    'intent_ref': 'MI2',
                    'row_ref': 'MDR2',
                    'local_ref': 'LS2',
                    'decision': 'needs_more_evidence',
                    'issue_codes': ['target_span_or_item_not_visible'],
                    'requested_request_types': ['episode_list'],
                    'subject_refs': ['BS1'],
                }
            ],
        }
    ])

    payload = json.loads(build_orchestrator_agent_input(workspace, reason='board check'))
    board = payload['work_unit_resolution_board']
    rows = {row['row_ref']: row for row in board['rows']}

    assert board['has_mapping_draft'] is True
    assert rows['MDR1']['current_outcome_kind'] == 'mapped'
    assert rows['MDR1']['selected_target_brief']['ref_kind'] == 'BE_item'
    assert rows['MDR2']['current_outcome_kind'] == 'needs_more_evidence'
    assert rows['MDR2']['target_ownership_conflicts'][0]['owner_row_refs'] == ['MDR1']
    assert 'REQ_EPISODE_LIST_BS1' in rows['MDR2']['latest_blocked_evidence_agenda_rows'][0]['matching_executable_request_ids']
    assert 'execute_evidence_with_matching_request_ids' in rows['MDR2']['recommended_next_actions']


def test_ref_rejection_returns_focused_work_unit_board_rows():
    workspace = _mapping_workspace()
    tool_call = OrchestratorAgentToolCall(
        tool_name='propose_mapping_intents',
        arguments=ProposeMappingIntentsToolArgs(
            reason='bad namespace',
            mapping_intents=[
                MappingIntent(
                    decision='map_explicit_item',
                    local_ref='LS1',
                    chosen_item_ref='LS1',
                    support_refs=['LS1'],
                )
            ],
        ),
        raw_arguments={
            'reason': 'bad namespace',
            'mapping_intents': [
                {
                    'decision': 'map_explicit_item',
                    'local_ref': 'LS1',
                    'chosen_item_ref': 'LS1',
                    'support_refs': ['LS1'],
                }
            ],
        },
        call_id='call_bad_ref',
    )

    _workspace_after, decision, acceptance = _decision_from_orchestrator_tool_call(workspace, tool_call)

    assert decision is None
    assert acceptance['reason'] == 'hidden_or_unknown_refs'
    focus = acceptance['work_unit_resolution_board_focus']
    assert focus['rows']
    assert focus['rows'][0]['local_ref'] == 'LS1'
    assert focus['rows'][0]['ref_namespace_reminder']


def test_finish_case_accepted_requires_agent_outcome_projection_review():
    workspace = _mapping_workspace()
    object.__setattr__(workspace, 'mapping_draft', MappingDraft(rows=[
        MappingDraftRow(
            row_ref='MDR1',
            local_ref='LS1',
            local_ref_kind='span',
            disposition='map_to_bangumi',
            status='verified',
            selected_target_ref='BE1',
            support_refs=['LS1', 'BE1'],
        ),
    ], version=2))
    tool_call = OrchestratorAgentToolCall(
        tool_name='finish_case',
        arguments=FinishCaseToolArgs(status='accepted', finish_kind='accepted', reason='accounting ready'),
        raw_arguments={'status': 'accepted', 'finish_kind': 'accepted', 'reason': 'accounting ready'},
        call_id='call_finish',
    )

    _workspace_after, decision, acceptance = _decision_from_orchestrator_tool_call(workspace, tool_call)

    assert decision is None
    assert acceptance['accepted'] is False
    assert acceptance['reason'] == 'finish_case_review_required'
    assert 'finish_review_projection_not_acknowledged' in acceptance['review_issue_codes']
    assert acceptance['global_outcome_projection']['draft_accounting']['mapped_file_count'] == 1
    template = acceptance['finish_case_review_template']
    assert template['acknowledged_mapped_file_count'] == 1
    assert template['acknowledged_excluded_file_count'] == 0
    assert template['work_unit_reviews'][0]['row_ref'] == 'MDR1'
    assert template['work_unit_reviews'][0]['local_ref'] == 'LS1'
    assert template['work_unit_reviews'][0]['outcome_kind'] == 'mapped'
    assert 'BE1' in template['work_unit_reviews'][0]['support_refs']


def test_finish_case_accepted_with_matching_projection_review_is_accepted():
    workspace = _mapping_workspace()
    object.__setattr__(workspace, 'mapping_draft', MappingDraft(rows=[
        MappingDraftRow(
            row_ref='MDR1',
            local_ref='LS1',
            local_ref_kind='span',
            disposition='map_to_bangumi',
            status='verified',
            selected_target_ref='BE1',
            support_refs=['LS1', 'BE1'],
        ),
    ], version=2))
    args = FinishCaseToolArgs.model_validate(
        _accepted_finish_args(reason='accounting ready', mapped=1, excluded=0, outcome_kind='mapped', file_count=1, support_refs=['LS1', 'BE1'])
    )
    tool_call = OrchestratorAgentToolCall(
        tool_name='finish_case',
        arguments=args,
        raw_arguments=args.model_dump(mode='json'),
        call_id='call_finish',
    )

    _workspace_after, decision, acceptance = _decision_from_orchestrator_tool_call(workspace, tool_call)

    assert decision is not None
    assert decision.action == 'accepted'
    assert acceptance['accepted'] is True
    assert acceptance['finish_review_present'] is True


def test_finish_case_accepted_missing_finish_kind_is_mechanically_defaulted():
    workspace = _mapping_workspace()
    object.__setattr__(workspace, 'mapping_draft', MappingDraft(rows=[
        MappingDraftRow(
            row_ref='MDR1',
            local_ref='LS1',
            local_ref_kind='span',
            disposition='map_to_bangumi',
            status='verified',
            selected_target_ref='BE1',
            support_refs=['LS1', 'BE1'],
        ),
    ], version=2))
    raw_args = _accepted_finish_args(
        reason='accounting ready without explicit finish_kind',
        mapped=1,
        excluded=0,
        outcome_kind='mapped',
        file_count=1,
        support_refs=['LS1', 'BE1'],
    )
    raw_args.pop('finish_kind')
    args = FinishCaseToolArgs.model_validate(raw_args)
    assert args.finish_kind == 'no_new_evidence'
    tool_call = OrchestratorAgentToolCall(
        tool_name='finish_case',
        arguments=args,
        raw_arguments=raw_args,
        call_id='call_finish_default_kind',
    )

    _workspace_after, decision, acceptance = _decision_from_orchestrator_tool_call(workspace, tool_call)

    assert decision is not None
    assert decision.action == 'accepted'
    assert acceptance['accepted'] is True
    assert acceptance['finish_kind'] == 'accepted'
    assert acceptance['finish_kind_defaulted_to_accepted'] is True


def test_finish_case_accepted_rejects_large_all_unmapped_projection_without_ledger():
    workspace = _large_target_absent_workspace()
    args = FinishCaseToolArgs.model_validate(_accepted_finish_args_for_large_target_absent())
    tool_call = OrchestratorAgentToolCall(
        tool_name='finish_case',
        arguments=args,
        raw_arguments=args.model_dump(mode='json'),
        call_id='call_finish',
    )

    _workspace_after, decision, acceptance = _decision_from_orchestrator_tool_call(workspace, tool_call)

    assert decision is None
    assert acceptance['accepted'] is False
    assert acceptance['reason'] == 'finish_case_review_required'
    assert 'finish_review_large_unmapped_projection_requires_ledger' in acceptance['review_issue_codes']


def test_finish_case_accepted_rejection_exposes_unresolved_ledger_rows():
    workspace = _large_target_absent_workspace()
    object.__setattr__(workspace, 'case_resolution_ledger', CaseResolutionLedger(rows=[
        CaseResolutionLedgerRow(
            ledger_row_ref='CRLR1',
            row_ref='MDR1',
            local_ref='LS1',
            outcome='needs_evidence',
            requested_request_types=['episode_list'],
            query_hints=['Title'],
            subject_refs=['BS1'],
            support_refs=['LS1', 'BS1'],
            reason='agent still needs target surface for this row',
        ),
        CaseResolutionLedgerRow(
            ledger_row_ref='CRLR2',
            row_ref='MDR2',
            local_ref='LS2',
            outcome='target_absent',
            support_refs=['LS2', 'BS1'],
            subject_refs=['BS1'],
            reason='agent reviewed target absence',
        ),
        CaseResolutionLedgerRow(
            ledger_row_ref='CRLR3',
            row_ref='MDR3',
            local_ref='LS3',
            outcome='target_absent',
            support_refs=['LS3', 'BS1'],
            subject_refs=['BS1'],
            reason='agent reviewed target absence',
        ),
        CaseResolutionLedgerRow(
            ledger_row_ref='CRLR4',
            row_ref='MDR4',
            local_ref='LS4',
            outcome='target_absent',
            support_refs=['LS4', 'BS1'],
            subject_refs=['BS1'],
            reason='agent reviewed target absence',
        ),
    ]))
    args = FinishCaseToolArgs.model_validate(_accepted_finish_args_for_large_target_absent())
    tool_call = OrchestratorAgentToolCall(
        tool_name='finish_case',
        arguments=args,
        raw_arguments=args.model_dump(mode='json'),
        call_id='call_finish',
    )

    _workspace_after, decision, acceptance = _decision_from_orchestrator_tool_call(workspace, tool_call)

    assert decision is None
    assert acceptance['accepted'] is False
    assert 'finish_review_unresolved_ledger_outcomes' in acceptance['review_issue_codes']
    assert acceptance['unresolved_case_resolution_ledger_row_count'] == 1
    assert acceptance['unresolved_case_resolution_ledger_rows'][0]['ledger_row_ref'] == 'CRLR1'
    assert acceptance['unresolved_case_resolution_ledger_rows'][0]['requested_request_types'] == ['episode_list']
    assert acceptance['unresolved_case_resolution_ledger_rows'][0]['current_draft_outcome_kind'] == 'target_absent'
    assert acceptance['unresolved_case_resolution_ledger_rows'][0]['ledger_stale_against_terminal_draft'] is True
    assert acceptance['stale_unresolved_ledger_row_count'] == 1
    assert acceptance['case_resolution_ledger_sync_observation']['stale_unresolved_row_count'] == 1
    assert acceptance['work_unit_resolution_board_focus']['rows'][0]['local_ref'] == 'LS1'
    assert 'mechanical provenance check' in acceptance['recommended_next_observation']


def test_finish_case_accepted_rejects_unresolved_ledger_for_multi_row_package_even_when_not_mostly_unmapped():
    workspace = _large_target_absent_workspace()
    rows = list(workspace.mapping_draft.rows)
    rows[0] = rows[0].model_copy(update={
        'disposition': 'map_to_bangumi',
        'selected_target_ref': 'BS1',
        'selected_target_kind': 'subject',
        'mapping_mode': 'span_by_index',
        'reason_kind': '',
    })
    rows[1] = rows[1].model_copy(update={
        'disposition': 'map_to_bangumi',
        'selected_target_ref': 'BS1',
        'selected_target_kind': 'subject',
        'mapping_mode': 'span_by_index',
        'reason_kind': '',
    })
    object.__setattr__(workspace, 'mapping_draft', workspace.mapping_draft.model_copy(update={'rows': rows, 'version': 3}))
    object.__setattr__(workspace, 'case_resolution_ledger', CaseResolutionLedger(rows=[
        CaseResolutionLedgerRow(ledger_row_ref='CRLR1', row_ref='MDR1', local_ref='LS1', outcome='map_to_bangumi', support_refs=['LS1', 'BS1'], subject_refs=['BS1']),
        CaseResolutionLedgerRow(ledger_row_ref='CRLR2', row_ref='MDR2', local_ref='LS2', outcome='needs_evidence', support_refs=['LS2', 'BS1'], subject_refs=['BS1']),
        CaseResolutionLedgerRow(ledger_row_ref='CRLR3', row_ref='MDR3', local_ref='LS3', outcome='target_absent', support_refs=['LS3', 'BS1'], subject_refs=['BS1']),
        CaseResolutionLedgerRow(ledger_row_ref='CRLR4', row_ref='MDR4', local_ref='LS4', outcome='target_absent', support_refs=['LS4', 'BS1'], subject_refs=['BS1']),
    ]))
    finish_args = _accepted_finish_args_for_large_target_absent()
    finish_args['acknowledged_mapped_file_count'] = 2
    finish_args['acknowledged_excluded_file_count'] = 2
    finish_args['work_unit_reviews'][0]['outcome_kind'] = 'mapped'
    finish_args['work_unit_reviews'][1]['outcome_kind'] = 'mapped'
    args = FinishCaseToolArgs.model_validate(finish_args)
    tool_call = OrchestratorAgentToolCall(
        tool_name='finish_case',
        arguments=args,
        raw_arguments=args.model_dump(mode='json'),
        call_id='call_finish_multirow_stale_ledger',
    )

    _workspace_after, decision, acceptance = _decision_from_orchestrator_tool_call(workspace, tool_call)

    assert decision is None
    assert acceptance['accepted'] is False
    assert 'finish_review_unresolved_ledger_outcomes' in acceptance['review_issue_codes']
    assert acceptance['unresolved_case_resolution_ledger_rows'][0]['ledger_row_ref'] == 'CRLR2'
    assert acceptance['unresolved_case_resolution_ledger_rows'][0]['current_draft_outcome_kind'] == 'mapped'
    assert acceptance['stale_unresolved_ledger_row_count'] == 1


def test_mapping_intents_mechanically_sync_terminal_outcome_to_existing_ledger():
    workspace = _large_target_absent_workspace()
    rows = list(workspace.mapping_draft.rows)
    rows[1] = rows[1].model_copy(update={
        'disposition': 'open',
        'status': 'open',
        'reason_kind': '',
        'support_refs': [],
    })
    object.__setattr__(workspace, 'mapping_draft', workspace.mapping_draft.model_copy(update={'rows': rows, 'version': 3}))
    object.__setattr__(workspace, 'case_resolution_ledger', CaseResolutionLedger(rows=[
        CaseResolutionLedgerRow(ledger_row_ref='CRLR1', row_ref='MDR1', local_ref='LS1', outcome='target_absent', support_refs=['LS1', 'BS1'], subject_refs=['BS1']),
        CaseResolutionLedgerRow(ledger_row_ref='CRLR2', row_ref='MDR2', local_ref='LS2', outcome='needs_evidence', support_refs=['LS2', 'BS1'], subject_refs=['BS1']),
        CaseResolutionLedgerRow(ledger_row_ref='CRLR3', row_ref='MDR3', local_ref='LS3', outcome='target_absent', support_refs=['LS3', 'BS1'], subject_refs=['BS1']),
        CaseResolutionLedgerRow(ledger_row_ref='CRLR4', row_ref='MDR4', local_ref='LS4', outcome='target_absent', support_refs=['LS4', 'BS1'], subject_refs=['BS1']),
    ]))

    workspace, observation = _run_orchestrator_propose_mapping_intents_tool(
        workspace,
        ProposeMappingIntentsToolArgs(
            reason='agent terminally resolved the ledger-stale row',
            mapping_intents=[
                MappingIntent(
                    intent_ref='INT_SYNC',
                    decision='mark_non_bangumi_or_supplemental',
                    row_ref='MDR2',
                    local_ref='LS2',
                    reason_kind='bangumi_target_absent',
                    support_refs=['LS2', 'BS1'],
                    reason='agent concluded this row has no Bangumi target',
                )
            ],
        ),
    )

    assert observation['ledger_synced_row_count'] == 1
    assert observation['ledger_synced_rows'][0]['ledger_row_ref'] == 'CRLR2'
    rows_by_ref = {row.ledger_row_ref: row for row in workspace.case_resolution_ledger.rows}
    assert rows_by_ref['CRLR2'].outcome == 'target_absent'
    assert rows_by_ref['CRLR2'].reason_kind == 'bangumi_target_absent'


def test_repeated_finish_case_review_rejection_is_marked_noop():
    from src.rename.case_agent.orchestrator import _workspace_with_judge_audit

    workspace = _large_target_absent_workspace()
    object.__setattr__(workspace, 'case_resolution_ledger', CaseResolutionLedger(rows=[
        CaseResolutionLedgerRow(
            ledger_row_ref='CRLR1',
            row_ref='MDR1',
            local_ref='LS1',
            outcome='needs_evidence',
            support_refs=['LS1', 'BS1'],
        ),
        CaseResolutionLedgerRow(ledger_row_ref='CRLR2', row_ref='MDR2', local_ref='LS2', outcome='target_absent', support_refs=['LS2', 'BS1']),
        CaseResolutionLedgerRow(ledger_row_ref='CRLR3', row_ref='MDR3', local_ref='LS3', outcome='target_absent', support_refs=['LS3', 'BS1']),
        CaseResolutionLedgerRow(ledger_row_ref='CRLR4', row_ref='MDR4', local_ref='LS4', outcome='target_absent', support_refs=['LS4', 'BS1']),
    ]))
    workspace = _workspace_with_judge_audit(workspace, {
        'note': 'orchestrator_tool_selected',
        'tool_name': 'finish_case',
        'accepted': False,
        'reason': 'finish_case_review_required',
    })
    args = FinishCaseToolArgs.model_validate(_accepted_finish_args_for_large_target_absent())
    tool_call = OrchestratorAgentToolCall(
        tool_name='finish_case',
        arguments=args,
        raw_arguments=args.model_dump(mode='json'),
        call_id='call_finish_retry',
    )

    _workspace_after, decision, acceptance = _decision_from_orchestrator_tool_call(workspace, tool_call)

    assert decision is None
    assert acceptance['finish_retry_is_noop'] is True
    assert acceptance['previous_finish_review_rejection_count'] == 1
    assert acceptance['current_finish_review_rejection_count'] == 2


def test_finish_case_accepted_all_unmapped_projection_allowed_with_valid_ledger():
    workspace = _large_target_absent_workspace()
    ledger_args = ProposeCaseResolutionLedgerToolArgs(
        reason='agent wrote package-level target_absent ledger',
        summary='all rows reviewed as target absent with visible target-side support',
        ledger_rows=[
            CaseResolutionLedgerRow(
                ledger_row_ref=f'CRLR{i}',
                row_ref=f'MDR{i}',
                local_ref=f'LS{i}',
                outcome='target_absent',
                support_refs=[f'LS{i}', 'BS1'],
                subject_refs=['BS1'],
                reason='agent reviewed this work unit and found no matching Bangumi item',
            )
            for i in range(1, 5)
        ],
    )
    workspace, observation = _run_orchestrator_propose_case_resolution_ledger_tool(workspace, ledger_args)
    assert workspace.case_resolution_ledger is not None
    assert observation['case_resolution_ledger_row_count'] == 4
    args = FinishCaseToolArgs.model_validate(_accepted_finish_args_for_large_target_absent())
    tool_call = OrchestratorAgentToolCall(
        tool_name='finish_case',
        arguments=args,
        raw_arguments=args.model_dump(mode='json'),
        call_id='call_finish',
    )

    _workspace_after, decision, acceptance = _decision_from_orchestrator_tool_call(workspace, tool_call)

    assert decision is not None
    assert decision.action == 'accepted'
    assert acceptance['accepted'] is True


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


def _split_root_workspace() -> CaseEvidenceWorkspace:
    file_refs = ['LF1', 'LF2']
    return CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-SPLIT'),
        budget=CaseBudget(max_judge_rounds=4, max_evidence_batches=2, max_requests_per_batch=2),
        contract=CaseContract(main_file_refs=file_refs, allowed_file_refs=file_refs),
        local_files=[
            LocalFileCard(ref='LF1', path='Season 1/Title 01.mkv', is_main=True, label='Title 01.mkv'),
            LocalFileCard(ref='LF2', path='Season 2/Title II 01.mkv', is_main=True, label='Title II 01.mkv'),
        ],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', title='Title')],
        case_briefing=CaseBriefingOutput(
            package_shape='two work units',
            work_units=[
                CaseBriefingWorkUnit(work_unit_ref='WU1', label='season 1', file_refs=['LF1'], local_refs=['LF1']),
                CaseBriefingWorkUnit(work_unit_ref='WU2', label='season 2', file_refs=['LF2'], local_refs=['LF2']),
            ],
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
        near_turn_limit_unhealthy_count=session.near_turn_limit_unhealthy_count,
        stall_suspected_count=session.stall_suspected_count,
        consecutive_stall_count=session.consecutive_stall_count,
        input_token_estimate=session.input_token_estimate,
        output_token_estimate=session.output_token_estimate,
        tool_sequence=list(session.tool_sequence),
        compacted_history_summary=session.compacted_history_summary,
        session_mode=session.session_mode,
        provider_session_enabled=session.provider_session_enabled,
        provider_response_id=session.provider_response_id,
        provider_conversation_id=session.provider_conversation_id,
        http_session_id=session.http_session_id,
        prompt_cache_key=session.prompt_cache_key,
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


def test_case_resolution_ledger_compiles_agent_owned_row_outcomes():
    workspace = _regular_span_workspace()
    args = ProposeCaseResolutionLedgerToolArgs(
        reason='settle regular span from visible items',
        summary='regular span maps to two visible Bangumi items',
        ledger_rows=[
            CaseResolutionLedgerRow(
                ledger_row_ref='CRLR1',
                local_ref='LS1',
                file_refs=['LF1', 'LF2'],
                role='regular_tv',
                outcome='map_to_bangumi',
                chosen_subject_ref='BS1',
                item_refs=['BE1', 'BE2'],
                support_refs=['LS1', 'BS1', 'BE1', 'BE2'],
                reason='same ordered two-episode run',
            )
        ],
    )

    updated, observation = _run_orchestrator_propose_case_resolution_ledger_tool(workspace, args)

    assert observation['status'] == 'ok'
    assert observation['case_resolution_ledger_row_count'] == 1
    assert observation['ledger_compiled_patch_count'] == 1
    assert observation['finish_gate']['accepted_finish_allowed'] is True
    assert updated.case_resolution_ledger is not None
    assert updated.mapping_draft is not None
    assert updated.mapping_draft.rows[0].disposition == 'map_to_bangumi'


def test_case_resolution_ledger_rejects_overlap_without_semantic_repair():
    workspace = _regular_span_workspace()
    args = ProposeCaseResolutionLedgerToolArgs(
        reason='bad overlapping ledger',
        ledger_rows=[
            CaseResolutionLedgerRow(ledger_row_ref='CRLR1', local_ref='LF1', file_refs=['LF1'], outcome='target_absent', support_refs=['LF1'], reason='agent says absent'),
            CaseResolutionLedgerRow(ledger_row_ref='CRLR2', local_ref='LF1', file_refs=['LF1'], outcome='target_absent', support_refs=['LF1'], reason='duplicate coverage'),
        ],
    )

    updated, observation = _run_orchestrator_propose_case_resolution_ledger_tool(workspace, args)

    assert observation['status'] == 'blocked_ledger_rows'
    assert 'ledger_missing_main_refs' in observation['blocked_ledger_issue_codes']
    assert 'ledger_duplicate_main_refs' in observation['blocked_ledger_issue_codes']
    assert updated.case_resolution_ledger is not None
    assert updated.mapping_draft is not None
    assert all(row.disposition == 'open' for row in updated.mapping_draft.rows)
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


def test_case_understanding_ignores_future_ls_refs_when_file_refs_cover_unit():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-FUTURE-LS'),
        budget=CaseBudget(max_judge_rounds=5),
        contract=CaseContract(main_file_refs=['LF1', 'LF2'], allowed_file_refs=['LF1', 'LF2']),
        local_files=[
            LocalFileCard(ref='LF1', path='Title 01.mkv', is_main=True),
            LocalFileCard(ref='LF2', path='Title 02.mkv', is_main=True),
        ],
    )
    args = ProposeCaseUnderstandingToolArgs(
        reason='agent pre-cited spans it expects the compiler to create',
        package_shape='two episodes',
        work_units=[
            CaseBriefingWorkUnit(work_unit_ref='WU1', label='episode 1', local_refs=['LS1'], span_refs=['LS1'], file_refs=['LF1']),
            CaseBriefingWorkUnit(work_unit_ref='WU2', label='episode 2', local_refs=['LS2'], span_refs=['LS2'], file_refs=['LF2']),
        ],
    )

    updated, observation = _compile_case_understanding(workspace, args)

    assert observation['status'] == 'ok'
    assert [row.local_ref for row in updated.mapping_draft.rows] == ['LS1', 'LS2']
    assert updated.case_briefing is not None
    assert [unit.local_refs for unit in updated.case_briefing.work_units] == [['LS1'], ['LS2']]
    assert [unit.span_refs for unit in updated.case_briefing.work_units] == [['LS1'], ['LS2']]


def test_case_understanding_ignores_agent_named_future_local_spans():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-NAMED-FUTURE-LS'),
        budget=CaseBudget(max_judge_rounds=5),
        contract=CaseContract(main_file_refs=['LF1', 'LF2'], allowed_file_refs=['LF1', 'LF2']),
        local_files=[
            LocalFileCard(ref='LF1', path='Title 01.mkv', is_main=True),
            LocalFileCard(ref='LF2', path='Title SP.mkv', is_main=True),
        ],
    )
    args = ProposeCaseUnderstandingToolArgs(
        reason='agent named future local spans',
        package_shape='episode plus extra',
        work_units=[
            CaseBriefingWorkUnit(work_unit_ref='WU_MAIN', label='main episode', local_refs=['LS_MAIN'], file_refs=['LF1']),
            CaseBriefingWorkUnit(work_unit_ref='WU_EXTRA', label='extra', local_refs=['LS_EXTRA'], span_refs=['LS_EXTRA'], file_refs=['LF2']),
        ],
    )

    updated, observation = _compile_case_understanding(workspace, args)

    assert observation['status'] == 'ok'
    assert [row.local_ref for row in updated.mapping_draft.rows] == ['LS1', 'LS2']
    assert updated.case_briefing is not None
    assert [unit.local_refs for unit in updated.case_briefing.work_units] == [['LS1'], ['LS2']]


def test_case_understanding_tool_skips_generic_ref_validation_for_future_spans():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-FUTURE-SPAN-VALIDATION'),
        budget=CaseBudget(max_judge_rounds=5),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1']),
        local_files=[LocalFileCard(ref='LF1', path='Title 01.mkv', is_main=True)],
    )
    tool_call = OrchestratorAgentToolCall(
        tool_name='propose_case_understanding',
        arguments=ProposeCaseUnderstandingToolArgs(
            reason='future span',
            work_units=[CaseBriefingWorkUnit(work_unit_ref='WU1', local_refs=['LS_MAIN'], file_refs=['LF1'])],
        ),
        raw_arguments={'work_units': [{'work_unit_ref': 'WU1', 'local_refs': ['LS_MAIN'], 'file_refs': ['LF1']}]},
        call_id='call_understand',
    )

    _workspace, decision, acceptance = _decision_from_orchestrator_tool_call(workspace, tool_call)

    assert decision is not None
    assert decision.action == 'propose_case_understanding'
    assert acceptance['accepted'] is True


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


def test_case_understanding_partial_repartition_preserves_terminal_rows():
    workspace = _regular_span_workspace()
    object.__setattr__(workspace, 'mapping_draft', MappingDraft(rows=[
        MappingDraftRow(
            row_ref='MDR1',
            local_ref='LF1',
            local_ref_kind='file',
            disposition='map_to_bangumi',
            status='proposed',
            selected_target_ref='BE1',
            selected_target_kind='item',
            mapping_mode='explicit',
            support_refs=['LF1', 'BE1'],
        ),
        MappingDraftRow(
            row_ref='MDR2',
            local_ref='LS1',
            local_ref_kind='span',
            disposition='needs_more_evidence',
            status='unresolved',
        ),
    ], version=2))
    object.__setattr__(workspace, 'judge_request_audits', [
        {'note': 'orchestrator_reconsider_split_observation', 'status': 'ok', 'repartition_requested': True},
    ])
    args = ProposeCaseUnderstandingToolArgs(
        reason='split unresolved row only',
        package_shape='partial repartition',
        work_units=[
            CaseBriefingWorkUnit(work_unit_ref='WU_A', label='file 2', file_refs=['LF2'], title_hints=['Title']),
        ],
    )

    updated, observation = _compile_case_understanding(workspace, args)
    rows = updated.mapping_draft.rows

    assert observation['status'] == 'ok'
    assert observation['case_understanding_revised'] is True
    assert any(row.row_ref == 'MDR1' and row.disposition == 'map_to_bangumi' for row in rows)
    assert any(row.local_ref == 'LS1' and row.disposition == 'open' for row in rows)


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
                        'arguments': json.dumps(_accepted_finish_args(reason='accounting ready', mapped=1, excluded=0, outcome_kind='mapped', file_count=1, support_refs=['LS1', 'BE1'])),
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
    assert not any(
        isinstance(audit, dict)
        and audit.get('note') == 'orchestrator_auto_finish_accepted_after_verified_draft'
        for audit in result.final_workspace.judge_request_audits
    )
    assert any(
        isinstance(audit, dict) and audit.get('note') == 'case_understanding_applied'
        for audit in result.final_workspace.judge_request_audits
    )


def test_target_absent_row_is_not_reopened_by_candidate_refresh():
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
    assert row.disposition == 'non_bangumi_or_supplemental'
    assert row.reason_kind == 'bangumi_target_absent'
    assert row.candidate_target_refs == []


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
    assert 'finish_case' in payload['available_tool_names']


def test_orchestrator_agent_uses_native_tool_call_with_http_history_replay_session():
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
    assert client.calls[0]['conversation_id'] == ''
    assert client.calls[0]['session_id'].startswith('bar_local_bangumi_CASE-ORCH_')
    assert client.calls[0]['prompt_cache_key'] == 'bar:lbg:orchestrator:v8'
    assert result.session.provider_conversation_id == ''
    assert result.session.provider_session_enabled is False
    assert result.session.session_mode == 'http_history_replay'
    assert result.session.http_session_id == client.calls[0]['session_id']
    assert result.session.prompt_cache_key == client.calls[0]['prompt_cache_key']
    assert client.calls[0]['parallel_tool_calls'] is False
    assert client.calls[0]['tool_choice'] == 'required'
    assert client.calls[0]['instructions']
    assert 'STABLE_CACHE_PREFIX:' in client.calls[0]['instructions']
    assert client.calls[0]['input_items'][0]['content'].startswith('TURN_STATE_TAIL:')
    assert result.session.history_items[-1]['type'] == 'function_call'
    assert result.session.history_items[-1]['call_id'] == 'call_1'


def test_orchestrator_agent_keeps_local_function_history_out_of_request_prefix():
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
    assert client.calls[0]['conversation_id'] == ''
    assert not any(item.get('type') == 'function_call' for item in client.calls[0]['input_items'] if isinstance(item, dict))
    tool_outputs = [
        item for item in client.calls[0]['input_items']
        if isinstance(item, dict) and item.get('type') == 'function_call_output'
    ]
    assert tool_outputs == []
    assert 'STABLE_CACHE_PREFIX:' in client.calls[0]['instructions']
    assert client.calls[0]['input_items'][0]['content'].startswith('TURN_STATE_TAIL:')
    assert result.session.history_items[-1]['type'] == 'function_call'
    assert result.tool_call is not None
    assert result.tool_call.tool_name == 'finish_case'


def test_orchestrator_agent_does_not_require_provider_conversation_transport():
    class NoConversationClient:
        def call_responses_tool_agent(self, **kwargs):
            return {
                'id': 'resp_1',
                'tool_calls': [
                    {
                        'call_id': 'call_1',
                        'name': 'propose_case_understanding',
                        'arguments': json.dumps({'reason': 'understand'}),
                    }
                ],
            }

    result = call_orchestrator_agent(NoConversationClient(), _workspace(), OrchestratorAgentSession(case_id='CASE-ORCH'))

    assert result.ok is True
    assert result.session.session_mode == 'http_history_replay'
    assert result.session.provider_session_enabled is False


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
    assert 'STABLE_CACHE_PREFIX:' in client.calls[0]['instructions']
    assert client.calls[0]['input_items'][0]['content'].startswith('Compacted prior OrchestratorAgent context')
    assert client.calls[0]['conversation_id'] == ''
    assert result.session.compact_count == 1
    assert result.audit['compacted'] is True
    assert result.audit['compact_mode'] == 'local_history_trim_after_context_threshold'


def test_orchestrator_stable_prefix_is_constant_while_turn_tail_changes():
    workspace = _workspace()
    updated = workspace.with_mapping_draft(MappingDraft(rows=[
        MappingDraftRow(row_ref='MDR1', local_ref='LF1', local_ref_kind='file', disposition='open', status='open'),
    ]))

    assert build_orchestrator_agent_stable_prefix(workspace) == build_orchestrator_agent_stable_prefix(updated)
    assert build_orchestrator_agent_turn_tail(workspace, reason='first') != build_orchestrator_agent_turn_tail(updated, reason='second')
    stable_payload = json.loads(build_orchestrator_agent_stable_prefix(workspace))
    tail_payload = json.loads(build_orchestrator_agent_turn_tail(workspace, reason='first'))
    assert 'local_main_file_groups' in stable_payload
    assert 'local_main_file_groups' not in tail_payload


def test_orchestrator_session_reuses_first_stable_prefix_across_turns():
    client = _ToolAgentClient([
        {
            'id': 'resp_1',
            'tool_calls': [
                {
                    'call_id': 'call_1',
                    'name': 'propose_case_understanding',
                    'arguments': json.dumps({'reason': 'first'}),
                }
            ],
            'usage': {'input_tokens': 1, 'output_tokens': 1, 'input_tokens_details': {'cached_tokens': 0}},
        },
        {
            'id': 'resp_2',
            'tool_calls': [
                {
                    'call_id': 'call_2',
                    'name': 'materialize_queries',
                    'arguments': json.dumps({'reason': 'second'}),
                }
            ],
            'usage': {'input_tokens': 1, 'output_tokens': 1, 'input_tokens_details': {'cached_tokens': 1}},
        },
    ])
    workspace = _workspace()
    updated = workspace.with_mapping_draft(MappingDraft(rows=[
        MappingDraftRow(row_ref='MDR1', local_ref='LF1', local_ref_kind='file', disposition='open', status='open'),
    ]))

    first = call_orchestrator_agent(client, workspace, OrchestratorAgentSession(case_id='CASE-ORCH'))
    second = call_orchestrator_agent(client, updated, first.session)

    first_prefix = client.calls[0]['instructions']
    second_prefix = client.calls[1]['instructions']
    assert first_prefix == second_prefix
    assert first.session.stable_cache_prefix
    assert second.session.stable_cache_prefix == first.session.stable_cache_prefix


def test_orchestrator_tool_definitions_are_strict_function_tools():
    tools = orchestrator_tool_definitions()

    tool_names = {tool['function']['name'] for tool in tools}
    assert tool_names >= {'propose_case_understanding', 'materialize_queries', 'execute_evidence', 'propose_case_resolution_ledger', 'propose_mapping_intents', 'split_into_child_cases', 'finish_case'}
    assert 'apply_draft_patches' not in tool_names
    materialize = next(tool for tool in tools if tool['function']['name'] == 'materialize_queries')
    parameters = materialize['function']['parameters']
    assert parameters['additionalProperties'] is False
    assert 'reason' in parameters['required']


def test_small_case_agent_input_exposes_reconsider_split_as_agent_capability():
    prompt = build_orchestrator_agent_input(_mapping_workspace(), reason='small single work unit')
    payload = json.loads(prompt)

    assert {'materialize_queries', 'execute_evidence', 'propose_mapping_intents', 'reconsider_split', 'split_into_child_cases'} <= set(payload['available_tool_names'])


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
    assert 'materialize_queries' in payload['available_tool_names']
    assert 'execute_evidence' in payload['available_tool_names']
    assert 'update_notebook' in payload['available_tool_names']


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
    assert {'execute_evidence', 'propose_mapping_intents', 'materialize_queries'} <= set(payload['available_tool_names'])


def test_subject_surface_without_items_keeps_evidence_visible_without_hiding_agent_tools():
    workspace = _regular_span_workspace()
    object.__setattr__(workspace, 'bangumi_subjects', [BangumiSubjectCard(ref='BS1', title='Title')])
    object.__setattr__(workspace, 'bangumi_items', [])
    object.__setattr__(workspace, 'bangumi_span_cards', [])
    object.__setattr__(workspace, 'mapping_draft', MappingDraft(rows=[
        MappingDraftRow(
            row_ref='MDR1',
            local_ref='LS1',
            local_ref_kind='span',
            disposition='needs_more_evidence',
            status='unresolved',
            subject_refs=['BS1'],
            requested_request_types=['episode_list'],
            reason_kind='ambiguous_candidate',
        ),
    ], version=2))

    payload = json.loads(build_orchestrator_agent_input(workspace, reason='subject surface needs episode items'))

    assert 'execute_evidence' in payload['available_tool_names']
    assert 'propose_mapping_intents' in payload['available_tool_names']
    assert 'propose_case_resolution_ledger' in payload['available_tool_names']
    assert payload['visible_target_surface_present'] is True
    assert payload['open_rows_requiring_agent_action'][0]['recommended_exit'].startswith('execute the requested evidence')


def test_actionable_surface_does_not_reopen_understanding_without_repartition_request():
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


def test_payload_exposes_row_outcome_closure_for_open_rows():
    workspace = _regular_span_workspace()
    object.__setattr__(workspace, 'mapping_draft', MappingDraft(rows=[
        MappingDraftRow(
            row_ref='MDR1',
            local_ref='LS1',
            local_ref_kind='span',
            disposition='open',
            status='open',
            subject_refs=['BS1'],
            candidate_target_refs=['BE1', 'BE2'],
        )
    ], version=2))

    payload = json.loads(build_orchestrator_agent_input(workspace, reason='row needs a concrete outcome'))

    closure = payload['row_outcome_closure']
    assert closure[0]['row_ref'] == 'MDR1'
    decisions = {template['decision'] for template in closure[0]['valid_intent_templates']}
    assert {'map_regular_span', 'reject_candidate', 'mark_non_bangumi_or_supplemental', 'mark_unaligned_fail_closed'} <= decisions
    supplemental_template = next(template for template in closure[0]['valid_intent_templates'] if template['decision'] == 'mark_non_bangumi_or_supplemental')
    assert 'bangumi_target_absent' in supplemental_template['allowed_reason_kinds']
    assert 'fixed layer only validates' in closure[0]['closure_rule']


def test_row_outcome_closure_records_recent_mechanical_blockers():
    workspace = _regular_span_workspace()
    object.__setattr__(workspace, 'mapping_draft', MappingDraft(rows=[
        MappingDraftRow(
            row_ref='MDR1',
            local_ref='LS1',
            local_ref_kind='span',
            disposition='open',
            status='open',
            subject_refs=['BS1'],
            candidate_target_refs=['BE1'],
        )
    ], version=2))
    object.__setattr__(workspace, 'judge_request_audits', [
        {
            'note': 'orchestrator_mapping_intents_result',
            'status': 'blocked_intents',
            'blocked_intent_count': 1,
            'blocked_intents': [
                {
                    'local_ref': 'LS1',
                    'row_ref': 'MDR1',
                    'decision': 'mark_non_bangumi_or_supplemental',
                    'issue_codes': ['invalid_reason_kind', 'invalid_explicit_multi_file_mapping'],
                }
            ],
        }
    ])

    payload = json.loads(build_orchestrator_agent_input(workspace, reason='blocked row needs a different legal shape'))

    closure = payload['row_outcome_closure'][0]
    assert closure['latest_blocker_issue_codes'] == ['invalid_reason_kind', 'invalid_explicit_multi_file_mapping']
    assert any('allowed_supplemental_reason_kinds' in item for item in closure['must_not_repeat'])
    assert any('map_explicit_item' in item for item in closure['must_not_repeat'])


def test_blocked_intent_with_matching_requested_evidence_keeps_agent_tools_visible():
    workspace = _regular_span_workspace()
    object.__setattr__(workspace, 'mapping_draft', MappingDraft(rows=[
        MappingDraftRow(
            row_ref='MDR1',
            local_ref='LS1',
            local_ref_kind='span',
            disposition='open',
            status='open',
            candidate_target_refs=['BE1'],
            subject_refs=['BS1'],
        )
    ], version=2))
    object.__setattr__(workspace, 'judge_request_audits', [
        {
            'note': 'orchestrator_mapping_intents_result',
            'status': 'blocked_intents',
            'blocked_intent_issue_codes': ['invalid_explicit_multi_file_mapping'],
            'blocked_intent_count': 1,
            'requested_evidence': ['episode_list', 'target_span'],
            'matching_requested_evidence_available': True,
        }
    ])

    payload = json.loads(build_orchestrator_agent_input(workspace, reason='blocked intent has evidence agenda'))

    assert 'execute_evidence' in payload['available_tool_names']
    assert 'propose_mapping_intents' in payload['available_tool_names']

    tool_call = OrchestratorAgentToolCall(
        tool_name='propose_mapping_intents',
        arguments=ProposeMappingIntentsToolArgs(
            reason='retry same blocked intent',
            mapping_intents=[
                MappingIntent(
                    decision='map_explicit_item',
                    local_ref='LS1',
                    chosen_item_ref='BE1',
                    support_refs=['LS1', 'BE1'],
                    reason='revise with visible target',
                )
            ],
        ),
        raw_arguments={'reason': 'retry same blocked intent'},
    )
    _workspace, decision, acceptance = _decision_from_orchestrator_tool_call(workspace, tool_call)

    assert decision is not None
    assert decision.action == 'propose_mapping_intents'
    assert acceptance['accepted'] is True


def test_blocked_target_detail_intent_matches_target_window_evidence_route():
    workspace = _regular_span_workspace()
    object.__setattr__(workspace, 'mapping_draft', MappingDraft(rows=[
        MappingDraftRow(
            row_ref='MDR1',
            local_ref='LS1',
            local_ref_kind='span',
            disposition='open',
            status='open',
            candidate_target_refs=['BE1'],
            subject_refs=['BS1'],
        )
    ], version=2))
    object.__setattr__(workspace, 'judge_request_audits', [
        {
            'note': 'orchestrator_mapping_intents_result',
            'status': 'blocked_intents',
            'blocked_intent_issue_codes': ['invalid_explicit_multi_file_mapping'],
            'blocked_intent_count': 1,
            'requested_evidence': ['target_detail'],
            'matching_requested_evidence_available': True,
            'blocked_intents': [
                {
                    'local_ref': 'LS1',
                    'row_ref': 'MDR1',
                    'subject_refs': ['BS1'],
                    'item_refs': ['BE1'],
                    'requested_request_types': ['target_detail'],
                }
            ],
        }
    ])

    payload = json.loads(build_orchestrator_agent_input(workspace, reason='blocked intent has compatible target evidence'))

    assert 'execute_evidence' in payload['available_tool_names']
    assert 'propose_mapping_intents' in payload['available_tool_names']

    tool_call = OrchestratorAgentToolCall(
        tool_name='execute_evidence',
        arguments=ExecuteEvidenceToolArgs(reason='run compatible target evidence'),
        raw_arguments={'reason': 'run compatible target evidence'},
    )
    _workspace, decision, acceptance = _decision_from_orchestrator_tool_call(workspace, tool_call)

    assert decision is not None
    assert decision.action == 'execute_evidence'
    assert acceptance['accepted'] is True
    assert acceptance['selected_menu_request_ids']


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

    assert 'finish_case' in payload['available_tool_names']
    assert 'execute_evidence' in payload['available_tool_names']
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

    prepared = _prepare_workspace_for_orchestrator_agent_turn(workspace)
    prompt = build_orchestrator_agent_input(prepared, reason='budget exhausted after semantic intent')
    payload = json.loads(prompt)

    assert 'finish_case' in payload['available_tool_names']
    assert 'propose_mapping_intents' in payload['available_tool_names']


def test_turn_limit_with_executable_evidence_does_not_fail_closed():
    client = _RunClient([
        {
            'id': 'resp_need_evidence',
            'tool_calls': [
                {
                    'call_id': 'call_need_evidence',
                    'name': 'propose_mapping_intents',
                    'arguments': json.dumps({
                        'mapping_intents': [
                            {
                                'decision': 'needs_more_evidence',
                                'local_ref': 'LS1',
                                'requested_request_types': ['target_span'],
                                'support_refs': ['LS1', 'BS1'],
                                'reason': 'need target span evidence before mapping',
                            }
                        ],
                    }),
                }
            ],
        },
    ])

    result = run_local_bangumi_case_agent(_regular_span_workspace(), client, object(), max_rounds=1)

    assert result.ok is False
    assert result.status == 'error'
    assert result.summary == 'orchestrator turn limit reached: 1'
    assert any(
        isinstance(audit, dict)
        and audit.get('note') == 'orchestrator_mapping_intents_result'
        and audit.get('matching_requested_evidence_available') is True
        and audit.get('required_next_tools') == ['execute_evidence']
        for audit in result.final_workspace.judge_request_audits
    )


def test_turn_limit_allows_one_accepted_finish_grace_turn():
    client = _RunClient([
        {
            'id': 'resp_map_ready',
            'tool_calls': [
                {
                    'call_id': 'call_map_ready',
                    'name': 'propose_mapping_intents',
                    'arguments': json.dumps({
                        'mapping_intents': [
                            {
                                'decision': 'map_explicit_item',
                                'local_ref': 'LS1',
                                'chosen_item_ref': 'BE1',
                                'support_refs': ['LS1', 'BE1'],
                                'reason': 'visible singleton episode item matches the local file',
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
                    'arguments': json.dumps(
                        _accepted_finish_args(
                            reason='accounting ready after final mapping turn',
                            mapped=1,
                            excluded=0,
                            outcome_kind='mapped',
                            file_count=1,
                            support_refs=['LS1', 'BE1'],
                        )
                    ),
                }
            ],
        },
    ])

    result = run_local_bangumi_case_agent(_mapping_workspace(), client, object(), max_rounds=1)

    assert result.status == 'accepted'
    assert len(client.calls) == 2
    assert any(
        isinstance(audit, dict)
        and audit.get('note') == 'orchestrator_accepted_finish_grace_turn'
        for audit in result.final_workspace.judge_request_audits
    )


def test_turn_limit_preserves_work_unit_and_latest_blocker_details():
    client = _RunClient([
        {
            'id': 'resp_bad_shape',
            'tool_calls': [
                {
                    'call_id': 'call_bad_shape',
                    'name': 'propose_mapping_intents',
                    'arguments': json.dumps({
                        'mapping_intents': [
                            {
                                'intent_ref': 'I_BAD',
                                'decision': 'map_explicit_item',
                                'local_ref': 'LS1',
                                'chosen_item_ref': 'BE1',
                                'support_refs': ['LS1', 'BE1'],
                                'requested_request_types': ['target_span'],
                                'reason': 'agent tried a singleton target for a multi-file row',
                            }
                        ],
                    }),
                }
            ],
        },
    ])

    result = run_local_bangumi_case_agent(_regular_span_workspace(), client, object(), max_rounds=1)

    assert result.status == 'error'
    blocked = next(
        audit for audit in result.final_workspace.judge_request_audits
        if isinstance(audit, dict)
        and audit.get('note') == 'orchestrator_mapping_intents_result'
    )
    assert blocked['blocked_intent_issue_codes'] == ['invalid_explicit_multi_file_mapping']
    assert blocked['blocked_intents'][0]['observation']['local_file_count'] == 2
    assert 'target_span' in blocked['requested_evidence']


def test_mapping_intent_candidate_target_refs_allow_subject_refs_as_context():
    tool_call = OrchestratorAgentToolCall(
        tool_name='propose_mapping_intents',
        arguments=ProposeMappingIntentsToolArgs(
            reason='bad candidate target namespace',
            mapping_intents=[
                MappingIntent(
                    decision='map_regular_span',
                    local_ref='LS1',
                    chosen_subject_ref='BS1',
                    candidate_target_refs=['BE1', 'BS1'],
                    item_refs=['BE1', 'BE2'],
                    support_refs=['LS1', 'BS1', 'BE1', 'BE2'],
                    reason='candidate_target_refs may include subject refs as non-terminal context',
                )
            ],
        ),
        raw_arguments={
            'mapping_intents': [
                {
                    'decision': 'map_regular_span',
                    'local_ref': 'LS1',
                    'chosen_subject_ref': 'BS1',
                    'candidate_target_refs': ['BE1', 'BS1'],
                    'item_refs': ['BE1', 'BE2'],
                    'support_refs': ['LS1', 'BS1', 'BE1', 'BE2'],
                }
            ]
        },
    )

    _workspace, decision, acceptance = _decision_from_orchestrator_tool_call(_regular_span_workspace(), tool_call)

    assert decision is not None
    assert decision.action == 'propose_mapping_intents'
    assert acceptance['accepted'] is True


def test_unknown_local_ref_does_not_count_as_same_count_target_surface():
    from src.rename.case_agent.orchestrator import _row_has_same_count_target_surface

    row = MappingDraftRow(row_ref='MDRX', local_ref='LS_UNKNOWN', local_ref_kind='span', candidate_target_refs=['BE1'])

    assert _row_has_same_count_target_surface(_regular_span_workspace(), row) is False


def test_finish_case_is_only_available_tool_when_accepted_accounting_ready():
    workspace = _mapping_workspace()
    object.__setattr__(workspace, 'mapping_draft', MappingDraft(rows=[
        MappingDraftRow(
            row_ref='MDR1',
            local_ref='LS1',
            local_ref_kind='span',
            disposition='map_to_bangumi',
            status='proposed',
            selected_target_ref='BE1',
            selected_target_kind='item',
            mapping_mode='explicit',
            support_refs=['LS1', 'BE1'],
        ),
    ], version=2))

    payload = json.loads(build_orchestrator_agent_input(workspace, reason='ready to finish'))

    assert payload['draft_accounting']['accepted_accounting_ready'] is True
    assert 'finish_case' in payload['available_tool_names']


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
    groups = payload['local_main_file_groups']
    assert groups[0]['file_ref_count'] == 18
    assert groups[0]['group_ref'] == 'LG1'
    assert groups[0]['file_refs'][0] == 'LF1'
    assert groups[0]['file_refs'][-1] == 'LF18'
    assert any('disjoint file_refs' in rule for rule in payload['rules'])
    assert any('main_group_refs' in rule for rule in payload['rules'])


def test_large_package_group_overview_uses_leaf_parent_directories():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-LEAF-GROUPS'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=['LF1', 'LF2', 'LF3'], allowed_file_refs=['LF1', 'LF2', 'LF3']),
        local_files=[
            LocalFileCard(ref='LF1', path='Title/Title 01.mkv', label='Title 01.mkv', is_main=True),
            LocalFileCard(ref='LF2', path='Title/Title 02.mkv', label='Title 02.mkv', is_main=True),
            LocalFileCard(ref='LF3', path='Title/SPs/Title SP01.mkv', label='Title SP01.mkv', is_main=True),
        ],
    )

    payload = json.loads(build_orchestrator_agent_input(workspace, reason='leaf grouping'))
    groups = {group['group_key']: group for group in payload['local_main_file_groups']}

    assert groups['Title']['group_ref'] == 'LG1'
    assert groups['Title/SPs']['group_ref'] == 'LG2'
    assert groups['Title']['file_refs'] == ['LF1', 'LF2']
    assert groups['Title/SPs']['file_refs'] == ['LF3']
    assert groups['Title/SPs']['top_group'] == 'Title'


def test_split_into_child_cases_expands_local_group_refs_before_validation():
    workspace = _split_root_workspace()
    from src.rename.case_agent.orchestrator import _expand_split_group_refs, _tool_ref_validation_issues

    args = SplitIntoChildCasesToolArgs(
        reason='split by visible local group refs',
        split_cases=[
            SplitCaseSpec(child_case_ref='S1', main_group_refs=['LG1'], title_hints=['Title']),
            SplitCaseSpec(child_case_ref='S2', main_group_refs=['LG2'], title_hints=['Title II']),
        ],
    )
    call = OrchestratorAgentToolCall(
        tool_name='split_into_child_cases',
        arguments=args,
        raw_arguments=json.loads(args.model_dump_json()),
    )

    assert _tool_ref_validation_issues(workspace, call) == []
    expanded, audit = _expand_split_group_refs(workspace, args.split_cases)

    assert [spec.main_file_refs for spec in expanded] == [['LF1'], ['LF2']]
    assert audit['split_group_refs_expanded'] is True
    assert audit['unknown_split_group_refs'] == []


def test_materialize_queries_accepts_visible_local_group_source_refs():
    workspace = _split_root_workspace()
    from src.rename.case_agent.orchestrator import _tool_ref_validation_issues

    args = MaterializeQueriesToolArgs(
        reason='query by visible local group',
        queries=[
            QueryCandidate(query_text='Title', source_refs=['LG1'], reason='group title cue', confidence='medium'),
        ],
    )
    call = OrchestratorAgentToolCall(
        tool_name='materialize_queries',
        arguments=args,
        raw_arguments=json.loads(args.model_dump_json()),
    )

    assert _tool_ref_validation_issues(workspace, call) == []
    updated, observation = _run_orchestrator_materialize_queries_tool(workspace, args)

    assert observation['status'] == 'ok'
    assert updated.query_cards
    assert updated.query_cards[0].source_refs == ['LG1']


def test_case_understanding_observation_includes_split_skeleton_from_work_units():
    workspace = _split_root_workspace()

    updated, observation = _compile_case_understanding(
        workspace,
        ProposeCaseUnderstandingToolArgs(
            reason='two independent groups',
            package_shape='two seasons',
            work_units=[
                CaseBriefingWorkUnit(work_unit_ref='WU1', label='season 1', file_refs=['LF1'], title_hints=['Title']),
                CaseBriefingWorkUnit(work_unit_ref='WU2', label='season 2', file_refs=['LF2'], title_hints=['Title II']),
            ],
        ),
    )

    assert observation['status'] == 'ok'
    assert observation['split_case_skeleton_from_work_units'][0]['main_group_refs'] == ['LG1']
    assert observation['split_case_skeleton_from_work_units'][1]['main_group_refs'] == ['LG2']
    assert updated.case_briefing is not None


def test_open_row_observation_exposes_multi_singleton_candidate_pool():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-MULTI-MOVIE-POOL'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=['LF1', 'LF2'], allowed_file_refs=['LF1', 'LF2'], visible_target_refs=['BE1', 'BE2', 'BE3']),
        local_files=[
            LocalFileCard(ref='LF1', path='Movies/Title [Part A].mkv', label='Title [Part A].mkv', is_main=True),
            LocalFileCard(ref='LF2', path='Movies/Title [Part B].mkv', label='Title [Part B].mkv', is_main=True),
        ],
        local_span_cards=[
            LocalSpanCard(ref='LS1', span_scope='directory', file_refs=['LF1', 'LF2'], file_ref_count=2, file_ref_samples=['LF1', 'LF2']),
        ],
        bangumi_items=[
            BangumiItemCard(ref='BE1', subject_ref='BS1', sort=1, ep=1, item_kind='movie', title='Part A'),
            BangumiItemCard(ref='BE2', subject_ref='BS2', sort=1, ep=1, item_kind='movie', title='Part B'),
            BangumiItemCard(ref='BE3', subject_ref='BS3', sort=1, ep=1, item_kind='movie', title='Other'),
        ],
    )
    object.__setattr__(workspace, 'mapping_draft', MappingDraft(rows=[
        MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span', candidate_target_refs=['BE1', 'BE2', 'BE3']),
    ]))
    from src.rename.case_agent.orchestrator import _open_rows_observation

    row = _open_rows_observation(workspace)[0]

    assert row['multi_singleton_candidate_pool']['choose_exactly'] == 2
    assert row['multi_singleton_candidate_pool']['candidate_item_refs'] == ['BE1', 'BE2', 'BE3']
    assert 'one visible BE per local file' in row['recommended_next']


def test_case_understanding_rejects_mixed_regular_and_extra_leaf_groups():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-MIXED-LEAF-UNDERSTANDING'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=['LF1', 'LF2', 'LF3'], allowed_file_refs=['LF1', 'LF2', 'LF3']),
        local_files=[
            LocalFileCard(ref='LF1', path='Title/Title 01.mkv', label='Title 01.mkv', is_main=True),
            LocalFileCard(ref='LF2', path='Title/Title 02.mkv', label='Title 02.mkv', is_main=True),
            LocalFileCard(ref='LF3', path='Title/SPs/Title SP01.mkv', label='Title SP01.mkv', is_main=True),
        ],
    )

    _updated, observation = _compile_case_understanding(
        workspace,
        ProposeCaseUnderstandingToolArgs(
            reason='too broad mixed unit',
            package_shape='regular plus SP',
            work_units=[
                CaseBriefingWorkUnit(
                    work_unit_ref='WU1',
                    label='mixed title files',
                    file_refs=['LF1', 'LF2', 'LF3'],
                    local_refs=['LF1', 'LF2', 'LF3'],
                )
            ],
        ),
    )

    assert observation['status'] == 'rejected'
    assert 'case_understanding_mixed_leaf_groups' in observation['issue_codes']


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
            previous_evidence_results=[
                EvidenceBatchResult(
                    batch_ref='EB1',
                    status='accepted',
                    request_results=[
                        EvidenceRequestResult(
                            request_ref='REQ_SUBJECT_SEARCH_QC1',
                            request_type='subject_search',
                            accepted=True,
                            response_refs=['BS1'],
                        )
                    ],
                )
            ],
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


def test_structural_intent_issue_is_guidance_not_tool_visibility_gate():
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
    assert any('item_ref_count_mismatch' in rule and 'revise understanding' in rule for rule in payload['rules'])


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

    assert 'execute_evidence' in payload['available_tool_names']
    assert 'propose_mapping_intents' in payload['available_tool_names']


def test_failed_execute_evidence_unhides_mapping_intents_when_request_agenda_has_no_fresh_match():
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
            'status': 'partial',
            'requested_evidence': ['target_span'],
            'blocked_intent_count': 1,
        },
        {
            'note': 'orchestrator_tool_selected',
            'tool_name': 'execute_evidence',
            'accepted': False,
            'reason': 'no_executable_menu_request',
        },
    ])

    payload = json.loads(build_orchestrator_agent_input(workspace, reason='execute evidence had no fresh match'))

    assert 'propose_mapping_intents' in payload['available_tool_names']
    assert 'materialize_queries' in payload['available_tool_names']


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
        case_briefing=CaseBriefingOutput(package_shape='single episode'),
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


def test_execute_evidence_stale_selected_query_ids_fall_back_to_fresh_requested_type():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-STALE-QC-FALLBACK'),
        budget=CaseBudget(max_requests_per_batch=4),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1']),
        local_files=[LocalFileCard(ref='LF1', path='Title 01.mkv', is_main=True)],
        query_cards=[
            QueryCard(ref='QC1', query_text='Old Title', query_kind='subject_search', query_origin='agent_composed', source_refs=['LF1']),
            QueryCard(ref='QC2', query_text='Fresh Title', query_kind='subject_search', query_origin='agent_composed', source_refs=['LF1']),
        ],
        case_briefing=CaseBriefingOutput(package_shape='single episode'),
    )
    object.__setattr__(workspace.plan_state, 'completed_menu_request_ids', ['REQ_SUBJECT_SEARCH_QC1'])
    tool_call = OrchestratorAgentToolCall(
        tool_name='execute_evidence',
        arguments=ExecuteEvidenceToolArgs(
            reason='retry subject search with requested type',
            selected_menu_request_ids=['REQ_SUBJECT_SEARCH_QC1'],
            requested_request_types=['subject_search'],
        ),
        raw_arguments={
            'reason': 'retry subject search with requested type',
            'selected_menu_request_ids': ['REQ_SUBJECT_SEARCH_QC1'],
            'requested_request_types': ['subject_search'],
        },
    )

    _workspace, decision, acceptance = _decision_from_orchestrator_tool_call(workspace, tool_call)

    assert acceptance['accepted'] is True
    assert acceptance['stale_menu_request_ids'] == ['REQ_SUBJECT_SEARCH_QC1']
    assert acceptance['unknown_menu_request_ids'] == []
    assert decision is not None
    assert decision.action == 'execute_evidence'
    assert decision.planner_output.plan.selected_menu_request_ids == ['REQ_SUBJECT_SEARCH_QC2']


def test_payload_exposes_latest_blocked_ledger_evidence_agenda_with_request_ids():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-LEDGER-AGENDA'),
        budget=CaseBudget(max_requests_per_batch=8),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1']),
        local_files=[LocalFileCard(ref='LF1', path='Title 01.mkv', is_main=True)],
        bangumi_subjects=[
            BangumiSubjectCard(ref='BS1', title='Wrong'),
            BangumiSubjectCard(ref='BS5', title='Right'),
        ],
        case_briefing=CaseBriefingOutput(package_shape='single episode'),
    )
    object.__setattr__(workspace, 'mapping_draft', MappingDraft(rows=[
        MappingDraftRow(
            row_ref='MDR1',
            local_ref='LF1',
            local_ref_kind='file',
            disposition='needs_more_evidence',
            status='unresolved',
            subject_refs=['BS5'],
            requested_request_types=['episode_list', 'target_span'],
        )
    ]))
    object.__setattr__(workspace, 'judge_request_audits', [
        {
            'note': 'orchestrator_case_resolution_ledger_result',
            'status': 'partial',
            'requested_evidence': ['episode_list', 'target_span'],
            'blocked_ledger_row_count': 1,
            'blocked_ledger_rows': [
                {
                    'ledger_row_ref': 'CRLR1',
                    'row_ref': 'MDR1',
                    'local_ref': 'LF1',
                    'outcome': 'needs_evidence',
                    'issue_codes': ['target_span_or_item_not_visible'],
                    'requested_request_types': ['episode_list', 'target_span'],
                    'subject_refs': ['BS5'],
                    'recommended_next_observation': 'execute requested evidence for BS5',
                }
            ],
        }
    ])

    payload = json.loads(build_orchestrator_agent_input(workspace, reason='ledger blocked'))
    agenda = payload['latest_blocked_evidence_agenda']

    assert agenda['active'] is True
    assert agenda['source_note'] == 'orchestrator_case_resolution_ledger_result'
    assert agenda['subject_refs'] == ['BS5']
    assert 'REQ_EPISODE_LIST_BS5' in agenda['matching_executable_request_ids']
    assert 'REQ_EPISODE_LIST_BS5' in agenda['suggested_execute_evidence_args']['selected_menu_request_ids']
    assert agenda['blocked_rows'][0]['recommended_next_tool'] == 'execute_evidence'


def test_repeated_mapping_intent_blocked_when_requested_target_evidence_is_executable():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-INTENT-AGENDA'),
        budget=CaseBudget(max_requests_per_batch=8),
        contract=CaseContract(main_file_refs=['LF1', 'LF2'], allowed_file_refs=['LF1', 'LF2']),
        local_files=[
            LocalFileCard(ref='LF1', path='Title 01.mkv', is_main=True),
            LocalFileCard(ref='LF2', path='Title 02.mkv', is_main=True),
        ],
        local_span_cards=[
            LocalSpanCard(ref='LS1', file_refs=['LF1', 'LF2'], file_ref_count=2, file_ref_samples=['LF1', 'LF2'])
        ],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', title='Title')],
        case_briefing=CaseBriefingOutput(package_shape='two regular episodes'),
    )
    object.__setattr__(workspace, 'mapping_draft', MappingDraft(rows=[
        MappingDraftRow(
            row_ref='MDR1',
            local_ref='LS1',
            local_ref_kind='span',
            status='unresolved',
            disposition='needs_more_evidence',
            subject_refs=['BS1'],
            requested_request_types=['episode_list', 'target_span'],
        )
    ]))
    object.__setattr__(workspace, 'judge_request_audits', [
        {
            'note': 'orchestrator_mapping_intents_result',
            'requested_evidence': ['episode_list', 'target_span'],
            'blocked_intents': [{'local_ref': 'LS1', 'subject_refs': ['BS1'], 'requested_request_types': ['episode_list', 'target_span']}],
        }
    ])
    tool_call = OrchestratorAgentToolCall(
        tool_name='propose_mapping_intents',
        arguments=ProposeMappingIntentsToolArgs(
            reason='repeat unavailable subject-only span intent',
            mapping_intents=[
                MappingIntent(
                    decision='map_regular_span',
                    local_ref='LS1',
                    chosen_subject_ref='BS1',
                    episode_start=1,
                    episode_end=2,
                    support_refs=['LS1', 'BS1'],
                    reason='same semantic choice still needs episode surface',
                )
            ],
        ),
        raw_arguments={'reason': 'repeat unavailable subject-only span intent'},
        call_id='call_repeat_mapping',
    )

    _workspace, decision, acceptance = _decision_from_orchestrator_tool_call(workspace, tool_call)

    assert decision is not None
    assert decision.action == 'propose_mapping_intents'
    assert acceptance['accepted'] is True


def test_mapping_intent_with_visible_target_choice_not_blocked_by_prior_evidence_agenda():
    workspace = _regular_span_workspace()
    object.__setattr__(workspace, 'judge_request_audits', [
        {
            'note': 'orchestrator_mapping_intents_result',
            'requested_evidence': ['episode_list', 'target_span'],
            'blocked_intents': [{'local_ref': 'LS1', 'subject_refs': ['BS1'], 'requested_request_types': ['episode_list', 'target_span']}],
        }
    ])
    tool_call = OrchestratorAgentToolCall(
        tool_name='propose_mapping_intents',
        arguments=ProposeMappingIntentsToolArgs(
            reason='now use visible item refs',
            mapping_intents=[
                MappingIntent(
                    decision='map_regular_span',
                    local_ref='LS1',
                    chosen_subject_ref='BS1',
                    episode_start=1,
                    episode_end=2,
                    item_refs=['BE1', 'BE2'],
                    support_refs=['LS1', 'BS1', 'BE1', 'BE2'],
                    reason='visible sequence covers the row',
                )
            ],
        ),
        raw_arguments={'reason': 'now use visible item refs'},
        call_id='call_visible_mapping',
    )

    _workspace, decision, acceptance = _decision_from_orchestrator_tool_call(workspace, tool_call)

    assert acceptance['accepted'] is True
    assert decision is not None
    assert decision.action == 'propose_mapping_intents'


def test_execute_evidence_defaults_to_episode_list_when_subjects_visible_but_no_items():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-SUBJECT-NO-ITEMS'),
        budget=CaseBudget(max_requests_per_batch=8),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1']),
        local_files=[LocalFileCard(ref='LF1', path='Title 01.mkv', is_main=True)],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', title='Title')],
        case_briefing=CaseBriefingOutput(package_shape='episode'),
    )
    object.__setattr__(workspace, 'mapping_draft', MappingDraft(rows=[
        MappingDraftRow(
            row_ref='MDR1',
            local_ref='LF1',
            local_ref_kind='file',
            disposition='needs_more_evidence',
            status='unresolved',
            subject_refs=['BS1'],
            requested_request_types=['episode_list'],
        )
    ]))
    tool_call = OrchestratorAgentToolCall(
        tool_name='execute_evidence',
        arguments=ExecuteEvidenceToolArgs(reason='continue evidence'),
        raw_arguments={'reason': 'continue evidence'},
    )

    _updated, decision, acceptance = _decision_from_orchestrator_tool_call(workspace, tool_call)

    assert acceptance['accepted'] is True
    assert decision is not None
    assert decision.action == 'execute_evidence'
    assert 'REQ_EPISODE_LIST_BS1' in decision.planner_output.plan.selected_menu_request_ids


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
        case_briefing=CaseBriefingOutput(package_shape='two episodes'),
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


def test_repeated_case_understanding_is_allowed_as_agent_capability():
    workspace = _mapping_workspace()
    tool_call = OrchestratorAgentToolCall(
        tool_name='propose_case_understanding',
        arguments=ProposeCaseUnderstandingToolArgs(
            reason='repeat understanding',
            package_shape='single file',
            work_units=[{'work_unit_ref': 'WU1', 'label': 'main file', 'local_refs': ['LF1'], 'file_refs': ['LF1']}],
            title_hypotheses=[{'title': 'Title', 'source_refs': ['LF1'], 'confidence': 'medium'}],
            summary='repeat',
        ),
        raw_arguments={'reason': 'repeat understanding'},
        call_id='call_understand',
    )

    _workspace, decision, acceptance = _decision_from_orchestrator_tool_call(workspace, tool_call)

    assert decision is not None
    assert decision.action == 'propose_case_understanding'
    assert acceptance['accepted'] is True


def test_orchestrator_keeps_query_available_when_evidence_is_pending():
    workspace = _regular_span_workspace()
    object.__setattr__(workspace, 'bangumi_subjects', [BangumiSubjectCard(ref='BS1', title='Title')])
    object.__setattr__(workspace, 'bangumi_items', [])
    object.__setattr__(workspace, 'bangumi_span_cards', [])
    object.__setattr__(workspace, 'mapping_draft', MappingDraft(rows=[
        MappingDraftRow(
            row_ref='MDR1',
            local_ref='LS1',
            local_ref_kind='span',
            disposition='needs_more_evidence',
            status='unresolved',
            subject_refs=['BS1'],
            requested_request_types=['episode_list'],
        ),
    ], version=2))
    tool_call = OrchestratorAgentToolCall(
        tool_name='materialize_queries',
        arguments=MaterializeQueriesToolArgs(reason='try unavailable tool'),
        raw_arguments={'reason': 'try unavailable tool'},
        call_id='call_materialize',
    )

    _workspace, decision, acceptance = _decision_from_orchestrator_tool_call(workspace, tool_call)

    assert acceptance['accepted'] is True
    assert decision is not None
    assert decision.action == 'compose_queries'


def test_run_does_not_auto_reroute_unavailable_tool_calls():
    client = _RunClient([
        {
            'id': 'resp_wrong_tool',
            'tool_calls': [
                {
                    'call_id': 'call_wrong',
                    'name': 'materialize_queries',
                    'arguments': json.dumps({'reason': 'try unavailable query tool'}),
                }
            ],
        }
    ])
    workspace = _regular_span_workspace()
    object.__setattr__(workspace, 'bangumi_subjects', [BangumiSubjectCard(ref='BS1', title='Title')])
    object.__setattr__(workspace, 'bangumi_items', [])
    object.__setattr__(workspace, 'bangumi_span_cards', [])
    object.__setattr__(workspace, 'mapping_draft', MappingDraft(rows=[
        MappingDraftRow(
            row_ref='MDR1',
            local_ref='LS1',
            local_ref_kind='span',
            disposition='needs_more_evidence',
            status='unresolved',
            subject_refs=['BS1'],
            requested_request_types=['episode_list'],
        ),
    ], version=2))

    result = run_local_bangumi_case_agent(workspace, client, object(), max_rounds=1)

    assert not any(
        isinstance(audit, dict)
        and audit.get('note') == 'orchestrator_tool_auto_rerouted'
        for audit in result.final_workspace.judge_request_audits
    )
    assert any(
            isinstance(audit, dict)
            and audit.get('note') == 'orchestrator_tool_selected'
            and audit.get('tool_name') == 'materialize_queries'
            and audit.get('accepted') is True
            for audit in result.final_workspace.judge_request_audits
        )


def test_update_notebook_rejected_while_open_rows_need_agent_action():
    workspace = _mapping_workspace()
    tool_call = OrchestratorAgentToolCall(
        tool_name='update_notebook',
        arguments=UpdateNotebookToolArgs(
            reason='bookkeeping before mapping',
            notebook_updates=[
                NotebookUpdate(
                    update_kind='note',
                    local_refs=['LF1'],
                    claim='LF1 is still unresolved',
                    confidence='medium',
                    reason='open row exists',
                )
            ],
        ),
        raw_arguments={'reason': 'bookkeeping before mapping'},
        call_id='call_note',
    )

    _workspace, decision, acceptance = _decision_from_orchestrator_tool_call(workspace, tool_call)

    assert decision is not None
    assert decision.action == 'update_notebook'
    assert acceptance['accepted'] is True


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


def test_open_row_sequences_do_not_offer_occupied_sequence_as_mapping_example():
    file_refs = [f'LF{i}' for i in range(1, 7)]
    items = [
        BangumiItemCard(ref=f'BE{i}', subject_ref='BS1', item_kind='episode', ep=i, sort=i)
        for i in range(1, 4)
    ]
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-OCCUPIED-SEQUENCE'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=file_refs, allowed_file_refs=file_refs, visible_target_refs=[item.ref for item in items]),
        local_files=[
            LocalFileCard(ref=ref, path=f'Title {index:02d}.mkv', is_main=True)
            for index, ref in enumerate(file_refs, start=1)
        ],
        local_span_cards=[
            LocalSpanCard(ref='LS1', span_scope='directory', file_refs=file_refs[:3], file_ref_count=3, file_ref_samples=file_refs[:3]),
            LocalSpanCard(ref='LS2', span_scope='directory', file_refs=file_refs[3:], file_ref_count=3, file_ref_samples=file_refs[3:]),
        ],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', title='Title')],
        bangumi_items=items,
        bangumi_span_cards=[
            BangumiSpanCard(ref='BES1', subject_ref='BS1', target_refs=['BE1', 'BE2', 'BE3'], target_ref_count=3),
        ],
        mapping_draft=MappingDraft(rows=[
            MappingDraftRow(
                row_ref='MDR1',
                local_ref='LS1',
                local_ref_kind='span',
                disposition='map_to_bangumi',
                status='proposed',
                selected_target_ref='BES1',
                selected_target_kind='span',
                mapping_mode='span_by_index',
                support_refs=['LS1', 'BES1'],
            ),
            MappingDraftRow(row_ref='MDR2', local_ref='LS2', local_ref_kind='span', subject_refs=['BS1']),
        ]),
    )

    payload = json.loads(build_orchestrator_agent_input(workspace, reason='inspect occupied sequence'))
    row = payload['open_rows_requiring_agent_action'][0]
    sequence = row['visible_subject_item_sequences'][0]

    assert sequence['matches_local_file_count'] is True
    assert sequence['all_item_refs_unowned'] is False
    assert sequence['mapping_legality'] == 'occupied_by_existing_rows'
    assert sequence['owner_row_refs'] == ['MDR1']
    assert row['intent_examples'] == []
    assert 'Do not reuse those occupied targets' in row['recommended_exit']

    from src.rename.case_agent.orchestrator import _open_rows_observation

    observation = _open_rows_observation(workspace)[0]
    assert observation['visible_subject_item_sequences'][0]['mapping_legality'] == 'occupied_by_existing_rows'
    assert 'Do not reuse those occupied targets' in observation['recommended_next']


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


def test_needs_more_evidence_warns_when_row_candidate_can_be_resolved():
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
    row = next(row for row in _updated.mapping_draft.rows if row.row_ref == 'MDR1')
    assert row.disposition == 'needs_more_evidence'


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
            LocalFileCard(ref='LF1', path='Extras/Title Bonus.mkv', is_main=True, file_kind='video')
        ],
        local_span_cards=[
            LocalSpanCard(
                ref='LS1',
                span_scope='token_segment',
                file_refs=['LF1'],
                file_ref_count=1,
                file_ref_samples=['LF1'],
                    title_cues=['Title Bonus'],
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
                    reason_kind='bonus_video',
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
            QueryCandidate(query_text='OVERLORD season 1 Bangumi', source_refs=['LS1'], reason='search meta words should be stripped', confidence='medium'),
            QueryCandidate(query_text='OVERLORD 2015 Bangumi', source_refs=['LS1'], reason='year plus search meta should be stripped', confidence='medium'),
            QueryCandidate(query_text='OVERLORD II Bangumi', source_refs=['LS1'], reason='roman numeral title should be preserved', confidence='medium'),
            QueryCandidate(query_text='OVERLORD III season 3 Bangumi', source_refs=['LS1'], reason='season scope should be stripped after title', confidence='medium'),
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
    assert 'OVERLORD' in query_texts
    assert 'OVERLORD II' in query_texts
    assert 'OVERLORD III' in query_texts
    assert 'OVERLORD season 1 Bangumi' not in query_texts
    assert 'OVERLORD 2015 Bangumi' not in query_texts
    assert 'OVERLORD II Bangumi' not in query_texts
    assert 'OVERLORD III season 3 Bangumi' not in query_texts
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
    assert audit['session_mode'] == 'http_history_replay'


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


def test_orchestrator_provider_no_response_does_not_consume_semantic_turn_budget():
    workspace = _mapping_workspace()
    object.__setattr__(workspace, 'mapping_draft', MappingDraft(rows=[
        MappingDraftRow(
            row_ref='MDR1',
            local_ref='LS1',
            local_ref_kind='span',
            disposition='map_to_bangumi',
            status='verified',
            selected_target_ref='BE1',
            selected_target_kind='item',
            mapping_mode='explicit',
            support_refs=['LS1', 'BE1'],
        ),
    ], version=2))
    client = _RunClient([
        None,
        {
            'id': 'resp_finish',
            'tool_calls': [
                {
                    'call_id': 'call_finish',
                    'name': 'finish_case',
                    'arguments': json.dumps(_accepted_finish_args(reason='accounting ready', mapped=1, excluded=0, outcome_kind='mapped', file_count=1, support_refs=['LS1', 'BE1'])),
                }
            ],
        },
    ])

    result = run_local_bangumi_case_agent(workspace, client, object(), max_rounds=1)

    assert result.status == 'accepted'
    assert any(
        isinstance(audit, dict)
        and audit.get('note') == 'orchestrator_agent_transport_retry'
        and audit.get('turn_index_unchanged') == 0
        for audit in result.final_workspace.judge_request_audits
    )
    session_summary = next(
        audit
        for audit in result.final_workspace.judge_request_audits
        if isinstance(audit, dict) and audit.get('note') == 'orchestrator_agent_session_summary'
    )
    assert session_summary['orchestrator_turn_count'] == 1
    assert session_summary['tool_rejection_count'] == 0


def test_orchestrator_agent_requires_finish_case_after_verified_mapping_intent_without_legacy_judge_loop():
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
                        'arguments': json.dumps(_accepted_finish_args(reason='accounting ready', mapped=1, excluded=0, outcome_kind='mapped', file_count=1, support_refs=['LS1', 'BE1'])),
                    }
                ],
            },
        ],
    )

    result = run_local_bangumi_case_agent(_mapping_workspace(), client, object(), max_rounds=2)

    assert result.ok is True
    assert result.status == 'accepted'
    assert result.final_verifier_result is not None
    assert result.final_verifier_result.passed is True
    assert result.final_output is not None
    assert len(result.final_output.assignment_intents) == 1
    assert client.mapping_editor_calls == 0
    assert client.case_judge_calls == 0
    assert [
        audit.get('tool_name')
        for audit in result.final_workspace.judge_request_audits
        if isinstance(audit, dict) and audit.get('note') == 'orchestrator_agent_called'
    ] == ['propose_mapping_intents', 'finish_case']
    assert not any(
        isinstance(audit, dict)
        and audit.get('note') == 'orchestrator_auto_finish_accepted_after_verified_draft'
        for audit in result.final_workspace.judge_request_audits
    )


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
                        'arguments': json.dumps(_accepted_finish_args(reason='accounting ready', mapped=2, excluded=0, outcome_kind='mapped', file_count=2, support_refs=['LS1', 'BE1', 'BE2'])),
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


def test_split_into_child_cases_rejects_duplicate_or_missing_main_refs():
    workspace = _split_root_workspace()
    tool_call = OrchestratorAgentToolCall(
        tool_name='split_into_child_cases',
        arguments=SplitIntoChildCasesToolArgs(
            reason='bad split',
            split_cases=[
                SplitCaseSpec(child_case_ref='C1', main_file_refs=['LF1']),
                SplitCaseSpec(child_case_ref='C2', main_file_refs=['LF1']),
            ],
        ),
        raw_arguments={
            'reason': 'bad split',
            'split_cases': [
                {'child_case_ref': 'C1', 'main_file_refs': ['LF1']},
                {'child_case_ref': 'C2', 'main_file_refs': ['LF1']},
            ],
        },
    )

    _workspace, decision, acceptance = _decision_from_orchestrator_tool_call(workspace, tool_call)

    assert decision is not None
    assert decision.action == 'split_into_child_cases'
    # The call is phase-legal; mechanical coverage is rejected by the tool output,
    # not by a semantic gate in the decision layer.
    from src.rename.case_agent.orchestrator import (
        _run_orchestrator_split_into_child_cases_tool,
        _split_decision_required,
        _workspace_with_judge_audit,
    )
    split_required_workspace = _workspace_with_judge_audit(workspace, {
        'note': 'orchestrator_split_decision_required',
        'reason': 'large mixed package',
    })
    result, updated, observation = _run_orchestrator_split_into_child_cases_tool(
        split_required_workspace,
        tool_call.arguments,
        _RunClient([]),
        object(),
        [],
        [],
        planning_depth=0,
        max_rounds=1,
        orchestrator_context_soft_token_limit=None,
        orchestrator_context_hard_token_limit=None,
    )

    assert result is None
    assert observation['status'] == 'rejected'
    assert 'split_duplicate_main_ref' in observation['issue_codes']
    assert 'split_duplicate_main_ref' in observation['verifier_issue_codes']
    assert 'split_missing_main_ref' in observation['verifier_issue_codes']
    assert observation['duplicate_main_refs'] == ['LF1']
    assert observation['missing_main_refs'] == ['LF2']
    assert observation['issues']
    assert any(issue.issue_code == 'split_duplicate_main_ref' for issue in updated.verifier_issues)
    assert _split_decision_required(updated) is True
    assert acceptance['accepted'] is True


def test_split_into_child_cases_tool_schema_supports_record_plan_mode():
    schema = SplitIntoChildCasesToolArgs.model_json_schema()
    execution_schema = schema['properties']['execution_mode']

    assert set(execution_schema.get('enum') or []) == {'run_child_cases', 'record_split_plan_only'}


def test_payload_promotes_pending_split_to_package_boundary_decision_board():
    from src.rename.case_agent.orchestrator import _workspace_with_judge_audit

    workspace = _workspace_with_judge_audit(_split_root_workspace(), {
        'note': 'orchestrator_split_decision_required',
        'reason': 'multi-unit package',
        'work_unit_count': 2,
        'main_file_count': 2,
        'split_case_skeleton_from_work_units': [
            {
                'child_case_ref': 'SPLIT1',
                'main_group_refs': ['LG1'],
                'expanded_main_file_count': 1,
                'expanded_main_file_range': ['LF1', 'LF1'],
                'title_hints': ['OVERLORD'],
                'reason': 'regular season unit',
            },
            {
                'child_case_ref': 'SPLIT2',
                'main_group_refs': ['LG2'],
                'expanded_main_file_count': 1,
                'expanded_main_file_range': ['LF2', 'LF2'],
                'title_hints': ['Menu'],
                'reason': 'menu/navigation extra',
            },
        ],
    })

    payload = json.loads(build_orchestrator_agent_input(workspace, reason='after understanding'))
    board = payload['package_boundary_decision_board']

    assert payload['case_desk_priority'][0]['desk'] == 'package_boundary_decision_board'
    assert board['active'] is True
    assert board['major_unit_like_count'] == 1
    assert board['packaging_or_extra_like_count'] == 1
    assert {option['option'] for option in board['human_like_options']} >= {
        'record_split_plan_only',
        'selected_child_deep_dive',
        'root_resolution_ledger',
        'complete_split',
    }
    assert 'root_mapping_before_boundary_decision_may_stall' not in board['self_review_flags']
    assert any('package_boundary_decision_board.active' in rule for rule in payload['rules'])


def test_package_boundary_decision_board_closes_after_selected_child_result():
    from src.rename.case_agent.orchestrator import _workspace_with_judge_audit

    workspace = _workspace_with_judge_audit(_split_root_workspace(), {
        'note': 'orchestrator_split_decision_required',
        'reason': 'multi-unit package',
        'split_case_skeleton_from_work_units': [
            {'child_case_ref': 'SPLIT1', 'main_group_refs': ['LG1'], 'expanded_main_file_count': 1},
        ],
    })
    workspace = _workspace_with_judge_audit(workspace, {
        'note': 'orchestrator_selected_child_cases_result',
        'coverage_mode': 'selected_child_cases',
    })

    payload = json.loads(build_orchestrator_agent_input(workspace, reason='after selected child result'))

    assert payload['split_decision']['pending'] is False
    assert payload['package_boundary_decision_board']['active'] is False
    assert all(item['desk'] != 'package_boundary_decision_board' for item in payload['case_desk_priority'])


def test_split_into_child_cases_record_plan_only_does_not_run_children_and_keeps_child_run_path_visible():
    from src.rename.case_agent.orchestrator import _split_decision_required, _workspace_with_judge_audit

    workspace = _workspace_with_judge_audit(_split_root_workspace(), {
        'note': 'orchestrator_split_decision_required',
        'reason': 'multi-unit package',
    })
    args = SplitIntoChildCasesToolArgs(
        reason='record root boundary before selected child deep-dive',
        execution_mode='record_split_plan_only',
        coverage_mode='selected_child_cases',
        split_cases=[SplitCaseSpec(child_case_ref='C1', main_file_refs=['LF1'], support_refs=['LF1'])],
    )

    result, updated, observation = _run_orchestrator_split_into_child_cases_tool(
        workspace,
        args,
        object(),
        object(),
        [],
        [],
        planning_depth=0,
        max_rounds=1,
        orchestrator_context_soft_token_limit=None,
        orchestrator_context_hard_token_limit=None,
    )

    assert result is None
    assert observation['status'] == 'split_plan_recorded'
    assert observation['execution_mode'] == 'record_split_plan_only'
    assert observation['target_surface_changed'] is False
    assert _split_decision_required(updated) is True
    assert any(
        audit.get('note') == 'orchestrator_split_plan_recorded'
        for audit in updated.judge_request_audits
        if isinstance(audit, dict)
    )
    payload = json.loads(build_orchestrator_agent_input(updated, reason='after record split plan'))
    assert payload['recorded_split_plan']['active'] is True
    assert payload['recorded_split_plan']['plan_row_refs'] == ['RSP1']
    assert payload['recorded_split_plan']['split_cases'][0]['plan_row_ref'] == 'RSP1'
    assert payload['recorded_split_plan']['split_cases'][0]['child_case_ref'] == 'C1'
    assert payload['recorded_split_plan']['run_selected_child_cases_args_template']['recorded_child_case_refs'] == ['C1']
    assert payload['package_boundary_decision_board']['active'] is True
    assert {item['option'] for item in payload['package_boundary_decision_board']['human_like_options']} >= {
        'run_from_recorded_split_plan',
        'root_resolution_ledger',
    }
    assert 'record_split_plan_only' not in {
        item['option'] for item in payload['package_boundary_decision_board']['human_like_options']
    }
    assert any(item['desk'] == 'recorded_split_plan' for item in payload['case_desk_priority'])


def test_split_into_child_cases_can_run_from_recorded_child_case_refs(monkeypatch):
    from src.rename.case_agent.orchestrator import _workspace_with_judge_audit

    workspace = _workspace_with_judge_audit(_split_root_workspace(), {
        'note': 'orchestrator_split_decision_required',
        'reason': 'multi-unit package',
    })
    record_args = SplitIntoChildCasesToolArgs(
        reason='record root boundary',
        execution_mode='record_split_plan_only',
        coverage_mode='selected_child_cases',
        split_cases=[
            SplitCaseSpec(child_case_ref='C1', main_group_refs=['LG1'], support_refs=['LG1']),
            SplitCaseSpec(child_case_ref='C2', main_group_refs=['LG2'], support_refs=['LG2']),
        ],
    )
    result, recorded, _observation = _run_orchestrator_split_into_child_cases_tool(
        workspace,
        record_args,
        object(),
        object(),
        [],
        [],
        planning_depth=0,
        max_rounds=1,
        orchestrator_context_soft_token_limit=None,
        orchestrator_context_hard_token_limit=None,
    )
    assert result is None

    child_workspaces: list[list[str]] = []

    def fake_child_runner(child_workspace, *_args, **_kwargs):
        child_workspaces.append(list(child_workspace.contract.main_file_refs))
        return CaseAgentRunResult(
            ok=False,
            case_id=child_workspace.header.case_id,
            status='fail_closed',
            final_action='finish_case',
            final_output=CaseJudgeOutput(action='fail_closed', summary='child blocker'),
            final_verifier_result=CaseVerifierResult(passed=True, summary='ok'),
            final_workspace=child_workspace,
            judge_outputs=[],
            evidence_batches=[],
            summary='child blocker',
            errors=[],
        )

    monkeypatch.setattr('src.rename.case_agent.orchestrator._run_orchestrator_agent_main_loop', fake_child_runner)

    run_args = SplitIntoChildCasesToolArgs(
        reason='run selected child from recorded plan',
        execution_mode='run_child_cases',
        coverage_mode='selected_child_cases',
        recorded_child_case_refs=['C2'],
    )
    result, _updated, observation = _run_orchestrator_split_into_child_cases_tool(
        recorded,
        run_args,
        object(),
        object(),
        [],
        [],
        planning_depth=0,
        max_rounds=1,
        orchestrator_context_soft_token_limit=None,
        orchestrator_context_hard_token_limit=None,
    )

    assert result is None
    assert observation['status'] == 'selected_child_cases_completed_with_blockers'
    assert observation['recorded_child_case_refs_used'] == ['C2']
    assert child_workspaces == [['LF2']]


def test_split_into_child_cases_explicit_specs_do_not_require_recorded_refs(monkeypatch):
    workspace = _split_root_workspace()
    child_workspaces: list[list[str]] = []

    def fake_child_runner(child_workspace, *_args, **_kwargs):
        child_workspaces.append(list(child_workspace.contract.main_file_refs))
        return CaseAgentRunResult(
            ok=False,
            case_id=child_workspace.header.case_id,
            status='fail_closed',
            final_action='finish_case',
            final_output=CaseJudgeOutput(action='fail_closed', summary='child blocker'),
            final_verifier_result=CaseVerifierResult(passed=True, summary='ok'),
            final_workspace=child_workspace,
            judge_outputs=[],
            evidence_batches=[],
            summary='child blocker',
            errors=[],
        )

    monkeypatch.setattr('src.rename.case_agent.orchestrator._run_orchestrator_agent_main_loop', fake_child_runner)

    args = SplitIntoChildCasesToolArgs(
        reason='run explicit split while stale recorded refs are present',
        execution_mode='run_child_cases',
        coverage_mode='selected_child_cases',
        recorded_child_case_refs=['SPLIT_DOES_NOT_EXIST'],
        split_cases=[SplitCaseSpec(child_case_ref='C1', main_file_refs=['LF1'], support_refs=['LF1'])],
    )
    result, _updated, observation = _run_orchestrator_split_into_child_cases_tool(
        workspace,
        args,
        object(),
        object(),
        [],
        [],
        planning_depth=0,
        max_rounds=1,
        orchestrator_context_soft_token_limit=None,
        orchestrator_context_hard_token_limit=None,
    )

    assert result is None
    assert observation['status'] == 'selected_child_cases_completed_with_blockers'
    assert observation['recorded_child_case_refs_missing'] == ['SPLIT_DOES_NOT_EXIST']
    assert child_workspaces == [['LF1']]


def test_split_into_child_cases_rejects_large_child_execution_batch_as_runtime_budget():
    file_refs = ['LF1', 'LF2', 'LF3']
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-SPLIT-BUDGET'),
        budget=CaseBudget(max_judge_rounds=4, max_evidence_batches=2, max_requests_per_batch=2),
        contract=CaseContract(main_file_refs=file_refs, allowed_file_refs=file_refs),
        local_files=[
            LocalFileCard(ref=ref, path=f'Season {index}/Title {index:02d}.mkv', is_main=True, label=f'Title {index:02d}.mkv')
            for index, ref in enumerate(file_refs, start=1)
        ],
    )

    result, updated, observation = _run_orchestrator_split_into_child_cases_tool(
        workspace,
        SplitIntoChildCasesToolArgs(
            reason='try to run too many child sessions at once',
            execution_mode='run_child_cases',
            coverage_mode='complete_root_coverage',
            split_cases=[
                SplitCaseSpec(child_case_ref='C1', main_file_refs=['LF1']),
                SplitCaseSpec(child_case_ref='C2', main_file_refs=['LF2']),
                SplitCaseSpec(child_case_ref='C3', main_file_refs=['LF3']),
            ],
        ),
        object(),
        object(),
        [],
        [],
        planning_depth=0,
        max_rounds=4,
        orchestrator_context_soft_token_limit=None,
        orchestrator_context_hard_token_limit=None,
    )

    assert result is None
    assert observation['status'] == 'rejected'
    assert observation['reason'] == 'child_execution_batch_too_large'
    assert observation['max_child_cases_per_tool_call'] == 2
    assert any(
        audit.get('note') == 'orchestrator_child_execution_budget_rejected'
        for audit in updated.judge_request_audits
        if isinstance(audit, dict)
    )


def test_default_orchestrator_turn_budget_scales_for_large_root_contracts():
    workspace = _split_root_workspace()
    large_contract = workspace.contract.model_copy(update={'main_file_refs': [f'LF{i}' for i in range(1, 124)]})
    large_workspace = replace(workspace, contract=large_contract)

    assert _default_orchestrator_max_turns_for_workspace(workspace) == 4
    assert _default_orchestrator_max_turns_for_workspace(large_workspace) == 18


def test_split_into_child_cases_allows_local_group_refs_as_support_refs():
    workspace = _split_root_workspace()
    tool_call = OrchestratorAgentToolCall(
        tool_name='split_into_child_cases',
        arguments=SplitIntoChildCasesToolArgs(
            reason='split by visible local groups',
            split_cases=[
                SplitCaseSpec(child_case_ref='C1', main_group_refs=['LG1'], support_refs=['LG1']),
                SplitCaseSpec(child_case_ref='C2', main_group_refs=['LG2'], support_refs=['LG2']),
            ],
        ),
        raw_arguments={
            'reason': 'split by visible local groups',
            'split_cases': [
                {'child_case_ref': 'C1', 'main_group_refs': ['LG1'], 'support_refs': ['LG1']},
                {'child_case_ref': 'C2', 'main_group_refs': ['LG2'], 'support_refs': ['LG2']},
            ],
        },
    )

    _workspace, decision, acceptance = _decision_from_orchestrator_tool_call(workspace, tool_call)

    assert decision is not None
    assert decision.action == 'split_into_child_cases'
    assert acceptance['accepted'] is True
    assert acceptance.get('ref_issue_refs') in (None, [])


def test_split_into_child_cases_rejects_at_configured_depth_limit():
    workspace = _split_root_workspace()
    args = SplitIntoChildCasesToolArgs(
        reason='valid split at depth limit',
        split_cases=[
            SplitCaseSpec(child_case_ref='C1', main_file_refs=['LF1']),
            SplitCaseSpec(child_case_ref='C2', main_file_refs=['LF2']),
        ],
    )

    from src.rename.case_agent.orchestrator import _run_orchestrator_split_into_child_cases_tool
    result, _updated, observation = _run_orchestrator_split_into_child_cases_tool(
        workspace,
        args,
        _RunClient([]),
        object(),
        [],
        [],
        planning_depth=3,
        max_rounds=1,
        orchestrator_context_soft_token_limit=None,
        orchestrator_context_hard_token_limit=None,
    )

    assert result is None
    assert observation['status'] == 'rejected'
    assert observation['reason'] == 'split_depth_limit_reached'
    assert observation['max_split_depth'] == 3


def test_split_depth_limit_rejection_unblocks_normal_tools_after_required_split():
    from src.rename.case_agent.orchestrator import _split_decision_required, _workspace_with_judge_audit

    workspace = _workspace_with_judge_audit(_split_root_workspace(), {
        'note': 'orchestrator_split_decision_required',
        'reason': 'multi-unit package',
    })
    workspace = _workspace_with_judge_audit(workspace, {
        'note': 'orchestrator_tool_output_rejected',
        'tool_name': 'split_into_child_cases',
        'reason': 'split_depth_limit_reached',
        'recommended_next_observation': 'handle this nested child as one case',
    })

    payload = json.loads(build_orchestrator_agent_input(workspace, reason='after depth limit'))

    assert _split_decision_required(workspace) is False
    assert payload['available_tool_names'] != ['split_into_child_cases']
    assert 'propose_mapping_intents' in payload['available_tool_names']
    assert 'materialize_queries' in payload['available_tool_names']
    assert 'finish_case' in payload['available_tool_names']


def test_split_decision_pending_is_visible_in_orchestrator_payload():
    from src.rename.case_agent.orchestrator import _workspace_with_judge_audit

    workspace = _workspace_with_judge_audit(_split_root_workspace(), {
        'note': 'orchestrator_split_decision_required',
        'reason': 'multi-unit package',
        'work_unit_count': 2,
        'main_file_count': 2,
        'recommended_next_observation': 'call split_into_child_cases or explicitly continue root resolution',
    })

    payload = json.loads(build_orchestrator_agent_input(workspace, reason='split pending'))

    assert payload['split_decision']['pending'] is True
    assert payload['split_decision']['work_unit_count'] == 2
    assert payload['split_decision']['main_file_count'] == 2
    assert 'split_into_child_cases' in payload['available_tool_names']
    assert any(
        observation.get('note') == 'orchestrator_split_decision_required'
        for observation in payload['recent_tool_observations']
    )


def test_depth_limit_case_understanding_does_not_force_split_decision():
    from src.rename.case_agent.orchestrator import _workspace_with_judge_audit

    file_refs = ['LF1', 'LF2', 'LF3']
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-DEEP-SPLIT'),
        budget=CaseBudget(max_judge_rounds=4, max_evidence_batches=2, max_requests_per_batch=2),
        contract=CaseContract(main_file_refs=file_refs, allowed_file_refs=file_refs),
        local_files=[
            LocalFileCard(ref='LF1', path='Season/Title 01.mkv', is_main=True, label='Title 01.mkv'),
            LocalFileCard(ref='LF2', path='Season/Title 02.mkv', is_main=True, label='Title 02.mkv'),
            LocalFileCard(ref='LF3', path='Season/Preview.mkv', is_main=True, label='Preview.mkv'),
        ],
    )
    workspace = _workspace_with_judge_audit(workspace, {
        'note': 'orchestrator_case_session_started',
        'planning_depth': 3,
        'max_split_depth': 3,
    })
    args = ProposeCaseUnderstandingToolArgs(
        reason='deep child understanding',
        package_shape='regular span plus extra',
        work_units=[
            CaseBriefingWorkUnit(
                work_unit_ref='WU1',
                label='regular span',
                file_refs=['LF1', 'LF2'],
                local_refs=['LF1', 'LF2'],
            ),
            CaseBriefingWorkUnit(
                work_unit_ref='WU2',
                label='preview extra',
                file_refs=['LF3'],
                local_refs=['LF3'],
            ),
        ],
    )

    updated, observation = _compile_case_understanding(workspace, args)
    payload = json.loads(build_orchestrator_agent_input(updated, reason='deep child after understanding'))

    assert observation['status'] == 'ok'
    assert observation['split_decision_required'] is False
    assert not any(
        isinstance(audit, dict)
        and audit.get('note') == 'orchestrator_split_decision_required'
        for audit in updated.judge_request_audits
    )
    assert 'propose_mapping_intents' in payload['available_tool_names']


def test_split_into_child_cases_canonicalizes_root_main_refs_from_supplemental():
    client = _RunClient([
        {
            'id': 'child_understand',
            'tool_calls': [
                {
                    'call_id': 'child_understand',
                    'name': 'propose_case_understanding',
                    'arguments': json.dumps({
                        'reason': 'understand canonicalized child',
                        'package_shape': 'two-file child',
                        'work_units': [
                            {
                                'work_unit_ref': 'WU1',
                                'label': 'two files',
                                'file_refs': ['LF1', 'LF2'],
                                'local_refs': ['LF1', 'LF2'],
                            }
                        ],
                    }),
                }
            ],
        },
        {
            'id': 'child_map',
            'tool_calls': [
                {
                    'call_id': 'child_map',
                    'name': 'propose_mapping_intents',
                    'arguments': json.dumps({
                        'reason': 'target absent but accounted',
                        'mapping_intents': [
                                {
                                    'decision': 'mark_non_bangumi_or_supplemental',
                                        'local_ref': 'LS1',
                                        'reason_kind': 'bangumi_target_absent',
                                        'support_refs': ['LS1', 'BS1'],
                                        'reason': 'no visible target in this focused unit test',
                                    }
                        ],
                    }),
                }
            ],
        },
        {
            'id': 'child_finish',
            'tool_calls': [
                {
                    'call_id': 'child_finish',
                    'name': 'finish_case',
                            'arguments': json.dumps(_accepted_finish_args(reason='accounting ready', mapped=0, excluded=2, outcome_kind='target_absent', file_count=2, support_refs=['LS1', 'BS1'])),
                }
            ],
        },
        {
            'id': 'child_finish',
            'tool_calls': [
                {
                    'call_id': 'child_finish',
                    'name': 'finish_case',
                            'arguments': json.dumps(_accepted_finish_args(reason='child accounting ready', mapped=0, excluded=2, outcome_kind='target_absent', file_count=2, support_refs=['LS1', 'BS1'])),
                }
            ],
        },
    ])
    from src.rename.case_agent.orchestrator import _run_orchestrator_split_into_child_cases_tool

    result, updated, observation = _run_orchestrator_split_into_child_cases_tool(
        _split_root_workspace(),
        SplitIntoChildCasesToolArgs(
            reason='agent put one root main in supplemental',
            split_cases=[
                SplitCaseSpec(child_case_ref='C1', main_file_refs=['LF1'], supplemental_file_refs=['LF2'], support_refs=['BS1']),
            ],
        ),
        client,
        object(),
        [],
        [],
        planning_depth=0,
        max_rounds=3,
        orchestrator_context_soft_token_limit=None,
        orchestrator_context_hard_token_limit=None,
    )

    assert observation['split_main_refs_canonicalized'] is True
    assert observation['moved_main_refs_from_supplemental_by_child'] == {'C1': ['LF2']}
    assert result is not None
    assert result.child_results[0].final_workspace.contract.main_file_refs == ['LF1', 'LF2']
    assert any(
        isinstance(audit, dict)
        and audit.get('note') == 'orchestrator_split_into_child_cases_requested'
        and audit.get('split_main_refs_canonicalized') is True
        for audit in updated.judge_request_audits
    )


def test_large_multi_unit_understanding_requires_split_decision_before_more_queries():
    file_refs = [f'LF{index}' for index in range(1, 25)]
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-LARGE-SPLIT'),
        budget=CaseBudget(max_requests_per_batch=8),
        contract=CaseContract(main_file_refs=file_refs, allowed_file_refs=file_refs),
        local_files=[
            LocalFileCard(ref=ref, path=f'Season {1 if index <= 12 else 2}/Title {index:02d}.mkv', is_main=True)
            for index, ref in enumerate(file_refs, start=1)
        ],
    )
    understanding = ProposeCaseUnderstandingToolArgs(
        reason='large two-unit package',
        package_shape='two seasons',
        work_units=[
            {
                'work_unit_ref': 'WU1',
                'label': 'season 1',
                'file_refs': file_refs[:12],
                'local_refs': file_refs[:12],
                'title_hints': ['Title'],
            },
            {
                'work_unit_ref': 'WU2',
                'label': 'season 2',
                'file_refs': file_refs[12:],
                'local_refs': file_refs[12:],
                'title_hints': ['Title II'],
            },
        ],
        summary='two coherent seasons',
    )
    updated, observation = _compile_case_understanding(workspace, understanding)

    assert observation['split_decision_required'] is True
    assert any(
        audit.get('note') == 'orchestrator_split_decision_required'
        for audit in updated.judge_request_audits
        if isinstance(audit, dict)
    )

    query_call = OrchestratorAgentToolCall(
        tool_name='materialize_queries',
        arguments=MaterializeQueriesToolArgs(reason='keep querying before split'),
        raw_arguments={'reason': 'keep querying before split'},
        call_id='call_query',
    )
    _workspace, decision, acceptance = _decision_from_orchestrator_tool_call(updated, query_call)

    assert acceptance['accepted'] is True
    assert decision is not None
    assert decision.action == 'compose_queries'

    split_call = OrchestratorAgentToolCall(
        tool_name='split_into_child_cases',
        arguments=SplitIntoChildCasesToolArgs(
            reason='split after understanding',
            split_cases=[
                SplitCaseSpec(child_case_ref='S1', main_file_refs=file_refs[:12]),
                SplitCaseSpec(child_case_ref='S2', main_file_refs=file_refs[12:]),
            ],
        ),
        raw_arguments={'reason': 'split after understanding'},
        call_id='call_split',
    )
    _workspace, split_decision, split_acceptance = _decision_from_orchestrator_tool_call(updated, split_call)

    assert split_acceptance['accepted'] is True
    assert split_decision is not None
    assert split_decision.action == 'split_into_child_cases'


def test_small_multi_unit_understanding_does_not_force_split_decision():
    workspace = _split_root_workspace()
    updated, observation = _compile_case_understanding(
        workspace,
        ProposeCaseUnderstandingToolArgs(
            reason='small two-unit package',
            package_shape='two singleton units',
            work_units=[
                {'work_unit_ref': 'WU1', 'label': 'unit 1', 'file_refs': ['LF1'], 'local_refs': ['LF1']},
                {'work_unit_ref': 'WU2', 'label': 'unit 2', 'file_refs': ['LF2'], 'local_refs': ['LF2']},
            ],
            summary='small package',
        ),
    )
    query_call = OrchestratorAgentToolCall(
        tool_name='materialize_queries',
        arguments=MaterializeQueriesToolArgs(reason='query small package'),
        raw_arguments={'reason': 'query small package'},
        call_id='call_query',
    )
    _workspace, decision, acceptance = _decision_from_orchestrator_tool_call(updated, query_call)

    assert observation['split_decision_required'] is False
    assert acceptance['accepted'] is True
    assert decision is not None
    assert decision.action == 'compose_queries'


def test_small_multi_unit_with_non_singleton_unit_keeps_tools_available():
    file_refs = ['LF1', 'LF2', 'LF3']
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-SMALL-MIXED-SPLIT'),
        budget=CaseBudget(max_requests_per_batch=4),
        contract=CaseContract(main_file_refs=file_refs, allowed_file_refs=file_refs),
        local_files=[
            LocalFileCard(ref='LF1', path='Movie/Part 1.mkv', is_main=True),
            LocalFileCard(ref='LF2', path='Movie/Part 2.mkv', is_main=True),
            LocalFileCard(ref='LF3', path='Movie/SP.mkv', is_main=True),
        ],
    )
    updated, observation = _compile_case_understanding(
        workspace,
        ProposeCaseUnderstandingToolArgs(
            reason='small mixed package',
            package_shape='movie plus special',
            work_units=[
                {'work_unit_ref': 'WU1', 'label': 'movie parts', 'file_refs': ['LF1', 'LF2'], 'local_refs': ['LF1', 'LF2']},
                {'work_unit_ref': 'WU2', 'label': 'special', 'file_refs': ['LF3'], 'local_refs': ['LF3']},
            ],
            summary='small mixed package',
        ),
    )
    query_call = OrchestratorAgentToolCall(
        tool_name='materialize_queries',
        arguments=MaterializeQueriesToolArgs(reason='query before split'),
        raw_arguments={'reason': 'query before split'},
        call_id='call_query',
    )
    _workspace, decision, acceptance = _decision_from_orchestrator_tool_call(updated, query_call)

    assert observation['split_decision_required'] is True
    assert acceptance['accepted'] is True
    assert decision is not None
    assert decision.action == 'compose_queries'


def test_materialize_queries_allowed_when_fresh_query_evidence_exists():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-QC-PENDING'),
        budget=CaseBudget(max_evidence_batches=3, max_requests_per_batch=2),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1']),
        local_files=[LocalFileCard(ref='LF1', path='Title 01.mkv', is_main=True, label='Title 01.mkv')],
        query_cards=[
            QueryCard(
                ref='QC1',
                query_text='Title',
                query_kind='subject_search',
                query_origin='agent_composed',
                source_refs=['LF1'],
            )
        ],
        case_briefing=CaseBriefingOutput(
            package_shape='single file',
            work_units=[CaseBriefingWorkUnit(work_unit_ref='WU1', label='single file', file_refs=['LF1'], local_refs=['LF1'])],
        ),
    )
    tool_call = OrchestratorAgentToolCall(
        tool_name='materialize_queries',
        arguments=MaterializeQueriesToolArgs(
            reason='repeat query before executing it',
            queries=[QueryCandidate(query_text='Title', source_refs=['LF1'])],
        ),
        raw_arguments={'reason': 'repeat query before executing it'},
    )

    _workspace, decision, acceptance = _decision_from_orchestrator_tool_call(workspace, tool_call)

    assert acceptance['accepted'] is True
    assert decision is not None
    assert decision.action == 'compose_queries'


def test_pending_requested_evidence_does_not_narrow_available_tools_to_execute_only():
    workspace = _regular_span_workspace()
    object.__setattr__(workspace, 'judge_request_audits', [
        {
            'note': 'orchestrator_mapping_intents_result',
            'blocked_intent_count': 1,
            'requested_evidence': ['target_span'],
        }
    ])

    payload = json.loads(build_orchestrator_agent_input(workspace, reason='pending evidence'))

    assert 'execute_evidence' in payload['available_tool_names']
    assert 'propose_mapping_intents' in payload['available_tool_names']
    assert 'propose_case_resolution_ledger' in payload['available_tool_names']


def test_count_mismatch_mapping_blocker_makes_case_understanding_revision_available():
    workspace = _regular_span_workspace()
    object.__setattr__(workspace, 'bangumi_items', [
        BangumiItemCard(ref='BE1', subject_ref='BS1', sort=1, ep=1, item_kind='episode'),
    ])
    object.__setattr__(workspace, 'contract', workspace.contract.model_copy(update={'visible_target_refs': ['BE1']}))
    client = _RunClient([
        {
            'id': 'resp_bad_shape',
            'tool_calls': [
                {
                    'call_id': 'call_bad_shape',
                    'name': 'propose_mapping_intents',
                    'arguments': json.dumps({
                        'mapping_intents': [
                            {
                                'decision': 'map_regular_span',
                                'local_ref': 'LS1',
                                'chosen_subject_ref': 'BS1',
                                'item_refs': ['BE1'],
                                'support_refs': ['LS1', 'BE1'],
                                'reason': 'one item for multi-file row',
                            }
                        ],
                    }),
                }
            ],
        },
    ])

    result = run_local_bangumi_case_agent(workspace, client, object(), max_rounds=1)
    payload = json.loads(build_orchestrator_agent_input(result.final_workspace, reason='after structural blocker'))

    assert any(
        isinstance(audit, dict)
        and audit.get('note') == 'case_understanding_repartition_requested'
        and 'item_ref_count_mismatch' in audit.get('issue_codes', [])
        for audit in result.final_workspace.judge_request_audits
    )
    assert 'propose_case_understanding' in payload['available_tool_names']
    assert 'propose_mapping_intents' in payload['available_tool_names']
    assert payload['case_understanding']['revision_available_now'] is True


def test_structural_mapping_blocker_does_not_force_repartition_when_same_count_surface_exists():
    client = _RunClient([
        {
            'id': 'resp_bad_shape',
            'tool_calls': [
                {
                    'call_id': 'call_bad_shape',
                    'name': 'propose_mapping_intents',
                    'arguments': json.dumps({
                        'mapping_intents': [
                            {
                                'decision': 'map_explicit_item',
                                'local_ref': 'LS1',
                                'chosen_item_ref': 'BE1',
                                'support_refs': ['LS1', 'BE1'],
                                'reason': 'single item for multi-file row',
                            }
                        ],
                    }),
                }
            ],
        },
    ])

    result = run_local_bangumi_case_agent(_regular_span_workspace(), client, object(), max_rounds=1)
    payload = json.loads(build_orchestrator_agent_input(result.final_workspace, reason='after structural blocker'))

    assert not any(
        isinstance(audit, dict)
        and audit.get('note') == 'case_understanding_repartition_requested'
        for audit in result.final_workspace.judge_request_audits
    )
    assert 'propose_mapping_intents' in payload['available_tool_names']
    assert payload['case_understanding']['revision_available_now'] is False


def test_invalid_explicit_multi_file_mapping_guides_intent_revision_without_forcing_repartition():
    workspace = _regular_span_workspace()
    object.__setattr__(workspace, 'bangumi_items', [
        BangumiItemCard(ref='BE1', subject_ref='BS1', sort=1, ep=1, item_kind='episode'),
    ])
    object.__setattr__(workspace, 'contract', workspace.contract.model_copy(update={'visible_target_refs': ['BE1']}))
    client = _RunClient([
        {
            'id': 'resp_bad_explicit_shape',
            'tool_calls': [
                {
                    'call_id': 'call_bad_explicit_shape',
                    'name': 'propose_mapping_intents',
                    'arguments': json.dumps({
                        'mapping_intents': [
                            {
                                'decision': 'map_explicit_item',
                                'local_ref': 'LS1',
                                'chosen_item_ref': 'BE1',
                                'support_refs': ['LS1', 'BE1'],
                                'reason': 'explicit item for multi-file row',
                            }
                        ],
                    }),
                }
            ],
        },
    ])

    result = run_local_bangumi_case_agent(workspace, client, object(), max_rounds=1)
    payload = json.loads(build_orchestrator_agent_input(result.final_workspace, reason='after explicit shape blocker'))

    assert any(
        isinstance(audit, dict)
        and audit.get('note') == 'orchestrator_mapping_intents_result'
        and 'invalid_explicit_multi_file_mapping' in audit.get('blocked_intent_issue_codes', [])
        for audit in result.final_workspace.judge_request_audits
    )
    assert not any(
        isinstance(audit, dict)
        and audit.get('note') == 'case_understanding_repartition_requested'
        for audit in result.final_workspace.judge_request_audits
    )
    assert 'propose_mapping_intents' in payload['available_tool_names']
    assert payload['case_understanding']['revision_available_now'] is False


def test_repartition_revision_rejects_same_multi_file_unit():
    workspace = _prepare_workspace_for_orchestrator_agent_turn(_regular_span_workspace())
    object.__setattr__(workspace, 'judge_request_audits', [
        {
            'note': 'case_understanding_repartition_requested',
            'issue_codes': ['invalid_explicit_multi_file_mapping'],
        }
    ])
    args = ProposeCaseUnderstandingToolArgs(
        reason='no-op repartition',
        package_shape='same two files',
        work_units=[
            CaseBriefingWorkUnit(
                work_unit_ref='WU1',
                label='same row',
                file_refs=['LF1', 'LF2'],
                local_refs=['LF1', 'LF2'],
            )
        ],
    )

    _updated, observation = _compile_case_understanding(workspace, args)
    payload = json.loads(build_orchestrator_agent_input(_updated, reason='after noop repartition'))

    assert observation['status'] == 'rejected'
    assert 'case_understanding_noop_repartition' in observation['issue_codes']
    assert 'propose_case_understanding' in payload['available_tool_names']
    assert 'propose_mapping_intents' in payload['available_tool_names']


def test_understanding_revision_rejects_same_partition_when_boundary_decision_pending():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-BOUNDARY-NOOP'),
        budget=CaseBudget(max_judge_rounds=4, max_evidence_batches=2, max_requests_per_batch=2),
        contract=CaseContract(main_file_refs=['LF1', 'LF2', 'LF3', 'LF4'], allowed_file_refs=['LF1', 'LF2', 'LF3', 'LF4']),
        local_files=[
            LocalFileCard(ref='LF1', path='Season 1/Title 01.mkv', is_main=True, label='Title 01.mkv'),
            LocalFileCard(ref='LF2', path='Season 1/Title 02.mkv', is_main=True, label='Title 02.mkv'),
            LocalFileCard(ref='LF3', path='Season 2/Title II 01.mkv', is_main=True, label='Title II 01.mkv'),
            LocalFileCard(ref='LF4', path='Season 2/Title II 02.mkv', is_main=True, label='Title II 02.mkv'),
        ],
    )
    initial = ProposeCaseUnderstandingToolArgs(
        reason='initial multi unit package understanding',
        package_shape='two work units',
        work_units=[
            CaseBriefingWorkUnit(work_unit_ref='WU1', label='season 1', file_refs=['LF1', 'LF2'], local_refs=['LF1', 'LF2']),
            CaseBriefingWorkUnit(work_unit_ref='WU2', label='season 2', file_refs=['LF3', 'LF4'], local_refs=['LF3', 'LF4']),
        ],
    )
    workspace, observation = _compile_case_understanding(workspace, initial)
    assert observation['status'] == 'ok'
    assert observation['split_decision_required'] is True

    revision = ProposeCaseUnderstandingToolArgs(
        reason='restate the same boundary instead of choosing split or ledger',
        package_shape='same two work units',
        work_units=[
            CaseBriefingWorkUnit(work_unit_ref='WU1', label='season 1 restated', file_refs=['LF1', 'LF2'], local_refs=['LF1', 'LF2']),
            CaseBriefingWorkUnit(work_unit_ref='WU2', label='season 2 restated', file_refs=['LF3', 'LF4'], local_refs=['LF3', 'LF4']),
        ],
    )
    updated, observation = _compile_case_understanding(workspace, revision)

    assert observation['status'] == 'rejected'
    assert 'case_understanding_repeats_pending_boundary_without_partition_change' in observation['issue_codes']
    assert any(
        isinstance(audit, dict)
        and audit.get('note') == 'case_understanding_rejected'
        and 'case_understanding_repeats_pending_boundary_without_partition_change' in audit.get('issue_codes', [])
        for audit in updated.judge_request_audits
    )


def test_split_into_child_cases_runs_independent_child_sessions_and_aggregates_acceptance(monkeypatch):
    client = _RunClient(
        [
            {
                'id': 'child_1_understand',
                'tool_calls': [
                    {
                        'call_id': 'c1_understand',
                        'name': 'propose_case_understanding',
                        'arguments': json.dumps({
                            'reason': 'understand child 1',
                            'package_shape': 'single episode child',
                            'work_units': [
                                {
                                    'work_unit_ref': 'WU1',
                                    'label': 'child 1 episode',
                                    'file_refs': ['LF1'],
                                    'local_refs': ['LF1'],
                                    'title_hints': ['Title'],
                                }
                            ],
                            'summary': 'child 1 single file',
                        }),
                    }
                ],
            },
            {
                'id': 'child_1_map',
                'tool_calls': [
                    {
                        'call_id': 'c1_map',
                        'name': 'propose_mapping_intents',
                        'arguments': json.dumps({
                            'reason': 'map child 1',
                            'mapping_intents': [
                                {
                                    'decision': 'map_explicit_item',
                                    'local_ref': 'LS1',
                                    'chosen_item_ref': 'BE1',
                                    'support_refs': ['LS1', 'BE1'],
                                    'reason': 'child visible item',
                                }
                            ],
                        }),
                }
            ],
        },
        {
            'id': 'child_1_finish',
            'tool_calls': [
                {
                    'call_id': 'c1_finish',
                    'name': 'finish_case',
                    'arguments': json.dumps(_accepted_finish_args(reason='child 1 accounting ready', mapped=1, excluded=0, outcome_kind='mapped', file_count=1, support_refs=['LS1', 'BE1'])),
                }
            ],
        },
        {
            'id': 'child_2_understand',
                'tool_calls': [
                    {
                        'call_id': 'c2_understand',
                        'name': 'propose_case_understanding',
                        'arguments': json.dumps({
                            'reason': 'understand child 2',
                            'package_shape': 'single episode child',
                            'work_units': [
                                {
                                    'work_unit_ref': 'WU1',
                                    'label': 'child 2 episode',
                                    'file_refs': ['LF2'],
                                    'local_refs': ['LF2'],
                                    'title_hints': ['Title II'],
                                }
                            ],
                            'summary': 'child 2 single file',
                        }),
                    }
                ],
            },
            {
                'id': 'child_2_map',
                'tool_calls': [
                    {
                        'call_id': 'c2_map',
                        'name': 'propose_mapping_intents',
                        'arguments': json.dumps({
                            'reason': 'map child 2',
                            'mapping_intents': [
                                {
                                    'decision': 'map_explicit_item',
                                    'local_ref': 'LS1',
                                    'chosen_item_ref': 'BE2',
                                    'support_refs': ['LS1', 'BE2'],
                                    'reason': 'child visible item',
                                }
                            ],
                        }),
                }
            ],
        },
        {
            'id': 'child_2_finish',
            'tool_calls': [
                {
                    'call_id': 'c2_finish',
                    'name': 'finish_case',
                    'arguments': json.dumps(_accepted_finish_args(reason='child 2 accounting ready', mapped=1, excluded=0, outcome_kind='mapped', file_count=1, support_refs=['LS1', 'BE2'])),
                }
            ],
        },
    ],
)
    workspace = _split_root_workspace()
    object.__setattr__(workspace, 'bangumi_items', [
        BangumiItemCard(ref='BE1', subject_ref='BS1', episode_id=101, sort=1, ep=1, item_kind='episode'),
        BangumiItemCard(ref='BE2', subject_ref='BS2', episode_id=201, sort=1, ep=1, item_kind='episode'),
    ])
    object.__setattr__(workspace, 'contract', workspace.contract.model_copy(update={'visible_target_refs': ['BE1', 'BE2']}))
    import src.rename.case_agent.orchestrator as orchestrator_module
    from src.rename.case_agent.orchestrator import _run_orchestrator_split_into_child_cases_tool
    monkeypatch.setattr(orchestrator_module, '_run_orchestrator_agent_main_loop', run_local_bangumi_case_agent)
    result, _updated, observation = _run_orchestrator_split_into_child_cases_tool(
        workspace,
        SplitIntoChildCasesToolArgs(
            reason='split into two independent seasons',
            split_cases=[
                SplitCaseSpec(child_case_ref='S1', main_file_refs=['LF1'], support_refs=['BE1'], title_hints=['Title']),
                SplitCaseSpec(child_case_ref='S2', main_file_refs=['LF2'], support_refs=['BE2'], title_hints=['Title II']),
            ],
        ),
        client,
        object(),
        [],
        [],
        planning_depth=0,
        max_rounds=4,
        orchestrator_context_soft_token_limit=None,
        orchestrator_context_hard_token_limit=None,
    )

    assert result is not None
    assert result.status == 'accepted'
    assert len(result.child_results) == 2
    assert {child.case_id for child in result.child_results} == {'CASE-SPLIT:S1', 'CASE-SPLIT:S2'}
    assert all(child.status == 'accepted' for child in result.child_results)
    assert observation['status'] == 'accepted_verified'
    assert result.final_verifier_result is not None
    assert result.final_verifier_result.passed is True


def test_split_into_child_cases_selected_children_return_observation_without_terminal_root_aggregation(monkeypatch):
    client = _RunClient(
        [
            {
                'id': 'child_1_understand',
                'tool_calls': [
                    {
                        'call_id': 'c1_understand',
                        'name': 'propose_case_understanding',
                        'arguments': json.dumps({
                            'reason': 'understand selected child',
                            'package_shape': 'selected child',
                            'work_units': [
                                {
                                    'work_unit_ref': 'WU1',
                                    'label': 'selected child episode',
                                    'file_refs': ['LF1'],
                                    'local_refs': ['LF1'],
                                    'title_hints': ['Title'],
                                }
                            ],
                            'summary': 'selected child',
                        }),
                    }
                ],
            },
            {
                'id': 'child_1_map',
                'tool_calls': [
                    {
                        'call_id': 'c1_map',
                        'name': 'propose_mapping_intents',
                        'arguments': json.dumps({
                            'reason': 'map selected child',
                            'mapping_intents': [
                                {
                                    'decision': 'map_explicit_item',
                                    'local_ref': 'LS1',
                                    'chosen_item_ref': 'BE1',
                                    'support_refs': ['LS1', 'BE1'],
                                    'reason': 'child visible item',
                                }
                            ],
                        }),
                    }
                ],
            },
            {
                'id': 'child_1_finish',
                'tool_calls': [
                    {
                        'call_id': 'c1_finish',
                        'name': 'finish_case',
                        'arguments': json.dumps(_accepted_finish_args(reason='child accounting ready', mapped=1, excluded=0, outcome_kind='mapped', file_count=1, support_refs=['LS1', 'BE1'])),
                    }
                ],
            },
        ],
    )
    workspace = _split_root_workspace()
    object.__setattr__(workspace, 'bangumi_items', [
        BangumiItemCard(ref='BE1', subject_ref='BS1', episode_id=101, sort=1, ep=1, item_kind='episode'),
    ])
    object.__setattr__(workspace, 'contract', workspace.contract.model_copy(update={'visible_target_refs': ['BE1']}))
    import src.rename.case_agent.orchestrator as orchestrator_module
    from src.rename.case_agent.orchestrator import _run_orchestrator_split_into_child_cases_tool
    monkeypatch.setattr(orchestrator_module, '_run_orchestrator_agent_main_loop', run_local_bangumi_case_agent)

    result, updated, observation = _run_orchestrator_split_into_child_cases_tool(
        workspace,
        SplitIntoChildCasesToolArgs(
            reason='deep-dive only one major unit',
            coverage_mode='selected_child_cases',
            split_cases=[
                SplitCaseSpec(child_case_ref='S1', main_file_refs=['LF1'], support_refs=['BE1'], title_hints=['Title']),
            ],
        ),
        client,
        object(),
        [],
        [],
        planning_depth=0,
        max_rounds=4,
        orchestrator_context_soft_token_limit=None,
        orchestrator_context_hard_token_limit=None,
    )

    assert result is None
    assert observation['status'] == 'selected_child_cases_completed'
    assert observation['missing_main_refs'] == ['LF2']
    assert observation['child_statuses'] == ['accepted']
    assert 'BECH1_1' in updated.contract.visible_target_refs
    assert any(
        isinstance(audit, dict)
        and audit.get('note') == 'orchestrator_selected_child_cases_result'
        and audit.get('child_statuses') == ['accepted']
        for audit in updated.judge_request_audits
    )


def test_child_target_source_map_uses_global_subject_identity_not_child_local_refs():
    from src.rename.case_agent.orchestrator import CaseAgentRunResult, _child_assignment_target_source_map

    child_one_workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE:CH1'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']),
        local_files=[LocalFileCard(ref='LF1', path='Season 1/Title 01.mkv', is_main=True)],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', subject_id=101, title='Title')],
        bangumi_items=[BangumiItemCard(ref='BE1', subject_ref='BS1', sort=1, ep=1, item_kind='episode')],
    )
    child_two_workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE:CH2'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=['LF2'], allowed_file_refs=['LF2'], visible_target_refs=['BE1']),
        local_files=[LocalFileCard(ref='LF2', path='Season 2/Title II 01.mkv', is_main=True)],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', subject_id=202, title='Title II')],
        bangumi_items=[BangumiItemCard(ref='BE1', subject_ref='BS1', sort=1, ep=1, item_kind='episode')],
    )
    child_one_output = CaseJudgeOutput(
        action='submit_verdict',
        assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_card_refs=['LF1', 'BE1'])],
    )
    child_two_output = CaseJudgeOutput(
        action='submit_verdict',
        assignment_intents=[AssignmentIntent(ref='A2', file_ref='LF2', target_ref='BE1', support_card_refs=['LF2', 'BE1'])],
    )

    source_map = _child_assignment_target_source_map([
        CaseAgentRunResult(True, 'CASE:CH1', 'accepted', 'finish_case', child_one_output, None, child_one_workspace),
        CaseAgentRunResult(True, 'CASE:CH2', 'accepted', 'finish_case', child_two_output, None, child_two_workspace),
    ])

    assert source_map[('LF1', 'BECH1_1')][1] == 'subject_id:101:1:1:episode:'
    assert source_map[('LF2', 'BECH2_1')][1] == 'subject_id:202:1:1:episode:'


def test_split_child_workspace_uses_child_local_span_scope_only():
    from src.rename.case_agent.orchestrator import _workspace_with_child_split_span
    from src.rename.case_agent.case_planner import build_child_workspace

    workspace = _split_root_workspace()
    object.__setattr__(workspace, 'local_span_cards', [
        LocalSpanCard(ref='LS4', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1']),
        LocalSpanCard(ref='LS5', file_refs=['LF2'], file_ref_count=1, file_ref_samples=['LF2']),
    ])
    spec = SplitCaseSpec(child_case_ref='S1', main_file_refs=['LF1'], support_refs=['LS4'], title_hints=['Title'])

    child = _workspace_with_child_split_span(build_child_workspace(workspace, spec), spec)

    assert [span.ref for span in child.local_span_cards] == ['LS1']
    assert child.local_span_cards[0].file_refs == ['LF1']
    assert 'LS4' not in child.all_visible_ref_set()
    assert 'LS_SPLIT' not in child.all_visible_ref_set()
    assert child.mapping_draft is not None
    assert [row.local_ref for row in child.mapping_draft.rows] == ['LS1']
    assert any(
        isinstance(audit, dict)
        and audit.get('note') == 'orchestrator_child_ref_scope_initialized'
        and audit.get('parent_refs_not_visible') is True
        for audit in child.judge_request_audits
    )


def test_child_payload_exposes_ref_scope_warning():
    from src.rename.case_agent.orchestrator import _workspace_with_child_split_span
    from src.rename.case_agent.case_planner import build_child_workspace

    spec = SplitCaseSpec(child_case_ref='S1', main_file_refs=['LF1'], title_hints=['Title'])
    child = _workspace_with_child_split_span(build_child_workspace(_split_root_workspace(), spec), spec)

    payload = json.loads(build_orchestrator_agent_input(child, reason='inspect child scope'))

    assert payload['child_case_ref_scope']['is_child_case'] is True
    assert payload['child_case_ref_scope']['parent_refs_not_visible'] is True
    assert payload['child_case_ref_scope']['visible_local_span_refs_only'] == ['LS1']
    assert 'parent/root refs are not visible' in ' '.join(payload['rules'])


def test_payload_exposes_authoritative_visible_item_ref_table():
    payload = json.loads(build_orchestrator_agent_input(_mapping_workspace(), reason='inspect visible refs'))

    assert payload['visible_ref_catalog']['bangumi_item_refs'] == ['BE1']
    assert payload['visible_item_ref_table'][0]['ref'] == 'BE1'
    assert payload['visible_item_ref_table'][0]['subject_ref'] == 'BS1'
    assert 'Do not infer that BE1..BE13 exist' in ' '.join(payload['rules'])


def test_split_into_child_cases_child_fail_closed_aggregates_to_root_fail_closed(monkeypatch):
    client = _RunClient(
        [
            {
                'id': 'child_1_understand',
                'tool_calls': [
                    {
                        'call_id': 'c1_understand',
                        'name': 'propose_case_understanding',
                        'arguments': json.dumps({
                            'reason': 'understand child 1',
                            'package_shape': 'single child',
                            'work_units': [{'work_unit_ref': 'WU1', 'label': 'child 1', 'file_refs': ['LF1'], 'local_refs': ['LF1']}],
                            'summary': 'child 1',
                        }),
                    }
                ],
            },
            {
                'id': 'child_1_map',
                'tool_calls': [
                    {
                        'call_id': 'c1_map',
                        'name': 'propose_mapping_intents',
                        'arguments': json.dumps({
                            'reason': 'target absent child 1',
                            'mapping_intents': [
                                    {
                                            'decision': 'mark_non_bangumi_or_supplemental',
                                            'local_ref': 'LS1',
                                            'reason_kind': 'bangumi_target_absent',
                                            'support_refs': ['LS1', 'BS1'],
                                            'reason': 'no Bangumi target for child 1',
                                        }
                            ],
                        }),
                    }
                ],
            },
            {
                'id': 'child_1_finish',
                'tool_calls': [
                    {
                        'call_id': 'c1_finish',
                        'name': 'finish_case',
                                'arguments': json.dumps(_accepted_finish_args(reason='child 1 accounting ready', mapped=0, excluded=1, outcome_kind='target_absent', file_count=1, support_refs=['LS1', 'BS1'])),
                    }
                ],
            },
            {
                'id': 'child_2_understand',
                'tool_calls': [
                    {
                        'call_id': 'c2_understand',
                        'name': 'propose_case_understanding',
                        'arguments': json.dumps({
                            'reason': 'understand child 2',
                            'package_shape': 'single child',
                            'work_units': [{'work_unit_ref': 'WU1', 'label': 'child 2', 'file_refs': ['LF2'], 'local_refs': ['LF2']}],
                            'summary': 'child 2',
                        }),
                    }
                ],
            },
            {
                'id': 'child_2_blocker',
                'tool_calls': [
                    {
                        'call_id': 'c2_blocker',
                        'name': 'propose_mapping_intents',
                        'arguments': json.dumps({
                            'reason': 'child 2 semantic blocker',
                            'mapping_intents': [
                                {
                                    'decision': 'mark_unaligned_fail_closed',
                                    'local_ref': 'LS1',
                                    'reason_kind': 'special_regular_conflict',
                                    'support_refs': ['LS1'],
                                    'reason': 'child 2 has conflicting target evidence',
                                }
                            ],
                        }),
                    }
                ],
            },
            {
                'id': 'child_2_fail',
                'tool_calls': [
                    {
                        'call_id': 'c2_fail',
                        'name': 'finish_case',
                        'arguments': json.dumps({
                            'status': 'fail_closed',
                            'finish_kind': 'semantic_target_conflict',
                            'reason': 'child 2 has conflicting target evidence',
                        }),
                    }
                ],
            },
            {
                'id': 'child_2_finish',
                'tool_calls': [
                    {
                        'call_id': 'c2_finish',
                        'name': 'finish_case',
                        'arguments': json.dumps(_accepted_finish_args(reason='child 2 accounting ready', mapped=0, excluded=1, outcome_kind='target_absent', file_count=1, support_refs=['LS1'])),
                    }
                ],
            },
        ],
    )

    import src.rename.case_agent.orchestrator as orchestrator_module
    from src.rename.case_agent.orchestrator import _run_orchestrator_split_into_child_cases_tool
    monkeypatch.setattr(orchestrator_module, '_run_orchestrator_agent_main_loop', run_local_bangumi_case_agent)
    result, _updated, observation = _run_orchestrator_split_into_child_cases_tool(
        _split_root_workspace(),
        SplitIntoChildCasesToolArgs(
            reason='split mixed package',
            split_cases=[
                SplitCaseSpec(child_case_ref='S1', main_file_refs=['LF1'], support_refs=['BS1']),
                SplitCaseSpec(child_case_ref='S2', main_file_refs=['LF2']),
            ],
        ),
        client,
        object(),
        [],
        [],
        planning_depth=0,
        max_rounds=4,
        orchestrator_context_soft_token_limit=None,
        orchestrator_context_hard_token_limit=None,
    )

    assert result is not None
    assert result.status == 'fail_closed'
    assert len(result.child_results) == 2
    assert result.child_results[1].status == 'fail_closed'
    assert observation['reason'] == 'child_case_unresolved'
    assert result.final_output is not None
    assert 'child 2' in result.final_output.fail_closed_reasons[0].description


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
    prompt = '\n'.join([
        str(client.calls[0].get('instructions') or ''),
        *[str(item.get('content') or '') for item in client.calls[0]['input_items']],
    ])

    assert result.ok is True
    assert tool_names == {tool['function']['name'] for tool in orchestrator_tool_definitions()}
    assert 'finish_case' in tool_names
    assert 'propose_mapping_intents' in tool_names
    assert 'mark_non_bangumi_or_supplemental(reason_kind=bangumi_target_absent)' in prompt
    assert 'will keep accounting unresolved' in prompt
    assert 'reject_candidate' in prompt


def test_reopen_mapping_draft_issue_rows_keeps_existing_duplicate_owner_closed():
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


def test_open_row_sequences_include_full_title_samples_for_row_size():
    file_refs = ['LF1', 'LF2', 'LF3', 'LF4']
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-SEQUENCE-TITLES'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=file_refs, allowed_file_refs=file_refs, visible_target_refs=['BE1', 'BE2', 'BE3', 'BE4']),
        local_files=[
            LocalFileCard(ref=ref, path=f'Title {index:02d}.mkv', is_main=True)
            for index, ref in enumerate(file_refs, start=1)
        ],
        local_span_cards=[
            LocalSpanCard(ref='LS1', file_refs=file_refs, file_ref_count=4, file_ref_samples=file_refs)
        ],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', title='Title')],
        bangumi_items=[
            BangumiItemCard(ref=f'BE{index}', subject_ref='BS1', item_kind='episode', sort=index, ep=index, title=f'Episode {index}')
            for index in range(1, 5)
        ],
        mapping_draft=MappingDraft(rows=[
            MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span', disposition='open', status='open')
        ]),
    )

    payload = json.loads(build_orchestrator_agent_input(workspace, reason='inspect title samples'))
    sequence = payload['open_rows_requiring_agent_action'][0]['visible_subject_item_sequences'][0]

    assert sequence['matches_local_file_count'] is True
    assert sequence['title_samples'] == ['Episode 1', 'Episode 2', 'Episode 3', 'Episode 4']


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
        and audit.get('reason') == 'finish_case_preconditions_not_met'
        for audit in result.final_workspace.judge_request_audits
    )


def test_finish_case_tool_hidden_when_accounting_not_ready():
    payload = json.loads(build_orchestrator_agent_input(_mapping_workspace(), reason='not ready'))

    assert payload['finish_protocol']['accepted_finish_allowed_now'] is False
    assert payload['finish_protocol']['finish_tool_available_now'] is True
    assert 'finish_case' in payload['available_tool_names']


def test_update_notebook_tool_is_allowed_while_open_rows_remain():
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
    assert any(
        isinstance(audit, dict)
        and audit.get('note') == 'orchestrator_update_notebook_applied'
        and audit.get('accepted_update_count') == 1
        and audit.get('rejected_update_count') == 0
        for audit in result.final_workspace.judge_request_audits
    )
    session_summary = next(
        audit
        for audit in result.final_workspace.judge_request_audits
        if isinstance(audit, dict) and audit.get('note') == 'orchestrator_agent_session_summary'
    )
    assert session_summary['orchestrator_tool_sequence'] == ['update_notebook']
    assert session_summary['tool_rejection_count'] == 0


def test_turn_health_audit_records_near_limit_and_stall_without_semantic_action():
    client = _RunClient([
        {
            'id': 'resp_note_1',
            'tool_calls': [
                {
                    'call_id': 'call_note_1',
                    'name': 'update_notebook',
                    'arguments': json.dumps({'reason': 'bookkeep without mapping'}),
                }
            ],
        },
        {
            'id': 'resp_note_2',
            'tool_calls': [
                {
                    'call_id': 'call_note_2',
                    'name': 'update_notebook',
                    'arguments': json.dumps({'reason': 'repeat bookkeeping'}),
                }
            ],
        },
    ])

    result = run_local_bangumi_case_agent(_mapping_workspace(), client, object(), max_rounds=2)

    assert result.status in {'fail_closed', 'error'}
    health_audits = [
        audit for audit in result.final_workspace.judge_request_audits
        if isinstance(audit, dict) and audit.get('note') == 'orchestrator_turn_health'
    ]
    assert health_audits
    assert health_audits[-1]['near_turn_limit_unhealthy'] is True
    assert health_audits[-1]['stall_suspected'] is True
    session_summary = next(
        audit
        for audit in result.final_workspace.judge_request_audits
        if isinstance(audit, dict) and audit.get('note') == 'orchestrator_agent_session_summary'
    )
    assert session_summary['near_turn_limit_unhealthy_count'] >= 1
    assert session_summary['stall_suspected_count'] >= 1
    payload = json.loads(build_orchestrator_agent_input(result.final_workspace, reason='inspect turn health'))
    assert payload['turn_health']['has_recent_warning'] is True
    assert payload['turn_health']['latest']['stall_suspected'] is True
    assert 'change strategy now' in ' '.join(payload['rules'])


def test_unavailable_case_understanding_revision_is_not_auto_rerouted():
    file_refs = [f'LF{index}' for index in range(1, 21)]
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-LARGE-REROUTE'),
        budget=CaseBudget(max_judge_rounds=2),
        contract=CaseContract(main_file_refs=file_refs, allowed_file_refs=file_refs, visible_target_refs=['BE1']),
        local_files=[
            LocalFileCard(ref=ref, path=f'Title {index:02d}.mkv', is_main=True)
            for index, ref in enumerate(file_refs, start=1)
        ],
        local_span_cards=[
            LocalSpanCard(ref='LS1', file_refs=file_refs, file_ref_count=len(file_refs), file_ref_samples=file_refs[:4])
        ],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', title='Title')],
        bangumi_items=[BangumiItemCard(ref='BE1', subject_ref='BS1', item_kind='episode', sort=1, ep=1)],
        case_briefing=CaseBriefingOutput(package_shape='large package'),
        mapping_draft=MappingDraft(rows=[
            MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span', disposition='open', status='open')
        ]),
    )
    client = _RunClient([
        {
            'id': 'resp_revise',
            'tool_calls': [
                {
                    'call_id': 'call_revise',
                    'name': 'propose_case_understanding',
                    'arguments': json.dumps({
                        'reason': 'revise broad package partition',
                        'work_units': [
                            {
                                'work_unit_ref': 'WU_REVISED',
                                'label': 'file 1',
                                'file_refs': ['LF1'],
                                'local_refs': ['LF1'],
                            }
                        ],
                    }),
                }
            ],
        }
    ])

    result = run_local_bangumi_case_agent(workspace, client, object(), max_rounds=1)

    assert result.status in {'fail_closed', 'error'}
    assert client.case_judge_calls == 0
    assert not any(
        isinstance(audit, dict)
        and audit.get('note') == 'orchestrator_tool_auto_rerouted'
        for audit in result.final_workspace.judge_request_audits
    )
    assert any(
        isinstance(audit, dict)
        and audit.get('note') == 'orchestrator_tool_selected'
        and audit.get('tool_name') == 'propose_case_understanding'
        for audit in result.final_workspace.judge_request_audits
    )


def test_mapping_intent_ref_validation_reports_wrong_namespace_corrections():
    tool_call = OrchestratorAgentToolCall(
        tool_name='propose_mapping_intents',
        arguments=ProposeMappingIntentsToolArgs(
            mapping_intents=[
                MappingIntent(
                    decision='map_regular_span',
                    local_ref='LS1',
                    chosen_subject_ref='BS1',
                    item_refs=['LF1', 'LF2'],
                    support_refs=['LS1', 'BS1', 'LF1', 'LF2'],
                    reason='mistakenly put local files in item_refs',
                )
            ],
        ),
        raw_arguments={
            'mapping_intents': [
                {
                    'decision': 'map_regular_span',
                    'local_ref': 'LS1',
                    'chosen_subject_ref': 'BS1',
                    'item_refs': ['LF1', 'LF2'],
                    'support_refs': ['LS1', 'BS1', 'LF1', 'LF2'],
                    'reason': 'mistakenly put local files in item_refs',
                }
            ],
        },
        call_id='call_bad_refs',
    )

    _workspace_after, decision, acceptance = _decision_from_orchestrator_tool_call(_regular_span_workspace(), tool_call)

    assert decision is None
    assert acceptance['reason'] == 'hidden_or_unknown_refs'
    assert acceptance['ref_issue_codes'] == ['hidden_or_wrong_ref_namespace']
    assert acceptance['ref_issue_refs'] == ['LF1', 'LF2']
    assert any('item_refs/chosen_item_ref must use visible BE*' in correction for correction in acceptance['ref_corrections'])
    assert 'open_rows' in acceptance


def test_recent_observations_include_ref_corrections_after_rejected_tool_call():
    client = _RunClient([
        {
            'id': 'resp_bad_refs',
            'tool_calls': [
                {
                    'call_id': 'call_bad_refs',
                    'name': 'propose_mapping_intents',
                    'arguments': json.dumps({
                        'mapping_intents': [
                            {
                                'decision': 'map_regular_span',
                                'local_ref': 'LS1',
                                'chosen_subject_ref': 'BS1',
                                'item_refs': ['LF1', 'LF2'],
                                'support_refs': ['LS1', 'BS1', 'LF1', 'LF2'],
                                'reason': 'mistakenly put local files in item_refs',
                            }
                        ],
                    }),
                }
            ],
        },
    ])

    result = run_local_bangumi_case_agent(_regular_span_workspace(), client, object(), max_rounds=1)

    rejected = next(
        audit for audit in result.final_workspace.judge_request_audits
        if isinstance(audit, dict)
        and audit.get('note') == 'orchestrator_tool_selected'
        and audit.get('accepted') is False
    )
    assert rejected['ref_issue_codes'] == ['hidden_or_wrong_ref_namespace']
    assert any('LF*/LS* are local refs' in correction for correction in rejected['ref_corrections'])
    tail = json.loads(build_orchestrator_agent_turn_tail(result.final_workspace))
    recent = tail['recent_tool_observations']
    assert any(
        item.get('note') == 'orchestrator_tool_selected'
        and item.get('accepted') is False
        and item.get('ref_issue_codes') == ['hidden_or_wrong_ref_namespace']
        and item.get('ref_corrections')
        for item in recent
    )
