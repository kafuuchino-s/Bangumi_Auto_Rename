from __future__ import annotations

from src.rename.case_agent.mapping_intent_compiler import MappingIntentCompiler
from src.rename.case_agent.models import (
    BangumiItemCard,
    BangumiSpanCard,
    BangumiSubjectCard,
    CaseBudget,
    CaseContract,
    CaseHeader,
    LocalFileCard,
    LocalSpanCard,
    MappingDraft,
    MappingDraftPatch,
    MappingDraftRow,
    MappingIntent,
)
from src.rename.case_agent.mapping_draft import apply_mapping_patches
from src.rename.case_agent.workspace import CaseEvidenceWorkspace


def _workspace(*, file_count: int = 1, subjects=None, items=None, spans=None) -> CaseEvidenceWorkspace:
    file_refs = [f'LF{i}' for i in range(1, file_count + 1)]
    return CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-INTENT'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=file_refs, allowed_file_refs=file_refs),
        local_files=[
            LocalFileCard(ref=ref, path=f'Title {index:02d}.mkv', is_main=True, label=f'Title {index:02d}.mkv')
            for index, ref in enumerate(file_refs, 1)
        ],
        local_span_cards=[
            LocalSpanCard(
                ref='LS1',
                span_scope='token_segment',
                file_refs=file_refs,
                file_ref_count=file_count,
                file_ref_samples=file_refs[:3],
                episode_token_start=1,
                episode_token_end=file_count,
                episode_token_count=file_count,
            )
        ],
        bangumi_subjects=list(subjects or []),
        bangumi_items=list(items or []),
        bangumi_span_cards=list(spans or []),
    )


def _draft() -> MappingDraft:
    return MappingDraft(rows=[MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span')])


def test_subject_only_regular_span_intent_requests_target_evidence_without_bad_patch():
    workspace = _workspace(file_count=12, subjects=[BangumiSubjectCard(ref='BS1', title='ばらかもん')])
    result = MappingIntentCompiler().compile(
        workspace.to_dossier(round_context='test'),
        _draft(),
        [
            MappingIntent(
                decision='map_regular_span',
                local_ref='LS1',
                chosen_subject_ref='BS1',
                episode_start=1,
                episode_end=12,
                support_refs=['LS1', 'BS1'],
                reason='main run belongs to the chosen subject',
            )
        ],
    )

    assert result.compiled_patches == []
    assert result.blocked_intents[0].issue_codes == ['target_span_or_item_not_visible']
    assert set(result.blocked_intents[0].requested_request_types) >= {'episode_list', 'target_span'}
    assert 'missing_target_ref' not in result.blocked_intents[0].issue_codes


def test_visible_singleton_item_intent_compiles_to_explicit_patch():
    workspace = _workspace(
        file_count=1,
        subjects=[BangumiSubjectCard(ref='BS1', title='Title')],
        items=[BangumiItemCard(ref='BE1', subject_ref='BS1', item_kind='episode', ep=1, sort=1)],
    )
    result = MappingIntentCompiler().compile(
        workspace.to_dossier(round_context='test'),
        _draft(),
        [MappingIntent(decision='map_explicit_item', local_ref='LS1', chosen_item_ref='BE1', support_refs=['LS1', 'BE1'])],
    )

    assert len(result.compiled_patches) == 1
    patch = result.compiled_patches[0]
    assert patch.op == 'map_to_bangumi'
    assert patch.target_ref == 'BE1'
    assert patch.mapping_mode == 'explicit'
    updated, issues = apply_mapping_patches(_draft(), result.compiled_patches, workspace.to_dossier(round_context='apply'))
    assert issues == []
    assert updated.rows[0].disposition == 'map_to_bangumi'


def test_visible_span_intent_compiles_to_span_patch():
    workspace = _workspace(
        file_count=12,
        subjects=[BangumiSubjectCard(ref='BS1', title='Title')],
        spans=[
            BangumiSpanCard(
                ref='BES1',
                subject_ref='BS1',
                target_refs=[f'BE{i}' for i in range(1, 13)],
                target_ref_count=12,
                sort_start=1,
                sort_end=12,
                ep_start=1,
                ep_end=12,
                detail_equivalent=True,
            )
        ],
    )
    result = MappingIntentCompiler().compile(
        workspace.to_dossier(round_context='test'),
        _draft(),
        [
            MappingIntent(
                decision='map_regular_span',
                local_ref='LS1',
                chosen_subject_ref='BS1',
                chosen_span_ref='BES1',
                episode_start=1,
                episode_end=12,
                support_refs=['LS1', 'BS1', 'BES1'],
            )
        ],
    )

    assert len(result.compiled_patches) == 1
    assert result.compiled_patches[0].target_span_ref == 'BES1'
    assert result.compiled_patches[0].mapping_mode == 'span_by_index'


def test_visible_span_intent_with_wrong_target_count_is_blocked_before_bad_patch():
    workspace = _workspace(
        file_count=12,
        subjects=[BangumiSubjectCard(ref='BS1', title='Title')],
        spans=[
            BangumiSpanCard(
                ref='BES1',
                subject_ref='BS1',
                target_refs=['BE1', 'BE2'],
                target_ref_count=2,
                sort_start=1,
                sort_end=2,
                ep_start=1,
                ep_end=2,
                detail_equivalent=True,
            )
        ],
    )
    result = MappingIntentCompiler().compile(
        workspace.to_dossier(round_context='test'),
        _draft(),
        [
            MappingIntent(
                decision='map_regular_span',
                local_ref='LS1',
                chosen_subject_ref='BS1',
                chosen_span_ref='BES1',
                support_refs=['LS1', 'BS1', 'BES1'],
            )
        ],
    )

    assert result.compiled_patches == []
    assert result.blocked_intents[0].issue_codes == ['count_mismatch']
    assert result.blocked_intents[0].observation['local_file_count'] == 12
    assert result.blocked_intents[0].observation['selected_span_target_ref_count'] == 2


def test_non_detail_span_intent_requests_target_span_instead_of_bad_patch():
    workspace = _workspace(
        file_count=12,
        subjects=[BangumiSubjectCard(ref='BS1', title='Title')],
        spans=[
            BangumiSpanCard(
                ref='BES1',
                subject_ref='BS1',
                target_refs=[f'BE{i}' for i in range(1, 13)],
                target_ref_count=12,
                sort_start=1,
                sort_end=12,
                ep_start=1,
                ep_end=12,
                detail_equivalent=False,
            )
        ],
    )
    result = MappingIntentCompiler().compile(
        workspace.to_dossier(round_context='test'),
        _draft(),
        [
            MappingIntent(
                decision='map_regular_span',
                local_ref='LS1',
                chosen_subject_ref='BS1',
                chosen_span_ref='BES1',
                episode_start=1,
                episode_end=12,
                support_refs=['LS1', 'BS1', 'BES1'],
            )
        ],
    )

    assert result.compiled_patches == []
    assert result.blocked_intents[0].issue_codes == ['target_span_not_detail_equivalent']
    assert result.blocked_intents[0].requested_request_types == ['target_span']


def test_ambiguous_visible_span_candidates_require_agent_choice():
    workspace = _workspace(
        file_count=12,
        subjects=[BangumiSubjectCard(ref='BS1', title='Title')],
        spans=[
            BangumiSpanCard(ref='BES1', subject_ref='BS1', target_refs=[f'BE{i}' for i in range(1, 13)], target_ref_count=12, sort_start=1, sort_end=12, detail_equivalent=True),
            BangumiSpanCard(ref='BES2', subject_ref='BS1', target_refs=[f'BE{i}' for i in range(13, 25)], target_ref_count=12, sort_start=1, sort_end=12, detail_equivalent=True),
        ],
    )
    draft = _draft()
    draft.rows[0].candidate_target_refs = ['BES1', 'BES2']
    result = MappingIntentCompiler().compile(
        workspace.to_dossier(round_context='test'),
        draft,
        [MappingIntent(decision='map_regular_span', local_ref='LS1', chosen_subject_ref='BS1', episode_start=1, episode_end=12, support_refs=['LS1', 'BS1'])],
    )

    assert result.compiled_patches == []
    assert result.blocked_intents[0].issue_codes == ['ambiguous_visible_target_candidates']
    assert result.blocked_intents[0].candidate_target_refs == ['BES1', 'BES2']


def test_regular_span_intent_with_explicit_visible_items_generates_span_card():
    items = [
        BangumiItemCard(ref=f'BE{i}', subject_ref='BS1', item_kind='episode', ep=i, sort=i)
        for i in range(1, 4)
    ]
    workspace = _workspace(
        file_count=3,
        subjects=[BangumiSubjectCard(ref='BS1', title='Title')],
        items=items,
    )
    result = MappingIntentCompiler().compile(
        workspace.to_dossier(round_context='test'),
        _draft(),
        [
            MappingIntent(
                decision='map_regular_span',
                local_ref='LS1',
                chosen_subject_ref='BS1',
                episode_start=1,
                episode_end=3,
                item_refs=['BE1', 'BE2', 'BE3'],
                support_refs=['LS1', 'BS1', 'BE1', 'BE2', 'BE3'],
            )
        ],
    )

    assert len(result.generated_span_cards) == 1
    assert result.generated_span_cards[0].target_refs == ['BE1', 'BE2', 'BE3']
    assert result.generated_span_cards[0].detail_equivalent is True
    assert len(result.compiled_patches) == 1
    assert result.compiled_patches[0].target_span_ref == result.generated_span_cards[0].ref


def test_regular_span_intent_with_explicit_items_overrides_non_detail_span_ref():
    items = [
        BangumiItemCard(ref=f'BE{i}', subject_ref='BS1', item_kind='episode', ep=i, sort=i)
        for i in range(1, 4)
    ]
    workspace = _workspace(
        file_count=3,
        subjects=[BangumiSubjectCard(ref='BS1', title='Title')],
        items=items,
        spans=[
            BangumiSpanCard(
                ref='BES1',
                subject_ref='BS1',
                target_refs=['BE1', 'BE2', 'BE3'],
                target_ref_count=3,
                sort_start=1,
                sort_end=3,
                ep_start=1,
                ep_end=3,
                detail_equivalent=False,
            )
        ],
    )
    result = MappingIntentCompiler().compile(
        workspace.to_dossier(round_context='test'),
        _draft(),
        [
            MappingIntent(
                decision='map_regular_span',
                local_ref='LS1',
                chosen_subject_ref='BS1',
                chosen_span_ref='BES1',
                episode_start=1,
                episode_end=3,
                item_refs=['BE1', 'BE2', 'BE3'],
                support_refs=['LS1', 'BS1', 'BES1', 'BE1', 'BE2', 'BE3'],
            )
        ],
    )

    assert result.blocked_intents == []
    assert len(result.generated_span_cards) == 1
    assert result.generated_span_cards[0].target_refs == ['BE1', 'BE2', 'BE3']
    assert len(result.compiled_patches) == 1
    assert result.compiled_patches[0].target_span_ref == result.generated_span_cards[0].ref


def test_regular_span_intent_with_subject_and_episode_range_materializes_visible_items():
    items = [
        BangumiItemCard(ref=f'BE{i}', subject_ref='BS1', item_kind='episode', ep=i, sort=i)
        for i in range(1, 4)
    ]
    workspace = _workspace(
        file_count=3,
        subjects=[BangumiSubjectCard(ref='BS1', title='Title')],
        items=items,
    )
    result = MappingIntentCompiler().compile(
        workspace.to_dossier(round_context='test'),
        _draft(),
        [
            MappingIntent(
                decision='map_regular_span',
                local_ref='LS1',
                chosen_subject_ref='BS1',
                episode_start=1,
                episode_end=3,
                support_refs=['LS1', 'BS1'],
                reason='agent chose this subject and episode range',
            )
        ],
    )

    assert result.blocked_intents == []
    assert len(result.generated_span_cards) == 1
    assert result.generated_span_cards[0].target_refs == ['BE1', 'BE2', 'BE3']
    assert len(result.compiled_patches) == 1
    assert result.compiled_patches[0].target_span_ref == result.generated_span_cards[0].ref


def test_multi_file_row_is_not_compiled_to_explicit_be_item():
    workspace = _workspace(
        file_count=2,
        subjects=[BangumiSubjectCard(ref='BS1', title='Title')],
        items=[BangumiItemCard(ref='BE1', subject_ref='BS1', item_kind='episode', ep=1, sort=1)],
    )
    result = MappingIntentCompiler().compile(
        workspace.to_dossier(round_context='test'),
        _draft(),
        [MappingIntent(decision='map_explicit_item', local_ref='LS1', chosen_subject_ref='BS1', chosen_item_ref='BE1', support_refs=['LS1', 'BE1'])],
    )

    assert result.compiled_patches == []
    assert result.blocked_intents[0].issue_codes == ['invalid_explicit_multi_file_mapping']


def test_hidden_support_ref_is_rejected_before_patch_application():
    workspace = _workspace(file_count=1, items=[BangumiItemCard(ref='BE1', subject_ref='BS1', item_kind='episode')])
    result = MappingIntentCompiler().compile(
        workspace.to_dossier(round_context='test'),
        _draft(),
        [MappingIntent(decision='map_explicit_item', local_ref='LS1', chosen_item_ref='BE1', support_refs=['LS1', 'BE1', 'HIDDEN'])],
    )

    assert result.compiled_patches == []
    assert result.blocked_intents[0].issue_codes == ['hidden_ref_rejected']


def test_reject_candidate_then_target_absent_compiles_to_accepted_exclusion():
    workspace = _workspace(
        file_count=1,
        items=[BangumiItemCard(ref='BE1', subject_ref='BS1', item_kind='special', ep=0, sort=1)],
    )
    draft = _draft()
    draft.rows[0].candidate_target_refs = ['BE1']
    result = MappingIntentCompiler().compile(
        workspace.to_dossier(round_context='test'),
        draft,
        [
            MappingIntent(
                decision='reject_candidate',
                local_ref='LS1',
                chosen_item_ref='BE1',
                support_refs=['LS1', 'BE1'],
                reason='BE1 is a different special item',
            ),
            MappingIntent(
                decision='mark_non_bangumi_or_supplemental',
                local_ref='LS1',
                reason_kind='bangumi_target_absent',
                support_refs=['LS1'],
                reason='after rejecting wrong candidates, Bangumi has no matching target',
            ),
        ],
    )

    assert [patch.op for patch in result.compiled_patches] == ['reject_candidate', 'mark_non_bangumi_or_supplemental']
    updated, issues = apply_mapping_patches(draft, result.compiled_patches, workspace.to_dossier(round_context='apply'))
    assert issues == []
    assert updated.rows[0].candidate_target_refs == []
    assert updated.rows[0].disposition == 'non_bangumi_or_supplemental'
    assert updated.rows[0].reason_kind == 'bangumi_target_absent'


def test_reject_candidate_accepts_visible_refs_from_item_refs():
    workspace = _workspace(
        file_count=1,
        items=[
            BangumiItemCard(ref='BE1', subject_ref='BS1', item_kind='special', ep=0, sort=1),
            BangumiItemCard(ref='BE2', subject_ref='BS1', item_kind='special', ep=0, sort=2),
        ],
    )
    draft = _draft()
    draft.rows[0].candidate_target_refs = ['BE1', 'BE2']
    result = MappingIntentCompiler().compile(
        workspace.to_dossier(round_context='test'),
        draft,
        [
            MappingIntent(
                decision='reject_candidate',
                local_ref='LS1',
                item_refs=['BE1', 'BE2'],
                support_refs=['LS1', 'BE1', 'BE2'],
                reason='agent rejects both visible candidates as semantically wrong',
            )
        ],
    )

    assert result.blocked_intents == []
    assert [patch.op for patch in result.compiled_patches] == ['reject_candidate', 'reject_candidate']
    updated, issues = apply_mapping_patches(draft, result.compiled_patches, workspace.to_dossier(round_context='apply'))
    assert issues == []
    assert updated.rows[0].candidate_target_refs == []


def test_reject_candidate_requires_current_row_candidate():
    workspace = _workspace(
        file_count=1,
        items=[BangumiItemCard(ref='BE1', subject_ref='BS1', item_kind='special', ep=0, sort=1)],
    )
    draft = _draft()
    result = MappingIntentCompiler().compile(
        workspace.to_dossier(round_context='test'),
        draft,
        [MappingIntent(decision='reject_candidate', local_ref='LS1', chosen_item_ref='BE1', support_refs=['LS1', 'BE1'])],
    )

    assert result.compiled_patches == []
    assert result.blocked_intents[0].issue_codes == ['candidate_ref_not_on_row']
