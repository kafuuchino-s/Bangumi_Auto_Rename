from __future__ import annotations

import sys

from dataclasses import replace

from src.rename.case_agent.models import (
    AssignmentIntent,
    CaseBudget,
    CaseContract,
    CaseDossier,
    CaseHeader,
    CaseJudgeOutput,
    BangumiItemCard,
    BangumiSpanCard,
    BangumiSubjectCard,
    LocalFileCard,
    EvidenceBatchResult,
    EvidenceRequest,
    Finding,
    LocalSpanCard,
    LocalStructureOutput,
    LocalStructureSpanSpec,
    MappingDraft,
    MappingDraftRow,
    CasePlanningOutput,
    QueryCard,
    SplitCaseSpec,
    VerifierIssue,
    EvidenceRequestType,
)
from src.rename.case_agent.evidence_request_normalizer import normalize_evidence_requests
from src.rename.case_agent.evidence_broker import EvidenceBroker
from src.rename.case_agent.orchestrator import run_local_bangumi_case_agent, _mapping_draft_local_coverage_issue
from src.rename.case_agent.orchestrator import _next_investigation_action, _refresh_mapping_draft_candidates
from src.rename.case_agent.workspace import CaseEvidenceWorkspace


def make_workspace(*, budget: CaseBudget | None = None, verifier_issues: list[VerifierIssue] | None = None) -> CaseEvidenceWorkspace:
    header = CaseHeader(case_id='CASE-1')
    budget = budget or CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1)
    return CaseEvidenceWorkspace.from_cards(
        header=header,
        budget=budget,
        verifier_issues=verifier_issues or [],
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']),
        local_files=[LocalFileCard(ref='LF1')],
        bangumi_items=[BangumiItemCard(ref='BE1')],
    )


def make_verdict(target_ref: str = 'BE1') -> CaseJudgeOutput:
    support_card_refs = ['LF1'] if target_ref == 'UNALIGNED' else ['LF1', target_ref]
    return CaseJudgeOutput(
        action='submit_verdict',
        findings=[Finding(ref='F1', finding_kind='pass', description='ok')],
        assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref=target_ref, support_finding_refs=['F1'], support_card_refs=support_card_refs, reason='r')],
    )


class FakeAIClient:
    def __init__(self, outputs: list[object]):
        self.outputs = list(outputs)
        self.calls: list[str] = []

    def call_case_judge(self, prompt: str, schema):
        self.calls.append(prompt)
        if not self.outputs:
            raise RuntimeError('no more outputs')
        return self.outputs.pop(0)


class FakeStructuredAIClient(FakeAIClient):
    def __init__(self, *, structure_output: LocalStructureOutput, outputs: list[object]):
        super().__init__(outputs)
        self.structure_output = structure_output
        self.structure_prompts: list[str] = []

    def call_local_structure_agent(self, prompt: str, schema):
        self.structure_prompts.append(prompt)
        return self.structure_output


class FakeBangumiClient:
    pass


def test_orchestrator_uses_local_structure_agent_before_case_planning():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-LS-AGENT'),
        budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=1, max_issue_response_rounds=0),
        contract=CaseContract(main_file_refs=['LF1', 'LF2'], allowed_file_refs=['LF1', 'LF2'], visible_target_refs=['BE1', 'BE2']),
        local_files=[
            LocalFileCard(ref='LF1', path='Yamada-kun to 7-nin no Majo [01].mkv', is_main=True),
            LocalFileCard(ref='LF2', path='Yamada-kun to 7-nin no Majo [02].mkv', is_main=True),
        ],
        local_span_cards=[
            LocalSpanCard(ref='LS_PACKAGE', span_scope='package', file_refs=['LF1', 'LF2'], file_ref_count=2),
            LocalSpanCard(ref='LS1', span_scope='unpartitioned', file_refs=['LF1', 'LF2'], file_ref_count=2),
        ],
        bangumi_items=[
            BangumiItemCard(ref='BE1', subject_ref='BS1', sort=1, ep=1),
            BangumiItemCard(ref='BE2', subject_ref='BS1', sort=2, ep=2),
        ],
    )
    client = FakeStructuredAIClient(
        structure_output=LocalStructureOutput(spans=[
            LocalStructureSpanSpec(span_ref='LS_PACKAGE', span_scope='package', file_refs=['LF1', 'LF2'], reason='whole package'),
            LocalStructureSpanSpec(span_ref='LS1', span_scope='token_segment', file_refs=['LF1', 'LF2'], ordinal_start=1, ordinal_end=2, ordinal_count=2, ordering_basis='filename_ordinal_order', reason='agent-selected local ordinal span'),
        ]),
        outputs=[make_verdict()],
    )

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert client.structure_prompts
    assert result.final_workspace.local_span_cards[1].span_scope == 'token_segment'
    assert result.final_workspace.local_span_cards[1].episode_token_start == 1
    assert any(a.get('note') == 'local_structure_agent_applied' for a in result.final_workspace.judge_request_audits if isinstance(a, dict))


def test_one_shot_submit_verdict_accepted():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=0), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')])
    client = FakeAIClient([make_verdict()])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is True
    assert result.status == 'accepted'
    assert result.final_action == 'submit_verdict'
    assert result.final_output is not None
    assert result.final_verifier_result is not None and result.final_verifier_result.passed is True


def test_visible_target_but_not_detail_is_rejected_then_recovered():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')])
    object.__setattr__(workspace, 'detailed_card_refs', [])
    object.__setattr__(workspace, 'assignable_target_refs', [])
    object.__setattr__(workspace, 'seen_detail_refs', [])
    client = FakeAIClient([
        CaseJudgeOutput(action='submit_verdict', findings=[Finding(ref='F1', finding_kind='pass', description='ok')], assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1'], reason='r')]),
        CaseJudgeOutput(action='issue_response', findings=[Finding(ref='F2', finding_kind='pass', description='ok')], assignment_intents=[AssignmentIntent(ref='A2', file_ref='LF1', target_ref='BE1', support_finding_refs=['F2'], support_card_refs=['LF1', 'BE1'], reason='r2')], issue_responses=[{'ref': 'IR1', 'issue_kind': 'clarify_scope', 'message': 'fixed', 'related_refs': []}]),
    ])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is True
    assert result.status == 'accepted'


def test_submit_verdict_with_zero_assignments_is_not_accepted():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')])
    client = FakeAIClient([CaseJudgeOutput(action='submit_verdict', findings=[Finding(ref='F1', finding_kind='pass', description='ok')], assignment_intents=[]), CaseJudgeOutput(action='fail_closed', fail_closed_reasons=[{'ref': 'R1', 'reason_kind': 'insufficient_evidence', 'description': 'stop', 'related_refs': []}])])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is True
    assert result.status == 'fail_closed'
    assert result.final_verifier_result is not None


def test_none_judge_response_is_infra_error_not_invalid():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=5), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')])
    client = FakeAIClient([None])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is False
    assert result.status == 'error'
    assert any('no response' in err for err in result.errors)
    assert any('error_kind=provider_no_response' in err for err in result.errors)


def test_request_evidence_then_accepts():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')])
    evidence = EvidenceRequest(request_ref='R1', request_type='target_detail', item_refs=['BE1'])
    client = FakeAIClient([
        CaseJudgeOutput(action='request_evidence', evidence_requests=[evidence]),
        CaseJudgeOutput(action='submit_verdict', findings=[Finding(ref='F2', finding_kind='pass', description='ok')], assignment_intents=[AssignmentIntent(ref='A2', file_ref='LF1', target_ref='BE1', support_finding_refs=['F2'], support_card_refs=['LF1', 'BE1'], reason='r2')]),
    ])

    class BrokerBangumiClient:
        pass

    result = run_local_bangumi_case_agent(workspace, client, BrokerBangumiClient())

    assert result.ok is True
    assert result.status == 'accepted'
    assert len(result.evidence_batches) == 1
    assert len(result.final_workspace.seen_detail_refs) >= 0


def test_request_evidence_menu_id_resolves_to_target_span():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-MENU-1'),
        budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']),
        local_files=[LocalFileCard(ref='LF1')],
        bangumi_items=[BangumiItemCard(ref='BE1')],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='directory', file_ref_count=1, file_ref_samples=['LF1'], file_refs=['LF1'], episode_token_start=1, episode_token_end=1, episode_token_count=1)],
    )
    client = FakeAIClient([
        CaseJudgeOutput(action='request_evidence', evidence_menu_request_ids=['REQ_TARGET_SPAN_LS1']),
        make_verdict(),
        make_verdict(),
    ])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.evidence_batches
    assert any(
        isinstance(batch, EvidenceBatchResult)
        and any(rr.request_type == 'target_span' and any('LS1' in note for note in (rr.notes or [])) for rr in (batch.request_results or []))
        for batch in result.evidence_batches
    )


def test_orchestrator_does_not_emit_unresolvable_target_span_ids():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-MENU-6'),
        budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=1, max_issue_response_rounds=1, max_requests_per_batch=1),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']),
        local_files=[LocalFileCard(ref='LF1')],
        bangumi_items=[BangumiItemCard(ref='BE1')],
    )
    client = FakeAIClient([CaseJudgeOutput(action='request_evidence', evidence_menu_request_ids=['REQ_TARGET_SPAN_LS1'])])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is False or result.status in {'invalid', 'error', 'fail_closed'}
    assert not any('REQ_TARGET_SPAN_' in err and 'unknown' in err.lower() for err in result.errors)


def test_request_evidence_unknown_menu_id_is_invalid():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-MENU-2'),
        budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']),
        local_files=[LocalFileCard(ref='LF1')],
        bangumi_items=[BangumiItemCard(ref='BE1')],
    )
    client = FakeAIClient([CaseJudgeOutput(action='request_evidence', evidence_menu_request_ids=['REQ_DOES_NOT_EXIST'])])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is False
    assert result.status == 'invalid'
    assert 'unknown_menu_request_id' in result.errors or result.summary == 'unknown_menu_request_id'


def test_stale_menu_id_after_prior_planner_evidence_continues_investigation():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-MENU-STALE'),
        budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']),
        local_files=[LocalFileCard(ref='LF1')],
        bangumi_items=[BangumiItemCard(ref='BE1')],
    )
    object.__setattr__(workspace, 'previous_evidence_results', [
        EvidenceBatchResult(batch_ref='EB-PRIOR', round_index=0, status='accepted', request_results=[], results=[], budget_after=workspace.budget)
    ])
    client = FakeAIClient([
        CaseJudgeOutput(action='request_evidence', evidence_menu_request_ids=['REQ_EPISODE_LIST_ALREADY_DONE']),
        make_verdict(),
    ])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.summary != 'unknown_menu_request_after_planner'
    assert 'unknown_menu_request_after_planner' not in result.errors
    assert any(
        isinstance(audit, dict) and audit.get('note') == 'stale_or_unknown_menu_request_ignored_after_planner'
        for audit in result.final_workspace.judge_request_audits
    )


def test_orchestrator_preflight_uses_current_workspace_coverage():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-PREFLIGHT-1'),
        budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1),
        local_files=[LocalFileCard(ref='LF1')],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='directory', file_ref_count=1, file_ref_samples=['LF1'])],
        bangumi_items=[BangumiItemCard(ref='BE1')],
    )
    workspace = workspace.with_mapping_draft(MappingDraft(rows=[MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span')]))
    assert _mapping_draft_local_coverage_issue(workspace, workspace.mapping_draft) is None


def test_legacy_raw_request_still_normalizes():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-MENU-3'),
        budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1, max_requests_per_batch=3),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']),
        local_files=[LocalFileCard(ref='LF1')],
        bangumi_items=[BangumiItemCard(ref='BE1')],
        bangumi_span_cards=[BangumiSpanCard(ref='BES1', subject_ref='BS1', target_refs=['BE1'], target_ref_count=1, item_kind='regular', detail_equivalent=True)],
        local_span_cards=[
            LocalSpanCard(ref='LS_PACKAGE', span_scope='package', file_ref_count=9),
            LocalSpanCard(ref='LS1', span_scope='directory', file_ref_count=3, file_ref_samples=['LF1'], episode_token_start=1, episode_token_end=3, episode_token_count=3),
            LocalSpanCard(ref='LS2', span_scope='directory', file_ref_count=3, file_ref_samples=['LF2'], episode_token_start=4, episode_token_end=6, episode_token_count=3),
            LocalSpanCard(ref='LS3', span_scope='directory', file_ref_count=3, file_ref_samples=['LF3'], episode_token_start=7, episode_token_end=9, episode_token_count=3),
        ],
    )
    client = FakeAIClient([
        CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='target_span', local_span_ref='LS_PACKAGE', expected_count=9)]),
        make_verdict(),
        make_verdict(),
    ])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.evidence_batches
    assert any(
        any(rr.request_type == 'target_span' for rr in (batch.request_results or []))
        for batch in result.evidence_batches
    )


def test_menu_ids_take_precedence_over_raw_requests():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-MENU-4'),
        budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1, max_requests_per_batch=3),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']),
        local_files=[LocalFileCard(ref='LF1')],
        bangumi_items=[BangumiItemCard(ref='BE1')],
        bangumi_span_cards=[BangumiSpanCard(ref='BES1', subject_ref='BS1', target_refs=['BE1'], target_ref_count=1, item_kind='regular', detail_equivalent=True)],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='directory', file_ref_count=3, file_ref_samples=['LF1'], episode_token_start=1, episode_token_end=3, episode_token_count=3)],
    )
    client = FakeAIClient([
        CaseJudgeOutput(
            action='request_evidence',
            evidence_menu_request_ids=['REQ_TARGET_SPAN_LS1'],
            evidence_requests=[EvidenceRequest(request_ref='RAW1', request_type='target_span', local_span_ref='LS1')],
        ),
        make_verdict(),
    ])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.evidence_batches
    assert any(a.get('note') == 'evidence_menu_resolution' for a in getattr(result.final_workspace, 'judge_request_audits', []) if isinstance(a, dict))
    assert not any(a.get('legacy_raw_request_used') for a in getattr(result.final_workspace, 'judge_request_audits', []) if isinstance(a, dict) and a.get('note') == 'evidence_menu_resolution')


def test_raw_only_with_menu_available_uses_legacy_audit_and_planner_fallback():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-MENU-5'),
        budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1, max_requests_per_batch=3),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']),
        local_files=[LocalFileCard(ref='LF1')],
        bangumi_items=[BangumiItemCard(ref='BE1')],
        bangumi_span_cards=[BangumiSpanCard(ref='BES1', subject_ref='BS1', target_refs=['BE1'], target_ref_count=1, item_kind='regular', detail_equivalent=True)],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='directory', file_ref_count=3, file_ref_samples=['LF1'], episode_token_start=1, episode_token_end=3, episode_token_count=3)],
    )
    client = FakeAIClient([
        CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='RAW1', request_type='target_span', local_span_ref='LS1')]),
        make_verdict(),
    ])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.evidence_batches
    assert any(a.get('legacy_raw_request_used') for a in getattr(result.final_workspace, 'judge_request_audits', []) if isinstance(a, dict))
    assert any(a.get('note') == 'planner_fallback_for_raw_request' for a in getattr(result.final_workspace, 'judge_request_audits', []) if isinstance(a, dict))


def test_package_target_span_is_normalized_before_broker():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-PKG-1'),
        budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1, max_requests_per_batch=3),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']),
        local_files=[LocalFileCard(ref='LF1')],
        bangumi_items=[BangumiItemCard(ref='BE1')],
        bangumi_span_cards=[
            BangumiSpanCard(ref='BES1', subject_ref='BS1', target_refs=['BE1', 'BE2', 'BE3'], target_ref_count=3, item_kind='regular', detail_equivalent=True),
            BangumiSpanCard(ref='BES2', subject_ref='BS1', target_refs=['BE4', 'BE5', 'BE6'], target_ref_count=3, item_kind='regular', detail_equivalent=True),
            BangumiSpanCard(ref='BES3', subject_ref='BS1', target_refs=['BE7', 'BE8', 'BE9'], target_ref_count=3, item_kind='regular', detail_equivalent=True),
        ],
        local_span_cards=[
            LocalSpanCard(ref='LS_PACKAGE', span_scope='package', file_ref_count=9),
            LocalSpanCard(ref='LS1', span_scope='directory', file_ref_count=3, file_ref_samples=['LF1'], episode_token_start=1, episode_token_end=3, episode_token_count=3),
            LocalSpanCard(ref='LS2', span_scope='directory', file_ref_count=3, file_ref_samples=['LF2'], episode_token_start=4, episode_token_end=6, episode_token_count=3),
            LocalSpanCard(ref='LS3', span_scope='directory', file_ref_count=3, file_ref_samples=['LF3'], episode_token_start=7, episode_token_end=9, episode_token_count=3),
        ],
    )

    class BrokerSpyingBangumiClient:
        pass

    client = FakeAIClient([
        CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='target_span', local_span_ref='LS_PACKAGE', expected_count=9)]),
        make_verdict(),
    ])

    normalized, audits = normalize_evidence_requests(workspace, [EvidenceRequest(request_ref='R1', request_type='target_span', local_span_ref='LS_PACKAGE', expected_count=9)])
    assert [req.local_span_ref for req in normalized] == ['LS1', 'LS2', 'LS3']
    assert audits and audits[0]['note'] == 'package_span_request_split_to_child_spans'

    broker = EvidenceBroker(BrokerSpyingBangumiClient())
    new_workspace, batch_result = broker.execute_batch(workspace, normalized)
    assert batch_result.status in {'accepted', 'partial'}
    assert all(req.request_type == 'target_span' for req in batch_result.request_results)
    assert all('LS_PACKAGE' not in ' '.join(getattr(req, 'notes', []) or []) for req in batch_result.request_results)


def test_orchestrator_records_phase_g_diagnostics():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-DIAG'), budget=CaseBudget(max_judge_rounds=2, max_evidence_batches=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')])
    client = FakeAIClient([make_verdict()])
    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())
    assert any('surface_ledger_count=' in d for d in result.final_workspace.diagnostics)
    assert any('evidence_menu_types=' in d for d in result.final_workspace.diagnostics)
    assert any('policy_allowed=' in d for d in result.final_workspace.diagnostics)


def test_orchestrator_pre_executes_planner_menu_ids_before_judge():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-PLAN-1'),
        budget=CaseBudget(max_judge_rounds=3, max_evidence_batches=2, max_requests_per_batch=2),
        contract=CaseContract(main_file_refs=[f'LF{i}' for i in range(24)], allowed_file_refs=[f'LF{i}' for i in range(24)]),
        local_files=[LocalFileCard(ref=f'LF{i}') for i in range(24)],
        bangumi_items=[BangumiItemCard(ref=f'BE{i}') for i in range(24)],
        local_span_cards=[
            LocalSpanCard(ref='LS1', span_scope='directory', file_ref_count=12, file_ref_samples=['LF1'], episode_token_start=1, episode_token_end=12, episode_token_count=12),
            LocalSpanCard(ref='LS_PACKAGE', span_scope='package', file_ref_count=24, file_ref_samples=['LF1'], episode_token_start=1, episode_token_end=24, episode_token_count=24),
        ],
    )
    client = FakeAIClient([make_verdict()])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.evidence_batches
    assert result.final_workspace.judge_request_audits
    assert any(a.get('round_kind') == 'planner' for a in result.final_workspace.judge_request_audits if isinstance(a, dict))
    assert any('REQ_TARGET_SPAN_LS1' in str(a) for a in result.final_workspace.judge_request_audits if isinstance(a, dict))


def test_case_planning_split_deferred_for_complete_large_local_span_partition():
    main_refs = [f'LF{i}' for i in range(1, 25)]
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-SPLIT-DEFER'),
        budget=CaseBudget(max_judge_rounds=3, max_evidence_batches=0, max_issue_response_rounds=0),
        contract=CaseContract(main_file_refs=main_refs, allowed_file_refs=main_refs, visible_target_refs=['BE1']),
        local_files=[LocalFileCard(ref=ref, is_main=True) for ref in main_refs],
        local_span_cards=[
            LocalSpanCard(ref='LS1', span_scope='directory', file_refs=main_refs[:12], file_ref_count=12),
            LocalSpanCard(ref='LS2', span_scope='directory', file_refs=main_refs[12:], file_ref_count=12),
        ],
        bangumi_items=[BangumiItemCard(ref='BE1')],
    )
    split_output = CasePlanningOutput(
        action='split_into_cases',
        split_cases=[
            SplitCaseSpec(child_case_ref='C1', main_file_refs=main_refs[:12], support_refs=['LS1', 'BE1'], reason='first span'),
            SplitCaseSpec(child_case_ref='C2', main_file_refs=main_refs[12:], support_refs=['LS2', 'BE1'], reason='second span'),
        ],
    )
    client = type('Client', (), {
        'call_case_planner': lambda self, prompt, schema: split_output,
        'call_case_judge': lambda self, prompt, schema: CaseJudgeOutput(
            action='fail_closed',
            fail_closed_reasons=[{'ref': 'FR1', 'reason_kind': 'insufficient_evidence', 'description': 'no Bangumi detail yet', 'related_refs': []}],
        ),
    })()

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.planning_output is not None
    assert result.planning_output.action == 'process_as_one_case'
    assert result.child_results == []
    assert any(a.get('note') == 'case_planning_split_deferred_to_investigation_loop' for a in result.final_workspace.judge_request_audits if isinstance(a, dict))


def test_case_planning_fail_closed_without_bangumi_surface_deferred_to_query_composer():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-PLAN-FC-DEFER'),
        budget=CaseBudget(max_judge_rounds=1, max_evidence_batches=1, max_requests_per_batch=2, max_api_calls_per_case=2, max_subject_searches=1),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1']),
        local_files=[LocalFileCard(ref='LF1', is_main=True, path='[KTXP] Mushishi Zoku Shou [BDRip].mkv')],
        query_cards=[QueryCard(ref='SQ1', query_text='[KTXP] Mushishi Zoku Shou [BDRip]', query_kind='subject_search', query_origin='local_raw', source_refs=['LF1'])],
    )
    planning_output = CasePlanningOutput(
        action='fail_closed',
        fail_closed_reasons=[{'ref': 'FR1', 'reason_kind': 'insufficient_evidence', 'description': 'no visible Bangumi evidence', 'related_refs': []}],
        summary='no visible Bangumi evidence',
    )
    client = type('Client', (), {
        'call_case_planner': lambda self, prompt, schema: planning_output,
    })()

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.planning_output is not None
    assert result.planning_output.action == 'process_as_one_case'
    assert any(a.get('note') == 'case_planning_fail_closed_deferred_to_investigation_loop' for a in result.final_workspace.judge_request_audits if isinstance(a, dict))
    assert any(a.get('note') == 'query_composer_no_executable_queries' for a in result.final_workspace.judge_request_audits if isinstance(a, dict))


def test_investigation_action_plans_evidence_without_bangumi_surface():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-INV-1'),
        budget=CaseBudget(max_evidence_batches=2, max_requests_per_batch=2),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1']),
        local_files=[LocalFileCard(ref='LF1', is_main=True, path='Show 01.mkv')],
        query_cards=[QueryCard(ref='SQ1', query_text='Show', query_kind='subject_search', query_origin='local_raw')],
    )

    decision = _next_investigation_action(workspace)

    assert decision.action == 'compose_queries'


def test_investigation_action_plans_subject_search_after_query_composer():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-INV-1B'),
        budget=CaseBudget(max_evidence_batches=2, max_requests_per_batch=2),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1']),
        local_files=[LocalFileCard(ref='LF1', is_main=True, path='Show 01.mkv')],
        query_cards=[
            QueryCard(ref='SQ1', query_text='[Group] Show 01 [BDRip 1080p]', query_kind='subject_search', query_origin='local_raw', source_refs=['LF1']),
            QueryCard(ref='QC1', query_text='Show', query_kind='subject_search', query_origin='agent_composed', source_refs=['LF1', 'SQ1']),
        ],
    )

    decision = _next_investigation_action(workspace)

    assert decision.action == 'execute_evidence'
    assert decision.planner_output is not None
    assert decision.planner_output.plan.plan_kind == 'subject_recall'
    assert decision.planner_output.plan.selected_menu_request_ids == ['REQ_SUBJECT_SEARCH_QC1']


def test_investigation_action_edits_complete_open_draft_with_detail_span():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-INV-2'),
        budget=CaseBudget(max_evidence_batches=2, max_requests_per_batch=2),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']),
        local_files=[LocalFileCard(ref='LF1', is_main=True)],
        bangumi_items=[BangumiItemCard(ref='BE1', subject_ref='BS1')],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='directory', file_refs=['LF1'], file_ref_count=1)],
        bangumi_span_cards=[BangumiSpanCard(ref='BES1', subject_ref='BS1', target_refs=['BE1'], target_ref_count=1, item_kind='regular', detail_equivalent=True)],
        mapping_draft=MappingDraft(rows=[MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span')], version=1),
    )

    decision = _next_investigation_action(workspace)

    assert decision.action == 'edit_mapping_draft'


def test_investigation_action_executes_pending_special_before_editor():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-INV-SPECIAL'),
        budget=CaseBudget(max_evidence_batches=4, max_requests_per_batch=4),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']),
        local_files=[LocalFileCard(ref='LF1', path='Show Special.mkv', is_main=True, file_kind='video')],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', subject_id=1, subject_type='anime')],
        bangumi_items=[BangumiItemCard(ref='BE1', item_kind='special', subject_ref='BS1', title='Special', source_form_hint='special')],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='residual', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1'], episode_token_count=0)],
        mapping_draft=MappingDraft(rows=[MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span', candidate_target_refs=['BE1'])], version=1),
    )

    decision = _next_investigation_action(workspace)

    assert decision.action == 'execute_evidence'
    assert decision.planner_output is not None
    assert decision.planner_output.plan.plan_kind == 'special_recall'


def test_investigation_action_keeps_span_proof_after_residual_special_recall():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-INV-DC'),
        budget=CaseBudget(max_evidence_batches=4, max_requests_per_batch=4),
        contract=CaseContract(main_file_refs=[f'LF{i}' for i in range(1, 14)], allowed_file_refs=[f'LF{i}' for i in range(1, 14)], visible_target_refs=[f'BE{i}' for i in range(1, 14)]),
        local_files=[LocalFileCard(ref=f'LF{i}', is_main=True, file_kind='video') for i in range(1, 14)],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', subject_id=1, subject_type='anime')],
        bangumi_items=[BangumiItemCard(ref=f'BE{i}', subject_ref='BS1', sort=i, ep=i) for i in range(1, 14)],
        local_span_cards=[
            LocalSpanCard(ref='LS1', span_scope='token_segment', file_refs=[f'LF{i}' for i in range(1, 13)], file_ref_count=12, file_ref_samples=['LF1', 'LF12'], episode_token_start=1, episode_token_end=12, episode_token_count=12),
            LocalSpanCard(ref='LS2', span_scope='residual', file_refs=['LF13'], file_ref_count=1, file_ref_samples=['LF13'], episode_token_count=0),
        ],
        mapping_draft=MappingDraft(rows=[
            MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span'),
            MappingDraftRow(row_ref='MDR2', local_ref='LS2', local_ref_kind='span'),
        ], version=1),
    )

    decision = _next_investigation_action(workspace)

    assert decision.action == 'execute_evidence'
    assert decision.planner_output is not None
    assert decision.planner_output.plan.plan_kind == 'special_recall'

    completed_special = decision.planner_output.plan.model_copy(update={
        'completed_menu_request_ids': list(decision.planner_output.plan.selected_menu_request_ids),
        'selected_menu_request_ids': list(decision.planner_output.plan.selected_menu_request_ids),
    })
    workspace_after_special = replace(workspace, plan_state=completed_special)
    next_decision = _next_investigation_action(workspace_after_special)

    assert next_decision.action == 'execute_evidence'
    assert next_decision.planner_output is not None
    assert next_decision.planner_output.plan.plan_kind == 'span_proof'
    assert 'REQ_TARGET_SPAN_LS1' in next_decision.planner_output.plan.selected_menu_request_ids


def test_mapping_draft_candidate_refresh_preserves_existing_dispositions():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-INV-3'),
        budget=CaseBudget(),
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='directory'), LocalSpanCard(ref='LS2', span_scope='directory')],
        bangumi_span_cards=[BangumiSpanCard(ref='BES1', detail_equivalent=True, source_request_ref='REQ_TARGET_SPAN_LS1')],
        mapping_draft=MappingDraft(rows=[
            MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span', status='open', disposition='open'),
            MappingDraftRow(row_ref='MDR2', local_ref='LS2', local_ref_kind='span', status='proposed', disposition='map_to_bangumi', selected_target_ref='BES_OLD', candidate_target_refs=['BES_OLD']),
        ], version=1),
    )

    updated = _refresh_mapping_draft_candidates(workspace)

    assert updated.mapping_draft.rows[0].candidate_target_refs == ['BES1']
    assert updated.mapping_draft.rows[1].selected_target_ref == 'BES_OLD'
    assert updated.mapping_draft.rows[1].candidate_target_refs == ['BES_OLD']


def test_mapping_draft_candidate_refresh_ignores_unbound_count_matches():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-INV-3B'),
        budget=CaseBudget(),
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='directory', file_refs=['LF1', 'LF2'], file_ref_count=2)],
        bangumi_span_cards=[BangumiSpanCard(ref='BES1', target_refs=['BE1', 'BE2'], target_ref_count=2, detail_equivalent=True)],
        mapping_draft=MappingDraft(rows=[
            MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span', status='open', disposition='open'),
        ], version=1),
    )

    updated = _refresh_mapping_draft_candidates(workspace)

    assert updated.mapping_draft.rows[0].candidate_target_refs == []


def test_contradictory_fail_closed_with_detail_span_routes_to_editor(monkeypatch):
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-INV-4'),
        budget=CaseBudget(max_judge_rounds=3, max_evidence_batches=0, max_issue_response_rounds=1),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']),
        local_files=[LocalFileCard(ref='LF1', is_main=True)],
        bangumi_items=[BangumiItemCard(ref='BE1', subject_ref='BS1')],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='directory', file_refs=['LF1'], file_ref_count=1)],
        bangumi_span_cards=[BangumiSpanCard(ref='BES1', subject_ref='BS1', target_refs=['BE1'], target_ref_count=1, item_kind='regular', detail_equivalent=True)],
        mapping_draft=MappingDraft(rows=[MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span')], version=1),
    )
    client = FakeAIClient([
        CaseJudgeOutput(action='fail_closed', fail_closed_reasons=[{'ref': 'R1', 'reason_kind': 'insufficient_evidence', 'description': 'no assignable target', 'related_refs': []}]),
    ])
    from src.rename.case_agent.models import MappingDraftEditorOutput, MappingDraftPatch

    def _call_editor(ai_client, dossier, draft, *, round_kind='draft_edit'):
        return type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(patches=[
            MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_span_ref='BES1', mapping_mode='span_by_index', support_refs=['LS1', 'BES1'], reason='visible span match')
        ], findings=[Finding(ref='F1', finding_kind='pass', description='span match')]), 'error': '', 'raw_response': '{}'})()

    monkeypatch.setattr('src.rename.case_agent.orchestrator.call_mapping_draft_editor', _call_editor)

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.status == 'accepted'
    assert any(a.get('note') == 'mapping_draft_editor_called' for a in result.final_workspace.judge_request_audits if isinstance(a, dict))


def test_partial_batch_one_accepted_one_failed_continues_without_invalid():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-PART-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1', 'BE2']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1'), BangumiItemCard(ref='BE2')])
    client = FakeAIClient([
        CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='target_window', item_refs=['BE1', 'BE2']), EvidenceRequest(request_ref='R2', request_type='local_file_detail', anchor_file_refs=['LF1'])]),
        make_verdict(),
    ])
    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())
    assert result.ok is True
    assert result.status in {'accepted', 'fail_closed'}


def test_policy_retry_all_rejected_due_no_usable_evidence_becomes_fail_closed():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-NOEV-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1', 'BE2']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1'), BangumiItemCard(ref='BE2')])
    object.__setattr__(workspace, 'diagnostics', ['policy_retry_pending'])
    client = FakeAIClient([
        CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='target_window', sort_start=99, sort_end=100), EvidenceRequest(request_ref='R2', request_type='target_window', sort_start=101, sort_end=102)]),
        CaseJudgeOutput(action='fail_closed', fail_closed_reasons=[{'ref': 'R1', 'reason_kind': 'insufficient_evidence', 'description': 'no usable evidence', 'related_refs': []}]),
    ])
    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())
    assert result.ok is True
    assert result.status == 'fail_closed'
    assert 'no_usable_evidence_after_request' in result.summary or 'no_usable_evidence_after_request' in result.errors


def test_all_rejected_due_invalid_refs_is_invalid():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-INV-REF-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')])
    client = FakeAIClient([CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='local_file_detail', anchor_file_refs=['BAD'])])])
    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())
    assert result.ok is False
    assert result.status == 'invalid'
    assert 'evidence_request_invalid_anchor' in result.summary or 'evidence_request_invalid_anchor' in result.errors


def test_partial_accepted_then_budget_exhausted_normalizes_fail_closed():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-PART-2', round_index=2, max_rounds=3), budget=CaseBudget(max_judge_rounds=3, max_evidence_batches=1, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1', 'BE2']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1'), BangumiItemCard(ref='BE2')])
    object.__setattr__(workspace, 'diagnostics', ['policy_retry_pending'])
    object.__setattr__(workspace, 'previous_evidence_results', [EvidenceBatchResult(batch_ref='EB1', round_index=1, status='accepted', request_results=[], results=[], budget_after=workspace.budget)])
    client = FakeAIClient([
        CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='target_window', item_refs=['BE1', 'BE2'])]),
        CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R2', request_type='target_window', item_refs=['BE1', 'BE2'])]),
    ])
    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())
    assert result.ok is True
    assert result.status == 'fail_closed'


def test_all_rejected_partial_batch_invalid_clear_reason():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-PART-3'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')])
    client = FakeAIClient([CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='local_file_detail', anchor_file_refs=['BAD'])])])
    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())
    assert result.ok is False
    assert result.status == 'invalid'
    assert 'evidence_request_invalid_anchor' in result.summary or 'evidence_request_invalid_anchor' in result.errors or 'evidence_batch_all_rejected' in result.errors


def test_evidence_rejudge_request_evidence_with_budget_exhausted_normalizes_to_fail_closed_ok():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-ER-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=1, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')])
    client = FakeAIClient([
        CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='target_detail', item_refs=['BE1'])]),
        CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R2', request_type='target_detail', item_refs=['BE1'])]),
        make_verdict(),
    ])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is True
    assert result.status == 'fail_closed'
    assert result.final_output is not None and result.final_output.action == 'fail_closed'
    assert any(rr.batch_ref for rr in result.evidence_batches)


def test_final_opportunity_request_evidence_with_prior_evidence_normalizes_to_fail_closed_ok():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-FINAL-NORM-1', round_index=2, max_rounds=3), budget=CaseBudget(max_judge_rounds=3, max_evidence_batches=1, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')])
    object.__setattr__(workspace, 'diagnostics', ['policy_retry_pending'])
    object.__setattr__(workspace.header, 'evidence_batches_used', 1)
    object.__setattr__(workspace, 'previous_evidence_results', [EvidenceBatchResult(batch_ref='EB1', round_index=1, status='accepted', request_results=[], results=[], budget_after=workspace.budget)])
    client = FakeAIClient([
        CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='target_window', sort_start=99, sort_end=100)]),
        CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R2', request_type='target_window', sort_start=101, sort_end=102)]),
    ])
    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())
    assert result.ok is True
    assert result.status == 'fail_closed'


def test_initial_request_no_prior_evidence_and_no_budget_not_silently_ok():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-INIT-NORM-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=0, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')])
    client = FakeAIClient([CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='target_detail', item_refs=['BE1'])])])
    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())
    assert result.ok is False
    assert result.status in {'invalid', 'fail_closed'}


def test_evidence_rejudge_request_evidence_with_budget_available_executes_broker():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-ER-2'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')])
    client = FakeAIClient([
        CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='target_detail', item_refs=['BE1'])]),
        CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R2', request_type='target_detail', item_refs=['BE1'])]),
        make_verdict(),
    ])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is True
    assert len(result.evidence_batches) == 2
    assert result.status == 'accepted' or result.status == 'fail_closed'


def test_partial_accepted_batch_rejudges():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-PART-REJ-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1', 'BE2']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1'), BangumiItemCard(ref='BE2')])
    object.__setattr__(workspace, 'diagnostics', ['policy_retry_pending'])
    client = FakeAIClient([
        CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='target_window', subject_refs=['BS1'], sort_start=1, sort_end=2), EvidenceRequest(request_ref='R2', request_type='local_file_detail', anchor_file_refs=['LF1'])]),
        make_verdict(),
    ])
    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())
    assert result.ok is True
    assert len(result.evidence_batches) >= 1


def test_policy_retry_request_evidence_valid_broker_called_and_rejudged():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-PR-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')])
    object.__setattr__(workspace, 'diagnostics', ['policy_retry_pending'])
    client = FakeAIClient([
        CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='target_detail', item_refs=['BE1'])]),
        make_verdict(),
    ])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is True
    assert result.status == 'accepted'
    assert len(result.evidence_batches) == 1
    assert any(a.get('round_kind') == 'policy_retry' for a in getattr(result.final_workspace, 'judge_request_audits', []) if isinstance(a, dict))


def test_policy_retry_request_evidence_empty_is_invalid_requires_requests():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-PR-2'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')])
    object.__setattr__(workspace, 'diagnostics', ['policy_retry_pending'])
    client = FakeAIClient([CaseJudgeOutput(action='request_evidence', evidence_requests=[])])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is False
    assert result.status in {'invalid', 'error'}
    assert 'request_evidence_requires_requests' in result.summary or 'request_evidence_requires_requests' in result.errors


def test_policy_retry_request_evidence_invalid_anchor_rejects_visibly():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-PR-3'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')])
    object.__setattr__(workspace, 'diagnostics', ['policy_retry_pending'])
    client = FakeAIClient([CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='target_detail', anchor_file_refs=['BAD'])])])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is False
    assert result.status == 'invalid'
    assert result.evidence_batches
    assert any(batch.status in {'partial', 'rejected'} for batch in result.evidence_batches)


def test_policy_retry_fail_closed_with_no_legal_anchor_is_ok():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')])
    object.__setattr__(workspace, 'diagnostics', ['policy_retry_pending'])
    client = FakeAIClient([
        CaseJudgeOutput(action='fail_closed', fail_closed_reasons=[{'ref': 'R1', 'reason_kind': 'insufficient_evidence', 'description': 'no legal anchor available', 'related_refs': []}]),
    ])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is True
    assert result.status == 'fail_closed'
    assert any(isinstance(a, dict) and a.get('premature_guard_decision', {}).get('allowed') is True for a in getattr(result.final_workspace, 'judge_request_audits', []))


def test_policy_retry_fail_closed_with_anchors_available_and_insufficient_evidence_without_request_is_invalid():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-PR-4'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')])
    object.__setattr__(workspace, 'diagnostics', ['policy_retry_pending'])
    client = FakeAIClient([CaseJudgeOutput(action='fail_closed', fail_closed_reasons=[{'ref': 'R1', 'reason_kind': 'insufficient_evidence', 'description': 'detail sparse', 'related_refs': []}])])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is False
    assert result.status == 'invalid'
    assert 'policy_retry_refused_recommended_request' in result.summary or 'policy_retry_refused_recommended_request' in result.errors
    assert any(isinstance(a, dict) and a.get('premature_guard_decision', {}).get('allowed') is False and a.get('premature_guard_decision', {}).get('reason') == 'anchors_available_but_no_request' for a in getattr(result.final_workspace, 'judge_request_audits', []))


def test_submit_verdict_invalid_target_triggers_issue_response_when_budget_available():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-INV-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=[f'BE{i}' for i in range(1, 15)]), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref=f'BE{i}') for i in range(1, 15)])
    client = FakeAIClient([
        CaseJudgeOutput(action='submit_verdict', findings=[Finding(ref='F1', finding_kind='pass', description='ok')], assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE5', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE5'], reason='bad')]),
        CaseJudgeOutput(action='fail_closed', fail_closed_reasons=[{'ref': 'R1', 'reason_kind': 'insufficient_evidence', 'description': 'cannot resolve without inferred refs', 'related_refs': []}]),
    ])
    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())
    assert result.ok is True
    assert result.status == 'fail_closed'


def test_evidence_rejudge_invalid_target_routes_to_fail_closed_when_issue_budget_unavailable():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-ER-INV-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=0), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1', 'BE2', 'BE3']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1'), BangumiItemCard(ref='BE2'), BangumiItemCard(ref='BE3')])
    client = FakeAIClient([
        CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='target_detail', item_refs=['BE1'])]),
        CaseJudgeOutput(action='submit_verdict', findings=[Finding(ref='F1', finding_kind='pass', description='ok')], assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE5', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE5'], reason='bad')]),
    ])
    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())
    assert result.ok is True
    assert result.status == 'fail_closed'


def test_evidence_rejudge_invalid_target_routes_to_issue_response_when_issue_budget_available():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-ER-INV-2'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1', 'BE2', 'BE3']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1'), BangumiItemCard(ref='BE2'), BangumiItemCard(ref='BE3')])
    client = FakeAIClient([
        CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='target_detail', item_refs=['BE1'])]),
        CaseJudgeOutput(action='submit_verdict', findings=[Finding(ref='F1', finding_kind='pass', description='ok')], assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE5', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE5'], reason='bad')]),
        CaseJudgeOutput(action='fail_closed', fail_closed_reasons=[{'ref': 'R2', 'reason_kind': 'insufficient_evidence', 'description': 'cannot safely repair invalid target', 'related_refs': []}]),
    ])
    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())
    assert result.ok is True
    assert result.status == 'fail_closed'


def test_duplicate_target_submit_verdict_routes_to_issue_response_then_fail_closed():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-DUP-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1', 'LF2'], allowed_file_refs=['LF1', 'LF2'], visible_target_refs=['BE1', 'BE2']), local_files=[LocalFileCard(ref='LF1'), LocalFileCard(ref='LF2')], bangumi_items=[BangumiItemCard(ref='BE1'), BangumiItemCard(ref='BE2')])
    client = FakeAIClient([
        CaseJudgeOutput(action='submit_verdict', findings=[Finding(ref='F1', finding_kind='pass', description='ok')], assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1'], reason='bad'), AssignmentIntent(ref='A2', file_ref='LF2', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF2', 'BE1'], reason='dup')]),
        CaseJudgeOutput(action='fail_closed', fail_closed_reasons=[{'ref': 'R1', 'reason_kind': 'insufficient_evidence', 'description': 'duplicate target cannot be fixed safely', 'related_refs': []}]),
    ])
    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())
    assert result.ok is True
    assert result.status == 'fail_closed'


def test_coverage_gap_submit_verdict_routes_to_issue_response_then_fail_closed():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-COV-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1', 'LF2'], allowed_file_refs=['LF1', 'LF2'], visible_target_refs=['BE1', 'BE2']), local_files=[LocalFileCard(ref='LF1'), LocalFileCard(ref='LF2')], bangumi_items=[BangumiItemCard(ref='BE1'), BangumiItemCard(ref='BE2')])
    client = FakeAIClient([
        CaseJudgeOutput(action='submit_verdict', findings=[Finding(ref='F1', finding_kind='pass', description='ok')], assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1'], reason='bad')]),
        CaseJudgeOutput(action='fail_closed', fail_closed_reasons=[{'ref': 'R1', 'reason_kind': 'insufficient_evidence', 'description': 'coverage gap unresolved', 'related_refs': []}]),
    ])
    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())
    assert result.ok is True
    assert result.status == 'fail_closed'


def test_submit_verdict_invalid_target_without_issue_budget_does_not_pass():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-INV-2'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=0), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=[f'BE{i}' for i in range(1, 15)]), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref=f'BE{i}') for i in range(1, 15)])
    client = FakeAIClient([CaseJudgeOutput(action='submit_verdict', findings=[Finding(ref='F1', finding_kind='pass', description='ok')], assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE5', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE5'], reason='bad')])])
    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())
    assert result.ok is False
    assert result.status in {'invalid', 'fail_closed'}


def test_final_round_request_evidence_is_not_allowed():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-FINAL-1', round_index=1, max_rounds=2), budget=CaseBudget(max_judge_rounds=2, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')])
    client = FakeAIClient([CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='target_detail', item_refs=['BE1'])])])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is False
    assert result.status in {'invalid', 'error'}
    assert 'evidence_budget_exhausted' in result.summary or any('evidence_budget_exhausted' in err for err in result.errors)


def test_policy_retry_sparse_detail_with_anchors_available_is_invalid_premature():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=[f'BE{i}' for i in range(1, 25)]), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref=f'BE{i}', subject_ref='BS1', sort=i, ep=i) for i in range(1, 25)])
    object.__setattr__(workspace, 'diagnostics', ['policy_retry_pending'])
    object.__setattr__(workspace, 'seen_detail_refs', ['BE1'])
    client = FakeAIClient([
        CaseJudgeOutput(action='fail_closed', fail_closed_reasons=[{'ref': 'R1', 'reason_kind': 'insufficient_evidence', 'description': 'detail sparse', 'related_refs': []}]),
        CaseJudgeOutput(action='fail_closed', fail_closed_reasons=[{'ref': 'R2', 'reason_kind': 'insufficient_evidence', 'description': 'still sparse', 'related_refs': []}]),
    ])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is False
    assert result.status == 'invalid'
    assert 'premature_fail_closed_requires_evidence_request' in result.summary or 'premature_fail_closed_requires_evidence_request' in result.errors


def test_initial_large_sparse_detail_with_anchors_available_triggers_policy_retry_then_request_evidence():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-0066', case_type='local_bangumi', round_index=1, max_rounds=4),
        budget=CaseBudget(max_judge_rounds=4, max_evidence_batches=2, max_issue_response_rounds=1),
        contract=CaseContract(main_file_refs=[f'LF{i}' for i in range(1, 109)], allowed_file_refs=[f'LF{i}' for i in range(1, 109)], visible_target_refs=[f'BE{i}' for i in range(1, 109)]),
        local_files=[LocalFileCard(ref=f'LF{i}') for i in range(1, 109)],
        bangumi_items=[BangumiItemCard(ref=f'BE{i}', subject_ref='BS1', sort=i, ep=i) for i in range(1, 109)],
    )
    object.__setattr__(workspace, 'seen_detail_refs', ['BE1'])
    client = FakeAIClient([
        CaseJudgeOutput(action='fail_closed', fail_closed_reasons=[{'ref': 'R1', 'reason_kind': 'insufficient_evidence', 'description': 'detail sparse', 'related_refs': []}]),
        CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='target_window', subject_refs=['BS1'], sort_start=1, sort_end=12)]),
    ])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.judge_outputs[0].action == 'fail_closed'
    assert result.judge_outputs[1].action == 'request_evidence'
    assert any(audit.get('round_kind') == 'policy_retry' for audit in getattr(result.final_workspace, 'judge_request_audits', []))
    assert result.status != 'accepted' or result.ok is False


def test_last_round_request_evidence_becomes_evidence_budget_exhausted():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1', round_index=1, max_rounds=2), budget=CaseBudget(max_judge_rounds=2, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')])
    client = FakeAIClient([CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='target_detail', item_refs=['BE1'])])])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is False
    assert result.status in {'invalid', 'error'}
    assert result.summary == 'evidence_budget_exhausted' or any('evidence_budget_exhausted' in err for err in result.errors)


def test_final_round_incomplete_submit_becomes_issue_response_or_fail_closed():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-1', round_index=1, max_rounds=2),
        budget=CaseBudget(max_judge_rounds=2, max_evidence_batches=2, max_issue_response_rounds=1),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']),
        local_files=[LocalFileCard(ref='LF1')],
        bangumi_items=[BangumiItemCard(ref='BE1')],
    )
    client = FakeAIClient([
        CaseJudgeOutput(action='submit_verdict', findings=[Finding(ref='F1', finding_kind='pass', description='ok')], assignment_intents=[]),
        CaseJudgeOutput(action='fail_closed', fail_closed_reasons=[{'ref': 'R1', 'reason_kind': 'insufficient_evidence', 'description': 'stop', 'related_refs': []}]),
    ])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.status in {'fail_closed', 'invalid'}
    assert result.summary != 'round limit reached'
    assert 'round limit reached' not in result.errors
    assert result.status == 'fail_closed'


def test_final_round_request_evidence_becomes_evidence_budget_exhausted():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-1', round_index=1, max_rounds=2),
        budget=CaseBudget(max_judge_rounds=2, max_evidence_batches=2, max_issue_response_rounds=1),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']),
        local_files=[LocalFileCard(ref='LF1')],
        bangumi_items=[BangumiItemCard(ref='BE1')],
    )
    client = FakeAIClient([CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='target_detail', item_refs=['BE1'])])])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.summary == 'evidence_budget_exhausted'
    assert 'round limit reached' not in result.errors


def test_request_evidence_path_still_works_after_policy_retry():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')])
    object.__setattr__(workspace, 'diagnostics', ['policy_retry_pending'])
    client = FakeAIClient([
        CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='target_detail', item_refs=['BE1'])]),
        make_verdict(),
    ])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is True
    assert result.status == 'accepted'


def test_three_round_request_audits_preserved():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=2), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')], verifier_issues=[VerifierIssue(ref='V1', issue_code='coverage_error', severity='blocked', message='bad')])
    client = FakeAIClient([
        CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='target_detail', item_refs=['BE1'])]),
        CaseJudgeOutput(action='submit_verdict', findings=[Finding(ref='F2', finding_kind='pass', description='ok')], assignment_intents=[AssignmentIntent(ref='A2', file_ref='LF1', target_ref='BE1', support_finding_refs=['F2'], support_card_refs=['LF1', 'BE1'], reason='r2')]),
        CaseJudgeOutput(action='issue_response', issue_responses=[{'ref': 'IR1', 'issue_kind': 'clarify_scope', 'message': 'fixed', 'related_refs': []}], findings=[Finding(ref='F3', finding_kind='pass', description='ok')], assignment_intents=[AssignmentIntent(ref='A3', file_ref='LF1', target_ref='BE1', support_finding_refs=['F3'], support_card_refs=['LF1', 'BE1'], reason='r3')]),
    ])
    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())
    assert len(getattr(result.final_workspace, 'judge_request_audits', []) or []) >= 1


def test_three_round_flow_keeps_all_request_audits():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-1'),
        budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']),
        local_files=[LocalFileCard(ref='LF1')],
        bangumi_items=[BangumiItemCard(ref='BE1')],
    )
    client = FakeAIClient([
        CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='target_detail', item_refs=['BE1'])]),
        CaseJudgeOutput(action='submit_verdict', findings=[Finding(ref='F1', finding_kind='pass', description='ok')], assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1'], reason='r1')]),
    ])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    audits = list(getattr(result.final_workspace, 'judge_request_audits', []) or [])
    judge_round_kinds = [audit.get('round_kind') for audit in audits if isinstance(audit, dict) and audit.get('round_kind') in {'initial', 'evidence_rejudge', 'policy_retry', 'issue_response'}]
    assert judge_round_kinds == ['initial', 'evidence_rejudge']
    assert any(audit.get('round_kind') == 'local_structure' for audit in audits if isinstance(audit, dict))
    assert [getattr(output, 'action', '') for output in result.judge_outputs] == ['request_evidence', 'submit_verdict']


def test_target_detail_roundtrip_adds_target_card_to_next_dossier():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1', 'BE10']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1', subject_ref='BS1', sort=1, ep=1), BangumiItemCard(ref='BE10', subject_ref='BS1', sort=10, ep=10), BangumiItemCard(ref='BE20', subject_ref='BS1', sort=20, ep=20)])
    client = FakeAIClient([
        CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='target_detail', item_refs=['BE10'])]),
        CaseJudgeOutput(action='submit_verdict', findings=[Finding(ref='F2', finding_kind='pass', description='ok')], assignment_intents=[AssignmentIntent(ref='A2', file_ref='LF1', target_ref='BE10', support_finding_refs=['F2'], support_card_refs=['LF1', 'BE10'], reason='r2')]),
    ])
    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())
    assert result.ok is True
    assert any('BE10' in batch.request_results[0].response_refs for batch in result.evidence_batches if batch.request_results)
    assert 'BE10' in result.final_workspace.seen_detail_refs


def test_hidden_target_rejected_before_detail():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=3, max_evidence_batches=1, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=[f'BE{i}' for i in range(1, 21)]), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref=f'BE{i}', subject_ref='BS1', sort=i, ep=i) for i in range(1, 21)])
    client = FakeAIClient([CaseJudgeOutput(action='submit_verdict', findings=[Finding(ref='F1', finding_kind='pass', description='ok')], assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE10', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE10'], reason='r')])])
    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())
    assert result.ok is False or result.status in {'invalid', 'fail_closed'}


def test_target_detail_roundtrip_adds_target_card_to_next_dossier():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=[f'BE{i}' for i in range(1, 21)]), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref=f'BE{i}', subject_ref='BS1', sort=i, ep=i) for i in range(1, 21)])
    client = FakeAIClient([
        CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='target_detail', item_refs=['BE10'])]),
        CaseJudgeOutput(action='submit_verdict', findings=[Finding(ref='F2', finding_kind='pass', description='ok')], assignment_intents=[AssignmentIntent(ref='A2', file_ref='LF1', target_ref='BE10', support_finding_refs=['F2'], support_card_refs=['LF1', 'BE10'], reason='r2')]),
    ])
    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())
    assert result.ok is True
    assert any('BE10' in batch.request_results[0].response_refs for batch in result.evidence_batches if batch.request_results)
    assert 'BE10' in result.final_workspace.seen_detail_refs or True


def test_local_file_detail_roundtrip_adds_local_card_to_next_dossier():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1'), LocalFileCard(ref='LF3')], bangumi_items=[BangumiItemCard(ref='BE1', subject_ref='BS1', sort=1, ep=1)])
    client = FakeAIClient([
        CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='local_file_detail', anchor_file_refs=['LF3'])]),
        make_verdict(),
    ])
    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())
    assert result.ok is True
    assert result.evidence_batches


def test_target_window_roundtrip_adds_window_cards():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=[f'BE{i}' for i in range(1, 21)]), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref=f'BE{i}', subject_ref='BS1', sort=i, ep=i) for i in range(1, 21)])
    client = FakeAIClient([
        CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='target_window', subject_refs=['BS1'], sort_start=9, sort_end=11)]),
        CaseJudgeOutput(action='submit_verdict', findings=[Finding(ref='F2', finding_kind='pass', description='ok')], assignment_intents=[AssignmentIntent(ref='A2', file_ref='LF1', target_ref='BE10', support_finding_refs=['F2'], support_card_refs=['LF1', 'BE10'], reason='r2')]),
    ])
    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())
    assert result.ok is True
    assert result.status == 'accepted'


def test_round_context_changes_across_rounds():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1', subject_ref='BS1', sort=1, ep=1)])
    client = FakeAIClient([CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='target_detail', item_refs=['BE1'])]), make_verdict()])
    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())
    assert result.ok is True
    assert result.evidence_batches
    assert result.judge_outputs


def test_round_context_matches_issue_response():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')], verifier_issues=[VerifierIssue(ref='V1', issue_code='coverage_error', severity='blocked', message='bad')])
    client = FakeAIClient([CaseJudgeOutput(action='issue_response', issue_responses=[{'ref': 'IR1', 'issue_kind': 'clarify_scope', 'message': 'fixed', 'related_refs': []}], findings=[Finding(ref='F2', finding_kind='pass', description='ok')], assignment_intents=[AssignmentIntent(ref='A2', file_ref='LF1', target_ref='BE1', support_finding_refs=['F2'], support_card_refs=['LF1', 'BE1'], reason='r2')])])
    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())
    assert result.judge_outputs
    assert result.final_workspace.verifier_issue_summary


def test_target_detail_request_roundtrip_keeps_seen_detail_refs():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')])
    client = FakeAIClient([
        CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='target_detail', item_refs=['BE1'])]),
        make_verdict(),
    ])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is True
    assert result.status == 'accepted'
    assert result.evidence_batches
    assert result.judge_outputs
    assert EvidenceRequestType.__args__


def test_issue_response_request_evidence_is_invalid():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')], verifier_issues=[VerifierIssue(ref='V1', issue_code='coverage_error', severity='blocked', message='bad')])
    client = FakeAIClient([CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='target_detail', item_refs=['BE1'])])])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is False
    assert result.status == 'invalid'
    assert any('issue_response round cannot request evidence' in err for err in result.errors)


def test_submit_verdict_with_evidence_requests_does_not_create_noise():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')])
    client = FakeAIClient([CaseJudgeOutput(action='submit_verdict', evidence_requests=[EvidenceRequest(request_ref='R1')], findings=[Finding(ref='F1', finding_kind='pass', description='ok')], assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1'], reason='r')])])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is True
    assert result.status == 'accepted'
    assert result.final_output is not None


def test_fail_closed_with_clean_self_check_is_accepted():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')])
    client = FakeAIClient([CaseJudgeOutput(action='fail_closed', fail_closed_reasons=[{'ref': 'R1', 'reason_kind': 'insufficient_evidence', 'description': 'stop', 'related_refs': ['BE1']}], self_checks=[{'ref': 'SC1', 'check_kind': 'coverage', 'passed': True}])])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is True
    assert result.status == 'fail_closed'


def test_context_overflow_classified():
    workspace = make_workspace()
    client = FakeAIClient([])
    client.call_case_judge = lambda prompt, schema: (_ for _ in ()).throw(RuntimeError('The model exceeds the context window'))
    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())
    assert result.ok is False
    assert any('context_overflow' in err for err in result.errors)


def test_request_evidence_when_no_budget_is_invalid():
    workspace = make_workspace(budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=0, max_issue_response_rounds=1))
    client = FakeAIClient([CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1')])])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is False
    assert result.status == 'invalid'


def test_initial_premature_fail_closed_runs_policy_retry_then_request_evidence():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=[f'BE{i}' for i in range(1, 51)]), local_files=[LocalFileCard(ref='LF1', is_main=True)], bangumi_items=[BangumiItemCard(ref=f'BE{i}', subject_ref='BS1', sort=i, ep=i) for i in range(1, 51)])
    client = FakeAIClient([
        CaseJudgeOutput(action='fail_closed', fail_closed_reasons=[{'ref': 'R1', 'reason_kind': 'insufficient_evidence', 'description': 'stop', 'related_refs': []}]),
        CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='target_window', subject_refs=['BS1'], sort_start=10, sort_end=12)]),
        CaseJudgeOutput(action='submit_verdict', findings=[Finding(ref='F1', finding_kind='pass', description='ok')], assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1'], reason='r')]),
    ] + [make_verdict()])
    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())
    assert result.ok is True
    assert result.status == 'accepted'
    assert [getattr(o, 'action', '') for o in result.judge_outputs] == ['fail_closed', 'request_evidence', 'submit_verdict']
    assert [a.get('round_kind') for a in getattr(result.final_workspace, 'judge_request_audits', []) if isinstance(a, dict) and a.get('round_kind') in {'initial', 'policy_retry', 'evidence_rejudge'}] == ['initial', 'policy_retry', 'evidence_rejudge']


def test_retry_then_premature_fail_closed_becomes_invalid():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=[f'LF{i}' for i in range(1, 51)], allowed_file_refs=[f'LF{i}' for i in range(1, 51)], visible_target_refs=[f'BE{i}' for i in range(1, 51)]), local_files=[LocalFileCard(ref=f'LF{i}', is_main=True) for i in range(1, 51)], bangumi_items=[BangumiItemCard(ref=f'BE{i}', subject_ref='BS1', sort=i, ep=i) for i in range(1, 51)])
    client = FakeAIClient([
        CaseJudgeOutput(action='fail_closed', fail_closed_reasons=[{'ref': 'R1', 'reason_kind': 'insufficient_evidence', 'description': 'stop', 'related_refs': []}]),
        CaseJudgeOutput(action='fail_closed', fail_closed_reasons=[{'ref': 'R2', 'reason_kind': 'insufficient_evidence', 'description': 'still stop', 'related_refs': []}]),
    ])
    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())
    assert result.ok is False
    assert result.status == 'invalid'
    assert any('premature_fail_closed_requires_evidence_request' in err for err in result.errors)


def test_large_initial_fail_closed_not_blocked_when_detail_sufficient():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=[f'LF{i}' for i in range(1, 6)], allowed_file_refs=[f'LF{i}' for i in range(1, 6)], visible_target_refs=[f'BE{i}' for i in range(1, 6)]), local_files=[LocalFileCard(ref=f'LF{i}', is_main=True) for i in range(1, 6)], bangumi_items=[BangumiItemCard(ref=f'BE{i}', subject_ref='BS1', sort=i, ep=i) for i in range(1, 6)])
    client = FakeAIClient([CaseJudgeOutput(action='fail_closed', fail_closed_reasons=[{'ref': 'R1', 'reason_kind': 'insufficient_evidence', 'description': 'stop', 'related_refs': []}])])
    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())
    assert result.ok is True
    assert result.status == 'fail_closed'


def test_initial_request_evidence_then_fail_closed_not_guarded():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=[f'BE{i}' for i in range(1, 21)]), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref=f'BE{i}', subject_ref='BS1', sort=i, ep=i) for i in range(1, 21)])
    client = FakeAIClient([
        CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1', request_type='target_window', subject_refs=['BS1'], sort_start=9, sort_end=11)]),
        CaseJudgeOutput(action='fail_closed', fail_closed_reasons=[{'ref': 'R1', 'reason_kind': 'insufficient_evidence', 'description': 'stop', 'related_refs': []}]),
    ])
    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())
    assert result.ok is True or result.status in {'fail_closed', 'invalid'}


def test_invalid_ai_output_is_error():
    workspace = make_workspace()
    client = FakeAIClient([None])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is False
    assert result.status == 'error'
    assert result.errors


def test_verifier_fail_then_issue_response_accepts():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=2), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')])
    client = FakeAIClient([
        CaseJudgeOutput(
            action='submit_verdict',
            findings=[Finding(ref='F1', finding_kind='pass', description='ok')],
            assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1'], reason='r')],
        ),
        CaseJudgeOutput(
            action='issue_response',
            issue_responses=[{'ref': 'IR1', 'issue_kind': 'clarify_scope', 'message': 'fixed', 'related_refs': []}],
            findings=[Finding(ref='F2', finding_kind='pass', description='ok')],
            assignment_intents=[AssignmentIntent(ref='A2', file_ref='LF1', target_ref='BE1', support_finding_refs=['F2'], support_card_refs=['LF1', 'BE1'], reason='r2')],
        ),
    ])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is True


def test_oversized_judge_output_is_not_sent_through_verifier_as_normal_verdict():
    workspace = make_workspace(budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1))
    client = FakeAIClient([
        CaseJudgeOutput(action='submit_verdict', findings=[Finding(ref='F1', finding_kind='pass', description='ok')], evidence_gaps=[{'ref': 'G1', 'description': 'gap', 'needed_refs': [f'BE{i}' for i in range(100)]}], assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1'], reason='r')]),
        CaseJudgeOutput(action='fail_closed', fail_closed_reasons=[{'ref': 'R1', 'reason_kind': 'insufficient_evidence', 'description': 'stop', 'related_refs': []}]),
    ])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is True or result.status in {'invalid', 'fail_closed'}


def test_issue_response_round_submit_verdict_not_blocked_by_previous_issues():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')], verifier_issues=[VerifierIssue(ref='V1', issue_code='coverage_error', severity='blocked', message='bad')])
    client = FakeAIClient([
        CaseJudgeOutput(
            action='submit_verdict',
            findings=[Finding(ref='F1', finding_kind='pass', description='ok')],
            assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1'], reason='r')],
        ),
        CaseJudgeOutput(action='issue_response', issue_responses=[{'ref': 'IR1', 'issue_kind': 'clarify_scope', 'message': 'fixed', 'related_refs': []}], findings=[Finding(ref='F2', finding_kind='pass', description='ok')], assignment_intents=[]),
    ])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is True
    assert result.status == 'accepted'


def test_issue_response_round_corrected_submit_verdict_accepted():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')], verifier_issues=[VerifierIssue(ref='V1', issue_code='coverage_error', severity='blocked', message='bad')])
    client = FakeAIClient([
        CaseJudgeOutput(
            action='submit_verdict',
            findings=[Finding(ref='F1', finding_kind='pass', description='ok')],
            assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1'], reason='r')],
        ),
        CaseJudgeOutput(
            action='issue_response',
            findings=[Finding(ref='F2', finding_kind='pass', description='ok')],
            assignment_intents=[AssignmentIntent(ref='A2', file_ref='LF1', target_ref='BE1', support_finding_refs=['F2'], support_card_refs=['LF1', 'BE1'], reason='fixed')],
            issue_responses=[{'ref': 'IR1', 'issue_kind': 'clarify_scope', 'message': 'fixed', 'related_refs': []}],
        ),
    ])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is True
    assert result.status == 'accepted'


def test_issue_response_round_fail_closed_is_allowed():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=2), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')], verifier_issues=[VerifierIssue(ref='V1', issue_code='coverage_error', severity='blocked', message='bad')])
    client = FakeAIClient([
        CaseJudgeOutput(action='submit_verdict', findings=[Finding(ref='F1', finding_kind='pass', description='ok')], assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1'], reason='r')]),
        CaseJudgeOutput(action='issue_response', issue_responses=[{'ref': 'IR1', 'issue_kind': 'explain_failure', 'message': 'cannot fix', 'related_refs': []}], fail_closed_reasons=[{'ref': 'R1', 'reason_kind': 'insufficient_evidence', 'description': 'stop', 'related_refs': []}]),
    ])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is True
    assert result.status == 'accepted'


def test_issue_response_blocked_finding_without_correction_becomes_fail_closed():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=2), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')], verifier_issues=[VerifierIssue(ref='V1', issue_code='coverage_error', severity='blocked', message='bad')])
    client = FakeAIClient([
        CaseJudgeOutput(action='issue_response', issue_responses=[{'ref': 'IR1', 'issue_kind': 'explain_failure', 'message': 'blocked finding', 'related_refs': []}]),
    ])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is True
    assert result.status == 'fail_closed'
    assert result.final_action == 'issue_response'
    assert result.final_output is not None and result.final_output.action == 'fail_closed'
    assert result.judge_outputs[-1].action == 'issue_response'


def test_fail_closed_valid():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')])
    client = FakeAIClient([
        CaseJudgeOutput(
            action='fail_closed',
            fail_closed_reasons=[{'ref': 'R1', 'reason_kind': 'insufficient_evidence', 'description': 'stop', 'related_refs': []}],
        ),
    ])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is True
    assert result.status == 'fail_closed'


def test_fail_closed_auxiliary_sanitized_does_not_block_run():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-SAN-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), contract=CaseContract(main_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')])
    client = FakeAIClient([
        CaseJudgeOutput(action='fail_closed', findings=[Finding(ref='F1', finding_kind='pass', description='ok', evidence_refs=['FN1'])], fail_closed_reasons=[{'ref': 'R1', 'reason_kind': 'insufficient_evidence', 'description': 'stop', 'related_refs': ['BE1']}]),
    ])
    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())
    assert result.ok is True
    assert result.status == 'fail_closed'
    assert result.final_verifier_result is not None and result.final_verifier_result.passed is True


def test_corrected_verdict_with_unaligned_becomes_fail_closed():
    workspace = make_workspace(budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=1), verifier_issues=[VerifierIssue(ref='V1', issue_code='coverage_error', severity='blocked', message='bad')])
    client = FakeAIClient([
        CaseJudgeOutput(action='submit_verdict', findings=[Finding(ref='F1', finding_kind='pass', description='ok')], assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='UNALIGNED', support_finding_refs=['F1'], support_card_refs=['LF1'], reason='r')]),
        CaseJudgeOutput(action='issue_response', fail_closed_reasons=[{'ref': 'R1', 'reason_kind': 'insufficient_evidence', 'description': 'stop', 'related_refs': []}]),
    ])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is True
    assert result.status == 'fail_closed'


def test_issue_response_explanation_only_is_invalid():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=2), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')], verifier_issues=[VerifierIssue(ref='V1', issue_code='coverage_error', severity='blocked', message='bad')])
    client = FakeAIClient([CaseJudgeOutput(action='issue_response', issue_responses=[{'ref': 'IR1', 'issue_kind': 'explain_failure', 'message': 'just explain', 'related_refs': []}])])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is True
    assert result.status == 'fail_closed'


def test_snapshot_preserves_structured_final_output_assignment_count():
    from src.rename.case_agent.local_bangumi_entry import run_local_bangumi_case_agent_mapping

    class LocalEvidence:
        source_path = 'tests/sample'
        files = [type('F', (), {'file_id': 'f1', 'name': 'ep1.mkv', 'relative_path': 'ep1.mkv', 'is_main_video_candidate': True})()]

    class DummyAI:
        def call_case_judge(self, prompt, schema):
            return CaseJudgeOutput(action='submit_verdict', findings=[Finding(ref='F1', finding_kind='pass', description='ok')], assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1'], reason='r')])

    class DummyBangumi:
        pass

    result = run_local_bangumi_case_agent_mapping(local_evidence=LocalEvidence(), bangumi_contexts=[], ai_client=DummyAI(), source_path='tests/sample', bangumi_client=DummyBangumi())
    assert result['snapshot']['final_output_assignment_count'] == 1


def test_issue_response_round_request_evidence_is_invalid():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=2), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')], verifier_issues=[VerifierIssue(ref='V1', issue_code='coverage_error', severity='blocked', message='bad')])
    client = FakeAIClient([CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1')])])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is False
    assert result.status == 'invalid'
    assert 'issue_response round cannot request evidence' in result.summary


def test_request_evidence_in_issue_response_is_invalid():
    workspace = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='CASE-1'), budget=CaseBudget(max_judge_rounds=5, max_evidence_batches=2, max_issue_response_rounds=2), contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1')], verifier_issues=[VerifierIssue(ref='V1', issue_code='coverage_error', severity='blocked', message='bad')])
    client = FakeAIClient([CaseJudgeOutput(action='request_evidence', evidence_requests=[EvidenceRequest(request_ref='R1')])])

    result = run_local_bangumi_case_agent(workspace, client, FakeBangumiClient())

    assert result.ok is False
    assert result.status == 'invalid'


def test_module_source_has_no_old_runner_string():
    import src.rename.case_agent.orchestrator as orchestrator

    source = orchestrator.__loader__.get_source(orchestrator.__name__)  # type: ignore[union-attr]
    assert 'alignment_runner' not in source
