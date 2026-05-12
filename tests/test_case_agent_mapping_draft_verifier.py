from src.rename.case_agent.assignment_expander import expand_mapping_draft
from src.rename.case_agent.models import BangumiSpanCard, CaseDossier, CaseHeader, LocalSpanCard, MappingDraft, MappingDraftRow, VisibleRefCatalog


def make_dossier() -> CaseDossier:
    return CaseDossier(
        header=CaseHeader(case_id='CASE-MD'),
        visible_refs=VisibleRefCatalog(local_file_refs=['LF1', 'LF2', 'LF3'], target_refs=['BE1', 'BE2', 'BE3']),
        local_span_cards=[LocalSpanCard(ref='LS1', file_refs=['LF1', 'LF2'], file_ref_count=2), LocalSpanCard(ref='LS2', file_refs=['LF3'], file_ref_count=1)],
        bangumi_span_cards=[BangumiSpanCard(ref='BS1', target_refs=['BE1', 'BE2'], target_ref_count=2, detail_equivalent=True), BangumiSpanCard(ref='BS2', target_refs=['BE3'], target_ref_count=1, detail_equivalent=True)],
    )


def test_span_by_index_draft_expands_n_assignments():
    dossier = make_dossier()
    draft = MappingDraft(rows=[MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', selected_target_ref='BS1', selected_target_kind='span', mapping_mode='span_by_index', status='proposed')])

    expanded, issues = expand_mapping_draft(dossier, draft)

    assert issues == []
    assert [item.ref for item in expanded] == ['MDA_R1_1', 'MDA_R1_2']
    assert [item.file_ref for item in expanded] == ['LF1', 'LF2']
    assert [item.target_ref for item in expanded] == ['BE1', 'BE2']


def test_span_by_index_count_mismatch_blocked():
    dossier = make_dossier().model_copy(update={'bangumi_span_cards': [BangumiSpanCard(ref='BS1', target_refs=['BE1'], target_ref_count=1, detail_equivalent=True)]})
    draft = MappingDraft(rows=[MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', selected_target_ref='BS1', selected_target_kind='span', mapping_mode='span_by_index', status='proposed')])

    expanded, issues = expand_mapping_draft(dossier, draft)

    assert expanded == []
    assert any(issue.issue_code == 'count_mismatch' for issue in issues)


def test_duplicate_target_blocked():
    dossier = make_dossier().model_copy(update={'bangumi_span_cards': [BangumiSpanCard(ref='BS1', target_refs=['BE1', 'BE2'], target_ref_count=2, detail_equivalent=True), BangumiSpanCard(ref='BS2', target_refs=['BE2'], target_ref_count=1, detail_equivalent=True)]})
    draft = MappingDraft(rows=[MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', selected_target_ref='BS1', selected_target_kind='span', mapping_mode='span_by_index', status='proposed'), MappingDraftRow(row_ref='R2', local_ref='LS2', local_ref_kind='span', selected_target_ref='BS2', selected_target_kind='span', mapping_mode='span_by_index', status='proposed')])

    expanded, issues = expand_mapping_draft(dossier, draft)

    assert [item.ref for item in expanded] == ['MDA_R1_1', 'MDA_R1_2']
    assert any(issue.issue_code == 'duplicate_target' for issue in issues)
    duplicate = next(issue for issue in issues if issue.issue_code == 'duplicate_target')
    assert 'R1' in duplicate.related_refs
    assert 'R2' in duplicate.related_refs
    assert 'BE2' in duplicate.related_refs


def test_missing_local_or_target_span_blocked():
    dossier = make_dossier()
    draft = MappingDraft(rows=[MappingDraftRow(row_ref='R1', local_ref='MISSING', local_ref_kind='span', selected_target_ref='BS1', selected_target_kind='span', mapping_mode='span_by_index', status='proposed'), MappingDraftRow(row_ref='R2', local_ref='LS1', local_ref_kind='span', selected_target_ref='MISSING', selected_target_kind='span', mapping_mode='span_by_index', status='proposed')])

    expanded, issues = expand_mapping_draft(dossier, draft)

    assert expanded == []
    assert sum(1 for issue in issues if issue.issue_code == 'missing_span_ref') == 2


def test_open_rows_not_expanded():
    dossier = make_dossier()
    draft = MappingDraft(rows=[MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', selected_target_ref='BS1', selected_target_kind='span', mapping_mode='span_by_index', status='open')])

    expanded, issues = expand_mapping_draft(dossier, draft)

    assert expanded == []
    assert issues == []
