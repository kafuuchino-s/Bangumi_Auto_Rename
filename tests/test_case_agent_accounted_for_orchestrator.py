from __future__ import annotations

from src.rename.case_agent.models import (
    AssignmentIntent,
    BangumiItemCard,
    BangumiSpanCard,
    CaseBudget,
    CaseHeader,
    CaseContract,
    CaseJudgeOutput,
    FailClosedReason,
    Finding,
    LocalFileCard,
    LocalSpanCard,
    MappingDraft,
    MappingDraftPatch,
    MappingDraftRow,
    MappingDraftEditorOutput,
    CandidateComparison,
)
from src.rename.case_agent.orchestrator import _reopen_mapping_draft_issue_rows, _try_mapping_draft_editor_acceptance, run_local_bangumi_case_agent
from src.rename.case_agent.workspace import CaseEvidenceWorkspace
from src.rename.case_agent.models import VerifierIssue


class FakeAIClient:
    def __init__(self, editor_output):
        self.outputs = list(editor_output) if isinstance(editor_output, list) else [editor_output]
        self.prompts: list[str] = []

    def _next_output(self):
        if len(self.outputs) > 1:
            return self.outputs.pop(0)
        return self.outputs[0]

    def call_mapping_draft_editor(self, prompt, schema):
        self.prompts.append(prompt)
        editor_output = self._next_output()
        content = getattr(editor_output, 'output', editor_output)
        return type('Resp', (), {'content': content})()

    def _call_with_schema(self, prompt, schema):
        self.prompts.append(prompt)
        editor_output = self._next_output()
        content = getattr(editor_output, 'output', editor_output)
        return type('Resp', (), {'content': content})()

    def _call_openai_simple(self, *args, **kwargs):
        return self._next_output()

    def call_case_judge(self, prompt, schema):
        return CaseJudgeOutput(action='fail_closed', fail_closed_reasons=[FailClosedReason(ref='FR1', reason_kind='insufficient_evidence', description='fallback', related_refs=[])])


class FakeBangumiClient:
    pass


def _workspace(*, row: MappingDraftRow) -> CaseEvidenceWorkspace:
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-ACCOUNTED'),
        budget=CaseBudget(max_judge_rounds=2, max_evidence_batches=0, max_issue_response_rounds=0),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1', 'LF2'], visible_target_refs=['BE1', 'BE2']),
        local_files=[LocalFileCard(ref='LF1'), LocalFileCard(ref='LF2')],
        bangumi_items=[BangumiItemCard(ref='BE1'), BangumiItemCard(ref='BE2')],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='directory', file_ref_count=1, file_ref_samples=['LF1'], file_refs=['LF1']), LocalSpanCard(ref='LS2', span_scope='directory', file_ref_count=1, file_ref_samples=['LF2'], file_refs=['LF2'])],
        bangumi_span_cards=[
            BangumiSpanCard(ref='BS1', detail_equivalent=True, target_refs=['BE1'], target_ref_count=1, source_request_ref='REQ_TARGET_SPAN_LS1'),
            BangumiSpanCard(ref='BS2', detail_equivalent=True, target_refs=['BE2'], target_ref_count=1, source_request_ref='REQ_TARGET_SPAN_LS2'),
        ],
    )
    draft = MappingDraft(draft_ref='MD1', rows=[row], version=1)
    return workspace.with_mapping_draft(draft)


def test_editor_patches_mapped_plus_supplemental_accepts(monkeypatch):
    workspace = _workspace(row=MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', status='open'))
    workspace = workspace.with_mapping_draft(MappingDraft(draft_ref='MD1', rows=[
        MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', status='open'),
    ], version=1))
    editor_output = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(patches=[
        MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_span_ref='BS1', mapping_mode='span_by_index', reason='mapped'),
        MappingDraftPatch(op='mark_non_bangumi_or_supplemental', local_ref='LS1', reason_kind='other_supplemental', support_refs=['LF1'], reason='visible bonus extra'),
    ], findings=[Finding(ref='F1', finding_kind='pass', description='ok')]), 'error': '', 'raw_response': '{}'})()
    result = _try_mapping_draft_editor_acceptance(workspace, FakeAIClient(editor_output), [], [])

    assert result is not None and result.status == 'accepted'
    assert result.final_output is not None
    assert len(result.final_output.assignment_intents or []) == 1
    assert result.final_verifier_result is not None and result.final_verifier_result.passed is True


def test_mapping_draft_supplemental_row_can_be_accepted_with_accounting(monkeypatch):
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-SUPPLEMENTAL-ACCEPTED'),
        budget=CaseBudget(max_judge_rounds=2, max_evidence_batches=0, max_issue_response_rounds=0),
        contract=CaseContract(main_file_refs=['LF1', 'LF2'], allowed_file_refs=['LF1', 'LF2'], visible_target_refs=['BE1']),
        local_files=[
            LocalFileCard(ref='LF1', path='Show #01.mkv', is_main=True, file_kind='video'),
            LocalFileCard(ref='LF2', path='Show sample extra.mkv', is_main=True, file_kind='video'),
        ],
        bangumi_items=[BangumiItemCard(ref='BE1')],
        local_span_cards=[
            LocalSpanCard(ref='LS1', span_scope='directory', file_ref_count=1, file_ref_samples=['LF1'], file_refs=['LF1']),
            LocalSpanCard(ref='LS2', span_scope='residual', file_ref_count=1, file_ref_samples=['LF2'], file_refs=['LF2'], title_cues=['sample extra']),
        ],
        bangumi_span_cards=[BangumiSpanCard(ref='BS1', detail_equivalent=True, target_refs=['BE1'], target_ref_count=1, source_request_ref='REQ_TARGET_SPAN_LS1')],
    ).with_mapping_draft(MappingDraft(draft_ref='MD1', rows=[
        MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', status='open'),
        MappingDraftRow(row_ref='R2', local_ref='LS2', local_ref_kind='span', status='open'),
    ], version=1))
    editor_output = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(patches=[
        MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_span_ref='BS1', mapping_mode='span_by_index', support_refs=['LS1', 'BS1'], reason='mapped'),
        MappingDraftPatch(op='mark_non_bangumi_or_supplemental', local_ref='LS2', reason_kind='sample', support_refs=['LS2'], reason='visible sample extra row'),
    ], findings=[Finding(ref='F1', finding_kind='pass', description='ok')]), 'error': '', 'raw_response': '{}'})()

    result = _try_mapping_draft_editor_acceptance(workspace, FakeAIClient(editor_output), [], [])

    assert result is not None and result.status == 'accepted'
    assert result.summary == 'accepted_from_mapping_draft'
    assert result.final_output is not None
    assert len(result.final_output.assignment_intents or []) == 2
    assert any(intent.target_ref == 'UNALIGNED' and ':supplemental:' in intent.reason for intent in result.final_output.assignment_intents)
    assert result.final_verifier_result is not None and result.final_verifier_result.passed is True


def test_editor_patches_mapped_plus_needs_more_evidence_fail_closed(monkeypatch):
    workspace = _workspace(row=MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', status='open'))
    editor_output = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(patches=[
        MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_span_ref='BS1', mapping_mode='span_by_index', reason='mapped'),
        MappingDraftPatch(op='needs_more_evidence', local_ref='LS1', reason_kind='missing_target_detail', needed_evidence_type='target_detail', reason='need more'),
    ]), 'error': '', 'raw_response': '{}'})()
    result = _try_mapping_draft_editor_acceptance(workspace, FakeAIClient(editor_output), [], [])

    assert result is not None and result.status == 'fail_closed'
    assert result.status == 'fail_closed'
    assert result.summary == 'mapping_draft_accounting_unresolved'
    assert result.final_output is not None and result.final_output.action == 'fail_closed'
    assert any('unresolved_count=' in err for err in result.errors)
    assert result.final_output.fail_closed_reasons[0].ref == 'R1'
    assert 'reason_kind=missing_target_detail' in result.final_output.fail_closed_reasons[0].description
    assert 'LS1' in result.final_output.fail_closed_reasons[0].related_refs


def test_editor_comparison_winner_repairs_unresolved_patch(monkeypatch):
    workspace = _workspace(row=MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', status='open', candidate_target_refs=['BS1']))
    editor_output = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(
        patches=[MappingDraftPatch(op='needs_more_evidence', local_ref='LS1', reason_kind='ambiguous_candidate', reason='ambiguous')],
        candidate_comparisons=[CandidateComparison(ref='R1', left_ref='BS1', right_ref='BS2', winner_ref='BS1', reason='visible count and span match')],
        findings=[Finding(ref='F1', finding_kind='pass', description='ok')],
    ), 'error': '', 'raw_response': '{}'})()
    result = _try_mapping_draft_editor_acceptance(workspace, FakeAIClient(editor_output), [], [])

    assert result is not None and result.status == 'accepted'
    assert result.final_output is not None
    assert len(result.final_output.assignment_intents or []) == 1


def test_invalid_patch_fail_closes_with_verified_final_output(monkeypatch):
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-PATCH-REJECT'),
        budget=CaseBudget(max_judge_rounds=2, max_evidence_batches=0, max_issue_response_rounds=0),
        contract=CaseContract(main_file_refs=['LF1', 'LF2'], allowed_file_refs=['LF1', 'LF2'], visible_target_refs=['BE1']),
        local_files=[LocalFileCard(ref='LF1', is_main=True), LocalFileCard(ref='LF2', is_main=True)],
        bangumi_items=[BangumiItemCard(ref='BE1')],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='directory', file_ref_count=2, file_refs=['LF1', 'LF2'])],
        bangumi_span_cards=[BangumiSpanCard(ref='BS1', detail_equivalent=True, target_refs=['BE1'], target_ref_count=1)],
    ).with_mapping_draft(MappingDraft(draft_ref='MD1', rows=[
        MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', status='open'),
    ], version=1))
    editor_output = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(patches=[MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_span_ref='BES_MISSING', mapping_mode='span_by_index', reason='bad')]), 'error': '', 'raw_response': '{}'})()
    result = _try_mapping_draft_editor_acceptance(workspace, FakeAIClient(editor_output), [], [])

    assert result is not None and result.status == 'fail_closed'
    assert result.summary == 'mapping_draft_patch_rejected'
    assert result.final_verifier_result is not None and result.final_verifier_result.passed is True
    assert any(a.get('note') == 'mapping_draft_patch_issues' for a in result.final_workspace.judge_request_audits if isinstance(a, dict))


def test_structural_repair_adds_support_finding_when_editor_omits_findings(monkeypatch):
    workspace = _workspace(row=MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', status='open'))
    editor_output = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(patches=[MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_span_ref='BES_MISSING', mapping_mode='span_by_index', reason='bad')]), 'error': '', 'raw_response': '{}'})()
    result = _try_mapping_draft_editor_acceptance(workspace, FakeAIClient(editor_output), [], [])

    assert result is not None and result.status == 'accepted'
    assert result.final_verifier_result is not None and result.final_verifier_result.passed is True
    assert result.final_output is not None and result.final_output.findings[0].ref == 'F_MAP1'


def test_soft_patch_issue_salvages_open_row_to_accounting_unresolved(monkeypatch):
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-PATCH-SALVAGE'),
        budget=CaseBudget(max_judge_rounds=2, max_evidence_batches=0, max_issue_response_rounds=0),
        contract=CaseContract(main_file_refs=['LF1', 'LF2'], allowed_file_refs=['LF1', 'LF2'], visible_target_refs=['BE1']),
        local_files=[LocalFileCard(ref='LF1', is_main=True), LocalFileCard(ref='LF2', is_main=True)],
        bangumi_items=[BangumiItemCard(ref='BE1')],
        local_span_cards=[
            LocalSpanCard(ref='LS1', span_scope='directory', file_ref_count=1, file_refs=['LF1']),
            LocalSpanCard(ref='LS2', span_scope='directory', file_ref_count=1, file_refs=['LF2']),
        ],
        bangumi_span_cards=[BangumiSpanCard(ref='BS1', detail_equivalent=True, target_refs=['BE1'], target_ref_count=1)],
    ).with_mapping_draft(MappingDraft(draft_ref='MD1', rows=[
        MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', status='open'),
        MappingDraftRow(row_ref='R2', local_ref='LS2', local_ref_kind='span', status='open'),
    ], version=1))
    editor_output = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(patches=[
        MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_span_ref='BS1', mapping_mode='span_by_index', reason='mapped'),
        MappingDraftPatch(op='mark_unaligned_fail_closed', local_ref='LS2', reason_kind='not_allowlisted', support_refs=['LS2'], reason='bad reason kind'),
    ]), 'error': '', 'raw_response': '{}'})()

    result = _try_mapping_draft_editor_acceptance(workspace, FakeAIClient(editor_output), [], [])

    assert result is not None and result.status == 'fail_closed'
    assert result.summary == 'mapping_draft_accounting_unresolved'
    assert result.final_workspace.mapping_draft is not None
    rows = {row.local_ref: row for row in result.final_workspace.mapping_draft.rows}
    assert rows['LS1'].disposition == 'map_to_bangumi'
    assert rows['LS2'].disposition == 'needs_more_evidence'
    assert any(a.get('note') == 'mapping_draft_patch_issues' for a in result.final_workspace.judge_request_audits if isinstance(a, dict))
    assert any(a.get('note') == 'mapping_draft_patch_issue_salvaged_as_unresolved' for a in result.final_workspace.judge_request_audits if isinstance(a, dict))


def test_soft_patch_issue_routes_back_to_editor_once_before_salvage(monkeypatch):
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-PATCH-REPAIR'),
        budget=CaseBudget(max_judge_rounds=2, max_evidence_batches=0, max_issue_response_rounds=0),
        contract=CaseContract(main_file_refs=['LF1', 'LF2'], allowed_file_refs=['LF1', 'LF2'], visible_target_refs=['BE1', 'BE2']),
        local_files=[LocalFileCard(ref='LF1', is_main=True), LocalFileCard(ref='LF2', is_main=True)],
        bangumi_items=[BangumiItemCard(ref='BE1'), BangumiItemCard(ref='BE2')],
        local_span_cards=[
            LocalSpanCard(ref='LS1', span_scope='directory', file_ref_count=1, file_refs=['LF1']),
            LocalSpanCard(ref='LS2', span_scope='directory', file_ref_count=1, file_refs=['LF2']),
        ],
        bangumi_span_cards=[
            BangumiSpanCard(ref='BS1', detail_equivalent=True, target_refs=['BE1'], target_ref_count=1, source_request_ref='REQ_TARGET_SPAN_LS1'),
            BangumiSpanCard(ref='BS2', detail_equivalent=True, target_refs=['BE2'], target_ref_count=1, source_request_ref='REQ_TARGET_SPAN_LS2'),
        ],
    ).with_mapping_draft(MappingDraft(draft_ref='MD1', rows=[
        MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', status='open', candidate_target_refs=['BS1']),
        MappingDraftRow(row_ref='R2', local_ref='LS2', local_ref_kind='span', status='open', candidate_target_refs=['BS2']),
    ], version=1))
    first = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(patches=[
        MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_span_ref='BS1', mapping_mode='span_by_index', reason='mapped'),
        MappingDraftPatch(op='mark_unaligned_fail_closed', local_ref='LS2', reason_kind='not_allowlisted', support_refs=['LS2'], reason='schema issue'),
    ]), 'error': '', 'raw_response': '{}'})()
    second = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(patches=[
        MappingDraftPatch(op='map_to_bangumi', local_ref='LS2', target_span_ref='BS2', mapping_mode='span_by_index', reason='repair selected visible candidate'),
    ], findings=[Finding(ref='F1', finding_kind='pass', description='repaired patch issue')]), 'error': '', 'raw_response': '{}'})()
    client = FakeAIClient([first, second])

    result = _try_mapping_draft_editor_acceptance(workspace, client, [], [])

    assert result is not None and result.status == 'accepted'
    assert len(client.prompts) == 2
    assert 'verifier_issues' in client.prompts[1]
    assert any(a.get('note') == 'mapping_draft_patch_issue_repair_requested' for a in result.final_workspace.judge_request_audits if isinstance(a, dict))
    assert result.final_output is not None
    assert [item.target_ref for item in result.final_output.assignment_intents] == ['BE1', 'BE2']


def test_open_rows_after_final_remain_fail_closed_or_issue_response(monkeypatch):
    workspace = _workspace(row=MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', status='open'))
    result = _try_mapping_draft_editor_acceptance(workspace, FakeAIClient(type('EditorResult', (), {'ok': False, 'output': None, 'error': 'no-op', 'raw_response': '{}'})()), [], [])

    assert result is not None


def test_mapping_draft_editor_schema_failure_retries_then_accepts(monkeypatch):
    workspace = _workspace(row=MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', status='open', candidate_target_refs=['BES1']))
    bad = type('EditorResult', (), {'output': None})()
    good = type('EditorResult', (), {'output': MappingDraftEditorOutput(
        patches=[
            MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_span_ref='BES1', mapping_mode='span_by_index', reason='retry success')
        ],
        findings=[Finding(ref='F1', finding_kind='pass', description='retry success')],
    )})()
    client = FakeAIClient([bad, good])

    result = _try_mapping_draft_editor_acceptance(workspace, client, [], [])

    assert result is not None and result.status == 'accepted'
    assert len(client.prompts) == 2
    assert any(a.get('note') == 'mapping_draft_editor_retry_requested' for a in result.final_workspace.judge_request_audits if isinstance(a, dict))


def test_accepted_mapping_draft_drops_stale_failed_editor_self_checks(monkeypatch):
    workspace = _workspace(row=MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', status='open', candidate_target_refs=['BES1']))
    editor_output = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(
        patches=[
            MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_span_ref='BES1', mapping_mode='span_by_index', reason='mapped')
        ],
        findings=[Finding(ref='F1', finding_kind='pass', description='mapped')],
        self_checks=[{'ref': 'SC1', 'check_kind': 'consistency', 'passed': False}],
    ), 'error': '', 'raw_response': '{}'})()

    result = _try_mapping_draft_editor_acceptance(workspace, FakeAIClient(editor_output), [], [])

    assert result is not None and result.status == 'accepted'
    assert result.final_verifier_result is not None and result.final_verifier_result.passed
    assert result.final_output is not None
    assert result.final_output.self_checks == []


def test_mapping_draft_editor_schema_failure_after_retries_is_error(monkeypatch):
    workspace = _workspace(row=MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', status='open', candidate_target_refs=['BES1']))
    client = FakeAIClient([type('EditorResult', (), {'output': None})(), type('EditorResult', (), {'output': None})(), type('EditorResult', (), {'output': None})()])

    result = _try_mapping_draft_editor_acceptance(workspace, client, [], [])

    assert result is not None and result.status == 'error'
    assert result.summary == 'mapping_draft_editor_unavailable'
    assert len(client.prompts) == 3


def test_duplicate_target_repairs_when_unique_non_overlapping_span_solution_exists(monkeypatch):
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-DUP'),
        budget=CaseBudget(max_judge_rounds=2, max_evidence_batches=0, max_issue_response_rounds=0),
        contract=CaseContract(main_file_refs=['LF1', 'LF2'], allowed_file_refs=['LF1', 'LF2'], visible_target_refs=['BE1', 'BE2']),
        local_files=[LocalFileCard(ref='LF1', is_main=True), LocalFileCard(ref='LF2', is_main=True)],
        bangumi_items=[BangumiItemCard(ref='BE1'), BangumiItemCard(ref='BE2')],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='directory', file_ref_count=1, file_ref_samples=['LF1'], file_refs=['LF1']), LocalSpanCard(ref='LS2', span_scope='directory', file_ref_count=1, file_ref_samples=['LF2'], file_refs=['LF2'])],
        bangumi_span_cards=[
            BangumiSpanCard(ref='BS1', detail_equivalent=True, target_refs=['BE1'], target_ref_count=1, source_request_ref='REQ_TARGET_SPAN_LS1'),
            BangumiSpanCard(ref='BS2', detail_equivalent=True, target_refs=['BE2'], target_ref_count=1, source_request_ref='REQ_TARGET_SPAN_LS2'),
        ],
    ).with_mapping_draft(MappingDraft(draft_ref='MD1', rows=[
        MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', status='open', candidate_target_refs=['BS1']),
        MappingDraftRow(row_ref='R2', local_ref='LS2', local_ref_kind='span', status='open', candidate_target_refs=['BS1', 'BS2']),
    ], version=1))
    editor_output = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(patches=[
        MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_span_ref='BS1', mapping_mode='span_by_index', reason='mapped'),
        MappingDraftPatch(op='map_to_bangumi', local_ref='LS2', target_span_ref='BS1', mapping_mode='span_by_index', reason='dup'),
    ], findings=[Finding(ref='F1', finding_kind='pass', description='ok')]), 'error': '', 'raw_response': '{}'})()
    result = _try_mapping_draft_editor_acceptance(workspace, FakeAIClient(editor_output), [], [])

    assert result is not None and result.status == 'accepted'
    assert result.final_output is not None
    assert len(result.final_output.assignment_intents or []) == 2
    assert [item.target_ref for item in result.final_output.assignment_intents] == ['BE1', 'BE2']
    assert any(a.get('note') == 'mapping_draft_accounting_structural_repair' for a in result.final_workspace.judge_request_audits if isinstance(a, dict))


def test_duplicate_target_fail_closes_without_unique_repair(monkeypatch):
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-DUP'),
        budget=CaseBudget(max_judge_rounds=2, max_evidence_batches=0, max_issue_response_rounds=0),
        local_files=[LocalFileCard(ref='LF1'), LocalFileCard(ref='LF2')],
        bangumi_items=[BangumiItemCard(ref='BE1')],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='directory', file_ref_count=1, file_ref_samples=['LF1'], file_refs=['LF1']), LocalSpanCard(ref='LS2', span_scope='directory', file_ref_count=1, file_ref_samples=['LF2'], file_refs=['LF2'])],
        bangumi_span_cards=[BangumiSpanCard(ref='BS1', detail_equivalent=True, target_refs=['BE1'], target_ref_count=1)],
    ).with_mapping_draft(MappingDraft(draft_ref='MD1', rows=[
        MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', status='open'),
        MappingDraftRow(row_ref='R2', local_ref='LS2', local_ref_kind='span', status='open'),
    ], version=1))
    editor_output = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(patches=[
        MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_span_ref='BS1', mapping_mode='span_by_index', reason='mapped'),
        MappingDraftPatch(op='map_to_bangumi', local_ref='LS2', target_span_ref='BS1', mapping_mode='span_by_index', reason='dup'),
    ]), 'error': '', 'raw_response': '{}'})()
    result = _try_mapping_draft_editor_acceptance(workspace, FakeAIClient(editor_output), [], [])

    assert result is not None and result.status == 'fail_closed'
    assert result.summary == 'mapping_draft_target_conflict'


def test_duplicate_special_targets_are_routed_back_to_editor_once(monkeypatch):
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-DUP-SPECIAL'),
        budget=CaseBudget(max_judge_rounds=2, max_evidence_batches=0, max_issue_response_rounds=0),
        contract=CaseContract(main_file_refs=['LF1', 'LF2'], allowed_file_refs=['LF1', 'LF2'], visible_target_refs=['BE1', 'BE2']),
        local_files=[
            LocalFileCard(ref='LF1', path='Special A.mkv', is_main=True, file_kind='video'),
            LocalFileCard(ref='LF2', path='Special B.mkv', is_main=True, file_kind='video'),
        ],
        bangumi_items=[
            BangumiItemCard(ref='BE1', item_kind='special', subject_ref='BS1', title='Special A', source_form_hint='special'),
            BangumiItemCard(ref='BE2', item_kind='special', subject_ref='BS1', title='Special B', source_form_hint='special'),
        ],
        local_span_cards=[
            LocalSpanCard(ref='LS1', span_scope='residual', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1']),
            LocalSpanCard(ref='LS2', span_scope='residual', file_refs=['LF2'], file_ref_count=1, file_ref_samples=['LF2']),
        ],
    ).with_seen_detail_refs(['BE1', 'BE2']).with_mapping_draft(MappingDraft(draft_ref='MD1', rows=[
        MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', status='open', candidate_target_refs=['BE1', 'BE2']),
        MappingDraftRow(row_ref='R2', local_ref='LS2', local_ref_kind='span', status='open', candidate_target_refs=['BE1', 'BE2']),
    ], version=1))
    first = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(patches=[
        MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_ref='BE1', mapping_mode='explicit', support_refs=['LS1', 'BE1'], reason='mapped'),
        MappingDraftPatch(op='map_to_bangumi', local_ref='LS2', target_ref='BE1', mapping_mode='explicit', support_refs=['LS2', 'BE1'], reason='dup'),
    ], candidate_comparisons=[
        CandidateComparison(ref='R1', left_ref='BE1', right_ref='BE2', winner_ref='BE1', reason='first row winner'),
        CandidateComparison(ref='R2', left_ref='BE1', right_ref='BE2', winner_ref='BE1', reason='intentionally duplicated winner'),
    ]), 'error': '', 'raw_response': '{}'})()
    second = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(patches=[
        MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_ref='BE1', mapping_mode='explicit', support_refs=['LS1', 'BE1'], reason='repair keeps first singleton'),
        MappingDraftPatch(op='map_to_bangumi', local_ref='LS2', target_ref='BE2', mapping_mode='explicit', support_refs=['LS2', 'BE2'], reason='repair duplicate'),
    ], candidate_comparisons=[
        CandidateComparison(ref='R1', left_ref='BE1', right_ref='BE2', winner_ref='BE1', reason='Special A row matches BE1'),
        CandidateComparison(ref='R2', left_ref='BE1', right_ref='BE2', winner_ref='BE2', reason='Special B row matches BE2'),
    ], findings=[Finding(ref='F1', finding_kind='pass', description='repaired duplicate')]), 'error': '', 'raw_response': '{}'})()
    client = FakeAIClient([first, second])

    result = _try_mapping_draft_editor_acceptance(workspace, client, [], [])

    assert result is not None and result.status == 'accepted'
    assert len(client.prompts) == 2
    assert 'verifier_issues' in client.prompts[1]
    assert any(a.get('note') == 'mapping_draft_duplicate_target_repair_requested' for a in result.final_workspace.judge_request_audits if isinstance(a, dict))
    assert result.final_output is not None
    assert [item.target_ref for item in result.final_output.assignment_intents] == ['BE1', 'BE2']


def test_duplicate_target_repair_reopens_target_conflict_set(monkeypatch):
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-DUP-REOPEN'),
        budget=CaseBudget(max_judge_rounds=2, max_evidence_batches=0, max_issue_response_rounds=0),
        contract=CaseContract(main_file_refs=['LF1', 'LF2'], allowed_file_refs=['LF1', 'LF2'], visible_target_refs=['BE1', 'BE2']),
        local_files=[
            LocalFileCard(ref='LF1', path='Special A.mkv', is_main=True, file_kind='video'),
            LocalFileCard(ref='LF2', path='Special B.mkv', is_main=True, file_kind='video'),
        ],
        bangumi_items=[
            BangumiItemCard(ref='BE1', item_kind='special', subject_ref='BS1', title='Special A', source_form_hint='special'),
            BangumiItemCard(ref='BE2', item_kind='special', subject_ref='BS1', title='Special B', source_form_hint='special'),
        ],
        local_span_cards=[
            LocalSpanCard(ref='LS1', span_scope='residual', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1']),
            LocalSpanCard(ref='LS2', span_scope='residual', file_refs=['LF2'], file_ref_count=1, file_ref_samples=['LF2']),
        ],
    ).with_mapping_draft(MappingDraft(draft_ref='MD1', rows=[
        MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', status='proposed', disposition='map_to_bangumi', selected_target_ref='BE1', selected_target_kind='item', mapping_mode='explicit', support_refs=['LS1', 'BE1']),
        MappingDraftRow(row_ref='R2', local_ref='LS2', local_ref_kind='span', status='proposed', disposition='map_to_bangumi', selected_target_ref='BE1', selected_target_kind='item', mapping_mode='explicit', support_refs=['LS2', 'BE1']),
    ], version=1))

    repaired = _reopen_mapping_draft_issue_rows(workspace, [VerifierIssue(ref='R2', issue_code='duplicate_target', severity='blocked', message='duplicate mapped target')])

    assert repaired.mapping_draft is not None
    rows = {row.row_ref: row for row in repaired.mapping_draft.rows}
    assert rows['R1'].status == 'open'
    assert rows['R2'].status == 'open'
    assert rows['R1'].selected_target_ref == ''
    assert rows['R2'].selected_target_ref == ''


def test_editor_patches_are_limited_to_open_rows(monkeypatch):
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-OPEN-PATCH-GATE'),
        budget=CaseBudget(max_judge_rounds=2, max_evidence_batches=0, max_issue_response_rounds=0),
        contract=CaseContract(main_file_refs=['LF1', 'LF2'], allowed_file_refs=['LF1', 'LF2'], visible_target_refs=['BE1', 'BE2']),
        local_files=[
            LocalFileCard(ref='LF1', path='Episode 1.mkv', is_main=True, file_kind='video'),
            LocalFileCard(ref='LF2', path='Episode 2.mkv', is_main=True, file_kind='video'),
        ],
        bangumi_items=[BangumiItemCard(ref='BE1'), BangumiItemCard(ref='BE2')],
        local_span_cards=[
            LocalSpanCard(ref='LS1', span_scope='directory', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1']),
            LocalSpanCard(ref='LS2', span_scope='directory', file_refs=['LF2'], file_ref_count=1, file_ref_samples=['LF2']),
        ],
        bangumi_span_cards=[
            BangumiSpanCard(ref='BES1', detail_equivalent=True, target_refs=['BE1'], target_ref_count=1),
            BangumiSpanCard(ref='BES2', detail_equivalent=True, target_refs=['BE2'], target_ref_count=1),
        ],
    ).with_mapping_draft(MappingDraft(draft_ref='MD1', rows=[
        MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', status='proposed', disposition='map_to_bangumi', selected_target_ref='BES1', selected_target_kind='span', mapping_mode='span_by_index', support_refs=['LS1', 'BES1']),
        MappingDraftRow(row_ref='R2', local_ref='LS2', local_ref_kind='span', status='open', candidate_target_refs=['BES2']),
    ], version=1))
    editor_output = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(patches=[
        MappingDraftPatch(op='needs_more_evidence', local_ref='LS1', reason_kind='ambiguous_candidate', reason='stable row should not be rewritten'),
        MappingDraftPatch(op='map_to_bangumi', local_ref='LS2', target_span_ref='BES2', mapping_mode='span_by_index', support_refs=['LS2', 'BES2'], reason='open row mapped'),
    ], findings=[Finding(ref='F1', finding_kind='pass', description='open row mapped')]), 'error': '', 'raw_response': '{}'})()

    result = _try_mapping_draft_editor_acceptance(workspace, FakeAIClient(editor_output), [], [])

    assert result is not None and result.status == 'accepted'
    rows = {row.local_ref: row for row in result.final_workspace.mapping_draft.rows}
    assert rows['LS1'].disposition == 'map_to_bangumi'
    assert rows['LS1'].selected_target_ref == 'BES1'
    assert rows['LS2'].selected_target_ref == 'BES2'
    assert any(a.get('note') == 'mapping_draft_editor_non_open_row_patches_ignored' for a in result.final_workspace.judge_request_audits if isinstance(a, dict))
    assert result.final_output is not None
    assert [item.target_ref for item in result.final_output.assignment_intents] == ['BE1', 'BE2']


def test_span_target_overlap_can_be_structurally_repaired(monkeypatch):
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-SPAN-EXPAND-DUP'),
        budget=CaseBudget(max_judge_rounds=2, max_evidence_batches=0, max_issue_response_rounds=0),
        contract=CaseContract(main_file_refs=['LF1', 'LF2'], allowed_file_refs=['LF1', 'LF2'], visible_target_refs=['BE1', 'BE2', 'BE3']),
        local_files=[LocalFileCard(ref='LF1', is_main=True), LocalFileCard(ref='LF2', is_main=True)],
        bangumi_items=[BangumiItemCard(ref='BE1'), BangumiItemCard(ref='BE2'), BangumiItemCard(ref='BE3')],
        local_span_cards=[
            LocalSpanCard(ref='LS1', span_scope='directory', file_ref_count=1, file_ref_samples=['LF1'], file_refs=['LF1']),
            LocalSpanCard(ref='LS2', span_scope='directory', file_ref_count=1, file_ref_samples=['LF2'], file_refs=['LF2']),
        ],
        bangumi_span_cards=[
            BangumiSpanCard(ref='BES1', detail_equivalent=True, target_refs=['BE1'], target_ref_count=1),
            BangumiSpanCard(ref='BES2', detail_equivalent=True, target_refs=['BE1'], target_ref_count=1),
            BangumiSpanCard(ref='BES3', detail_equivalent=True, target_refs=['BE3'], target_ref_count=1),
        ],
    ).with_mapping_draft(MappingDraft(draft_ref='MD1', rows=[
        MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', status='open', candidate_target_refs=['BES1']),
        MappingDraftRow(row_ref='R2', local_ref='LS2', local_ref_kind='span', status='open', candidate_target_refs=['BES2', 'BES3']),
    ], version=1))
    first = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(patches=[
        MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_span_ref='BES1', mapping_mode='span_by_index', support_refs=['LS1', 'BES1'], reason='mapped'),
        MappingDraftPatch(op='map_to_bangumi', local_ref='LS2', target_span_ref='BES2', mapping_mode='span_by_index', support_refs=['LS2', 'BES2'], reason='overlap'),
    ]), 'error': '', 'raw_response': '{}'})()
    second = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(patches=[
        MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_span_ref='BES1', mapping_mode='span_by_index', support_refs=['LS1', 'BES1'], reason='kept'),
        MappingDraftPatch(op='map_to_bangumi', local_ref='LS2', target_span_ref='BES3', mapping_mode='span_by_index', support_refs=['LS2', 'BES3'], reason='repaired overlap'),
    ]), 'error': '', 'raw_response': '{}'})()
    client = FakeAIClient([first, second])

    result = _try_mapping_draft_editor_acceptance(workspace, client, [], [])

    assert result is not None and result.status == 'accepted'
    assert len(client.prompts) == 1
    assert any(a.get('note') == 'mapping_draft_accounting_structural_repair' for a in result.final_workspace.judge_request_audits if isinstance(a, dict))
    assert [item.target_ref for item in result.final_output.assignment_intents] == ['BE1', 'BE3']


def test_target_overlap_with_travel_feature_is_structurally_accounted_as_supplemental(monkeypatch):
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-SPAN-EXTRA-OVERLAP'),
        budget=CaseBudget(max_judge_rounds=2, max_evidence_batches=0, max_issue_response_rounds=0),
        contract=CaseContract(main_file_refs=['LF1', 'LF2'], allowed_file_refs=['LF1', 'LF2'], visible_target_refs=['BE1']),
        local_files=[
            LocalFileCard(ref='LF1', path='Main #01.mkv', is_main=True, file_kind='video'),
            LocalFileCard(ref='LF2', path='Cast travel feature #01.mkv', is_main=True, file_kind='video'),
        ],
        bangumi_items=[BangumiItemCard(ref='BE1')],
        local_span_cards=[
            LocalSpanCard(ref='LS_MAIN', span_scope='directory', file_ref_count=1, file_ref_samples=['LF1'], file_refs=['LF1'], title_cues=['Main #01']),
            LocalSpanCard(ref='LS_TRAVEL', span_scope='token_segment', file_ref_count=1, file_ref_samples=['LF2'], file_refs=['LF2'], title_cues=['cast travel feature #01']),
        ],
        bangumi_span_cards=[
            BangumiSpanCard(ref='BES_MAIN', detail_equivalent=True, target_refs=['BE1'], target_ref_count=1),
            BangumiSpanCard(ref='BES_TRAVEL_BAD', detail_equivalent=True, target_refs=['BE1'], target_ref_count=1, source_request_ref='REQ_TARGET_SPAN_LS_TRAVEL'),
        ],
    ).with_mapping_draft(MappingDraft(draft_ref='MD1', rows=[
        MappingDraftRow(row_ref='R1', local_ref='LS_MAIN', local_ref_kind='span', status='open', candidate_target_refs=['BES_MAIN']),
        MappingDraftRow(row_ref='R2', local_ref='LS_TRAVEL', local_ref_kind='span', status='open', candidate_target_refs=['BES_TRAVEL_BAD']),
    ], version=1))
    editor_output = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(patches=[
        MappingDraftPatch(op='map_to_bangumi', local_ref='LS_MAIN', target_span_ref='BES_MAIN', mapping_mode='span_by_index', support_refs=['LS_MAIN', 'BES_MAIN'], reason='main mapped'),
        MappingDraftPatch(op='map_to_bangumi', local_ref='LS_TRAVEL', target_span_ref='BES_TRAVEL_BAD', mapping_mode='span_by_index', support_refs=['LS_TRAVEL', 'BES_TRAVEL_BAD'], reason='bad overlap'),
    ]), 'error': '', 'raw_response': '{}'})()

    result = _try_mapping_draft_editor_acceptance(workspace, FakeAIClient(editor_output), [], [])

    assert result is not None and result.status == 'accepted'
    rows = {row.local_ref: row for row in result.final_workspace.mapping_draft.rows}
    assert rows['LS_MAIN'].disposition == 'map_to_bangumi'
    assert rows['LS_TRAVEL'].disposition == 'non_bangumi_or_supplemental'
    assert rows['LS_TRAVEL'].reason_kind == 'making_of'
    assert any(a.get('note') == 'mapping_draft_accounting_structural_repair' for a in result.final_workspace.judge_request_audits if isinstance(a, dict))
    assert result.final_output is not None
    assert result.final_output.action == 'submit_verdict'
    assert any(intent.target_ref == 'UNALIGNED' and ':supplemental:' in intent.reason for intent in result.final_output.assignment_intents)
    assert result.final_verifier_result is not None and result.final_verifier_result.passed is True


def test_unresolved_pv_and_creditless_rows_are_structurally_accounted_as_supplemental(monkeypatch):
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-UNRESOLVED-SUPPLEMENTAL'),
        budget=CaseBudget(max_judge_rounds=2, max_evidence_batches=0, max_issue_response_rounds=0),
        contract=CaseContract(main_file_refs=['LF1', 'LF2'], allowed_file_refs=['LF1', 'LF2'], visible_target_refs=[]),
        local_files=[
            LocalFileCard(ref='LF1', path='Show PV CM collection.mkv', is_main=True, file_kind='video'),
            LocalFileCard(ref='LF2', path='Show creditless OP.mkv', is_main=True, file_kind='video'),
        ],
        local_span_cards=[
            LocalSpanCard(ref='LS_PV', span_scope='residual', file_ref_count=1, file_ref_samples=['LF1'], file_refs=['LF1'], title_cues=['PV CM collection']),
            LocalSpanCard(ref='LS_OP', span_scope='residual', file_ref_count=1, file_ref_samples=['LF2'], file_refs=['LF2'], title_cues=['creditless OP']),
        ],
        bangumi_span_cards=[BangumiSpanCard(ref='BES_DUMMY', detail_equivalent=True, target_refs=['BE1'], target_ref_count=1)],
    ).with_mapping_draft(MappingDraft(draft_ref='MD1', rows=[
        MappingDraftRow(row_ref='R1', local_ref='LS_PV', local_ref_kind='span', status='open'),
        MappingDraftRow(row_ref='R2', local_ref='LS_OP', local_ref_kind='span', status='open'),
    ], version=1))
    editor_output = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(patches=[
        MappingDraftPatch(op='needs_more_evidence', local_ref='LS_PV', reason_kind='ambiguous_candidate', reason='no matching item'),
        MappingDraftPatch(op='needs_more_evidence', local_ref='LS_OP', reason_kind='ambiguous_candidate', reason='no matching item'),
    ]), 'error': '', 'raw_response': '{}'})()

    result = _try_mapping_draft_editor_acceptance(workspace, FakeAIClient(editor_output), [], [])

    assert result is not None and result.status == 'accepted'
    rows = {row.local_ref: row for row in result.final_workspace.mapping_draft.rows}
    assert rows['LS_PV'].disposition == 'non_bangumi_or_supplemental'
    assert rows['LS_PV'].reason_kind == 'pv_cm'
    assert rows['LS_OP'].disposition == 'non_bangumi_or_supplemental'
    assert rows['LS_OP'].reason_kind == 'creditless_op_ed'
    assert result.final_output is not None
    assert result.final_output.action == 'submit_verdict'
    assert len(result.final_output.assignment_intents or []) == 2
    assert all(intent.target_ref == 'UNALIGNED' and ':supplemental:' in intent.reason for intent in result.final_output.assignment_intents)
    assert result.final_verifier_result is not None and result.final_verifier_result.passed is True


def test_unresolved_open_span_with_unique_non_overlapping_candidate_is_structurally_completed(monkeypatch):
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-OPEN-SPAN-COMPLETE'),
        budget=CaseBudget(max_judge_rounds=2, max_evidence_batches=0, max_issue_response_rounds=0),
        contract=CaseContract(main_file_refs=['LF1', 'LF2'], allowed_file_refs=['LF1', 'LF2'], visible_target_refs=['BE1', 'BE2']),
        local_files=[LocalFileCard(ref='LF1', is_main=True), LocalFileCard(ref='LF2', is_main=True)],
        bangumi_items=[BangumiItemCard(ref='BE1'), BangumiItemCard(ref='BE2')],
        local_span_cards=[
            LocalSpanCard(ref='LS1', span_scope='directory', file_ref_count=1, file_ref_samples=['LF1'], file_refs=['LF1']),
            LocalSpanCard(ref='LS2', span_scope='directory', file_ref_count=1, file_ref_samples=['LF2'], file_refs=['LF2']),
        ],
        bangumi_span_cards=[
            BangumiSpanCard(ref='BES1', detail_equivalent=True, target_refs=['BE1'], target_ref_count=1),
            BangumiSpanCard(ref='BES2_OVERLAP', detail_equivalent=True, target_refs=['BE1'], target_ref_count=1, source_request_ref='REQ_TARGET_SPAN_LS2'),
            BangumiSpanCard(ref='BES2_OK', detail_equivalent=True, target_refs=['BE2'], target_ref_count=1, source_request_ref='REQ_TARGET_SPAN_LS2'),
        ],
    ).with_mapping_draft(MappingDraft(draft_ref='MD1', rows=[
        MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', status='proposed', disposition='map_to_bangumi', selected_target_ref='BES1', selected_target_kind='span', mapping_mode='span_by_index', support_refs=['LS1', 'BES1']),
        MappingDraftRow(row_ref='R2', local_ref='LS2', local_ref_kind='span', status='open', disposition='open', candidate_target_refs=['BES2_OVERLAP', 'BES2_OK']),
    ], version=1))
    editor_output = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(patches=[
        MappingDraftPatch(op='needs_more_evidence', local_ref='LS2', reason_kind='ambiguous_candidate', reason='still unsure'),
    ]), 'error': '', 'raw_response': '{}'})()

    result = _try_mapping_draft_editor_acceptance(workspace, FakeAIClient(editor_output), [], [])

    assert result is not None and result.status == 'accepted'
    assert any(a.get('note') == 'mapping_draft_accounting_structural_repair' for a in result.final_workspace.judge_request_audits if isinstance(a, dict))
    assert result.final_output is not None


def test_regular_multi_file_span_is_not_structurally_supplemental_accepted(monkeypatch):
    main_refs = [f'LF{i}' for i in range(1, 13)]
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-REGULAR-NOT-SUPP'),
        budget=CaseBudget(max_judge_rounds=2, max_evidence_batches=0, max_issue_response_rounds=0),
        contract=CaseContract(main_file_refs=main_refs, allowed_file_refs=main_refs, visible_target_refs=['BE99']),
        local_files=[
            LocalFileCard(ref=ref, path=f'Show #{index:02d}.mkv', is_main=True, file_kind='video')
            for index, ref in enumerate(main_refs, 1)
        ],
        bangumi_items=[BangumiItemCard(ref='BE99')],
        local_span_cards=[
            LocalSpanCard(
                ref='LS_REGULAR',
                span_scope='token_segment',
                file_ref_count=len(main_refs),
                file_ref_samples=['LF1', 'LF2', 'LF12'],
                file_refs=main_refs,
                title_cues=['Show regular episodes #01-#12'],
                ordering_basis='episode_token_order',
                episode_token_start=1,
                episode_token_end=12,
                episode_token_count=12,
            )
        ],
        bangumi_span_cards=[BangumiSpanCard(ref='BES_BAD', detail_equivalent=True, target_refs=['BE99'], target_ref_count=1)],
    ).with_mapping_draft(MappingDraft(draft_ref='MD1', rows=[
        MappingDraftRow(row_ref='R1', local_ref='LS_REGULAR', local_ref_kind='span', status='open', candidate_target_refs=['BES_BAD']),
    ], version=1))
    first = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(patches=[
        MappingDraftPatch(op='mark_non_bangumi_or_supplemental', local_ref='LS_REGULAR', reason_kind='other_supplemental', support_refs=['LS_REGULAR'], reason='extra'),
    ]), 'error': '', 'raw_response': '{}'})()
    second = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(patches=[
        MappingDraftPatch(op='needs_more_evidence', local_ref='LS_REGULAR', reason_kind='ambiguous_candidate', reason='cannot classify the regular span as supplemental'),
    ]), 'error': '', 'raw_response': '{}'})()

    result = _try_mapping_draft_editor_acceptance(workspace, FakeAIClient([first, second]), [], [])

    assert result is not None and result.status == 'fail_closed'
    assert result.final_output is not None and result.final_output.action == 'fail_closed'
    assert not any(
        assignment.target_ref == 'UNALIGNED'
        for assignment in list(result.final_output.assignment_intents or [])
    )


def test_final_special_missing_comparison_repair_runs_before_structural_supplemental(monkeypatch):
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-SPECIAL-FINAL-REPAIR-FIRST'),
        budget=CaseBudget(max_judge_rounds=2, max_evidence_batches=0, max_issue_response_rounds=0),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE_BAD', 'BE_GOOD']),
        local_files=[LocalFileCard(ref='LF1', path='Mushishi Tokubetsu Hen Suzu no Shizuku.mkv', is_main=True, file_kind='video')],
        bangumi_items=[
            BangumiItemCard(ref='BE_BAD', item_kind='special', subject_ref='BS1', title='Wrong Special'),
            BangumiItemCard(ref='BE_GOOD', item_kind='special', subject_ref='BS1', title='Suzu no Shizuku'),
        ],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='residual', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1'])],
    ).with_seen_detail_refs(['BE_BAD', 'BE_GOOD']).with_mapping_draft(MappingDraft(draft_ref='MD1', rows=[
        MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', status='open', candidate_target_refs=['BE_BAD', 'BE_GOOD']),
    ], version=1))
    first = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(
        patches=[MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_ref='BE_GOOD', mapping_mode='explicit', support_refs=['LS1', 'BE_GOOD'], reason='selected without comparison')],
        findings=[Finding(ref='F1', finding_kind='pass', description='selected singleton')],
    ), 'error': '', 'raw_response': '{}'})()
    second = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(
        patches=[MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_ref='BE_GOOD', mapping_mode='explicit', support_refs=['LS1', 'BE_GOOD'], reason='selected with comparison')],
        candidate_comparisons=[CandidateComparison(ref='R1', left_ref='BE_BAD', right_ref='BE_GOOD', winner_ref='BE_GOOD', reason='visible title matches selected special')],
        findings=[Finding(ref='F1', finding_kind='pass', description='selected singleton')],
    ), 'error': '', 'raw_response': '{}'})()
    client = FakeAIClient([first, second])

    result = _try_mapping_draft_editor_acceptance(workspace, client, [], [])

    assert result is not None and result.status == 'accepted'
    assert len(client.prompts) == 2
    repair_notes = {
        a.get('note')
        for a in result.final_workspace.judge_request_audits
        if isinstance(a, dict)
    }
    assert repair_notes & {'mapping_draft_comparison_conflict_repair_requested', 'mapping_draft_final_special_comparison_repair_requested'}
    assert not any(a.get('note') == 'mapping_draft_final_special_comparison_structural_repair' for a in result.final_workspace.judge_request_audits if isinstance(a, dict))
    assert result.final_output is not None
    assert result.final_output.assignment_intents[0].target_ref == 'BE_GOOD'


def test_comparison_patch_conflict_routes_back_to_editor_once(monkeypatch):
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-COMPARISON-CONFLICT'),
        budget=CaseBudget(max_judge_rounds=2, max_evidence_batches=0, max_issue_response_rounds=0),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1', 'BE2']),
        local_files=[LocalFileCard(ref='LF1', path='Special.mkv', is_main=True, file_kind='video')],
        bangumi_items=[
            BangumiItemCard(ref='BE1', item_kind='special', subject_ref='BS1', title='Special One', source_form_hint='special'),
            BangumiItemCard(ref='BE2', item_kind='special', subject_ref='BS1', title='Special Two', source_form_hint='special'),
        ],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='residual', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1'])],
    ).with_seen_detail_refs(['BE1', 'BE2']).with_mapping_draft(MappingDraft(draft_ref='MD1', rows=[
        MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', status='open', candidate_target_refs=['BE1', 'BE2']),
    ], version=1))
    first = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(
        patches=[MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_ref='BE1', mapping_mode='explicit', support_refs=['LS1', 'BE1'], reason='patch conflicts with comparison')],
        candidate_comparisons=[CandidateComparison(ref='R1', left_ref='BE1', right_ref='BE2', winner_ref='BE2', reason='visible comparison winner')],
    ), 'error': '', 'raw_response': '{}'})()
    second = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(
        patches=[MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_ref='BE2', mapping_mode='explicit', support_refs=['LS1', 'BE2'], reason='repair follows comparison')],
        candidate_comparisons=[CandidateComparison(ref='R1', left_ref='BE1', right_ref='BE2', winner_ref='BE2', reason='visible comparison winner')],
        findings=[Finding(ref='F1', finding_kind='pass', description='comparison repaired')],
    ), 'error': '', 'raw_response': '{}'})()
    client = FakeAIClient([first, second])

    result = _try_mapping_draft_editor_acceptance(workspace, client, [], [])

    assert result is not None and result.status == 'accepted'
    assert len(client.prompts) == 2
    assert any(a.get('note') == 'mapping_draft_comparison_conflict_repair_requested' for a in result.final_workspace.judge_request_audits if isinstance(a, dict))
    assert result.final_output is not None
    assert result.final_output.assignment_intents[0].target_ref == 'BE2'


def test_multi_candidate_singleton_mapping_requires_winner_comparison(monkeypatch):
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-SINGLETON-COMPARISON-REQUIRED'),
        budget=CaseBudget(max_judge_rounds=2, max_evidence_batches=0, max_issue_response_rounds=0),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1', 'BE2']),
        local_files=[LocalFileCard(ref='LF1', path='Special B.mkv', is_main=True, file_kind='video')],
        bangumi_items=[
            BangumiItemCard(ref='BE1', item_kind='special', subject_ref='BS1', title='Special A', source_form_hint='special'),
            BangumiItemCard(ref='BE2', item_kind='special', subject_ref='BS1', title='Special B', source_form_hint='special'),
        ],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='residual', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1'])],
    ).with_seen_detail_refs(['BE1', 'BE2']).with_mapping_draft(MappingDraft(draft_ref='MD1', rows=[
        MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', status='open', candidate_target_refs=['BE1', 'BE2']),
    ], version=1))
    first = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(
        patches=[MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_ref='BE2', mapping_mode='explicit', support_refs=['LS1', 'BE2'], reason='selected without comparison')],
        findings=[Finding(ref='F1', finding_kind='pass', description='selected singleton')],
    ), 'error': '', 'raw_response': '{}'})()
    second = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(
        patches=[MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_ref='BE2', mapping_mode='explicit', support_refs=['LS1', 'BE2'], reason='selected with comparison')],
        candidate_comparisons=[CandidateComparison(ref='R1', left_ref='BE1', right_ref='BE2', winner_ref='BE2', reason='visible row comparison')],
        findings=[Finding(ref='F1', finding_kind='pass', description='selected singleton')],
    ), 'error': '', 'raw_response': '{}'})()
    client = FakeAIClient([first, second])

    result = _try_mapping_draft_editor_acceptance(workspace, client, [], [])

    assert result is not None and result.status == 'accepted'
    assert len(client.prompts) == 2
    assert any(a.get('note') == 'mapping_draft_comparison_conflict_repair_requested' for a in result.final_workspace.judge_request_audits if isinstance(a, dict))
    assert result.final_output is not None
    assert result.final_output.assignment_intents[0].target_ref == 'BE2'


def test_unresolved_special_candidate_gets_one_more_editor_repair(monkeypatch):
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-UNRESOLVED-SPECIAL-REPAIR'),
        budget=CaseBudget(max_judge_rounds=2, max_evidence_batches=0, max_issue_response_rounds=0),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1', 'BE2']),
        local_files=[LocalFileCard(ref='LF1', path='Special B.mkv', is_main=True, file_kind='video')],
        bangumi_items=[
            BangumiItemCard(ref='BE1', item_kind='special', subject_ref='BS1', title='Special A', source_form_hint='special'),
            BangumiItemCard(ref='BE2', item_kind='special', subject_ref='BS1', title='Special B', source_form_hint='special'),
        ],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='residual', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1'])],
    ).with_seen_detail_refs(['BE1', 'BE2']).with_mapping_draft(MappingDraft(draft_ref='MD1', rows=[
        MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', status='open', candidate_target_refs=['BE1', 'BE2']),
    ], version=1))
    first = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(
        patches=[MappingDraftPatch(op='needs_more_evidence', local_ref='LS1', reason_kind='ambiguous_candidate', reason='unsure')],
        candidate_comparisons=[CandidateComparison(ref='R1', left_ref='BE1', right_ref='BE2', winner_ref='', reason='no winner')],
    ), 'error': '', 'raw_response': '{}'})()
    second = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(
        patches=[MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_ref='BE2', mapping_mode='explicit', support_refs=['LS1', 'BE2'], reason='repair selects visible candidate')],
        candidate_comparisons=[CandidateComparison(ref='R1', left_ref='BE1', right_ref='BE2', winner_ref='BE2', reason='visible row comparison')],
        findings=[Finding(ref='F1', finding_kind='pass', description='repaired unresolved special')],
    ), 'error': '', 'raw_response': '{}'})()
    client = FakeAIClient([first, second])

    result = _try_mapping_draft_editor_acceptance(workspace, client, [], [])

    assert result is not None and result.status == 'accepted'
    assert len(client.prompts) == 2
    assert any(a.get('note') == 'mapping_draft_unresolved_special_repair_requested' for a in result.final_workspace.judge_request_audits if isinstance(a, dict))
    assert result.final_output is not None
    assert result.final_output.assignment_intents[0].target_ref == 'BE2'


def test_editor_accepts_explicit_special_singleton_item(monkeypatch):
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-SPECIAL-ITEM'),
        budget=CaseBudget(max_judge_rounds=2, max_evidence_batches=0, max_issue_response_rounds=0),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE_SPECIAL']),
        local_files=[LocalFileCard(ref='LF1', path='Special.mkv', is_main=True, file_kind='video')],
        bangumi_items=[BangumiItemCard(ref='BE_SPECIAL', item_kind='special', subject_ref='BS1', title='Special', source_form_hint='special')],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='residual', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1'])],
    ).with_seen_detail_refs(['BE_SPECIAL']).with_mapping_draft(MappingDraft(draft_ref='MD1', rows=[
        MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', status='open', candidate_target_refs=['BE_SPECIAL']),
    ], version=1))
    editor_output = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(patches=[
        MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_ref='BE_SPECIAL', mapping_mode='explicit', support_refs=['LS1', 'BE_SPECIAL'], reason='special item card and local singleton context agree'),
    ], findings=[Finding(ref='F1', finding_kind='pass', description='special item supported')]), 'error': '', 'raw_response': '{}'})()

    result = _try_mapping_draft_editor_acceptance(workspace, FakeAIClient(editor_output), [], [])

    assert result is not None and result.status == 'accepted'
    assert result.final_output is not None
    assert len(result.final_output.assignment_intents or []) == 1
    assert result.final_output.assignment_intents[0].file_ref == 'LF1'
    assert result.final_output.assignment_intents[0].target_ref == 'BE_SPECIAL'
