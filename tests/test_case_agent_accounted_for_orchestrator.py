from __future__ import annotations

from src.rename.case_agent.models import (
    AssignmentIntent,
    BangumiItemCard,
    BangumiSubjectCard,
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
    assert result.summary == 'no_new_evidence'
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
    assert result.summary == 'no_new_evidence'
    assert result.final_verifier_result is not None and result.final_verifier_result.passed is True
    assert any(a.get('note') == 'mapping_draft_patch_issues' for a in result.final_workspace.judge_request_audits if isinstance(a, dict))


def test_invalid_patch_fail_closed_output_is_slim_even_with_large_editor_context(monkeypatch):
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-PATCH-SLIM'),
        budget=CaseBudget(max_judge_rounds=2, max_evidence_batches=0, max_issue_response_rounds=0),
        contract=CaseContract(main_file_refs=['LF1', 'LF2'], allowed_file_refs=['LF1', 'LF2'], visible_target_refs=['BE1']),
        local_files=[LocalFileCard(ref='LF1', is_main=True), LocalFileCard(ref='LF2', is_main=True)],
        bangumi_items=[BangumiItemCard(ref='BE1')],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='directory', file_ref_count=2, file_refs=['LF1', 'LF2'])],
        bangumi_span_cards=[BangumiSpanCard(ref='BS1', detail_equivalent=True, target_refs=['BE1'], target_ref_count=1)],
    ).with_mapping_draft(MappingDraft(draft_ref='MD1', rows=[
        MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', status='open'),
    ], version=1))
    editor_output = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(
        patches=[MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_span_ref='BES_MISSING', mapping_mode='span_by_index', reason='bad')],
        findings=[Finding(ref='F_BIG', finding_kind='warning', description='too many refs', evidence_refs=[f'LF{i}' for i in range(1, 30)])],
        candidate_comparisons=[
            CandidateComparison(ref=f'C{i}', left_ref='BE1', right_ref=f'BE{i}', winner_ref='BE1', reason='large rejected comparison')
            for i in range(1, 30)
        ],
    ), 'error': '', 'raw_response': '{}'})()

    result = _try_mapping_draft_editor_acceptance(workspace, FakeAIClient(editor_output), [], [])

    assert result is not None and result.status == 'fail_closed'
    assert result.summary == 'no_new_evidence'
    assert result.final_output is not None
    assert result.final_output.findings == []
    assert result.final_output.candidate_comparisons == []
    assert result.final_verifier_result is not None and result.final_verifier_result.passed is True


def test_structural_repair_adds_support_finding_when_editor_omits_findings(monkeypatch):
    workspace = _workspace(row=MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', status='open'))
    editor_output = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(patches=[MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_span_ref='BS1', mapping_mode='span_by_index', reason='mapped')]), 'error': '', 'raw_response': '{}'})()
    result = _try_mapping_draft_editor_acceptance(workspace, FakeAIClient(editor_output), [], [])

    assert result is not None and result.status == 'accepted'
    assert result.final_verifier_result is not None and result.final_verifier_result.passed is True
    assert result.final_output is not None and result.final_output.findings[0].ref == 'F_MAP1'


def test_accepted_mapping_draft_compacts_editor_finding_refs_before_verifier(monkeypatch):
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-ACCEPT-SLIM'),
        budget=CaseBudget(max_judge_rounds=2, max_evidence_batches=0, max_issue_response_rounds=0),
        contract=CaseContract(
            main_file_refs=[f'LF{i}' for i in range(1, 22)],
            allowed_file_refs=[f'LF{i}' for i in range(1, 22)],
            visible_target_refs=[f'BE{i}' for i in range(1, 22)],
        ),
        local_files=[LocalFileCard(ref=f'LF{i}', is_main=True) for i in range(1, 22)],
        bangumi_items=[BangumiItemCard(ref=f'BE{i}') for i in range(1, 22)],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='directory', file_ref_count=21, file_refs=[f'LF{i}' for i in range(1, 22)])],
        bangumi_span_cards=[BangumiSpanCard(ref='BES1', detail_equivalent=True, target_refs=[f'BE{i}' for i in range(1, 22)], target_ref_count=21)],
    ).with_seen_detail_refs([f'BE{i}' for i in range(1, 22)]).with_mapping_draft(MappingDraft(draft_ref='MD1', rows=[
        MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', status='open', candidate_target_refs=['BES1']),
    ], version=1))
    editor_output = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(
        patches=[
            MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_span_ref='BES1', mapping_mode='span_by_index', support_refs=['LS1', 'BES1'], reason='mapped'),
        ],
        findings=[
            Finding(
                ref='F_BIG',
                finding_kind='pass',
                description='large span accepted',
                evidence_refs=[f'LF{i}' for i in range(1, 22)] + ['LS1', 'BES1'] + [f'BE{i}' for i in range(1, 22)],
            ),
        ],
    ), 'error': '', 'raw_response': '{}'})()

    result = _try_mapping_draft_editor_acceptance(workspace, FakeAIClient(editor_output), [], [])

    assert result is not None and result.status == 'accepted'
    assert result.final_verifier_result is not None and result.final_verifier_result.passed is True
    assert result.final_output is not None
    assert len(result.final_output.findings[0].evidence_refs) <= 12


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
    assert result.summary == 'no_new_evidence'
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
    workspace = _workspace(row=MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', status='open', candidate_target_refs=['BS1']))
    bad = type('EditorResult', (), {'output': None})()
    good = type('EditorResult', (), {'output': MappingDraftEditorOutput(
        patches=[
            MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_span_ref='BS1', mapping_mode='span_by_index', reason='retry success')
        ],
        findings=[Finding(ref='F1', finding_kind='pass', description='retry success')],
    )})()
    client = FakeAIClient([bad, good])

    result = _try_mapping_draft_editor_acceptance(workspace, client, [], [])

    assert result is not None and result.status == 'accepted'
    assert len(client.prompts) == 2
    assert any(a.get('note') == 'mapping_draft_editor_retry_requested' for a in result.final_workspace.judge_request_audits if isinstance(a, dict))


def test_accepted_mapping_draft_drops_stale_failed_editor_self_checks(monkeypatch):
    workspace = _workspace(row=MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', status='open', candidate_target_refs=['BS1']))
    editor_output = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(
        patches=[
            MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_span_ref='BS1', mapping_mode='span_by_index', reason='mapped')
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
    first = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(patches=[
        MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_span_ref='BS1', mapping_mode='span_by_index', reason='mapped'),
        MappingDraftPatch(op='map_to_bangumi', local_ref='LS2', target_span_ref='BS1', mapping_mode='span_by_index', reason='dup'),
    ], findings=[Finding(ref='F1', finding_kind='pass', description='ok')]), 'error': '', 'raw_response': '{}'})()
    second = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(patches=[
        MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_span_ref='BS1', mapping_mode='span_by_index', support_refs=['LS1', 'BS1'], reason='kept first mapping'),
        MappingDraftPatch(op='map_to_bangumi', local_ref='LS2', target_span_ref='BS2', mapping_mode='span_by_index', support_refs=['LS2', 'BS2'], reason='editor repaired duplicate target'),
    ], findings=[Finding(ref='F2', finding_kind='pass', description='duplicate repaired by editor')]), 'error': '', 'raw_response': '{}'})()
    client = FakeAIClient([first, second])

    result = _try_mapping_draft_editor_acceptance(workspace, client, [], [])

    assert result is not None and result.status == 'accepted'
    assert len(client.prompts) == 2
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
    assert result.summary == 'semantic_target_conflict'


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
    assert len(client.prompts) == 2
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
    first = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(patches=[
        MappingDraftPatch(op='map_to_bangumi', local_ref='LS_MAIN', target_span_ref='BES_MAIN', mapping_mode='span_by_index', support_refs=['LS_MAIN', 'BES_MAIN'], reason='main mapped'),
        MappingDraftPatch(op='map_to_bangumi', local_ref='LS_TRAVEL', target_span_ref='BES_TRAVEL_BAD', mapping_mode='span_by_index', support_refs=['LS_TRAVEL', 'BES_TRAVEL_BAD'], reason='bad overlap'),
    ]), 'error': '', 'raw_response': '{}'})()
    second = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(patches=[
        MappingDraftPatch(op='map_to_bangumi', local_ref='LS_MAIN', target_span_ref='BES_MAIN', mapping_mode='span_by_index', support_refs=['LS_MAIN', 'BES_MAIN'], reason='main mapped'),
        MappingDraftPatch(op='mark_non_bangumi_or_supplemental', local_ref='LS_TRAVEL', reason_kind='making_of', support_refs=['LS_TRAVEL'], reason='editor marks visible travel feature as supplemental'),
    ], findings=[Finding(ref='F1', finding_kind='pass', description='editor repaired overlap by excluding travel feature')]), 'error': '', 'raw_response': '{}'})()
    client = FakeAIClient([first, second])

    result = _try_mapping_draft_editor_acceptance(workspace, client, [], [])

    assert result is not None and result.status == 'accepted'
    assert len(client.prompts) == 2
    rows = {row.local_ref: row for row in result.final_workspace.mapping_draft.rows}
    assert rows['LS_MAIN'].disposition == 'map_to_bangumi'
    assert rows['LS_TRAVEL'].disposition == 'non_bangumi_or_supplemental'
    assert rows['LS_TRAVEL'].reason_kind == 'making_of'
    assert any(
        a.get('note') in {'mapping_draft_accounting_structural_repair', 'mapping_draft_duplicate_target_repair_requested'}
        for a in result.final_workspace.judge_request_audits
        if isinstance(a, dict)
    )
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
    first = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(patches=[
        MappingDraftPatch(op='needs_more_evidence', local_ref='LS_PV', reason_kind='ambiguous_candidate', reason='no matching item'),
        MappingDraftPatch(op='needs_more_evidence', local_ref='LS_OP', reason_kind='ambiguous_candidate', reason='no matching item'),
    ]), 'error': '', 'raw_response': '{}'})()
    second = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(patches=[
        MappingDraftPatch(op='mark_non_bangumi_or_supplemental', local_ref='LS_PV', reason_kind='pv_cm', support_refs=['LS_PV'], reason='editor marks visible PV/CM collection as supplemental'),
        MappingDraftPatch(op='mark_non_bangumi_or_supplemental', local_ref='LS_OP', reason_kind='creditless_op_ed', support_refs=['LS_OP'], reason='editor marks visible creditless OP as supplemental'),
    ], findings=[Finding(ref='F1', finding_kind='pass', description='editor accounted visible supplemental rows')]), 'error': '', 'raw_response': '{}'})()
    client = FakeAIClient([first, second])

    result = _try_mapping_draft_editor_acceptance(workspace, client, [], [])

    assert result is not None and result.status == 'accepted'
    assert len(client.prompts) == 2
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
    first = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(patches=[
        MappingDraftPatch(op='needs_more_evidence', local_ref='LS2', reason_kind='ambiguous_candidate', reason='still unsure'),
    ]), 'error': '', 'raw_response': '{}'})()
    second = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(patches=[
        MappingDraftPatch(op='map_to_bangumi', local_ref='LS2', target_span_ref='BES2_OK', mapping_mode='span_by_index', support_refs=['LS2', 'BES2_OK'], reason='editor selects non-overlapping candidate'),
    ], findings=[Finding(ref='F1', finding_kind='pass', description='editor completed open span')]), 'error': '', 'raw_response': '{}'})()
    client = FakeAIClient([first, second])

    result = _try_mapping_draft_editor_acceptance(workspace, client, [], [])

    assert result is not None and result.status == 'accepted'
    assert len(client.prompts) == 2
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


def test_final_special_direct_visible_mapping_does_not_require_comparison_repair(monkeypatch):
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
    client = FakeAIClient(first)

    result = _try_mapping_draft_editor_acceptance(workspace, client, [], [])

    assert result is not None and result.status == 'accepted'
    assert len(client.prompts) == 1
    repair_notes = {
        a.get('note')
        for a in result.final_workspace.judge_request_audits
        if isinstance(a, dict)
    }
    assert not repair_notes & {'mapping_draft_comparison_conflict_repair_requested', 'mapping_draft_final_special_comparison_repair_requested'}
    assert not any(a.get('note') == 'mapping_draft_final_special_comparison_structural_repair' for a in result.final_workspace.judge_request_audits if isinstance(a, dict))
    assert result.final_output is not None
    assert result.final_output.assignment_intents[0].target_ref == 'BE_GOOD'


def test_comparison_patch_conflict_rewrites_from_visible_winner_without_retry(monkeypatch):
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
    assert len(client.prompts) == 1
    assert not any(a.get('note') == 'mapping_draft_comparison_conflict_repair_requested' for a in result.final_workspace.judge_request_audits if isinstance(a, dict))
    assert result.final_output is not None
    assert result.final_output.assignment_intents[0].target_ref == 'BE2'


def test_comparison_winner_span_rewrites_conflicting_patch_without_retry(monkeypatch):
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-SPAN-COMPARISON-REWRITE'),
        budget=CaseBudget(max_judge_rounds=2, max_evidence_batches=0, max_issue_response_rounds=0),
        contract=CaseContract(main_file_refs=['LF1', 'LF2'], allowed_file_refs=['LF1', 'LF2'], visible_target_refs=['BE1', 'BE2', 'BE3']),
        local_files=[
            LocalFileCard(ref='LF1', path='Show SP01.mkv', is_main=True, file_kind='video'),
            LocalFileCard(ref='LF2', path='Show SP02.mkv', is_main=True, file_kind='video'),
        ],
        bangumi_items=[
            BangumiItemCard(ref='BE1', item_kind='special', subject_ref='BS1', title='SP 1', source_form_hint='special'),
            BangumiItemCard(ref='BE2', item_kind='special', subject_ref='BS1', title='SP 2', source_form_hint='special'),
            BangumiItemCard(ref='BE3', item_kind='special', subject_ref='BS1', title='wrong singleton', source_form_hint='special'),
        ],
        local_span_cards=[
            LocalSpanCard(ref='LS1', span_scope='token_segment', file_refs=['LF1', 'LF2'], file_ref_count=2, file_ref_samples=['LF1', 'LF2'], title_cues=['SP'])
        ],
        bangumi_span_cards=[
            BangumiSpanCard(ref='BES_SP', item_kind='special', detail_equivalent=True, target_refs=['BE1', 'BE2'], target_ref_count=2),
        ],
    ).with_seen_detail_refs(['BE1', 'BE2', 'BE3', 'BES_SP']).with_mapping_draft(MappingDraft(draft_ref='MD1', rows=[
        MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', status='open', candidate_target_refs=['BES_SP', 'BE3']),
    ], version=1))
    editor_output = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(
        patches=[MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_ref='BE3', mapping_mode='explicit', support_refs=['LS1', 'BE3'], reason='bad singleton patch')],
        candidate_comparisons=[CandidateComparison(ref='R1', left_ref='BE3', right_ref='BES_SP', winner_ref='BES_SP', reason='span covers both SP files')],
        findings=[Finding(ref='F1', finding_kind='pass', description='span winner')],
    ), 'error': '', 'raw_response': '{}'})()
    client = FakeAIClient(editor_output)

    result = _try_mapping_draft_editor_acceptance(workspace, client, [], [])

    assert result is not None and result.status == 'accepted'
    assert len(client.prompts) == 1
    assert result.final_output is not None
    assert [a.target_ref for a in result.final_output.assignment_intents] == ['BE1', 'BE2']
    assert not any(a.get('note') == 'mapping_draft_comparison_conflict_repair_requested' for a in result.final_workspace.judge_request_audits if isinstance(a, dict))


def test_multi_candidate_singleton_mapping_can_use_direct_visible_target_without_comparison(monkeypatch):
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
    client = FakeAIClient(first)

    result = _try_mapping_draft_editor_acceptance(workspace, client, [], [])

    assert result is not None and result.status == 'accepted'
    assert len(client.prompts) == 1
    assert not any(a.get('note') == 'mapping_draft_comparison_conflict_repair_requested' for a in result.final_workspace.judge_request_audits if isinstance(a, dict))
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


def test_unresolved_singleton_without_target_can_repair_to_bangumi_target_absent(monkeypatch):
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-TARGET-ABSENT-REPAIR'),
        budget=CaseBudget(max_judge_rounds=2, max_evidence_batches=1, max_issue_response_rounds=0, used_evidence_batches=1),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=[]),
        local_files=[LocalFileCard(ref='LF1', path='Show OAD.mkv', is_main=True, file_kind='video')],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', subject_type='anime', title='Show', platform='TV')],
        bangumi_items=[],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='residual', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1'], title_cues=['OAD'])],
    ).with_mapping_draft(MappingDraft(draft_ref='MD1', rows=[
        MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', status='open'),
    ], version=1))
    first = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(
        patches=[MappingDraftPatch(op='needs_more_evidence', local_ref='LS1', reason_kind='ambiguous_candidate', reason='no visible Bangumi target')],
    ), 'error': '', 'raw_response': '{}'})()
    second = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(
        patches=[MappingDraftPatch(
            op='mark_non_bangumi_or_supplemental',
            local_ref='LS1',
            reason_kind='bangumi_target_absent',
            support_refs=['LS1'],
            reason='investigated Bangumi surface has no visible OAD target',
        )],
        findings=[Finding(ref='F1', finding_kind='pass', description='Bangumi target absent row accounted')],
    ), 'error': '', 'raw_response': '{}'})()
    client = FakeAIClient([first, second])

    result = _try_mapping_draft_editor_acceptance(workspace, client, [], [])

    assert result is not None and result.status == 'accepted'
    assert len(client.prompts) == 2
    assert any(a.get('note') == 'mapping_draft_unresolved_special_repair_requested' for a in result.final_workspace.judge_request_audits if isinstance(a, dict))
    assert result.final_workspace.mapping_draft is not None
    row = result.final_workspace.mapping_draft.rows[0]
    assert row.disposition == 'non_bangumi_or_supplemental'
    assert row.reason_kind == 'bangumi_target_absent'
    assert result.final_output is not None
    assert result.final_output.assignment_intents[0].target_ref == 'UNALIGNED'
    assert 'bangumi_target_absent' in result.final_output.assignment_intents[0].reason


def test_generic_supplemental_sp_span_salvages_to_bangumi_target_absent(monkeypatch):
    main_refs = [f'LF{i}' for i in range(1, 5)]
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-TARGET-ABSENT-SP-SALVAGE'),
        budget=CaseBudget(max_judge_rounds=2, max_evidence_batches=1, max_issue_response_rounds=0, used_evidence_batches=1),
        contract=CaseContract(main_file_refs=main_refs, allowed_file_refs=main_refs, visible_target_refs=[]),
        local_files=[
            LocalFileCard(ref=ref, path=f'SPs/Show SP{index:02d}.mkv', is_main=True, file_kind='video')
            for index, ref in enumerate(main_refs, start=1)
        ],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', subject_type='anime', title='Show', platform='TV')],
        bangumi_items=[],
        local_span_cards=[
            LocalSpanCard(
                ref='LS_SP',
                span_scope='residual',
                file_refs=main_refs,
                file_ref_count=len(main_refs),
                file_ref_samples=['LF1', 'LF4'],
                ordering_basis='unknown',
                title_cues=['SPs'],
            )
        ],
    ).with_mapping_draft(MappingDraft(draft_ref='MD1', rows=[
        MappingDraftRow(row_ref='R1', local_ref='LS_SP', local_ref_kind='span', status='open'),
    ], version=1))
    editor_output = type('EditorResult', (), {'ok': True, 'output': MappingDraftEditorOutput(
        patches=[MappingDraftPatch(
            op='mark_non_bangumi_or_supplemental',
            local_ref='LS_SP',
            reason_kind='other_supplemental',
            support_refs=['LS_SP'],
            reason='SP extras investigated but no Bangumi target is visible',
        )],
        findings=[Finding(ref='F1', finding_kind='pass', description='SP extra should not enter Bangumi mapping')],
    ), 'error': '', 'raw_response': '{}'})()

    result = _try_mapping_draft_editor_acceptance(workspace, FakeAIClient(editor_output), [], [])

    assert result is not None and result.status == 'accepted'
    assert result.final_workspace.mapping_draft is not None
    row = result.final_workspace.mapping_draft.rows[0]
    assert row.disposition == 'non_bangumi_or_supplemental'
    assert row.reason_kind == 'bangumi_target_absent'
    assert result.final_output is not None
    assert {assignment.file_ref for assignment in result.final_output.assignment_intents} == set(main_refs)


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
