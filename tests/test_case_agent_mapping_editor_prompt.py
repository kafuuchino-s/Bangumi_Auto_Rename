from __future__ import annotations

from src.rename.case_agent.mapping_editor import render_mapping_draft_editor_prompt
from src.rename.case_agent.models import (
    BangumiItemCard,
    BangumiRelationCard,
    BangumiSubjectCard,
    BangumiSpanCard,
    CaseBudget,
    CaseHeader,
    CaseDossier,
    MappingDraft,
    MappingDraftEditorOutput,
    MappingDraftPatch,
    MappingDraftRow,
    LocalSpanCard,
    LocalFileCard,
)


def _make_dossier() -> CaseDossier:
    header = CaseHeader(case_id='CASE-1', round_index=0, max_rounds=3)
    budget = CaseBudget()
    local_span_cards = [LocalSpanCard(ref='LS1', span_scope='package', parent_key='p1', season_cue='S1', file_refs=['F1', 'F2'], file_ref_count=2, file_ref_samples=['F1', 'F2'])]
    bangumi_span_cards = [BangumiSpanCard(ref='BES1', subject_ref='BS1', group_ref='BG1', target_refs=['BE1', 'BE2'], target_ref_count=2, target_ref_samples=['BE1', 'BE2'], detail_equivalent=True)]
    return CaseDossier(header=header, budget=budget, local_span_cards=local_span_cards, bangumi_span_cards=bangumi_span_cards)


def test_mapping_draft_editor_prompt_mentions_required_intents() -> None:
    dossier = _make_dossier()
    draft = MappingDraft(rows=[MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', candidate_target_refs=['BES1'])])

    prompt = render_mapping_draft_editor_prompt(dossier, draft)

    assert 'MappingDraftEditor' in prompt
    assert 'MappingDraft' in prompt
    assert 'patch intents' in prompt
    assert 'map_to_bangumi' in prompt
    assert 'mark_non_bangumi_or_supplemental' in prompt
    assert 'needs_more_evidence' in prompt
    assert 'mark_unaligned_fail_closed' in prompt
    assert 'accounted for' in prompt or 'accounted-for' in prompt


def test_mapping_draft_editor_prompt_compacts_visible_refs() -> None:
    dossier = _make_dossier()
    dossier = dossier.model_copy(update={'bangumi_span_cards': [BangumiSpanCard(ref=f'BES{i}', subject_ref='BS1', group_ref='BG1', target_refs=[f'BE{i}'], target_ref_count=1, target_ref_samples=[f'BE{i}'], detail_equivalent=True) for i in range(1, 101)]})
    draft = MappingDraft(rows=[MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', candidate_target_refs=[f'BES{i}' for i in range(1, 101)])])

    prompt = render_mapping_draft_editor_prompt(dossier, draft)

    assert prompt.count('BES') < 40
    assert 'BE1' in prompt
    assert 'BE100' in prompt
    assert 'status_counts' in prompt
    assert 'accounting_summary' in prompt


def test_mapping_draft_editor_prompt_keeps_all_draft_local_spans_and_candidate_context() -> None:
    dossier = CaseDossier(
        header=CaseHeader(case_id='CASE-CONTEXT', round_index=0, max_rounds=3),
        budget=CaseBudget(),
        local_span_cards=[
            LocalSpanCard(ref='LS_PACKAGE', span_scope='package', file_ref_count=9, file_ref_samples=['LF1', 'LF9'], title_cues=['Package']),
            *[
                LocalSpanCard(
                    ref=f'LS{i}',
                    span_scope='directory',
                    file_ref_count=1,
                    file_ref_samples=[f'LF{i}'],
                    episode_token_start=i,
                    episode_token_end=i,
                    episode_token_count=1,
                    title_cues=[f'Local span {i}'],
                )
                for i in range(1, 10)
            ],
        ],
        bangumi_subjects=[
            BangumiSubjectCard(ref='BSA', title='Subject A', relation_refs=['BR1']),
            BangumiSubjectCard(ref='BSB', title='Subject B', relation_refs=['BR1']),
        ],
        bangumi_relations=[BangumiRelationCard(ref='BR1', relation_kind='sequel', source_subject_ref='BSA', target_subject_ref='BSB')],
        bangumi_span_cards=[
            BangumiSpanCard(ref='BES_LS5_A', subject_ref='BSA', target_refs=['BE5A'], target_ref_count=1, target_ref_samples=['BE5A'], title_samples=['Candidate A'], detail_equivalent=True, source_request_ref='REQ_TARGET_SPAN_LS5'),
            BangumiSpanCard(ref='BES_LS5_B', subject_ref='BSB', target_refs=['BE5B'], target_ref_count=1, target_ref_samples=['BE5B'], title_samples=['Candidate B'], detail_equivalent=True, source_request_ref='REQ_TARGET_SPAN_LS5'),
        ],
    )
    draft = MappingDraft(rows=[
        MappingDraftRow(row_ref=f'MDR{i}', local_ref=f'LS{i}', local_ref_kind='span', candidate_target_refs=(['BES_LS5_A', 'BES_LS5_B'] if i == 5 else []))
        for i in range(1, 10)
    ])

    prompt = render_mapping_draft_editor_prompt(dossier, draft)

    assert 'LS5' in prompt
    assert 'Local span 5' in prompt
    assert 'BES_LS5_A' in prompt
    assert 'BES_LS5_B' in prompt
    assert 'bangumi_subject_cards' in prompt
    assert 'bangumi_relation_cards' in prompt
    assert 'BR1' in prompt
    assert 'subject_ref' in prompt
    assert 'source_request_ref' in prompt
    assert 'sort_start' in prompt
    assert 'ep_start' in prompt


def test_mapping_draft_editor_prompt_compacts_draft_candidates() -> None:
    dossier = _make_dossier()
    draft = MappingDraft(rows=[MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', candidate_target_refs=[f'BES{i}' for i in range(1, 101)], support_refs=[f'BES{i}' for i in range(1, 101)])])

    prompt = render_mapping_draft_editor_prompt(dossier, draft)

    assert prompt.count('BES') < 50
    assert 'candidate_target_refs' in prompt
    assert 'selected_target_ref' in prompt
    assert 'mapping_mode' in prompt
    assert 'support_refs' in prompt
    assert 'full dump' not in prompt.lower()


def test_mapping_draft_editor_prompt_accounts_for_contract_and_limits() -> None:
    dossier = _make_dossier()
    draft = MappingDraft(rows=[MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span')])

    prompt = render_mapping_draft_editor_prompt(dossier, draft)

    assert 'accepted does **not** require every row mapped' in prompt.lower() or 'accepted does not require every row mapped' in prompt.lower()
    assert 'every row to be accounted for' in prompt.lower() or 'every row be accounted for' in prompt.lower()
    assert 'silent ignore' in prompt.lower()
    assert 'supplemental' in prompt.lower()
    assert 'support refs' in prompt.lower()
    assert 'creditless_op_ed' in prompt
    assert 'pv_cm' in prompt
    assert 'travel/location' in prompt
    assert 'reason_kind=making_of' in prompt
    assert 'mark_non_bangumi_or_supplemental' in prompt
    assert 'sort=0' in prompt


def test_mapping_draft_editor_models_are_serializable() -> None:
    output = MappingDraftEditorOutput(
        patches=[MappingDraftPatch(op='propose_span_mapping', local_ref='LS1', target_span_ref='BES1', mapping_mode='span_by_index', support_refs=['LS1', 'BES1'], reason='detail-equivalent span')],
    )

    dumped = output.model_dump(mode='json')
    roundtrip = MappingDraftEditorOutput.model_validate(dumped)

    assert roundtrip.patches[0].op == 'propose_span_mapping'
    assert roundtrip.patches[0].target_span_ref == 'BES1'


def test_mapping_draft_editor_prompt_includes_special_singleton_context_without_subtitle_or_size_requirements() -> None:
    dossier = CaseDossier(
        header=CaseHeader(case_id='CASE-SPECIAL', round_index=0, max_rounds=3),
        budget=CaseBudget(),
        local_files=[LocalFileCard(ref='LF1', path='pkg/Mushishi Tokubetsu Hen Hihamu Kage.mkv', parent_display='Mushishi Zoku Shou', label='Mushishi Tokubetsu Hen Hihamu Kage.mkv', is_main=True, file_kind='video')],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='residual', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1'], title_cues=['Mushishi Tokubetsu Hen Hihamu Kage'])],
        bangumi_items=[BangumiItemCard(ref='BE_SPECIAL', subject_ref='BS2', item_kind='special', title='Hihamu Kage', name='日蝕む翳', source_form_hint='special', relation_to_main='番外篇')],
        assignable_target_refs=['BE_SPECIAL'],
        detailed_card_refs=['BE_SPECIAL'],
        seen_detail_refs=['BE_SPECIAL'],
    )
    draft = MappingDraft(rows=[MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', candidate_target_refs=['BE_SPECIAL'])])

    prompt = render_mapping_draft_editor_prompt(dossier, draft)

    assert 'local_singleton_context' in prompt
    assert 'bangumi_item_cards' in prompt
    assert 'BE_SPECIAL' in prompt
    assert 'Mushishi Tokubetsu Hen Hihamu Kage.mkv' in prompt
    assert 'Title overlap alone is not sufficient' in prompt
    assert 'absence must not block' in prompt
    assert 'concrete Bangumi special episode item' in prompt
    assert 'source form is supporting evidence' in prompt


def test_mapping_draft_editor_prompt_exposes_singleton_filename_anchors_and_row_candidates() -> None:
    dossier = CaseDossier(
        header=CaseHeader(case_id='CASE-SPECIAL-CANDIDATES', round_index=0, max_rounds=3),
        budget=CaseBudget(),
        local_files=[
            LocalFileCard(
                ref='LF1',
                path='pkg/[KTXP][Mushishi Tokubetsu Hen_Hihamu Kage].mkv',
                parent_display='Mushishi Zoku Shou',
                label='[KTXP][Mushishi Tokubetsu Hen_Hihamu Kage].mkv',
                is_main=True,
                file_kind='video',
                size_bytes=2304584703,
            )
        ],
        local_span_cards=[
            LocalSpanCard(
                ref='LS1',
                span_scope='residual',
                file_refs=['LF1'],
                file_ref_count=1,
                file_ref_samples=['LF1'],
                title_cues=['Mushishi Tokubetsu Hen Hihamu Kage'],
            )
        ],
        bangumi_items=[
            BangumiItemCard(ref='BE_SPECIAL_A', subject_ref='BS2', item_kind='special', title='Another Special', name='Another Special', source_form_hint='special', relation_to_main='side_story'),
            BangumiItemCard(ref='BE_SPECIAL_B', subject_ref='BS2', item_kind='special', title='Hihamu Kage', name='Hihamu Kage', source_form_hint='special', relation_to_main='side_story'),
            BangumiItemCard(ref='BE_SPECIAL_C', subject_ref='BS2', item_kind='movie', title='Suzu no Shizuku', name='Suzu no Shizuku', synthetic=True, subject_level_target='true', source_form_hint='movie', relation_to_main='sequel'),
        ],
        assignable_target_refs=['BE_SPECIAL_A', 'BE_SPECIAL_B', 'BE_SPECIAL_C'],
        detailed_card_refs=['BE_SPECIAL_A', 'BE_SPECIAL_B', 'BE_SPECIAL_C'],
        seen_detail_refs=['BE_SPECIAL_A', 'BE_SPECIAL_B', 'BE_SPECIAL_C'],
    )
    draft = MappingDraft(rows=[
        MappingDraftRow(row_ref='R1', local_ref='LS1', local_ref_kind='span', candidate_target_refs=['BE_SPECIAL_A', 'BE_SPECIAL_B', 'BE_SPECIAL_C'])
    ])

    prompt = render_mapping_draft_editor_prompt(dossier, draft)

    assert 'local_singleton_context' in prompt
    assert 'candidate_item_cards' in prompt
    assert 'bracket_segments' in prompt
    assert 'Mushishi Tokubetsu Hen_Hihamu Kage' in prompt
    assert 'BE_SPECIAL_B' in prompt
    assert 'BE_SPECIAL_C' in prompt
    assert 'required_singleton_comparison_rows' in prompt
    assert '2304584703' in prompt
    assert 'filename anchors' in prompt


def test_mapping_draft_editor_prompt_inlines_candidate_subject_context() -> None:
    dossier = CaseDossier(
        header=CaseHeader(case_id='CASE-SPECIAL-SUBJECT-CONTEXT', round_index=0, max_rounds=3),
        budget=CaseBudget(),
        local_files=[
            LocalFileCard(
                ref='LF1',
                path='pkg/[KTXP][Mushishi Tokubetsu Hen_Hihamu Kage].mkv',
                parent_display='Mushishi Zoku Shou',
                label='[KTXP][Mushishi Tokubetsu Hen_Hihamu Kage].mkv',
                is_main=True,
                file_kind='video',
            )
        ],
        local_span_cards=[
            LocalSpanCard(
                ref='LS1',
                span_scope='residual',
                file_refs=['LF1'],
                file_ref_count=1,
                file_ref_samples=['LF1'],
                title_cues=['Mushishi Tokubetsu Hen Hihamu Kage'],
            )
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref='BS_HIHAMU',
                subject_id=2070,
                name='蟲師 特別篇「日蝕む翳」',
                name_cn='虫师 特别篇 日蚀之翳',
                summary_short='日蝕む翳 special subject',
                source_form_hint='special',
                relation_to_main='side_story',
            )
        ],
        bangumi_items=[
            BangumiItemCard(
                ref='BE55',
                subject_ref='BS_HIHAMU',
                item_kind='special',
                title='虫师 特别篇 日蚀之翳',
                name='蟲師 特別篇「日蝕む翳」',
                name_cn='虫师 特别篇 日蚀之翳',
                synthetic=True,
                subject_level_target='true',
                source_form_hint='special',
                relation_to_main='side_story',
            )
        ],
        assignable_target_refs=['BE55'],
        detailed_card_refs=['BE55'],
        seen_detail_refs=['BE55'],
    )
    draft = MappingDraft(rows=[MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span', candidate_target_refs=['BE55'])])

    prompt = render_mapping_draft_editor_prompt(dossier, draft)

    assert 'subject_card' in prompt
    assert 'BS_HIHAMU' in prompt
    assert '虫师 特别篇 日蚀之翳' in prompt
    assert '蟲師 特別篇' in prompt


def test_mapping_draft_editor_prompt_lists_required_singleton_comparison_rows() -> None:
    dossier = CaseDossier(
        header=CaseHeader(case_id='CASE-SPECIAL-COMPARISON-CHECKLIST', round_index=0, max_rounds=3),
        budget=CaseBudget(),
        local_files=[LocalFileCard(ref='LF1', path='pkg/Special One.mkv', is_main=True, file_kind='video')],
        local_span_cards=[
            LocalSpanCard(
                ref='LS1',
                span_scope='residual',
                file_refs=['LF1'],
                file_ref_count=1,
                file_ref_samples=['LF1'],
                title_cues=['Special One'],
            )
        ],
        bangumi_items=[
            BangumiItemCard(ref='BE_A', subject_ref='BS1', item_kind='special', title='Special A', source_form_hint='special'),
            BangumiItemCard(ref='BE_B', subject_ref='BS1', item_kind='special', title='Special B', source_form_hint='special'),
        ],
        assignable_target_refs=['BE_A', 'BE_B'],
        detailed_card_refs=['BE_A', 'BE_B'],
        seen_detail_refs=['BE_A', 'BE_B'],
    )
    draft = MappingDraft(rows=[MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span', candidate_target_refs=['BE_A', 'BE_B'])])

    prompt = render_mapping_draft_editor_prompt(dossier, draft)

    assert 'required_singleton_comparison_rows' in prompt
    assert 'MDR1' in prompt
    assert 'BE_A' in prompt
    assert 'BE_B' in prompt
    assert 'mechanical checklist' in prompt


def test_mapping_draft_editor_prompt_lists_singleton_target_conflict_sets() -> None:
    dossier = CaseDossier(
        header=CaseHeader(case_id='CASE-SPECIAL-CONFLICT-SET', round_index=0, max_rounds=3),
        budget=CaseBudget(),
        local_files=[
            LocalFileCard(ref='LF1', path='pkg/Special One.mkv', is_main=True, file_kind='video'),
            LocalFileCard(ref='LF2', path='pkg/Special Two.mkv', is_main=True, file_kind='video'),
        ],
        local_span_cards=[
            LocalSpanCard(ref='LS1', span_scope='residual', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1'], title_cues=['Special One']),
            LocalSpanCard(ref='LS2', span_scope='residual', file_refs=['LF2'], file_ref_count=1, file_ref_samples=['LF2'], title_cues=['Special Two']),
        ],
        bangumi_items=[
            BangumiItemCard(ref='BE_SHARED', subject_ref='BS1', item_kind='special', title='Special Two', source_form_hint='special'),
            BangumiItemCard(ref='BE_DISTINCT', subject_ref='BS1', item_kind='special', title='Special One', source_form_hint='special'),
        ],
        assignable_target_refs=['BE_SHARED', 'BE_DISTINCT'],
        detailed_card_refs=['BE_SHARED', 'BE_DISTINCT'],
        seen_detail_refs=['BE_SHARED', 'BE_DISTINCT'],
    )
    draft = MappingDraft(rows=[
        MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span', candidate_target_refs=['BE_SHARED', 'BE_DISTINCT']),
        MappingDraftRow(
            row_ref='MDR2',
            local_ref='LS2',
            local_ref_kind='span',
            candidate_target_refs=['BE_SHARED'],
            selected_target_ref='BE_SHARED',
            selected_target_kind='item',
            mapping_mode='explicit',
            status='proposed',
            disposition='map_to_bangumi',
        ),
    ])

    prompt = render_mapping_draft_editor_prompt(dossier, draft)

    assert 'singleton_target_conflict_sets' in prompt
    assert 'mutually exclusive target ownership' in prompt
    assert 'BE_SHARED' in prompt
    assert 'MDR1' in prompt
    assert 'MDR2' in prompt
    assert 'selected_by_row_refs' in prompt


def test_mapping_draft_editor_prompt_keeps_middle_special_item_candidates() -> None:
    item_refs = [f'BE{i}' for i in range(21, 31)]
    dossier = CaseDossier(
        header=CaseHeader(case_id='CASE-SPECIAL-MIDDLE-CANDIDATE', round_index=0, max_rounds=3),
        budget=CaseBudget(),
        local_files=[
            LocalFileCard(
                ref='LF1',
                path='pkg/[KTXP][Mushishi Tokubetsu Hen_Odoro no Michi].mkv',
                parent_display='Mushishi Zoku Shou Vol.3',
                label='[KTXP][Mushishi Tokubetsu Hen_Odoro no Michi].mkv',
                is_main=True,
                file_kind='video',
            )
        ],
        local_span_cards=[
            LocalSpanCard(
                ref='LS4',
                span_scope='residual',
                file_refs=['LF1'],
                file_ref_count=1,
                file_ref_samples=['LF1'],
                title_cues=['Mushishi Tokubetsu Hen Odoro no Michi'],
            )
        ],
        bangumi_items=[
            BangumiItemCard(ref=ref, subject_ref='BS2', item_kind='special', title=('Road of Thorns' if ref == 'BE26' else f'Special {ref}'), name=('Toge no Michi' if ref == 'BE26' else f'Special {ref}'), source_form_hint='special', relation_to_main='side_story', duration_seconds=(2841 if ref == 'BE26' else 1440))
            for ref in item_refs
        ],
        assignable_target_refs=item_refs,
        detailed_card_refs=item_refs,
        seen_detail_refs=item_refs,
    )
    draft = MappingDraft(rows=[
        MappingDraftRow(
            row_ref='MDR4',
            local_ref='LS4',
            local_ref_kind='span',
            candidate_target_refs=['BES_LS2_1', 'BES_LS3_1', *item_refs],
        )
    ])

    prompt = render_mapping_draft_editor_prompt(dossier, draft)

    assert 'BE26' in prompt
    assert 'Toge no Michi' in prompt
    assert '2841' in prompt
    assert 'filename_anchor_tokens' in prompt
    assert 'title_anchor_candidates' in prompt
    assert 'Odoro' in prompt
    assert 'candidate_item_cards' in prompt
    assert 'no Michi' in prompt
