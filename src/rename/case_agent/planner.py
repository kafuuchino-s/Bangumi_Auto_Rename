from __future__ import annotations

from .evidence_menu import build_executable_evidence_menu
from .models import EvidencePlan, EvidencePlanStep, EvidencePlannerOutput
from .special_investigation import special_eligible_open_row_refs
from .workspace import CaseEvidenceWorkspace


def build_deterministic_evidence_plan(workspace: CaseEvidenceWorkspace) -> EvidencePlannerOutput | None:
    if workspace.budget.max_evidence_batches and workspace.budget.used_evidence_batches >= workspace.budget.max_evidence_batches:
        return None

    menu = build_executable_evidence_menu(workspace)
    menu_audit = dict(menu.get('audit') or {})
    max_requests = int(getattr(workspace.budget, 'max_requests_per_batch', 0) or 0)
    completed_or_failed_ids = set(getattr(workspace.plan_state, 'completed_menu_request_ids', []) or []) | set(getattr(workspace.plan_state, 'failed_menu_request_ids', []) or [])

    has_target_surface = bool(
        list(getattr(workspace, 'bangumi_subjects', []) or [])
        or list(getattr(workspace, 'bangumi_items', []) or [])
        or list(getattr(workspace, 'bangumi_span_cards', []) or [])
    )
    subject_search_ids = [
        str(item.get('request_id') or '')
        for item in list(menu.get('prompt_summaries') or [])
        if str(item.get('request_type') or '') == 'subject_search'
        and str(item.get('request_id') or '') not in completed_or_failed_ids
    ]
    weak_recall_retry_pending = 'weak_subject_recall_retry_pending' in list(getattr(workspace, 'diagnostics', []) or [])
    if not has_target_surface or weak_recall_retry_pending:
        selected_ids = [request_id for request_id in subject_search_ids if request_id]
        selected_ids = selected_ids[:max_requests] if max_requests > 0 else list(selected_ids)
        if not selected_ids:
            return None
        plan = EvidencePlan(
            plan_id=f"PLAN_SUBJECT_{workspace.header.case_id or 'CASE'}_{workspace.header.round_index + 1}",
            plan_kind='subject_recall',
            selected_menu_request_ids=selected_ids,
            completed_menu_request_ids=[],
            failed_menu_request_ids=[],
            ready_span_refs=[],
            planned_span_request_count=int(menu_audit.get('planned_span_request_count') or 0),
            selected_span_request_count=0,
            completed_span_request_count=int(menu_audit.get('completed_span_request_count') or 0),
            span_rows_with_candidates=int(menu_audit.get('span_rows_with_candidates') or 0),
            span_rows_without_candidates=int(menu_audit.get('span_rows_without_candidates') or 0),
            plan_status='in_progress',
            goal='collect Bangumi subject candidates before target span proof',
            stop_conditions=['budget exhausted', 'selected subject search requests executed'],
            risk_flags=['agent_composed_queries', 'no_semantic_mapping'],
            steps=[EvidencePlanStep(selected_menu_request_ids=selected_ids)],
        )
        return EvidencePlannerOutput(selected_evidence=True, plan=plan)

    has_subjects = bool(list(getattr(workspace, 'bangumi_subjects', []) or []))
    has_items = bool(list(getattr(workspace, 'bangumi_items', []) or []))
    if has_subjects and not has_items:
        episode_list_ids = [
            str(item.get('request_id') or '')
            for item in list(menu.get('prompt_summaries') or [])
            if str(item.get('request_type') or '') == 'episode_list'
            and str(item.get('request_id') or '').startswith('REQ_EPISODE_LIST_')
            and str(item.get('request_id') or '') not in completed_or_failed_ids
        ]
        if episode_list_ids:
            max_slots = max_requests if max_requests > 0 else len(episode_list_ids)
            selected_ids = [request_id for request_id in episode_list_ids if request_id][:max_slots]
            selected_ids = selected_ids[:max_slots]
            if selected_ids:
                plan = EvidencePlan(
                    plan_id=f"PLAN_EPISODE_{workspace.header.case_id or 'CASE'}_{workspace.header.round_index + 1}",
                    plan_kind='episode_recall',
                    selected_menu_request_ids=selected_ids,
                    completed_menu_request_ids=[],
                    failed_menu_request_ids=[],
                    ready_span_refs=[],
                    planned_span_request_count=int(menu_audit.get('planned_span_request_count') or 0),
                    selected_span_request_count=0,
                    completed_span_request_count=int(menu_audit.get('completed_span_request_count') or 0),
                    span_rows_with_candidates=int(menu_audit.get('span_rows_with_candidates') or 0),
                    span_rows_without_candidates=int(menu_audit.get('span_rows_without_candidates') or 0),
                    plan_status='in_progress',
                    goal='collect episode targets before semantic span/window selection',
                    stop_conditions=['budget exhausted', 'selected episode requests executed'],
                    risk_flags=['deterministic', 'no_semantic_mapping', 'no_unanchored_span_guess'],
                    steps=[EvidencePlanStep(selected_menu_request_ids=selected_ids)],
                )
                return EvidencePlannerOutput(selected_evidence=True, plan=plan)

    if not any(str(getattr(card, 'span_scope', '') or '') != 'package' for card in getattr(workspace, 'local_span_cards', []) or []):
        return None
    draft = getattr(workspace, 'mapping_draft', None)
    dossier = workspace.to_dossier(round_context='deterministic_planner')
    special_row_refs = special_eligible_open_row_refs(draft, dossier)
    has_detail_equivalent_spans = any(bool(getattr(card, 'detail_equivalent', False)) for card in getattr(workspace, 'bangumi_span_cards', []) or [])
    if has_detail_equivalent_spans and draft is None:
        return None

    special_request_ids = [
        str(item.get('request_id') or '')
        for item in list(menu.get('prompt_summaries') or [])
        if str(item.get('request_id') or '').startswith(('REQ_SPECIAL_',))
        and str(item.get('request_id') or '') not in completed_or_failed_ids
    ]
    special_already_attempted = any(str(request_id).startswith('REQ_SPECIAL_') for request_id in completed_or_failed_ids)
    if special_row_refs and special_request_ids and not special_already_attempted:
        max_slots = max_requests if max_requests > 0 else len(special_request_ids)
        selected_ids = [request_id for request_id in special_request_ids if request_id][:max_slots]
        if selected_ids:
            plan = EvidencePlan(
                plan_id=f"PLAN_SPECIAL_{workspace.header.case_id or 'CASE'}_{workspace.header.round_index + 1}",
                plan_kind='special_recall',
                selected_menu_request_ids=selected_ids,
                completed_menu_request_ids=[],
                failed_menu_request_ids=[],
                ready_span_refs=[],
                planned_span_request_count=int(menu_audit.get('planned_span_request_count') or 0),
                selected_span_request_count=0,
                completed_span_request_count=int(menu_audit.get('completed_span_request_count') or 0),
                span_rows_with_candidates=int(menu_audit.get('span_rows_with_candidates') or 0),
                span_rows_without_candidates=int(menu_audit.get('span_rows_without_candidates') or 0),
                plan_status='in_progress',
                goal='collect special/movie/related subject targets for singleton unresolved local rows',
                stop_conditions=['budget exhausted', 'selected special evidence requests executed', 'no pending special requests'],
                risk_flags=['deterministic', 'special_investigation_candidate', 'no_semantic_mapping'],
                steps=[EvidencePlanStep(selected_menu_request_ids=selected_ids)],
            )
            return EvidencePlannerOutput(selected_evidence=True, plan=plan)
    planned_ids = [
        str(item.get('request_id') or '')
        for item in list(menu.get('prompt_summaries') or [])
        if str(item.get('request_id') or '').startswith('REQ_TARGET_SPAN_LS')
        and str(item.get('request_id') or '') not in completed_or_failed_ids
    ]
    planned_ids = [request_id for request_id in planned_ids if request_id and request_id != 'REQ_TARGET_SPAN_LS_PACKAGE']
    selected_ids = planned_ids[:max_requests] if max_requests > 0 else list(planned_ids)
    if has_items and selected_ids:
        plan = EvidencePlan(
            plan_id=f"PLAN_SPAN_{workspace.header.case_id or 'CASE'}_{workspace.header.round_index + 1}",
            plan_kind='span_proof',
            selected_menu_request_ids=selected_ids,
            completed_menu_request_ids=[],
            failed_menu_request_ids=[],
            ready_span_refs=[card.ref for card in getattr(workspace, 'bangumi_span_cards', []) or [] if bool(getattr(card, 'detail_equivalent', False))],
            planned_span_request_count=int(menu_audit.get('planned_span_request_count') or len(planned_ids)),
            selected_span_request_count=len(selected_ids),
            completed_span_request_count=int(menu_audit.get('completed_span_request_count') or 0),
            span_rows_with_candidates=int(menu_audit.get('span_rows_with_candidates') or 0),
            span_rows_without_candidates=int(menu_audit.get('span_rows_without_candidates') or 0),
            plan_status='in_progress',
            goal='collect child span proof before judge review',
            stop_conditions=['budget exhausted', 'selected child span requests executed', 'planner plan is span_proof only'],
            risk_flags=['deterministic', 'no_semantic_mapping'],
            steps=[EvidencePlanStep(selected_menu_request_ids=selected_ids)],
        )
        return EvidencePlannerOutput(selected_evidence=True, plan=plan)

    if special_row_refs and special_request_ids:
        max_slots = max_requests if max_requests > 0 else len(special_request_ids)
        selected_ids = [request_id for request_id in special_request_ids if request_id][:max_slots]
        if selected_ids:
            plan = EvidencePlan(
                plan_id=f"PLAN_SPECIAL_{workspace.header.case_id or 'CASE'}_{workspace.header.round_index + 1}",
                plan_kind='special_recall',
                selected_menu_request_ids=selected_ids,
                completed_menu_request_ids=[],
                failed_menu_request_ids=[],
                ready_span_refs=[],
                planned_span_request_count=int(menu_audit.get('planned_span_request_count') or 0),
                selected_span_request_count=0,
                completed_span_request_count=int(menu_audit.get('completed_span_request_count') or 0),
                span_rows_with_candidates=int(menu_audit.get('span_rows_with_candidates') or 0),
                span_rows_without_candidates=int(menu_audit.get('span_rows_without_candidates') or 0),
                plan_status='in_progress',
                goal='collect special/movie/related subject targets for singleton unresolved local rows',
                stop_conditions=['budget exhausted', 'selected special evidence requests executed', 'no pending special requests'],
                risk_flags=['deterministic', 'special_investigation_candidate', 'no_semantic_mapping'],
                steps=[EvidencePlanStep(selected_menu_request_ids=selected_ids)],
            )
            return EvidencePlannerOutput(selected_evidence=True, plan=plan)

    return None
