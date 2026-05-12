from src.rename.case_agent.mapping_draft import apply_mapping_patches, build_initial_mapping_draft, compute_mapping_draft_accounting, normalize_mapping_patch_op, validate_mapping_patch
from src.rename.case_agent.models import BangumiItemCard, BangumiSpanCard, CaseContract, CaseDossier, LocalFileCard, LocalSpanCard, MappingDraft, MappingDraftPatch, MappingDraftRow


def _dossier() -> CaseDossier:
    local_files = [
        LocalFileCard(ref='F1', path='pkg/a1.mkv', is_main=True),
        LocalFileCard(ref='F2', path='pkg/a2.mkv', is_main=True),
        LocalFileCard(ref='F3', path='pkg/b1.mkv', is_main=True),
    ]
    local_spans = [
        LocalSpanCard(ref='LS1', span_scope='directory', file_refs=['F1', 'F2'], file_ref_count=2),
        LocalSpanCard(ref='LS2', span_scope='token_segment', file_refs=['F3'], file_ref_count=1),
    ]
    bangumi_spans = [BangumiSpanCard(ref='BS1', detail_equivalent=True, target_refs=['BE1'])]
    bangumi_items = [BangumiItemCard(ref='BE1', subject_ref='SUB1', item_kind='episode')]
    contract = CaseContract(main_file_refs=['F1', 'F2', 'F3'])
    return CaseDossier(local_files=local_files, local_span_cards=local_spans, bangumi_span_cards=bangumi_spans, bangumi_items=bangumi_items, contract=contract)


def test_normalize_legacy_ops():
    assert normalize_mapping_patch_op(MappingDraftPatch(op='propose_span_mapping')).op == 'map_to_bangumi'
    assert normalize_mapping_patch_op(MappingDraftPatch(op='propose_explicit_mapping')).op == 'map_to_bangumi'
    assert normalize_mapping_patch_op(MappingDraftPatch(op='mark_unresolved')).op == 'needs_more_evidence'


def test_all_rows_mapped_ready():
    dossier = _dossier()
    draft = MappingDraft(rows=[
        MappingDraftRow(local_ref='LS1', disposition='map_to_bangumi', status='proposed'),
        MappingDraftRow(local_ref='LS2', disposition='map_to_bangumi', status='proposed'),
    ])
    accounting = compute_mapping_draft_accounting(draft, dossier)
    assert accounting.accounted_for_count == 3
    assert accounting.unresolved_count == 0
    assert accounting.accepted_accounting_ready is True


def test_supplemental_counts_as_accounted_and_ready():
    dossier = _dossier()
    dossier.local_files[2].path = 'pkg/bonus extra video.mkv'
    dossier.local_span_cards[1].title_cues = ['bonus extra video']
    dossier.local_span_cards[1].file_refs = ['F3']
    dossier.local_span_cards[1].file_ref_count = 1
    draft = MappingDraft(rows=[
        MappingDraftRow(local_ref='LS1', disposition='map_to_bangumi'),
        MappingDraftRow(local_ref='LS2', disposition='non_bangumi_or_supplemental', status='proposed', reason_kind='bonus_video', support_refs=['LS2']),
    ])
    accounting = compute_mapping_draft_accounting(draft, dossier)
    assert accounting.accounted_for_count == 3
    assert accounting.excluded_file_count == 1
    assert accounting.accepted_accounting_ready is True


def test_needs_more_evidence_blocks_ready():
    dossier = _dossier()
    draft = MappingDraft(rows=[
        MappingDraftRow(local_ref='LS1', disposition='map_to_bangumi'),
        MappingDraftRow(local_ref='LS2', disposition='needs_more_evidence', status='unresolved'),
    ])
    accounting = compute_mapping_draft_accounting(draft, dossier)
    assert accounting.unresolved_count > 0
    assert accounting.accepted_accounting_ready is False


def test_supplemental_patch_requires_support_and_no_target():
    dossier = _dossier()
    draft = build_initial_mapping_draft(dossier)
    bad1 = MappingDraftPatch(op='mark_non_bangumi_or_supplemental', local_ref='LS1', reason_kind='sample')
    bad2 = MappingDraftPatch(op='mark_non_bangumi_or_supplemental', local_ref='LS1', target_ref='BE1', support_refs=['F1'], reason_kind='sample')
    assert validate_mapping_patch(bad1, dossier, draft)
    assert validate_mapping_patch(bad2, dossier, draft)


def test_duplicate_local_ref_conflict_and_missing_main_refs():
    dossier = _dossier()
    draft = MappingDraft(rows=[
        MappingDraftRow(local_ref='LS1', disposition='map_to_bangumi'),
        MappingDraftRow(local_ref='LS1', disposition='non_bangumi_or_supplemental'),
    ])
    accounting = compute_mapping_draft_accounting(draft, dossier)
    assert accounting.duplicate_local_ref_count == 1
    assert accounting.overlap_main_file_count >= 1

    dossier2 = _dossier()
    dossier2.contract.main_file_refs = ['F1', 'F2', 'F3', 'F4']
    accounting2 = compute_mapping_draft_accounting(MappingDraft(rows=[MappingDraftRow(local_ref='LS1', disposition='map_to_bangumi'), MappingDraftRow(local_ref='LS2', disposition='map_to_bangumi')]), dossier2)
    assert accounting2.missing_main_file_count == 1


def test_span_local_ref_resolves_file_count():
    dossier = _dossier()
    accounting = compute_mapping_draft_accounting(MappingDraft(rows=[MappingDraftRow(local_ref='LS1', disposition='map_to_bangumi')]), dossier)
    assert accounting.mapped_file_count == 2


def test_legacy_propose_span_mapping_applies_as_mapping():
    dossier = _dossier()
    draft = build_initial_mapping_draft(dossier)
    updated, issues = apply_mapping_patches(draft, [MappingDraftPatch(op='propose_span_mapping', local_ref='LS1', target_span_ref='BS1', reason='mapped')], dossier)
    assert issues == []
    row = next(row for row in updated.rows if row.local_ref == 'LS1')
    assert row.disposition == 'map_to_bangumi'
    assert row.selected_target_ref == 'BS1'
