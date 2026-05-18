from src.rename.case_agent.assignment_expander import expand_mapping_draft
from src.rename.case_agent.models import (
    BangumiItemCard,
    CaseContract,
    CaseDossier,
    CaseHeader,
    EvidenceBatchResult,
    EvidenceRequestResult,
    MappingDraft,
    MappingDraftRow,
    LocalFileCard,
    LocalSpanCard,
    BangumiSpanCard,
)
from src.rename.case_agent.verifier import verify_mapping_draft_accounting


def target_evidence() -> list[EvidenceBatchResult]:
    return [
        EvidenceBatchResult(
            batch_ref='EB1',
            status='accepted',
            request_results=[
                EvidenceRequestResult(
                    request_ref='REQ_SUBJECT_SEARCH_QC1',
                    request_type='subject_search',
                    accepted=True,
                    response_refs=[],
                )
            ],
        )
    ]


def make_dossier() -> CaseDossier:
    main_refs = [f'LF{i}' for i in range(1, 11)]
    supplemental_refs = ['LF11', 'LF12']
    target_refs = [f'BE{i}' for i in range(1, 11)]
    return CaseDossier(
        header=CaseHeader(case_id='CASE-ACC'),
        local_files=[LocalFileCard(ref=ref, is_main=True) for ref in main_refs] + [LocalFileCard(ref=ref, is_main=False) for ref in supplemental_refs],
        contract=CaseContract(main_file_refs=main_refs, supplemental_file_refs=supplemental_refs, allowed_file_refs=[*main_refs, *supplemental_refs], visible_target_refs=target_refs),
        local_span_cards=[LocalSpanCard(ref='LS1', file_refs=main_refs, file_ref_count=10, file_ref_range=main_refs, file_ref_samples=main_refs[:3])],
        bangumi_items=[BangumiItemCard(ref=ref, subject_ref='S1') for ref in target_refs],
        bangumi_span_cards=[BangumiSpanCard(ref='BS1', subject_ref='S1', group_ref='G1', target_refs=target_refs, target_ref_count=10, target_ref_range=target_refs, target_ref_samples=target_refs[:3], detail_equivalent=True)],
        detailed_card_refs=target_refs,
        assignable_target_refs=target_refs,
        seen_detail_refs=target_refs,
    )


def draft_with_rows(rows):
    return MappingDraft(draft_ref='MD1', rows=rows, version=1)


def test_accounted_for_ready_and_expand_only_mapped_rows():
    dossier = make_dossier()
    rows = [
        MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', disposition='map_to_bangumi', selected_target_ref='BS1', selected_target_kind='span', mapping_mode='span_by_index', support_refs=['LS1', 'BS1'])
    ] + [
        MappingDraftRow(row_ref='R11', local_ref='LF11', disposition='non_bangumi_or_supplemental', reason_kind='sample', support_refs=['LS1']),
        MappingDraftRow(row_ref='R12', local_ref='LF12', disposition='non_bangumi_or_supplemental', reason_kind='sample', support_refs=['LS1']),
    ]
    draft = draft_with_rows(rows)
    result = verify_mapping_draft_accounting(dossier, draft)
    assert result.passed is True
    expanded, issues = expand_mapping_draft(dossier, draft)
    assert not issues
    assert len(expanded) == 10


def test_needs_more_evidence_not_ready_and_not_accepted():
    dossier = make_dossier()
    rows = [
        MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', disposition='map_to_bangumi', selected_target_ref='BS1', selected_target_kind='span', mapping_mode='span_by_index', support_refs=['LS1', 'BS1'])
    ] + [
        MappingDraftRow(row_ref='R11', local_ref='LF11', disposition='needs_more_evidence', reason_kind='missing_target_span', support_refs=['LS1']),
        MappingDraftRow(row_ref='R12', local_ref='LF12', disposition='needs_more_evidence', reason_kind='missing_target_span', support_refs=['LS1']),
    ]
    result = verify_mapping_draft_accounting(dossier, draft_with_rows(rows))
    assert result.passed is False
    assert any(issue.issue_code in {'not_ready', 'fail_closed', 'coverage_error'} for issue in result.issues)


def test_supplemental_with_target_ref_invalid():
    dossier = make_dossier()
    draft = draft_with_rows([
        MappingDraftRow(row_ref='R11', local_ref='LF11', disposition='non_bangumi_or_supplemental', selected_target_ref='BE1', reason_kind='sample', support_refs=['LS1']),
    ])
    assert any(issue.issue_code == 'invalid_target' for issue in verify_mapping_draft_accounting(dossier, draft).issues)


def test_supplemental_without_support_invalid():
    dossier = make_dossier()
    draft = draft_with_rows([
        MappingDraftRow(row_ref='R11', local_ref='LF11', disposition='non_bangumi_or_supplemental', reason_kind='sample'),
    ])
    assert any(issue.issue_code == 'missing_support_refs' for issue in verify_mapping_draft_accounting(dossier, draft).issues)


def test_duplicate_mapped_target_blocked():
    dossier = make_dossier()
    draft = draft_with_rows([
        MappingDraftRow(row_ref='R1', local_ref='LF1', disposition='map_to_bangumi', selected_target_ref='BE1', selected_target_kind='span', mapping_mode='span_by_index', support_refs=['LS1']),
        MappingDraftRow(row_ref='R2', local_ref='LF2', disposition='map_to_bangumi', selected_target_ref='BE1', selected_target_kind='span', mapping_mode='span_by_index', support_refs=['LS1']),
    ])
    duplicate = next(issue for issue in verify_mapping_draft_accounting(dossier, draft).issues if issue.issue_code == 'duplicate_target')
    assert duplicate.related_refs[:3] == ['R1', 'R2', 'BE1']


def test_explicit_singleton_be_mapping_expands():
    dossier = make_dossier()
    dossier.local_span_cards.append(LocalSpanCard(ref='LS_SINGLE', file_refs=['LF1'], file_ref_count=1))
    draft = draft_with_rows([
        MappingDraftRow(row_ref='R1', local_ref='LS_SINGLE', local_ref_kind='span', disposition='map_to_bangumi', selected_target_ref='BE1', selected_target_kind='item', mapping_mode='explicit', support_refs=['LS_SINGLE', 'BE1']),
    ])
    expanded, issues = expand_mapping_draft(dossier, draft)
    assert not issues
    assert len(expanded) == 1
    assert expanded[0].file_ref == 'LF1'
    assert expanded[0].target_ref == 'BE1'


def test_explicit_singleton_be_mapping_rejects_hidden_ref():
    dossier = make_dossier()
    dossier.local_span_cards.append(LocalSpanCard(ref='LS_SINGLE', file_refs=['LF1'], file_ref_count=1))
    draft = draft_with_rows([
        MappingDraftRow(row_ref='R1', local_ref='LS_SINGLE', local_ref_kind='span', disposition='map_to_bangumi', selected_target_ref='BE999', selected_target_kind='item', mapping_mode='explicit', support_refs=['LS_SINGLE', 'BE999']),
    ])
    assert any(issue.issue_code == 'invalid_target' for issue in verify_mapping_draft_accounting(dossier, draft).issues)


def test_mapped_row_without_expandable_target_is_not_ready():
    dossier = make_dossier()
    draft = draft_with_rows([
        MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', disposition='map_to_bangumi', status='proposed'),
    ])

    result = verify_mapping_draft_accounting(dossier, draft)

    assert result.passed is False
    assert any(issue.issue_code == 'invalid_mapping_mode' for issue in result.issues)


def test_supplemental_rows_expand_to_unaligned_accounting_assignments():
    dossier = make_dossier()
    dossier.contract.main_file_refs = ['LF11', 'LF12']
    dossier.contract.supplemental_file_refs = []
    dossier.local_files = [
        LocalFileCard(ref='LF11', path='Show NCOP.mkv', is_main=True, file_kind='video'),
        LocalFileCard(ref='LF12', path='Show NCED.mkv', is_main=True, file_kind='video'),
    ]
    draft = draft_with_rows([
        MappingDraftRow(row_ref='R11', local_ref='LF11', disposition='non_bangumi_or_supplemental', reason_kind='creditless_op_ed', support_refs=['LF11']),
        MappingDraftRow(row_ref='R12', local_ref='LF12', disposition='non_bangumi_or_supplemental', reason_kind='creditless_op_ed', support_refs=['LF12']),
    ])

    expanded, issues = expand_mapping_draft(dossier, draft)

    assert issues == []
    assert [item.file_ref for item in expanded] == ['LF11', 'LF12']
    assert [item.target_ref for item in expanded] == ['UNALIGNED', 'UNALIGNED']


def test_multi_file_supplemental_is_validated_without_fixed_semantic_shape_gate():
    dossier = make_dossier()
    dossier.local_files = [
        LocalFileCard(ref=f'LF{i}', path=f'Extras/Show Extra #{i:02d}.mkv', is_main=True, file_kind='video')
        for i in range(1, 11)
    ]
    dossier.local_span_cards = [
        LocalSpanCard(
            ref='LS_REGULAR',
            span_scope='token_segment',
            file_refs=[f'LF{i}' for i in range(1, 11)],
            file_ref_count=10,
                file_ref_samples=['LF1', 'LF2', 'LF10'],
                ordering_basis='episode_token_order',
                episode_token_start=1,
                episode_token_end=10,
                episode_token_count=10,
                title_cues=['Extras'],
            )
        ]
    draft = draft_with_rows([
        MappingDraftRow(row_ref='R1', local_ref='LS_REGULAR', local_ref_kind='span', disposition='non_bangumi_or_supplemental', reason_kind='other_supplemental', support_refs=['LS_REGULAR']),
    ])

    result = verify_mapping_draft_accounting(dossier, draft)

    assert result.passed is True
    assert not any(issue.issue_code == 'regular_main_span_cannot_be_supplemental' for issue in result.issues)


def test_supplemental_reason_kind_is_agent_semantics_not_filename_regex_gate():
    dossier = make_dossier()
    main_refs = [f'LF{i}' for i in range(1, 5)]
    dossier.contract.main_file_refs = main_refs
    dossier.contract.allowed_file_refs = list(main_refs)
    dossier.local_files = [
        LocalFileCard(ref=ref, path=f'SPs/Show SP{index:02d} Theater Manners.mkv', is_main=True, file_kind='video')
        for index, ref in enumerate(main_refs, start=1)
    ]
    dossier.local_span_cards = [
        LocalSpanCard(
            ref='LS_THEATER_MANNERS',
            span_scope='residual',
            file_refs=main_refs,
            file_ref_count=len(main_refs),
            file_ref_samples=['LF1', 'LF2', 'LF4'],
            ordering_basis='unknown',
            title_cues=['SPs', 'Theater Manners'],
        )
    ]
    draft = draft_with_rows([
        MappingDraftRow(
            row_ref='R1',
            local_ref='LS_THEATER_MANNERS',
            local_ref_kind='span',
            disposition='non_bangumi_or_supplemental',
            reason_kind='bonus_video',
            support_refs=['LS_THEATER_MANNERS'],
        ),
    ])

    result = verify_mapping_draft_accounting(dossier, draft)

    assert result.passed is True
    assert not any(issue.issue_code == 'supplemental_reason_not_supported_by_local_text' for issue in result.issues)


def test_singleton_visible_extra_can_be_accepted_as_supplemental():
    dossier = make_dossier()
    dossier.contract.main_file_refs = ['LF1']
    dossier.local_files = [LocalFileCard(ref='LF1', path='Show Non Telop OP.mkv', is_main=True, file_kind='video')]
    dossier.local_span_cards = [
        LocalSpanCard(ref='LS_OP', span_scope='residual', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1'], title_cues=['Non Telop OP'])
    ]
    draft = draft_with_rows([
        MappingDraftRow(row_ref='R1', local_ref='LS_OP', local_ref_kind='span', disposition='non_bangumi_or_supplemental', reason_kind='creditless_op_ed', support_refs=['LS_OP']),
    ])

    result = verify_mapping_draft_accounting(dossier, draft)

    assert result.passed is True


def test_singleton_without_bangumi_target_can_be_accepted_as_target_absent():
    dossier = make_dossier()
    dossier.contract.main_file_refs = ['LF1']
    dossier.local_files = [LocalFileCard(ref='LF1', path='Show OAD.mkv', is_main=True, file_kind='video')]
    dossier.local_span_cards = [
        LocalSpanCard(ref='LS_OAD', span_scope='residual', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1'], title_cues=['OAD'])
    ]
    dossier.bangumi_items = []
    dossier.bangumi_span_cards = []
    dossier.assignable_target_refs = []
    dossier.detailed_card_refs = []
    dossier.seen_detail_refs = []
    dossier.previous_evidence_results = target_evidence()
    draft = draft_with_rows([
        MappingDraftRow(row_ref='R1', local_ref='LS_OAD', local_ref_kind='span', disposition='non_bangumi_or_supplemental', reason_kind='bangumi_target_absent', support_refs=['LS_OAD']),
    ])

    result = verify_mapping_draft_accounting(dossier, draft)

    assert result.passed is True


def test_non_regular_sp_extra_span_can_be_accepted_as_target_absent():
    dossier = make_dossier()
    main_refs = [f'LF{i}' for i in range(1, 7)]
    dossier.contract.main_file_refs = main_refs
    dossier.contract.allowed_file_refs = list(main_refs)
    dossier.local_files = [
        LocalFileCard(ref=ref, path=f'SPs/Show SP{index:02d}.mkv', is_main=True, file_kind='video')
        for index, ref in enumerate(main_refs, start=1)
    ]
    dossier.local_span_cards = [
        LocalSpanCard(
            ref='LS_SP_EXTRA',
            span_scope='residual',
            file_refs=main_refs,
            file_ref_count=len(main_refs),
            file_ref_samples=['LF1', 'LF2', 'LF6'],
            ordering_basis='unknown',
            title_cues=['SPs'],
        )
    ]
    dossier.bangumi_items = []
    dossier.bangumi_span_cards = []
    dossier.assignable_target_refs = []
    dossier.detailed_card_refs = []
    dossier.seen_detail_refs = []
    dossier.previous_evidence_results = target_evidence()
    draft = draft_with_rows([
        MappingDraftRow(row_ref='R1', local_ref='LS_SP_EXTRA', local_ref_kind='span', disposition='non_bangumi_or_supplemental', reason_kind='bangumi_target_absent', support_refs=['LS_SP_EXTRA']),
    ])

    result = verify_mapping_draft_accounting(dossier, draft)

    assert result.passed is True


def test_target_absent_can_override_visible_candidate_when_agent_judges_absent():
    dossier = make_dossier()
    dossier.contract.main_file_refs = ['LF1']
    dossier.local_files = [LocalFileCard(ref='LF1', path='Show OAD.mkv', is_main=True, file_kind='video')]
    dossier.local_span_cards = [
        LocalSpanCard(ref='LS_OAD', span_scope='residual', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1'], title_cues=['OAD'])
    ]
    dossier.bangumi_items = [BangumiItemCard(ref='BE_SPECIAL', subject_ref='S1', item_kind='special')]
    dossier.assignable_target_refs = ['BE_SPECIAL']
    dossier.detailed_card_refs = ['BE_SPECIAL']
    dossier.seen_detail_refs = ['BE_SPECIAL']
    draft = draft_with_rows([
        MappingDraftRow(row_ref='R1', local_ref='LS_OAD', local_ref_kind='span', candidate_target_refs=['BE_SPECIAL'], disposition='non_bangumi_or_supplemental', reason_kind='bangumi_target_absent', support_refs=['LS_OAD']),
    ])

    result = verify_mapping_draft_accounting(dossier, draft)

    assert result.passed is True
    assert result.issues == []


def test_regular_numbered_span_target_absent_is_editor_semantics_not_shape_rejected():
    dossier = make_dossier()
    dossier.local_files = [
        LocalFileCard(ref=f'LF{i}', path=f'Show #{i:02d}.mkv', is_main=True, file_kind='video')
        for i in range(1, 6)
    ]
    dossier.contract.main_file_refs = [f'LF{i}' for i in range(1, 6)]
    dossier.local_span_cards = [
        LocalSpanCard(
            ref='LS_REGULAR',
            span_scope='token_segment',
            file_refs=[f'LF{i}' for i in range(1, 6)],
            file_ref_count=5,
            ordering_basis='episode_token_order',
            episode_token_start=1,
            episode_token_end=5,
            episode_token_count=5,
        )
    ]
    dossier.bangumi_items = []
    dossier.bangumi_span_cards = []
    dossier.assignable_target_refs = []
    dossier.detailed_card_refs = []
    dossier.seen_detail_refs = []
    dossier.previous_evidence_results = target_evidence()
    draft = draft_with_rows([
        MappingDraftRow(row_ref='R1', local_ref='LS_REGULAR', local_ref_kind='span', disposition='non_bangumi_or_supplemental', reason_kind='bangumi_target_absent', support_refs=['LS_REGULAR']),
    ])

    result = verify_mapping_draft_accounting(dossier, draft)

    assert result.passed is True
    assert result.issues == []


def test_duplicate_span_ref_not_blocked_before_expansion():
    dossier = make_dossier()
    dossier.local_span_cards.append(LocalSpanCard(ref='LS2', file_refs=[], file_ref_count=0))
    draft = draft_with_rows([
        MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', disposition='map_to_bangumi', selected_target_ref='BS1', selected_target_kind='span', mapping_mode='span_by_index', support_refs=['LS1', 'BS1']),
        MappingDraftRow(row_ref='R2', local_ref='LS2', local_ref_kind='span', disposition='map_to_bangumi', selected_target_ref='BS1', selected_target_kind='span', mapping_mode='span_by_index', support_refs=['LS2', 'BS1']),
    ])
    issues = verify_mapping_draft_accounting(dossier, draft).issues
    assert not any(issue.ref == 'R2' and issue.issue_code == 'duplicate_target' for issue in issues)


def test_span_mapped_and_supplemental_residual_accounted_for_count_matches_main():
    dossier = make_dossier()
    rows = [
        MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', disposition='map_to_bangumi', selected_target_ref='BS1', selected_target_kind='span', mapping_mode='span_by_index', support_refs=['LS1', 'BS1']),
        MappingDraftRow(row_ref='R2', local_ref='LF11', disposition='non_bangumi_or_supplemental', reason_kind='sample', support_refs=['LS1']),
    ]
    result = verify_mapping_draft_accounting(dossier, draft_with_rows(rows))
    assert result.passed is True
    assert result.issues == []
