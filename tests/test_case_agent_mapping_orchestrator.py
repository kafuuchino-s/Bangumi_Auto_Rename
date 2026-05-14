from src.rename.case_agent.models import BangumiItemCard, BangumiSpanCard, CandidateComparison, CaseBudget, CaseContract, CaseHeader, LocalFileCard, LocalSpanCard, MappingDraft, MappingDraftEditorOutput, MappingDraftPatch, MappingDraftRow, VerifierIssue
from src.rename.case_agent.orchestrator import _build_initial_mapping_draft, _comparison_reason_undermines_winner, _editor_patches_with_comparison_repairs, _final_special_singleton_comparison_issues, _mapping_editor_output_with_workspace_comparisons, _reopen_mapping_draft_issue_rows, _salvage_unresolved_mapping_patches, _should_repair_mapping_patch_issues, _should_try_mapping_editor, _structural_special_singleton_mismatch_patches, _try_mapping_draft_editor_acceptance, _unresolved_supplemental_candidate_issues, _workspace_with_initial_mapping_draft
from src.rename.case_agent.workspace import CaseEvidenceWorkspace


def test_workspace_copy_preserves_mapping_draft():
    draft = MappingDraft(draft_ref='MDX', version=1)
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='case-1'),
        budget=CaseBudget(max_api_calls_per_case=10),
        bangumi_items=[BangumiItemCard(ref='BE1')],
        mapping_draft=draft,
    )

    rebuilt = CaseEvidenceWorkspace.from_cards(
        header=workspace.header,
        budget=workspace.budget,
        bangumi_items=workspace.bangumi_items,
        mapping_draft=workspace.mapping_draft,
        mapping_draft_patches=workspace.mapping_draft_patches,
    )

    assert rebuilt.mapping_draft is not None
    assert rebuilt.mapping_draft.draft_ref == 'MDX'


def test_initial_mapping_draft_is_initialized_from_local_and_bangumi_spans():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='case-2'),
        budget=CaseBudget(max_api_calls_per_case=10),
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='directory', file_ref_count=2, file_ref_samples=['LF1'])],
        bangumi_span_cards=[BangumiSpanCard(ref='BES1', detail_equivalent=True, target_refs=['BE1'], target_ref_count=1)],
    )

    draft = _build_initial_mapping_draft(workspace)
    assert draft is not None
    assert draft.version == 1
    assert draft.rows and draft.rows[0].local_ref == 'LS1'
    assert draft.rows[0].candidate_target_refs == ['BES1']


def test_orchestrator_initializes_mapping_draft_and_audits_it():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='case-3'),
        budget=CaseBudget(max_api_calls_per_case=10),
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='directory', file_ref_count=2, file_ref_samples=['LF1'])],
        bangumi_span_cards=[BangumiSpanCard(ref='BES1', detail_equivalent=True, target_refs=['BE1'], target_ref_count=1)],
    )

    updated = _workspace_with_initial_mapping_draft(workspace)

    assert updated.mapping_draft is not None
    assert updated.mapping_draft.rows[0].local_ref == 'LS1'
    assert any(a.get('note') == 'mapping_draft_initialized' for a in updated.judge_request_audits if isinstance(a, dict))


def _build_workspace_with_mapping_draft(*, local_rows: list[str], main_file_refs: list[str]) -> CaseEvidenceWorkspace:
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='case-map'),
        budget=CaseBudget(max_api_calls_per_case=10),
        local_span_cards=[LocalSpanCard(ref=ref, span_scope='directory', file_ref_count=1, file_ref_samples=[f'{ref}-F']) for ref in local_rows],
        bangumi_span_cards=[BangumiSpanCard(ref='BES1', detail_equivalent=True, target_refs=['BE1'], target_ref_count=1)],
    )
    workspace = CaseEvidenceWorkspace.from_cards(
        header=workspace.header,
        budget=workspace.budget,
        contract=workspace.contract.model_copy(update={'main_file_refs': list(main_file_refs)}),
        local_files=workspace.local_files,
        local_clusters=workspace.local_clusters,
        local_span_cards=workspace.local_span_cards,
        bangumi_subjects=workspace.bangumi_subjects,
        bangumi_relations=workspace.bangumi_relations,
        bangumi_groups=workspace.bangumi_groups,
        bangumi_items=[BangumiItemCard(ref='BE1')],
        bangumi_span_cards=workspace.bangumi_span_cards,
    )
    workspace = _workspace_with_initial_mapping_draft(workspace)
    return workspace


def test_mapping_draft_preflight_blocks_incomplete_coverage(monkeypatch):
    workspace = _build_workspace_with_mapping_draft(local_rows=['LS1'], main_file_refs=['LS1', 'LS2'])
    class _NoopClient:
        def _call_with_schema(self, prompt, schema):
            raise AssertionError('editor should not be called')

    result = _try_mapping_draft_editor_acceptance(workspace, _NoopClient(), [], [])

    assert result is not None
    assert result.status == 'invalid'
    assert result.summary == 'mapping_draft_incomplete_local_coverage'
    assert any(a.get('note') == 'mapping_draft_incomplete_local_coverage' for a in result.final_workspace.judge_request_audits if isinstance(a, dict))


def test_mapping_draft_allows_complete_coverage(monkeypatch):
    workspace = _build_workspace_with_mapping_draft(local_rows=['LS1', 'LS2'], main_file_refs=['LS1', 'LS2'])
    called: dict[str, object] = {}

    class _EditorOutput:
        def __init__(self):
            self.ok = False
            self.output = None
            self.error = 'no-op'
            self.raw_response = ''

    def _call_editor(ai_client, dossier, draft, *, round_kind='draft_edit', max_provider_retries=0):
        called['round_kind'] = round_kind
        called['draft_rows'] = len(draft.rows)
        return _EditorOutput()

    monkeypatch.setattr('src.rename.case_agent.orchestrator.call_mapping_draft_editor', _call_editor)

    result = _try_mapping_draft_editor_acceptance(workspace, object(), [], [])

    assert result is None
    assert called['round_kind'] == 'mapping_draft_edit'


def test_mapping_editor_can_run_before_regular_span_proof_done():
    workspace = _build_workspace_with_mapping_draft(local_rows=['LS1', 'LS2'], main_file_refs=['LS1', 'LS2'])
    assert workspace.mapping_draft is not None
    workspace.mapping_draft.rows[1].candidate_target_refs = []
    workspace.local_span_cards[1].episode_token_start = 2
    workspace.local_span_cards[1].episode_token_end = 2
    workspace.local_span_cards[1].episode_token_count = 1
    workspace.local_span_cards[1].gap_count = 0
    workspace.local_span_cards[1].duplicate_count = 0

    assert _should_try_mapping_editor(workspace) is True


def test_mapping_editor_does_not_wait_for_unexecutable_zero_token_span_proof():
    workspace = _build_workspace_with_mapping_draft(local_rows=['LS1', 'LS2'], main_file_refs=['LS1', 'LS2'])
    assert workspace.mapping_draft is not None
    workspace.mapping_draft.rows[1].candidate_target_refs = []
    workspace.local_span_cards[1].episode_token_start = 0
    workspace.local_span_cards[1].episode_token_end = 0
    workspace.local_span_cards[1].episode_token_count = 1
    workspace.local_span_cards[1].gap_count = 0
    workspace.local_span_cards[1].duplicate_count = 0

    assert _should_try_mapping_editor(workspace) is True


def test_mapping_editor_can_run_before_zero_based_regular_span_proof():
    workspace = _build_workspace_with_mapping_draft(local_rows=['LS1', 'LS2'], main_file_refs=['LS1', 'LS2'])
    assert workspace.mapping_draft is not None
    workspace.mapping_draft.rows[1].candidate_target_refs = []
    workspace.local_span_cards[1].episode_token_start = 0
    workspace.local_span_cards[1].episode_token_end = 1
    workspace.local_span_cards[1].episode_token_count = 2
    workspace.local_span_cards[1].file_ref_count = 2
    workspace.local_span_cards[1].gap_count = 0
    workspace.local_span_cards[1].duplicate_count = 0

    assert _should_try_mapping_editor(workspace) is True


def test_mapping_editor_can_handle_open_rows_without_candidates_after_span_proof_failed():
    workspace = _build_workspace_with_mapping_draft(local_rows=['LS1', 'LS2'], main_file_refs=['LS1', 'LS2'])
    assert workspace.mapping_draft is not None
    workspace.mapping_draft.rows[1].candidate_target_refs = []
    workspace.local_span_cards[1].episode_token_start = 2
    workspace.local_span_cards[1].episode_token_end = 2
    workspace.local_span_cards[1].episode_token_count = 1
    workspace.local_span_cards[1].gap_count = 0
    workspace.local_span_cards[1].duplicate_count = 0
    object.__setattr__(workspace, 'plan_state', workspace.plan_state.model_copy(update={'failed_menu_request_ids': ['REQ_TARGET_SPAN_LS2']}))

    assert _should_try_mapping_editor(workspace) is True


def test_salvage_canonicalizes_supplemental_patch_reason_kind():
    dossier = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='case-supplemental'),
        budget=CaseBudget(max_api_calls_per_case=10),
        local_files=[
            LocalFileCard(ref='LF1', path='pkg/Show ノンクレジットOP.mkv', is_main=True, file_kind='video'),
        ],
        local_span_cards=[
            LocalSpanCard(ref='LS1', span_scope='residual', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1'], title_cues=['ノンクレジットOP']),
        ],
        mapping_draft=MappingDraft(rows=[MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span')]),
    ).to_dossier(round_context='test')
    original = MappingDraft(rows=[MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span')])
    updated = MappingDraft(rows=[MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span')])
    attempted = [
        MappingDraftPatch(
            op='mark_non_bangumi_or_supplemental',
            local_ref='LS1',
            support_refs=['LS1'],
            reason_kind='support',
            reason='visible non-credit OP extra',
        )
    ]
    issues = [VerifierIssue(ref='LS1', issue_code='invalid_reason_kind', severity='blocked', message='bad reason kind')]

    salvage = _salvage_unresolved_mapping_patches(original, updated, attempted, issues, dossier)

    assert len(salvage) == 1
    assert salvage[0].op == 'mark_non_bangumi_or_supplemental'
    assert salvage[0].reason_kind == 'creditless_op_ed'


def test_salvage_canonicalizes_travel_feature_as_making_of_supplemental():
    dossier = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='case-travel-supplemental'),
        budget=CaseBudget(max_api_calls_per_case=10),
        local_files=[
            LocalFileCard(ref='LF1', path='pkg/Show cast travel feature #01.mkv', is_main=True, file_kind='video'),
            LocalFileCard(ref='LF2', path='pkg/Show cast travel feature #02.mkv', is_main=True, file_kind='video'),
        ],
        local_span_cards=[
            LocalSpanCard(ref='LS1', span_scope='token_segment', file_refs=['LF1', 'LF2'], file_ref_count=2, file_ref_samples=['LF1', 'LF2'], title_cues=['cast travel feature']),
        ],
        mapping_draft=MappingDraft(rows=[MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span')]),
    ).to_dossier(round_context='test')
    original = MappingDraft(rows=[MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span')])
    updated = MappingDraft(rows=[MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span')])
    attempted = [
        MappingDraftPatch(
            op='mark_non_bangumi_or_supplemental',
            local_ref='LS1',
            support_refs=['LS1'],
            reason_kind='support',
            reason='visible travel feature extra',
        )
    ]
    issues = [VerifierIssue(ref='LS1', issue_code='invalid_reason_kind', severity='blocked', message='bad reason kind')]

    salvage = _salvage_unresolved_mapping_patches(original, updated, attempted, issues, dossier)

    assert len(salvage) == 1
    assert salvage[0].op == 'mark_non_bangumi_or_supplemental'
    assert salvage[0].reason_kind == 'making_of'


def test_unresolved_supplemental_candidate_issue_routes_editor_identified_extra_to_repair():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='case-supplemental-unresolved'),
        budget=CaseBudget(max_api_calls_per_case=10),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1']),
        local_files=[
            LocalFileCard(ref='LF1', path='pkg/Show ノンクレジットED.mkv', is_main=True, file_kind='video'),
        ],
        local_span_cards=[
            LocalSpanCard(ref='LS1', span_scope='residual', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1']),
        ],
        mapping_draft=MappingDraft(rows=[
            MappingDraftRow(
                row_ref='MDR1',
                local_ref='LS1',
                local_ref_kind='span',
                status='unresolved',
                disposition='needs_more_evidence',
                reason_kind='ambiguous_candidate',
                reason='visible row is clearly a non-credit ED extra, but no matching Bangumi item is visible',
            )
        ]),
    )
    dossier = workspace.to_dossier(round_context='test')

    issues = _unresolved_supplemental_candidate_issues(dossier, workspace.mapping_draft)

    assert [issue.issue_code for issue in issues] == ['unresolved_supplemental_candidate']
    assert _should_repair_mapping_patch_issues(issues, repair_depth=0) is True


def test_mapping_draft_no_draft_remains_noop():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='case-no-draft'),
        budget=CaseBudget(max_api_calls_per_case=10),
    )

    result = _try_mapping_draft_editor_acceptance(workspace, object(), [], [])

    assert result is None


def test_candidate_comparison_repair_replaces_bad_patch_for_same_row():
    workspace = _build_workspace_with_mapping_draft(local_rows=['LS1'], main_file_refs=['LS1'])
    draft = workspace.mapping_draft
    dossier = workspace.to_dossier(round_context='mapping_draft_edit')
    output = MappingDraftEditorOutput(
        patches=[
            MappingDraftPatch(
                op='needs_more_evidence',
                local_ref='MDR1',
                reason_kind='ambiguous_candidate',
                reason='original patch did not use the selected candidate',
            )
        ],
        candidate_comparisons=[
            CandidateComparison(ref='MDR1', left_ref='BES1', right_ref='BES2', winner_ref='BES1', reason='visible winner')
        ],
    )

    patches = _editor_patches_with_comparison_repairs(draft, output, dossier)

    assert len(patches) == 1
    assert patches[0].op == 'map_to_bangumi'
    assert patches[0].local_ref == 'LS1'
    assert patches[0].target_span_ref == 'BES1'


def test_candidate_comparison_repair_overrides_conflicting_mapping_patch():
    workspace = _build_workspace_with_mapping_draft(local_rows=['LS1'], main_file_refs=['LS1'])
    draft = workspace.mapping_draft
    dossier = workspace.to_dossier(round_context='mapping_draft_edit')
    output = MappingDraftEditorOutput(
        patches=[
            MappingDraftPatch(
                op='map_to_bangumi',
                local_ref='LS1',
                target_span_ref='BES2',
                mapping_mode='span_by_index',
                reason='patch conflicts with comparison',
            )
        ],
        candidate_comparisons=[
            CandidateComparison(ref='MDR1', left_ref='BES1', right_ref='BES2', winner_ref='BES1', reason='visible winner')
        ],
    )

    patches = _editor_patches_with_comparison_repairs(draft, output, dossier)

    assert len(patches) == 1
    assert patches[0].target_span_ref == 'BES1'


def test_candidate_comparison_repair_supports_explicit_special_item():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='case-special-repair'),
        budget=CaseBudget(max_api_calls_per_case=10),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE_SPECIAL']),
        local_files=[LocalFileCard(ref='LF1', path='Special.mkv', is_main=True, file_kind='video')],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='residual', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1'])],
        bangumi_items=[BangumiItemCard(ref='BE_SPECIAL', item_kind='special', subject_ref='BS1', title='Special')],
        mapping_draft=MappingDraft(rows=[MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span', candidate_target_refs=['BE_SPECIAL'])]),
    )
    dossier = workspace.with_seen_detail_refs(['BE_SPECIAL']).to_dossier(round_context='mapping_draft_edit')
    output = MappingDraftEditorOutput(
        patches=[MappingDraftPatch(op='needs_more_evidence', local_ref='MDR1', reason_kind='ambiguous_candidate')],
        candidate_comparisons=[CandidateComparison(ref='MDR1', left_ref='BE_SPECIAL', right_ref='', winner_ref='BE_SPECIAL', reason='visible special item')],
    )

    patches = _editor_patches_with_comparison_repairs(workspace.mapping_draft, output, dossier)

    assert len(patches) == 1
    assert patches[0].op == 'map_to_bangumi'
    assert patches[0].local_ref == 'LS1'
    assert patches[0].target_ref == 'BE_SPECIAL'
    assert patches[0].mapping_mode == 'explicit'


def test_candidate_comparison_repair_does_not_force_duplicate_special_winner():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='case-special-duplicate-repair'),
        budget=CaseBudget(max_api_calls_per_case=10),
        contract=CaseContract(main_file_refs=['LF1', 'LF2'], allowed_file_refs=['LF1', 'LF2'], visible_target_refs=['BE_SPECIAL']),
        local_files=[
            LocalFileCard(ref='LF1', path='Special A.mkv', is_main=True, file_kind='video'),
            LocalFileCard(ref='LF2', path='Special B.mkv', is_main=True, file_kind='video'),
        ],
        local_span_cards=[
            LocalSpanCard(ref='LS1', span_scope='residual', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1']),
            LocalSpanCard(ref='LS2', span_scope='residual', file_refs=['LF2'], file_ref_count=1, file_ref_samples=['LF2']),
        ],
        bangumi_items=[BangumiItemCard(ref='BE_SPECIAL', item_kind='special', subject_ref='BS1', title='Special')],
        mapping_draft=MappingDraft(rows=[
            MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span', candidate_target_refs=['BE_SPECIAL']),
            MappingDraftRow(row_ref='MDR2', local_ref='LS2', local_ref_kind='span', candidate_target_refs=['BE_SPECIAL']),
        ]),
    )
    dossier = workspace.with_seen_detail_refs(['BE_SPECIAL']).to_dossier(round_context='mapping_draft_edit')
    output = MappingDraftEditorOutput(
        patches=[
            MappingDraftPatch(op='map_to_bangumi', local_ref='LS1', target_ref='BE_SPECIAL', mapping_mode='explicit'),
            MappingDraftPatch(op='map_to_bangumi', local_ref='LS2', target_ref='BE_SPECIAL', mapping_mode='explicit'),
        ],
        candidate_comparisons=[
            CandidateComparison(ref='MDR1', left_ref='BE_SPECIAL', right_ref='', winner_ref='BE_SPECIAL', reason='visible special item'),
            CandidateComparison(ref='MDR2', left_ref='BE_SPECIAL', right_ref='', winner_ref='BE_SPECIAL', reason='visible special item'),
        ],
    )

    patches = _editor_patches_with_comparison_repairs(workspace.mapping_draft, output, dossier)

    assert [patch.target_ref for patch in patches] == ['BE_SPECIAL', 'BE_SPECIAL']


def test_candidate_comparison_repair_does_not_borrow_occupied_singleton_target():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='case-special-occupied-repair'),
        budget=CaseBudget(max_api_calls_per_case=10),
        contract=CaseContract(main_file_refs=['LF1', 'LF2'], allowed_file_refs=['LF1', 'LF2'], visible_target_refs=['BE_SHARED', 'BE_DISTINCT']),
        local_files=[
            LocalFileCard(ref='LF1', path='Special One.mkv', is_main=True, file_kind='video'),
            LocalFileCard(ref='LF2', path='Special Two.mkv', is_main=True, file_kind='video'),
        ],
        local_span_cards=[
            LocalSpanCard(ref='LS1', span_scope='residual', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1']),
            LocalSpanCard(ref='LS2', span_scope='residual', file_refs=['LF2'], file_ref_count=1, file_ref_samples=['LF2']),
        ],
        bangumi_items=[
            BangumiItemCard(ref='BE_SHARED', item_kind='special', subject_ref='BS1', title='Special Two'),
            BangumiItemCard(ref='BE_DISTINCT', item_kind='special', subject_ref='BS1', title='Special One'),
        ],
        mapping_draft=MappingDraft(rows=[
            MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span', status='open', disposition='open', candidate_target_refs=['BE_SHARED', 'BE_DISTINCT']),
            MappingDraftRow(
                row_ref='MDR2',
                local_ref='LS2',
                local_ref_kind='span',
                status='proposed',
                disposition='map_to_bangumi',
                selected_target_ref='BE_SHARED',
                selected_target_kind='item',
                mapping_mode='explicit',
                candidate_target_refs=['BE_SHARED'],
            ),
        ]),
    )
    dossier = workspace.with_seen_detail_refs(['BE_SHARED', 'BE_DISTINCT']).to_dossier(round_context='mapping_draft_edit')
    output = MappingDraftEditorOutput(
        patches=[MappingDraftPatch(op='needs_more_evidence', local_ref='MDR1', reason_kind='ambiguous_candidate')],
        candidate_comparisons=[
            CandidateComparison(ref='MDR1', left_ref='BE_SHARED', right_ref='BE_DISTINCT', winner_ref='BE_SHARED', reason='stale local comparison winner'),
        ],
    )

    patches = _editor_patches_with_comparison_repairs(workspace.mapping_draft, output, dossier)

    assert len(patches) == 1
    assert patches[0].op == 'needs_more_evidence'


def test_candidate_comparison_repair_does_not_override_valid_mapping_patch():
    workspace = _build_workspace_with_mapping_draft(local_rows=['LS1'], main_file_refs=['LS1'])
    draft = workspace.mapping_draft
    dossier = workspace.to_dossier(round_context='mapping_draft_edit')
    output = MappingDraftEditorOutput(
        patches=[
            MappingDraftPatch(
                op='map_to_bangumi',
                local_ref='LS1',
                target_span_ref='BES1',
                mapping_mode='span_by_index',
                reason='editor selected explicit patch',
            )
        ],
        candidate_comparisons=[
            CandidateComparison(ref='MDR1', left_ref='BES1', right_ref='BES2', winner_ref='BES2', reason='stale comparison')
        ],
    )

    patches = _editor_patches_with_comparison_repairs(draft, output, dossier)

    assert len(patches) == 1
    assert patches[0].target_span_ref == 'BES1'


def test_final_special_singleton_mapping_does_not_require_matching_comparison_winner():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='case-special-final-comparison'),
        budget=CaseBudget(max_api_calls_per_case=10),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE_BAD', 'BE_GOOD']),
        local_files=[LocalFileCard(ref='LF1', path='Hihamu Kage.mkv', is_main=True, file_kind='video')],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='residual', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1'])],
        bangumi_items=[
            BangumiItemCard(ref='BE_BAD', item_kind='special', subject_ref='BS1', title='Wrong Special'),
            BangumiItemCard(ref='BE_GOOD', item_kind='special', subject_ref='BS1', title='Hihamu Kage'),
        ],
        mapping_draft=MappingDraft(rows=[
            MappingDraftRow(
                row_ref='MDR1',
                local_ref='LS1',
                local_ref_kind='span',
                candidate_target_refs=['BE_BAD', 'BE_GOOD'],
                selected_target_ref='BE_BAD',
                selected_target_kind='item',
                mapping_mode='explicit',
                support_refs=['LS1', 'BE_BAD'],
                status='proposed',
                disposition='map_to_bangumi',
            )
        ]),
    )
    dossier = workspace.with_seen_detail_refs(['BE_BAD', 'BE_GOOD']).to_dossier(round_context='mapping_draft_acceptance')
    output = MappingDraftEditorOutput(
        candidate_comparisons=[
            CandidateComparison(ref='MDR1', left_ref='BE_BAD', right_ref='BE_GOOD', winner_ref='BE_GOOD', reason='visible better candidate')
        ]
    )

    issues = _final_special_singleton_comparison_issues(dossier, workspace.mapping_draft, output)

    assert issues == []


def test_final_special_singleton_mapping_accepts_matching_comparison_winner():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='case-special-final-comparison-ok'),
        budget=CaseBudget(max_api_calls_per_case=10),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE_BAD', 'BE_GOOD']),
        local_files=[LocalFileCard(ref='LF1', path='Hihamu Kage.mkv', is_main=True, file_kind='video')],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='residual', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1'])],
        bangumi_items=[
            BangumiItemCard(ref='BE_BAD', item_kind='special', subject_ref='BS1', title='Wrong Special'),
            BangumiItemCard(ref='BE_GOOD', item_kind='special', subject_ref='BS1', title='Hihamu Kage'),
        ],
        mapping_draft=MappingDraft(rows=[
            MappingDraftRow(
                row_ref='MDR1',
                local_ref='LS1',
                local_ref_kind='span',
                candidate_target_refs=['BE_BAD', 'BE_GOOD'],
                selected_target_ref='BE_GOOD',
                selected_target_kind='item',
                mapping_mode='explicit',
                support_refs=['LS1', 'BE_GOOD'],
                status='proposed',
                disposition='map_to_bangumi',
            )
        ]),
    )
    dossier = workspace.with_seen_detail_refs(['BE_BAD', 'BE_GOOD']).to_dossier(round_context='mapping_draft_acceptance')
    output = MappingDraftEditorOutput(
        candidate_comparisons=[
            CandidateComparison(ref='MDR1', left_ref='BE_BAD', right_ref='BE_GOOD', winner_ref='BE_GOOD', reason='visible better candidate')
        ]
    )

    issues = _final_special_singleton_comparison_issues(dossier, workspace.mapping_draft, output)

    assert issues == []


def test_final_special_singleton_mapping_does_not_semantically_reject_category_mismatch():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='case-special-supplemental-mismatch'),
        budget=CaseBudget(max_api_calls_per_case=10),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE_PV']),
        local_files=[LocalFileCard(ref='LF1', path='pkg/Show ノンクレジットOP.mkv', is_main=True, file_kind='video')],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='residual', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1'], title_cues=['ノンクレジットOP'])],
        bangumi_items=[
            BangumiItemCard(ref='BE_PV', item_kind='special', subject_ref='BS1', title='Promotion Video', source_form_hint='pv'),
        ],
        mapping_draft=MappingDraft(rows=[
            MappingDraftRow(
                row_ref='MDR1',
                local_ref='LS1',
                local_ref_kind='span',
                candidate_target_refs=['BE_PV'],
                selected_target_ref='BE_PV',
                selected_target_kind='item',
                mapping_mode='explicit',
                support_refs=['LS1', 'BE_PV'],
                status='proposed',
                disposition='map_to_bangumi',
            )
        ]),
    )
    dossier = workspace.with_seen_detail_refs(['BE_PV']).to_dossier(round_context='mapping_draft_acceptance')
    output = MappingDraftEditorOutput(
        candidate_comparisons=[
            CandidateComparison(ref='MDR1', left_ref='BE_PV', right_ref='', winner_ref='BE_PV', reason='selected visible singleton')
        ]
    )

    issues = _final_special_singleton_comparison_issues(dossier, workspace.mapping_draft, output)

    assert issues == []


def test_structural_special_singleton_mismatch_retracts_visible_supplemental_extra():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='case-special-supplemental-structural'),
        budget=CaseBudget(max_api_calls_per_case=10),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE_OVA']),
        local_files=[LocalFileCard(ref='LF1', path='Show/PV CM Collection.mkv', is_main=True, file_kind='video')],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='residual', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1'], title_cues=['PV CM Collection'])],
        bangumi_items=[
            BangumiItemCard(ref='BE_OVA', item_kind='special', subject_ref='BS1', title='Unrelated OVA', source_form_hint='ova'),
        ],
        mapping_draft=MappingDraft(rows=[
            MappingDraftRow(
                row_ref='MDR1',
                local_ref='LS1',
                local_ref_kind='span',
                candidate_target_refs=['BE_OVA'],
                selected_target_ref='BE_OVA',
                selected_target_kind='item',
                mapping_mode='explicit',
                support_refs=['LS1', 'BE_OVA'],
                status='proposed',
                disposition='map_to_bangumi',
            )
        ]),
    )
    dossier = workspace.with_seen_detail_refs(['BE_OVA']).to_dossier(round_context='mapping_draft_acceptance')

    patches = _structural_special_singleton_mismatch_patches(workspace.mapping_draft, dossier)

    assert [patch.op for patch in patches] == ['retract_mapping', 'mark_non_bangumi_or_supplemental']
    assert patches[1].reason_kind == 'pv_cm'


def test_final_special_singleton_mapping_ignores_comparison_reason_persuasiveness():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='case-special-final-comparison-weak'),
        budget=CaseBudget(max_api_calls_per_case=10),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE_BAD', 'BE_GOOD']),
        local_files=[LocalFileCard(ref='LF1', path='Hihamu Kage.mkv', is_main=True, file_kind='video')],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='residual', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1'])],
        bangumi_items=[
            BangumiItemCard(ref='BE_BAD', item_kind='special', subject_ref='BS1', title='Wrong Special'),
            BangumiItemCard(ref='BE_GOOD', item_kind='special', subject_ref='BS1', title='Hihamu Kage'),
        ],
        mapping_draft=MappingDraft(rows=[
            MappingDraftRow(
                row_ref='MDR1',
                local_ref='LS1',
                local_ref_kind='span',
                candidate_target_refs=['BE_BAD', 'BE_GOOD'],
                selected_target_ref='BE_BAD',
                selected_target_kind='item',
                mapping_mode='explicit',
                support_refs=['LS1', 'BE_BAD'],
                status='proposed',
                disposition='map_to_bangumi',
            )
        ]),
    )
    dossier = workspace.with_seen_detail_refs(['BE_BAD', 'BE_GOOD']).to_dossier(round_context='mapping_draft_acceptance')
    output = MappingDraftEditorOutput(
        candidate_comparisons=[
            CandidateComparison(
                ref='MDR1',
                left_ref='BE_BAD',
                right_ref='BE_GOOD',
                winner_ref='BE_BAD',
                reason='BE_BAD wins only loosely; BE_BAD has no title overlap and the exact anchor is not strong enough for a firm map.',
            )
        ]
    )

    issues = _final_special_singleton_comparison_issues(dossier, workspace.mapping_draft, output)

    assert issues == []


def test_comparison_reason_guard_allows_negative_evidence_about_losing_candidate():
    comparison = CandidateComparison(
        ref='MDR1',
        left_ref='BE_BAD',
        right_ref='BE_GOOD',
        winner_ref='BE_GOOD',
        reason='BE_BAD has no title overlap, while BE_GOOD matches the visible title, relation, and package context.',
    )

    assert _comparison_reason_undermines_winner(comparison) is False


def test_comparison_reason_guard_flags_selected_target_uncertainty():
    comparison = CandidateComparison(
        ref='MDR1',
        left_ref='BE_BAD',
        right_ref='BE_GOOD',
        winner_ref='BE_BAD',
        reason='The selected target is only loosely related and not strong enough for a firm map.',
    )

    assert _comparison_reason_undermines_winner(comparison) is False


def test_mapping_editor_output_keeps_prior_row_comparisons_across_repairs():
    existing = CandidateComparison(ref='MDR4', left_ref='BE24', right_ref='BE26', winner_ref='BE26', reason='prior winner')
    incoming = CandidateComparison(ref='MDR1', left_ref='BE55', right_ref='BE54', winner_ref='BE55', reason='new winner')
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='case-comparison-memory'),
        budget=CaseBudget(max_api_calls_per_case=10),
        mapping_draft_candidate_comparisons=[existing],
    )
    output = MappingDraftEditorOutput(candidate_comparisons=[incoming])

    merged = _mapping_editor_output_with_workspace_comparisons(workspace, output)

    assert [comparison.ref for comparison in merged.candidate_comparisons] == ['MDR4', 'MDR1']
    assert [comparison.winner_ref for comparison in merged.candidate_comparisons] == ['BE26', 'BE55']


def test_reopen_mapping_draft_issue_rows_drops_only_reopened_comparisons():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='case-comparison-memory-reopen'),
        budget=CaseBudget(max_api_calls_per_case=10),
        mapping_draft=MappingDraft(rows=[
            MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span', status='proposed', disposition='map_to_bangumi', selected_target_ref='BE55', selected_target_kind='item', mapping_mode='explicit'),
            MappingDraftRow(row_ref='MDR4', local_ref='LS4', local_ref_kind='span', status='proposed', disposition='map_to_bangumi', selected_target_ref='BE26', selected_target_kind='item', mapping_mode='explicit'),
        ]),
        mapping_draft_candidate_comparisons=[
            CandidateComparison(ref='MDR1', left_ref='BE55', right_ref='BE54', winner_ref='BE55', reason='stale row'),
            CandidateComparison(ref='MDR4', left_ref='BE24', right_ref='BE26', winner_ref='BE26', reason='keep row'),
        ],
    )

    updated = _reopen_mapping_draft_issue_rows(workspace, [
        VerifierIssue(ref='MDR1', issue_code='missing_singleton_candidate_comparison', severity='blocked', message='repair row'),
    ])

    assert [comparison.ref for comparison in updated.mapping_draft_candidate_comparisons] == ['MDR4']


def test_reopen_duplicate_target_issue_reopens_reported_conflict_rows():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='case-duplicate-reopen-narrow'),
        budget=CaseBudget(max_api_calls_per_case=10),
        mapping_draft=MappingDraft(rows=[
            MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span', status='proposed', disposition='map_to_bangumi', selected_target_ref='BES_MAIN', selected_target_kind='span', mapping_mode='span_by_index'),
            MappingDraftRow(row_ref='MDR3', local_ref='LS3', local_ref_kind='span', status='proposed', disposition='map_to_bangumi', selected_target_ref='BES_EXTRA', selected_target_kind='span', mapping_mode='span_by_index'),
        ]),
        bangumi_span_cards=[
            BangumiSpanCard(ref='BES_MAIN', detail_equivalent=True, target_refs=['BE1', 'BE2'], target_ref_count=2),
            BangumiSpanCard(ref='BES_EXTRA', detail_equivalent=True, target_refs=['BE1', 'BE2'], target_ref_count=2),
        ],
        mapping_draft_candidate_comparisons=[
            CandidateComparison(ref='MDR1', left_ref='BES_MAIN', right_ref='', winner_ref='BES_MAIN', reason='keep row'),
            CandidateComparison(ref='MDR3', left_ref='BES_EXTRA', right_ref='', winner_ref='BES_EXTRA', reason='repair row'),
        ],
    )

    updated = _reopen_mapping_draft_issue_rows(workspace, [
        VerifierIssue(ref='MDR3', issue_code='duplicate_target', severity='blocked', message='duplicate target refs', related_refs=['MDR1', 'MDR3', 'BE1']),
    ])

    rows = {row.row_ref: row for row in updated.mapping_draft.rows}
    assert rows['MDR1'].disposition == 'open'
    assert rows['MDR3'].disposition == 'open'
    assert updated.mapping_draft_candidate_comparisons == []


def test_reopen_duplicate_target_issue_reopens_rows_owning_related_target_ref():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='case-duplicate-reopen-by-target'),
        budget=CaseBudget(max_api_calls_per_case=10),
        mapping_draft=MappingDraft(rows=[
            MappingDraftRow(
                row_ref='MDR1',
                local_ref='LS1',
                local_ref_kind='span',
                status='open',
                disposition='open',
                candidate_target_refs=['BE55', 'BE56'],
            ),
            MappingDraftRow(
                row_ref='MDR5',
                local_ref='LS5',
                local_ref_kind='span',
                status='proposed',
                disposition='map_to_bangumi',
                selected_target_ref='BE55',
                selected_target_kind='item',
                mapping_mode='explicit',
                candidate_target_refs=['BE55'],
            ),
        ]),
        mapping_draft_candidate_comparisons=[
            CandidateComparison(ref='MDR5', left_ref='BE55', right_ref='BE56', winner_ref='BE55', reason='owned row'),
        ],
    )

    updated = _reopen_mapping_draft_issue_rows(workspace, [
        VerifierIssue(ref='MDR1', issue_code='duplicate_target', severity='blocked', message='duplicate target refs', related_refs=['BE55']),
    ])

    rows = {row.row_ref: row for row in updated.mapping_draft.rows}
    assert rows['MDR1'].disposition == 'open'
    assert rows['MDR5'].disposition == 'open'
    assert updated.mapping_draft_candidate_comparisons == []


def test_mapping_editor_output_replaces_stale_prior_row_comparison():
    existing = CandidateComparison(ref='MDR1', left_ref='BE24', right_ref='BE55', winner_ref='BE24', reason='stale winner')
    incoming = CandidateComparison(ref='MDR1', left_ref='BE24', right_ref='BE55', winner_ref='BE55', reason='repair winner')
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='case-comparison-memory-replace'),
        budget=CaseBudget(max_api_calls_per_case=10),
        mapping_draft_candidate_comparisons=[existing],
    )
    output = MappingDraftEditorOutput(candidate_comparisons=[incoming])

    merged = _mapping_editor_output_with_workspace_comparisons(workspace, output)

    assert len(merged.candidate_comparisons) == 1
    assert merged.candidate_comparisons[0].winner_ref == 'BE55'
