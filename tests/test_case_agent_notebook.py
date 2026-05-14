from src.rename.case_agent.notebook import (
    apply_notebook_updates,
    build_initial_investigation_notebook,
    build_notebook,
    close_notebook_agenda_for_evidence_results,
    human_next_action_blockers,
    validate_case_briefing_refs,
)
from src.rename.case_agent.models import (
    CaseBriefingEvidenceQuestion,
    CaseBriefingOutput,
    CaseBriefingTitleHypothesis,
    CaseBriefingWorkUnit,
    CaseBudget,
    CaseHeader,
    EvidenceRequestResult,
    LocalFileCard,
    LocalSpanCard,
    MappingDraft,
    MappingDraftRow,
    NotebookUpdate,
)
from src.rename.case_agent.workspace import CaseEvidenceWorkspace


def test_notebook_is_compact_and_has_no_full_dump_flags():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c1'),
        budget=CaseBudget(),
        local_files=[LocalFileCard(ref='LF1', path='a.mkv', is_main=True)],
    )
    note = build_notebook(ws)
    assert note['compact'] is True
    assert note['no_full_prompt'] is True
    assert note['no_full_raw_output'] is True
    assert note['no_full_catalog'] is True


def test_notebook_includes_compact_plan_state():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c2'),
        budget=CaseBudget(),
    )
    note = build_notebook(ws)
    assert 'plan_state' in note
    assert note['plan_state']['plan_status'] == 'idle'


def test_notebook_mapping_draft_summary_includes_accounting_counts():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c3'),
        budget=CaseBudget(),
        local_files=[LocalFileCard(ref='LF1', path='a.mkv', is_main=True), LocalFileCard(ref='LF2', path='b.mkv', is_main=True)],
        mapping_draft=MappingDraft(rows=[MappingDraftRow(local_ref='LF1', disposition='map_to_bangumi'), MappingDraftRow(local_ref='LF2', disposition='non_bangumi_or_supplemental')]),
    )
    note = build_notebook(ws)
    summary = note['mapping_draft_summary']
    assert summary['main_file_count'] == 2
    assert summary['accounted_for_count'] == 2
    assert summary['accepted_accounting_ready'] is True


def test_case_briefing_hidden_refs_are_rejected():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c4'),
        budget=CaseBudget(),
        local_files=[LocalFileCard(ref='LF1', path='a.mkv', is_main=True)],
        local_span_cards=[LocalSpanCard(ref='LS1', file_refs=['LF1'], file_ref_count=1)],
    )
    briefing = CaseBriefingOutput(
        work_units=[CaseBriefingWorkUnit(work_unit_ref='WU1', local_refs=['LF404'], file_refs=['LF1'])],
        title_hypotheses=[CaseBriefingTitleHypothesis(title='Title', source_refs=['LS1'])],
    )

    issues = validate_case_briefing_refs(briefing, ws)

    assert any(issue.issue_code == 'briefing_hidden_ref' for issue in issues)


def test_initial_notebook_uses_briefing_work_units_and_questions():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c5'),
        budget=CaseBudget(),
        local_files=[LocalFileCard(ref='LF1', path='a.mkv', is_main=True)],
        local_span_cards=[LocalSpanCard(ref='LS1', file_refs=['LF1'], file_ref_count=1)],
    )
    briefing = CaseBriefingOutput(
        package_shape='singleton_special',
        work_units=[CaseBriefingWorkUnit(work_unit_ref='WU1', label='special unit', local_refs=['LS1'], file_refs=['LF1'], span_refs=['LS1'])],
        evidence_questions=[CaseBriefingEvidenceQuestion(question_ref='BQ1', question_kind='related_special', question='check related specials', local_refs=['LS1'], requested_request_types=['related_expansion'])],
    )

    notebook = build_initial_investigation_notebook(briefing, ws)

    assert notebook.work_unit_states[0].work_unit_ref == 'WU1'
    assert notebook.open_questions[0].question_ref == 'BQ1'
    assert human_next_action_blockers(ws.to_dossier().model_copy(update={'investigation_notebook': notebook}))


def test_notebook_updates_are_validated_and_persisted():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c6'),
        budget=CaseBudget(),
        local_files=[LocalFileCard(ref='LF1', path='a.mkv', is_main=True)],
    )
    notebook, issues = apply_notebook_updates(
        None,
        [NotebookUpdate(update_kind='note', local_refs=['LF1'], claim='row handled', reason='visible local singleton')],
        ws,
    )

    assert issues == []
    assert notebook.update_log
    assert notebook.active_hypotheses or notebook.work_unit_states or notebook.target_ownership

    _notebook, hidden_issues = apply_notebook_updates(
        notebook,
        [NotebookUpdate(update_kind='note', local_refs=['LF404'], claim='bad')],
        ws,
    )
    assert any(issue.issue_code == 'notebook_update_hidden_ref' for issue in hidden_issues)


def test_notebook_update_uses_notebook_refs_for_internal_memory_refs():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c7'),
        budget=CaseBudget(),
        local_files=[LocalFileCard(ref='LF1', path='a.mkv', is_main=True)],
    )
    briefing = CaseBriefingOutput(
        work_units=[CaseBriefingWorkUnit(work_unit_ref='WU1', label='unit', local_refs=['LF1'], file_refs=['LF1'])],
    )
    notebook = build_initial_investigation_notebook(briefing, ws)

    updated, issues = apply_notebook_updates(
        notebook,
        [NotebookUpdate(update_kind='needs_more_evidence', notebook_refs=['WU1'], local_refs=['LF1'], requested_request_types=['subject_search'], query_hints=['Clean Title'])],
        ws.to_dossier(round_context='notebook_update').model_copy(update={'investigation_notebook': notebook}),
    )

    assert issues == []
    assert updated.open_questions[-1].requested_request_types == ['subject_search']
    assert updated.open_questions[-1].query_hints == ['Clean Title']

    _unchanged, hidden_issues = apply_notebook_updates(
        notebook,
        [NotebookUpdate(update_kind='needs_more_evidence', local_refs=['WU1'], requested_request_types=['subject_search'])],
        ws.to_dossier(round_context='notebook_update').model_copy(update={'investigation_notebook': notebook}),
    )
    assert any(issue.issue_code == 'notebook_update_hidden_ref' for issue in hidden_issues)


def test_evidence_results_close_matching_notebook_agenda():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c8'),
        budget=CaseBudget(),
        local_files=[LocalFileCard(ref='LF1', path='a.mkv', is_main=True)],
    )
    notebook, issues = apply_notebook_updates(
        None,
        [NotebookUpdate(update_kind='request_evidence', local_refs=['LF1'], requested_request_types=['subject_search'], query_hints=['Title'])],
        ws,
    )
    assert issues == []
    assert human_next_action_blockers(ws.to_dossier().model_copy(update={'investigation_notebook': notebook}))

    closed = close_notebook_agenda_for_evidence_results(
        notebook,
        [EvidenceRequestResult(request_ref='ER1', request_type='subject_search', accepted=True)],
    )

    assert not human_next_action_blockers(ws.to_dossier().model_copy(update={'investigation_notebook': closed}))
