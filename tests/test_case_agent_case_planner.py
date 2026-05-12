from __future__ import annotations

from src.rename.case_agent.case_planner import build_child_workspace, verify_case_planning_output
from src.rename.case_agent.models import (
    AssignmentIntent,
    BangumiItemCard,
    CaseBudget,
    CaseContract,
    CaseDossier,
    CaseHeader,
    CaseJudgeOutput,
    CasePlanningOutput,
    Finding,
    LocalClusterCard,
    LocalFileCard,
    QueryCard,
    SplitCaseSpec,
)
from src.rename.case_agent.orchestrator import run_local_bangumi_case_agent
from src.rename.case_agent.workspace import CaseEvidenceWorkspace


class _BangumiClient:
    pass


class _AIClient:
    def __init__(self, planner_output: CasePlanningOutput, judge_outputs: list[CaseJudgeOutput]):
        self.planner_output = planner_output
        self.judge_outputs = list(judge_outputs)

    def call_case_planner(self, prompt: str, schema):
        return self.planner_output

    def call_case_judge(self, prompt: str, schema):
        if not self.judge_outputs:
            raise RuntimeError('no more judge outputs')
        return self.judge_outputs.pop(0)


def _workspace() -> CaseEvidenceWorkspace:
    return CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-SPLIT'),
        budget=CaseBudget(max_judge_rounds=3, max_evidence_batches=1, max_issue_response_rounds=0),
        contract=CaseContract(
            main_file_refs=['LF1', 'LF2'],
            supplemental_file_refs=['LF3'],
            allowed_file_refs=['LF1', 'LF2', 'LF3'],
            visible_target_refs=['BE1', 'BE2'],
        ),
        local_files=[
            LocalFileCard(ref='LF1', path='A/01.mkv', is_main=True),
            LocalFileCard(ref='LF2', path='B/01.mkv', is_main=True),
            LocalFileCard(ref='LF3', path='A/menu.mkv', is_main=False),
        ],
        local_clusters=[LocalClusterCard(ref='LC1', cluster_name='mixed', file_refs=['LF1', 'LF2', 'LF3'])],
        bangumi_items=[BangumiItemCard(ref='BE1', title='Episode 1'), BangumiItemCard(ref='BE2', title='Episode 1')],
        query_cards=[QueryCard(ref='SQ1', query_text='title a', source_refs=['LC1'], result_refs=['BE1', 'BE2'])],
    )


def _split_output() -> CasePlanningOutput:
    return CasePlanningOutput(
        action='split_into_cases',
        split_cases=[
            SplitCaseSpec(child_case_ref='C1', main_file_refs=['LF1'], supplemental_file_refs=['LF3'], support_refs=['LC1', 'SQ1', 'BE1'], reason='first local title'),
            SplitCaseSpec(child_case_ref='C2', main_file_refs=['LF2'], support_refs=['LC1', 'BE2'], reason='second local title'),
        ],
    )


def _verdict(file_ref: str, target_ref: str) -> CaseJudgeOutput:
    return CaseJudgeOutput(
        action='submit_verdict',
        findings=[Finding(ref='FN1', finding_kind='pass', description='ok')],
        assignment_intents=[
            AssignmentIntent(
                ref='A1',
                file_ref=file_ref,
                target_ref=target_ref,
                support_finding_refs=['FN1'],
                support_card_refs=[file_ref, target_ref],
                reason='ok',
            )
        ],
    )


def test_case_planning_output_schema_is_strict():
    output = CasePlanningOutput.model_validate({'action': 'process_as_one_case'})
    assert output.action == 'process_as_one_case'
    assert output.split_cases == []

    try:
        CasePlanningOutput.model_validate({'action': 'process_as_one_case', 'unexpected': 1})
        assert False, 'expected strict schema rejection'
    except Exception as exc:
        assert 'extra_forbidden' in str(exc)


def test_split_verifier_accepts_exact_once_partition():
    result = verify_case_planning_output(_workspace().to_dossier(round_context='case_planning'), _split_output())

    assert result.passed is True
    assert result.issues == []


def test_split_verifier_rejects_duplicate_missing_hidden_and_empty_children():
    dossier = _workspace().to_dossier(round_context='case_planning')

    duplicate = CasePlanningOutput(action='split_into_cases', split_cases=[
        SplitCaseSpec(child_case_ref='C1', main_file_refs=['LF1'], support_refs=['BE1']),
        SplitCaseSpec(child_case_ref='C2', main_file_refs=['LF1'], support_refs=['BE2']),
    ])
    missing = CasePlanningOutput(action='split_into_cases', split_cases=[
        SplitCaseSpec(child_case_ref='C1', main_file_refs=['LF1'], support_refs=['BE1']),
    ])
    hidden = CasePlanningOutput(action='split_into_cases', split_cases=[
        SplitCaseSpec(child_case_ref='C1', main_file_refs=['LF1'], support_refs=['BE1']),
        SplitCaseSpec(child_case_ref='C2', main_file_refs=['LF2'], support_refs=['BE404']),
    ])
    empty = CasePlanningOutput(action='split_into_cases', split_cases=[
        SplitCaseSpec(child_case_ref='C1', main_file_refs=['LF1'], support_refs=['BE1']),
        SplitCaseSpec(child_case_ref='C2', main_file_refs=[], support_refs=['BE2']),
    ])

    assert any(issue.issue_code == 'split_duplicate_main_ref' for issue in verify_case_planning_output(dossier, duplicate).issues)
    assert any(issue.issue_code == 'split_missing_main_ref' for issue in verify_case_planning_output(dossier, missing).issues)
    assert any(issue.issue_code == 'split_unknown_support_ref' for issue in verify_case_planning_output(dossier, hidden).issues)
    assert any(issue.issue_code == 'split_child_empty' for issue in verify_case_planning_output(dossier, empty).issues)


def test_child_workspace_keeps_only_owned_local_refs_and_explicit_support_refs():
    parent = _workspace()
    child = build_child_workspace(parent, _split_output().split_cases[0])

    assert [card.ref for card in child.local_files] == ['LF1', 'LF3']
    assert [card.ref for card in child.bangumi_items] == ['BE1']
    assert child.contract.main_file_refs == ['LF1']
    assert child.contract.supplemental_file_refs == ['LF3']
    assert child.local_clusters[0].file_refs == ['LF1', 'LF3']
    assert child.query_cards[0].result_refs == ['BE1']
    assert 'LF2' not in child.all_visible_ref_set()
    assert 'BE2' not in child.all_visible_ref_set()


def test_split_aggregate_accepts_when_all_children_accept():
    client = _AIClient(_split_output(), [_verdict('LF1', 'BE1'), _verdict('LF2', 'BE2')])

    result = run_local_bangumi_case_agent(_workspace(), client, _BangumiClient())

    assert result.ok is True
    assert result.status == 'accepted'
    assert result.final_action == 'split_into_cases'
    assert len(result.child_results) == 2
    assert [child.status for child in result.child_results] == ['accepted', 'accepted']


def test_split_aggregate_fail_closed_if_any_child_fail_closed():
    child_fail = CaseJudgeOutput(action='fail_closed', fail_closed_reasons=[{'ref': 'FR1', 'reason_kind': 'insufficient_evidence', 'description': 'ambiguous', 'related_refs': []}])
    client = _AIClient(_split_output(), [_verdict('LF1', 'BE1'), child_fail])

    result = run_local_bangumi_case_agent(_workspace(), client, _BangumiClient())

    assert result.ok is True
    assert result.status == 'fail_closed'
    assert len(result.child_results) == 2


def test_split_aggregate_invalid_if_child_contract_breaks():
    child_invalid = CaseJudgeOutput(action='request_evidence')
    client = _AIClient(_split_output(), [_verdict('LF1', 'BE1'), child_invalid])

    result = run_local_bangumi_case_agent(_workspace(), client, _BangumiClient())

    assert result.ok is False
    assert result.status == 'invalid'
    assert len(result.child_results) == 2


def test_invalid_planner_split_defers_to_single_case_investigation():
    bad_split = CasePlanningOutput(action='split_into_cases', split_cases=[
        SplitCaseSpec(child_case_ref='C1', main_file_refs=['LF1'], supplemental_file_refs=['LF2'], support_refs=['BE1'], reason='bad ownership'),
        SplitCaseSpec(child_case_ref='C2', main_file_refs=['LF2'], supplemental_file_refs=['LF2'], support_refs=['BE2'], reason='duplicate supplemental'),
    ])
    fail_output = CaseJudgeOutput(action='fail_closed', fail_closed_reasons=[{'ref': 'FR1', 'reason_kind': 'insufficient_evidence', 'description': 'investigate as one case', 'related_refs': []}])
    client = _AIClient(bad_split, [fail_output])

    result = run_local_bangumi_case_agent(_workspace(), client, _BangumiClient())

    assert result.ok is True
    assert result.status == 'fail_closed'
    assert result.planning_output is not None
    assert result.planning_output.action == 'process_as_one_case'
    assert result.child_results == []
    assert any(
        audit.get('note') == 'case_planning_invalid_split_deferred_to_investigation_loop'
        for audit in result.final_workspace.judge_request_audits
        if isinstance(audit, dict)
    )
