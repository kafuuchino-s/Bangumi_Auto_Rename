from src.rename.case_agent.models import (
    BangumiItemCard,
    BangumiSubjectCard,
    CandidateComparison,
    CaseBriefingOutput,
    CaseBriefingWorkUnit,
    CaseBudget,
    CaseContract,
    CaseHeader,
    EvidencePlan,
    EvidenceBatchResult,
    InvestigationNotebook,
    LocalFileCard,
    NotebookOpenQuestion,
    ProvenanceCard,
    LocalSpanCard,
    MappingDraft,
    MappingDraftRow,
)
from src.rename.case_agent.workspace import CaseEvidenceWorkspace


def _make_workspace() -> CaseEvidenceWorkspace:
    return CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='case-1'),
        budget=CaseBudget(max_api_calls_per_case=10),
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', subject_id=1)],
        bangumi_items=[BangumiItemCard(ref='BE1', subject_ref='BS1', item_kind='episode')],
    )


def test_visible_refs_and_target_catalog():
    workspace = _make_workspace()

    catalog = workspace.visible_refs()
    assert catalog.target_refs == ['BE1']
    assert catalog.bangumi_item_refs == ['BE1']


def test_ref_lookup_and_target_checks():
    workspace = _make_workspace()

    assert workspace.has_ref('BS1') is True
    assert workspace.get_ref_kind('BS1') == 'bangumi_subject'
    assert workspace.get_ref_kind('BE1') == 'bangumi_item'
    assert workspace.get_ref_kind('missing') == 'unknown'
    assert workspace.is_visible_target('BE1') is True
    assert workspace.is_visible_target('UNALIGNED') is True
    assert workspace.is_visible_target('missing') is False


def test_duplicate_added_ref_raises():
    workspace = _make_workspace()

    try:
        workspace.with_added_evidence(subjects=[BangumiSubjectCard(ref='BS1', subject_id=2)])
        assert False, 'expected duplicate ref error'
    except ValueError as exc:
        assert 'duplicate ref' in str(exc)


def test_with_added_evidence_returns_new_workspace():
    workspace = _make_workspace()
    added = workspace.with_added_evidence(
        provenance=[ProvenanceCard(ref='PV1', source_operation='fetch')],
        evidence_results=[EvidenceBatchResult(batch_ref='B1')],
    )

    assert added is not workspace
    assert workspace.has_ref('PV1') is False
    assert added.has_ref('PV1') is True
    assert workspace.previous_evidence_results == []
    assert len(added.previous_evidence_results) == 1


def test_seen_detail_refs_are_carried():
    workspace = _make_workspace()
    updated = workspace.with_seen_detail_refs(['BE1'])

    assert updated.seen_detail_refs == ['BE1']


def test_seen_detail_refs_do_not_become_assignable_targets_without_contract_surface():
    workspace = _make_workspace()
    workspace = CaseEvidenceWorkspace.from_cards(
        header=workspace.header,
        budget=workspace.budget,
        contract=workspace.contract.model_copy(update={'visible_target_refs': []}),
        bangumi_subjects=workspace.bangumi_subjects,
        bangumi_items=workspace.bangumi_items,
    ).with_seen_detail_refs(['BE1'])

    dossier = workspace.to_dossier()

    assert dossier.seen_detail_refs
    assert dossier.assignable_target_refs == []


def test_seen_detail_refs_become_assignable_when_in_contract_surface():
    workspace = _make_workspace()
    workspace = CaseEvidenceWorkspace.from_cards(
        header=workspace.header,
        budget=workspace.budget,
        contract=workspace.contract.model_copy(update={'visible_target_refs': ['BE1']}),
        bangumi_subjects=workspace.bangumi_subjects,
        bangumi_items=workspace.bangumi_items,
    ).with_seen_detail_refs(['BE1'])

    dossier = workspace.to_dossier()

    assert dossier.assignable_target_refs == ['BE1']


def test_to_dossier_builds_case_dossier():
    workspace = _make_workspace()
    dossier = workspace.to_dossier(contract=CaseContract(summary='contract'))

    assert dossier.header.case_id == 'case-1'
    assert dossier.contract.summary == 'contract'
    assert dossier.visible_refs.target_refs == ['BE1']
    assert dossier.bangumi_items[0].ref == 'BE1'
    assert dossier.plan_state.plan_status == 'idle'


def test_workspace_plan_state_survives_rebuild():
    workspace = _make_workspace()
    plan = EvidencePlan(plan_id='PLAN1', plan_kind='span_proof', selected_menu_request_ids=['R1'], plan_status='in_progress')
    rebuilt = CaseEvidenceWorkspace.from_cards(header=workspace.header, budget=workspace.budget, bangumi_subjects=workspace.bangumi_subjects, bangumi_items=workspace.bangumi_items, plan_state=plan)

    assert rebuilt.plan_state.plan_id == 'PLAN1'
    assert rebuilt.plan_state.selected_menu_request_ids == ['R1']


def test_workspace_preserves_mapping_draft_after_evidence_batch():
    draft = MappingDraft(rows=[MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span')])
    comparison = CandidateComparison(ref='R1', left_ref='BE1', right_ref='BE2', winner_ref='BE1', reason='kept')
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='case-4'),
        budget=CaseBudget(max_api_calls_per_case=10),
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='directory', file_ref_count=1)],
        mapping_draft=draft,
        mapping_draft_candidate_comparisons=[comparison],
    )
    updated = workspace.with_added_evidence(evidence_results=[EvidenceBatchResult(batch_ref='B1')])
    assert updated.mapping_draft is not None
    assert updated.mapping_draft.rows[0].local_ref == 'LS1'
    assert updated.mapping_draft_candidate_comparisons == [comparison]


def test_workspace_rebuilds_preserve_case_briefing_and_notebook():
    briefing = CaseBriefingOutput(
        package_shape='tv_plus_extras',
        work_units=[CaseBriefingWorkUnit(work_unit_ref='WU1', local_refs=['LS1'], file_refs=['LF1'])],
    )
    notebook = InvestigationNotebook(open_questions=[NotebookOpenQuestion(question_ref='NQ1', question_kind='subject_recall', question='search subject', local_refs=['LF1'], requested_request_types=['subject_search'])])
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='case-memory'),
        budget=CaseBudget(max_api_calls_per_case=10),
        local_files=[LocalFileCard(ref='LF1', path='a.mkv', is_main=True)],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='directory', file_refs=['LF1'], file_ref_count=1)],
        case_briefing=briefing,
        investigation_notebook=notebook,
    )

    added = workspace.with_added_evidence(evidence_results=[EvidenceBatchResult(batch_ref='B1')])
    queried = added.with_query_cards([])
    detailed = queried.with_seen_detail_refs(['LF1'])
    dossier = detailed.to_dossier()

    assert detailed.case_briefing is briefing
    assert detailed.investigation_notebook.open_questions[0].question_ref == 'NQ1'
    assert dossier.case_briefing is briefing
    assert dossier.notebook['investigation_notebook']['counts']['open_question_count'] == 1


def test_to_dossier_preserves_previous_bangumi_span_cards():
    workspace = _make_workspace()
    from src.rename.case_agent.models import BangumiSpanCard, EvidenceBatchResult, EvidenceRequestResult
    object.__setattr__(workspace, 'previous_evidence_results', [EvidenceBatchResult(batch_ref='B1', request_results=[EvidenceRequestResult(request_ref='R1', request_type='target_span', bangumi_span_cards=[BangumiSpanCard(ref='BES1', subject_ref='BS1', target_refs=['BE1'], target_ref_count=1, item_kind='regular', detail_equivalent=True)])])])
    dossier = workspace.to_dossier()
    assert any(card.ref == 'BES1' for card in dossier.bangumi_span_cards)


def test_to_dossier_keeps_multiple_bangumi_span_cards():
    workspace = _make_workspace()
    from src.rename.case_agent.models import BangumiSpanCard, EvidenceBatchResult, EvidenceRequestResult
    object.__setattr__(workspace, 'previous_evidence_results', [EvidenceBatchResult(batch_ref='B1', request_results=[EvidenceRequestResult(request_ref='R1', request_type='target_span', bangumi_span_cards=[BangumiSpanCard(ref='BES1', subject_ref='BS1', target_refs=['BE1'], target_ref_count=1, item_kind='regular', detail_equivalent=True), BangumiSpanCard(ref='BES2', subject_ref='BS1', target_refs=['BE2'], target_ref_count=1, item_kind='regular', detail_equivalent=True)])])])
    dossier = workspace.to_dossier()
    refs = [card.ref for card in dossier.bangumi_span_cards]
    assert 'BES1' in refs and 'BES2' in refs


def test_visible_refs_target_refs_only_from_items():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='case-2'),
        budget=CaseBudget(max_api_calls_per_case=10),
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', subject_id=1)],
    )

    assert workspace.visible_refs().target_refs == []


def test_workspace_records_duplicate_visible_target_ref_diagnostic():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='case-3'),
        budget=CaseBudget(max_api_calls_per_case=10),
        bangumi_items=[BangumiItemCard(ref='BE1'), BangumiItemCard(ref='BE1')],
        contract=CaseContract(visible_target_refs=['BE1', 'BE1']),
    )

    catalog = workspace.visible_refs()

    assert catalog.target_refs == ['BE1']
    assert 'dossier_target_ref_duplicate' in workspace.diagnostics
