from dataclasses import replace

from src.rename.case_agent.models import BangumiItemCard, BangumiSpanCard, BangumiSubjectCard, CaseBudget, CaseContract, CaseHeader, LocalFileCard, LocalSpanCard, MappingDraft, MappingDraftRow
from src.rename.case_agent.planner import build_deterministic_evidence_plan
from src.rename.case_agent.workspace import CaseEvidenceWorkspace


def build_ws(*, budget: CaseBudget | None = None, detail_equivalent: bool = False, main_count: int = 24):
    return CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-P'),
        budget=budget or CaseBudget(max_evidence_batches=2, max_requests_per_batch=3),
        contract=CaseContract(main_file_refs=[f'LF{i}' for i in range(main_count)], allowed_file_refs=[f'LF{i}' for i in range(main_count)]),
        local_files=[LocalFileCard(ref=f'LF{i}') for i in range(main_count)],
        bangumi_items=[BangumiItemCard(ref=f'BE{i}') for i in range(main_count)],
        bangumi_span_cards=[BangumiSpanCard(ref='BES1', subject_ref='BS1', target_refs=['BE1'], target_ref_count=1, detail_equivalent=detail_equivalent)],
        local_span_cards=[
            LocalSpanCard(ref='LS1', span_scope='directory', file_ref_count=12, file_ref_samples=['LF1'], episode_token_start=1, episode_token_end=12, episode_token_count=12),
            LocalSpanCard(ref='LS_PACKAGE', span_scope='package', file_ref_count=24, file_ref_samples=['LF1'], episode_token_start=1, episode_token_end=24, episode_token_count=24),
        ],
    )


def build_subject_only_ws(*, budget: CaseBudget | None = None):
    return CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-P-SUBJECT'),
        budget=budget or CaseBudget(max_evidence_batches=3, max_requests_per_batch=8),
        contract=CaseContract(main_file_refs=[f'LF{i}' for i in range(4)], allowed_file_refs=[f'LF{i}' for i in range(4)]),
        local_files=[LocalFileCard(ref=f'LF{i}') for i in range(4)],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', subject_id=101, subject_type='anime')],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='directory', file_ref_count=4, file_ref_samples=['LF1'])],
    )


def test_planner_selects_child_span_ids():
    ws = build_ws()
    out = build_deterministic_evidence_plan(ws)
    assert out is not None and out.selected_evidence is True
    assert out.plan is not None
    assert 'REQ_TARGET_SPAN_LS1' in out.plan.selected_menu_request_ids
    assert out.plan.plan_status == 'in_progress'
    assert out.plan.planned_span_request_count >= 1
    assert out.plan.selected_span_request_count == len(out.plan.selected_menu_request_ids)


def test_planner_collects_episode_list_before_span_proof():
    ws = build_subject_only_ws()
    out = build_deterministic_evidence_plan(ws)
    assert out is not None and out.plan is not None
    assert out.plan.plan_kind == 'episode_recall'
    assert out.plan.selected_menu_request_ids == ['REQ_EPISODE_LIST_BS1']
    assert out.plan.selected_span_request_count == 0


def test_planner_does_not_select_package():
    ws = build_ws()
    out = build_deterministic_evidence_plan(ws)
    assert out is not None and all('LS_PACKAGE' not in rid for rid in out.plan.selected_menu_request_ids)


def test_planner_populates_ready_span_refs():
    ws = build_ws(detail_equivalent=True)
    out = build_deterministic_evidence_plan(ws)
    assert out is None


def test_planner_noop_on_detail_equivalent_or_small_or_exhausted():
    assert build_deterministic_evidence_plan(build_ws(detail_equivalent=True)) is None
    small_without_span = replace(build_ws(main_count=23), local_span_cards=[])
    assert build_deterministic_evidence_plan(small_without_span) is None
    assert build_deterministic_evidence_plan(build_ws(main_count=23)) is not None
    assert build_deterministic_evidence_plan(build_ws(budget=CaseBudget(max_evidence_batches=1, used_evidence_batches=1, max_requests_per_batch=3))) is None


def test_planner_respects_budget_batches_and_keeps_pending_plan():
    ws = build_ws(budget=CaseBudget(max_evidence_batches=2, max_requests_per_batch=1))
    ws = replace(ws, local_span_cards=ws.local_span_cards + [LocalSpanCard(ref='LS2', span_scope='directory', file_ref_count=12, file_ref_samples=['LF2'], episode_token_start=13, episode_token_end=24, episode_token_count=12)])
    out = build_deterministic_evidence_plan(ws)
    assert out is not None and out.plan is not None
    assert len(out.plan.selected_menu_request_ids) == 1
    assert out.plan.plan_status == 'in_progress'
    assert out.plan.planned_span_request_count >= 2


def test_planner_selects_special_recall_for_unresolved_singleton_residual():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-SPECIAL'),
        budget=CaseBudget(max_evidence_batches=4, max_requests_per_batch=4),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']),
        local_files=[LocalFileCard(ref='LF1', path='Mushishi Tokubetsu Hen.mkv', is_main=True, file_kind='video')],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', subject_id=1, subject_type='anime')],
        bangumi_items=[BangumiItemCard(ref='BE1', subject_ref='BS1', sort=1, ep=1)],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='residual', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1'], episode_token_count=0)],
        mapping_draft=MappingDraft(rows=[MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span')]),
    )

    out = build_deterministic_evidence_plan(ws)

    assert out is not None and out.plan is not None
    assert out.plan.plan_kind == 'special_recall'
    assert any(request_id.startswith('REQ_SPECIAL_') for request_id in out.plan.selected_menu_request_ids)


def test_planner_special_recall_runs_before_regular_span_proof_for_singletons():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-SPECIAL-FIRST'),
        budget=CaseBudget(max_evidence_batches=4, max_requests_per_batch=4),
        contract=CaseContract(main_file_refs=['LF1', 'LF2'], allowed_file_refs=['LF1', 'LF2'], visible_target_refs=['BE1', 'BE2']),
        local_files=[
            LocalFileCard(ref='LF1', path='Show Special.mkv', is_main=True, file_kind='video'),
            LocalFileCard(ref='LF2', path='Show 01.mkv', is_main=True, file_kind='video'),
        ],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', subject_id=1, subject_type='anime')],
        bangumi_items=[BangumiItemCard(ref='BE1', subject_ref='BS1', sort=1, ep=1), BangumiItemCard(ref='BE2', subject_ref='BS1', sort=2, ep=2)],
        local_span_cards=[
            LocalSpanCard(ref='LS1', span_scope='residual', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1'], episode_token_count=0),
            LocalSpanCard(ref='LS2', span_scope='directory', file_refs=['LF2'], file_ref_count=1, file_ref_samples=['LF2'], episode_token_start=1, episode_token_end=1, episode_token_count=1),
        ],
        mapping_draft=MappingDraft(rows=[
            MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span', candidate_target_refs=['BE1']),
            MappingDraftRow(row_ref='MDR2', local_ref='LS2', local_ref_kind='span'),
        ]),
    )

    out = build_deterministic_evidence_plan(ws)

    assert out is not None and out.plan is not None
    assert out.plan.plan_kind == 'special_recall'
    assert any(request_id.startswith('REQ_SPECIAL_') for request_id in out.plan.selected_menu_request_ids)
    assert not any(request_id.startswith('REQ_TARGET_SPAN_') for request_id in out.plan.selected_menu_request_ids)


def test_planner_does_not_special_recall_regular_numbered_span():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-SPECIAL-NO'),
        budget=CaseBudget(max_evidence_batches=4, max_requests_per_batch=4),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']),
        local_files=[LocalFileCard(ref='LF1', path='Show 01.mkv', is_main=True, file_kind='video')],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', subject_id=1, subject_type='anime')],
        bangumi_items=[BangumiItemCard(ref='BE1', subject_ref='BS1', sort=1, ep=1)],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='directory', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1'], episode_token_start=1, episode_token_end=1, episode_token_count=1)],
        mapping_draft=MappingDraft(rows=[MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span')]),
    )

    out = build_deterministic_evidence_plan(ws)

    assert out is not None and out.plan is not None
    assert out.plan.plan_kind != 'special_recall'


def test_planner_keeps_span_proof_pending_while_residual_gets_special_recall_first():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-DC'),
        budget=CaseBudget(max_evidence_batches=4, max_requests_per_batch=4),
        contract=CaseContract(main_file_refs=[f'LF{i}' for i in range(1, 14)], allowed_file_refs=[f'LF{i}' for i in range(1, 14)], visible_target_refs=[f'BE{i}' for i in range(1, 14)]),
        local_files=[LocalFileCard(ref=f'LF{i}', is_main=True, file_kind='video') for i in range(1, 14)],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', subject_id=1, subject_type='anime')],
        bangumi_items=[BangumiItemCard(ref=f'BE{i}', subject_ref='BS1', sort=i, ep=i) for i in range(1, 14)],
        local_span_cards=[
            LocalSpanCard(ref='LS1', span_scope='token_segment', file_refs=[f'LF{i}' for i in range(1, 13)], file_ref_count=12, file_ref_samples=['LF1', 'LF12'], episode_token_start=1, episode_token_end=12, episode_token_count=12),
            LocalSpanCard(ref='LS2', span_scope='residual', file_refs=['LF13'], file_ref_count=1, file_ref_samples=['LF13'], episode_token_count=0),
        ],
        mapping_draft=MappingDraft(rows=[
            MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span'),
            MappingDraftRow(row_ref='MDR2', local_ref='LS2', local_ref_kind='span'),
        ]),
    )

    out = build_deterministic_evidence_plan(ws)

    assert out is not None and out.plan is not None
    assert out.plan.plan_kind == 'special_recall'
    assert any(request_id.startswith('REQ_SPECIAL_') for request_id in out.plan.selected_menu_request_ids)
    assert 'REQ_TARGET_SPAN_LS2' not in out.plan.selected_menu_request_ids

    completed_special = out.plan.model_copy(update={
        'completed_menu_request_ids': list(out.plan.selected_menu_request_ids),
        'selected_menu_request_ids': list(out.plan.selected_menu_request_ids),
    })
    ws_after_special = replace(ws, plan_state=completed_special)
    next_out = build_deterministic_evidence_plan(ws_after_special)

    assert next_out is not None and next_out.plan is not None
    assert next_out.plan.plan_kind == 'span_proof'
    assert 'REQ_TARGET_SPAN_LS1' in next_out.plan.selected_menu_request_ids
    assert 'REQ_TARGET_SPAN_LS2' not in next_out.plan.selected_menu_request_ids
