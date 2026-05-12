from src.rename.case_agent.mapping_draft import apply_mapping_patches, build_initial_mapping_draft, compact_mapping_draft, summarize_mapping_draft_coverage, validate_mapping_patch
from src.rename.case_agent.mapping_draft import compute_local_span_partition_coverage
from src.rename.case_agent.models import BangumiItemCard, BangumiSpanCard, CaseDossier, LocalFileCard, LocalSpanCard, MappingDraftPatch


def _dossier() -> CaseDossier:
    local_files = [
        LocalFileCard(ref='F1', path='pkg/a1.mkv', is_main=True),
        LocalFileCard(ref='F2', path='pkg/a2.mkv', is_main=True),
        LocalFileCard(ref='F3', path='pkg/b1.mkv', is_main=True),
    ]
    local_spans = [
        LocalSpanCard(ref='LS_PACKAGE', span_scope='package', file_refs=['F1', 'F2', 'F3'], file_ref_count=3),
        LocalSpanCard(ref='LS1', span_scope='directory', file_refs=['F1', 'F2'], file_ref_count=2),
        LocalSpanCard(ref='LS2', span_scope='token_segment', file_refs=['F3'], file_ref_count=1),
    ]
    bangumi_spans = [
        BangumiSpanCard(ref='BS1', detail_equivalent=True, target_refs=['BE1']),
        BangumiSpanCard(ref='BS2', detail_equivalent=False, target_refs=['BE2']),
    ]
    bangumi_items = [BangumiItemCard(ref='BE1', subject_ref='SUB1', item_kind='episode')]
    return CaseDossier(local_files=local_files, local_span_cards=local_spans, bangumi_span_cards=bangumi_spans, bangumi_items=bangumi_items)


def _dossier_with_108_main_files() -> CaseDossier:
    local_files = [LocalFileCard(ref=f'F{i}', path=f'pkg/{i}.mkv', is_main=True) for i in range(1, 109)]
    local_spans = [
        LocalSpanCard(ref='LS_PACKAGE', span_scope='package', file_refs=[f'F{i}' for i in range(1, 109)], file_ref_count=108),
        LocalSpanCard(ref='LS1', span_scope='directory', file_refs=[f'F{i}' for i in range(1, 13)], file_ref_count=12),
    ]
    return CaseDossier(local_files=local_files, local_span_cards=local_spans)


def test_initial_draft_uses_child_spans_and_detail_targets():
    dossier = _dossier()
    draft = build_initial_mapping_draft(dossier)

    assert [row.local_ref for row in draft.rows] == ['LS1', 'LS2']
    assert all(row.local_ref_kind == 'span' for row in draft.rows)
    assert draft.rows[0].candidate_target_refs == ['BS1']


def test_initial_draft_builds_rows_for_all_child_spans():
    local_files = [LocalFileCard(ref=f'F{i}', path=f'pkg/{i}.mkv', is_main=True) for i in range(1, 10)]
    local_spans = [
        LocalSpanCard(ref='LS_PACKAGE', span_scope='package', file_refs=[f'F{i}' for i in range(1, 10)], file_ref_count=9),
        *[LocalSpanCard(ref=f'LS{i}', span_scope='directory', file_refs=[f'F{i}'], file_ref_count=1) for i in range(1, 8)],
    ]
    dossier = CaseDossier(local_files=local_files, local_span_cards=local_spans)

    draft = build_initial_mapping_draft(dossier)

    assert len(draft.rows) == 7
    assert [row.local_ref for row in draft.rows] == [f'LS{i}' for i in range(1, 8)]


def test_valid_span_mapping_patch_is_applied():
    dossier = _dossier()
    draft = build_initial_mapping_draft(dossier)
    patch = MappingDraftPatch(op='propose_span_mapping', local_ref='LS1', target_span_ref='BS1', support_refs=['F1'], reason='mapped')

    updated, issues = apply_mapping_patches(draft, [patch], dossier)

    assert issues == []
    row = next(row for row in updated.rows if row.local_ref == 'LS1')
    assert row.selected_target_ref == 'BS1'
    assert row.selected_target_kind == 'span'
    assert row.mapping_mode == 'span_by_index'
    assert row.status == 'proposed'


def test_span_ref_in_target_ref_is_canonicalized_to_span_mapping():
    dossier = _dossier()
    draft = build_initial_mapping_draft(dossier)
    patch = MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_ref='BS1', support_refs=['F1'], reason='mapped')

    updated, issues = apply_mapping_patches(draft, [patch], dossier)

    assert issues == []
    row = next(row for row in updated.rows if row.local_ref == 'LS1')
    assert row.selected_target_ref == 'BS1'
    assert row.selected_target_kind == 'span'
    assert row.mapping_mode == 'span_by_index'


def test_item_ref_in_target_span_ref_is_canonicalized_to_explicit_mapping():
    dossier = _dossier()
    draft = build_initial_mapping_draft(dossier)
    patch = MappingDraftPatch(op='map_to_bangumi', local_ref='LS2', target_span_ref='BE1', support_refs=['LS2', 'BE1'], reason='singleton mapped')

    updated, issues = apply_mapping_patches(draft, [patch], dossier)

    assert issues == []
    row = next(row for row in updated.rows if row.local_ref == 'LS2')
    assert row.selected_target_ref == 'BE1'
    assert row.selected_target_kind == 'item'
    assert row.mapping_mode == 'explicit'


def test_bes_span_ref_is_not_canonicalized_to_explicit_item_mapping():
    dossier = _dossier()
    dossier = dossier.model_copy(update={
        'bangumi_span_cards': [
            *dossier.bangumi_span_cards,
            BangumiSpanCard(ref='BES_LS1_1', detail_equivalent=True, target_refs=['BE1', 'BE2'], target_ref_count=2),
        ],
    })
    draft = build_initial_mapping_draft(dossier)
    patch = MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_span_ref='BES_LS1_1', support_refs=['LS1', 'BES_LS1_1'], reason='span mapped')

    updated, issues = apply_mapping_patches(draft, [patch], dossier)

    assert issues == []
    row = next(row for row in updated.rows if row.local_ref == 'LS1')
    assert row.selected_target_ref == 'BES_LS1_1'
    assert row.selected_target_kind == 'span'
    assert row.mapping_mode == 'span_by_index'


def test_bes_span_ref_in_both_target_fields_is_canonicalized_to_span_mapping():
    dossier = _dossier()
    dossier = dossier.model_copy(update={
        'bangumi_span_cards': [
            *dossier.bangumi_span_cards,
            BangumiSpanCard(ref='BES_LS1_1', detail_equivalent=True, target_refs=['BE1', 'BE2'], target_ref_count=2),
        ],
    })
    draft = build_initial_mapping_draft(dossier)
    patch = MappingDraftPatch(
        op='map_to_bangumi',
        local_ref='LS1',
        target_ref='BES_LS1_1',
        target_span_ref='BES_LS1_1',
        mapping_mode='explicit',
        support_refs=['LS1', 'BES_LS1_1'],
        reason='span mapped',
    )

    updated, issues = apply_mapping_patches(draft, [patch], dossier)

    assert issues == []
    row = next(row for row in updated.rows if row.local_ref == 'LS1')
    assert row.selected_target_ref == 'BES_LS1_1'
    assert row.selected_target_kind == 'span'
    assert row.mapping_mode == 'span_by_index'


def test_row_ref_patch_local_ref_is_canonicalized_to_row_local_ref():
    dossier = _dossier()
    draft = build_initial_mapping_draft(dossier)
    patch = MappingDraftPatch(op='map_to_bangumi', local_ref='MDR1', target_span_ref='BS1', support_refs=['F1'], reason='mapped')

    updated, issues = apply_mapping_patches(draft, [patch], dossier)

    assert issues == []
    row = next(row for row in updated.rows if row.row_ref == 'MDR1')
    assert row.local_ref == 'LS1'
    assert row.selected_target_ref == 'BS1'


def test_row_ref_support_ref_is_canonicalized_to_row_local_ref():
    dossier = _dossier()
    draft = build_initial_mapping_draft(dossier)
    patch = MappingDraftPatch(op='map_to_bangumi', local_ref='MDR1', target_span_ref='BS1', support_refs=['MDR1'], reason='mapped')

    updated, issues = apply_mapping_patches(draft, [patch], dossier)

    assert issues == []
    row = next(row for row in updated.rows if row.row_ref == 'MDR1')
    assert row.support_refs == ['LS1', 'BS1']


def test_retract_mapping_resets_disposition_to_open():
    dossier = _dossier()
    draft = build_initial_mapping_draft(dossier)

    mapped, issues = apply_mapping_patches(draft, [
        MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_span_ref='BS1', support_refs=['LS1', 'BS1'], reason='mapped'),
    ], dossier)
    updated, issues = apply_mapping_patches(mapped, [
        MappingDraftPatch(op='retract_mapping', local_ref='LS1', reason='repair'),
    ], dossier)

    assert issues == []
    row = next(row for row in updated.rows if row.local_ref == 'LS1')
    assert row.status == 'open'
    assert row.disposition == 'open'
    assert row.selected_target_ref == ''


def test_needs_more_evidence_reason_kind_is_canonicalized():
    dossier = _dossier()
    draft = build_initial_mapping_draft(dossier)
    patch = MappingDraftPatch(op='needs_more_evidence', local_ref='LS1', reason_kind='insufficient_evidence', reason='ambiguous')

    updated, issues = apply_mapping_patches(draft, [patch], dossier)

    assert issues == []
    row = next(row for row in updated.rows if row.local_ref == 'LS1')
    assert row.disposition == 'needs_more_evidence'
    assert row.reason_kind == 'ambiguous_candidate'


def test_hidden_target_span_ref_is_rejected():
    dossier = _dossier()
    draft = build_initial_mapping_draft(dossier)
    patch = MappingDraftPatch(op='propose_span_mapping', local_ref='LS1', target_span_ref='BS2')

    issues = validate_mapping_patch(patch, dossier, draft)

    assert any(issue.issue_code == 'unknown_target_span_ref' for issue in issues)


def test_compact_draft_hides_full_rows_and_file_refs():
    dossier = _dossier()
    draft = build_initial_mapping_draft(dossier)
    compact = compact_mapping_draft(draft)

    assert compact['row_count'] == 2
    assert compact['status_counts']['open'] == 2
    assert 'rows' not in compact
    assert 'file_refs' not in compact
    assert compact['local_ref_samples'] == ['LS1', 'LS2']


def test_coverage_summary_marks_all_main_files_covered_when_child_rows_cover_everything():
    dossier = _dossier()
    draft = build_initial_mapping_draft(dossier)

    coverage = summarize_mapping_draft_coverage(dossier, draft)

    assert coverage.missing_main_file_count == 0
    assert coverage.overlap_count == 0
    assert coverage.partition_complete is True


def test_coverage_summary_marks_ls1_only_gap_on_108_main_files():
    dossier = _dossier_with_108_main_files()
    draft = build_initial_mapping_draft(dossier)

    coverage = summarize_mapping_draft_coverage(dossier, draft)

    assert coverage.main_file_count == 108
    assert coverage.covered_main_file_count == 12
    assert coverage.missing_main_file_count == 96
    assert coverage.partition_complete is False


def test_package_span_is_ignored_unless_it_is_the_only_span():
    dossier = _dossier()
    draft = build_initial_mapping_draft(dossier)

    assert 'LS_PACKAGE' not in [row.local_ref for row in draft.rows]


def test_compact_includes_coverage_diagnostics_without_full_refs():
    dossier = _dossier_with_108_main_files()
    draft = build_initial_mapping_draft(dossier)

    compact = compact_mapping_draft(draft, dossier)

    assert compact['coverage_summary']['missing_main_file_count'] == 96
    assert compact['coverage_summary']['partition_complete'] is False
    assert 'file_refs' not in str(compact)


def test_mapping_draft_coverage_single_source():
    dossier = _dossier_with_108_main_files()
    draft = build_initial_mapping_draft(dossier)
    coverage = compute_local_span_partition_coverage(dossier, draft)

    assert coverage['mapping_draft_row_count'] == 1
    assert coverage['mapping_draft_covered_main_count'] == 12
    assert coverage['mapping_draft_missing_main_count'] == 96
    assert coverage['span_covered_main_file_count'] == 12
    assert coverage['span_missing_main_file_count'] == 96


def test_mapping_draft_coverage_overlap_counts_cross_row_duplicates_only():
    local_files = [
        LocalFileCard(ref='F1', path='pkg/a1.mkv', is_main=True),
        LocalFileCard(ref='F2', path='pkg/a2.mkv', is_main=True),
        LocalFileCard(ref='F3', path='pkg/a3.mkv', is_main=True),
    ]
    local_spans = [
        LocalSpanCard(ref='LS_PACKAGE', span_scope='package', file_refs=['F1', 'F2', 'F3'], file_ref_count=3),
        LocalSpanCard(ref='LS1', span_scope='directory', file_refs=['F1', 'F2'], file_ref_count=2),
        LocalSpanCard(ref='LS2', span_scope='directory', file_refs=['F2', 'F3'], file_ref_count=2),
    ]
    dossier = CaseDossier(local_files=local_files, local_span_cards=local_spans)
    draft = build_initial_mapping_draft(dossier)

    coverage = compute_local_span_partition_coverage(dossier, draft)

    assert coverage['covered_main_file_count'] == 3
    assert coverage['missing_main_file_count'] == 0
    assert coverage['overlap_count'] == 1
    assert coverage['partition_complete'] is False


def test_local_span_partition_coverage_uses_spans_when_no_draft_exists():
    dossier = _dossier()
    coverage = compute_local_span_partition_coverage(dossier, None)

    assert coverage['coverage_source'] == 'local_spans'
    assert coverage['covered_main_file_count'] == 3
    assert coverage['missing_main_file_count'] == 0
    assert coverage['span_partition_complete'] is True
    assert coverage['mapping_draft_row_count'] == 0
    assert coverage['mapping_draft_covered_main_count'] == 0
    assert coverage['mapping_draft_missing_main_count'] == 3
