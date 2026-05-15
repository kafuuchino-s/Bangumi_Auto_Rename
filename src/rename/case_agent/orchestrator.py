from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Literal

from .evidence_broker import EvidenceBroker
from .evidence_menu import build_executable_evidence_menu
from .evidence_menu_resolver import resolve_evidence_menu_requests
from .evidence_request_normalizer import normalize_evidence_requests
from .case_planner import build_child_workspace, call_case_planner, verify_case_planning_output
from .case_briefing_agent import call_case_briefing_agent
from .assignment_expander import expand_mapping_draft
from .mapping_draft import apply_mapping_patches, build_initial_mapping_draft, compact_mapping_draft, compute_local_span_partition_coverage, summarize_mapping_draft_coverage
from .mapping_draft import compute_mapping_draft_accounting
from .mapping_draft import normalize_mapping_patch_op
from .mapping_editor import call_mapping_draft_editor
from .mapping_intent_compiler import MappingIntentCompiler
from .orchestrator_agent import (
    ExecuteEvidenceToolArgs,
    FinishCaseToolArgs,
    MaterializeQueriesToolArgs,
    OrchestratorAgentSession,
    OrchestratorAgentToolCall,
    ProposeCaseUnderstandingToolArgs,
    ProposeMappingIntentsToolArgs,
    ReconsiderSplitToolArgs,
    UpdateNotebookToolArgs,
    call_orchestrator_agent,
    record_orchestrator_tool_output,
    orchestrator_session_audit,
)
from .planner import build_deterministic_evidence_plan
from .query_composer import call_query_composer
from .local_structure_agent import call_local_structure_agent
from .dossier import build_bounded_case_dossier
from .policy import normalize_fail_closed, build_action_policy
from .judge_client import call_case_judge
from .prompting import _recommended_neutral_requests
from .special_investigation import is_special_eligible_span, special_eligible_open_row_refs, special_eligible_row_refs, special_like_item_refs
from .supplemental_policy import ALLOWED_SUPPLEMENTAL_REASON_KINDS, classify_supplemental_reason, local_ref_text_for_supplemental_issue, main_file_refs_for_mapping_row, supplemental_category_supported_by_text, supplemental_reason_from_local_ref, supplemental_row_policy_issues
from .surface_ledger import build_surface_ledger
from .notebook import apply_notebook_updates, build_initial_investigation_notebook, build_notebook, close_notebook_agenda_for_mapping_patches, human_next_action_blockers, validate_case_briefing_refs
from .issue_router import route_verifier_issues
from .models import CaseBriefingOutput, CaseBriefingWorkUnit, CaseJudgeOutput, CasePlanningOutput, CaseVerifierResult, EvidenceBatchResult, EvidencePlan, EvidencePlannerOutput, FailClosedReason, Finding, LocalSpanCard, MappingDraftPatch, MappingDraftRow, QueryCard, VerifierIssue
from .models import EvidenceRequest, MappingDraft
from .verifier import _compact_fail_closed_related_refs, verify_judge_output, verify_mapping_draft_accounting
from .workspace import CaseEvidenceWorkspace


InvestigationAction = Literal[
    'propose_case_understanding',
    'compose_queries',
    'plan_evidence',
    'execute_evidence',
    'edit_mapping_draft',
    'propose_mapping_intents',
    'verify_mapping_draft',
    'judge_semantic_blocker',
    'update_notebook',
    'reconsider_split',
    'fail_closed',
    'accepted',
]


def _workspace_preserving_state(workspace: CaseEvidenceWorkspace, **updates) -> CaseEvidenceWorkspace:
    updated = CaseEvidenceWorkspace.from_cards(
        header=updates.get('header', workspace.header),
        budget=updates.get('budget', workspace.budget),
        contract=updates.get('contract', workspace.contract),
        local_files=updates.get('local_files', workspace.local_files),
        local_clusters=updates.get('local_clusters', workspace.local_clusters),
        local_span_cards=updates.get('local_span_cards', workspace.local_span_cards),
        bangumi_subjects=updates.get('bangumi_subjects', workspace.bangumi_subjects),
        bangumi_relations=updates.get('bangumi_relations', workspace.bangumi_relations),
        bangumi_groups=updates.get('bangumi_groups', workspace.bangumi_groups),
        bangumi_items=updates.get('bangumi_items', workspace.bangumi_items),
        bangumi_span_cards=updates.get('bangumi_span_cards', workspace.bangumi_span_cards),
        query_cards=updates.get('query_cards', workspace.query_cards),
        provenance_cards=updates.get('provenance_cards', workspace.provenance_cards),
        previous_hypotheses=updates.get('previous_hypotheses', workspace.previous_hypotheses),
        previous_evidence_results=updates.get('previous_evidence_results', workspace.previous_evidence_results),
        verifier_issues=updates.get('verifier_issues', workspace.verifier_issues),
        diagnostics=updates.get('diagnostics', workspace.diagnostics),
        plan_state=updates.get('plan_state', workspace.plan_state),
        mapping_draft=updates.get('mapping_draft', workspace.mapping_draft),
        mapping_draft_patches=updates.get('mapping_draft_patches', workspace.mapping_draft_patches),
        mapping_draft_candidate_comparisons=updates.get('mapping_draft_candidate_comparisons', getattr(workspace, 'mapping_draft_candidate_comparisons', [])),
        case_briefing=updates.get('case_briefing', getattr(workspace, 'case_briefing', None)),
        investigation_notebook=updates.get('investigation_notebook', getattr(workspace, 'investigation_notebook', None)),
    )
    object.__setattr__(updated, 'seen_detail_refs', list(updates.get('seen_detail_refs', getattr(workspace, 'seen_detail_refs', []) or [])))
    object.__setattr__(updated, 'judge_request_audits', list(updates.get('judge_request_audits', getattr(workspace, 'judge_request_audits', []) or [])))
    return updated


def _has_composed_subject_search_query(workspace: CaseEvidenceWorkspace) -> bool:
    return any(
        str(getattr(card, 'query_kind', '') or '') == 'subject_search'
        and str(getattr(card, 'query_origin', '') or '') == 'agent_composed'
        and str(getattr(card, 'ref', '') or '').startswith('QC')
        and str(getattr(card, 'query_text', '') or '').strip()
        for card in list(getattr(workspace, 'query_cards', []) or [])
    )


def _all_composed_subject_queries_searched_without_results(workspace: CaseEvidenceWorkspace) -> bool:
    composed_refs = {
        str(getattr(card, 'ref', '') or '')
        for card in list(getattr(workspace, 'query_cards', []) or [])
        if str(getattr(card, 'query_kind', '') or '') == 'subject_search'
        and str(getattr(card, 'query_origin', '') or '') == 'agent_composed'
        and str(getattr(card, 'ref', '') or '').startswith('QC')
        and str(getattr(card, 'query_text', '') or '').strip()
    }
    if not composed_refs:
        return False
    searched_refs: set[str] = set()
    result_refs: list[str] = []
    for batch in list(getattr(workspace, 'previous_evidence_results', []) or []):
        for rr in list(getattr(batch, 'request_results', []) or getattr(batch, 'results', []) or []):
            if str(getattr(rr, 'request_type', '') or '') != 'subject_search':
                continue
            request_ref = str(getattr(rr, 'request_ref', '') or '')
            if request_ref.startswith('REQ_SUBJECT_SEARCH_QC'):
                searched_refs.add(request_ref.replace('REQ_SUBJECT_SEARCH_', '', 1))
            result_refs.extend(str(ref or '') for ref in list(getattr(rr, 'response_refs', []) or []) if str(ref or ''))
    return bool(composed_refs) and composed_refs <= searched_refs and not result_refs


def _needs_alternate_subject_query_after_empty_recall(workspace: CaseEvidenceWorkspace) -> bool:
    if list(getattr(workspace, 'bangumi_subjects', []) or []):
        return False
    if workspace.budget.max_evidence_batches and workspace.budget.used_evidence_batches >= workspace.budget.max_evidence_batches:
        return False
    if workspace.budget.max_subject_searches and workspace.budget.used_subject_searches >= workspace.budget.max_subject_searches:
        return False
    if 'alternate_subject_query_exhausted' in (getattr(workspace, 'diagnostics', []) or []):
        return False
    return _all_composed_subject_queries_searched_without_results(workspace)


def _has_pending_composed_subject_search_query(workspace: CaseEvidenceWorkspace) -> bool:
    completed_or_failed = set(getattr(workspace.plan_state, 'completed_menu_request_ids', []) or []) | set(getattr(workspace.plan_state, 'failed_menu_request_ids', []) or [])
    for card in list(getattr(workspace, 'query_cards', []) or []):
        if (
            str(getattr(card, 'query_kind', '') or '') == 'subject_search'
            and str(getattr(card, 'query_origin', '') or '') == 'agent_composed'
            and str(getattr(card, 'ref', '') or '').startswith('QC')
            and str(getattr(card, 'query_text', '') or '').strip()
            and f'REQ_SUBJECT_SEARCH_{card.ref}' not in completed_or_failed
        ):
            return True
    return False


_TARGET_SIDE_EVIDENCE_REQUEST_TYPES = {
    'subject_search',
    'subject_lookup',
    'related_expansion',
    'episode_list',
    'episode_detail',
    'target_detail',
    'target_window',
    'target_span',
}

_REQUIRES_SUBJECT_EVIDENCE_TYPES = {
    'subject_lookup',
    'related_expansion',
    'episode_list',
    'episode_detail',
    'target_detail',
    'target_window',
    'target_span',
}

_REQUIRES_ITEM_EVIDENCE_TYPES = {
    'episode_detail',
    'target_detail',
    'target_window',
    'target_span',
}


def _completed_or_failed_menu_request_ids(workspace: CaseEvidenceWorkspace) -> set[str]:
    return set(getattr(workspace.plan_state, 'completed_menu_request_ids', []) or []) | set(getattr(workspace.plan_state, 'failed_menu_request_ids', []) or [])


def _filter_stale_menu_request_ids(workspace: CaseEvidenceWorkspace, request_ids: list[str]) -> tuple[list[str], list[str]]:
    completed_or_failed = _completed_or_failed_menu_request_ids(workspace)
    fresh: list[str] = []
    stale: list[str] = []
    for request_id in list(request_ids or []):
        rid = str(request_id or '')
        if not rid:
            continue
        if rid in completed_or_failed:
            stale.append(rid)
        else:
            fresh.append(rid)
    return _dedupe_preserve_order(fresh), _dedupe_preserve_order(stale)


def _remaining_executable_menu_summaries(workspace: CaseEvidenceWorkspace, *, target_side_only: bool = False) -> list[dict[str, object]]:
    completed_or_failed = _completed_or_failed_menu_request_ids(workspace)
    menu = build_executable_evidence_menu(workspace)
    summaries: list[dict[str, object]] = []
    for item in list(menu.get('prompt_summaries') or []):
        request_id = str(item.get('request_id') or '')
        if not request_id or request_id in completed_or_failed:
            continue
        if target_side_only and str(item.get('request_type') or '') not in _TARGET_SIDE_EVIDENCE_REQUEST_TYPES:
            continue
        summaries.append(item)
    return summaries


def _remaining_executable_menu_request_ids(workspace: CaseEvidenceWorkspace, *, target_side_only: bool = False) -> list[str]:
    return [
        str(item.get('request_id') or '')
        for item in _remaining_executable_menu_summaries(workspace, target_side_only=target_side_only)
        if str(item.get('request_id') or '')
    ]


def _phase_route_remaining_target_side_request_ids(
    workspace: CaseEvidenceWorkspace,
    request_ids: list[str],
) -> tuple[list[str], dict[str, object]]:
    summaries = _remaining_executable_menu_summaries(workspace, target_side_only=True)
    type_by_id = _request_summary_type_by_id(summaries)
    selected_ids = [
        request_id
        for request_id in _dedupe_preserve_order([str(value or '') for value in list(request_ids or [])])
        if request_id in type_by_id
    ]
    requested_types = _dedupe_preserve_order([type_by_id.get(request_id, '') for request_id in selected_ids])
    return _evidence_phase_request_ids_for_editor_intent(
        workspace,
        summaries,
        selected_ids,
        requested_types,
    )


def _workspace_has_bangumi_subjects(workspace: CaseEvidenceWorkspace) -> bool:
    return bool(list(getattr(workspace, 'bangumi_subjects', []) or []))


def _workspace_has_bangumi_items(workspace: CaseEvidenceWorkspace) -> bool:
    return bool(list(getattr(workspace, 'bangumi_items', []) or []))


def _request_summary_type_by_id(summaries: list[dict[str, object]]) -> dict[str, str]:
    return {
        str(summary.get('request_id') or ''): str(summary.get('request_type') or '')
        for summary in list(summaries or [])
        if str(summary.get('request_id') or '')
    }


def _request_summary_source_refs(summary: dict[str, object]) -> list[str]:
    return [str(ref or '') for ref in list(summary.get('source_refs') or []) if str(ref or '')]


def _request_ids_matching_subject_refs(
    summaries: list[dict[str, object]],
    *,
    request_types: set[str],
    subject_refs: list[str],
) -> list[str]:
    subjects = {str(ref or '') for ref in list(subject_refs or []) if str(ref or '')}
    if not subjects:
        return []
    broad: list[str] = []
    exact: list[str] = []
    for summary in list(summaries or []):
        request_id = str(summary.get('request_id') or '')
        if not request_id or str(summary.get('request_type') or '') not in request_types:
            continue
        source_subjects = {
            ref for ref in _request_summary_source_refs(summary)
            if ref.startswith('BS')
        }
        if not subjects.intersection(source_subjects):
            continue
        broad.append(request_id)
        if source_subjects and source_subjects <= subjects:
            exact.append(request_id)
    return exact or broad


def _subject_refs_from_evidence_tool_args(args: ExecuteEvidenceToolArgs) -> list[str]:
    refs = [str(ref or '') for ref in list(getattr(args, 'subject_refs', []) or []) if str(ref or '')]
    for ref in list(getattr(args, 'item_refs', []) or []):
        value = str(ref or '')
        if value.startswith('BS'):
            refs.append(value)
    return _dedupe_preserve_order(refs)


def _subject_refs_from_intent_patches(patches: list[MappingDraftPatch]) -> list[str]:
    return _dedupe_preserve_order([
        str(ref or '')
        for patch in list(patches or [])
        for ref in list(getattr(patch, 'subject_refs', []) or [])
        if str(ref or '')
    ])


def _request_summary_for_request(request: EvidenceRequest) -> dict[str, object]:
    source_refs: list[str] = []
    if request.request_type == 'target_span':
        source_refs = [
            str(request.local_span_ref or ''),
            *[str(ref or '') for ref in list(request.subject_refs or [])],
            *[str(ref or '') for ref in list(request.group_refs or [])],
        ]
    else:
        source_refs = [
            *[str(ref or '') for ref in list(request.anchor_file_refs or [])],
            *[str(ref or '') for ref in list(request.subject_refs or [])],
            *[str(ref or '') for ref in list(request.group_refs or [])],
            *[str(ref or '') for ref in list(request.item_refs or [])],
            *[str(ref or '') for ref in list(request.query_refs or [])],
            str(request.local_span_ref or ''),
        ]
    return {
        'request_id': str(request.request_ref or ''),
        'request_type': str(request.request_type or ''),
        'summary': str(request.reason or request.request_type or ''),
        'source_refs': _dedupe_preserve_order([ref for ref in source_refs if ref])[:8],
        'expected_result': str(request.expected_decision or 'unknown'),
        'neutral': True,
    }


def _agent_subject_request_for_id(request_id: str, subject_refs: list[str], request_types: list[str]) -> EvidenceRequest | None:
    rid = str(request_id or '')
    subjects = set(_dedupe_preserve_order([str(ref or '') for ref in list(subject_refs or []) if str(ref or '')]))
    requested = {str(value or '') for value in list(request_types or []) if str(value or '')}
    if rid.startswith('REQ_SUBJECT_LOOKUP_'):
        subject_ref = rid.replace('REQ_SUBJECT_LOOKUP_', '', 1)
        if subject_ref in subjects and (not requested or 'subject_lookup' in requested):
            return EvidenceRequest(
                request_ref=rid,
                request_type='subject_lookup',
                subject_refs=[subject_ref],
                reason='agent-selected subject needs subject detail',
                expected_decision='need_more_evidence',
            )
    if rid.startswith('REQ_EPISODE_LIST_'):
        subject_ref = rid.replace('REQ_EPISODE_LIST_', '', 1)
        if subject_ref in subjects and (not requested or 'episode_list' in requested):
            return EvidenceRequest(
                request_ref=rid,
                request_type='episode_list',
                subject_refs=[subject_ref],
                include_episode_cards=True,
                max_episode_cards=240,
                reason='agent-selected subject needs visible episode targets',
                expected_decision='need_more_evidence',
            )
    return None


def _augment_menu_with_agent_subject_requests(
    summaries: list[dict[str, object]],
    registry: dict[str, EvidenceRequest],
    *,
    subject_refs: list[str],
    request_types: list[str],
) -> tuple[list[dict[str, object]], dict[str, EvidenceRequest], list[str]]:
    subjects = _dedupe_preserve_order([str(ref or '') for ref in list(subject_refs or []) if str(ref or '')])
    requested = {str(value or '') for value in list(request_types or []) if str(value or '')}
    if not subjects or not requested:
        return summaries, registry, []
    updated_summaries = list(summaries)
    updated_registry = dict(registry)
    added: list[str] = []
    for subject_ref in subjects:
        requests: list[EvidenceRequest] = []
        if 'subject_lookup' in requested:
            requests.append(EvidenceRequest(
                request_ref=f'REQ_SUBJECT_LOOKUP_{subject_ref}',
                request_type='subject_lookup',
                subject_refs=[subject_ref],
                reason='agent-selected subject needs subject detail',
                expected_decision='need_more_evidence',
            ))
        if 'episode_list' in requested:
            requests.append(EvidenceRequest(
                request_ref=f'REQ_EPISODE_LIST_{subject_ref}',
                request_type='episode_list',
                subject_refs=[subject_ref],
                include_episode_cards=True,
                max_episode_cards=240,
                reason='agent-selected subject needs visible episode targets',
                expected_decision='need_more_evidence',
            ))
        for request in requests:
            if request.request_ref in updated_registry:
                continue
            updated_registry[request.request_ref] = request
            updated_summaries.append(_request_summary_for_request(request))
            added.append(request.request_ref)
    return updated_summaries, updated_registry, added


def _latest_blocked_evidence_agenda(workspace: CaseEvidenceWorkspace) -> tuple[list[str], list[str]]:
    audits = list(getattr(workspace, 'judge_request_audits', []) or [])
    latest: dict[str, object] | None = None
    for audit in reversed(audits):
        if isinstance(audit, dict) and audit.get('note') == 'orchestrator_mapping_intents_result':
            latest = audit
            break
    if not latest:
        return [], []
    requested_types = _dedupe_preserve_order([
        str(value or '')
        for value in list(latest.get('requested_evidence') or [])
        if str(value or '')
    ])
    blocked_intents = list(latest.get('blocked_intents') or [])
    subject_refs = _dedupe_preserve_order([
        str(ref or '')
        for item in blocked_intents
        if isinstance(item, dict)
        for ref in list(item.get('subject_refs') or [])
        if str(ref or '')
    ])
    return requested_types, subject_refs


def _evidence_phase_request_ids_for_editor_intent(
    workspace: CaseEvidenceWorkspace,
    summaries: list[dict[str, object]],
    selected_ids: list[str],
    requested_types: list[str],
    subject_refs: list[str] | None = None,
) -> tuple[list[str], dict[str, object]]:
    type_by_id = _request_summary_type_by_id(summaries)
    requested_type_set = {str(value or '') for value in list(requested_types or []) if str(value or '')}
    selected_type_set = {type_by_id.get(request_id, '') for request_id in list(selected_ids or [])}
    requested_subject_refs = _dedupe_preserve_order([
        *[str(ref or '') for ref in list(subject_refs or [])],
        *[
            ref
            for request_id in list(selected_ids or [])
            for ref in _request_summary_source_refs(next((summary for summary in summaries if str(summary.get('request_id') or '') == request_id), {}))
            if str(ref or '').startswith('BS')
        ],
    ])
    needs_subject_surface = bool(
        requested_type_set & _REQUIRES_SUBJECT_EVIDENCE_TYPES
        or selected_type_set & _REQUIRES_SUBJECT_EVIDENCE_TYPES
    )
    needs_item_surface = bool(
        requested_type_set & _REQUIRES_ITEM_EVIDENCE_TYPES
        or selected_type_set & _REQUIRES_ITEM_EVIDENCE_TYPES
    )
    has_subjects = _workspace_has_bangumi_subjects(workspace)
    has_items = _workspace_has_bangumi_items(workspace)

    if needs_subject_surface and not has_subjects and not has_items:
        subject_search_ids = [
            str(summary.get('request_id') or '')
            for summary in summaries
            if str(summary.get('request_type') or '') == 'subject_search'
        ]
        return _dedupe_preserve_order(subject_search_ids), {
            'evidence_phase': 'subject_recall',
            'deferred_evidence_intent_count': len([request_id for request_id in selected_ids if type_by_id.get(request_id, '') != 'subject_search']),
            'target_span_blocked_by_missing_items_count': 0,
            'target_evidence_blocked_by_missing_subjects_count': len([request_id for request_id in selected_ids if type_by_id.get(request_id, '') in _REQUIRES_SUBJECT_EVIDENCE_TYPES]),
        }

    needs_episode_surface = bool(
        has_subjects
        and not has_items
        and (
            needs_item_surface
            or 'subject_lookup' in requested_type_set
            or 'subject_lookup' in selected_type_set
            or 'episode_list' in requested_type_set
            or 'episode_list' in selected_type_set
        )
    )
    if needs_episode_surface:
        episode_ids = [
            str(summary.get('request_id') or '')
            for summary in summaries
            if str(summary.get('request_type') or '') in {'subject_lookup', 'episode_list'}
        ]
        prioritized_episode_ids = _request_ids_matching_subject_refs(
            summaries,
            request_types={'subject_lookup', 'episode_list'},
            subject_refs=requested_subject_refs,
        )
        if prioritized_episode_ids:
            episode_ids = prioritized_episode_ids
        return _dedupe_preserve_order(episode_ids), {
            'evidence_phase': 'episode_recall',
            'prioritized_subject_refs': requested_subject_refs,
            'deferred_evidence_intent_count': len([request_id for request_id in selected_ids if type_by_id.get(request_id, '') not in {'subject_lookup', 'episode_list'}]),
            'target_span_blocked_by_missing_items_count': len([request_id for request_id in selected_ids if type_by_id.get(request_id, '') == 'target_span']),
            'target_evidence_blocked_by_missing_subjects_count': 0,
        }

    return selected_ids, {
        'evidence_phase': 'target_recall',
        'deferred_evidence_intent_count': 0,
        'target_span_blocked_by_missing_items_count': 0,
        'target_evidence_blocked_by_missing_subjects_count': 0,
        'prioritized_subject_refs': requested_subject_refs,
    }


def _execute_menu_request_ids(
    workspace: CaseEvidenceWorkspace,
    selected_ids: list[str],
    bangumi_client,
    evidence_batches: list[EvidenceBatchResult],
    *,
    note: str,
    planner_output: EvidencePlannerOutput | None = None,
) -> tuple[CaseEvidenceWorkspace, EvidenceBatchResult | None]:
    fresh_ids, stale_ids = _filter_stale_menu_request_ids(workspace, selected_ids)
    if stale_ids:
        workspace = _workspace_with_judge_audit(workspace, {
            'note': f'{note}_stale_menu_request_ids_ignored',
            'stale_menu_request_ids': stale_ids,
            'reason': 'request ids already completed or failed in this workspace',
        })
    if not fresh_ids or bangumi_client is None:
        return workspace, None
    resolved_requests, selected_menu_request_ids, unknown_menu_request_ids, resolved_menu_request_count = resolve_evidence_menu_requests(workspace, fresh_ids)
    dynamic_subject_refs: list[str] = []
    dynamic_request_types: list[str] = []
    if planner_output is not None:
        plan = getattr(planner_output, 'plan', None)
        dynamic_request_types = [str(value or '') for value in list(getattr(plan, 'risk_flags', []) or []) if str(value or '') in {'subject_lookup', 'episode_list'}]
        dynamic_subject_refs = [str(value or '') for value in list(getattr(plan, 'stop_conditions', []) or []) if str(value or '').startswith('BS')]
    if unknown_menu_request_ids and dynamic_subject_refs:
        recovered_requests: list[EvidenceRequest] = []
        still_unknown: list[str] = []
        for request_id in unknown_menu_request_ids:
            request = _agent_subject_request_for_id(request_id, dynamic_subject_refs, dynamic_request_types)
            if request is None:
                still_unknown.append(request_id)
                continue
            recovered_requests.append(request)
        if recovered_requests:
            resolved_requests = [*resolved_requests, *recovered_requests]
            selected_menu_request_ids = _dedupe_preserve_order([*selected_menu_request_ids, *[request.request_ref for request in recovered_requests]])
            unknown_menu_request_ids = still_unknown
    workspace = _workspace_with_judge_audit(workspace, {
        'note': f'{note}_menu_resolution',
        'selected_menu_request_ids': selected_menu_request_ids,
        'unknown_menu_request_ids': unknown_menu_request_ids,
        'resolved_menu_request_count': resolved_menu_request_count,
        'dynamic_subject_request_refs': [str(getattr(request, 'request_ref', '') or '') for request in resolved_requests if str(getattr(request, 'request_ref', '') or '').startswith(('REQ_SUBJECT_LOOKUP_', 'REQ_EPISODE_LIST_')) and str(getattr(request, 'request_ref', '') or '') in fresh_ids],
        'planner_plan_kind': getattr(getattr(planner_output, 'plan', None), 'plan_kind', ''),
    })
    if not resolved_requests:
        return workspace, None
    normalized_requests, normalization_audits = normalize_evidence_requests(workspace, resolved_requests)
    workspace = _workspace_with_request_normalization_audits(workspace, normalization_audits)
    broker = EvidenceBroker(bangumi_client)
    new_workspace, batch_result = broker.execute_batch(workspace, normalized_requests)
    evidence_batches.append(batch_result)
    if planner_output is not None:
        new_workspace = _workspace_with_planner_batch_audit(new_workspace, batch_result, planner_output)
    new_workspace = _refresh_mapping_draft_candidates(_workspace_with_initial_mapping_draft(new_workspace))
    return new_workspace, batch_result


def _regular_local_span_counts(workspace: CaseEvidenceWorkspace) -> list[int]:
    dossier = workspace.to_dossier(round_context='weak_subject_recall_gate')
    counts: list[int] = []
    for span in list(getattr(dossier, 'local_span_cards', []) or []):
        if str(getattr(span, 'span_scope', '') or '') == 'package':
            continue
        if is_special_eligible_span(span, dossier):
            continue
        count = int(getattr(span, 'file_ref_count', 0) or len(getattr(span, 'file_refs', []) or []))
        if count >= 2:
            counts.append(count)
    return counts


def _needs_alternate_subject_query_after_weak_recall(workspace: CaseEvidenceWorkspace) -> bool:
    diagnostics = list(getattr(workspace, 'diagnostics', []) or [])
    if 'weak_subject_recall_exhausted' in diagnostics or 'weak_subject_recall_retry_pending' in diagnostics:
        return False
    if workspace.budget.max_evidence_batches and workspace.budget.used_evidence_batches >= workspace.budget.max_evidence_batches:
        return False
    if workspace.budget.max_subject_searches and workspace.budget.used_subject_searches >= workspace.budget.max_subject_searches:
        return False
    regular_counts = _regular_local_span_counts(workspace)
    if not regular_counts or max(regular_counts) < 6:
        return False
    if _detail_equivalent_span_refs(workspace):
        return False
    subjects = list(getattr(workspace, 'bangumi_subjects', []) or [])
    items = [
        item for item in list(getattr(workspace, 'bangumi_items', []) or [])
        if str(getattr(item, 'item_kind', '') or '') in {'episode', 'unknown'}
    ]
    if not subjects:
        return False
    max_regular_count = max(regular_counts)
    subject_item_counts: dict[str, int] = {}
    for item in items:
        subject_item_counts[str(getattr(item, 'subject_ref', '') or '')] = subject_item_counts.get(str(getattr(item, 'subject_ref', '') or ''), 0) + 1
    if any(count >= max_regular_count for count in subject_item_counts.values()):
        return False
    return True


def _workspace_with_local_structure(workspace: CaseEvidenceWorkspace, ai_client) -> CaseEvidenceWorkspace:
    if any(
        str(getattr(card, 'ref', '') or '') != 'LS_PACKAGE'
        and str(getattr(card, 'span_scope', '') or '') != 'unpartitioned'
        for card in list(getattr(workspace, 'local_span_cards', []) or [])
    ):
        return workspace
    result = call_local_structure_agent(ai_client, workspace.to_dossier(round_context='local_structure'))
    audits = [*list(getattr(workspace, 'judge_request_audits', []) or [])]
    if result.request_audit:
        audits.append(result.request_audit)
    audits.append({
        'note': 'local_structure_agent_applied',
        'ok': result.ok,
        'span_count': len(result.local_span_cards),
        'error': result.error,
    })
    return _workspace_preserving_state(
        workspace,
        local_span_cards=result.local_span_cards,
        judge_request_audits=audits,
    )


def _workspace_with_case_briefing(workspace: CaseEvidenceWorkspace, ai_client) -> CaseEvidenceWorkspace:
    if getattr(workspace, 'case_briefing', None) is not None and getattr(workspace, 'investigation_notebook', None) is not None:
        return workspace
    result = call_case_briefing_agent(ai_client, workspace.to_dossier(round_context='case_briefing'))
    audits = [*list(getattr(workspace, 'judge_request_audits', []) or [])]
    if result.request_audit:
        audits.append(result.request_audit)
    briefing = result.output
    notebook = build_initial_investigation_notebook(briefing, workspace.to_dossier(round_context='case_briefing_notebook'))
    audits.append({
        'note': 'case_briefing_agent_applied',
        'ok': result.ok,
        'work_unit_count': len(list(getattr(briefing, 'work_units', []) or [])) if briefing is not None else 0,
        'title_hypothesis_count': len(list(getattr(briefing, 'title_hypotheses', []) or [])) if briefing is not None else 0,
        'open_question_count': len(list(getattr(notebook, 'open_questions', []) or [])),
        'error': result.error,
    })
    return _workspace_preserving_state(
        workspace,
        case_briefing=briefing,
        investigation_notebook=notebook,
        judge_request_audits=audits,
    )


def _case_understanding_applied(workspace: CaseEvidenceWorkspace) -> bool:
    return getattr(workspace, 'case_briefing', None) is not None


def _case_understanding_repartition_requested(workspace: CaseEvidenceWorkspace) -> bool:
    audits = list(getattr(workspace, 'judge_request_audits', []) or [])
    for audit in reversed(audits):
        if not isinstance(audit, dict):
            continue
        note = str(audit.get('note') or '')
        if note in {'case_understanding_applied', 'case_understanding_revised'}:
            break
        if note in {
            'orchestrator_reconsider_split_observation',
            'case_understanding_repartition_requested',
            'orchestrator_reconsider_split_requested',
        }:
            return True
    notebook = getattr(workspace, 'investigation_notebook', None)
    if notebook is None:
        return False
    for question in list(getattr(notebook, 'open_questions', []) or []):
        if str(getattr(question, 'question_kind', '') or '') == 'work_unit_repartition':
            return True
    for action in list(getattr(notebook, 'next_actions', []) or []):
        if str(getattr(action, 'action_type', '') or '') == 'work_unit_repartition':
            return True
    return False


def _sample_refs(values: list[str], *, limit: int = 4) -> list[str]:
    values = [value for value in values if value]
    if len(values) <= limit:
        return list(values)
    edge = max(1, limit // 2)
    return _dedupe_preserve_order([*values[:edge], *values[-edge:]])[:limit]


def _local_file_refs_for_understanding_ref(workspace: CaseEvidenceWorkspace, ref: str) -> list[str]:
    ref = str(ref or '')
    if not ref:
        return []
    main_refs = set(list(getattr(getattr(workspace, 'contract', None), 'main_file_refs', []) or []))
    if ref in main_refs:
        return [ref]
    file_refs = {
        str(getattr(card, 'ref', '') or '')
        for card in list(getattr(workspace, 'local_files', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }
    if ref in file_refs:
        return [ref]
    for span in list(getattr(workspace, 'local_span_cards', []) or []):
        if str(getattr(span, 'ref', '') or '') == ref:
            return [
                file_ref
                for file_ref in list(getattr(span, 'file_refs', []) or [])
                if not main_refs or file_ref in main_refs
            ]
    return []


def _understanding_unit_file_refs(workspace: CaseEvidenceWorkspace, unit: CaseBriefingWorkUnit) -> list[str]:
    explicit_file_refs: list[str] = []
    for ref in list(getattr(unit, 'file_refs', []) or []):
        explicit_file_refs.extend(_local_file_refs_for_understanding_ref(workspace, ref))
    if explicit_file_refs:
        return _dedupe_preserve_order(explicit_file_refs)

    refs: list[str] = []
    for ref in [*list(getattr(unit, 'local_refs', []) or []), *list(getattr(unit, 'span_refs', []) or [])]:
        refs.extend(_local_file_refs_for_understanding_ref(workspace, ref))
    return _dedupe_preserve_order(refs)


def _span_scope_for_understanding_unit(unit: CaseBriefingWorkUnit) -> str:
    text = ' '.join([
        str(getattr(unit, 'unit_kind', '') or ''),
        str(getattr(unit, 'label', '') or ''),
        ' '.join(str(value or '') for value in list(getattr(unit, 'source_form_hints', []) or [])),
    ]).casefold()
    if 'dir' in text or 'season' in text or 'series' in text:
        return 'directory'
    if any(marker in text for marker in ('regular', 'episode', 'main', 'tv')):
        return 'token_segment'
    if any(marker in text for marker in ('extra', 'special', 'ova', 'oad', 'sp', 'movie')):
        return 'residual'
    return 'unpartitioned'


def _compile_case_understanding(
    workspace: CaseEvidenceWorkspace,
    args: ProposeCaseUnderstandingToolArgs,
) -> tuple[CaseEvidenceWorkspace, dict[str, object]]:
    was_revision = _case_understanding_applied(workspace)
    raw_units = list(getattr(args, 'work_units', []) or [])
    main_refs = list(dict.fromkeys(list(getattr(getattr(workspace, 'contract', None), 'main_file_refs', []) or [])))
    issues: list[VerifierIssue] = []
    if not raw_units:
        issues.append(VerifierIssue(ref='case_understanding', issue_code='case_understanding_empty_work_units', severity='blocked', message='propose_case_understanding must provide at least one work unit'))

    expanded_by_unit: list[tuple[CaseBriefingWorkUnit, list[str]]] = []
    ownership: dict[str, list[str]] = {}
    for index, unit in enumerate(raw_units, start=1):
        unit_ref = str(getattr(unit, 'work_unit_ref', '') or f'WU{index}')
        file_refs = _understanding_unit_file_refs(workspace, unit)
        if not file_refs:
            issues.append(VerifierIssue(ref=unit_ref, issue_code='case_understanding_empty_work_unit', severity='blocked', message='work unit did not cite any visible local file coverage refs'))
        for file_ref in file_refs:
            if file_ref in main_refs:
                ownership.setdefault(file_ref, []).append(unit_ref)
        expanded_by_unit.append((unit, file_refs))

    if main_refs:
        missing = [ref for ref in main_refs if ref not in ownership]
        duplicates = [ref for ref, owners in ownership.items() if len(owners) > 1]
        if missing:
            issues.append(VerifierIssue(ref='case_understanding', issue_code='case_understanding_missing_main_refs', severity='blocked', message='work units must cover every main file ref exactly once', related_refs=missing[:12]))
        if duplicates:
            issues.append(VerifierIssue(ref='case_understanding', issue_code='case_understanding_duplicate_main_refs', severity='blocked', message='main file refs appeared in more than one work unit', related_refs=duplicates[:12]))

    candidate_briefing = CaseBriefingOutput(
        package_shape=str(getattr(args, 'package_shape', '') or ''),
        work_units=raw_units,
        title_hypotheses=list(getattr(args, 'title_hypotheses', []) or []),
        split_hints=list(getattr(args, 'split_hints', []) or []),
        evidence_questions=list(getattr(args, 'evidence_questions', []) or []),
        summary=str(getattr(args, 'summary', '') or getattr(args, 'reason', '') or ''),
    )
    issues.extend(validate_case_briefing_refs(candidate_briefing, workspace.to_dossier(round_context='case_understanding_validate')))
    if issues:
        issue_codes = _dedupe_preserve_order([str(getattr(issue, 'issue_code', '') or '') for issue in issues])
        workspace = _workspace_with_judge_audit(workspace, {
            'note': 'case_understanding_rejected',
            'issue_codes': issue_codes,
            'issues': [issue.model_dump(mode='json') for issue in issues[:12]],
            'reason': str(getattr(args, 'reason', '') or ''),
        })
        return workspace, {
            'status': 'rejected',
            'reason': 'case_understanding_contract_failed',
            'issue_codes': issue_codes,
            'issues': [issue.model_dump(mode='json') for issue in issues[:12]],
            'recommended_next_observation': 'retry propose_case_understanding with work units that cite visible LF/LS refs and cover every main LF exactly once',
        }

    package_span = LocalSpanCard(
        ref='LS_PACKAGE',
        span_scope='package',
        file_refs=main_refs,
        file_ref_count=len(main_refs),
        file_ref_range=[main_refs[0], main_refs[-1]] if main_refs else [],
        file_ref_samples=_sample_refs(main_refs),
        ordering_basis='path_order',
        title_cues=_dedupe_preserve_order([
            cue
            for unit in raw_units
            for cue in list(getattr(unit, 'title_hints', []) or [])
        ])[:8],
        confidence_facts=['case understanding package coverage shell'],
    )
    compiled_spans: list[LocalSpanCard] = [package_span]
    compiled_units: list[CaseBriefingWorkUnit] = []
    for index, (unit, file_refs) in enumerate(expanded_by_unit, start=1):
        span_ref = f'LS{index}'
        compiled_spans.append(LocalSpanCard(
            ref=span_ref,
            span_scope=_span_scope_for_understanding_unit(unit),
            parent_key=str(getattr(unit, 'label', '') or getattr(unit, 'work_unit_ref', '') or ''),
            file_refs=file_refs,
            file_ref_count=len(file_refs),
            file_ref_range=[file_refs[0], file_refs[-1]] if file_refs else [],
            file_ref_samples=_sample_refs(file_refs),
            ordering_basis='path_order',
            title_cues=list(getattr(unit, 'title_hints', []) or [])[:8],
            confidence_facts=[str(getattr(unit, 'reason', '') or 'case understanding work unit')],
        ))
        compiled_units.append(unit.model_copy(update={
            'work_unit_ref': str(getattr(unit, 'work_unit_ref', '') or f'WU{index}'),
            'file_refs': file_refs,
            'span_refs': [span_ref],
            'local_refs': _dedupe_preserve_order([*list(getattr(unit, 'local_refs', []) or []), span_ref]),
        }))

    briefing = candidate_briefing.model_copy(update={'work_units': compiled_units})
    repartition_requested = _case_understanding_repartition_requested(workspace)
    preserve_existing_case_memory = was_revision and getattr(workspace, 'mapping_draft', None) is not None and not repartition_requested
    preserved_mapping_draft = workspace.mapping_draft if preserve_existing_case_memory else None
    preserved_mapping_draft_patches = list(getattr(workspace, 'mapping_draft_patches', []) or []) if preserve_existing_case_memory else []
    preserved_mapping_draft_comparisons = list(getattr(workspace, 'mapping_draft_candidate_comparisons', []) or []) if preserve_existing_case_memory else []
    preserved_notebook = getattr(workspace, 'investigation_notebook', None) if preserve_existing_case_memory else None
    staged = _workspace_preserving_state(
        workspace,
        local_span_cards=compiled_spans,
        case_briefing=briefing,
        mapping_draft=preserved_mapping_draft,
        mapping_draft_patches=preserved_mapping_draft_patches,
        mapping_draft_candidate_comparisons=preserved_mapping_draft_comparisons,
        investigation_notebook=preserved_notebook,
    )
    if not preserve_existing_case_memory:
        notebook = build_initial_investigation_notebook(briefing, staged.to_dossier(round_context='case_understanding_notebook'))
        staged = _workspace_preserving_state(staged, investigation_notebook=notebook)
    elif repartition_requested and preserved_notebook is not None:
        notebook = preserved_notebook.model_copy(deep=True)
        notebook.open_questions = [
            question.model_copy(update={'status': 'answered'})
            if str(getattr(question, 'status', '') or 'open') == 'open'
            and str(getattr(question, 'question_kind', '') or '') == 'work_unit_repartition'
            else question
            for question in list(getattr(notebook, 'open_questions', []) or [])
        ]
        notebook.next_actions = [
            action.model_copy(update={'status': 'done'})
            if str(getattr(action, 'status', '') or 'open') == 'open'
            and str(getattr(action, 'action_type', '') or '') == 'work_unit_repartition'
            else action
            for action in list(getattr(notebook, 'next_actions', []) or [])
        ]
        staged = _workspace_preserving_state(staged, investigation_notebook=notebook)
    staged = _workspace_with_initial_mapping_draft(staged)
    staged = _refresh_mapping_draft_candidates(staged)
    staged = _workspace_with_tool_accounting_audit(staged, note='case_understanding_mapping_draft_accounting')
    staged = _workspace_with_judge_audit(staged, {
        'note': 'case_understanding_revised' if was_revision else 'case_understanding_applied',
        'work_unit_count': len(compiled_units),
        'local_span_refs': [span.ref for span in compiled_spans],
        'title_hypothesis_count': len(list(getattr(briefing, 'title_hypotheses', []) or [])),
        'open_question_count': len(list(getattr(getattr(staged, 'investigation_notebook', None), 'open_questions', []) or [])),
        'repartition_requested': repartition_requested,
        'preserved_mapping_draft': preserve_existing_case_memory,
        'preserved_notebook': preserve_existing_case_memory,
        'reason': str(getattr(args, 'reason', '') or ''),
    })
    return staged, {
        'status': 'ok',
        'workspace_changed': True,
        'case_understanding_applied': True,
        'case_understanding_revised': was_revision,
        'work_unit_count': len(compiled_units),
        'local_span_refs': [span.ref for span in compiled_spans],
        'draft_accounting': _mapping_draft_observation(staged).get('draft_accounting'),
        'open_rows': _mapping_draft_observation(staged).get('open_rows'),
        'executable_menu_summary': _executable_menu_observation(staged),
        'recommended_next_observation': 'materialize clean title queries or execute visible evidence; if enough Bangumi target surface is already visible, propose mapping intents',
    }


@dataclass
class CaseAgentRunResult:
    ok: bool
    case_id: str
    status: Literal['accepted', 'fail_closed', 'invalid', 'error']
    final_action: str
    final_output: CaseJudgeOutput | None
    final_verifier_result: CaseVerifierResult | None
    final_workspace: CaseEvidenceWorkspace
    judge_outputs: list[CaseJudgeOutput] = field(default_factory=list)
    evidence_batches: list[EvidenceBatchResult] = field(default_factory=list)
    summary: str = ''
    errors: list[str] = field(default_factory=list)
    planning_output: CasePlanningOutput | None = None
    child_results: list['CaseAgentRunResult'] = field(default_factory=list)


@dataclass
class _PlanningPhaseResult:
    workspace: CaseEvidenceWorkspace
    evidence_batches: list[EvidenceBatchResult] = field(default_factory=list)
    terminal_result: CaseAgentRunResult | None = None
    planning_output: CasePlanningOutput | None = None


@dataclass
class _MappingDraftEditorAttempt:
    result: CaseAgentRunResult | None
    workspace: CaseEvidenceWorkspace


@dataclass
class _InvestigationDecision:
    action: InvestigationAction
    reason: str = ''
    planner_output: object | None = None
    planner_key: tuple[str, tuple[str, ...]] | None = None


def run_local_bangumi_case_agent(
    initial_workspace: CaseEvidenceWorkspace,
    ai_client,
    bangumi_client,
    *,
    max_rounds: int | None = None,
    orchestrator_context_soft_token_limit: int | None = None,
    orchestrator_context_hard_token_limit: int | None = None,
    _planning_depth: int = 0,
) -> CaseAgentRunResult:
    workspace = initial_workspace
    planning_output: CasePlanningOutput | None = None
    planning_evidence_batches: list[EvidenceBatchResult] = []

    def _budget_exhausted_fail_closed(
        current_workspace: CaseEvidenceWorkspace,
        action: str,
        output: CaseJudgeOutput | None,
        verifier_result: CaseVerifierResult | None,
        *,
        reason: str = 'budget_exhausted',
    ) -> CaseAgentRunResult:
        accounting, _accounting_verifier_result = _mapping_draft_accounting_result(current_workspace)
        description_parts = [
            reason,
            f'evidence_batches={len(evidence_batches)}',
            f'used_evidence_batches={int(getattr(current_workspace.budget, "used_evidence_batches", 0) or 0)}',
            f'max_evidence_batches={int(getattr(current_workspace.budget, "max_evidence_batches", 0) or 0)}',
        ]
        if accounting is not None:
            description_parts.extend([
                f'unresolved_count={int(getattr(accounting, "unresolved_count", 0) or 0)}',
                f'needs_more_evidence_file_count={int(getattr(accounting, "needs_more_evidence_file_count", 0) or 0)}',
                f'unaligned_file_count={int(getattr(accounting, "unaligned_file_count", 0) or 0)}',
            ])
        fail_output = CaseJudgeOutput(
            action='fail_closed',
            fail_closed_reasons=[
                FailClosedReason(
                    ref='FR1',
                    reason_kind='insufficient_evidence',
                    description='; '.join(description_parts),
                    related_refs=_open_rows_without_candidates(getattr(current_workspace, 'mapping_draft', None))[:8],
                )
            ],
            summary='budget exhausted before accepted mapping',
        )
        fail_verifier = verify_judge_output(current_workspace.to_dossier(round_context='budget_exhausted_fail_closed'), fail_output)
        audited_workspace = _workspace_with_judge_audit(current_workspace, {
            'note': 'budget_exhausted_fail_closed',
            'reason': reason,
            'evidence_batch_count': len(evidence_batches),
            'verifier_passed': bool(getattr(fail_verifier, 'passed', False)),
        })
        return _result(True, audited_workspace.header.case_id, 'fail_closed', 'fail_closed', fail_output, fail_verifier or verifier_result, audited_workspace, judge_outputs, evidence_batches, 'budget_exhausted', [*errors, 'budget_exhausted'])

    def _no_new_evidence_fail_closed(
        current_workspace: CaseEvidenceWorkspace,
        *,
        reason: str = 'no_new_evidence',
        description: str = 'no new executable evidence remained before accepted mapping',
    ) -> CaseAgentRunResult:
        accounting, _accounting_verifier_result = _mapping_draft_accounting_result(current_workspace)
        description_parts = [
            reason,
            description,
            f'evidence_batches={len(evidence_batches)}',
            f'used_evidence_batches={int(getattr(current_workspace.budget, "used_evidence_batches", 0) or 0)}',
        ]
        if accounting is not None:
            description_parts.extend([
                f'unresolved_count={int(getattr(accounting, "unresolved_count", 0) or 0)}',
                f'needs_more_evidence_file_count={int(getattr(accounting, "needs_more_evidence_file_count", 0) or 0)}',
                f'unaligned_file_count={int(getattr(accounting, "unaligned_file_count", 0) or 0)}',
            ])
        fail_output = CaseJudgeOutput(
            action='fail_closed',
            fail_closed_reasons=[
                FailClosedReason(
                    ref='FR1',
                    reason_kind='insufficient_evidence',
                    description='; '.join(description_parts),
                    related_refs=[],
                )
            ],
            summary='no new evidence before accepted mapping',
        )
        fail_verifier = verify_judge_output(current_workspace.to_dossier(round_context='no_new_evidence_fail_closed'), fail_output)
        audited_workspace = _workspace_with_judge_audit(current_workspace, {
            'note': 'no_new_evidence_fail_closed',
            'reason': reason,
            'evidence_batch_count': len(evidence_batches),
            'verifier_passed': bool(getattr(fail_verifier, 'passed', False)),
            **_no_new_evidence_precondition_audit(current_workspace),
        })
        return _result(True, audited_workspace.header.case_id, 'fail_closed', 'fail_closed', fail_output, fail_verifier, audited_workspace, judge_outputs, evidence_batches, 'no_new_evidence', [*errors, reason])

    def _semantic_target_conflict_fail_closed(
        current_workspace: CaseEvidenceWorkspace,
        verifier_result: CaseVerifierResult,
        *,
        reason: str = 'verifier_rejected_unexecutable_verdict',
    ) -> CaseAgentRunResult:
        issues = list(getattr(verifier_result, 'issues', []) or [])
        conflict_dossier = current_workspace.to_dossier(round_context='semantic_target_conflict_fail_closed')
        visible_refs = {
            *list(getattr(getattr(conflict_dossier, 'visible_refs', None), 'local_file_refs', []) or []),
            *list(getattr(getattr(conflict_dossier, 'visible_refs', None), 'local_cluster_refs', []) or []),
            *list(getattr(getattr(conflict_dossier, 'visible_refs', None), 'bangumi_subject_refs', []) or []),
            *list(getattr(getattr(conflict_dossier, 'visible_refs', None), 'bangumi_relation_refs', []) or []),
            *list(getattr(getattr(conflict_dossier, 'visible_refs', None), 'bangumi_group_refs', []) or []),
            *list(getattr(getattr(conflict_dossier, 'visible_refs', None), 'bangumi_item_refs', []) or []),
            *list(getattr(getattr(conflict_dossier, 'visible_refs', None), 'query_refs', []) or []),
            *list(getattr(getattr(conflict_dossier, 'visible_refs', None), 'target_refs', []) or []),
            *[str(getattr(card, 'ref', '') or '') for card in list(getattr(conflict_dossier, 'local_span_cards', []) or [])],
            *[str(getattr(card, 'ref', '') or '') for card in list(getattr(conflict_dossier, 'bangumi_span_cards', []) or [])],
        }
        fail_output = CaseJudgeOutput(
            action='fail_closed',
            fail_closed_reasons=[
                FailClosedReason(
                    ref=f'FR{index}',
                    reason_kind='contradiction',
                    description=f'{str(getattr(issue, "issue_code", "") or "verifier_issue")}: {str(getattr(issue, "message", "") or reason)}',
                    related_refs=[ref for ref in list(getattr(issue, 'related_refs', []) or []) if str(ref or '') in visible_refs][:8],
                )
                for index, issue in enumerate(issues[:8], start=1)
            ] or [
                FailClosedReason(
                    ref='FR1',
                    reason_kind='contradiction',
                    description=reason,
                    related_refs=[],
                )
            ],
            summary='semantic target conflict',
        )
        fail_verifier = verify_judge_output(conflict_dossier, fail_output)
        audited_workspace = _workspace_with_judge_audit(current_workspace, {
            'note': 'semantic_target_conflict_fail_closed',
            'reason': reason,
            'source_verifier_issue_count': len(issues),
            'verifier_passed': bool(getattr(fail_verifier, 'passed', False)),
        })
        return _result(True, audited_workspace.header.case_id, 'fail_closed', 'fail_closed', fail_output, fail_verifier, audited_workspace, judge_outputs, evidence_batches, 'semantic_target_conflict', [*errors, reason])

    return _run_orchestrator_agent_main_loop(
        workspace,
        ai_client,
        bangumi_client,
        planning_output=planning_output,
        planning_evidence_batches=planning_evidence_batches,
        max_rounds=max_rounds,
        orchestrator_context_soft_token_limit=orchestrator_context_soft_token_limit,
        orchestrator_context_hard_token_limit=orchestrator_context_hard_token_limit,
    )
    broker = EvidenceBroker(bangumi_client)
    judge_outputs: list[CaseJudgeOutput] = []
    evidence_batches: list[EvidenceBatchResult] = list(planning_evidence_batches)
    errors: list[str] = []
    final_output: CaseJudgeOutput | None = None
    final_verifier_result: CaseVerifierResult | None = None
    final_action = ''
    policy_retry_used = False
    planner_output = None
    planner_batch_executed = False
    executed_planner_keys: set[tuple[str, tuple[str, ...]]] = set()
    orchestrator_session = OrchestratorAgentSession(case_id=workspace.header.case_id)
    orchestrator_agent_enabled = callable(getattr(ai_client, 'call_responses_tool_agent', None))
    orchestrator_soft_token_limit = max(8192, int(orchestrator_context_soft_token_limit or 180000))
    orchestrator_hard_token_limit = max(
        orchestrator_soft_token_limit + 1024,
        int(orchestrator_context_hard_token_limit or 300000),
    )
    while True:
        workspace = _refresh_mapping_draft_candidates(_workspace_with_initial_mapping_draft(workspace))
        active_orchestrator_tool_call: OrchestratorAgentToolCall | None = None
        if orchestrator_agent_enabled:
            agent_result = call_orchestrator_agent(
                ai_client,
                workspace,
                orchestrator_session,
                reason='select next Local to Bangumi investigation tool',
                soft_token_limit=orchestrator_soft_token_limit,
                hard_token_limit=orchestrator_hard_token_limit,
            )
            orchestrator_session = agent_result.session
            workspace = _workspace_with_judge_audit(workspace, agent_result.audit)
            if agent_result.ok and agent_result.tool_call is not None:
                active_orchestrator_tool_call = agent_result.tool_call
                workspace, decision, tool_acceptance = _decision_from_orchestrator_tool_call(workspace, active_orchestrator_tool_call)
                workspace = _workspace_with_judge_audit(workspace, {
                    'note': 'orchestrator_tool_selected',
                    'tool_name': active_orchestrator_tool_call.tool_name,
                    'tool_call_id': active_orchestrator_tool_call.call_id,
                    'accepted': bool(tool_acceptance.get('accepted')),
                    **tool_acceptance,
                })
                if decision is None:
                    accepted_tool_call = bool(tool_acceptance.get('accepted'))
                    if not accepted_tool_call:
                        orchestrator_session = replace(
                            orchestrator_session,
                            tool_rejection_count=orchestrator_session.tool_rejection_count + 1,
                        )
                    orchestrator_session = record_orchestrator_tool_output(
                        orchestrator_session,
                        active_orchestrator_tool_call,
                        {'status': 'ok' if accepted_tool_call else 'rejected', **tool_acceptance},
                    )
                    if accepted_tool_call or orchestrator_session.tool_rejection_count <= 3:
                        continue
                    decision = _next_investigation_action(workspace, executed_planner_keys=executed_planner_keys)
            else:
                workspace = _workspace_with_judge_audit(workspace, {
                    'note': 'orchestrator_agent_fallback_to_state_machine',
                    'error': agent_result.error,
                })
                decision = _next_investigation_action(workspace, executed_planner_keys=executed_planner_keys)
        else:
            decision = _next_investigation_action(workspace, executed_planner_keys=executed_planner_keys)
        workspace = _workspace_with_judge_audit(workspace, {
            'note': 'investigation_action_selected',
            'action': decision.action,
            'reason': decision.reason,
            'orchestrator_agent_enabled': orchestrator_agent_enabled,
        })
        if decision.action == 'compose_queries':
            composer_result = call_query_composer(
                ai_client,
                workspace.to_dossier(round_context='query_composer'),
                investigation_reason=decision.reason,
            )
            workspace = _workspace_with_judge_audit(workspace, getattr(composer_result, 'request_audit', None))
            if not composer_result.ok:
                error_text = composer_result.error or 'query composer call failed'
                lower = error_text.casefold()
                error_kind = 'context_overflow' if 'exceeds the context window' in lower else ('provider_no_response' if 'no response' in lower else 'provider_error')
                summary = 'query composer context overflow' if error_kind == 'context_overflow' else ('query composer infra no response' if error_kind == 'provider_no_response' else 'query composer infra error')
                return _result(False, workspace.header.case_id, 'error', 'query_composer', final_output, final_verifier_result, workspace, judge_outputs, evidence_batches, summary, [*errors, error_text, f'error_kind={error_kind}'])
            if not composer_result.query_cards:
                diagnostics = list(getattr(workspace, 'diagnostics', []) or [])
                if decision.reason == 'empty_subject_recall_requires_alternate_query' and 'alternate_subject_query_exhausted' not in diagnostics:
                    workspace = _workspace_preserving_state(workspace, diagnostics=[*diagnostics, 'alternate_subject_query_exhausted'])
                if decision.reason == 'weak_subject_recall_requires_alternate_query' and 'weak_subject_recall_exhausted' not in diagnostics:
                    workspace = _workspace_preserving_state(workspace, diagnostics=[*diagnostics, 'weak_subject_recall_exhausted'])
                workspace = _workspace_with_judge_audit(workspace, {
                    'note': 'query_composer_no_executable_queries',
                    'summary': getattr(composer_result.output, 'summary', '') if composer_result.output is not None else '',
                    'reason': decision.reason,
                })
                if decision.reason in {'empty_subject_recall_requires_alternate_query', 'weak_subject_recall_requires_alternate_query'}:
                    continue
                break
            workspace = workspace.with_query_cards(composer_result.query_cards)
            if decision.reason == 'weak_subject_recall_requires_alternate_query':
                diagnostics = list(getattr(workspace, 'diagnostics', []) or [])
                if 'weak_subject_recall_retry_pending' not in diagnostics:
                    workspace = _workspace_preserving_state(workspace, diagnostics=[*diagnostics, 'weak_subject_recall_retry_pending'])
            workspace = _workspace_with_judge_audit(workspace, {
                'note': 'query_composer_added_queries',
                'query_refs': [card.ref for card in composer_result.query_cards],
                'query_texts': [card.query_text for card in composer_result.query_cards],
            })
            if active_orchestrator_tool_call is not None:
                orchestrator_session = record_orchestrator_tool_output(
                    orchestrator_session,
                    active_orchestrator_tool_call,
                    {
                        'status': 'ok',
                        'query_refs': [card.ref for card in composer_result.query_cards],
                        'query_texts': [card.query_text for card in composer_result.query_cards],
                    },
                )
            continue
        if decision.action == 'edit_mapping_draft':
            draft_attempt = _try_mapping_draft_editor_acceptance_with_workspace(workspace, ai_client, judge_outputs, evidence_batches, bangumi_client=bangumi_client)
            workspace = draft_attempt.workspace
            if active_orchestrator_tool_call is not None:
                draft = getattr(workspace, 'mapping_draft', None)
                orchestrator_session = record_orchestrator_tool_output(
                    orchestrator_session,
                    active_orchestrator_tool_call,
                    {
                        'status': 'terminal' if draft_attempt.result is not None else 'ok',
                        'terminal_status': getattr(draft_attempt.result, 'status', '') if draft_attempt.result is not None else '',
                        'draft_row_count': len(list(getattr(draft, 'rows', []) or [])) if draft is not None else 0,
                    },
                )
            if draft_attempt.result is not None:
                return _with_planning_output(draft_attempt.result)
            next_decision = _next_investigation_action(workspace, executed_planner_keys=executed_planner_keys)
            if next_decision.action in {'compose_queries', 'execute_evidence', 'accepted', 'fail_closed'}:
                continue
            decision = _InvestigationDecision(action='judge_semantic_blocker', reason='mapping_draft_editor_no_terminal_result')
        if decision.action == 'accepted':
            draft_attempt = _try_mapping_draft_editor_acceptance_with_workspace(workspace, ai_client, judge_outputs, evidence_batches, bangumi_client=bangumi_client)
            workspace = draft_attempt.workspace
            if active_orchestrator_tool_call is not None:
                orchestrator_session = record_orchestrator_tool_output(
                    orchestrator_session,
                    active_orchestrator_tool_call,
                    {
                        'status': 'accepted_verified' if draft_attempt.result is not None and draft_attempt.result.status == 'accepted' else 'accepted_not_ready',
                        'terminal_status': getattr(draft_attempt.result, 'status', '') if draft_attempt.result is not None else '',
                    },
                )
            if draft_attempt.result is not None:
                return _with_planning_output(draft_attempt.result)
            if active_orchestrator_tool_call is not None:
                continue
            break
        if decision.action == 'fail_closed':
            accounting, accounting_verifier_result = _mapping_draft_accounting_result(workspace)
            if accounting is not None:
                reasons = [FailClosedReason(ref='FR1', reason_kind='insufficient_evidence', description=f'unresolved_count={int(getattr(accounting, "unresolved_count", 0) or 0)}', related_refs=[])]
                fail_output = CaseJudgeOutput(action='fail_closed', fail_closed_reasons=reasons, summary='mapping draft accounting unresolved')
                verifier_result = verify_judge_output(workspace.to_dossier(round_context='mapping_draft_fail_closed'), fail_output)
                workspace = _workspace_with_judge_audit(workspace, {
                    'note': 'mapping_draft_accounting_computed',
                    'mapping_draft_accounting': accounting.model_dump(mode='json') if hasattr(accounting, 'model_dump') else accounting,
                    'verifier_passed': bool(getattr(accounting_verifier_result, 'passed', False)) if accounting_verifier_result is not None else False,
                })
                if active_orchestrator_tool_call is not None:
                    orchestrator_session = record_orchestrator_tool_output(
                        orchestrator_session,
                        active_orchestrator_tool_call,
                        {
                            'status': 'fail_closed_verified',
                            'unresolved_count': int(getattr(accounting, "unresolved_count", 0) or 0),
                        },
                    )
                return _result(True, workspace.header.case_id, 'fail_closed', 'fail_closed', fail_output, verifier_result, workspace, judge_outputs, evidence_batches, 'no_new_evidence', [f'unresolved_count={int(getattr(accounting, "unresolved_count", 0) or 0)}'])
        if decision.action != 'execute_evidence' or decision.planner_output is None:
            break
        planner_output = decision.planner_output
        planned_ids, stale_ids = _filter_stale_menu_request_ids(workspace, list(planner_output.plan.selected_menu_request_ids or []))
        if stale_ids:
            workspace = _workspace_with_judge_audit(workspace, {
                'note': 'planner_stale_menu_request_ids_ignored',
                'planner_plan_kind': planner_output.plan.plan_kind,
                'stale_menu_request_ids': stale_ids,
            })
        planner_key = decision.planner_key or (str(planner_output.plan.plan_kind or ''), tuple(planned_ids))
        if planner_key in executed_planner_keys:
            break
        if not planned_ids:
            break
        if planned_ids != list(planner_output.plan.selected_menu_request_ids or []):
            planner_output = planner_output.model_copy(update={'plan': planner_output.plan.model_copy(update={'selected_menu_request_ids': planned_ids})})
        executed_planner_keys.add(planner_key)
        resolved_requests, selected_menu_request_ids, unknown_menu_request_ids, resolved_menu_request_count = resolve_evidence_menu_requests(workspace, planned_ids)
        if unknown_menu_request_ids:
            workspace = _workspace_with_judge_audit(workspace, {
                'note': 'planner_unknown_menu_request_ids_ignored',
                'planner_plan_kind': planner_output.plan.plan_kind,
                'planner_selected_menu_request_ids': selected_menu_request_ids,
                'unknown_menu_request_ids': unknown_menu_request_ids,
                'resolved_menu_request_count': resolved_menu_request_count,
                'reason': 'execute resolved requests and keep stale ids as audit only',
            })
        if not resolved_requests:
            break
        normalized_requests, normalization_audits = normalize_evidence_requests(workspace, resolved_requests)
        workspace = _workspace_with_request_normalization_audits(workspace, normalization_audits)
        broker = EvidenceBroker(bangumi_client)
        new_workspace, batch_result = broker.execute_batch(workspace, normalized_requests)
        evidence_batches.append(batch_result)
        planner_batch_executed = True
        workspace = new_workspace
        workspace = _workspace_with_judge_audit(workspace, {
            'note': 'planner_selected_menu_request_ids',
            'planner_plan_kind': planner_output.plan.plan_kind,
            'planner_selected_menu_request_ids': selected_menu_request_ids,
            'planner_selected_menu_request_count': len(selected_menu_request_ids),
            'resolved_menu_request_count': resolved_menu_request_count,
            'planner_plan_id': planner_output.plan.plan_id,
        })
        workspace = _workspace_with_planner_batch_audit(workspace, batch_result, planner_output)
        if active_orchestrator_tool_call is not None:
            orchestrator_session = record_orchestrator_tool_output(
                orchestrator_session,
                active_orchestrator_tool_call,
                {
                    'status': str(getattr(batch_result, 'status', '') or 'unknown'),
                    'request_count': len(list(getattr(batch_result, 'request_results', []) or [])),
                    'response_refs': [
                        ref
                        for request_result in list(getattr(batch_result, 'request_results', []) or [])
                        for ref in list(getattr(request_result, 'response_refs', []) or [])
                    ][:24],
                },
            )
        workspace = _refresh_mapping_draft_candidates(_workspace_with_initial_mapping_draft(workspace))
        if _should_try_mapping_editor(workspace):
            draft_attempt = _try_mapping_draft_editor_acceptance_with_workspace(workspace, ai_client, judge_outputs, evidence_batches, bangumi_client=bangumi_client)
            workspace = draft_attempt.workspace
            if draft_attempt.result is not None:
                return _with_planning_output(draft_attempt.result)
    round_limit = max_rounds if max_rounds is not None else workspace.budget.max_judge_rounds
    rounds_used = 0
    issue_response_used = workspace.header.issue_response_used

    while True:
        if round_limit and rounds_used >= round_limit:
            return _with_planning_output(_finish_on_round_limit(workspace, final_action, final_output, final_verifier_result, judge_outputs, evidence_batches, errors))

        round_kind = _next_round_kind(workspace)
        dossier = workspace.to_dossier(round_context=round_kind)
        judge_result = call_case_judge(ai_client, dossier, round_kind=round_kind)
        workspace = _workspace_with_judge_audit(workspace, getattr(judge_result, 'request_audit', None))
        rounds_used += 1
        if not judge_result.ok or judge_result.output is None:
            error_text = judge_result.error or 'case judge call failed'
            lower = error_text.casefold()
            error_kind = 'context_overflow' if 'exceeds the context window' in lower else ('provider_no_response' if 'no response' in lower else 'provider_error')
            status = 'error'
            summary = 'infra no response' if error_kind == 'provider_no_response' else 'infra error'
            if error_kind == 'context_overflow':
                summary = 'context overflow'
            return _result(False, workspace.header.case_id, status, final_action, final_output, final_verifier_result, workspace, judge_outputs, evidence_batches, summary, [*errors, error_text, f'error_kind={error_kind}'])

        output = judge_result.output
        judge_outputs.append(output)
        final_output = output
        final_action = output.action
        workspace = _workspace_with_judge_output_capture(workspace, output)

        is_final_round = bool(round_limit) and getattr(workspace.header, 'round_index', 0) >= max(0, round_limit - 1)
        if is_final_round and output.action == 'request_evidence':
            if workspace.previous_evidence_results or evidence_batches:
                return _no_new_evidence_fail_closed(workspace, reason='no_usable_evidence_after_request', description='final evidence request opportunity exhausted after prior investigation')
            return _budget_exhausted_fail_closed(workspace, final_action, output.model_copy(update={'evidence_requests': []}), final_verifier_result, reason='final_round_request_evidence_without_prior_evidence')

        if round_kind == 'issue_response' and output.action == 'request_evidence':
            return _result(False, workspace.header.case_id, 'invalid', final_action, final_output, final_verifier_result, workspace, judge_outputs, evidence_batches, 'issue_response round cannot request evidence', [*errors, 'issue_response round cannot request evidence'])

        if output.action == 'request_evidence':
            if not output.evidence_requests and not output.evidence_menu_request_ids:
                return _result(False, workspace.header.case_id, 'invalid', final_action, final_output, final_verifier_result, workspace, judge_outputs, evidence_batches, 'request_evidence_requires_requests', [*errors, 'request_evidence_requires_requests'])
            budget_exhausted = bool(workspace.budget.max_evidence_batches and workspace.budget.used_evidence_batches >= workspace.budget.max_evidence_batches)
            final_opportunity = bool(round_limit and getattr(workspace.header, 'round_index', 0) >= max(0, round_limit - 1))
            has_prior_evidence = bool(workspace.previous_evidence_results or evidence_batches)
            if normalize_fail_closed(final_request_evidence=True, prior_evidence=has_prior_evidence, exhausted=budget_exhausted, final_opportunity=final_opportunity):
                return _no_new_evidence_fail_closed(workspace, reason='no_usable_evidence_after_request', description='evidence budget exhausted after prior batches')
            if budget_exhausted:
                return _budget_exhausted_fail_closed(workspace, final_action, final_output, final_verifier_result, reason='evidence_budget_exhausted_before_request')
            raw_requests = list(output.evidence_requests or [])
            menu_request_ids = list(output.evidence_menu_request_ids or [])
            legacy_raw_request_count = len(raw_requests)
            resolved_requests: list[EvidenceRequest] = []
            selected_menu_request_ids: list[str] = []
            unknown_menu_request_ids: list[str] = []
            resolved_menu_request_count = 0
            raw_only = bool(raw_requests) and not menu_request_ids
            if menu_request_ids:
                resolved_requests, selected_menu_request_ids, unknown_menu_request_ids, resolved_menu_request_count = resolve_evidence_menu_requests(workspace, menu_request_ids)
            elif raw_only:
                workspace = _workspace_with_judge_audit(workspace, {
                    'note': 'legacy_raw_request_used',
                    'legacy_raw_request_used': True,
                    'legacy_raw_request_count': legacy_raw_request_count,
                    'reason': 'raw evidence requests present without menu ids',
                })
                planner_ids = list(getattr(getattr(planner_output, 'plan', None), 'selected_menu_request_ids', []) or []) if planner_output else []
                workspace = _workspace_with_judge_audit(workspace, {
                    'note': 'planner_fallback_for_raw_request',
                    'legacy_raw_request_used': True,
                    'planner_plan_kind': getattr(getattr(planner_output, 'plan', None), 'plan_kind', '') if planner_output else '',
                    'planner_selected_menu_request_ids': planner_ids,
                    'planner_selected_menu_request_count': len(planner_ids),
                    'planner_plan_id': getattr(getattr(planner_output, 'plan', None), 'plan_id', '') if planner_output else '',
                })
                if planner_ids and planner_output and planner_output.selected_evidence and planner_output.plan and (workspace.budget.max_evidence_batches == 0 or workspace.budget.used_evidence_batches < workspace.budget.max_evidence_batches):
                    resolved_requests, selected_menu_request_ids, unknown_menu_request_ids, resolved_menu_request_count = resolve_evidence_menu_requests(workspace, planner_ids)
                else:
                    resolved_requests = []
            merged_requests = [*resolved_requests]
            if raw_only and not resolved_requests:
                merged_requests = raw_requests
            deduped_requests: list[EvidenceRequest] = []
            seen_request_keys: set[tuple[object, ...]] = set()
            for request in merged_requests:
                normalized_request = request if isinstance(request, EvidenceRequest) else EvidenceRequest(**request.model_dump(mode='json') if hasattr(request, 'model_dump') else dict(request))
                request_key = (
                    str(normalized_request.request_type or ''),
                    str(normalized_request.local_span_ref or ''),
                    tuple(sorted(str(ref) for ref in (normalized_request.item_refs or []) if ref)),
                    tuple(sorted(str(ref) for ref in (normalized_request.anchor_file_refs or []) if ref)),
                    tuple(sorted(str(ref) for ref in (normalized_request.group_refs or []) if ref)),
                    tuple(sorted(str(ref) for ref in (normalized_request.subject_refs or []) if ref)),
                    tuple(sorted(str(ref) for ref in (normalized_request.query_refs or []) if ref)),
                )
                if request_key in seen_request_keys:
                    continue
                seen_request_keys.add(request_key)
                deduped_requests.append(normalized_request)
            if unknown_menu_request_ids and menu_request_ids:
                audits = list(getattr(workspace, 'judge_request_audits', []) or [])
                audits.append({
                    'request_planning_violation': {
                        'selected_menu_request_ids': selected_menu_request_ids,
                        'unknown_menu_request_ids': unknown_menu_request_ids,
                        'resolved_menu_request_count': resolved_menu_request_count,
                        'legacy_raw_request_count': legacy_raw_request_count,
                        'reason': 'judge attempted unknown menu request id',
                        'planner_batch_executed': planner_batch_executed,
                    }
                })
                object.__setattr__(workspace, 'judge_request_audits', audits)
                workspace = _workspace_with_judge_audit(workspace, {
                    'note': 'unknown_menu_request_id_observed',
                    'selected_menu_request_ids': selected_menu_request_ids,
                    'unknown_menu_request_ids': unknown_menu_request_ids,
                    'resolved_menu_request_count': resolved_menu_request_count,
                    'has_prior_evidence': has_prior_evidence,
                    'planner_batch_executed': planner_batch_executed,
                })
                if planner_batch_executed or has_prior_evidence:
                    workspace = _workspace_with_judge_audit(workspace, {
                        'note': 'stale_or_unknown_menu_request_ignored_after_planner',
                        'selected_menu_request_ids': selected_menu_request_ids,
                        'unknown_menu_request_ids': unknown_menu_request_ids,
                        'resolved_menu_request_count': resolved_menu_request_count,
                        'executable_request_count': len(deduped_requests),
                    })
                    if deduped_requests:
                        workspace = _workspace_with_judge_audit(workspace, {
                            'note': 'stale_menu_ids_did_not_block_executable_requests',
                            'unknown_menu_request_ids': unknown_menu_request_ids,
                            'executable_request_count': len(deduped_requests),
                        })
                    else:
                        workspace = _refresh_mapping_draft_candidates(_workspace_with_initial_mapping_draft(workspace))
                        if _should_try_mapping_editor(workspace):
                            draft_result = _try_mapping_draft_editor_acceptance(workspace, ai_client, judge_outputs, evidence_batches, bangumi_client=bangumi_client)
                            if draft_result is not None:
                                return _with_planning_output(draft_result)
                        continue
                if deduped_requests:
                    workspace = _workspace_with_judge_audit(workspace, {
                        'note': 'unknown_menu_ids_partially_resolved',
                        'unknown_menu_request_ids': unknown_menu_request_ids,
                        'executable_request_count': len(deduped_requests),
                    })
                else:
                    fail_output = CaseJudgeOutput(
                    action='fail_closed',
                    fail_closed_reasons=[
                        FailClosedReason(
                            ref='FR1',
                            reason_kind='insufficient_evidence',
                            description='unknown_menu_request_id: requested menu ids were not executable in the refreshed evidence menu',
                            related_refs=[],
                        )
                    ],
                    summary='unknown menu request id blocked evidence execution',
                    )
                    fail_verifier = verify_judge_output(workspace.to_dossier(round_context='unknown_menu_request_fail_closed'), fail_output)
                    return _result(True, workspace.header.case_id, 'fail_closed', 'fail_closed', fail_output, fail_verifier, workspace, judge_outputs, evidence_batches, 'no_new_evidence', [*errors, 'unknown_menu_request_id'])
            normalized_requests, normalization_audits = normalize_evidence_requests(workspace, deduped_requests)
            workspace = _workspace_with_request_normalization_audits(workspace, normalization_audits)
            new_workspace, batch_result = broker.execute_batch(workspace, normalized_requests)
            evidence_batches.append(batch_result)
            workspace = new_workspace
            workspace = _workspace_with_judge_audit(workspace, {
                'note': 'evidence_menu_resolution',
                'selected_menu_request_ids': selected_menu_request_ids,
                'unknown_menu_request_ids': unknown_menu_request_ids,
                'resolved_menu_request_count': resolved_menu_request_count,
                'legacy_raw_request_count': legacy_raw_request_count,
            })
            workspace = _workspace_with_evidence_batch_audit(workspace, batch_result, output, round_kind)
            workspace = workspace.with_seen_detail_refs([ref for rr in batch_result.request_results for ref in (getattr(rr, 'response_refs', []) or [])])
            if round_kind == 'policy_retry':
                workspace = _workspace_without_policy_retry_marker(workspace)
            workspace = _refresh_mapping_draft_candidates(_workspace_with_initial_mapping_draft(workspace))
            if _should_try_mapping_editor(workspace):
                draft_result = _try_mapping_draft_editor_acceptance(workspace, ai_client, judge_outputs, evidence_batches, bangumi_client=bangumi_client)
                if draft_result is not None:
                    return _with_planning_output(draft_result)
            accepted_count = sum(1 for rr in (batch_result.request_results or []) if getattr(rr, 'accepted', False))
            rejected_count = sum(1 for rr in (batch_result.request_results or []) if not getattr(rr, 'accepted', False))
            usable_response_ref_count = sum(len(getattr(rr, 'response_refs', []) or []) for rr in (batch_result.request_results or []) if getattr(rr, 'accepted', False))
            rejection_notes = ' '.join(' '.join(getattr(rr, 'notes', []) or []) for rr in (batch_result.request_results or []) if not getattr(rr, 'accepted', False)).casefold()
            invalid_contract = any(marker in rejection_notes for marker in ('invalid anchor', 'invalid subject', 'invalid item', 'invalid query', 'unknown request_type'))
            no_usable_evidence = any(marker in rejection_notes for marker in ('no matching local files', 'no matching target window', 'no matching targets', 'no matching span', 'package_span_requires_child_span_requests', 'target_window too wide', 'no usable evidence'))
            if batch_result.status == 'rejected' or (batch_result.status == 'partial' and usable_response_ref_count == 0):
                if invalid_contract:
                    reason = 'evidence_request_invalid_anchor'
                    return _result(False, workspace.header.case_id, 'invalid', final_action, final_output, final_verifier_result, workspace, judge_outputs, evidence_batches, reason, [*errors, reason])
                if no_usable_evidence:
                    reason = 'no_usable_evidence_after_request'
                    if round_kind == 'policy_retry' or workspace.previous_evidence_results:
                        return _no_new_evidence_fail_closed(workspace, reason=reason, description='evidence request returned no usable Bangumi/local target proof')
                    return _result(False, workspace.header.case_id, 'invalid', final_action, final_output, final_verifier_result, workspace, judge_outputs, evidence_batches, reason, [*errors, reason])
                reason = 'evidence_batch_all_rejected' if rejected_count else 'evidence_batch_rejected'
                return _result(False, workspace.header.case_id, 'invalid', final_action, final_output, final_verifier_result, workspace, judge_outputs, evidence_batches, reason, [*errors, reason])
            if batch_result.status == 'partial' and usable_response_ref_count > 0:
                workspace = _workspace_with_judge_output_capture(workspace, output)
                workspace = workspace.with_seen_detail_refs([ref for rr in batch_result.request_results for ref in (getattr(rr, 'response_refs', []) or [])])
                if workspace.budget.max_evidence_batches and workspace.budget.used_evidence_batches >= workspace.budget.max_evidence_batches:
                    return _result(True, workspace.header.case_id, 'fail_closed', final_action, final_output, final_verifier_result, workspace, judge_outputs, evidence_batches, 'no_new_evidence', errors)
                continue
            continue

        if output.action in ('submit_verdict', 'fail_closed', 'issue_response'):
            if round_kind == 'policy_retry' and output.action == 'issue_response':
                return _result(False, workspace.header.case_id, 'invalid', final_action, final_output, final_verifier_result, workspace, judge_outputs, evidence_batches, 'policy_retry cannot issue_response', [*errors, 'policy_retry cannot issue_response'])
            if round_kind == 'policy_retry' and output.action == 'fail_closed' and not output.evidence_requests:
                reasons = list(getattr(output, 'fail_closed_reasons', []) or [])
                reason_kinds = {str(getattr(reason, 'reason_kind', '')).casefold() for reason in reasons}
                descriptions = ' '.join(str(getattr(reason, 'description', '') or '').casefold() for reason in reasons)
                bounded = build_bounded_case_dossier(dossier)
                has_request_types = bool(getattr(dossier, 'available_detail_request_types', []) or getattr(bounded, 'available_detail_request_types', []) or [])
                recommended_requests = _recommended_neutral_requests(bounded)
                if 'insufficient_evidence' in reason_kinds and recommended_requests and has_request_types and _fail_closed_has_legal_anchor(bounded, workspace) and 'no legal anchor' not in descriptions and 'request types unavailable' not in descriptions and 'budget exhausted' not in descriptions and 'cannot request evidence' not in descriptions:
                    guard = _structured_premature_guard_decision(workspace=workspace, dossier=dossier, output=output, round_kind=round_kind, triggered=True, allowed=False, reason='anchors_available_but_no_request', fail_closed_reason_kinds=list(reason_kinds))
                    workspace = _workspace_with_guard_decision(workspace, guard)
                    return _no_new_evidence_fail_closed(workspace, reason='policy_retry_refused_recommended_request', description='policy retry still failed to produce executable evidence or accepted mapping')
            premature_reason = _premature_fail_closed_guard(workspace, dossier, output, round_kind=round_kind)
            if premature_reason == 'policy_retry_required':
                if not policy_retry_used:
                    policy_retry_used = True
                    workspace = _workspace_with_policy_retry_round(workspace)
                    continue
                return _result(False, workspace.header.case_id, 'invalid', final_action, final_output, final_verifier_result, workspace, judge_outputs, evidence_batches, 'premature_fail_closed_requires_evidence_request', [*errors, 'premature_fail_closed_requires_evidence_request'])
            if premature_reason == 'invalid_premature_fail_closed':
                return _result(False, workspace.header.case_id, 'invalid', final_action, final_output, final_verifier_result, workspace, judge_outputs, evidence_batches, 'premature_fail_closed_requires_evidence_request', [*errors, 'premature_fail_closed_requires_evidence_request'])
            workspace = _workspace_without_policy_retry_marker(workspace)
            effective_output, effective_action, issue_response_mode = _normalize_judge_output_for_verifier(output, round_kind=round_kind)
            if issue_response_mode == 'invalid':
                return _result(False, workspace.header.case_id, 'invalid', final_action, final_output, final_verifier_result, workspace, judge_outputs, evidence_batches, effective_output.summary or 'issue_response requires corrected verdict or fail_closed reasons', [*errors, effective_output.summary or 'issue_response requires corrected verdict or fail_closed reasons'])

            if getattr(judge_result, 'request_audit', None) and judge_result.request_audit.get('oversized_output'):
                oversized_reason = str(judge_result.request_audit.get('oversized_output_reason') or 'oversized output')
                summary = f'oversized output: {oversized_reason}'
                if not policy_retry_used:
                    policy_retry_used = True
                    workspace = _workspace_with_policy_retry_round(workspace)
                    continue
                fail_output = CaseJudgeOutput(
                    action='fail_closed',
                    fail_closed_reasons=[FailClosedReason(ref='FR1', reason_kind='insufficient_evidence', description=f'output_budget_exceeded: {oversized_reason}', related_refs=[])],
                    summary='output budget exceeded before accepted mapping',
                )
                fail_verifier = verify_judge_output(dossier, fail_output)
                return _result(True, workspace.header.case_id, 'fail_closed', 'fail_closed', fail_output, fail_verifier, workspace, judge_outputs, evidence_batches, 'no_new_evidence', [*errors, 'output_budget_exceeded'])

            final_output = effective_output

            contradiction_reason = _contradictory_fail_closed_guard(workspace, dossier, effective_output)
            if contradiction_reason:
                workspace = _workspace_with_judge_audit(workspace, {
                    'note': contradiction_reason,
                    'reason': (
                        'judge required span proof despite visible explicit item target refs'
                        if contradiction_reason == 'contradictory_span_proof_fail_closed'
                        else 'judge claimed no assignable target while assignable/detail refs are visible'
                    ),
                    'assignable_target_count': len(getattr(dossier, 'assignable_target_refs', []) or []),
                    'seen_detail_ref_count': len(getattr(dossier, 'seen_detail_refs', []) or []),
                    'detail_equivalent_target_span_count': len(_detail_equivalent_span_refs(workspace)),
                })
                workspace = _refresh_mapping_draft_candidates(_workspace_with_initial_mapping_draft(workspace))
                if _should_try_mapping_editor(workspace):
                    draft_result = _try_mapping_draft_editor_acceptance(workspace, ai_client, judge_outputs, evidence_batches, bangumi_client=bangumi_client)
                    if draft_result is not None:
                        return _with_planning_output(draft_result)
                if 'contradictory_fail_closed_retry_used' not in (getattr(workspace, 'diagnostics', []) or []):
                    workspace = _workspace_preserving_state(workspace, diagnostics=[*workspace.diagnostics, 'contradictory_fail_closed_retry_used'])
                    continue
                contradiction_verifier = CaseVerifierResult(
                    passed=False,
                    issues=[],
                    summary=contradiction_reason,
                )
                return _semantic_target_conflict_fail_closed(workspace, contradiction_verifier, reason=contradiction_reason)

            verifier_result = verify_judge_output(dossier, effective_output)
            final_verifier_result = verifier_result
            workspace = _workspace_with_diagnostics(workspace, dossier, verifier_result, effective_output, final_opportunity=bool(is_final_round))
            if verifier_result.passed:
                if effective_action == 'submit_verdict':
                    if any(intent.target_ref == 'UNALIGNED' for intent in effective_output.assignment_intents):
                        return _result(True, workspace.header.case_id, 'fail_closed', final_action, final_output, final_verifier_result, workspace, judge_outputs, evidence_batches, 'no_new_evidence', errors)
                    return _result(True, workspace.header.case_id, 'accepted', final_action, final_output, final_verifier_result, workspace, judge_outputs, evidence_batches, 'accepted', errors)
                if effective_action == 'fail_closed':
                    return _result(True, workspace.header.case_id, 'fail_closed', final_action, final_output, final_verifier_result, workspace, judge_outputs, evidence_batches, 'no_new_evidence', errors)
            if effective_action == 'submit_verdict':
                has_invalid_target = any(str(getattr(issue, 'issue_code', '')).endswith('invalid_target') for issue in verifier_result.issues)
                has_duplicate_target = any(str(getattr(issue, 'issue_code', '')).endswith('duplicate_target') for issue in verifier_result.issues)
                has_coverage_gap = any(str(getattr(issue, 'issue_code', '')).endswith('coverage_gap') for issue in verifier_result.issues)
                has_unaligned_not_accepted = any(str(getattr(issue, 'issue_code', '')).endswith('unaligned_not_accepted') for issue in verifier_result.issues)
                if has_unaligned_not_accepted:
                    reason = 'verdict_contains_unaligned_target'
                    return _semantic_target_conflict_fail_closed(workspace, verifier_result, reason=reason)
                if has_invalid_target or has_duplicate_target or has_coverage_gap:
                    if round_kind == 'evidence_rejudge' and workspace.previous_evidence_results:
                        reason = 'verifier_rejected_unexecutable_verdict'
                        workspace = _workspace_with_verifier_issues(workspace, verifier_result)
                        return _semantic_target_conflict_fail_closed(workspace, verifier_result, reason=reason)
                    if workspace.budget.max_issue_response_rounds and issue_response_used < workspace.budget.max_issue_response_rounds:
                        issue_response_used += 1
                        workspace = _workspace_with_verifier_issues(workspace, verifier_result)
                        continue
                    reason = 'verifier_rejected_unexecutable_verdict'
                    return _semantic_target_conflict_fail_closed(workspace, verifier_result, reason=reason)
                if round_kind == 'issue_response':
                    return _result(False, workspace.header.case_id, 'invalid', final_action, final_output, final_verifier_result, workspace, judge_outputs, evidence_batches, 'issue_response still failed verifier', [*errors, 'issue_response still failed verifier'])
                if is_final_round:
                    if workspace.budget.max_issue_response_rounds and issue_response_used < workspace.budget.max_issue_response_rounds:
                        issue_response_used += 1
                        workspace = _workspace_with_verifier_issues(workspace, verifier_result)
                        continue
                    summary = _verifier_gap_summary(verifier_result)
                    return _result(True, workspace.header.case_id, 'fail_closed', final_action, final_output, final_verifier_result, workspace, judge_outputs, evidence_batches, 'no_new_evidence', [*errors, summary or 'coverage_gap_unresolved'])
                if workspace.budget.max_issue_response_rounds and issue_response_used >= workspace.budget.max_issue_response_rounds:
                    summary = _verifier_gap_summary(verifier_result)
                    return _result(True, workspace.header.case_id, 'fail_closed', final_action, final_output, final_verifier_result, workspace, judge_outputs, evidence_batches, 'no_new_evidence', [*errors, summary or 'issue response budget exhausted'])
                issue_response_used += 1
                workspace = _workspace_with_verifier_issues(workspace, verifier_result)
                continue

            if effective_action == 'issue_response':
                return _result(False, workspace.header.case_id, 'invalid', final_action, final_output, final_verifier_result, workspace, judge_outputs, evidence_batches, 'issue_response still failed verifier', [*errors, 'issue_response still failed verifier'])

            return _result(False, workspace.header.case_id, 'invalid', final_action, final_output, final_verifier_result, workspace, judge_outputs, evidence_batches, 'verifier rejected output', [*errors, 'verifier rejected output'])

        return _result(False, workspace.header.case_id, 'invalid', final_action, final_output, final_verifier_result, workspace, judge_outputs, evidence_batches, f'unsupported action: {output.action}', [*errors, f'unsupported action: {output.action}'])


def _orchestrator_error_result(
    workspace: CaseEvidenceWorkspace,
    *,
    summary: str,
    error_kind: str,
    planning_output: CasePlanningOutput | None,
    evidence_batches: list[EvidenceBatchResult] | None = None,
) -> CaseAgentRunResult:
    verifier_result = CaseVerifierResult(
        passed=False,
        issues=[
            VerifierIssue(
                ref='orchestrator_agent',
                issue_code=error_kind,
                severity='blocked',
                message=summary,
            )
        ],
        summary=summary,
    )
    result = CaseAgentRunResult(
        False,
        workspace.header.case_id,
        'error',
        'orchestrator_agent',
        None,
        verifier_result,
        workspace,
        [],
        list(evidence_batches or []),
        summary,
        [summary, f'error_kind={error_kind}'],
    )
    result.planning_output = planning_output
    return result


def _target_surface_counts(workspace: CaseEvidenceWorkspace) -> dict[str, int]:
    detail_spans = [
        card for card in list(getattr(workspace, 'bangumi_span_cards', []) or [])
        if bool(getattr(card, 'detail_equivalent', False))
    ]
    return {
        'subject_count': len(list(getattr(workspace, 'bangumi_subjects', []) or [])),
        'item_count': len(list(getattr(workspace, 'bangumi_items', []) or [])),
        'span_count': len(list(getattr(workspace, 'bangumi_span_cards', []) or [])),
        'detail_equivalent_span_count': len(detail_spans),
        'visible_target_count': len(list(getattr(getattr(workspace, 'contract', None), 'visible_target_refs', []) or [])),
    }


def _local_ref_brief(workspace: CaseEvidenceWorkspace, local_ref: str) -> dict[str, object]:
    for span in list(getattr(workspace, 'local_span_cards', []) or []):
        if str(getattr(span, 'ref', '') or '') == local_ref:
            return {
                'local_ref': local_ref,
                'local_ref_kind': 'span',
                'file_ref_count': int(getattr(span, 'file_ref_count', 0) or len(list(getattr(span, 'file_refs', []) or [])) or 0),
                'file_ref_samples': list(getattr(span, 'file_ref_samples', []) or [])[:8] or list(getattr(span, 'file_refs', []) or [])[:8],
                'episode_token_start': getattr(span, 'episode_token_start', None),
                'episode_token_end': getattr(span, 'episode_token_end', None),
                'title_cues': list(getattr(span, 'title_cues', []) or [])[:6],
                'span_scope': str(getattr(span, 'span_scope', '') or ''),
            }
    for card in list(getattr(workspace, 'local_files', []) or []):
        if str(getattr(card, 'ref', '') or '') == local_ref:
            return {
                'local_ref': local_ref,
                'local_ref_kind': 'file',
                'file_ref_count': 1,
                'file_ref_samples': [local_ref],
                'path': str(getattr(card, 'path', '') or ''),
                'label': str(getattr(card, 'label', '') or ''),
            }
    return {'local_ref': local_ref, 'local_ref_kind': 'unknown', 'file_ref_count': 0}


def _target_ref_briefs(workspace: CaseEvidenceWorkspace, refs: list[str], *, limit: int = 12) -> list[dict[str, object]]:
    item_by_ref = {
        str(getattr(card, 'ref', '') or ''): card
        for card in list(getattr(workspace, 'bangumi_items', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }
    span_by_ref = {
        str(getattr(card, 'ref', '') or ''): card
        for card in list(getattr(workspace, 'bangumi_span_cards', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }
    result: list[dict[str, object]] = []
    for ref in list(dict.fromkeys(ref for ref in refs if ref))[:limit]:
        if ref in span_by_ref:
            span = span_by_ref[ref]
            result.append({
                'ref': ref,
                'target_kind': 'span',
                'subject_ref': str(getattr(span, 'subject_ref', '') or ''),
                'target_ref_count': int(getattr(span, 'target_ref_count', 0) or len(list(getattr(span, 'target_refs', []) or [])) or 0),
                'sort_start': getattr(span, 'sort_start', None),
                'sort_end': getattr(span, 'sort_end', None),
                'ep_start': getattr(span, 'ep_start', None),
                'ep_end': getattr(span, 'ep_end', None),
                'item_kind': str(getattr(span, 'item_kind', '') or ''),
                'target_ref_samples': list(getattr(span, 'target_ref_samples', []) or [])[:6],
                'target_refs': list(getattr(span, 'target_refs', []) or [])[:12],
                'detail_equivalent': bool(getattr(span, 'detail_equivalent', False)),
            })
        elif ref in item_by_ref:
            item = item_by_ref[ref]
            result.append({
                'ref': ref,
                'target_kind': 'item',
                'subject_ref': str(getattr(item, 'subject_ref', '') or ''),
                'sort': getattr(item, 'sort', None),
                'ep': getattr(item, 'ep', None),
                'item_kind': str(getattr(item, 'item_kind', '') or ''),
                'title': str(getattr(item, 'title', '') or getattr(item, 'name_cn', '') or getattr(item, 'name', '') or '')[:80],
            })
        else:
            result.append({'ref': ref, 'target_kind': 'unknown'})
    return result


def _selected_target_ownership(workspace: CaseEvidenceWorkspace) -> dict[str, dict[str, object]]:
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None:
        return {}
    span_by_ref = {
        str(getattr(card, 'ref', '') or ''): card
        for card in list(getattr(workspace, 'bangumi_span_cards', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }
    ownership: dict[str, dict[str, object]] = {}
    for row in list(getattr(draft, 'rows', []) or []):
        if str(getattr(row, 'disposition', '') or '') != 'map_to_bangumi':
            continue
        row_ref = str(getattr(row, 'row_ref', '') or '')
        local_ref = str(getattr(row, 'local_ref', '') or '')
        selected = str(getattr(row, 'selected_target_ref', '') or '')
        if not selected:
            continue
        if str(getattr(row, 'mapping_mode', '') or '') == 'span_by_index' and selected in span_by_ref:
            span = span_by_ref[selected]
            target_refs = [str(ref or '') for ref in list(getattr(span, 'target_refs', []) or []) if str(ref or '')]
        else:
            target_refs = [selected]
        for target_ref in target_refs:
            ownership.setdefault(target_ref, {
                'target_ref': target_ref,
                'owner_row_ref': row_ref,
                'owner_local_ref': local_ref,
                'owner_selected_target_ref': selected,
            })
    return ownership


def _target_ref_ownership_observation(workspace: CaseEvidenceWorkspace, refs: list[str], *, limit: int = 24) -> list[dict[str, object]]:
    ownership = _selected_target_ownership(workspace)
    rows: list[dict[str, object]] = []
    for ref in _dedupe_preserve_order([str(value or '') for value in list(refs or [])])[:limit]:
        owner = ownership.get(ref)
        if owner:
            rows.append(dict(owner))
        else:
            rows.append({'target_ref': ref, 'owner_row_ref': '', 'owner_local_ref': '', 'owner_selected_target_ref': ''})
    return rows


def _candidate_target_conflicts_for_row(workspace: CaseEvidenceWorkspace, row) -> list[dict[str, object]]:
    candidate_refs = [str(ref or '') for ref in list(getattr(row, 'candidate_target_refs', []) or []) if str(ref or '')]
    if not candidate_refs:
        return []
    span_by_ref = {
        str(getattr(card, 'ref', '') or ''): card
        for card in list(getattr(workspace, 'bangumi_span_cards', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }
    ownership = _selected_target_ownership(workspace)
    conflicts: list[dict[str, object]] = []
    for candidate_ref in candidate_refs:
        if candidate_ref in span_by_ref:
            span = span_by_ref[candidate_ref]
            target_refs = [str(ref or '') for ref in list(getattr(span, 'target_refs', []) or []) if str(ref or '')]
        else:
            target_refs = [candidate_ref]
        occupied = [dict(ownership[ref]) for ref in target_refs if ref in ownership]
        if occupied:
            conflicts.append({
                'candidate_target_ref': candidate_ref,
                'occupied_target_count': len(occupied),
                'occupied_target_refs': [str(item.get('target_ref') or '') for item in occupied[:12]],
                'owner_row_refs': _dedupe_preserve_order([str(item.get('owner_row_ref') or '') for item in occupied])[:8],
                'owner_local_refs': _dedupe_preserve_order([str(item.get('owner_local_ref') or '') for item in occupied])[:8],
            })
    return conflicts


def _unowned_candidate_target_refs_for_row(workspace: CaseEvidenceWorkspace, row) -> list[str]:
    candidate_refs = [str(ref or '') for ref in list(getattr(row, 'candidate_target_refs', []) or []) if str(ref or '')]
    if not candidate_refs:
        return []
    span_by_ref = {
        str(getattr(card, 'ref', '') or ''): card
        for card in list(getattr(workspace, 'bangumi_span_cards', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }
    ownership = _selected_target_ownership(workspace)
    unowned: list[str] = []
    for candidate_ref in candidate_refs:
        if candidate_ref in span_by_ref:
            span = span_by_ref[candidate_ref]
            target_refs = [str(ref or '') for ref in list(getattr(span, 'target_refs', []) or []) if str(ref or '')]
        else:
            target_refs = [candidate_ref]
        if not any(ref in ownership for ref in target_refs):
            unowned.append(candidate_ref)
    return _dedupe_preserve_order(unowned)


def _visible_subject_item_sequences_for_row(
    workspace: CaseEvidenceWorkspace,
    row,
    *,
    file_count: int,
    candidate_refs: list[str],
    extra_subject_refs: list[str] | None = None,
    limit: int = 4,
) -> list[dict[str, object]]:
    has_row_evidence_surface = bool(
        candidate_refs
        or list(getattr(row, 'subject_refs', []) or [])
        or list(extra_subject_refs or [])
        or list(getattr(row, 'item_refs', []) or [])
        or list(getattr(row, 'requested_request_types', []) or [])
    )
    item_by_ref = {
        str(getattr(card, 'ref', '') or ''): card
        for card in list(getattr(workspace, 'bangumi_items', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }
    subject_title_by_ref = {
        str(getattr(subject, 'ref', '') or ''): str(getattr(subject, 'title', '') or getattr(subject, 'name_cn', '') or getattr(subject, 'name', '') or '')
        for subject in list(getattr(workspace, 'bangumi_subjects', []) or [])
        if str(getattr(subject, 'ref', '') or '')
    }
    subject_refs = _dedupe_preserve_order([
        *[str(ref or '') for ref in list(getattr(row, 'subject_refs', []) or [])],
        *[str(ref or '') for ref in list(extra_subject_refs or [])],
        *[
            str(getattr(item_by_ref.get(ref), 'subject_ref', '') or '')
            for ref in candidate_refs
            if item_by_ref.get(ref) is not None
        ],
    ])
    has_target_side_anchor = bool(
        subject_refs
        or candidate_refs
        or list(getattr(row, 'item_refs', []) or [])
    )
    all_visible_subject_refs = _dedupe_preserve_order([
        str(getattr(item, 'subject_ref', '') or '')
        for item in list(getattr(workspace, 'bangumi_items', []) or [])
        if str(getattr(item, 'subject_ref', '') or '')
    ])
    # This only broadens the evidence surface: when the agent has already
    # investigated subjects/items, show same-count visible sequences even if a
    # prior blocked intent pointed at the wrong subject.
    if has_target_side_anchor:
        subject_refs = _dedupe_preserve_order([*subject_refs, *all_visible_subject_refs])
    if not subject_refs and not has_row_evidence_surface:
        subject_refs = all_visible_subject_refs
    local_span = next(
        (
            card for card in list(getattr(workspace, 'local_span_cards', []) or [])
            if str(getattr(card, 'ref', '') or '') == str(getattr(row, 'local_ref', '') or '')
        ),
        None,
    )
    special_eligible = is_special_eligible_span(local_span, workspace.to_dossier(round_context='open_row_special_sequence')) if local_span is not None else False
    sequence_filters: list[tuple[str, set[str]]] = []
    if special_eligible:
        sequence_filters.append(('special', {'special', 'movie'}))
    sequence_filters.append(('regular', {'episode', 'regular', 'unknown', ''}))
    ownership = _selected_target_ownership(workspace)
    result: list[dict[str, object]] = []
    for subject_ref in subject_refs[:limit]:
        for sequence_kind, allowed_kinds in sequence_filters:
            ordered_items = sorted(
                [
                    item for item in list(getattr(workspace, 'bangumi_items', []) or [])
                    if str(getattr(item, 'subject_ref', '') or '') == subject_ref
                    and str(getattr(item, 'item_kind', '') or '') in allowed_kinds
                    and str(getattr(item, 'ref', '') or '')
                ],
                key=lambda item: (getattr(item, 'sort', 0) or 0, getattr(item, 'ep', 0) or 0, str(getattr(item, 'ref', '') or '')),
            )
            if not ordered_items:
                continue
            width = max(1, int(file_count or 0))
            refs = [str(getattr(item, 'ref', '') or '') for item in ordered_items[:width]]
            occupied_refs = [ref for ref in refs if ref in ownership]
            item_ref_limit = max(24, min(width, 256))
            result.append({
                'subject_ref': subject_ref,
                'subject_title': subject_title_by_ref.get(subject_ref, ''),
                'sequence_kind': sequence_kind,
                'item_refs': refs[:item_ref_limit],
                'item_ref_count': len(refs),
                'matches_local_file_count': bool(file_count and len(refs) == file_count),
                'item_refs_truncated': len(refs) > item_ref_limit,
                'unowned_item_ref_count': len([ref for ref in refs if ref not in ownership]),
                'occupied_item_refs': occupied_refs[:12],
                'owner_row_refs': _dedupe_preserve_order([str(ownership[ref].get('owner_row_ref') or '') for ref in occupied_refs])[:8],
                'sort_start': getattr(ordered_items[0], 'sort', None),
                'sort_end': getattr(ordered_items[min(len(refs), len(ordered_items)) - 1], 'sort', None) if refs else None,
                'title_samples': [
                    str(getattr(item, 'title', '') or getattr(item, 'name_cn', '') or getattr(item, 'name', '') or '')
                    for item in ordered_items[:3]
                ],
            })
            if len(result) >= limit:
                return result
    return result


def _non_progress_needs_more_evidence_issues(
    workspace: CaseEvidenceWorkspace,
    draft: MappingDraft,
    patches: list[MappingDraftPatch],
) -> list[VerifierIssue]:
    rows_by_local = {
        str(getattr(row, 'local_ref', '') or ''): row
        for row in list(getattr(draft, 'rows', []) or [])
        if str(getattr(row, 'local_ref', '') or '')
    }
    issues: list[VerifierIssue] = []
    for patch in list(patches or []):
        normalized = normalize_mapping_patch_op(patch)
        if str(getattr(normalized, 'op', '') or '') != 'needs_more_evidence':
            continue
        local_ref = _patch_draft_local_ref(draft, normalized)
        row = rows_by_local.get(local_ref)
        if row is None:
            continue
        candidate_refs = _dedupe_preserve_order([
            *list(getattr(row, 'candidate_target_refs', []) or []),
            *[str(ref or '') for ref in list(getattr(normalized, 'item_refs', []) or [])],
            *[str(ref or '') for ref in list(getattr(normalized, 'subject_refs', []) or [])],
        ])
        local_brief = _local_ref_brief(workspace, local_ref)
        file_count = int(local_brief.get('file_ref_count') or 0)
        sequences = _visible_subject_item_sequences_for_row(
            workspace,
            row,
            file_count=file_count,
            candidate_refs=candidate_refs,
            extra_subject_refs=[str(ref or '') for ref in list(getattr(normalized, 'subject_refs', []) or [])],
        )
        has_span_candidate = any(str(ref or '').startswith('BES') for ref in candidate_refs)
        has_actionable_visible_sequence = any(
            isinstance(sequence, dict)
            and bool(sequence.get('matches_local_file_count'))
            and not bool(sequence.get('item_refs_truncated'))
            and int(sequence.get('unowned_item_ref_count') or 0) > 0
            for sequence in sequences
        )
        if not has_span_candidate and not has_actionable_visible_sequence:
            continue
        issues.append(VerifierIssue(
            ref=str(getattr(row, 'row_ref', '') or local_ref or 'mapping_draft_row'),
            issue_code='non_progress_needs_more_evidence_with_visible_candidates',
            severity='blocked',
            message='needs_more_evidence would not make progress while this open row already has visible candidate targets or same-count item sequences; the agent must map with explicit visible BE/BES refs, reject wrong candidates, mark accepted target_absent/supplemental, or finish fail_closed with a real blocker',
            related_refs=_dedupe_preserve_order([
                local_ref,
                *candidate_refs[:12],
                *[
                    ref
                    for sequence in sequences
                    if isinstance(sequence, dict) and bool(sequence.get('matches_local_file_count'))
                    for ref in list(sequence.get('item_refs') or [])[:12]
                ],
            ]),
        ))
    return issues


def _open_rows_observation(workspace: CaseEvidenceWorkspace, *, limit: int = 12) -> list[dict[str, object]]:
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None:
        return []
    rows = [
        row for row in list(getattr(draft, 'rows', []) or [])
        if str(getattr(row, 'status', '') or '') != 'verified'
        and str(getattr(row, 'disposition', '') or '') != 'map_to_bangumi'
        and str(getattr(row, 'disposition', '') or '') != 'non_bangumi_or_supplemental'
    ]
    observations: list[dict[str, object]] = []
    for row in rows[:limit]:
        local_ref = str(getattr(row, 'local_ref', '') or '')
        disposition = str(getattr(row, 'disposition', '') or '')
        candidate_refs = list(getattr(row, 'candidate_target_refs', []) or [])
        requested_types = list(getattr(row, 'requested_request_types', []) or [])
        local_brief = _local_ref_brief(workspace, local_ref)
        file_count = int(local_brief.get('file_ref_count') or 0)
        protocol_warning = ''
        if disposition == 'unaligned_fail_closed':
            protocol_warning = (
                'This row is terminal fail_closed and will not count as accepted accounting. '
                'If the intended semantic conclusion is "Bangumi has no corresponding target but the row is handled", '
                'revise it with mark_non_bangumi_or_supplemental(reason_kind=bangumi_target_absent). '
                'Keep mark_unaligned_fail_closed only when the whole case should fail_closed.'
            )
            if candidate_refs:
                recommended = (
                    'this row is currently fail_closed with visible candidates; choose a visible candidate target, '
                    'use reject_candidate for semantically wrong candidates, request more comparison evidence, '
                    'or finish_case(fail_closed) if this is a real semantic conflict'
                )
            else:
                recommended = (
                    'revise to accepted target_absent/supplemental if Bangumi lacks the target, '
                    'or finish_case(fail_closed) if this is a real unresolved conflict'
                )
        elif candidate_refs:
            if file_count > 1 and any(str(ref or '').startswith('BES') for ref in candidate_refs):
                recommended = 'for this multi-file row, propose map_regular_span with chosen_span_ref set to the visible BES* candidate, or use target_absent/supplemental if you judge the visible candidates do not correspond'
            elif file_count > 1:
                recommended = 'for this multi-file row, use visible item_refs to propose map_regular_span, or use target_absent/supplemental if you judge the visible candidates do not correspond'
            else:
                recommended = 'propose_mapping_intents choosing one visible candidate_target_ref, reject wrong candidates, or use target_absent/supplemental with a clear reason'
        elif requested_types:
            recommended = 'execute_evidence for the requested_request_types, or revise the semantic intent with a visible BS/BE/BES ref'
        elif file_count == 1:
            recommended = 'choose a visible BE item with map_explicit_item, request subject/episode evidence, or mark target_absent/supplemental'
        else:
            recommended = 'choose a visible BS subject with map_regular_span plus episode_start/episode_end, then execute target_span if the compiler asks for BES'
        observations.append({
            'row_ref': str(getattr(row, 'row_ref', '') or ''),
            'local_ref': local_ref,
            'status': str(getattr(row, 'status', '') or ''),
            'disposition': str(getattr(row, 'disposition', '') or ''),
            'reason_kind': str(getattr(row, 'reason_kind', '') or ''),
            'selected_target_ref': str(getattr(row, 'selected_target_ref', '') or ''),
            'candidate_target_refs': candidate_refs[:12],
            'candidate_target_briefs': _target_ref_briefs(workspace, candidate_refs, limit=12),
            'candidate_target_conflicts': _candidate_target_conflicts_for_row(workspace, row),
            'unowned_candidate_target_refs': _unowned_candidate_target_refs_for_row(workspace, row)[:12],
            'requested_request_types': requested_types[:8],
            'query_hints': list(getattr(row, 'query_hints', []) or [])[:8],
            'subject_refs': list(getattr(row, 'subject_refs', []) or [])[:8],
            'item_refs': list(getattr(row, 'item_refs', []) or [])[:8],
            'visible_subject_item_sequences': _visible_subject_item_sequences_for_row(
                workspace,
                row,
                file_count=file_count,
                candidate_refs=candidate_refs,
            ),
            **local_brief,
            'protocol_warning': protocol_warning,
            'recommended_next': recommended,
        })
    return observations


def _all_open_rows_are_terminal_fail_closed(workspace: CaseEvidenceWorkspace) -> bool:
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None:
        return False
    rows = [
        row for row in list(getattr(draft, 'rows', []) or [])
        if str(getattr(row, 'disposition', '') or '') not in {'map_to_bangumi', 'non_bangumi_or_supplemental'}
        and str(getattr(row, 'status', '') or '') != 'verified'
    ]
    return bool(rows) and all(
        str(getattr(row, 'disposition', '') or '') == 'unaligned_fail_closed'
        for row in rows
    )


def _finish_gate_observation(workspace: CaseEvidenceWorkspace) -> dict[str, object]:
    draft_observation = _mapping_draft_observation(workspace)
    accounting = draft_observation.get('draft_accounting') if isinstance(draft_observation, dict) else None
    accepted_ready = bool((accounting or {}).get('accepted_accounting_ready')) if isinstance(accounting, dict) else False
    no_new_audit = _no_new_evidence_precondition_audit(workspace)
    terminal_fail_closed_ready = _all_open_rows_are_terminal_fail_closed(workspace)
    return {
        'accepted_finish_allowed': bool(accepted_ready and draft_observation.get('accounting_verifier_passed')),
        'fail_closed_finish_allowed_for_terminal_fail_rows': terminal_fail_closed_ready,
        'fail_closed_no_new_evidence_allowed': bool(no_new_audit.get('no_new_evidence_preconditions_ok')),
        'draft_accounting': accounting,
        'accounting_issue_codes': draft_observation.get('accounting_issue_codes') if isinstance(draft_observation, dict) else [],
        'open_rows': _open_rows_observation(workspace),
        'target_absent_protocol': 'Bangumi target absence is accepted accounting only through mark_non_bangumi_or_supplemental(reason_kind=bangumi_target_absent); mark_unaligned_fail_closed is terminal fail_closed.',
        **no_new_audit,
    }


def _mapping_draft_observation(workspace: CaseEvidenceWorkspace) -> dict[str, object]:
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None:
        return {
            'draft_row_count': 0,
            'draft_accounting': None,
            'accounting_verifier_passed': False,
            'accounting_issue_codes': [],
            'open_rows': [],
        }
    dossier = workspace.to_dossier(round_context='orchestrator_tool_observation')
    accounting = compute_mapping_draft_accounting(draft, dossier)
    verifier_result = verify_mapping_draft_accounting(dossier, draft)
    return {
        'draft_row_count': len(list(getattr(draft, 'rows', []) or [])),
        'draft_accounting': accounting.model_dump(mode='json') if hasattr(accounting, 'model_dump') else accounting,
        'accounting_verifier_passed': bool(getattr(verifier_result, 'passed', False)),
        'accounting_issue_codes': _dedupe_preserve_order([
            str(getattr(issue, 'issue_code', '') or '')
            for issue in list(getattr(verifier_result, 'issues', []) or [])
        ]),
        'open_rows': _open_rows_observation(workspace),
    }


def _executable_menu_observation(workspace: CaseEvidenceWorkspace) -> dict[str, object]:
    menu = build_executable_evidence_menu(workspace, max_requests=24)
    completed_or_failed = _completed_or_failed_menu_request_ids(workspace)
    summaries = [
        summary for summary in list(menu.get('prompt_summaries') or [])
        if isinstance(summary, dict)
        and str(summary.get('request_id') or '')
        and str(summary.get('request_id') or '') not in completed_or_failed
    ]
    return {
        'request_count': len(summaries),
        'request_ids': [str(summary.get('request_id') or '') for summary in summaries[:12]],
        'request_types': _dedupe_preserve_order([str(summary.get('request_type') or '') for summary in summaries if str(summary.get('request_type') or '')]),
        'completed_or_failed_request_count': len(completed_or_failed),
    }


def _workspace_with_tool_accounting_audit(workspace: CaseEvidenceWorkspace, *, note: str = 'orchestrator_tool_mapping_draft_accounting') -> CaseEvidenceWorkspace:
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None:
        return workspace
    dossier = workspace.to_dossier(round_context=note)
    accounting = compute_mapping_draft_accounting(draft, dossier)
    verifier_result = verify_mapping_draft_accounting(dossier, draft)
    return _workspace_with_judge_audit(workspace, {
        'note': note,
        'mapping_draft_accounting': accounting.model_dump(mode='json') if hasattr(accounting, 'model_dump') else accounting,
        'verifier_passed': bool(getattr(verifier_result, 'passed', False)),
        'verifier_issue_codes': _dedupe_preserve_order([
            str(getattr(issue, 'issue_code', '') or '')
            for issue in list(getattr(verifier_result, 'issues', []) or [])
        ]),
    })


def _workspace_visible_ref_set(workspace: CaseEvidenceWorkspace) -> set[str]:
    visible = workspace.to_dossier(round_context='orchestrator_tool_visible_refs').visible_refs
    refs = {
        *list(getattr(visible, 'local_file_refs', []) or []),
        *list(getattr(visible, 'local_cluster_refs', []) or []),
        *list(getattr(visible, 'bangumi_subject_refs', []) or []),
        *list(getattr(visible, 'bangumi_relation_refs', []) or []),
        *list(getattr(visible, 'bangumi_group_refs', []) or []),
        *list(getattr(visible, 'bangumi_item_refs', []) or []),
        *list(getattr(visible, 'query_refs', []) or []),
        *list(getattr(visible, 'target_refs', []) or []),
        *[str(getattr(card, 'ref', '') or '') for card in list(getattr(workspace, 'local_span_cards', []) or [])],
        *[str(getattr(card, 'ref', '') or '') for card in list(getattr(workspace, 'bangumi_span_cards', []) or [])],
    }
    return {ref for ref in refs if ref}


_QUERY_BRACKETED_TEXT_RE = re.compile(r'[\[\(\uFF08\u3010\u300C\u300E]\s*([^\]\)\uFF09\u3011\u300D\u300F]{2,80}?)\s*[\]\)\uFF09\u3011\u300D\u300F]')
_QUERY_TRAILING_YEAR_RE = re.compile(r'(?i)(?:[\s._-]+|\s*[\(\[\uFF08\u3010])((?:19|20)\d{2})(?:[\]\)\uFF09\u3011])?\s*$')
_QUERY_TRAILING_SCOPE_RE = re.compile(
    r'(?i)(?:[\s._-]+(?:OAD|OAV|OVA|ONA|SP|Specials?|S\d+|Season\s*\d+|TV\s+Series|Movie|\u7B2C\s*\d+\s*\u5B63)\s*\d*)\s*$'
)
_QUERY_TECHNICAL_TEXT_RE = re.compile(
    r'(?i)(?:\b(?:BDRip|Blu-?ray|WEB-?DL|HEVC|AVC|x26[45]|H\.?26[45]|1080p|720p|2160p|FLAC|AAC|Hi10P|Ma10p|YUV|CRC|MKV|PNG|Sub)\b|\u5B57\u5E55)'
)
_QUERY_CJK_TEXT_RE = re.compile(r'[\u3040-\u30ff\u3400-\u9fff]')
_QUERY_CAMEL_TOKEN_RE = re.compile(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+')
_QUERY_INSTRUCTION_TEXT_RE = re.compile(
    r'(?i)\b(?:prefer|avoid|use|search|query|queries|title-preserving|codec|resolution|group\s+tags|failed\s+recall|separately|instead)\b'
)


def _strip_subject_query_scope_suffix(text: str) -> tuple[str, list[str]]:
    value = str(text or '').strip()
    removed: list[str] = []
    while value:
        changed = False
        year_match = _QUERY_TRAILING_YEAR_RE.search(value)
        if year_match:
            before = value[:year_match.start()].strip(' ._-([{\uFF08\u3010')
            if before:
                removed.append(year_match.group(1))
                value = before
                changed = True
        scope_match = _QUERY_TRAILING_SCOPE_RE.search(value)
        if scope_match:
            before = value[:scope_match.start()].strip(' ._-([{\uFF08\u3010')
            if before:
                removed.append(scope_match.group(0).strip(' ._-([{\uFF08\u3010)]}\uFF09\u3011'))
                value = before
                changed = True
        if not changed:
            break
    return value.strip(), [term for term in removed if term]


def _looks_like_metadata_only_subject_query(text: str) -> bool:
    value = str(text or '').strip()
    if not value:
        return True
    if len(value) > 48 and _QUERY_INSTRUCTION_TEXT_RE.search(value):
        return True
    if re.fullmatch(r'(?i)(?:main\s+tv\s+series|regular\s+episodes?|specials?|OAD|OAV|OVA|ONA|SP|S\d+|Season\s*\d+|Movie|(?:19|20)\d{2})(?:\s*\d+\s*(?:-\s*\d+)?)?', value):
        return True
    if _QUERY_TECHNICAL_TEXT_RE.search(value) and not _QUERY_CJK_TEXT_RE.search(value):
        return True
    return False


def _camelcase_subject_query_variants(text: str) -> list[str]:
    value = str(text or '').strip()
    if not value or not re.fullmatch(r'[A-Za-z][A-Za-z0-9]*', value):
        return []
    tokens = _QUERY_CAMEL_TOKEN_RE.findall(value)
    if len(tokens) < 2:
        return []
    variants: list[str] = []
    spaced = ' '.join(tokens)
    if spaced and spaced.casefold() != value.casefold():
        variants.append(spaced)
    # Title-preserving romanized anime names are often stored with a fused first
    # word and a spaced noun, e.g. two short identical syllable tokens followed
    # by a longer title noun. This is recall normalization, not target choice.
    if len(tokens) >= 3 and tokens[0].casefold() == tokens[1].casefold():
        fused_first = ''.join(tokens[:2]).capitalize()
        rest = ' '.join(tokens[2:])
        if rest:
            variants.append(f'{fused_first} {rest}')
    return _dedupe_preserve_order(variants)


def _subject_query_text_variants(raw_text: str) -> tuple[list[str], list[dict[str, object]]]:
    text = re.sub(r'\s+', ' ', str(raw_text or '').strip())
    if not text:
        return [], [{'reason': 'empty_query_text'}]
    raw_variants: list[str] = []
    bracket_values = [match.group(1).strip() for match in _QUERY_BRACKETED_TEXT_RE.finditer(text)]
    outside = _QUERY_BRACKETED_TEXT_RE.sub(' ', text)
    outside = re.sub(r'\s+', ' ', outside).strip()
    if outside:
        raw_variants.append(outside)
    for value in bracket_values:
        if value and not _looks_like_metadata_only_subject_query(value):
            raw_variants.append(value)
    if not raw_variants:
        raw_variants.append(text)

    variants: list[str] = []
    dropped: list[dict[str, object]] = []
    for candidate in raw_variants:
        normalized, removed_terms = _strip_subject_query_scope_suffix(candidate)
        if _looks_like_metadata_only_subject_query(normalized):
            dropped.append({'query_text': candidate, 'reason': 'metadata_only_query_text'})
            continue
        variants.append(normalized)
        variants.extend(_camelcase_subject_query_variants(normalized))
        if removed_terms:
            dropped.append({'query_text': candidate, 'reason': 'scope_terms_removed', 'removed_terms': removed_terms})
    return _dedupe_preserve_order(variants), dropped


def _run_orchestrator_materialize_queries_tool(
    workspace: CaseEvidenceWorkspace,
    args: MaterializeQueriesToolArgs,
) -> tuple[CaseEvidenceWorkspace, dict[str, object]]:
    existing_texts = {
        str(getattr(card, 'query_text', '') or '').strip().casefold()
        for card in list(getattr(workspace, 'query_cards', []) or [])
        if str(getattr(card, 'query_text', '') or '').strip()
    }
    visible_refs = _workspace_visible_ref_set(workspace)
    next_index = _next_query_card_index(list(getattr(workspace, 'query_cards', []) or []))
    query_specs = list(getattr(args, 'queries', []) or [])
    for hint in list(getattr(args, 'query_hints', []) or []):
        query_specs.append(type('QuerySpec', (), {
            'query_text': str(hint or ''),
            'source_refs': list(getattr(args, 'local_refs', []) or []),
            'included_terms': [],
            'ignored_terms': list(getattr(args, 'ignored_noise_terms', []) or []),
            'reason': str(getattr(args, 'reason', '') or 'orchestrator query hint'),
            'confidence': 'medium',
        })())

    query_cards: list[QueryCard] = []
    dropped: list[dict[str, object]] = []
    for spec in query_specs:
        source_refs = _dedupe_preserve_order([
            str(ref or '')
            for ref in list(getattr(spec, 'source_refs', []) or list(getattr(args, 'local_refs', []) or []))
            if str(ref or '')
        ])
        query_texts, variant_drops = _subject_query_text_variants(str(getattr(spec, 'query_text', '') or ''))
        dropped.extend(variant_drops)
        if not query_texts:
            continue
        hidden_source_refs = [ref for ref in source_refs if ref not in visible_refs]
        if hidden_source_refs:
            dropped.extend({'query_text': query_text, 'reason': 'hidden_source_refs', 'source_refs': hidden_source_refs} for query_text in query_texts)
            continue
        for query_text in query_texts:
            key = query_text.casefold()
            if key in existing_texts:
                dropped.append({'query_text': query_text, 'reason': 'duplicate_query_text'})
                continue
            ref = f'QC{next_index}'
            next_index += 1
            existing_texts.add(key)
            query_cards.append(QueryCard(
                ref=ref,
                query_text=query_text,
                query_kind='subject_search',
                query_origin='agent_composed',
                source_refs=source_refs,
                included_terms=list(getattr(spec, 'included_terms', []) or []),
                ignored_terms=list(getattr(spec, 'ignored_terms', []) or list(getattr(args, 'ignored_noise_terms', []) or [])),
                reason=str(getattr(spec, 'reason', '') or getattr(args, 'reason', '') or 'orchestrator materialized query'),
                confidence=str(getattr(spec, 'confidence', '') or 'medium'),
            ))
    if not query_cards:
        workspace = _workspace_with_judge_audit(workspace, {
            'note': 'orchestrator_materialize_queries_noop',
            'reason': str(getattr(args, 'reason', '') or ''),
            'dropped_queries': dropped,
        })
        return workspace, {
            'status': 'rejected',
            'reason': 'no_new_query_cards',
            'dropped_queries': dropped,
            'executable_menu_summary': _executable_menu_observation(workspace),
            'recommended_next_observation': 'provide clean title/alias query texts with materialize_queries, execute existing subject_search evidence, or finish only if evidence is truly exhausted',
        }
    workspace = workspace.with_query_cards(query_cards)
    workspace = _workspace_with_judge_audit(workspace, {
        'note': 'orchestrator_queries_materialized',
        'query_refs': [card.ref for card in query_cards],
        'query_texts': [card.query_text for card in query_cards],
        'dropped_queries': dropped,
    })
    return workspace, {
        'status': 'ok',
        'workspace_changed': True,
        'query_refs': [card.ref for card in query_cards],
        'query_texts': [card.query_text for card in query_cards],
        'dropped_queries': dropped,
        'executable_menu_summary': _executable_menu_observation(workspace),
        'recommended_next_observation': 'execute subject_search evidence for useful QC refs or propose_mapping_intents if enough target surface is already visible',
    }


def _run_orchestrator_execute_evidence_tool(
    workspace: CaseEvidenceWorkspace,
    planner_output: EvidencePlannerOutput,
    bangumi_client,
    evidence_batches: list[EvidenceBatchResult],
) -> tuple[CaseEvidenceWorkspace, dict[str, object]]:
    before_counts = _target_surface_counts(workspace)
    selected_ids = list(getattr(getattr(planner_output, 'plan', None), 'selected_menu_request_ids', []) or [])
    fresh_ids, stale_ids = _filter_stale_menu_request_ids(workspace, selected_ids)
    if selected_ids and not fresh_ids:
        return workspace, {
            'status': 'rejected',
            'reason': 'all_selected_evidence_requests_are_stale',
            'workspace_changed': False,
            'target_surface_changed': False,
            'selected_menu_request_ids': selected_ids,
            'stale_menu_request_ids': stale_ids,
            'executed_menu_request_ids': [],
            'response_refs': [],
            'target_surface_before': before_counts,
            'target_surface_after': before_counts,
            **_mapping_draft_observation(workspace),
            'executable_menu_summary': _executable_menu_observation(workspace),
            'finish_gate': _finish_gate_observation(workspace),
            'recommended_next_observation': 'choose a fresh evidence request, propose mapping intents for the listed open_rows using the existing target surface, or finish only when finish_gate allows it',
        }
    workspace, batch_result = _execute_menu_request_ids(
        workspace,
        fresh_ids,
        bangumi_client,
        evidence_batches,
        note='orchestrator_execute_evidence',
        planner_output=planner_output,
    )
    workspace = _workspace_with_tool_accounting_audit(workspace)
    after_counts = _target_surface_counts(workspace)
    target_surface_changed = before_counts != after_counts
    response_refs = [
        ref
        for request_result in list(getattr(batch_result, 'request_results', []) or []) if batch_result is not None
        for ref in list(getattr(request_result, 'response_refs', []) or [])
    ]
    request_statuses = [
        {
            'request_ref': str(getattr(request_result, 'request_ref', '') or ''),
            'request_type': str(getattr(request_result, 'request_type', '') or ''),
            'accepted': bool(getattr(request_result, 'accepted', False)),
            'response_refs': list(getattr(request_result, 'response_refs', []) or [])[:12],
            'notes': list(getattr(request_result, 'notes', []) or [])[:4],
        }
        for request_result in list(getattr(batch_result, 'request_results', []) or []) if batch_result is not None
    ]
    recommended_next = 'propose_mapping_intents if target_surface_changed or candidate refs changed; otherwise choose another evidence tool or finish_case if exhausted'
    if after_counts.get('subject_count', 0) > 0 and after_counts.get('item_count', 0) == 0:
        recommended_next = 'subjects are visible but no BE/BES assignable targets exist yet; execute episode_list, subject_lookup, or related_expansion before applying mapping patches'
    elif after_counts.get('item_count', 0) > 0 and not target_surface_changed:
        recommended_next = 'use the visible BE items in propose_mapping_intents, or execute target_span for multi-file rows that need BES span_by_index mapping'
    return workspace, {
        'status': str(getattr(batch_result, 'status', '') or ('skipped' if not fresh_ids else 'no_batch')),
        'workspace_changed': target_surface_changed or bool(response_refs),
        'target_surface_changed': target_surface_changed,
        'selected_menu_request_ids': selected_ids,
        'stale_menu_request_ids': stale_ids,
        'executed_menu_request_ids': fresh_ids,
        'response_refs': response_refs[:24],
        'request_statuses': request_statuses,
        'target_surface_before': before_counts,
        'target_surface_after': after_counts,
        **_mapping_draft_observation(workspace),
        'executable_menu_summary': _executable_menu_observation(workspace),
        'finish_gate': _finish_gate_observation(workspace),
        'recommended_next_observation': recommended_next,
    }


def _run_orchestrator_propose_mapping_intents_tool(
    workspace: CaseEvidenceWorkspace,
    args: ProposeMappingIntentsToolArgs,
) -> tuple[CaseEvidenceWorkspace, dict[str, object]]:
    workspace = _refresh_mapping_draft_candidates(_workspace_with_initial_mapping_draft(workspace))
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None or not getattr(draft, 'rows', None):
        workspace = _workspace_with_judge_audit(workspace, {'note': 'mapping_intents_skipped', 'reason': 'no_draft'})
        return workspace, {'status': 'rejected', 'reason': 'no_draft', 'executable_menu_summary': _executable_menu_observation(workspace)}
    if not _draft_open_rows(draft):
        workspace = _workspace_with_judge_audit(workspace, {'note': 'mapping_intents_skipped', 'reason': 'no_open_rows'})
        return workspace, {
            'status': 'rejected',
            'reason': 'no_open_rows',
            **_mapping_draft_observation(workspace),
            'executable_menu_summary': _executable_menu_observation(workspace),
        }
    coverage_issue = _mapping_draft_local_coverage_issue(workspace, draft)
    if coverage_issue is not None:
        workspace = _workspace_with_judge_audit(workspace, coverage_issue)
        return workspace, {'status': 'rejected', 'reason': 'mapping_draft_incomplete_local_coverage', **coverage_issue}

    mapping_intents = list(getattr(args, 'mapping_intents', []) or [])
    notebook_updates = list(getattr(args, 'notebook_updates', []) or [])
    workspace = _workspace_with_judge_audit(workspace, {
        'note': 'orchestrator_mapping_intents_called',
        'mapping_intent_count': len(mapping_intents),
        'notebook_update_count': len(notebook_updates),
        'reason': str(getattr(args, 'reason', '') or ''),
    })
    if not mapping_intents and not notebook_updates:
        return workspace, {
            'status': 'rejected',
            'reason': 'no_mapping_intents_or_notebook_updates',
            **_mapping_draft_observation(workspace),
            'executable_menu_summary': _executable_menu_observation(workspace),
        }

    dossier = workspace.to_dossier(round_context='mapping_intents')
    workspace = _workspace_with_notebook_updates(workspace, dossier, notebook_updates, source='orchestrator_agent_mapping_intents')
    dossier = workspace.to_dossier(round_context='mapping_intent_compile')
    compiler_result = MappingIntentCompiler().compile(dossier, draft, mapping_intents)
    compiled_patches = list(getattr(compiler_result, 'compiled_patches', []) or [])
    generated_span_cards = list(getattr(compiler_result, 'generated_span_cards', []) or [])
    if generated_span_cards:
        workspace = _workspace_preserving_state(
            workspace,
            bangumi_span_cards=[*list(getattr(workspace, 'bangumi_span_cards', []) or []), *generated_span_cards],
        )
        workspace = workspace.with_seen_detail_refs(_dedupe_preserve_order([
            *[str(getattr(card, 'ref', '') or '') for card in generated_span_cards],
            *[
                str(target_ref or '')
                for card in generated_span_cards
                for target_ref in list(getattr(card, 'target_refs', []) or [])
            ],
        ]))
        dossier = workspace.to_dossier(round_context='mapping_intent_compile_generated_spans')
        workspace = _workspace_with_judge_audit(workspace, {
            'note': 'orchestrator_mapping_intents_generated_spans',
            'span_refs': [str(getattr(card, 'ref', '') or '') for card in generated_span_cards],
            'target_ref_counts': [int(getattr(card, 'target_ref_count', 0) or 0) for card in generated_span_cards],
        })
    compiled_patches, dropped_non_open_patches = _filter_mapping_patches_for_agent_revision(draft, compiled_patches)
    if dropped_non_open_patches:
        workspace = _workspace_with_judge_audit(workspace, {
            'note': 'orchestrator_non_open_compiled_intent_patches_ignored',
            'patch_count': len(dropped_non_open_patches),
            'local_refs': _dedupe_preserve_order([
                _patch_draft_local_ref(draft, patch)
                for patch in dropped_non_open_patches
                if _patch_draft_local_ref(draft, patch)
            ]),
        })

    non_progress_issues = _non_progress_needs_more_evidence_issues(workspace, draft, compiled_patches)
    if non_progress_issues:
        blocked_refs = {
            str(ref or '')
            for issue in non_progress_issues
            for ref in list(getattr(issue, 'related_refs', []) or [])
            if str(ref or '')
        }
        compiled_patches = [
            patch for patch in compiled_patches
            if not (
                str(getattr(normalize_mapping_patch_op(patch), 'op', '') or '') == 'needs_more_evidence'
                and _patch_draft_local_ref(draft, patch) in blocked_refs
            )
        ]
    updated_draft, patch_issues = apply_mapping_patches(draft, compiled_patches, dossier)
    patch_issues = [*non_progress_issues, *patch_issues]
    workspace = _workspace_with_mapping_draft(
        workspace,
        updated_draft,
        patches=compiled_patches,
        note='orchestrator_mapping_intents_compiled',
    )
    if patch_issues:
        workspace = _workspace_with_mapping_patch_issue_audit(workspace, patch_issues)
    workspace, materialized_query_refs = _workspace_with_editor_query_hints(workspace, compiled_patches)
    workspace = _workspace_with_tool_accounting_audit(workspace)
    accounting_observation = _mapping_draft_observation(workspace)
    reopened_accounting_issue_codes: list[str] = []
    reopened_accounting_issue_refs: list[str] = []
    reopened_accounting_issue_row_count = 0
    if not bool(accounting_observation.get('accounting_verifier_passed')):
        _accounting, accounting_verifier = _mapping_draft_accounting_result(workspace)
        accounting_issues = list(getattr(accounting_verifier, 'issues', []) or []) if accounting_verifier is not None else []
        repairable_codes = {
            'duplicate_target',
            'count_mismatch',
            'invalid_target',
            'invalid_mapping_mode',
            'invalid_span_alignment',
            'missing_span_ref',
            'missing_support_refs',
            'invalid_explicit_multi_file_mapping',
            'duplicate_local_span',
            'duplicate_local_ref',
        }
        repairable_issues = [
            issue for issue in accounting_issues
            if str(getattr(issue, 'issue_code', '') or '') in repairable_codes
        ]
        if repairable_issues and not _open_rows_observation(workspace, limit=1):
            before_open_refs = {
                str(row.get('row_ref') or '')
                for row in _open_rows_observation(workspace)
                if isinstance(row, dict) and str(row.get('row_ref') or '')
            }
            workspace = _reopen_mapping_draft_issue_rows(workspace, repairable_issues)
            after_open_rows = _open_rows_observation(workspace)
            after_open_refs = {
                str(row.get('row_ref') or '')
                for row in after_open_rows
                if isinstance(row, dict) and str(row.get('row_ref') or '')
            }
            reopened_refs = _dedupe_preserve_order(list(after_open_refs - before_open_refs))
            if reopened_refs:
                reopened_accounting_issue_codes = _dedupe_preserve_order([
                    str(getattr(issue, 'issue_code', '') or '')
                    for issue in repairable_issues
                ])
                reopened_accounting_issue_refs = _dedupe_preserve_order([
                    ref
                    for issue in repairable_issues
                    for ref in [
                        str(getattr(issue, 'ref', '') or ''),
                        *[str(value or '') for value in list(getattr(issue, 'related_refs', []) or [])],
                    ]
                    if ref
                ])[:24]
                reopened_accounting_issue_row_count = len(reopened_refs)
                workspace = _workspace_with_judge_audit(workspace, {
                    'note': 'orchestrator_accounting_issue_rows_reopened',
                    'issue_codes': reopened_accounting_issue_codes,
                    'issue_refs': reopened_accounting_issue_refs,
                    'reopened_row_refs': reopened_refs,
                    'reason': 'mapping draft accounting verifier found repairable legality issues; rows reopened for OrchestratorAgent semantic repair',
                })
                accounting_observation = _mapping_draft_observation(workspace)
    blocked_intents = list(getattr(compiler_result, 'blocked_intents', []) or [])
    blocked_issue_codes = _dedupe_preserve_order([
        str(code or '')
        for blocked in blocked_intents
        for code in list(getattr(blocked, 'issue_codes', []) or [])
    ])
    patch_issue_codes = _dedupe_preserve_order([str(getattr(issue, 'issue_code', '') or '') for issue in patch_issues])
    requested_evidence = _dedupe_preserve_order([
        str(request_type or '')
        for request_type in list(getattr(compiler_result, 'requested_evidence', []) or [])
    ])
    recommended_next = str(getattr(compiler_result, 'recommended_next_observation', '') or '')
    if 'non_progress_needs_more_evidence_with_visible_candidates' in patch_issue_codes:
        recommended_next = 'do not repeat needs_more_evidence for this row while visible candidates or same-count item sequences remain. Map the visible sequence if semantically correct, revise/repartition if ownership is wrong, mark_non_bangumi_or_supplemental(reason_kind=bangumi_target_absent) if Bangumi lacks the corresponding target, or finish fail_closed only with a concrete semantic blocker'
    elif patch_issue_codes:
        recommended_next = 'revise semantic intents or execute missing evidence shown in patch issues'
    executable_summary = _executable_menu_observation(workspace)
    executable_types = set(str(value or '') for value in list(executable_summary.get('request_types') or []))
    has_matching_executable_evidence = bool(set(requested_evidence) & executable_types)
    if blocked_intents and requested_evidence and has_matching_executable_evidence:
        recommended_next = 'execute requested evidence, then propose the same semantic mapping intent again'
    elif blocked_intents and requested_evidence:
        recommended_next = (
            'the compiler requested evidence, but no matching executable evidence request is currently available. '
            'Revise the semantic intent using visible refs, materialize a clean title query, repartition if the row is too broad, '
            'or mark target_absent/supplemental if that is your investigated conclusion.'
        )
    elif blocked_intents:
        recommended_next = 'revise semantic intents using visible refs or finish only if evidence is genuinely exhausted'
    accounting_payload = accounting_observation.get('draft_accounting')
    unresolved_count = int((accounting_payload or {}).get('unresolved_count') or 0) if isinstance(accounting_payload, dict) else 0
    open_rows_payload = accounting_observation.get('open_rows') if isinstance(accounting_observation.get('open_rows'), list) else []
    terminal_fail_rows = [
        row for row in open_rows_payload
        if isinstance(row, dict) and str(row.get('disposition') or '') == 'unaligned_fail_closed'
    ]
    if unresolved_count > 0 and not blocked_intents and not patch_issue_codes:
        if terminal_fail_rows:
            recommended_next = (
                'accounting is unresolved because some rows are mark_unaligned_fail_closed. '
                'That disposition is a terminal fail_closed row and can never produce accepted. '
                'If Bangumi simply has no corresponding target, revise those rows with '
                'mark_non_bangumi_or_supplemental(reason_kind=bangumi_target_absent); otherwise call finish_case(fail_closed) when the finish gate allows it.'
            )
        else:
            recommended_next = 'accounting is still unresolved; do not finish_case yet. Propose mapping intents for open_rows, or execute evidence if an open row still needs target surface.'
    if reopened_accounting_issue_codes:
        recommended_next = (
            'accounting verifier found legality issues and reopened the affected rows. '
            'Do not finish_case yet. If the open row is too broad or mixes multiple works/resources, call propose_case_understanding '
            'with a new exact-once file partition; otherwise propose revised mapping intents for open_rows and avoid duplicate/invalid targets.'
        )
    status = 'ok'
    if blocked_intents and not compiled_patches:
        status = 'blocked_intents'
    elif blocked_intents:
        status = 'partial'
    elif patch_issues:
        status = 'patch_issues'
    elif reopened_accounting_issue_codes:
        status = 'accounting_issues'
    workspace = _workspace_with_judge_audit(workspace, {
        'note': 'orchestrator_mapping_intents_result',
        'status': status,
        'mapping_intent_count': len(mapping_intents),
        'compiled_patch_count': len(compiled_patches),
        'generated_span_count': len(generated_span_cards),
        'generated_span_refs': [str(getattr(card, 'ref', '') or '') for card in generated_span_cards],
        'blocked_intent_count': len(blocked_intents),
        'blocked_intent_issue_codes': blocked_issue_codes,
        'blocked_intents': [
            item.model_dump(mode='json') if hasattr(item, 'model_dump') else item
            for item in blocked_intents[:8]
        ],
        'requested_evidence': requested_evidence,
        'patch_issue_codes': patch_issue_codes,
        'reopened_accounting_issue_codes': reopened_accounting_issue_codes,
        'reopened_accounting_issue_refs': reopened_accounting_issue_refs,
        'reopened_accounting_issue_row_count': reopened_accounting_issue_row_count,
        'draft_accounting': accounting_observation.get('draft_accounting'),
        'open_rows': open_rows_payload,
        'terminal_fail_closed_row_count': len(terminal_fail_rows),
        'finish_gate': _finish_gate_observation(workspace),
        'executable_menu_summary': executable_summary,
        'matching_requested_evidence_available': has_matching_executable_evidence,
        'recommended_next_observation': recommended_next,
    })
    return workspace, {
        'status': status,
        'workspace_changed': bool(compiled_patches or notebook_updates or materialized_query_refs),
        'target_surface_changed': False,
        'mapping_intent_count': len(mapping_intents),
        'compiled_patch_count': len(compiled_patches),
        'generated_span_count': len(generated_span_cards),
        'generated_span_refs': [str(getattr(card, 'ref', '') or '') for card in generated_span_cards],
        'dropped_patch_count': len(dropped_non_open_patches),
        'blocked_intents': [
            item.model_dump(mode='json') if hasattr(item, 'model_dump') else item
            for item in blocked_intents
        ],
        'blocked_intent_count': len(blocked_intents),
        'blocked_intent_issue_codes': blocked_issue_codes,
        'requested_evidence': requested_evidence,
        'patch_issue_codes': patch_issue_codes,
        'reopened_accounting_issue_codes': reopened_accounting_issue_codes,
        'reopened_accounting_issue_refs': reopened_accounting_issue_refs,
        'reopened_accounting_issue_row_count': reopened_accounting_issue_row_count,
        'patch_issue_refs': _dedupe_preserve_order([str(getattr(issue, 'ref', '') or '') for issue in patch_issues]),
        'materialized_query_refs': materialized_query_refs,
        'draft_accounting': accounting_observation.get('draft_accounting'),
        'open_rows': open_rows_payload,
        'terminal_fail_closed_rows': terminal_fail_rows,
        'accounting_verifier_passed': accounting_observation.get('accounting_verifier_passed'),
        'accounting_issue_codes': accounting_observation.get('accounting_issue_codes'),
        'executable_menu_summary': executable_summary,
        'finish_gate': _finish_gate_observation(workspace),
        'matching_requested_evidence_available': has_matching_executable_evidence,
        'recommended_next_observation': recommended_next,
    }


def _run_orchestrator_apply_draft_patches_tool(
    workspace: CaseEvidenceWorkspace,
    args: ApplyDraftPatchesToolArgs,
) -> tuple[CaseEvidenceWorkspace, dict[str, object]]:
    workspace = _refresh_mapping_draft_candidates(_workspace_with_initial_mapping_draft(workspace))
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None or not getattr(draft, 'rows', None):
        workspace = _workspace_with_judge_audit(workspace, {'note': 'mapping_draft_editor_skipped', 'reason': 'no_draft'})
        return workspace, {'status': 'rejected', 'reason': 'no_draft', 'executable_menu_summary': _executable_menu_observation(workspace)}
    if not _draft_open_rows(draft):
        workspace = _workspace_with_judge_audit(workspace, {'note': 'mapping_draft_editor_skipped', 'reason': 'no_open_rows'})
        return workspace, {
            'status': 'rejected',
            'reason': 'no_open_rows',
            **_mapping_draft_observation(workspace),
            'executable_menu_summary': _executable_menu_observation(workspace),
        }
    coverage_issue = _mapping_draft_local_coverage_issue(workspace, draft)
    if coverage_issue is not None:
        workspace = _workspace_with_judge_audit(workspace, coverage_issue)
        return workspace, {'status': 'rejected', 'reason': 'mapping_draft_incomplete_local_coverage', **coverage_issue}
    dossier = workspace.to_dossier(round_context='mapping_draft_edit')
    incoming_patches = list(getattr(args, 'patches', []) or [])
    candidate_comparisons = list(getattr(args, 'candidate_comparisons', []) or [])
    notebook_updates = list(getattr(args, 'notebook_updates', []) or [])
    workspace = _workspace_with_judge_audit(workspace, {
        'note': 'orchestrator_apply_draft_patches_called',
        'patch_count': len(incoming_patches),
        'candidate_comparison_count': len(candidate_comparisons),
        'notebook_update_count': len(notebook_updates),
        'reason': str(getattr(args, 'reason', '') or ''),
    })
    if not incoming_patches and not candidate_comparisons and not notebook_updates:
        return workspace, {
            'status': 'rejected',
            'reason': 'no_patches_comparisons_or_notebook_updates',
            **_mapping_draft_observation(workspace),
            'executable_menu_summary': _executable_menu_observation(workspace),
        }
    workspace = _workspace_with_notebook_updates(workspace, dossier, notebook_updates, source='orchestrator_agent_apply_draft_patches')
    editor_patches, dropped_non_open_patches = _filter_mapping_patches_to_open_rows(draft, incoming_patches)
    if dropped_non_open_patches:
        workspace = _workspace_with_judge_audit(workspace, {
            'note': 'orchestrator_non_open_row_patches_ignored',
            'patch_count': len(dropped_non_open_patches),
            'local_refs': _dedupe_preserve_order([
                _patch_draft_local_ref(draft, patch)
                for patch in dropped_non_open_patches
                if _patch_draft_local_ref(draft, patch)
            ]),
        })
    comparison_output = type('DirectDraftPatchOutput', (), {'candidate_comparisons': candidate_comparisons, 'patches': editor_patches})()
    comparison_issues = _comparison_patch_consistency_issues(draft, comparison_output, dossier, editor_patches)
    if comparison_issues:
        workspace = _workspace_with_mapping_patch_issue_audit(workspace, comparison_issues)
    updated_draft, patch_issues = apply_mapping_patches(draft, editor_patches, dossier)
    all_patch_issues = [*comparison_issues, *patch_issues]
    workspace = _workspace_with_mapping_draft(
        workspace,
        updated_draft,
        patches=editor_patches,
        candidate_comparisons=candidate_comparisons,
        note='orchestrator_draft_patches_applied',
    )
    workspace = _workspace_with_judge_audit(workspace, {
        'note': 'orchestrator_draft_patch_evidence_intent_observed',
        'evidence_intent_count': _mapping_patch_evidence_intent_count(editor_patches),
    })
    if patch_issues:
        workspace = _workspace_with_mapping_patch_issue_audit(workspace, patch_issues)
    workspace, materialized_query_refs = _workspace_with_editor_query_hints(workspace, editor_patches)
    workspace = _workspace_with_tool_accounting_audit(workspace)
    accounting_observation = _mapping_draft_observation(workspace)
    issue_codes = _dedupe_preserve_order([str(getattr(issue, 'issue_code', '') or '') for issue in all_patch_issues])
    recommended_next = 'fix patch issues with apply_draft_patches, execute requested evidence, or finish_case if accounting is ready/exhausted'
    if 'missing_target_ref' in issue_codes or 'unknown_target_ref' in issue_codes:
        recommended_next = 'mapping patches need visible BE/BES targets; execute episode_list/related_expansion/target_span if targets are missing, or retry apply_draft_patches with exact visible target_ref/target_span_ref'
    elif 'invalid_explicit_multi_file_mapping' in issue_codes or 'unknown_target_span_ref' in issue_codes:
        recommended_next = 'multi-file rows need an exact visible BES target_span_ref with mapping_mode=span_by_index; execute target_span if no suitable BES is visible, then retry apply_draft_patches'
    return workspace, {
        'status': 'ok' if not all_patch_issues else 'patch_issues',
        'workspace_changed': True,
        'target_surface_changed': False,
        'patch_count': len(editor_patches),
        'dropped_patch_count': len(dropped_non_open_patches),
        'patch_issue_codes': issue_codes,
        'patch_issue_refs': _dedupe_preserve_order([str(getattr(issue, 'ref', '') or '') for issue in all_patch_issues]),
        'materialized_query_refs': materialized_query_refs,
        'draft_accounting': accounting_observation.get('draft_accounting'),
        'accounting_verifier_passed': accounting_observation.get('accounting_verifier_passed'),
        'accounting_issue_codes': accounting_observation.get('accounting_issue_codes'),
        'executable_menu_summary': _executable_menu_observation(workspace),
        'recommended_next_observation': recommended_next,
    }


def _latest_judge_fail_closed_output(judge_outputs: list[CaseJudgeOutput]) -> CaseJudgeOutput | None:
    for output in reversed(list(judge_outputs or [])):
        if str(getattr(output, 'action', '') or '') == 'fail_closed':
            return output
    return None


def _run_orchestrator_judge_blocker_tool(
    workspace: CaseEvidenceWorkspace,
    ai_client,
    judge_outputs: list[CaseJudgeOutput],
) -> tuple[CaseEvidenceWorkspace, CaseAgentRunResult | None, dict[str, object]]:
    dossier = workspace.to_dossier(round_context='semantic_blocker')
    judge_result = call_case_judge(ai_client, dossier, round_kind='semantic_blocker')
    workspace = _workspace_with_judge_audit(workspace, getattr(judge_result, 'request_audit', None))
    if not judge_result.ok or judge_result.output is None:
        error_text = judge_result.error or 'case judge blocker call failed'
        lower = error_text.casefold()
        error_kind = 'context_overflow' if 'exceeds the context window' in lower else ('provider_no_response' if 'no response' in lower else 'provider_error')
        verifier_result = CaseVerifierResult(
            passed=False,
            issues=[VerifierIssue(ref='case_judge', issue_code=error_kind, severity='blocked', message=error_text)],
            summary=error_text,
        )
        result = CaseAgentRunResult(False, workspace.header.case_id, 'error', 'judge_blocker', None, verifier_result, workspace, judge_outputs, [], error_text, [error_text, f'error_kind={error_kind}'])
        return workspace, result, {'status': 'error', 'error_kind': error_kind, 'error': error_text}
    output = judge_result.output
    judge_outputs.append(output)
    workspace = _workspace_with_judge_output_capture(workspace, output)
    verifier_result = verify_judge_output(dossier, output) if output.action in {'submit_verdict', 'fail_closed', 'issue_response'} else CaseVerifierResult(passed=False, issues=[], summary='non-terminal blocker output')
    workspace = _workspace_with_judge_audit(workspace, {
        'note': 'orchestrator_judge_blocker_called',
        'action': output.action,
        'summary': output.summary,
        'verifier_passed': bool(getattr(verifier_result, 'passed', False)),
        'verifier_issue_codes': _dedupe_preserve_order([str(getattr(issue, 'issue_code', '') or '') for issue in list(getattr(verifier_result, 'issues', []) or [])]),
    })
    if not verifier_result.passed and list(getattr(verifier_result, 'issues', []) or []):
        workspace = _workspace_with_verifier_issues(workspace, verifier_result)
    is_valid_blocker = output.action == 'fail_closed' and bool(getattr(verifier_result, 'passed', False))
    issue_codes = _dedupe_preserve_order([str(getattr(issue, 'issue_code', '') or '') for issue in list(getattr(verifier_result, 'issues', []) or [])])
    return workspace, None, {
        'status': 'ok' if is_valid_blocker else 'rejected',
        'reason': '' if is_valid_blocker else ('judge_blocker_non_fail_closed_output' if output.action != 'fail_closed' else 'judge_blocker_verifier_rejected'),
        'judge_action': output.action,
        'judge_summary': output.summary,
        'verifier_passed': bool(getattr(verifier_result, 'passed', False)),
        'verifier_issue_codes': issue_codes,
        'recommended_next_observation': 'finish_case with semantic_target_conflict only after a verifier-valid fail_closed blocker; otherwise use edit_mapping_draft or execute_evidence to repair the listed issues',
    }


def _run_orchestrator_update_notebook_tool(
    workspace: CaseEvidenceWorkspace,
    args: UpdateNotebookToolArgs,
) -> tuple[CaseEvidenceWorkspace, dict[str, object]]:
    before_notebook = getattr(workspace, 'investigation_notebook', None)
    before_count = len(list(getattr(before_notebook, 'update_log', []) or [])) if before_notebook is not None else 0
    workspace = _workspace_with_notebook_updates(
        workspace,
        workspace.to_dossier(round_context='orchestrator_update_notebook'),
        list(args.notebook_updates or []),
        source='orchestrator_agent',
    )
    update_audit = next(
        (
            audit for audit in reversed(list(getattr(workspace, 'judge_request_audits', []) or []))
            if isinstance(audit, dict)
            and audit.get('note') == 'investigation_notebook_updates_observed'
            and audit.get('source') == 'orchestrator_agent'
        ),
        {},
    )
    after_notebook = getattr(workspace, 'investigation_notebook', None)
    after_count = len(list(getattr(after_notebook, 'update_log', []) or [])) if after_notebook is not None else 0
    rejected_count = int(update_audit.get('rejected_update_count') or 0) if isinstance(update_audit, dict) else 0
    workspace = _workspace_with_judge_audit(workspace, {
        'note': 'orchestrator_update_notebook_applied',
        'notebook_update_count': len(list(args.notebook_updates or [])),
        'accepted_update_count': int(update_audit.get('accepted_update_count') or 0) if isinstance(update_audit, dict) else 0,
        'rejected_update_count': rejected_count,
        'issue_codes': list(update_audit.get('issue_codes') or []) if isinstance(update_audit, dict) else [],
        'notebook_update_log_count_before': before_count,
        'notebook_update_log_count_after': after_count,
    })
    return workspace, {
        'status': 'rejected' if rejected_count else 'ok',
        'reason': 'notebook_update_rejected' if rejected_count else '',
        'workspace_changed': after_count != before_count,
        'notebook_update_count': len(list(args.notebook_updates or [])),
        'accepted_update_count': int(update_audit.get('accepted_update_count') or 0) if isinstance(update_audit, dict) else 0,
        'rejected_update_count': rejected_count,
        'issue_codes': list(update_audit.get('issue_codes') or []) if isinstance(update_audit, dict) else [],
        'issue_refs': list(update_audit.get('issue_refs') or []) if isinstance(update_audit, dict) else [],
        'open_question_count': len([
            question
            for question in list(getattr(after_notebook, 'open_questions', []) or [])
            if str(getattr(question, 'status', '') or '') == 'open'
        ]) if after_notebook is not None else 0,
        'draft_accounting': _mapping_draft_observation(workspace).get('draft_accounting'),
        'executable_menu_summary': _executable_menu_observation(workspace),
        'recommended_next_observation': 'continue with materialize_queries, execute_evidence, propose_mapping_intents, or finish_case after verification preconditions are met',
    }


def _run_orchestrator_reconsider_split_tool(
    workspace: CaseEvidenceWorkspace,
    args: ReconsiderSplitToolArgs,
) -> tuple[CaseEvidenceWorkspace, dict[str, object]]:
    local_refs = _dedupe_preserve_order([str(ref or '') for ref in list(getattr(args, 'local_refs', []) or []) if str(ref or '')])
    notebook_refs = _dedupe_preserve_order([str(ref or '') for ref in list(getattr(args, 'notebook_refs', []) or []) if str(ref or '')])
    workspace = _workspace_with_judge_audit(workspace, {
        'note': 'orchestrator_reconsider_split_observation',
        'reason': str(getattr(args, 'reason', '') or ''),
        'local_refs': local_refs,
        'notebook_refs': notebook_refs,
        'main_file_count': len(list(getattr(getattr(workspace, 'contract', None), 'main_file_refs', []) or [])),
        'local_cluster_count': len(list(getattr(workspace, 'local_clusters', []) or [])),
    })
    return workspace, {
        'status': 'ok',
        'workspace_changed': False,
        'target_surface_changed': False,
        'local_refs': local_refs,
        'notebook_refs': notebook_refs,
        'main_file_count': len(list(getattr(getattr(workspace, 'contract', None), 'main_file_refs', []) or [])),
        'local_cluster_sample': [
            {
                'ref': str(getattr(cluster, 'ref', '') or ''),
                'cluster_name': str(getattr(cluster, 'cluster_name', '') or ''),
                'file_refs': list(getattr(cluster, 'file_refs', []) or [])[:12],
            }
            for cluster in list(getattr(workspace, 'local_clusters', []) or [])[:8]
        ],
        'draft_accounting': _mapping_draft_observation(workspace).get('draft_accounting'),
        'executable_menu_summary': _executable_menu_observation(workspace),
        'recommended_next_observation': 'if current work units are too broad or mixed, call propose_case_understanding again with revised work units; otherwise update_notebook or continue evidence/mapping intents',
    }


def _workspace_with_orchestrator_session_summary(
    workspace: CaseEvidenceWorkspace,
    session: OrchestratorAgentSession,
) -> CaseEvidenceWorkspace:
    return _workspace_with_judge_audit(workspace, {
        'note': 'orchestrator_agent_session_summary',
        **orchestrator_session_audit(session),
    })


def _finalize_orchestrator_result(
    result: CaseAgentRunResult,
    workspace: CaseEvidenceWorkspace,
    session: OrchestratorAgentSession,
) -> CaseAgentRunResult:
    final_workspace = _workspace_with_orchestrator_session_summary(workspace, session)
    result.final_workspace = final_workspace
    return result


def _build_mapping_draft_accepted_result(
    workspace: CaseEvidenceWorkspace,
    judge_outputs: list[CaseJudgeOutput],
    evidence_batches: list[EvidenceBatchResult],
    *,
    planning_output: CasePlanningOutput | None,
) -> tuple[CaseAgentRunResult | None, CaseEvidenceWorkspace, dict[str, object]]:
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None:
        return None, workspace, {'status': 'rejected', 'reason': 'no_mapping_draft'}
    dossier = workspace.to_dossier(round_context='finish_case_accepted')
    accounting = compute_mapping_draft_accounting(draft, dossier)
    accounting_verifier = verify_mapping_draft_accounting(dossier, draft)
    workspace = _workspace_with_judge_audit(workspace, {
        'note': 'finish_case_accepted_accounting_checked',
        'mapping_draft_accounting': accounting.model_dump(mode='json'),
        'verifier_passed': bool(accounting_verifier.passed),
        'verifier_issue_codes': _dedupe_preserve_order([str(getattr(issue, 'issue_code', '') or '') for issue in list(accounting_verifier.issues or [])]),
    })
    if not bool(getattr(accounting, 'accepted_accounting_ready', False)) or not accounting_verifier.passed:
        workspace = _workspace_with_verifier_issues(workspace, accounting_verifier)
        return None, workspace, {
            'status': 'rejected',
            'reason': 'accepted_accounting_not_ready',
            **_mapping_draft_observation(workspace),
            'finish_gate': _finish_gate_observation(workspace),
            'executable_menu_summary': _executable_menu_observation(workspace),
            'recommended_next_observation': 'accepted finish is not available while open_rows remain; propose_mapping_intents for those rows or execute target-side evidence first',
        }
    expanded, expand_issues = expand_mapping_draft(dossier, draft)
    if expand_issues:
        verifier_result = CaseVerifierResult(passed=False, issues=expand_issues, summary='mapping draft expansion failed')
        workspace = _workspace_with_verifier_issues(workspace, verifier_result)
        return None, workspace, {
            'status': 'rejected',
            'reason': 'mapping_draft_expansion_failed',
            'patch_issue_codes': _dedupe_preserve_order([str(getattr(issue, 'issue_code', '') or '') for issue in expand_issues]),
        }
    accepted_findings = [Finding(ref='F_MAP1', finding_kind='pass', description='mapping draft accounting accepted')]
    accepted_output = CaseJudgeOutput(
        action='submit_verdict',
        findings=accepted_findings,
        assignment_intents=_with_mapping_draft_support_findings(
            _compact_final_assignment_support_refs(expanded),
            CaseJudgeOutput(action='submit_verdict', findings=accepted_findings),
        ),
        self_checks=[],
        summary='accepted from OrchestratorAgent mapping draft',
    )
    verifier_result = verify_judge_output(dossier, accepted_output)
    if not verifier_result.passed:
        workspace = _workspace_with_verifier_issues(workspace, verifier_result)
        return None, workspace, {
            'status': 'rejected',
            'reason': 'accepted_verifier_rejected',
            'verifier_issue_codes': _dedupe_preserve_order([str(getattr(issue, 'issue_code', '') or '') for issue in list(verifier_result.issues or [])]),
        }
    result = CaseAgentRunResult(
        True,
        workspace.header.case_id,
        'accepted',
        'submit_verdict',
        accepted_output,
        verifier_result,
        workspace,
        judge_outputs,
        evidence_batches,
        'accepted_from_mapping_draft',
        [],
    )
    result.planning_output = planning_output
    return result, workspace, {
        'status': 'accepted_verified',
        'assignment_count': len(expanded),
        **_mapping_draft_observation(workspace),
    }


def _fail_closed_reasons_from_workspace(workspace: CaseEvidenceWorkspace, *, finish_kind: str, reason: str) -> list[FailClosedReason]:
    draft = getattr(workspace, 'mapping_draft', None)
    dossier = workspace.to_dossier(round_context=f'finish_case_{finish_kind}')
    reasons = _mapping_draft_unresolved_fail_closed_reasons(draft, dossier) if draft is not None else []
    if reasons:
        return reasons
    accounting = compute_mapping_draft_accounting(draft, dossier) if draft is not None else None
    details = [finish_kind, reason]
    if accounting is not None:
        details.extend([
            f'unresolved_count={int(getattr(accounting, "unresolved_count", 0) or 0)}',
            f'needs_more_evidence_file_count={int(getattr(accounting, "needs_more_evidence_file_count", 0) or 0)}',
            f'unaligned_file_count={int(getattr(accounting, "unaligned_file_count", 0) or 0)}',
        ])
    return [FailClosedReason(ref='FR1', reason_kind='insufficient_evidence', description='; '.join(part for part in details if part), related_refs=[])]


def _build_orchestrator_fail_closed_result(
    workspace: CaseEvidenceWorkspace,
    judge_outputs: list[CaseJudgeOutput],
    evidence_batches: list[EvidenceBatchResult],
    *,
    planning_output: CasePlanningOutput | None,
    finish_kind: str,
    reason: str,
    allow_tool_loop: bool = False,
) -> tuple[CaseAgentRunResult | None, CaseEvidenceWorkspace, dict[str, object]]:
    budget_exhausted = bool(
        getattr(workspace.budget, 'max_evidence_batches', 0)
        and workspace.budget.used_evidence_batches >= workspace.budget.max_evidence_batches
    )
    no_new_audit = _no_new_evidence_precondition_audit(workspace)
    draft_accounting_for_finish = _mapping_draft_observation(workspace).get('draft_accounting')
    unresolved_for_finish = int((draft_accounting_for_finish or {}).get('unresolved_count') or 0) if isinstance(draft_accounting_for_finish, dict) else 0
    if finish_kind == 'budget_exhausted' and not budget_exhausted:
        return None, workspace, {
            'status': 'rejected',
            'reason': 'budget_not_exhausted',
            **no_new_audit,
            **_mapping_draft_observation(workspace),
            'finish_gate': _finish_gate_observation(workspace),
            'executable_menu_summary': _executable_menu_observation(workspace),
            'recommended_next_observation': 'budget is not exhausted; continue evidence or propose_mapping_intents for open_rows',
        }
    if (
        finish_kind == 'budget_exhausted'
        and int(no_new_audit.get('semantic_decision_call_count_after_latest_evidence') or 0) == 0
        and unresolved_for_finish > 0
    ):
        return None, workspace, {
            'status': 'rejected',
            'reason': 'budget_exhausted_requires_mapping_intent_after_latest_evidence',
            **no_new_audit,
            **_mapping_draft_observation(workspace),
            'finish_gate': _finish_gate_observation(workspace),
            'executable_menu_summary': _executable_menu_observation(workspace),
            'recommended_next_observation': 'after the latest evidence, propose semantic mapping intents for open_rows before claiming budget_exhausted',
        }
    if finish_kind == 'tool_loop_blocked':
        if not allow_tool_loop:
            return None, workspace, {
                'status': 'rejected',
                'reason': 'tool_loop_not_blocked',
                **no_new_audit,
                **_mapping_draft_observation(workspace),
                'finish_gate': _finish_gate_observation(workspace),
                'executable_menu_summary': _executable_menu_observation(workspace),
                'recommended_next_observation': 'tool_loop_blocked is only a fixed-layer emergency; continue with evidence or mapping intents',
            }
        turn_limit_forced = str(reason or '').startswith('orchestrator turn limit reached')
        if not turn_limit_forced and (
            int(no_new_audit.get('remaining_target_side_executable_request_count') or 0) > 0
            or int(no_new_audit.get('durable_draft_evidence_intent_count') or 0) > 0
            or int(no_new_audit.get('human_next_action_blocked_no_new_evidence_count') or 0) > 0
        ):
            return None, workspace, {
                'status': 'rejected',
                'reason': 'tool_loop_blocked_preconditions_not_met',
                **no_new_audit,
                **_mapping_draft_observation(workspace),
                'finish_gate': _finish_gate_observation(workspace),
                'executable_menu_summary': _executable_menu_observation(workspace),
                'recommended_next_observation': 'the case is not blocked: execute remaining_target_side_executable_request_ids or resolve open_rows with propose_mapping_intents',
            }
    if finish_kind == 'semantic_target_conflict':
        if (
            int(no_new_audit.get('semantic_decision_call_count_after_latest_evidence') or 0) == 0
            or int(no_new_audit.get('remaining_target_side_executable_request_count') or 0) > 0
            or int(no_new_audit.get('durable_draft_evidence_intent_count') or 0) > 0
            or int(no_new_audit.get('human_next_action_blocked_no_new_evidence_count') or 0) > 0
        ):
            return None, workspace, {
                'status': 'rejected',
                'reason': 'semantic_target_conflict_preconditions_not_met',
                **no_new_audit,
                **_mapping_draft_observation(workspace),
                'finish_gate': _finish_gate_observation(workspace),
                'executable_menu_summary': _executable_menu_observation(workspace),
                'recommended_next_observation': 'semantic conflict finish needs a latest semantic decision and no executable target-side evidence; continue investigation for open_rows',
            }
        fail_output = CaseJudgeOutput(
            action='fail_closed',
            fail_closed_reasons=[
                FailClosedReason(
                    ref='FR1',
                    reason_kind='contradiction',
                    description=reason or 'OrchestratorAgent identified an unresolved semantic target conflict',
                    related_refs=[],
                )
            ],
            summary='semantic target conflict from OrchestratorAgent',
        )
        verifier_result = verify_judge_output(workspace.to_dossier(round_context='finish_case_semantic_target_conflict'), fail_output)
        workspace = _workspace_with_judge_audit(workspace, {
            'note': 'finish_case_semantic_target_conflict_verified',
            'finish_kind': finish_kind,
            'reason': reason,
            'verifier_passed': bool(verifier_result.passed),
        })
        if not verifier_result.passed:
            workspace = _workspace_with_verifier_issues(workspace, verifier_result)
            return None, workspace, {
                'status': 'rejected',
                'reason': 'semantic_target_conflict_verifier_rejected',
                'verifier_issue_codes': _dedupe_preserve_order([str(getattr(issue, 'issue_code', '') or '') for issue in list(verifier_result.issues or [])]),
            }
        result = CaseAgentRunResult(
            True,
            workspace.header.case_id,
            'fail_closed',
            'fail_closed',
            fail_output,
            verifier_result,
            workspace,
            judge_outputs,
            evidence_batches,
            'semantic_target_conflict',
            ['semantic_target_conflict'],
        )
        result.planning_output = planning_output
        return result, workspace, {'status': 'fail_closed_verified', 'finish_kind': finish_kind}
    if finish_kind == 'no_new_evidence' and not bool(no_new_audit.get('no_new_evidence_preconditions_ok')):
        return None, workspace, {
            'status': 'rejected',
            'reason': 'no_new_evidence_preconditions_not_met',
            **no_new_audit,
            **_mapping_draft_observation(workspace),
            'finish_gate': _finish_gate_observation(workspace),
            'executable_menu_summary': _executable_menu_observation(workspace),
            'recommended_next_observation': 'no_new_evidence is not true yet; execute remaining target-side evidence or propose intents for open_rows',
        }
    summary = 'budget_exhausted' if finish_kind == 'budget_exhausted' else ('tool_loop_blocked' if finish_kind == 'tool_loop_blocked' else 'no_new_evidence')
    fail_output = CaseJudgeOutput(
        action='fail_closed',
        fail_closed_reasons=_fail_closed_reasons_from_workspace(workspace, finish_kind=finish_kind, reason=reason),
        summary=f'{summary} before accepted mapping',
    )
    verifier_result = verify_judge_output(workspace.to_dossier(round_context=f'finish_case_{finish_kind}'), fail_output)
    workspace = _workspace_with_judge_audit(workspace, {
        'note': 'finish_case_fail_closed_verified',
        'finish_kind': finish_kind,
        'summary': summary,
        'verifier_passed': bool(verifier_result.passed),
        **no_new_audit,
    })
    result = CaseAgentRunResult(
        True,
        workspace.header.case_id,
        'fail_closed',
        'fail_closed',
        fail_output,
        verifier_result,
        workspace,
        judge_outputs,
        evidence_batches,
        summary,
        [summary],
    )
    result.planning_output = planning_output
    return result, workspace, {'status': 'fail_closed_verified', 'finish_kind': finish_kind, 'summary': summary}


def _run_orchestrator_agent_main_loop(
    workspace: CaseEvidenceWorkspace,
    ai_client,
    bangumi_client,
    *,
    planning_output: CasePlanningOutput | None,
    planning_evidence_batches: list[EvidenceBatchResult],
    max_rounds: int | None,
    orchestrator_context_soft_token_limit: int | None,
    orchestrator_context_hard_token_limit: int | None,
) -> CaseAgentRunResult:
    evidence_batches: list[EvidenceBatchResult] = list(planning_evidence_batches or [])
    judge_outputs: list[CaseJudgeOutput] = []
    call_fn = getattr(ai_client, 'call_responses_tool_agent', None)
    if not callable(call_fn):
        workspace = _workspace_with_judge_audit(workspace, {
            'note': 'orchestrator_agent_transport_unavailable',
            'reason': 'Local to Bangumi primary path requires OrchestratorAgent tool transport',
        })
        return _orchestrator_error_result(
            workspace,
            summary='orchestrator agent transport unavailable',
            error_kind='orchestrator_agent_unavailable',
            planning_output=planning_output,
            evidence_batches=evidence_batches,
        )
    orchestrator_session = OrchestratorAgentSession(case_id=workspace.header.case_id)
    soft_limit = max(8192, int(orchestrator_context_soft_token_limit or 180000))
    hard_limit = max(soft_limit + 1024, int(orchestrator_context_hard_token_limit or 300000))
    max_turns = max(1, int(max_rounds or workspace.budget.max_judge_rounds or 12))
    tool_rejection_limit = 12
    consecutive_tool_rejections = 0
    for _turn in range(max_turns):
        workspace = _prepare_workspace_for_orchestrator_agent_turn(workspace)
        agent_result = call_orchestrator_agent(
            ai_client,
            workspace,
            orchestrator_session,
            reason='select next Local to Bangumi investigation tool',
            soft_token_limit=soft_limit,
            hard_token_limit=hard_limit,
        )
        orchestrator_session = agent_result.session
        workspace = _workspace_with_judge_audit(workspace, agent_result.audit)
        if not agent_result.ok or agent_result.tool_call is None:
            consecutive_tool_rejections += 1
            orchestrator_session = replace(
                orchestrator_session,
                tool_rejection_count=orchestrator_session.tool_rejection_count + 1,
            )
            if agent_result.tool_call is not None:
                orchestrator_session = record_orchestrator_tool_output(
                    orchestrator_session,
                    agent_result.tool_call,
                    {'status': 'rejected', 'reason': agent_result.error or 'orchestrator_agent_tool_parse_failed'},
                )
            if consecutive_tool_rejections >= tool_rejection_limit:
                result, workspace, _observation = _build_orchestrator_fail_closed_result(
                    workspace,
                    judge_outputs,
                    evidence_batches,
                    planning_output=planning_output,
                    finish_kind='tool_loop_blocked',
                    reason=agent_result.error or 'orchestrator agent repeatedly failed to produce a valid tool call',
                    allow_tool_loop=True,
                )
                if result is not None:
                    return _finalize_orchestrator_result(result, workspace, orchestrator_session)
                error_result = _orchestrator_error_result(
                    workspace,
                    summary=agent_result.error or 'orchestrator agent failed to produce a valid tool call',
                    error_kind='orchestrator_agent_tool_call_failed',
                    planning_output=planning_output,
                    evidence_batches=evidence_batches,
                )
                return _finalize_orchestrator_result(error_result, workspace, orchestrator_session)
            continue
        tool_call = agent_result.tool_call
        workspace, decision, tool_acceptance = _decision_from_orchestrator_tool_call(workspace, tool_call)
        workspace = _workspace_with_judge_audit(workspace, {
            'note': 'orchestrator_tool_selected',
            'tool_name': tool_call.tool_name,
            'tool_call_id': tool_call.call_id,
            'accepted': bool(tool_acceptance.get('accepted')),
            **tool_acceptance,
        })
        if decision is None:
            consecutive_tool_rejections += 1
            orchestrator_session = replace(
                orchestrator_session,
                tool_rejection_count=orchestrator_session.tool_rejection_count + 1,
            )
            orchestrator_session = record_orchestrator_tool_output(
                orchestrator_session,
                tool_call,
                {'status': 'rejected', **tool_acceptance},
            )
            if consecutive_tool_rejections >= tool_rejection_limit:
                result, workspace, _observation = _build_orchestrator_fail_closed_result(
                    workspace,
                    judge_outputs,
                    evidence_batches,
                    planning_output=planning_output,
                    finish_kind='tool_loop_blocked',
                    reason='too many rejected OrchestratorAgent tool calls',
                    allow_tool_loop=True,
                )
                if result is not None:
                    return _finalize_orchestrator_result(result, workspace, orchestrator_session)
            continue
        result: CaseAgentRunResult | None = None
        observation: dict[str, object]
        if decision.action == 'propose_case_understanding':
            args = tool_call.arguments if isinstance(tool_call.arguments, ProposeCaseUnderstandingToolArgs) else ProposeCaseUnderstandingToolArgs()
            workspace, observation = _compile_case_understanding(workspace, args)
        elif decision.action == 'compose_queries':
            args = tool_call.arguments if isinstance(tool_call.arguments, MaterializeQueriesToolArgs) else MaterializeQueriesToolArgs()
            workspace, observation = _run_orchestrator_materialize_queries_tool(workspace, args)
        elif decision.action == 'execute_evidence':
            if decision.planner_output is None:
                observation = {'status': 'rejected', 'reason': 'missing_evidence_plan'}
            else:
                workspace, observation = _run_orchestrator_execute_evidence_tool(workspace, decision.planner_output, bangumi_client, evidence_batches)
        elif decision.action == 'propose_mapping_intents':
            args = tool_call.arguments if isinstance(tool_call.arguments, ProposeMappingIntentsToolArgs) else ProposeMappingIntentsToolArgs()
            workspace, observation = _run_orchestrator_propose_mapping_intents_tool(workspace, args)
        elif decision.action == 'update_notebook':
            args = tool_call.arguments if isinstance(tool_call.arguments, UpdateNotebookToolArgs) else UpdateNotebookToolArgs()
            workspace, observation = _run_orchestrator_update_notebook_tool(workspace, args)
        elif decision.action == 'reconsider_split':
            args = tool_call.arguments if isinstance(tool_call.arguments, ReconsiderSplitToolArgs) else ReconsiderSplitToolArgs()
            workspace, observation = _run_orchestrator_reconsider_split_tool(workspace, args)
        elif decision.action == 'accepted':
            result, workspace, observation = _build_mapping_draft_accepted_result(
                workspace,
                judge_outputs,
                evidence_batches,
                planning_output=planning_output,
            )
        elif decision.action == 'fail_closed':
            args = tool_call.arguments if isinstance(tool_call.arguments, FinishCaseToolArgs) else None
            finish_kind = str(getattr(args, 'finish_kind', '') or 'no_new_evidence')
            result, workspace, observation = _build_orchestrator_fail_closed_result(
                workspace,
                judge_outputs,
                evidence_batches,
                planning_output=planning_output,
                finish_kind=finish_kind,
                reason=decision.reason,
                allow_tool_loop=consecutive_tool_rejections >= tool_rejection_limit - 1,
            )
        else:
            observation = {'status': 'rejected', 'reason': f'unsupported_decision:{decision.action}'}
        if result is not None:
            orchestrator_session = record_orchestrator_tool_output(
                orchestrator_session,
                tool_call,
                {'status': 'terminal', 'terminal_status': result.status, **(observation or {})},
            )
            return _finalize_orchestrator_result(result, workspace, orchestrator_session)
        if str(observation.get('status') or '') in {'rejected', 'error'}:
            workspace = _workspace_with_judge_audit(workspace, {
                'note': 'orchestrator_tool_output_rejected',
                'tool_name': tool_call.tool_name,
                'reason': str(observation.get('reason') or ''),
                'recommended_next_observation': str(observation.get('recommended_next_observation') or ''),
                'verifier_issue_codes': observation.get('verifier_issue_codes') if isinstance(observation.get('verifier_issue_codes'), list) else [],
                'patch_issue_codes': observation.get('patch_issue_codes') if isinstance(observation.get('patch_issue_codes'), list) else [],
                'accounting_issue_codes': observation.get('accounting_issue_codes') if isinstance(observation.get('accounting_issue_codes'), list) else [],
                'finish_gate': observation.get('finish_gate') if isinstance(observation.get('finish_gate'), dict) else {},
                'open_rows': observation.get('open_rows') if isinstance(observation.get('open_rows'), list) else [],
                'executable_menu_summary': observation.get('executable_menu_summary') if isinstance(observation.get('executable_menu_summary'), dict) else {},
            })
            consecutive_tool_rejections += 1
            orchestrator_session = replace(
                orchestrator_session,
                tool_rejection_count=orchestrator_session.tool_rejection_count + 1,
            )
        else:
            consecutive_tool_rejections = 0
        orchestrator_session = record_orchestrator_tool_output(orchestrator_session, tool_call, observation)
        if consecutive_tool_rejections >= tool_rejection_limit:
            result, workspace, _observation = _build_orchestrator_fail_closed_result(
                workspace,
                judge_outputs,
                evidence_batches,
                planning_output=planning_output,
                finish_kind='tool_loop_blocked',
                reason='too many rejected OrchestratorAgent tool outputs',
                allow_tool_loop=True,
            )
            if result is not None:
                return _finalize_orchestrator_result(result, workspace, orchestrator_session)
            error_result = _orchestrator_error_result(
                workspace,
                summary='too many rejected OrchestratorAgent tool outputs',
                error_kind='orchestrator_agent_tool_loop_blocked',
                planning_output=planning_output,
                evidence_batches=evidence_batches,
            )
            return _finalize_orchestrator_result(error_result, workspace, orchestrator_session)
    result, workspace, _observation = _build_orchestrator_fail_closed_result(
        workspace,
        judge_outputs,
        evidence_batches,
        planning_output=planning_output,
        finish_kind='tool_loop_blocked',
        reason=f'orchestrator turn limit reached: {max_turns}',
        allow_tool_loop=True,
    )
    if result is not None:
        return _finalize_orchestrator_result(result, workspace, orchestrator_session)
    error_result = _orchestrator_error_result(
        workspace,
        summary=f'orchestrator turn limit reached: {max_turns}',
        error_kind='orchestrator_agent_turn_limit',
        planning_output=planning_output,
        evidence_batches=evidence_batches,
    )
    return _finalize_orchestrator_result(error_result, workspace, orchestrator_session)


def _prepare_workspace_for_orchestrator_agent_turn(workspace: CaseEvidenceWorkspace) -> CaseEvidenceWorkspace:
    if not _case_understanding_applied(workspace):
        return workspace
    finish_gate = _finish_gate_observation(workspace)
    if bool(finish_gate.get('accepted_finish_allowed')):
        return workspace
    return _refresh_mapping_draft_candidates(_workspace_with_initial_mapping_draft(workspace))


def _run_case_planning_phase(
    workspace: CaseEvidenceWorkspace,
    ai_client,
    bangumi_client,
    *,
    max_rounds: int | None,
    orchestrator_context_soft_token_limit: int | None,
    orchestrator_context_hard_token_limit: int | None,
    planning_depth: int,
) -> _PlanningPhaseResult:
    dossier = workspace.to_dossier(round_context='case_planning')
    planner_result = call_case_planner(ai_client, dossier)
    workspace = _workspace_with_judge_audit(workspace, getattr(planner_result, 'request_audit', None))
    if not planner_result.ok or planner_result.output is None:
        error_text = planner_result.error or 'case planner call failed'
        lower = error_text.casefold()
        error_kind = 'context_overflow' if 'exceeds the context window' in lower else ('provider_no_response' if 'no response' in lower else 'provider_error')
        summary = 'planner context overflow' if error_kind == 'context_overflow' else ('planner infra no response' if error_kind == 'provider_no_response' else 'planner infra error')
        return _PlanningPhaseResult(
            workspace=workspace,
            terminal_result=CaseAgentRunResult(
                ok=False,
                case_id=workspace.header.case_id,
                status='error',
                final_action='case_planning',
                final_output=None,
                final_verifier_result=None,
                final_workspace=workspace,
                summary=summary,
                errors=[error_text, f'error_kind={error_kind}'],
            ),
        )

    output = planner_result.output
    verifier_result = verify_case_planning_output(dossier, output)
    workspace = _workspace_with_judge_audit(workspace, {
        'planning_round_kind': 'case_planning',
        'note': 'case_planning_verifier',
        'action_actual': output.action,
        'verifier_passed': verifier_result.passed,
        'verifier_issue_count': len(verifier_result.issues),
        'verifier_issue_codes': [issue.issue_code for issue in verifier_result.issues],
    })
    if not verifier_result.passed:
        if output.action == 'request_evidence':
            deferred_output = output.model_copy(update={
                'action': 'process_as_one_case',
                'evidence_requests': [],
                'evidence_menu_request_ids': [],
                'summary': output.summary or 'invalid planner evidence request deferred to investigation loop',
            })
            workspace = _workspace_with_judge_audit(workspace, {
                'planning_round_kind': 'case_planning',
                'note': 'case_planning_invalid_evidence_request_deferred_to_investigation_loop',
                'verifier_issue_count': len(verifier_result.issues),
                'verifier_issue_codes': [issue.issue_code for issue in verifier_result.issues],
                'original_evidence_request_count': len(list(output.evidence_requests or [])),
                'original_evidence_menu_request_count': len(list(output.evidence_menu_request_ids or [])),
            })
            return _PlanningPhaseResult(workspace=workspace, planning_output=deferred_output)
        if output.action == 'split_into_cases':
            deferred_output = output.model_copy(update={
                'action': 'process_as_one_case',
                'split_cases': [],
                'summary': output.summary or 'invalid split deferred to investigation loop',
            })
            workspace = _workspace_with_judge_audit(workspace, {
                'planning_round_kind': 'case_planning',
                'note': 'case_planning_invalid_split_deferred_to_investigation_loop',
                'verifier_issue_count': len(verifier_result.issues),
                'verifier_issue_codes': [issue.issue_code for issue in verifier_result.issues],
                'original_split_case_count': len(list(output.split_cases or [])),
            })
            return _PlanningPhaseResult(workspace=workspace, planning_output=deferred_output)
        return _PlanningPhaseResult(
            workspace=workspace,
            planning_output=output,
            terminal_result=CaseAgentRunResult(
                ok=False,
                case_id=workspace.header.case_id,
                status='invalid',
                final_action='case_planning',
                final_output=None,
                final_verifier_result=verifier_result,
                final_workspace=workspace,
                summary='case planning rejected',
                errors=['case_planning_rejected'],
                planning_output=output,
            ),
        )

    if output.action == 'process_as_one_case':
        return _PlanningPhaseResult(workspace=workspace, planning_output=output)

    if output.action == 'fail_closed':
        if _should_defer_planner_fail_closed_to_investigation_loop(workspace):
            deferred_output = output.model_copy(update={
                'action': 'process_as_one_case',
                'fail_closed_reasons': [],
                'summary': output.summary or 'planner fail_closed deferred to query composition investigation',
            })
            workspace = _workspace_with_judge_audit(workspace, {
                'planning_round_kind': 'case_planning',
                'note': 'case_planning_fail_closed_deferred_to_investigation_loop',
                'reason': 'no_bangumi_surface_but_subject_search_budget_available',
                'original_fail_closed_reason_count': len(list(output.fail_closed_reasons or [])),
            })
            return _PlanningPhaseResult(workspace=workspace, planning_output=deferred_output)
        fail_output = CaseJudgeOutput(
            action='fail_closed',
            fail_closed_reasons=list(output.fail_closed_reasons or []),
            summary=output.summary or 'case planner failed closed',
        )
        fail_verifier = verify_judge_output(dossier, fail_output)
        return _PlanningPhaseResult(
            workspace=workspace,
            planning_output=output,
            terminal_result=CaseAgentRunResult(
                ok=True,
                case_id=workspace.header.case_id,
                status='fail_closed',
                final_action='fail_closed',
                final_output=fail_output,
                final_verifier_result=fail_verifier,
                final_workspace=workspace,
                summary=output.summary or 'case planner failed closed',
                errors=[],
                planning_output=output,
            ),
        )

    if output.action == 'request_evidence':
        broker = EvidenceBroker(bangumi_client)
        evidence_workspace, evidence_batches, terminal = _execute_planner_evidence_request(workspace, broker, output)
        if terminal is not None:
            terminal.planning_output = output
            return _PlanningPhaseResult(workspace=evidence_workspace, evidence_batches=evidence_batches, terminal_result=terminal, planning_output=output)
        return _PlanningPhaseResult(workspace=evidence_workspace, evidence_batches=evidence_batches, planning_output=output)

    if output.action == 'split_into_cases':
        if _should_defer_split_to_investigation_loop(workspace):
            deferred_output = output.model_copy(update={
                'action': 'process_as_one_case',
                'split_cases': [],
                'summary': output.summary or 'split deferred to investigation loop',
            })
            workspace = _workspace_with_judge_audit(workspace, {
                'planning_round_kind': 'case_planning',
                'note': 'case_planning_split_deferred_to_investigation_loop',
                'main_file_count': len(list(getattr(workspace.contract, 'main_file_refs', []) or [])),
                'local_child_span_count': len([
                    card for card in list(getattr(workspace, 'local_span_cards', []) or [])
                    if str(getattr(card, 'span_scope', '') or '') != 'package'
                ]),
                'original_split_case_count': len(list(output.split_cases or [])),
            })
            return _PlanningPhaseResult(workspace=workspace, planning_output=deferred_output)
        split_result = _run_split_child_cases(
            workspace,
            output,
            ai_client,
            bangumi_client,
            max_rounds=max_rounds,
            orchestrator_context_soft_token_limit=orchestrator_context_soft_token_limit,
            orchestrator_context_hard_token_limit=orchestrator_context_hard_token_limit,
            planning_depth=planning_depth,
        )
        return _PlanningPhaseResult(workspace=workspace, planning_output=output, terminal_result=split_result)

    return _PlanningPhaseResult(
        workspace=workspace,
        planning_output=output,
        terminal_result=CaseAgentRunResult(
            ok=False,
            case_id=workspace.header.case_id,
            status='invalid',
            final_action='case_planning',
            final_output=None,
            final_verifier_result=verifier_result,
            final_workspace=workspace,
            summary=f'unsupported case planning action: {output.action}',
            errors=[f'unsupported_case_planning_action:{output.action}'],
            planning_output=output,
        ),
    )


def _execute_planner_evidence_request(
    workspace: CaseEvidenceWorkspace,
    broker: EvidenceBroker,
    output: CasePlanningOutput,
) -> tuple[CaseEvidenceWorkspace, list[EvidenceBatchResult], CaseAgentRunResult | None]:
    resolved_requests: list[EvidenceRequest] = []
    selected_menu_request_ids: list[str] = []
    unknown_menu_request_ids: list[str] = []
    resolved_menu_request_count = 0
    if output.evidence_menu_request_ids:
        resolved_requests, selected_menu_request_ids, unknown_menu_request_ids, resolved_menu_request_count = resolve_evidence_menu_requests(workspace, list(output.evidence_menu_request_ids))
    merged_requests = [*resolved_requests, *list(output.evidence_requests or [])]
    workspace = _workspace_with_judge_audit(workspace, {
        'planning_round_kind': 'case_planning',
        'note': 'case_planning_evidence_menu_resolution',
        'selected_menu_request_ids': selected_menu_request_ids,
        'unknown_menu_request_ids': unknown_menu_request_ids,
        'resolved_menu_request_count': resolved_menu_request_count,
        'raw_evidence_request_count': len(output.evidence_requests or []),
    })
    if unknown_menu_request_ids:
        workspace = _workspace_with_judge_audit(workspace, {
            'planning_round_kind': 'case_planning',
            'note': 'case_planning_unknown_menu_request_deferred_to_investigation_loop',
            'unknown_menu_request_ids': unknown_menu_request_ids,
            'selected_menu_request_ids': selected_menu_request_ids,
            'resolved_menu_request_count': resolved_menu_request_count,
            'raw_evidence_request_count': len(output.evidence_requests or []),
            'reason': 'case planner selected stale or non-menu ids; OrchestratorAgent must recover through visible tools',
        })
        if not merged_requests:
            return workspace, [], None
    if not merged_requests:
        return workspace, [], CaseAgentRunResult(
            ok=False,
            case_id=workspace.header.case_id,
            status='invalid',
            final_action='case_planning',
            final_output=None,
            final_verifier_result=None,
            final_workspace=workspace,
            summary='case planning request_evidence produced no executable requests',
            errors=['case_planning_empty_evidence_request'],
            planning_output=output,
        )

    normalized_requests, normalization_audits = normalize_evidence_requests(workspace, merged_requests)
    workspace = _workspace_with_request_normalization_audits(workspace, normalization_audits)
    new_workspace, batch_result = broker.execute_batch(workspace, normalized_requests)
    new_workspace = _workspace_with_judge_audit(new_workspace, {
        'planning_round_kind': 'case_planning',
        'note': 'case_planning_evidence_batch',
        'evidence_batch_status': batch_result.status,
        'evidence_request_count': len(batch_result.request_results or []),
    })
    new_workspace = new_workspace.with_seen_detail_refs([ref for rr in batch_result.request_results for ref in (getattr(rr, 'response_refs', []) or [])])
    usable_response_ref_count = sum(len(getattr(rr, 'response_refs', []) or []) for rr in (batch_result.request_results or []) if getattr(rr, 'accepted', False))
    rejection_notes = ' '.join(' '.join(getattr(rr, 'notes', []) or []) for rr in (batch_result.request_results or []) if not getattr(rr, 'accepted', False)).casefold()
    invalid_contract = any(marker in rejection_notes for marker in ('invalid anchor', 'invalid subject', 'invalid item', 'invalid query', 'unknown request_type'))
    no_usable_evidence = any(marker in rejection_notes for marker in ('no matching local files', 'no matching target window', 'no matching targets', 'no matching span', 'package_span_requires_child_span_requests', 'target_window too wide', 'no usable evidence'))
    if batch_result.status == 'rejected' or (batch_result.status == 'partial' and usable_response_ref_count == 0):
        if invalid_contract:
            return new_workspace, [batch_result], CaseAgentRunResult(
                ok=False,
                case_id=new_workspace.header.case_id,
                status='invalid',
                final_action='case_planning',
                final_output=None,
                final_verifier_result=None,
                final_workspace=new_workspace,
                evidence_batches=[batch_result],
                summary='case planning evidence request invalid',
                errors=['case_planning_evidence_request_invalid'],
                planning_output=output,
            )
        if no_usable_evidence:
            audited = _workspace_with_judge_audit(new_workspace, {
                'planning_round_kind': 'case_planning',
                'note': 'case_planning_no_usable_evidence_deferred_to_investigation_loop',
                'reason': 'planner evidence request was not enough to close the case; continue through mapping draft/editor loop',
                'evidence_batch_status': batch_result.status,
                'usable_response_ref_count': usable_response_ref_count,
            })
            return audited, [batch_result], None
        audited = _workspace_with_judge_audit(new_workspace, {
            'planning_round_kind': 'case_planning',
            'note': 'case_planning_rejected_evidence_deferred_to_investigation_loop',
            'reason': 'planner evidence request was rejected without a contract error; continue through mapping draft/editor loop',
            'evidence_batch_status': batch_result.status,
            'usable_response_ref_count': usable_response_ref_count,
        })
        return audited, [batch_result], None
    return new_workspace, [batch_result], None


def _should_defer_split_to_investigation_loop(workspace: CaseEvidenceWorkspace) -> bool:
    main_refs = list(dict.fromkeys(list(getattr(workspace.contract, 'main_file_refs', []) or [])))
    if len(main_refs) < 20:
        return False
    main_ref_set = set(main_refs)
    child_spans = [
        card for card in list(getattr(workspace, 'local_span_cards', []) or [])
        if str(getattr(card, 'span_scope', '') or '') != 'package'
    ]
    if len(child_spans) < 2:
        return False
    covered: list[str] = []
    for span in child_spans:
        covered.extend([ref for ref in list(getattr(span, 'file_refs', []) or []) if ref in main_ref_set])
    if set(covered) != main_ref_set:
        return False
    return len(covered) == len(set(covered))


def _should_defer_planner_fail_closed_to_investigation_loop(workspace: CaseEvidenceWorkspace) -> bool:
    has_target_surface = bool(
        list(getattr(workspace, 'bangumi_subjects', []) or [])
        or list(getattr(workspace, 'bangumi_items', []) or [])
        or list(getattr(workspace, 'bangumi_span_cards', []) or [])
    )
    if has_target_surface:
        return False
    budget = getattr(workspace, 'budget', None)
    if budget is None:
        return False
    can_use_batch = int(getattr(budget, 'max_evidence_batches', 0) or 0) == 0 or int(getattr(budget, 'used_evidence_batches', 0) or 0) < int(getattr(budget, 'max_evidence_batches', 0) or 0)
    can_use_api = int(getattr(budget, 'max_api_calls_per_case', 0) or 0) == 0 or int(getattr(budget, 'used_api_calls', 0) or 0) < int(getattr(budget, 'max_api_calls_per_case', 0) or 0)
    can_search = int(getattr(budget, 'max_subject_searches', 0) or 0) == 0 or int(getattr(budget, 'used_subject_searches', 0) or 0) < int(getattr(budget, 'max_subject_searches', 0) or 0)
    return bool(can_use_batch and can_use_api and can_search)


def _run_split_child_cases(
    workspace: CaseEvidenceWorkspace,
    output: CasePlanningOutput,
    ai_client,
    bangumi_client,
    *,
    max_rounds: int | None,
    orchestrator_context_soft_token_limit: int | None,
    orchestrator_context_hard_token_limit: int | None,
    planning_depth: int,
) -> CaseAgentRunResult:
    child_results: list[CaseAgentRunResult] = []
    for spec in output.split_cases:
        child_workspace = build_child_workspace(workspace, spec)
        child_result = run_local_bangumi_case_agent(
            child_workspace,
            ai_client,
            bangumi_client,
            max_rounds=max_rounds,
            orchestrator_context_soft_token_limit=orchestrator_context_soft_token_limit,
            orchestrator_context_hard_token_limit=orchestrator_context_hard_token_limit,
            _planning_depth=planning_depth + 1,
        )
        child_results.append(child_result)

    provider_error_children = [
        result for result in child_results
        if result.status == 'error'
        and any('error_kind=provider_no_response' in str(err) for err in list(result.errors or []))
    ]
    hard_error_children = [
        result for result in child_results
        if result.status == 'error' and result not in provider_error_children
    ]
    if hard_error_children:
        first = hard_error_children[0]
        return CaseAgentRunResult(
            ok=False,
            case_id=workspace.header.case_id,
            status='error',
            final_action='split_into_cases',
            final_output=first.final_output,
            final_verifier_result=first.final_verifier_result,
            final_workspace=workspace,
            evidence_batches=[batch for result in child_results for batch in result.evidence_batches],
            summary=first.summary or 'split child error',
            errors=[err for result in child_results for err in result.errors],
            planning_output=output,
            child_results=child_results,
        )

    hard_invalid_children = [
        result for result in child_results
        if result.status == 'invalid'
        and result.summary not in {'budget_exhausted', 'no_new_evidence'}
    ]
    if hard_invalid_children:
        first = hard_invalid_children[0]
        return CaseAgentRunResult(
            ok=False,
            case_id=workspace.header.case_id,
            status='invalid',
            final_action='split_into_cases',
            final_output=first.final_output,
            final_verifier_result=first.final_verifier_result,
            final_workspace=workspace,
            evidence_batches=[batch for result in child_results for batch in result.evidence_batches],
            summary=first.summary or 'split child invalid',
            errors=[err for result in child_results for err in result.errors],
            planning_output=output,
            child_results=child_results,
        )

    if any(result.status in {'fail_closed', 'invalid'} for result in child_results) or provider_error_children:
        reasons = []
        for index, child in enumerate(child_results, start=1):
            if child.status not in {'fail_closed', 'invalid', 'error'}:
                continue
            child_accounting = compute_mapping_draft_accounting(getattr(child.final_workspace, 'mapping_draft', None), child.final_workspace) if getattr(child.final_workspace, 'mapping_draft', None) is not None else None
            description_parts = [
                f'child_case_unresolved: child={child.case_id}',
                f'status={child.status}',
                f'summary={child.summary}',
            ]
            if child_accounting is not None:
                description_parts.extend([
                    f'unresolved_count={int(getattr(child_accounting, "unresolved_count", 0) or 0)}',
                    f'needs_more_evidence_file_count={int(getattr(child_accounting, "needs_more_evidence_file_count", 0) or 0)}',
                    f'unaligned_file_count={int(getattr(child_accounting, "unaligned_file_count", 0) or 0)}',
                ])
            child_briefing = getattr(getattr(child, 'final_workspace', None), 'case_briefing', None)
            child_notebook = getattr(getattr(child, 'final_workspace', None), 'investigation_notebook', None)
            if child_briefing is not None:
                description_parts.extend([
                    f'briefing={str(getattr(child_briefing, "summary", "") or getattr(child_briefing, "package_shape", "") or "")[:160]}',
                    f'work_unit_count={len(list(getattr(child_briefing, "work_units", []) or []))}',
                ])
            if child_notebook is not None:
                open_questions = [q for q in list(getattr(child_notebook, 'open_questions', []) or []) if str(getattr(q, 'status', '') or '') == 'open']
                unresolved_units = [u for u in list(getattr(child_notebook, 'work_unit_states', []) or []) if str(getattr(u, 'status', '') or '') in {'open', 'needs_evidence', 'blocked'}]
                description_parts.extend([
                    f'open_question_count={len(open_questions)}',
                    f'unresolved_work_unit_count={len(unresolved_units)}',
                ])
            child_error_kinds = [str(err) for err in list(child.errors or []) if str(err).startswith('error_kind=')]
            if child_error_kinds:
                description_parts.extend(child_error_kinds)
            reasons.append(FailClosedReason(ref=f'FR{index}', reason_kind='insufficient_evidence', description='; '.join(description_parts), related_refs=[]))
        fail_output = CaseJudgeOutput(action='fail_closed', fail_closed_reasons=reasons or [FailClosedReason(ref='FR1', reason_kind='insufficient_evidence', description='child_case_unresolved: one or more split child cases failed closed', related_refs=[])], summary='child_case_unresolved')
        verifier_result = verify_judge_output(workspace.to_dossier(round_context='case_planning'), fail_output)
        return CaseAgentRunResult(
            ok=True,
            case_id=workspace.header.case_id,
            status='fail_closed',
            final_action='fail_closed',
            final_output=fail_output,
            final_verifier_result=verifier_result,
            final_workspace=workspace,
            evidence_batches=[batch for result in child_results for batch in result.evidence_batches],
            summary='child_case_unresolved',
            errors=[err for result in child_results for err in result.errors],
            planning_output=output,
            child_results=child_results,
        )

    accepted_output = CaseJudgeOutput(action='submit_verdict', summary='accepted from split child cases')
    verifier_result = CaseVerifierResult(passed=True, issues=[], summary='split child cases accepted')
    return CaseAgentRunResult(
        ok=True,
        case_id=workspace.header.case_id,
        status='accepted',
        final_action='split_into_cases',
        final_output=accepted_output,
        final_verifier_result=verifier_result,
        final_workspace=workspace,
        evidence_batches=[batch for result in child_results for batch in result.evidence_batches],
        summary='accepted from split child cases',
        errors=[],
        planning_output=output,
        child_results=child_results,
    )


def _workspace_with_verifier_issues(workspace: CaseEvidenceWorkspace, verifier_result: CaseVerifierResult) -> CaseEvidenceWorkspace:
    header = workspace.header.model_copy(update={
        'round_index': workspace.header.round_index + 1,
        'issue_response_used': workspace.header.issue_response_used + 1,
    })
    return _workspace_preserving_state(
        workspace,
        header=header,
        budget=workspace.budget.model_copy(deep=True),
        verifier_issues=list(verifier_result.issues),
        diagnostics=[*workspace.diagnostics, 'issue_response_pending'],
    )


def _workspace_with_invalid_target_issue(workspace: CaseEvidenceWorkspace, verifier_result: CaseVerifierResult) -> CaseEvidenceWorkspace:
    audits = list(getattr(workspace, 'judge_request_audits', []) or [])
    issue_samples = []
    for issue in getattr(verifier_result, 'issues', []) or []:
        if str(getattr(issue, 'issue_code', '')).endswith('invalid_target') or 'invalid target' in str(getattr(issue, 'message', '')).casefold():
            issue_samples.append(getattr(issue, 'message', ''))
    audits.append({'invalid_target_issue': {'issue_samples': issue_samples, 'issue_count': len(issue_samples), 'reason': 'remove unassignable assignments; do not infer unseen BE refs'}})
    return _workspace_preserving_state(workspace, verifier_issues=list(verifier_result.issues), judge_request_audits=audits)


def _workspace_with_policy_retry_round(workspace: CaseEvidenceWorkspace) -> CaseEvidenceWorkspace:
    header = workspace.header.model_copy(update={'round_index': workspace.header.round_index + 1})
    return _workspace_preserving_state(
        workspace,
        header=header,
        budget=workspace.budget.model_copy(deep=True),
        diagnostics=[*workspace.diagnostics, 'policy_retry_pending'],
    )


def _workspace_with_diagnostics(workspace: CaseEvidenceWorkspace, dossier, verifier_result: CaseVerifierResult, output: CaseJudgeOutput, *, final_opportunity: bool) -> CaseEvidenceWorkspace:
    diagnostics = list(getattr(workspace, 'diagnostics', []) or [])
    ledger = build_surface_ledger(dossier)
    menu = _recommended_neutral_requests(dossier)
    policy = build_action_policy(has_evidence=bool(workspace.previous_evidence_results), can_request_more=bool(workspace.budget.max_evidence_batches == 0 or workspace.budget.used_evidence_batches < workspace.budget.max_evidence_batches), final_opportunity=final_opportunity, budget=workspace.budget.model_dump(mode='json'))
    surface_samples = list((ledger.get('catalog_visible') or {}).get('sample_refs') or [])
    diagnostics.extend([
        f"surface_ledger_count={ledger['summary']['catalog_visible_count']}",
        f"surface_ledger_sample={','.join(surface_samples[:3])}",
        f"evidence_menu_count={len(menu)}",
        f"evidence_menu_types={','.join(sorted({str(req.get('request_type') or '') for req in menu if str(req.get('request_type') or '')}))}",
        f"evidence_menu_sample={','.join(str(req.get('request_type') or '') for req in menu[:3])}",
        f"policy_allowed={','.join(policy.allowed_actions)}",
        f"policy_disallowed={','.join(policy.disallowed_actions)}",
        f"policy_final_opportunity={policy.final_opportunity}",
        f"notebook_rounds={build_notebook(dossier)['rounds']}",
        f"notebook_evidence_requests={build_notebook(dossier)['evidence_requests']}",
        f"issue_router_count={sum(route_verifier_issues(list(getattr(dossier, 'verifier_issues', []) or []))['counts'].values())}",
    ])
    return _workspace_preserving_state(workspace, diagnostics=diagnostics)


def _workspace_without_policy_retry_marker(workspace: CaseEvidenceWorkspace) -> CaseEvidenceWorkspace:
    diagnostics = [diag for diag in (getattr(workspace, 'diagnostics', []) or []) if diag != 'policy_retry_pending']
    if diagnostics == list(getattr(workspace, 'diagnostics', []) or []):
        return workspace
    return _workspace_preserving_state(workspace, diagnostics=diagnostics)


def _workspace_with_issue_round(workspace: CaseEvidenceWorkspace) -> CaseEvidenceWorkspace:
    header = workspace.header.model_copy(update={'round_index': workspace.header.round_index + 1})
    return _workspace_preserving_state(
        workspace,
        header=header,
        budget=workspace.budget.model_copy(deep=True),
        verifier_issues=[],
    )


def _verifier_gap_summary(verifier_result: CaseVerifierResult) -> str:
    issues = list(getattr(verifier_result, 'issues', []) or [])
    if not issues:
        return 'coverage_gap_unresolved'
    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue.issue_code] = counts.get(issue.issue_code, 0) + 1
    top_codes = ', '.join(f'{code}={count}' for code, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:3])
    return f'coverage_gap_unresolved: {len(issues)} verifier issues ({top_codes})'


def _finish_on_round_limit(workspace: CaseEvidenceWorkspace, final_action: str, final_output: CaseJudgeOutput | None, final_verifier_result: CaseVerifierResult | None, judge_outputs: list[CaseJudgeOutput], evidence_batches: list[EvidenceBatchResult], errors: list[str]) -> CaseAgentRunResult:
    if final_output is not None and final_action == 'fail_closed':
        verifier_result = verify_judge_output(workspace.to_dossier(round_context='round_limit_fail_closed'), final_output)
        return CaseAgentRunResult(True, workspace.header.case_id, 'fail_closed', 'fail_closed', final_output, verifier_result, workspace, judge_outputs, evidence_batches, 'no_new_evidence', [*errors, 'round_limit_fail_closed'])
    if final_output is not None and final_action == 'request_evidence':
        accounting = compute_mapping_draft_accounting(getattr(workspace, 'mapping_draft', None), workspace) if getattr(workspace, 'mapping_draft', None) is not None else None
        description = [
            'budget_exhausted',
            f'evidence_batches={len(evidence_batches)}',
            f'used_evidence_batches={int(getattr(workspace.budget, "used_evidence_batches", 0) or 0)}',
            f'max_evidence_batches={int(getattr(workspace.budget, "max_evidence_batches", 0) or 0)}',
        ]
        if accounting is not None:
            description.extend([
                f'unresolved_count={int(getattr(accounting, "unresolved_count", 0) or 0)}',
                f'needs_more_evidence_file_count={int(getattr(accounting, "needs_more_evidence_file_count", 0) or 0)}',
            ])
        fail_output = CaseJudgeOutput(
            action='fail_closed',
            fail_closed_reasons=[FailClosedReason(ref='FR1', reason_kind='insufficient_evidence', description='; '.join(description), related_refs=[])],
            summary='budget exhausted before accepted mapping',
        )
        verifier_result = verify_judge_output(workspace.to_dossier(round_context='round_limit_budget_exhausted'), fail_output)
        return CaseAgentRunResult(True, workspace.header.case_id, 'fail_closed', 'fail_closed', fail_output, verifier_result, workspace, judge_outputs, evidence_batches, 'budget_exhausted', [*errors, 'budget_exhausted'])
    if final_output is not None and final_action == 'submit_verdict' and final_verifier_result is not None:
        summary = _verifier_gap_summary(final_verifier_result)
        return CaseAgentRunResult(False, workspace.header.case_id, 'fail_closed', final_action, final_output, final_verifier_result, workspace, judge_outputs, evidence_batches, summary, [*errors, summary])
    fail_output = CaseJudgeOutput(
        action='fail_closed',
        fail_closed_reasons=[FailClosedReason(ref='FR1', reason_kind='insufficient_evidence', description='round limit reached before accepted mapping', related_refs=[])],
        summary='round limit reached before accepted mapping',
    )
    verifier_result = verify_judge_output(workspace.to_dossier(round_context='round_limit_no_new_evidence'), fail_output)
    return CaseAgentRunResult(True, workspace.header.case_id, 'fail_closed', 'fail_closed', fail_output, verifier_result, workspace, judge_outputs, evidence_batches, 'no_new_evidence', [*errors, 'round_limit_no_new_evidence'])


def _detail_equivalent_span_refs(workspace: CaseEvidenceWorkspace) -> list[str]:
    dossier = workspace.to_dossier(round_context='investigation_action')
    return list(dict.fromkeys([
        card.ref
        for card in (getattr(dossier, 'bangumi_span_cards', []) or [])
        if getattr(card, 'ref', '') and bool(getattr(card, 'detail_equivalent', False))
    ]))


def _draft_open_rows(draft: MappingDraft | None) -> list:
    if draft is None:
        return []
    return [
        row for row in list(getattr(draft, 'rows', []) or [])
        if str(getattr(row, 'disposition', '') or '') in {'open', 'needs_more_evidence', 'unaligned_fail_closed'}
        or str(getattr(row, 'status', '') or '') in {'open', 'unresolved', 'rejected'}
    ]


def _patch_draft_local_ref(draft: MappingDraft, patch: MappingDraftPatch) -> str:
    normalized = normalize_mapping_patch_op(patch)
    raw_local_ref = str(getattr(normalized, 'local_ref', '') or '')
    rows_by_ref = {
        key: row
        for row in list(getattr(draft, 'rows', []) or [])
        for key in (str(getattr(row, 'row_ref', '') or ''), str(getattr(row, 'local_ref', '') or ''))
        if key
    }
    row = rows_by_ref.get(raw_local_ref)
    if row is not None:
        return str(getattr(row, 'local_ref', '') or '')
    return raw_local_ref


def _filter_mapping_patches_to_open_rows(draft: MappingDraft, patches: list[MappingDraftPatch]) -> tuple[list[MappingDraftPatch], list[MappingDraftPatch]]:
    open_local_refs = {
        str(getattr(row, 'local_ref', '') or '')
        for row in _draft_open_rows(draft)
        if str(getattr(row, 'local_ref', '') or '')
    }
    known_local_refs = {
        str(getattr(row, 'local_ref', '') or '')
        for row in list(getattr(draft, 'rows', []) or [])
        if str(getattr(row, 'local_ref', '') or '')
    }
    kept: list[MappingDraftPatch] = []
    dropped: list[MappingDraftPatch] = []
    for patch in list(patches or []):
        local_ref = _patch_draft_local_ref(draft, patch)
        if local_ref in known_local_refs and local_ref not in open_local_refs:
            dropped.append(patch)
            continue
        kept.append(patch)
    return kept, dropped


def _patch_changes_existing_row(draft: MappingDraft, patch: MappingDraftPatch) -> bool:
    normalized = normalize_mapping_patch_op(patch)
    local_ref = _patch_draft_local_ref(draft, normalized)
    row = next(
        (
            item for item in list(getattr(draft, 'rows', []) or [])
            if str(getattr(item, 'local_ref', '') or '') == local_ref
        ),
        None,
    )
    if row is None:
        return True
    op = str(getattr(normalized, 'op', '') or '')
    if op == 'map_to_bangumi':
        target_ref = str(getattr(normalized, 'target_span_ref', '') or getattr(normalized, 'target_ref', '') or '')
        return (
            str(getattr(row, 'disposition', '') or '') != 'map_to_bangumi'
            or str(getattr(row, 'selected_target_ref', '') or '') != target_ref
        )
    if op == 'mark_non_bangumi_or_supplemental':
        return (
            str(getattr(row, 'disposition', '') or '') != 'non_bangumi_or_supplemental'
            or str(getattr(row, 'reason_kind', '') or '') != str(getattr(normalized, 'reason_kind', '') or '')
        )
    if op == 'needs_more_evidence':
        return (
            str(getattr(row, 'disposition', '') or '') != 'needs_more_evidence'
            or list(getattr(row, 'requested_request_types', []) or []) != list(getattr(normalized, 'requested_request_types', []) or [])
            or str(getattr(row, 'reason_kind', '') or '') != str(getattr(normalized, 'reason_kind', '') or '')
        )
    if op == 'mark_unaligned_fail_closed':
        return (
            str(getattr(row, 'disposition', '') or '') != 'unaligned_fail_closed'
            or str(getattr(row, 'reason_kind', '') or '') != str(getattr(normalized, 'reason_kind', '') or '')
        )
    if op == 'retract_mapping':
        return str(getattr(row, 'disposition', '') or '') == 'map_to_bangumi'
    if op == 'reject_candidate':
        reject_refs = {
            ref for ref in [
                str(getattr(normalized, 'target_ref', '') or ''),
                str(getattr(normalized, 'target_span_ref', '') or ''),
                *[str(value or '') for value in list(getattr(normalized, 'support_refs', []) or [])],
            ]
            if ref
        }
        return bool(reject_refs & {str(ref or '') for ref in list(getattr(row, 'candidate_target_refs', []) or [])})
    return True


def _filter_mapping_patches_for_agent_revision(draft: MappingDraft, patches: list[MappingDraftPatch]) -> tuple[list[MappingDraftPatch], list[MappingDraftPatch]]:
    open_local_refs = {
        str(getattr(row, 'local_ref', '') or '')
        for row in _draft_open_rows(draft)
        if str(getattr(row, 'local_ref', '') or '')
    }
    known_local_refs = {
        str(getattr(row, 'local_ref', '') or '')
        for row in list(getattr(draft, 'rows', []) or [])
        if str(getattr(row, 'local_ref', '') or '')
    }
    kept: list[MappingDraftPatch] = []
    dropped: list[MappingDraftPatch] = []
    for patch in list(patches or []):
        local_ref = _patch_draft_local_ref(draft, patch)
        if local_ref in known_local_refs and local_ref not in open_local_refs and not _patch_changes_existing_row(draft, patch):
            dropped.append(patch)
            continue
        kept.append(patch)
    return kept, dropped


def _mapping_editor_output_with_open_row_patches(draft: MappingDraft, output):
    kept, dropped = _filter_mapping_patches_to_open_rows(draft, list(getattr(output, 'patches', []) or []))
    if not dropped:
        return output, []
    return output.model_copy(update={'patches': kept}), dropped


def _refresh_mapping_draft_candidates(workspace: CaseEvidenceWorkspace) -> CaseEvidenceWorkspace:
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None or not getattr(draft, 'rows', None):
        return workspace
    dossier = workspace.to_dossier(round_context='mapping_draft_candidate_refresh')
    detail_spans = [
        card for card in (getattr(dossier, 'bangumi_span_cards', []) or [])
        if getattr(card, 'ref', '') and bool(getattr(card, 'detail_equivalent', False))
    ]
    special_item_refs = special_like_item_refs(dossier)
    if not detail_spans and not special_item_refs:
        return workspace
    span_bound_local_ref = {
        str(getattr(span, 'ref', '') or ''): str(getattr(span, 'source_request_ref', '') or '').removeprefix('INTENT_')
        for span in detail_spans
        if str(getattr(span, 'ref', '') or '').startswith('BES_INTENT_')
        and str(getattr(span, 'source_request_ref', '') or '').startswith('INTENT_')
    }
    changed = False
    updated = draft.model_copy(deep=True)
    for row in updated.rows:
        is_target_absent_row = (
            str(getattr(row, 'disposition', '') or '') == 'non_bangumi_or_supplemental'
            and str(getattr(row, 'reason_kind', '') or '') == 'bangumi_target_absent'
        )
        if (
            getattr(row, 'status', '') not in {'open', 'unresolved'}
            and getattr(row, 'disposition', '') not in {'open', 'needs_more_evidence'}
            and not is_target_absent_row
        ):
            continue
        row_local_ref = str(getattr(row, 'local_ref', '') or '')
        local_span = next((card for card in getattr(dossier, 'local_span_cards', []) or [] if getattr(card, 'ref', '') == row_local_ref), None)
        special_eligible = is_special_eligible_span(local_span, dossier)
        linked = [
            span.ref for span in detail_spans
            if str(getattr(span, 'source_request_ref', '') or '') == f'REQ_TARGET_SPAN_{row_local_ref}'
            and (not special_eligible or str(getattr(span, 'item_kind', '') or '') == 'special')
        ]
        if not linked:
            local_start = getattr(local_span, 'episode_token_start', None)
            local_end = getattr(local_span, 'episode_token_end', None)
            local_count = int(getattr(local_span, 'file_ref_count', 0) or len(getattr(local_span, 'file_refs', []) or [])) if local_span is not None else 0
            has_exact_window = bool(
                local_start is not None
                and local_end is not None
                and (int(local_start) > 0 or int(local_end) > int(local_start))
                and int(getattr(local_span, 'episode_token_count', 0) or 0) == local_count
                and int(getattr(local_span, 'gap_count', 0) or 0) == 0
                and int(getattr(local_span, 'duplicate_count', 0) or 0) == 0
            )
            if has_exact_window and not special_eligible:
                linked = [
                    span.ref for span in detail_spans
                    if not str(getattr(span, 'ref', '') or '').startswith('BES_INTENT_')
                    and (
                        not span_bound_local_ref.get(str(getattr(span, 'ref', '') or ''))
                        or span_bound_local_ref.get(str(getattr(span, 'ref', '') or '')) == row_local_ref
                    )
                    and int(getattr(span, 'target_ref_count', 0) or len(getattr(span, 'target_refs', []) or [])) == local_count
                    and getattr(span, 'sort_start', None) == local_start
                    and getattr(span, 'sort_end', None) == local_end
                ]
            if not linked and local_count == 1 and len(detail_spans) == 1 and not special_eligible:
                linked = [
                    span.ref for span in detail_spans
                    if not str(getattr(span, 'ref', '') or '').startswith('BES_INTENT_')
                    and int(getattr(span, 'target_ref_count', 0) or len(getattr(span, 'target_refs', []) or [])) == local_count
                ]
            if not linked and local_count == 1 and local_start == 0 and local_end == 0 and not special_eligible:
                zero_items = [
                    str(getattr(item, 'ref', '') or '')
                    for item in list(getattr(dossier, 'bangumi_items', []) or [])
                    if str(getattr(item, 'ref', '') or '')
                    and str(getattr(item, 'item_kind', '') or '') not in {'special', 'movie'}
                    and int(getattr(item, 'sort', 0) or 0) == 0
                ]
                linked = zero_items
        special_linked: list[str] = []
        if special_item_refs:
            if special_eligible:
                special_linked = list(special_item_refs)
        special_span_linked: list[str] = []
        if special_eligible:
            local_count = int(getattr(local_span, 'file_ref_count', 0) or len(getattr(local_span, 'file_refs', []) or [])) if local_span is not None else 0
            special_span_linked = [
                span.ref for span in detail_spans
                if str(getattr(span, 'item_kind', '') or '') == 'special'
                and not str(getattr(span, 'ref', '') or '').startswith('BES_INTENT_')
                and int(getattr(span, 'target_ref_count', 0) or len(getattr(span, 'target_refs', []) or [])) == local_count
            ]
        before = list(row.candidate_target_refs or [])
        kept_bound_intent_refs = [
            ref for ref in before
            if str(ref or '') not in span_bound_local_ref
            or span_bound_local_ref.get(str(ref or '')) == row_local_ref
        ]
        if kept_bound_intent_refs != before:
            before = kept_bound_intent_refs
            changed = True
        if special_eligible:
            regular_span_refs = {
                str(getattr(span, 'ref', '') or '')
                for span in detail_spans
                if str(getattr(span, 'ref', '') or '')
                and str(getattr(span, 'item_kind', '') or '') != 'special'
            }
            kept_existing = [ref for ref in before if ref not in regular_span_refs]
            merged = list(dict.fromkeys([*kept_existing, *special_span_linked, *special_linked]))
        else:
            merged = list(dict.fromkeys([*before, *linked, *special_span_linked, *special_linked]))
        if merged and getattr(row, 'disposition', '') in {'needs_more_evidence', 'non_bangumi_or_supplemental'}:
            row.disposition = 'open'
            row.status = 'open'
            row.selected_target_ref = ''
            row.selected_target_kind = 'none'
            row.mapping_mode = 'unresolved'
            row.reason_kind = ''
            row.reason = ''
            changed = True
        if merged != before:
            row.candidate_target_refs = merged
            changed = True
    if not changed:
        return workspace
    updated.version += 1
    return _workspace_with_mapping_draft(workspace, updated, note='mapping_draft_candidates_refreshed')


def _mapping_draft_accounting_result(workspace: CaseEvidenceWorkspace) -> tuple[object | None, CaseVerifierResult | None]:
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None:
        return None, None
    dossier = workspace.to_dossier(round_context='mapping_draft_verify')
    accounting = compute_mapping_draft_accounting(draft, dossier)
    verifier_result = verify_mapping_draft_accounting(dossier, draft)
    return accounting, verifier_result


def _reopen_mapping_draft_issue_rows(workspace: CaseEvidenceWorkspace, issues: list[VerifierIssue]) -> CaseEvidenceWorkspace:
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None:
        return workspace
    rows = list(getattr(draft, 'rows', []) or [])
    rows_by_ref = {
        key: row
        for row in rows
        for key in (str(getattr(row, 'row_ref', '') or ''), str(getattr(row, 'local_ref', '') or ''))
        if key
    }
    issue_row_refs: set[str] = set()
    for issue in list(issues or []):
        primary_ref = str(getattr(issue, 'ref', '') or '')
        primary_row = rows_by_ref.get(primary_ref)
        primary_row_ref = str(getattr(primary_row, 'row_ref', '') or '') if primary_row is not None else ''
        if primary_row_ref:
            issue_row_refs.add(primary_row_ref)
            continue
        for related_ref in list(getattr(issue, 'related_refs', []) or []):
            row = rows_by_ref.get(str(related_ref or ''))
            row_ref = str(getattr(row, 'row_ref', '') or '') if row is not None else ''
            if row_ref:
                issue_row_refs.add(row_ref)
    if not issue_row_refs:
        return workspace
    updated = draft.model_copy(deep=True)
    changed = False
    for row in list(getattr(updated, 'rows', []) or []):
        if str(getattr(row, 'row_ref', '') or '') not in issue_row_refs:
            continue
        row.selected_target_ref = ''
        row.selected_target_kind = 'none'
        row.mapping_mode = 'unresolved'
        row.status = 'open'
        row.disposition = 'open'
        row.support_refs = []
        row.reason_kind = ''
        row.reason = 'reopened for verifier issue repair'
        changed = True
    if not changed:
        return workspace
    updated.version += 1
    preserved_comparisons = [
        comparison
        for comparison in list(getattr(workspace, 'mapping_draft_candidate_comparisons', []) or [])
        if _comparison_row_ref(comparison) not in issue_row_refs
    ]
    reopened = _workspace_preserving_state(workspace, mapping_draft_candidate_comparisons=preserved_comparisons)
    return _workspace_with_mapping_draft(reopened, updated, note='mapping_draft_issue_rows_reopened')


def _mapping_draft_has_complete_local_coverage(workspace: CaseEvidenceWorkspace, draft: MappingDraft | None) -> bool:
    if draft is None:
        return False
    coverage_issue = _mapping_draft_local_coverage_issue(workspace, draft)
    return coverage_issue is None


def _editor_call_count_after_latest_evidence(workspace: CaseEvidenceWorkspace) -> int:
    audits = list(getattr(workspace, 'judge_request_audits', []) or [])
    latest_evidence_index = -1
    for index, audit in enumerate(audits):
        if not isinstance(audit, dict):
            continue
        if str(audit.get('note') or '') in {
            'planner_selected_menu_request_ids',
            'evidence_menu_resolution',
            'orchestrator_execute_evidence_menu_resolution',
            'editor_evidence_menu_resolution',
            'planner_batch_result',
            'evidence_batch_result',
        }:
            latest_evidence_index = index
    return sum(
        1
        for audit in audits[latest_evidence_index + 1:]
        if isinstance(audit, dict) and audit.get('note') == 'mapping_draft_editor_called'
    )


def _mapping_intent_call_count_after_latest_evidence(workspace: CaseEvidenceWorkspace) -> int:
    audits = list(getattr(workspace, 'judge_request_audits', []) or [])
    latest_evidence_index = -1
    for index, audit in enumerate(audits):
        if not isinstance(audit, dict):
            continue
        if str(audit.get('note') or '') in {
            'planner_selected_menu_request_ids',
            'evidence_menu_resolution',
            'orchestrator_execute_evidence_menu_resolution',
            'editor_evidence_menu_resolution',
            'planner_batch_result',
            'evidence_batch_result',
        }:
            latest_evidence_index = index
    return sum(
        1
        for audit in audits[latest_evidence_index + 1:]
        if isinstance(audit, dict) and audit.get('note') == 'orchestrator_mapping_intents_result'
    )


def _no_new_evidence_precondition_audit(workspace: CaseEvidenceWorkspace) -> dict[str, object]:
    executable_ids = _remaining_executable_menu_request_ids(workspace)
    target_side_ids = _remaining_executable_menu_request_ids(workspace, target_side_only=True)
    editor_calls = _editor_call_count_after_latest_evidence(workspace)
    mapping_intent_calls = _mapping_intent_call_count_after_latest_evidence(workspace)
    semantic_decision_calls = editor_calls + mapping_intent_calls
    deferred_subject_recall = _has_deferred_subject_recall_intent(workspace)
    human_blockers = human_next_action_blockers(workspace)
    durable_draft_intent_count = _durable_draft_evidence_intent_count(getattr(workspace, 'mapping_draft', None))
    return {
        'editor_call_count_after_latest_evidence': editor_calls,
        'mapping_intent_call_count_after_latest_evidence': mapping_intent_calls,
        'semantic_decision_call_count_after_latest_evidence': semantic_decision_calls,
        'remaining_executable_request_count': len(executable_ids),
        'remaining_executable_request_ids': executable_ids[:12],
        'remaining_target_side_executable_request_count': len(target_side_ids),
        'remaining_target_side_executable_request_ids': target_side_ids[:12],
        'deferred_subject_recall_intent': deferred_subject_recall,
        'durable_draft_evidence_intent_count': durable_draft_intent_count,
        'human_next_action_blocked_no_new_evidence_count': len(human_blockers),
        'human_next_action_blockers': human_blockers[:8],
        'no_new_evidence_preconditions_ok': bool(semantic_decision_calls > 0 and not target_side_ids and not deferred_subject_recall and not human_blockers and durable_draft_intent_count == 0),
    }


def _should_try_mapping_editor(workspace: CaseEvidenceWorkspace) -> bool:
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None or not getattr(draft, 'rows', None):
        return False
    if not _draft_open_rows(draft):
        return False
    return _mapping_draft_has_complete_local_coverage(workspace, draft)


def _open_rows_without_candidates(draft: MappingDraft | None) -> list[str]:
    return [
        str(getattr(row, 'local_ref', '') or '')
        for row in _draft_open_rows(draft)
        if not list(getattr(row, 'candidate_target_refs', []) or [])
    ]


def _open_regular_rows_waiting_for_span_proof(workspace: CaseEvidenceWorkspace) -> list[str]:
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None:
        return []
    completed_or_failed = set(getattr(workspace.plan_state, 'completed_menu_request_ids', []) or []) | set(getattr(workspace.plan_state, 'failed_menu_request_ids', []) or [])
    waiting: list[str] = []
    spans_by_ref = {
        str(getattr(card, 'ref', '') or ''): card
        for card in list(getattr(workspace, 'local_span_cards', []) or [])
        if str(getattr(card, 'ref', '') or '') and str(getattr(card, 'span_scope', '') or '') != 'package'
    }
    for row in _draft_open_rows(draft):
        if list(getattr(row, 'candidate_target_refs', []) or []):
            continue
        local_ref = str(getattr(row, 'local_ref', '') or '')
        span = spans_by_ref.get(local_ref)
        if span is None:
            continue
        raw_sort_start = getattr(span, 'episode_token_start', None)
        raw_sort_end = getattr(span, 'episode_token_end', None)
        sort_start = int(raw_sort_start) if raw_sort_start is not None else 0
        sort_end = int(raw_sort_end) if raw_sort_end is not None else 0
        if raw_sort_start is None or raw_sort_end is None or sort_end < sort_start:
            continue
        if sort_start <= 0 and sort_end <= sort_start:
            continue
        if int(getattr(span, 'episode_token_count', 0) or 0) <= 0:
            continue
        if int(getattr(span, 'episode_token_count', 0) or 0) != int(getattr(span, 'file_ref_count', 0) or len(getattr(span, 'file_refs', []) or [])):
            continue
        if int(getattr(span, 'gap_count', 0) or 0) or int(getattr(span, 'duplicate_count', 0) or 0):
            continue
        request_id = f'REQ_TARGET_SPAN_{local_ref}'
        if request_id not in completed_or_failed:
            waiting.append(local_ref)
    return waiting


def _open_special_rows_with_candidates(workspace: CaseEvidenceWorkspace) -> list[str]:
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None:
        return []
    dossier = workspace.to_dossier(round_context='special_candidate_gate')
    eligible = set(special_eligible_open_row_refs(draft, dossier))
    special_refs = set(special_like_item_refs(dossier))
    special_span_refs = {
        str(getattr(card, 'ref', '') or '')
        for card in list(getattr(dossier, 'bangumi_span_cards', []) or [])
        if str(getattr(card, 'ref', '') or '')
        and bool(getattr(card, 'detail_equivalent', False))
        and str(getattr(card, 'item_kind', '') or '') == 'special'
    }
    rows: list[str] = []
    for row in list(getattr(draft, 'rows', []) or []):
        local_ref = str(getattr(row, 'local_ref', '') or '')
        if local_ref not in eligible:
            continue
        if any(ref in special_refs or ref in special_span_refs for ref in list(getattr(row, 'candidate_target_refs', []) or [])):
            rows.append(local_ref)
    return rows


def _open_target_absent_candidate_rows(workspace: CaseEvidenceWorkspace) -> list[str]:
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None:
        return []
    if not _bangumi_target_absent_surface_exhausted(workspace):
        return []
    dossier = workspace.to_dossier(round_context='target_absent_candidate_gate')
    issues = _unresolved_bangumi_target_absent_candidate_issues(dossier, draft)
    return _dedupe_preserve_order([str(getattr(issue, 'ref', '') or '') for issue in issues])


def _bangumi_target_absent_surface_exhausted(workspace: CaseEvidenceWorkspace) -> bool:
    evidence_touched_bangumi = bool(
        list(getattr(workspace, 'bangumi_subjects', []) or [])
        or list(getattr(workspace, 'bangumi_items', []) or [])
        or list(getattr(workspace, 'bangumi_relations', []) or [])
        or list(getattr(workspace, 'bangumi_span_cards', []) or [])
        or list(getattr(workspace, 'previous_evidence_results', []) or [])
    )
    if not evidence_touched_bangumi:
        return False
    if workspace.budget.max_evidence_batches and workspace.budget.used_evidence_batches >= workspace.budget.max_evidence_batches:
        return True
    if _pending_special_request_ids(workspace):
        return False
    completed_or_failed = set(getattr(workspace.plan_state, 'completed_menu_request_ids', []) or []) | set(getattr(workspace.plan_state, 'failed_menu_request_ids', []) or [])
    menu = build_executable_evidence_menu(workspace)
    pending_ids = [
        str(item.get('request_id') or '')
        for item in list(menu.get('prompt_summaries') or [])
        if str(item.get('request_id') or '')
        and str(item.get('request_id') or '') not in completed_or_failed
        and str(item.get('request_type') or '') in {'subject_search', 'subject_lookup', 'related_expansion', 'episode_list', 'target_detail', 'target_window', 'target_span'}
    ]
    return not pending_ids


def _pending_special_request_ids(workspace: CaseEvidenceWorkspace) -> list[str]:
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None:
        return []
    dossier = workspace.to_dossier(round_context='special_pending_gate')
    if not special_eligible_open_row_refs(draft, dossier):
        return []
    completed_or_failed = set(getattr(workspace.plan_state, 'completed_menu_request_ids', []) or []) | set(getattr(workspace.plan_state, 'failed_menu_request_ids', []) or [])
    menu = build_executable_evidence_menu(workspace)
    return [
        str(item.get('request_id') or '')
        for item in list(menu.get('prompt_summaries') or [])
        if str(item.get('request_id') or '').startswith('REQ_SPECIAL_')
        and str(item.get('request_id') or '') not in completed_or_failed
    ]


def _has_special_investigation_rows(workspace: CaseEvidenceWorkspace) -> bool:
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None:
        return False
    dossier = workspace.to_dossier(round_context='special_investigation_rows')
    return bool(special_eligible_row_refs(draft, dossier))


def _has_assignable_or_detail_surface(workspace: CaseEvidenceWorkspace, dossier=None) -> bool:
    active = dossier if dossier is not None else workspace.to_dossier(round_context='contradiction_guard')
    if getattr(active, 'assignable_target_refs', None):
        return True
    if any(bool(getattr(card, 'detail_equivalent', False)) for card in (getattr(active, 'bangumi_span_cards', []) or [])):
        return True
    return False


def _notebook_blockers_request_subject_query(blockers: list[dict[str, object]]) -> bool:
    for blocker in list(blockers or []):
        requested = {str(value or '') for value in list(blocker.get('requested_request_types') or [])}
        label = ' '.join([
            str(blocker.get('question_kind') or ''),
            str(blocker.get('action_type') or ''),
            str(blocker.get('reason') or ''),
        ]).casefold()
        if 'subject_search' in requested or 'subject' in label or 'alternate' in label:
            return True
    return False


def _notebook_evidence_plan_from_blockers(
    workspace: CaseEvidenceWorkspace,
    blockers: list[dict[str, object]],
) -> tuple[EvidencePlannerOutput | None, dict[str, object]]:
    if not blockers:
        return None, {'notebook_agenda_count': 0}
    menu = build_executable_evidence_menu(workspace)
    summaries = list(menu.get('prompt_summaries') or [])
    requested_types = _dedupe_preserve_order([
        str(request_type or '')
        for blocker in blockers
        for request_type in list(blocker.get('requested_request_types') or [])
        if str(request_type or '') in _TARGET_SIDE_EVIDENCE_REQUEST_TYPES
    ])
    if not requested_types:
        return None, {'notebook_agenda_count': len(blockers), 'notebook_agenda_requested_types': []}
    blocker_refs = {
        str(ref or '')
        for blocker in blockers
        for ref in [
            *list(blocker.get('local_refs') or []),
            *list(blocker.get('target_refs') or []),
            *list(blocker.get('subject_refs') or []),
            *list(blocker.get('item_refs') or []),
        ]
        if str(ref or '')
    }
    selected_ids: list[str] = []
    for summary in summaries:
        request_id = str(summary.get('request_id') or '')
        request_type = str(summary.get('request_type') or '')
        if not request_id or request_type not in requested_types:
            continue
        source_refs = {str(ref or '') for ref in list(summary.get('source_refs') or []) if str(ref or '')}
        if blocker_refs and source_refs and not (blocker_refs & source_refs):
            continue
        selected_ids.append(request_id)
    selected_ids = _dedupe_preserve_order(selected_ids)
    selected_ids, phase_audit = _evidence_phase_request_ids_for_editor_intent(
        workspace,
        summaries,
        selected_ids,
        requested_types,
        subject_refs=[
            str(ref or '')
            for blocker in blockers
            for ref in list(blocker.get('subject_refs') or [])
            if str(ref or '')
        ],
    )
    selected_ids, stale_ids = _filter_stale_menu_request_ids(workspace, selected_ids)
    audit = {
        'notebook_agenda_count': len(blockers),
        'notebook_agenda_requested_types': requested_types,
        'notebook_agenda_selected_request_ids': selected_ids,
        'notebook_agenda_stale_request_ids': stale_ids,
        **phase_audit,
    }
    if not selected_ids:
        return None, audit
    if workspace.budget.max_requests_per_batch:
        selected_ids = selected_ids[:workspace.budget.max_requests_per_batch]
    plan = EvidencePlan(
        plan_id='NOTEBOOK_AGENDA_1',
        plan_kind=_plan_kind_for_editor_request_ids(summaries, selected_ids),
        selected_menu_request_ids=selected_ids,
        plan_status='in_progress',
        goal='execute InvestigationNotebook open evidence agenda',
    )
    return EvidencePlannerOutput(selected_evidence=True, plan=plan), audit


def _fail_closed_claims_no_assignable_target(output: CaseJudgeOutput) -> bool:
    if output.action != 'fail_closed':
        return False
    text = ' '.join([
        str(getattr(output, 'summary', '') or ''),
        *[str(getattr(reason, 'description', '') or '') for reason in list(getattr(output, 'fail_closed_reasons', []) or [])],
    ]).casefold()
    markers = (
        'no assignable',
        'no visible assignable',
        'zero assignable',
        'zero visible assignable',
        'no legal target',
        'no target',
        'no usable target',
        'no bangumi target',
        'no visible target',
    )
    return any(marker in text for marker in markers)


def _tool_ref_validation_issues(workspace: CaseEvidenceWorkspace, tool_call: OrchestratorAgentToolCall) -> list[str]:
    visible = workspace.to_dossier(round_context='orchestrator_tool_validation').visible_refs
    allowed_local = {
        *list(getattr(visible, 'local_file_refs', []) or []),
        *list(getattr(visible, 'local_cluster_refs', []) or []),
        *[str(getattr(card, 'ref', '') or '') for card in list(getattr(workspace, 'local_span_cards', []) or [])],
    }
    allowed_query = set(getattr(visible, 'query_refs', []) or [])
    allowed_subject = set(getattr(visible, 'bangumi_subject_refs', []) or [])
    allowed_item = set(getattr(visible, 'bangumi_item_refs', []) or [])
    allowed_row = {
        str(getattr(row, 'row_ref', '') or '')
        for row in list(getattr(getattr(workspace, 'mapping_draft', None), 'rows', []) or [])
        if str(getattr(row, 'row_ref', '') or '')
    }
    allowed_target = {
        *set(getattr(visible, 'target_refs', []) or []),
        *set(getattr(visible, 'bangumi_subject_refs', []) or []),
        *set(getattr(visible, 'bangumi_relation_refs', []) or []),
        *set(getattr(visible, 'bangumi_group_refs', []) or []),
        *set(getattr(visible, 'bangumi_item_refs', []) or []),
        *[str(getattr(card, 'ref', '') or '') for card in list(getattr(workspace, 'bangumi_span_cards', []) or [])],
    }
    args = tool_call.raw_arguments
    issues: list[str] = []

    def check_refs(key: str, allowed: set[str]) -> None:
        for ref in list(args.get(key) or []):
            value = str(ref or '')
            if value and value not in allowed:
                issues.append(f'{key}:{value}')

    def check_ref_value(key: str, value: object, allowed: set[str]) -> None:
        ref = str(value or '')
        if ref and ref not in allowed:
            issues.append(f'{key}:{ref}')

    def walk_nested(value: object, prefix: str = '') -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                path = f'{prefix}.{key}' if prefix else str(key)
                if key in {'local_ref'}:
                    check_ref_value(path, item, allowed_local)
                elif key in {'row_ref'}:
                    check_ref_value(path, item, allowed_row)
                elif key in {'chosen_subject_ref'}:
                    check_ref_value(path, item, allowed_subject)
                elif key in {'chosen_item_ref'}:
                    check_ref_value(path, item, allowed_item)
                elif key in {'chosen_span_ref'}:
                    check_ref_value(path, item, allowed_target)
                elif key in {'target_ref', 'target_span_ref', 'left_ref', 'right_ref', 'winner_ref'}:
                    check_ref_value(path, item, allowed_target | allowed_local)
                elif key in {'local_refs', 'source_refs', 'support_refs'} and isinstance(item, list):
                    for ref in item:
                        check_ref_value(path, ref, allowed_local | allowed_target | allowed_query)
                elif key == 'query_refs' and isinstance(item, list):
                    for ref in item:
                        check_ref_value(path, ref, allowed_query)
                elif key == 'subject_refs' and isinstance(item, list):
                    for ref in item:
                        check_ref_value(path, ref, allowed_subject)
                elif key == 'item_refs' and isinstance(item, list):
                    for ref in item:
                        check_ref_value(path, ref, allowed_item | allowed_target)
                else:
                    walk_nested(item, path)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk_nested(item, f'{prefix}[{index}]')

    check_refs('local_refs', allowed_local)
    check_refs('query_refs', allowed_query)
    check_refs('subject_refs', allowed_subject)
    check_refs('item_refs', allowed_item | allowed_target)
    check_refs('target_refs', allowed_target)
    check_refs('row_refs', allowed_row)
    walk_nested(args)
    return issues


def _plan_kind_for_request_types(request_types: list[str]) -> str:
    if any(value == 'subject_search' for value in request_types):
        return 'subject_recall'
    if any(value in {'related_expansion', 'episode_detail'} for value in request_types):
        return 'special_recall'
    if any(value in {'subject_lookup', 'episode_list'} for value in request_types):
        return 'episode_recall'
    return 'span_proof'


def _orchestrator_evidence_plan_from_tool(
    workspace: CaseEvidenceWorkspace,
    tool_call: OrchestratorAgentToolCall,
) -> tuple[EvidencePlannerOutput | None, dict[str, object]]:
    args = tool_call.arguments
    if not isinstance(args, ExecuteEvidenceToolArgs):
        return None, {'accepted': False, 'reason': 'wrong_tool_args'}
    if (
        getattr(workspace.budget, 'max_evidence_batches', 0)
        and workspace.budget.used_evidence_batches >= workspace.budget.max_evidence_batches
    ):
        return None, {
            'accepted': False,
            'reason': 'evidence_budget_exhausted',
            **_mapping_draft_observation(workspace),
            'finish_gate': _finish_gate_observation(workspace),
            'executable_menu_summary': _executable_menu_observation(workspace),
            'recommended_next_observation': (
                'evidence budget is exhausted. Propose_mapping_intents for remaining open_rows using visible evidence, '
                'or finish fail_closed only after the finish gate allows it.'
            ),
        }
    menu = build_executable_evidence_menu(workspace, max_requests=32)
    summaries = list(menu.get('prompt_summaries') or [])
    registry = menu.get('payload_registry') if isinstance(menu.get('payload_registry'), dict) else {}
    selected_ids = [str(value or '') for value in list(args.selected_menu_request_ids or []) if str(value or '')]
    request_types = [str(value or '') for value in list(args.requested_request_types or []) if str(value or '')]
    agenda_request_types, agenda_subject_refs = _latest_blocked_evidence_agenda(workspace)
    if not request_types and agenda_request_types:
        request_types = agenda_request_types
    explicit_subject_refs = _subject_refs_from_evidence_tool_args(args)
    if not explicit_subject_refs and agenda_subject_refs:
        explicit_subject_refs = agenda_subject_refs
    summaries, registry, augmented_request_ids = _augment_menu_with_agent_subject_requests(
        summaries,
        registry,
        subject_refs=explicit_subject_refs,
        request_types=request_types,
    )
    if not selected_ids and request_types:
        requested_subject_refs = set(explicit_subject_refs)
        for summary in summaries:
            request_id = str(summary.get('request_id') or '')
            request_type = str(summary.get('request_type') or '')
            source_refs = set(_request_summary_source_refs(summary))
            if request_id and request_type in request_types and (not requested_subject_refs or requested_subject_refs & source_refs):
                selected_ids.append(request_id)
    selected_ids = list(dict.fromkeys(selected_ids))
    unknown_ids = [request_id for request_id in selected_ids if request_id not in registry]
    selected_ids = [request_id for request_id in selected_ids if request_id in registry]
    selected_ids, stale_ids = _filter_stale_menu_request_ids(workspace, selected_ids)
    if not selected_ids:
        executable_summary = _executable_menu_observation(workspace)
        return None, {
            'accepted': False,
            'reason': 'stale_or_no_executable_menu_request' if stale_ids else 'no_executable_menu_request',
            'requested_request_types': request_types,
            'unknown_menu_request_ids': unknown_ids,
            'stale_menu_request_ids': stale_ids,
            'available_request_ids': [str(item.get('request_id') or '') for item in summaries[:12]],
            **_mapping_draft_observation(workspace),
            'executable_menu_summary': executable_summary,
            'finish_gate': _finish_gate_observation(workspace),
            'recommended_next_observation': (
                'There is no fresh executable evidence request matching the requested types. '
                'Do not repeat execute_evidence with the same stale request. Use propose_mapping_intents with visible refs, '
                'materialize_queries for new clean title aliases, repartition with propose_case_understanding if the row is too broad, '
                'or finish only when finish_gate allows it.'
            ),
        }
    selected_ids, phase_audit = _evidence_phase_request_ids_for_editor_intent(
        workspace,
        summaries,
        selected_ids,
        request_types,
        subject_refs=explicit_subject_refs,
    )
    if not selected_ids:
        return None, {
            'accepted': False,
            'reason': 'evidence_phase_prerequisite_missing',
            'requested_request_types': request_types,
            'unknown_menu_request_ids': unknown_ids,
            'stale_menu_request_ids': stale_ids,
            **_mapping_draft_observation(workspace),
            'executable_menu_summary': _executable_menu_observation(workspace),
            'finish_gate': _finish_gate_observation(workspace),
            'recommended_next_observation': (
                'Evidence prerequisite routing found no executable next request. '
                'Use existing visible refs in propose_mapping_intents, materialize a clean subject query, or repartition broad rows.'
            ),
            **phase_audit,
        }
    max_per_batch = int(getattr(workspace.budget, 'max_requests_per_batch', 0) or 0)
    if max_per_batch > 0:
        selected_ids = selected_ids[:max_per_batch]
    plan = EvidencePlan(
        plan_id=f'ORCH_TOOL_{max(1, int(getattr(workspace.budget, "used_evidence_batches", 0) or 0) + 1)}',
        plan_kind=_plan_kind_for_request_types([
            str(getattr(registry.get(request_id), 'request_type', '') or '')
            for request_id in selected_ids
        ]),
        selected_menu_request_ids=selected_ids,
        plan_status='in_progress',
        goal=str(getattr(args, 'reason', '') or 'orchestrator selected evidence'),
        risk_flags=_dedupe_preserve_order(['orchestrator_agent_tool_call', *request_types]),
        stop_conditions=_dedupe_preserve_order(explicit_subject_refs),
    )
    return EvidencePlannerOutput(selected_evidence=True, plan=plan), {
        'accepted': True,
        'selected_menu_request_ids': selected_ids,
        'unknown_menu_request_ids': unknown_ids,
        'stale_menu_request_ids': stale_ids,
        'agenda_request_types': agenda_request_types,
        'agenda_subject_refs': agenda_subject_refs,
        'augmented_menu_request_ids': augmented_request_ids,
        **phase_audit,
    }


def _decision_from_orchestrator_tool_call(
    workspace: CaseEvidenceWorkspace,
    tool_call: OrchestratorAgentToolCall,
) -> tuple[CaseEvidenceWorkspace, _InvestigationDecision | None, dict[str, object]]:
    ref_issues = _tool_ref_validation_issues(workspace, tool_call)
    if ref_issues:
        return workspace, None, {'accepted': False, 'reason': 'hidden_or_unknown_refs', 'ref_issues': ref_issues}
    tool_name = tool_call.tool_name
    finish_gate = _finish_gate_observation(workspace)
    open_rows_present = bool(_open_rows_observation(workspace, limit=1))
    budget_exhausted = bool(
        getattr(workspace.budget, 'max_evidence_batches', 0)
        and workspace.budget.used_evidence_batches >= workspace.budget.max_evidence_batches
    )
    budget_finish_ready = bool(
        budget_exhausted
        and (
            not open_rows_present
            or int(finish_gate.get('semantic_decision_call_count_after_latest_evidence') or 0) > 0
        )
    )
    finish_allowed = (
        bool(finish_gate.get('accepted_finish_allowed'))
        or budget_finish_ready
        or bool(finish_gate.get('fail_closed_finish_allowed_for_terminal_fail_rows'))
        or not open_rows_present
    )
    if tool_name == 'finish_case' and not finish_allowed:
        return workspace, None, {
            'accepted': False,
            'reason': 'tool_not_available_in_current_state',
            'tool_name': tool_name,
            **_mapping_draft_observation(workspace),
            'finish_gate': finish_gate,
            'executable_menu_summary': _executable_menu_observation(workspace),
            'recommended_next_observation': 'finish_case is hidden until accounting is ready, or fail_closed finish preconditions are met; use propose_mapping_intents for open_rows or execute_evidence while evidence budget remains',
        }
    reason = str(getattr(tool_call.arguments, 'reason', '') or tool_name)
    if tool_name == 'materialize_queries':
        return workspace, _InvestigationDecision(action='compose_queries', reason=reason), {'accepted': True}
    if tool_name == 'propose_case_understanding':
        return workspace, _InvestigationDecision(action='propose_case_understanding', reason=reason), {'accepted': True}
    if tool_name == 'propose_mapping_intents':
        return workspace, _InvestigationDecision(action='propose_mapping_intents', reason=reason), {'accepted': True}
    if tool_name == 'finish_case':
        args = tool_call.arguments
        status = str(getattr(args, 'status', '') or '')
        if not isinstance(args, FinishCaseToolArgs) or status not in {'accepted', 'fail_closed'}:
            return workspace, None, {
                'accepted': False,
                'reason': 'invalid_finish_case_status',
                'status': status,
                **_mapping_draft_observation(workspace),
                'finish_gate': _finish_gate_observation(workspace),
                'recommended_next_observation': 'use status=accepted with finish_kind=accepted only when finish_gate.accepted_finish_allowed is true; otherwise continue investigation',
            }
        finish_kind = str(getattr(args, 'finish_kind', '') or '')
        if status == 'accepted' and finish_kind != 'accepted':
            return workspace, None, {
                'accepted': False,
                'reason': 'accepted_finish_requires_finish_kind_accepted',
                'status': status,
                'finish_kind': finish_kind,
                **_mapping_draft_observation(workspace),
                'finish_gate': _finish_gate_observation(workspace),
                'recommended_next_observation': 'accepted finish requires status=accepted and finish_kind=accepted; if accounting is not ready, continue with open_rows',
            }
        if status == 'fail_closed' and finish_kind == 'accepted':
            return workspace, None, {
                'accepted': False,
                'reason': 'fail_closed_finish_kind_cannot_be_accepted',
                'status': status,
                'finish_kind': finish_kind,
                **_mapping_draft_observation(workspace),
                'finish_gate': _finish_gate_observation(workspace),
                'recommended_next_observation': 'you mixed fail_closed with accepted. If mapping is ready call status=accepted/finish_kind=accepted; otherwise repair open_rows before trying finish_case again',
            }
        return workspace, _InvestigationDecision(action=('accepted' if status == 'accepted' else 'fail_closed'), reason=reason), {
            'accepted': True,
            'status': status,
            'finish_kind': finish_kind,
        }
    if tool_name == 'update_notebook':
        args = tool_call.arguments
        if not isinstance(args, UpdateNotebookToolArgs):
            return workspace, None, {'accepted': False, 'reason': 'wrong_tool_args'}
        return workspace, _InvestigationDecision(action='update_notebook', reason=reason), {
            'accepted': True,
            'notebook_update_count': len(list(args.notebook_updates or [])),
        }
    if tool_name == 'execute_evidence':
        planner_output, audit = _orchestrator_evidence_plan_from_tool(workspace, tool_call)
        if planner_output is None:
            return workspace, None, audit
        planner_key = (
            str(getattr(planner_output.plan, 'plan_kind', '') or ''),
            tuple(getattr(planner_output.plan, 'selected_menu_request_ids', []) or []),
        )
        return workspace, _InvestigationDecision(
            action='execute_evidence',
            reason=reason,
            planner_output=planner_output,
            planner_key=planner_key,
        ), audit
    if tool_name == 'reconsider_split':
        args = tool_call.arguments
        if not isinstance(args, ReconsiderSplitToolArgs):
            return workspace, None, {'accepted': False, 'reason': 'wrong_tool_args'}
        return workspace, _InvestigationDecision(action='reconsider_split', reason=reason), {'accepted': True}
    return workspace, None, {'accepted': False, 'reason': f'unknown_tool:{tool_name}'}


def _fail_closed_claims_missing_span_proof_despite_item_target(output: CaseJudgeOutput) -> bool:
    if output.action != 'fail_closed':
        return False
    descriptions = [
        str(getattr(output, 'summary', '') or ''),
        *[str(getattr(reason, 'description', '') or '') for reason in list(getattr(output, 'fail_closed_reasons', []) or [])],
        *[str(getattr(finding, 'description', '') or '') for finding in list(getattr(output, 'findings', []) or [])],
    ]
    text = ' '.join(descriptions).casefold()
    if not any(marker in text for marker in ('span-proof', 'span proof', 'span/assignment', 'span refs', 'bangumi span')):
        return False
    related_refs = {
        str(ref or '')
        for reason in list(getattr(output, 'fail_closed_reasons', []) or [])
        for ref in list(getattr(reason, 'related_refs', []) or [])
    }
    finding_refs = {
        str(ref or '')
        for finding in list(getattr(output, 'findings', []) or [])
        for ref in list(getattr(finding, 'evidence_refs', []) or [])
    }
    return any(ref.startswith('BE') and not ref.startswith('BES') for ref in (related_refs | finding_refs))


def _contradictory_fail_closed_guard(workspace: CaseEvidenceWorkspace, dossier, output: CaseJudgeOutput) -> str | None:
    no_target_claim = _fail_closed_claims_no_assignable_target(output)
    span_proof_claim = _fail_closed_claims_missing_span_proof_despite_item_target(output)
    if not no_target_claim and not span_proof_claim:
        return None
    if not _has_assignable_or_detail_surface(workspace, dossier):
        return None
    if span_proof_claim:
        return 'contradictory_span_proof_fail_closed'
    return 'contradictory_fail_closed'


def _next_investigation_action(workspace: CaseEvidenceWorkspace, *, executed_planner_keys: set[tuple[str, tuple[str, ...]]] | None = None) -> _InvestigationDecision:
    workspace = _refresh_mapping_draft_candidates(_workspace_with_initial_mapping_draft(workspace))
    draft = getattr(workspace, 'mapping_draft', None)
    accounting, accounting_verifier_result = _mapping_draft_accounting_result(workspace)
    if accounting is not None and bool(getattr(accounting, 'accepted_accounting_ready', False)) and accounting_verifier_result is not None and accounting_verifier_result.passed:
        return _InvestigationDecision(action='accepted', reason='mapping_draft_accounting_ready')

    open_rows = _draft_open_rows(draft)
    editor_calls_after_latest_evidence = _editor_call_count_after_latest_evidence(workspace)
    if _has_deferred_subject_recall_intent(workspace):
        return _InvestigationDecision(action='compose_queries', reason='editor_deferred_subject_recall_requires_query_composer')
    notebook_blockers = human_next_action_blockers(workspace)
    if draft is not None and open_rows and _mapping_draft_has_complete_local_coverage(workspace, draft) and editor_calls_after_latest_evidence == 0:
        return _InvestigationDecision(action='edit_mapping_draft', reason='open_draft_rows_editor_driven')
    if (
        draft is not None
        and _needs_more_evidence_patches_from_draft(draft)
        and _mapping_draft_has_complete_local_coverage(workspace, draft)
        and (workspace.budget.max_evidence_batches == 0 or workspace.budget.used_evidence_batches < workspace.budget.max_evidence_batches)
    ):
        return _InvestigationDecision(action='edit_mapping_draft', reason='needs_more_evidence_rows_have_durable_evidence_intent')
    if (
        notebook_blockers
        and _notebook_blockers_request_subject_query(notebook_blockers)
        and not _workspace_has_bangumi_subjects(workspace)
        and not _has_pending_composed_subject_search_query(workspace)
        and (workspace.budget.max_evidence_batches == 0 or workspace.budget.used_evidence_batches < workspace.budget.max_evidence_batches)
        and (workspace.budget.max_api_calls_per_case == 0 or workspace.budget.used_api_calls < workspace.budget.max_api_calls_per_case)
        and (workspace.budget.max_subject_searches == 0 or workspace.budget.used_subject_searches < workspace.budget.max_subject_searches)
    ):
        return _InvestigationDecision(action='compose_queries', reason='notebook_open_question_requires_subject_recall')

    notebook_plan, _notebook_plan_audit = _notebook_evidence_plan_from_blockers(workspace, notebook_blockers)
    if notebook_plan and notebook_plan.plan and notebook_plan.plan.selected_menu_request_ids and (workspace.budget.max_evidence_batches == 0 or workspace.budget.used_evidence_batches < workspace.budget.max_evidence_batches):
        planner_key = (str(notebook_plan.plan.plan_kind or ''), tuple(notebook_plan.plan.selected_menu_request_ids or []))
        if planner_key not in (executed_planner_keys or set()):
            return _InvestigationDecision(
                action='execute_evidence',
                reason='notebook_open_agenda_selected_evidence',
                planner_output=notebook_plan,
                planner_key=planner_key,
            )

    has_target_surface = bool(
        list(getattr(workspace, 'bangumi_subjects', []) or [])
        or list(getattr(workspace, 'bangumi_items', []) or [])
        or list(getattr(workspace, 'bangumi_span_cards', []) or [])
    )
    can_search_subjects = bool(
        not has_target_surface
        and (workspace.budget.max_evidence_batches == 0 or workspace.budget.used_evidence_batches < workspace.budget.max_evidence_batches)
        and (workspace.budget.max_api_calls_per_case == 0 or workspace.budget.used_api_calls < workspace.budget.max_api_calls_per_case)
        and (workspace.budget.max_subject_searches == 0 or workspace.budget.used_subject_searches < workspace.budget.max_subject_searches)
    )
    if can_search_subjects and _needs_alternate_subject_query_after_empty_recall(workspace):
        return _InvestigationDecision(action='compose_queries', reason='empty_subject_recall_requires_alternate_query')
    if can_search_subjects and not _has_composed_subject_search_query(workspace):
        return _InvestigationDecision(action='compose_queries', reason='subject_recall_requires_agent_composed_queries')
    if (
        (workspace.budget.max_api_calls_per_case == 0 or workspace.budget.used_api_calls < workspace.budget.max_api_calls_per_case)
        and (workspace.budget.max_subject_searches == 0 or workspace.budget.used_subject_searches < workspace.budget.max_subject_searches)
        and _needs_alternate_subject_query_after_weak_recall(workspace)
    ):
        return _InvestigationDecision(action='compose_queries', reason='weak_subject_recall_requires_alternate_query')

    next_planner_output = build_deterministic_evidence_plan(workspace)
    if next_planner_output and next_planner_output.selected_evidence and next_planner_output.plan and (workspace.budget.max_evidence_batches == 0 or workspace.budget.used_evidence_batches < workspace.budget.max_evidence_batches):
        planned_ids, _stale_ids = _filter_stale_menu_request_ids(workspace, list(next_planner_output.plan.selected_menu_request_ids or []))
        if not planned_ids:
            return _InvestigationDecision(action='judge_semantic_blocker', reason='deterministic_planner_only_selected_stale_evidence')
        planner_key = (str(next_planner_output.plan.plan_kind or ''), tuple(planned_ids))
        if planner_key not in (executed_planner_keys or set()):
            if planned_ids != list(next_planner_output.plan.selected_menu_request_ids or []):
                next_planner_output = next_planner_output.model_copy(update={'plan': next_planner_output.plan.model_copy(update={'selected_menu_request_ids': planned_ids})})
            return _InvestigationDecision(action='execute_evidence', reason='deterministic_planner_selected_evidence', planner_output=next_planner_output, planner_key=planner_key)

    if editor_calls_after_latest_evidence > 0:
        no_new_audit = _no_new_evidence_precondition_audit(workspace)
        if bool(no_new_audit.get('no_new_evidence_preconditions_ok')):
            return _InvestigationDecision(action='fail_closed', reason='no_new_evidence_after_editor_and_exhausted_evidence')

    if draft is not None and open_rows and _mapping_draft_has_complete_local_coverage(workspace, draft):
        if editor_calls_after_latest_evidence > 0 and notebook_blockers:
            return _InvestigationDecision(action='judge_semantic_blocker', reason='notebook_open_question_after_editor_requires_semantic_blocker')
        return _InvestigationDecision(action='edit_mapping_draft', reason='open_draft_rows_after_evidence_planning')

    return _InvestigationDecision(action='judge_semantic_blocker', reason='no_deterministic_investigation_action')


def _try_mapping_draft_editor_acceptance_with_workspace(
    workspace: CaseEvidenceWorkspace,
    ai_client,
    judge_outputs: list[CaseJudgeOutput],
    evidence_batches: list[EvidenceBatchResult],
    *,
    repair_depth: int = 0,
    editor_retry_depth: int = 0,
    bangumi_client=None,
) -> _MappingDraftEditorAttempt:
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None or not getattr(draft, 'rows', None):
        workspace = _workspace_with_judge_audit(workspace, {'note': 'mapping_draft_editor_skipped', 'reason': 'no_draft'})
        return _MappingDraftEditorAttempt(None, workspace)
    workspace = _refresh_mapping_draft_candidates(workspace)
    draft = getattr(workspace, 'mapping_draft', None)
    dossier = workspace.to_dossier(round_context='mapping_draft_edit')
    detail_spans = [card for card in (getattr(dossier, 'bangumi_span_cards', []) or []) if bool(getattr(card, 'detail_equivalent', False))]
    special_candidate_rows = _open_special_rows_with_candidates(workspace)
    target_absent_candidate_rows = _open_target_absent_candidate_rows(workspace)
    if not any(row.status == 'open' for row in draft.rows):
        evidence_workspace, editor_evidence_batch = _try_execute_editor_requested_evidence(workspace, _needs_more_evidence_patches_from_draft(draft), bangumi_client, evidence_batches)
        if editor_evidence_batch is not None:
            return _try_mapping_draft_editor_acceptance_with_workspace(
                evidence_workspace,
                ai_client,
                judge_outputs,
                evidence_batches,
                repair_depth=repair_depth,
                bangumi_client=bangumi_client,
            )
        workspace = evidence_workspace
        workspace = _workspace_with_judge_audit(workspace, {'note': 'mapping_draft_editor_skipped', 'reason': 'no_open_rows'})
        return _MappingDraftEditorAttempt(None, workspace)

    coverage_issue = _mapping_draft_local_coverage_issue(workspace, draft)
    if coverage_issue is not None:
        workspace = _workspace_with_judge_audit(workspace, coverage_issue)
        reason = 'mapping_draft_incomplete_local_coverage'
        return _MappingDraftEditorAttempt(CaseAgentRunResult(False, workspace.header.case_id, 'invalid', 'submit_verdict', None, None, workspace, judge_outputs, evidence_batches, reason, [reason]), workspace)

    editor_result = call_mapping_draft_editor(ai_client, dossier, draft, round_kind='mapping_draft_edit', max_provider_retries=0)
    workspace = _workspace_with_judge_audit(workspace, getattr(editor_result, 'request_audit', None))
    workspace = _workspace_with_judge_audit(workspace, {
        'note': 'mapping_draft_editor_called',
        'ok': editor_result.ok,
        'error': editor_result.error,
        'mapping_editor_call_count': 1,
        'editor_call_count_after_latest_evidence': _editor_call_count_after_latest_evidence(workspace),
        'detail_equivalent_span_count': len(detail_spans),
        'special_candidate_row_count': len(special_candidate_rows),
        'target_absent_candidate_row_count': len(target_absent_candidate_rows),
        'pending_special_request_ids': _pending_special_request_ids(workspace),
        'mapping_editor_output_bytes': len(str(getattr(editor_result, 'raw_response', '') or '')),
    })
    if not editor_result.ok or editor_result.output is None:
        editor_error_text = str(editor_result.error or '')
        if 'does not provide a schema-aware mapping draft editor' in editor_error_text or 'transport unavailable' in editor_error_text:
            workspace = _workspace_with_judge_audit(workspace, {
                'note': 'mapping_draft_editor_skipped',
                'reason': 'editor_transport_unavailable',
                'error': editor_error_text,
            })
            return _MappingDraftEditorAttempt(None, workspace)
        if editor_result.error and editor_result.error != 'no-op':
            max_editor_retries = 2
            if editor_retry_depth < max_editor_retries:
                retry_workspace = _workspace_with_judge_audit(
                    workspace,
                    {
                        'note': 'mapping_draft_editor_retry_requested',
                        'retry_depth': editor_retry_depth + 1,
                        'max_editor_retries': max_editor_retries,
                        'error': editor_result.error,
                    },
                )
                return _try_mapping_draft_editor_acceptance_with_workspace(
                    retry_workspace,
                    ai_client,
                    judge_outputs,
                    evidence_batches,
                    repair_depth=repair_depth,
                    editor_retry_depth=editor_retry_depth + 1,
                    bangumi_client=bangumi_client,
                )
            verifier_result = CaseVerifierResult(
                passed=False,
                issues=[
                    VerifierIssue(
                        ref='mapping_draft_editor',
                        issue_code='editor_unavailable',
                        severity='blocked',
                        message='mapping draft editor did not return a valid patch set after retries',
                    )
                ],
                summary='mapping draft editor unavailable',
            )
            return _MappingDraftEditorAttempt(CaseAgentRunResult(False, workspace.header.case_id, 'error', 'edit_mapping_draft', None, verifier_result, workspace, judge_outputs, evidence_batches, 'mapping_draft_editor_unavailable', [editor_result.error]), workspace)
        return _MappingDraftEditorAttempt(None, workspace)

    output = editor_result.output
    workspace = _workspace_with_notebook_updates(workspace, dossier, list(getattr(output, 'notebook_updates', []) or []), source='mapping_draft_editor')
    output = _mapping_editor_output_with_workspace_comparisons(workspace, output)
    output, dropped_non_open_patches = _mapping_editor_output_with_open_row_patches(draft, output)
    if dropped_non_open_patches:
        workspace = _workspace_with_judge_audit(workspace, {
            'note': 'mapping_draft_editor_non_open_row_patches_ignored',
            'patch_count': len(dropped_non_open_patches),
            'local_refs': _dedupe_preserve_order([
                _patch_draft_local_ref(draft, patch)
                for patch in dropped_non_open_patches
                if _patch_draft_local_ref(draft, patch)
            ]),
        })
    editor_patches = _editor_patches_with_comparison_repairs(draft, output, dossier)
    if len(editor_patches) != len(list(output.patches or [])):
        workspace = _workspace_with_judge_audit(workspace, {
            'note': 'mapping_draft_editor_comparison_patches_synthesized',
            'original_patch_count': len(list(output.patches or [])),
            'repaired_patch_count': len(editor_patches),
        })
    editor_patches, dropped_non_open_repair_patches = _filter_mapping_patches_to_open_rows(draft, editor_patches)
    if dropped_non_open_repair_patches:
        workspace = _workspace_with_judge_audit(workspace, {
            'note': 'mapping_draft_editor_non_open_repair_patches_ignored',
            'patch_count': len(dropped_non_open_repair_patches),
            'local_refs': _dedupe_preserve_order([
                _patch_draft_local_ref(draft, patch)
                for patch in dropped_non_open_repair_patches
                if _patch_draft_local_ref(draft, patch)
            ]),
        })
    comparison_issues = _comparison_patch_consistency_issues(draft, output, dossier, editor_patches)
    if comparison_issues and _should_repair_mapping_patch_issues(comparison_issues, repair_depth) and ai_client is not None:
        workspace = _workspace_with_mapping_patch_issue_audit(workspace, comparison_issues)
        repair_base_workspace = _workspace_with_repair_base_draft(
            workspace,
            draft,
            editor_patches,
            comparison_issues,
            dossier,
            note='mapping_draft_comparison_repair_base_preserved',
        )
        repair_workspace = _workspace_with_judge_audit(
            _workspace_preserving_state(repair_base_workspace, verifier_issues=comparison_issues),
            {
                'note': 'mapping_draft_comparison_conflict_repair_requested',
                'issue_count': len(comparison_issues),
                'issue_codes': _dedupe_preserve_order([str(getattr(issue, 'issue_code', '') or '') for issue in comparison_issues]),
                'issue_refs': _dedupe_preserve_order([str(getattr(issue, 'ref', '') or '') for issue in comparison_issues]),
            },
        )
        repair_workspace = _reopen_mapping_draft_issue_rows(repair_workspace, comparison_issues)
        repair_workspace = _refresh_mapping_draft_candidates(repair_workspace)
        repair_attempt = _try_mapping_draft_editor_acceptance_with_workspace(
            repair_workspace,
            ai_client,
            judge_outputs=judge_outputs,
            evidence_batches=evidence_batches,
            repair_depth=repair_depth + 1,
            bangumi_client=bangumi_client,
        )
        if repair_attempt.result is not None:
            return repair_attempt
        workspace = repair_attempt.workspace
    if comparison_issues:
        patch_issues = comparison_issues
        workspace = _workspace_with_mapping_patch_issue_audit(workspace, patch_issues)
        verifier_result = CaseVerifierResult(passed=False, issues=patch_issues, summary='mapping draft comparison conflict')
        fail_output = CaseJudgeOutput(
            action='fail_closed',
            findings=[],
            candidate_comparisons=[],
            fail_closed_reasons=[FailClosedReason(ref='FR1', reason_kind='contradiction', description='mapping draft comparison conflicts with selected patch', related_refs=[])],
            summary='mapping draft comparison conflict',
        )
        verifier_result = verify_judge_output(dossier, fail_output)
        return _MappingDraftEditorAttempt(CaseAgentRunResult(True, workspace.header.case_id, 'fail_closed', 'fail_closed', fail_output, verifier_result, workspace, judge_outputs, evidence_batches, 'semantic_target_conflict', ['mapping_draft_comparison_conflict']), workspace)
    updated_draft, patch_issues = apply_mapping_patches(draft, editor_patches, dossier)
    workspace = _workspace_with_mapping_draft(workspace, updated_draft, patches=editor_patches, candidate_comparisons=list(output.candidate_comparisons or []), note='mapping_draft_editor_patches_applied')
    workspace = _workspace_with_judge_audit(workspace, {
        'note': 'mapping_draft_editor_evidence_intent_observed',
        'evidence_intent_count': _mapping_patch_evidence_intent_count(editor_patches),
    })
    if patch_issues:
        workspace = _workspace_with_mapping_patch_issue_audit(workspace, patch_issues)
        if _should_repair_mapping_patch_issues(patch_issues, repair_depth) and ai_client is not None:
            repair_base_workspace = _workspace_with_repair_base_draft(
                workspace,
                draft,
                editor_patches,
                patch_issues,
                dossier,
                note='mapping_draft_patch_issue_repair_base_preserved',
            )
            repair_workspace = _workspace_with_judge_audit(
                _workspace_preserving_state(repair_base_workspace, verifier_issues=patch_issues),
                {
                    'note': 'mapping_draft_patch_issue_repair_requested',
                    'issue_count': len(patch_issues),
                    'issue_codes': _dedupe_preserve_order([str(getattr(issue, 'issue_code', '') or '') for issue in patch_issues]),
                    'issue_refs': _dedupe_preserve_order([str(getattr(issue, 'ref', '') or '') for issue in patch_issues]),
                },
            )
            repair_workspace = _reopen_mapping_draft_issue_rows(repair_workspace, patch_issues)
            repair_workspace = _refresh_mapping_draft_candidates(repair_workspace)
            repair_attempt = _try_mapping_draft_editor_acceptance_with_workspace(
                repair_workspace,
                ai_client,
                judge_outputs=judge_outputs,
                evidence_batches=evidence_batches,
                repair_depth=repair_depth + 1,
                bangumi_client=bangumi_client,
            )
            if repair_attempt.result is not None:
                return repair_attempt
            workspace = repair_attempt.workspace
        salvage_patches = _salvage_unresolved_mapping_patches(draft, updated_draft, editor_patches, patch_issues, dossier)
        if salvage_patches:
            salvaged_draft, salvage_issues = apply_mapping_patches(updated_draft, salvage_patches, dossier)
            if not salvage_issues:
                workspace = _workspace_with_judge_audit(workspace, {
                    'note': 'mapping_draft_patch_issue_salvaged_as_unresolved',
                    'patch_count': len(salvage_patches),
                    'source_issue_codes': _dedupe_preserve_order([str(getattr(issue, 'issue_code', '') or '') for issue in patch_issues]),
                    'source_issue_refs': _dedupe_preserve_order([str(getattr(issue, 'ref', '') or '') for issue in patch_issues]),
                })
                workspace = _workspace_with_mapping_draft(workspace, salvaged_draft, patches=salvage_patches, note='mapping_draft_salvage_patches_applied')
                finish_attempt = _finish_mapping_draft_after_patches_attempt(
                    workspace,
                    dossier,
                    salvaged_draft,
                    output,
                    judge_outputs,
                    evidence_batches,
                    ai_client=ai_client,
                    repair_depth=repair_depth,
                    bangumi_client=bangumi_client,
                )
                return finish_attempt
        verifier_result = CaseVerifierResult(passed=False, issues=patch_issues, summary='mapping draft patch rejected')
        fail_output = CaseJudgeOutput(
            action='fail_closed',
            findings=[],
            candidate_comparisons=[],
            fail_closed_reasons=[FailClosedReason(ref='FR1', reason_kind='insufficient_evidence', description='mapping draft patch rejected by mechanical validator: ' + ','.join(_dedupe_preserve_order([str(getattr(issue, 'issue_code', '') or '') for issue in patch_issues])), related_refs=[])],
            summary='mapping draft patch rejected',
        )
        verifier_result = verify_judge_output(dossier, fail_output)
        return _MappingDraftEditorAttempt(CaseAgentRunResult(True, workspace.header.case_id, 'fail_closed', 'fail_closed', fail_output, verifier_result, workspace, judge_outputs, evidence_batches, 'no_new_evidence', ['mapping_draft_patch_rejected']), workspace)

    evidence_workspace, editor_evidence_batch = _try_execute_editor_requested_evidence(workspace, editor_patches, bangumi_client, evidence_batches)
    workspace = evidence_workspace
    if editor_evidence_batch is not None:
        return _try_mapping_draft_editor_acceptance_with_workspace(
            workspace,
            ai_client,
            judge_outputs,
            evidence_batches,
            repair_depth=repair_depth,
            bangumi_client=bangumi_client,
        )

    finish_result = _finish_mapping_draft_after_patches(
        workspace,
        dossier,
        updated_draft,
        output,
        judge_outputs,
        evidence_batches,
        ai_client=ai_client,
        repair_depth=repair_depth,
        bangumi_client=bangumi_client,
    )
    if finish_result is None:
        return _MappingDraftEditorAttempt(None, workspace)
    return _MappingDraftEditorAttempt(finish_result, finish_result.final_workspace)


def _has_deferred_subject_recall_intent(workspace: CaseEvidenceWorkspace) -> bool:
    if _workspace_has_bangumi_subjects(workspace):
        return False
    if _has_pending_composed_subject_search_query(workspace):
        return False
    for audit in reversed(list(getattr(workspace, 'judge_request_audits', []) or [])):
        if not isinstance(audit, dict):
            continue
        note = str(audit.get('note') or '')
        if note == 'mapping_draft_editor_evidence_intent_deferred_for_query_composer':
            return True
        if note in {
            'query_composer_added_queries',
            'query_composer_no_executable_queries',
            'planner_selected_menu_request_ids',
            'editor_evidence_menu_resolution',
            'planner_batch_result',
            'evidence_batch_result',
        }:
            return False
    return False


def _try_mapping_draft_editor_acceptance(
    workspace: CaseEvidenceWorkspace,
    ai_client,
    judge_outputs: list[CaseJudgeOutput],
    evidence_batches: list[EvidenceBatchResult],
    *,
    repair_depth: int = 0,
    editor_retry_depth: int = 0,
    bangumi_client=None,
) -> CaseAgentRunResult | None:
    return _try_mapping_draft_editor_acceptance_with_workspace(
        workspace,
        ai_client,
        judge_outputs,
        evidence_batches,
        repair_depth=repair_depth,
        editor_retry_depth=editor_retry_depth,
        bangumi_client=bangumi_client,
    ).result


def _finish_mapping_draft_after_patches_attempt(
    workspace: CaseEvidenceWorkspace,
    dossier,
    updated_draft: MappingDraft,
    output,
    judge_outputs: list[CaseJudgeOutput],
    evidence_batches: list[EvidenceBatchResult],
    *,
    ai_client=None,
    repair_depth: int = 0,
    bangumi_client=None,
) -> _MappingDraftEditorAttempt:
    finish_result = _finish_mapping_draft_after_patches(
        workspace,
        dossier,
        updated_draft,
        output,
        judge_outputs,
        evidence_batches,
        ai_client=ai_client,
        repair_depth=repair_depth,
        bangumi_client=bangumi_client,
    )
    if finish_result is None:
        return _MappingDraftEditorAttempt(None, workspace)
    return _MappingDraftEditorAttempt(finish_result, finish_result.final_workspace)


def _finish_mapping_draft_after_patches(
    workspace: CaseEvidenceWorkspace,
    dossier,
    updated_draft: MappingDraft,
    output,
    judge_outputs: list[CaseJudgeOutput],
    evidence_batches: list[EvidenceBatchResult],
    *,
    ai_client=None,
    repair_depth: int = 0,
    bangumi_client=None,
) -> CaseAgentRunResult | None:
    accounting = compute_mapping_draft_accounting(updated_draft, dossier)
    accounting_verifier_result = verify_mapping_draft_accounting(dossier, updated_draft)
    workspace = _workspace_with_judge_audit(workspace, {
        'note': 'mapping_draft_accounting_computed',
        'mapping_draft_accounting': accounting.model_dump(mode='json') if hasattr(accounting, 'model_dump') else accounting,
    })
    if not accounting_verifier_result.passed:
        structural_result = _try_structural_mapping_draft_repair(
            workspace,
            dossier,
            updated_draft,
            output,
            judge_outputs,
            evidence_batches,
            reason_note='mapping_draft_accounting_structural_repair',
            ai_client=ai_client,
            repair_depth=repair_depth,
            bangumi_client=bangumi_client,
        )
        if structural_result is not None:
            return structural_result
        if _has_special_investigation_rows(workspace) and any(
            str(getattr(issue, 'issue_code', '')).casefold() == 'duplicate_target'
            for issue in list(getattr(accounting_verifier_result, 'issues', []) or [])
        ):
            pending_special = _pending_special_request_ids(workspace)
            if pending_special:
                if (
                    bangumi_client is not None
                    and (workspace.budget.max_evidence_batches == 0 or workspace.budget.used_evidence_batches < workspace.budget.max_evidence_batches)
                ):
                    workspace = _workspace_with_judge_audit(workspace, {
                        'note': 'mapping_draft_pending_special_evidence_continues',
                        'selected_menu_request_ids': pending_special,
                        'reason': 'duplicate target conflict has pending special evidence; execute evidence before semantic fail_closed',
                    })
                    evidence_workspace, evidence_batch = _execute_menu_request_ids(
                        workspace,
                        pending_special,
                        bangumi_client,
                        evidence_batches,
                        note='mapping_draft_pending_special',
                    )
                    workspace = evidence_workspace
                    if evidence_batch is not None and ai_client is not None and repair_depth < _max_mapping_editor_repair_depth():
                        editor_attempt = _try_mapping_draft_editor_acceptance_with_workspace(
                            workspace,
                            ai_client,
                            judge_outputs,
                            evidence_batches,
                            repair_depth=repair_depth + 1,
                            bangumi_client=bangumi_client,
                        )
                        if editor_attempt.result is not None:
                            return editor_attempt.result
                        workspace = editor_attempt.workspace
                    if evidence_batch is not None:
                        finish_attempt = _finish_mapping_draft_after_patches_attempt(
                            workspace,
                            workspace.to_dossier(round_context='mapping_draft_after_pending_special_evidence'),
                            getattr(workspace, 'mapping_draft', updated_draft) or updated_draft,
                            output,
                            judge_outputs,
                            evidence_batches,
                            ai_client=ai_client,
                            repair_depth=repair_depth + 1,
                            bangumi_client=bangumi_client,
                        )
                        if finish_attempt.result is not None:
                            return finish_attempt.result
                        workspace = finish_attempt.workspace
                fail_output = CaseJudgeOutput(
                    action='fail_closed',
                    findings=list(output.findings or []),
                    candidate_comparisons=list(output.candidate_comparisons or []),
                    fail_closed_reasons=[
                        FailClosedReason(
                            ref='FR1',
                            reason_kind='insufficient_evidence',
                            description='special singleton target conflict remains before pending special evidence was executed',
                            related_refs=pending_special[:8],
                        )
                    ],
                    summary='special evidence pending after duplicate target conflict',
                )
                verifier_result = verify_judge_output(dossier, fail_output)
                summary = 'budget_exhausted' if workspace.budget.max_evidence_batches and workspace.budget.used_evidence_batches >= workspace.budget.max_evidence_batches else 'semantic_target_conflict'
                return CaseAgentRunResult(True, workspace.header.case_id, 'fail_closed', 'fail_closed', fail_output, verifier_result, workspace, judge_outputs, evidence_batches, summary, [summary])
        accounting_issues = list(getattr(accounting_verifier_result, 'issues', []) or [])
        accounting_issue_codes = {str(getattr(issue, 'issue_code', '')).casefold() for issue in accounting_issues}
        fail_closed_codes = {'duplicate_target'}
        invalid_codes = {'invalid_target', 'invalid_reason_kind', 'missing_support_refs', 'duplicate_local_ref'}
        if accounting_issue_codes and accounting_issue_codes <= fail_closed_codes and repair_depth < _max_mapping_editor_repair_depth() and ai_client is not None:
            repair_workspace = _workspace_with_judge_audit(
                _workspace_preserving_state(workspace, verifier_issues=accounting_issues),
                {
                    'note': 'mapping_draft_duplicate_target_repair_requested',
                    'issue_count': len(accounting_issues),
                    'issue_refs': _dedupe_preserve_order([str(getattr(issue, 'ref', '') or '') for issue in accounting_issues]),
                },
            )
            repair_workspace = _reopen_mapping_draft_issue_rows(repair_workspace, accounting_issues)
            repair_workspace = _refresh_mapping_draft_candidates(repair_workspace)
            repair_result = _try_mapping_draft_editor_acceptance(
                repair_workspace,
                ai_client,
                judge_outputs=judge_outputs,
                evidence_batches=evidence_batches,
                repair_depth=repair_depth + 1,
                bangumi_client=bangumi_client,
            )
            if repair_result is not None:
                return repair_result
        if accounting_issue_codes and accounting_issue_codes <= fail_closed_codes:
            fail_output = CaseJudgeOutput(
                action='fail_closed',
                findings=list(output.findings or []),
                candidate_comparisons=list(output.candidate_comparisons or []),
                fail_closed_reasons=[
                    FailClosedReason(
                        ref=issue.ref or 'FR1',
                        reason_kind='contradiction',
                        description=getattr(issue, 'message', '') or 'mapping draft target conflict',
                        related_refs=[issue.ref] if getattr(issue, 'ref', '') else [],
                    )
                    for issue in accounting_issues
                ],
                summary='mapping draft target conflict',
            )
            verifier_result = verify_judge_output(dossier, fail_output)
            return CaseAgentRunResult(True, workspace.header.case_id, 'fail_closed', 'fail_closed', fail_output, verifier_result, workspace, judge_outputs, evidence_batches, 'semantic_target_conflict', ['mapping_draft_target_conflict'])
        invalid = any(code in invalid_codes for code in accounting_issue_codes)
        if invalid:
            verifier_result = accounting_verifier_result
            fail_output = CaseJudgeOutput(
                action='fail_closed',
                findings=list(output.findings or []),
                candidate_comparisons=list(output.candidate_comparisons or []),
                fail_closed_reasons=[FailClosedReason(ref=issue.ref or 'FR1', reason_kind='insufficient_evidence' if getattr(issue, 'issue_code', '') != 'duplicate_target' else 'unknown', description=getattr(issue, 'message', '') or 'mapping draft accounting conflict', related_refs=[issue.ref] if getattr(issue, 'ref', '') else []) for issue in accounting_issues],
                summary='mapping draft accounting invalid',
            )
            return CaseAgentRunResult(False, workspace.header.case_id, 'invalid', 'fail_closed', fail_output, verifier_result, workspace, judge_outputs, evidence_batches, 'mapping_draft_accounting_invalid', ['mapping_draft_accounting_invalid'])

        fail_closed_ok = bool(getattr(accounting, 'accepted_accounting_ready', False)) is False and (int(getattr(accounting, 'unresolved_count', 0) or 0) > 0 or int(getattr(accounting, 'needs_more_evidence_file_count', 0) or 0) > 0 or int(getattr(accounting, 'unaligned_file_count', 0) or 0) > 0)
        if fail_closed_ok:
            structural_result = _try_structural_mapping_draft_repair(
                workspace,
                dossier,
                updated_draft,
                output,
                judge_outputs,
                evidence_batches,
                reason_note='mapping_draft_unresolved_structural_repair',
                ai_client=ai_client,
                repair_depth=repair_depth,
                bangumi_client=bangumi_client,
            )
            if structural_result is not None:
                return structural_result
            unresolved_special_issues = [
                *_unresolved_special_candidate_issues(dossier, updated_draft),
                *_unresolved_open_candidate_issues(dossier, updated_draft),
                *_unresolved_supplemental_candidate_issues(dossier, updated_draft),
                *(
                    _unresolved_bangumi_target_absent_candidate_issues(dossier, updated_draft)
                    if _bangumi_target_absent_surface_exhausted(workspace)
                    else []
                ),
            ]
            if unresolved_special_issues and repair_depth < _max_mapping_editor_repair_depth() and ai_client is not None:
                repair_workspace = _workspace_with_judge_audit(
                    _workspace_preserving_state(workspace, verifier_issues=unresolved_special_issues),
                    {
                        'note': 'mapping_draft_unresolved_special_repair_requested',
                        'issue_count': len(unresolved_special_issues),
                        'issue_refs': _dedupe_preserve_order([str(getattr(issue, 'ref', '') or '') for issue in unresolved_special_issues]),
                    },
                )
                repair_workspace = _reopen_mapping_draft_issue_rows(repair_workspace, unresolved_special_issues)
                repair_workspace = _refresh_mapping_draft_candidates(repair_workspace)
                repair_result = _try_mapping_draft_editor_acceptance(
                    repair_workspace,
                    ai_client,
                    judge_outputs=judge_outputs,
                    evidence_batches=evidence_batches,
                    repair_depth=repair_depth + 1,
                    bangumi_client=bangumi_client,
                )
                if repair_result is not None:
                    return repair_result
            no_new_audit = _no_new_evidence_precondition_audit(workspace)
            remaining_target_ids = list(no_new_audit.get('remaining_target_side_executable_request_ids') or [])
            routed_target_ids, phase_audit = _phase_route_remaining_target_side_request_ids(workspace, remaining_target_ids)
            if bool(no_new_audit.get('deferred_subject_recall_intent')):
                workspace = _workspace_with_judge_audit(workspace, {
                    'note': 'mapping_draft_unresolved_deferred_subject_recall_continues',
                    **no_new_audit,
                    'reason': 'editor deferred target-side evidence until subject recall is composed',
                })
                return None
            if remaining_target_ids and not routed_target_ids:
                workspace = _workspace_with_judge_audit(workspace, {
                    'note': 'mapping_draft_unresolved_evidence_deferred',
                    **no_new_audit,
                    **phase_audit,
                    'deferred_menu_request_ids': remaining_target_ids,
                    'reason': 'target-side evidence is waiting for earlier evidence phase prerequisites',
                })
                if str(phase_audit.get('evidence_phase') or '') == 'subject_recall':
                    workspace = _workspace_with_judge_audit(workspace, {
                        'note': 'mapping_draft_editor_evidence_intent_deferred_for_query_composer',
                        'evidence_phase': 'subject_recall',
                        'evidence_intent_count': len(remaining_target_ids),
                        'requested_request_types': ['target_span'],
                        'deferred_evidence_intent_count': len(remaining_target_ids),
                        'target_evidence_blocked_by_missing_subjects_count': int(phase_audit.get('target_evidence_blocked_by_missing_subjects_count') or len(remaining_target_ids)),
                        'reason': 'remaining target-side evidence requires Bangumi subject recall first',
                    })
                    return None
            if (
                not bool(no_new_audit.get('no_new_evidence_preconditions_ok'))
                and routed_target_ids
                and bangumi_client is not None
                and (workspace.budget.max_evidence_batches == 0 or workspace.budget.used_evidence_batches < workspace.budget.max_evidence_batches)
            ):
                workspace = _workspace_with_judge_audit(workspace, {
                    'note': 'mapping_draft_unresolved_evidence_continues',
                    **no_new_audit,
                    **phase_audit,
                    'selected_menu_request_ids': routed_target_ids,
                    'deferred_menu_request_ids': [request_id for request_id in remaining_target_ids if request_id not in set(routed_target_ids)],
                })
                evidence_workspace, evidence_batch = _execute_menu_request_ids(
                    workspace,
                    routed_target_ids,
                    bangumi_client,
                    evidence_batches,
                    note='mapping_draft_unresolved',
                )
                workspace = evidence_workspace
                if evidence_batch is not None and ai_client is not None and repair_depth < _max_mapping_editor_repair_depth():
                    editor_attempt = _try_mapping_draft_editor_acceptance_with_workspace(
                        workspace,
                        ai_client,
                        judge_outputs,
                        evidence_batches,
                        repair_depth=repair_depth + 1,
                        bangumi_client=bangumi_client,
                    )
                    if editor_attempt.result is not None:
                        return editor_attempt.result
                    workspace = editor_attempt.workspace
                    dossier = workspace.to_dossier(round_context='mapping_draft_after_unresolved_evidence')
                    updated_draft = getattr(workspace, 'mapping_draft', updated_draft) or updated_draft
                    accounting = compute_mapping_draft_accounting(updated_draft, dossier)
                    accounting_verifier_result = verify_mapping_draft_accounting(dossier, updated_draft)
                if evidence_batch is not None:
                    finish_attempt = _finish_mapping_draft_after_patches_attempt(
                        workspace,
                        workspace.to_dossier(round_context='mapping_draft_after_unresolved_evidence'),
                        getattr(workspace, 'mapping_draft', updated_draft) or updated_draft,
                        output,
                        judge_outputs,
                        evidence_batches,
                        ai_client=ai_client,
                        repair_depth=repair_depth + 1,
                        bangumi_client=bangumi_client,
                    )
                    if finish_attempt.result is not None:
                        return finish_attempt.result
                    workspace = finish_attempt.workspace
            refreshed_no_new_audit = _no_new_evidence_precondition_audit(workspace)
            if int(refreshed_no_new_audit.get('human_next_action_blocked_no_new_evidence_count') or 0) > 0:
                workspace = _workspace_with_judge_audit(workspace, {
                    'note': 'human_next_action_blocked_no_new_evidence',
                    **refreshed_no_new_audit,
                    'reason': 'investigation notebook still contains an actionable open question or next action',
                })
                return None
            if int(refreshed_no_new_audit.get('durable_draft_evidence_intent_count') or 0) > 0:
                workspace = _workspace_with_judge_audit(workspace, {
                    'note': 'durable_draft_evidence_intent_blocked_no_new_evidence',
                    **refreshed_no_new_audit,
                    'reason': 'mapping draft still carries unresolved evidence intent',
                })
                return None
            reasons = _mapping_draft_unresolved_fail_closed_reasons(updated_draft, dossier)
            if not reasons:
                if int(getattr(accounting, 'unresolved_count', 0) or 0) > 0:
                    reasons.append(FailClosedReason(ref='FR1', reason_kind='insufficient_evidence', description=f'unresolved_count={int(getattr(accounting, "unresolved_count", 0) or 0)}', related_refs=[]))
                if int(getattr(accounting, 'needs_more_evidence_file_count', 0) or 0) > 0:
                    reasons.append(FailClosedReason(ref='FR2', reason_kind='insufficient_evidence', description=f'needs_more_evidence_file_count={int(getattr(accounting, "needs_more_evidence_file_count", 0) or 0)}', related_refs=[]))
                if int(getattr(accounting, 'unaligned_file_count', 0) or 0) > 0:
                    reasons.append(FailClosedReason(ref='FR3', reason_kind='insufficient_evidence', description=f'unaligned_file_count={int(getattr(accounting, "unaligned_file_count", 0) or 0)}', related_refs=[]))
            fail_output = CaseJudgeOutput(
                action='fail_closed',
                findings=[],
                candidate_comparisons=[],
                fail_closed_reasons=reasons,
                summary='mapping draft accounting unresolved',
            )
            verifier_result = verify_judge_output(dossier, fail_output)
            workspace = _workspace_with_judge_audit(workspace, {
                'note': 'mapping_draft_no_new_evidence_preconditions',
                **_no_new_evidence_precondition_audit(workspace),
            })
            return CaseAgentRunResult(True, workspace.header.case_id, 'fail_closed', 'fail_closed', fail_output, verifier_result, workspace, judge_outputs, evidence_batches, 'no_new_evidence', [*([f'unresolved_count={getattr(accounting, "unresolved_count", 0)}'] if int(getattr(accounting, 'unresolved_count', 0) or 0) > 0 else []), *([f'needs_more_evidence_file_count={getattr(accounting, "needs_more_evidence_file_count", 0)}'] if int(getattr(accounting, 'needs_more_evidence_file_count', 0) or 0) > 0 else []), *([f'unaligned_file_count={getattr(accounting, "unaligned_file_count", 0)}'] if int(getattr(accounting, 'unaligned_file_count', 0) or 0) > 0 else [])])

    final_comparison_issues = _final_special_singleton_comparison_issues(dossier, updated_draft, output)
    if final_comparison_issues:
        workspace = _workspace_with_mapping_patch_issue_audit(workspace, final_comparison_issues)
        if _should_repair_mapping_patch_issues(final_comparison_issues, repair_depth) and ai_client is not None:
            repair_workspace = _workspace_with_judge_audit(
                _workspace_preserving_state(workspace, verifier_issues=final_comparison_issues),
                {
                    'note': 'mapping_draft_final_special_comparison_repair_requested',
                    'issue_count': len(final_comparison_issues),
                    'issue_codes': _dedupe_preserve_order([str(getattr(issue, 'issue_code', '') or '') for issue in final_comparison_issues]),
                    'issue_refs': _dedupe_preserve_order([str(getattr(issue, 'ref', '') or '') for issue in final_comparison_issues]),
                },
            )
            repair_workspace = _reopen_mapping_draft_issue_rows(repair_workspace, final_comparison_issues)
            repair_workspace = _refresh_mapping_draft_candidates(repair_workspace)
            repair_result = _try_mapping_draft_editor_acceptance(
                repair_workspace,
                ai_client,
                judge_outputs=judge_outputs,
                evidence_batches=evidence_batches,
                repair_depth=repair_depth + 1,
                bangumi_client=bangumi_client,
            )
            if repair_result is not None:
                return repair_result
        structural_result = _try_structural_mapping_draft_repair(
            workspace,
            dossier,
            updated_draft,
            output,
            judge_outputs,
            evidence_batches,
            reason_note='mapping_draft_final_special_comparison_structural_repair',
            ai_client=ai_client,
            repair_depth=repair_depth,
            bangumi_client=bangumi_client,
        )
        if structural_result is not None:
            return structural_result
        fail_output = CaseJudgeOutput(
            action='fail_closed',
            findings=[],
            candidate_comparisons=[],
            fail_closed_reasons=[
                FailClosedReason(
                    ref=issue.ref or 'FR1',
                    reason_kind='contradiction',
                    description=getattr(issue, 'message', '') or 'special singleton comparison conflict',
                    related_refs=list(getattr(issue, 'related_refs', []) or []),
                )
                for issue in final_comparison_issues
            ],
            summary='mapping draft special singleton comparison conflict',
        )
        verifier_result = verify_judge_output(dossier, fail_output)
        return CaseAgentRunResult(True, workspace.header.case_id, 'fail_closed', 'fail_closed', fail_output, verifier_result, workspace, judge_outputs, evidence_batches, 'semantic_target_conflict', ['mapping_draft_special_comparison_conflict'])

    expanded, expand_issues = expand_mapping_draft(dossier, updated_draft)
    if expand_issues:
        expansion_issue_codes = {str(getattr(issue, 'issue_code', '') or '').casefold() for issue in expand_issues}
        repairable_expansion_codes = {'duplicate_target', 'count_mismatch', 'missing_support_refs', 'invalid_target', 'missing_span_ref', 'duplicate_local_span', 'invalid_explicit_multi_file_mapping'}
        if expansion_issue_codes and expansion_issue_codes <= repairable_expansion_codes and repair_depth < _max_mapping_editor_repair_depth() and ai_client is not None:
            repair_workspace = _workspace_with_judge_audit(
                _workspace_preserving_state(workspace, verifier_issues=expand_issues),
                {
                    'note': 'mapping_draft_expansion_repair_requested',
                    'issue_count': len(expand_issues),
                    'issue_codes': _dedupe_preserve_order([str(getattr(issue, 'issue_code', '') or '') for issue in expand_issues]),
                    'issue_refs': _dedupe_preserve_order([str(getattr(issue, 'ref', '') or '') for issue in expand_issues]),
                },
            )
            repair_workspace = _reopen_mapping_draft_issue_rows(repair_workspace, expand_issues)
            repair_workspace = _refresh_mapping_draft_candidates(repair_workspace)
            repair_result = _try_mapping_draft_editor_acceptance(
                repair_workspace,
                ai_client,
                judge_outputs=judge_outputs,
                evidence_batches=evidence_batches,
                repair_depth=repair_depth + 1,
                bangumi_client=bangumi_client,
            )
            if repair_result is not None:
                return repair_result
        structural_result = _try_structural_mapping_draft_repair(
            workspace,
            dossier,
            updated_draft,
            output,
            judge_outputs,
            evidence_batches,
            reason_note='mapping_draft_expansion_structural_repair',
            ai_client=ai_client,
            repair_depth=repair_depth,
            bangumi_client=bangumi_client,
        )
        if structural_result is not None:
            return structural_result
        verifier_result = CaseVerifierResult(passed=False, issues=expand_issues, summary='mapping draft expansion failed')
        fail_output = CaseJudgeOutput(
            action='fail_closed',
            findings=[],
            candidate_comparisons=[],
            fail_closed_reasons=[
                FailClosedReason(
                    ref='FR1',
                    reason_kind='insufficient_evidence',
                    description='mapping draft expansion failed: ' + ','.join(_dedupe_preserve_order([str(getattr(issue, 'issue_code', '') or '') for issue in expand_issues])),
                    related_refs=_dedupe_preserve_order([ref for issue in expand_issues for ref in list(getattr(issue, 'related_refs', []) or []) if ref])[:8],
                )
            ],
            summary='mapping draft expansion failed',
        )
        verifier_result = verify_judge_output(dossier, fail_output)
        return CaseAgentRunResult(True, workspace.header.case_id, 'fail_closed', 'fail_closed', fail_output, verifier_result, workspace, judge_outputs, evidence_batches, 'semantic_target_conflict', ['mapping_draft_expansion_failed'])

    if not expanded:
        if output.fail_closed_reasons:
            fail_output = CaseJudgeOutput(
                action='fail_closed',
                findings=[],
                candidate_comparisons=[],
                fail_closed_reasons=list(output.fail_closed_reasons or []),
                summary='mapping draft editor unresolved',
            )
            verifier_result = verify_judge_output(dossier, fail_output)
            return CaseAgentRunResult(True, workspace.header.case_id, 'fail_closed', 'fail_closed', fail_output, verifier_result, workspace, judge_outputs, evidence_batches, 'mapping_draft_editor_unresolved', ['mapping_draft_editor_unresolved'])
        return None

    sanitized_output = _sanitize_mapping_editor_output_refs(output, dossier)
    accepted_findings = list(sanitized_output.findings or [])
    if not accepted_findings:
        accepted_findings = [Finding(ref='F_MAP1', finding_kind='pass', description='mapping draft accounting accepted')]
    accepted_output = CaseJudgeOutput(
        action='submit_verdict',
        findings=accepted_findings,
        candidate_comparisons=list(sanitized_output.candidate_comparisons or []),
        assignment_intents=_with_mapping_draft_support_findings(expanded, sanitized_output.model_copy(update={'findings': accepted_findings})),
        self_checks=[],
        summary='accepted from mapping draft',
    )

    verifier_result = verify_judge_output(dossier, accepted_output)
    if verifier_result.passed:
        return CaseAgentRunResult(True, workspace.header.case_id, 'accepted', 'submit_verdict', accepted_output, verifier_result, workspace, judge_outputs, evidence_batches, 'accepted_from_mapping_draft', [])
    return CaseAgentRunResult(False, workspace.header.case_id, 'invalid', 'submit_verdict', accepted_output, verifier_result, workspace, judge_outputs, evidence_batches, 'mapping_draft_verifier_rejected', ['mapping_draft_verifier_rejected'])


def _sanitize_mapping_editor_output_refs(output, dossier):
    visible_refs = {
        *list(getattr(getattr(dossier, 'visible_refs', None), 'local_file_refs', []) or []),
        *list(getattr(getattr(dossier, 'visible_refs', None), 'local_cluster_refs', []) or []),
        *list(getattr(getattr(dossier, 'visible_refs', None), 'bangumi_subject_refs', []) or []),
        *list(getattr(getattr(dossier, 'visible_refs', None), 'bangumi_relation_refs', []) or []),
        *list(getattr(getattr(dossier, 'visible_refs', None), 'bangumi_group_refs', []) or []),
        *list(getattr(getattr(dossier, 'visible_refs', None), 'bangumi_item_refs', []) or []),
        *list(getattr(getattr(dossier, 'visible_refs', None), 'query_refs', []) or []),
        *list(getattr(getattr(dossier, 'visible_refs', None), 'target_refs', []) or []),
        *[card.ref for card in list(getattr(dossier, 'local_span_cards', []) or []) if getattr(card, 'ref', '')],
        *[card.ref for card in list(getattr(dossier, 'bangumi_span_cards', []) or []) if getattr(card, 'ref', '')],
        *list(getattr(dossier, 'seen_detail_refs', []) or []),
        *list(getattr(dossier, 'assignable_target_refs', []) or []),
        *list(getattr(dossier, 'detailed_card_refs', []) or []),
    }

    def _filter_refs(values: list[str]) -> list[str]:
        return [ref for ref in list(values or []) if ref in visible_refs]

    def _filter_aux_refs(values: list[str]) -> list[str]:
        return _compact_mapping_editor_aux_refs(_filter_refs(values))

    findings = [
        finding.model_copy(update={'evidence_refs': _filter_aux_refs(list(getattr(finding, 'evidence_refs', []) or []))})
        for finding in list(getattr(output, 'findings', []) or [])
    ]
    comparisons = [
        comparison.model_copy(update={'evidence_refs': _filter_aux_refs(list(getattr(comparison, 'evidence_refs', []) or []))})
        for comparison in list(getattr(output, 'candidate_comparisons', []) or [])
    ]
    self_checks = []
    for self_check in list(getattr(output, 'self_checks', []) or []):
        check_findings = [
            finding.model_copy(update={'evidence_refs': _filter_aux_refs(list(getattr(finding, 'evidence_refs', []) or []))})
            for finding in list(getattr(self_check, 'findings', []) or [])
        ]
        self_checks.append(self_check.model_copy(update={'findings': check_findings}))
    return output.model_copy(update={
        'findings': findings,
        'candidate_comparisons': comparisons,
        'self_checks': self_checks,
    })


def _compact_mapping_editor_aux_refs(values: list[str], *, max_refs: int = 12) -> list[str]:
    refs = _dedupe_preserve_order(list(values or []))
    if len(refs) <= max_refs:
        return refs

    local_refs: list[str] = []
    target_span_refs: list[str] = []
    target_refs: list[str] = []
    other_refs: list[str] = []
    for ref in refs:
        if ref.startswith(('LF', 'LS', 'LC')):
            local_refs.append(ref)
        elif ref.startswith('BES'):
            target_span_refs.append(ref)
        elif ref.startswith(('BE', 'BS', 'BR')):
            target_refs.append(ref)
        else:
            other_refs.append(ref)

    compact = [
        *local_refs[:4],
        *target_span_refs[:4],
        *target_refs[:2],
        *other_refs[:2],
    ]
    if len(compact) < max_refs:
        compact.extend(
            ref
            for ref in [*local_refs[4:], *target_span_refs[4:], *target_refs[2:], *other_refs[2:]]
            if ref not in compact
        )
    return compact[:max_refs]


def _mapping_draft_unresolved_fail_closed_reasons(draft: MappingDraft, dossier) -> list[FailClosedReason]:
    local_spans = {
        str(getattr(card, 'ref', '') or ''): card
        for card in list(getattr(dossier, 'local_span_cards', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }
    reasons: list[FailClosedReason] = []
    for row in list(getattr(draft, 'rows', []) or []):
        disposition = str(getattr(row, 'disposition', '') or '')
        if disposition not in {'needs_more_evidence', 'unaligned_fail_closed', 'open'}:
            continue
        local_ref = str(getattr(row, 'local_ref', '') or '')
        span = local_spans.get(local_ref)
        file_refs = list(getattr(span, 'file_refs', []) or []) if span is not None else []
        candidate_refs = list(getattr(row, 'candidate_target_refs', []) or [])
        support_refs = list(getattr(row, 'support_refs', []) or [])
        reason_kind = str(getattr(row, 'reason_kind', '') or '')
        row_reason = str(getattr(row, 'reason', '') or '').strip()
        description_parts = [
            f'{local_ref} remains {disposition}',
            f'reason_kind={reason_kind or "unknown"}',
        ]
        if row_reason:
            description_parts.append(f'reason={row_reason}')
        if candidate_refs:
            description_parts.append(f'candidate_refs={",".join(candidate_refs[:8])}')
        related_refs = _compact_fail_closed_related_refs(_dedupe_preserve_order([local_ref, *file_refs[:6], *support_refs[:6], *candidate_refs[:8]]))
        reasons.append(FailClosedReason(
            ref=str(getattr(row, 'row_ref', '') or local_ref or f'FR{len(reasons) + 1}'),
            reason_kind='insufficient_evidence' if disposition in {'needs_more_evidence', 'open'} else 'contradiction',
            description='; '.join(description_parts),
            related_refs=related_refs,
        ))
    return reasons[:12]


def _try_structural_mapping_draft_repair(
    workspace: CaseEvidenceWorkspace,
    dossier,
    draft: MappingDraft,
    output,
    judge_outputs: list[CaseJudgeOutput],
    evidence_batches: list[EvidenceBatchResult],
    *,
    reason_note: str,
    ai_client=None,
    repair_depth: int = 0,
    bangumi_client=None,
) -> CaseAgentRunResult | None:
    if any(isinstance(audit, dict) and audit.get('note') == reason_note for audit in list(getattr(workspace, 'judge_request_audits', []) or [])):
        return None
    if ai_client is None or repair_depth >= _max_mapping_editor_repair_depth():
        return None
    undermined_comparison_refs = _comparison_refs_with_undermined_winner(output)
    structural_patches = _structural_supplemental_span_conflict_patches(
        draft,
        dossier,
        force_local_refs=undermined_comparison_refs,
    )
    if not structural_patches:
        structural_patches = _structural_unique_span_patches(draft, dossier)
    if not structural_patches:
        structural_patches = _structural_special_singleton_mismatch_patches(draft, dossier)
    if not structural_patches:
        structural_patches = _structural_unresolved_supplemental_patches(draft, dossier)
    if not structural_patches:
        structural_patches = _structural_open_span_completion_patches(draft, dossier)
    if not structural_patches:
        return None
    guidance_issues = [
        VerifierIssue(
            ref=str(getattr(patch, 'local_ref', '') or f'structural_repair_{index}'),
            issue_code='structural_repair_guidance',
            severity='blocked',
            message='mechanical structural repair candidate exists; MappingDraftEditor must choose the final semantic disposition',
            related_refs=_dedupe_preserve_order([
                str(getattr(patch, 'local_ref', '') or ''),
                str(getattr(patch, 'target_span_ref', '') or ''),
                str(getattr(patch, 'target_ref', '') or ''),
                *[str(ref or '') for ref in list(getattr(patch, 'support_refs', []) or [])],
            ])[:8],
        )
        for index, patch in enumerate(structural_patches, start=1)
    ]
    workspace = _workspace_with_judge_audit(workspace, {
        'note': reason_note,
        'guidance_count': len(guidance_issues),
        'candidate_patch_count': len(structural_patches),
        'reason': 'structural helper emitted guidance only; no semantic mapping patch was applied mechanically',
    })
    repair_workspace = _workspace_preserving_state(workspace, verifier_issues=guidance_issues)
    repair_workspace = _reopen_mapping_draft_issue_rows(repair_workspace, guidance_issues)
    repair_workspace = _refresh_mapping_draft_candidates(repair_workspace)
    return _try_mapping_draft_editor_acceptance(
        repair_workspace,
        ai_client,
        judge_outputs=judge_outputs,
        evidence_batches=evidence_batches,
        repair_depth=repair_depth + 1,
        bangumi_client=bangumi_client,
    )


def _with_mapping_draft_support_findings(assignments: list, output: CaseJudgeOutput) -> list:
    finding_refs = [finding.ref for finding in list(output.findings or []) if getattr(finding, 'ref', '')]
    if not finding_refs:
        return assignments
    primary_finding_ref = finding_refs[0]
    enriched = []
    for assignment in assignments:
        support_finding_refs = list(getattr(assignment, 'support_finding_refs', []) or [])
        if not support_finding_refs:
            support_finding_refs = [primary_finding_ref]
        enriched.append(assignment.model_copy(update={'support_finding_refs': support_finding_refs}))
    return enriched


def _compact_final_assignment_support_refs(assignments: list) -> list:
    compacted = []
    for assignment in list(assignments or []):
        file_ref = str(getattr(assignment, 'file_ref', '') or '')
        target_ref = str(getattr(assignment, 'target_ref', '') or '')
        support_refs = [file_ref]
        if target_ref and target_ref != 'UNALIGNED':
            support_refs.append(target_ref)
        else:
            support_refs.extend(
                str(ref or '') for ref in list(getattr(assignment, 'support_card_refs', []) or [])
                if str(ref or '').startswith(('LS', 'LC'))
            )
        compacted.append(assignment.model_copy(update={
            'support_card_refs': _dedupe_preserve_order(support_refs),
        }))
    return compacted


def _mapping_draft_local_coverage_issue(workspace: CaseEvidenceWorkspace, draft: MappingDraft) -> dict[str, object] | None:
    coverage = compute_local_span_partition_coverage(workspace, draft)
    if not coverage['main_file_count']:
        return None
    if not coverage['missing_main_file_count'] and not coverage['overlap_count']:
        return None
    # Keep the preflight gate strict: every main file must be represented by exactly one draft row.
    return {
        'note': 'mapping_draft_incomplete_local_coverage',
        **coverage,
    }


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _legacy_needed_evidence_request_types(needed_evidence_type: str) -> list[str]:
    value = str(needed_evidence_type or '').strip().casefold()
    if not value:
        return []
    if value in {'target_detail', 'episode_detail', 'item_detail'}:
        return ['target_detail', 'episode_detail']
    if value in {'subject_search', 'subject_recall'}:
        return ['subject_search']
    if value in {'subject_lookup', 'subject_detail'}:
        return ['subject_lookup']
    if value in {'related_expansion', 'relations', 'related'}:
        return ['related_expansion']
    if value in {'episode_list', 'episodes', 'special_episode_list'}:
        return ['episode_list']
    if value in {'target_span', 'span_proof'}:
        return ['target_span']
    if value in {'target_window', 'window'}:
        return ['target_window']
    return []


def _patch_evidence_request_types(patch: MappingDraftPatch) -> list[str]:
    requested = [str(value or '') for value in list(getattr(patch, 'requested_request_types', []) or []) if str(value or '')]
    requested.extend(_legacy_needed_evidence_request_types(str(getattr(patch, 'needed_evidence_type', '') or '')))
    return _dedupe_preserve_order(requested)


def _mapping_patch_evidence_intent_count(patches: list[MappingDraftPatch]) -> int:
    count = 0
    for patch in list(patches or []):
        normalized = normalize_mapping_patch_op(patch)
        if str(getattr(normalized, 'op', '') or '') != 'needs_more_evidence':
            continue
        if (
            list(getattr(normalized, 'menu_request_ids', []) or [])
            or _patch_evidence_request_types(normalized)
            or list(getattr(normalized, 'query_hints', []) or [])
            or list(getattr(normalized, 'subject_refs', []) or [])
            or list(getattr(normalized, 'item_refs', []) or [])
            or list(getattr(normalized, 'local_refs', []) or [])
        ):
            count += 1
    return count


def _needs_more_evidence_patches_from_draft(draft: MappingDraft | None) -> list[MappingDraftPatch]:
    patches: list[MappingDraftPatch] = []
    if draft is None:
        return patches
    for row in list(getattr(draft, 'rows', []) or []):
        if str(getattr(row, 'disposition', '') or '') != 'needs_more_evidence':
            continue
        local_ref = str(getattr(row, 'local_ref', '') or '')
        if not local_ref:
            continue
        patches.append(MappingDraftPatch(
            op='needs_more_evidence',
            local_ref=local_ref,
            requested_request_types=list(getattr(row, 'requested_request_types', []) or []),
            query_hints=list(getattr(row, 'query_hints', []) or []),
            subject_refs=list(getattr(row, 'subject_refs', []) or []),
            item_refs=list(getattr(row, 'item_refs', []) or []),
            local_refs=list(getattr(row, 'local_refs', []) or [local_ref]),
            support_refs=list(getattr(row, 'support_refs', []) or []),
            reason_kind=str(getattr(row, 'reason_kind', '') or ''),
            reason=str(getattr(row, 'reason', '') or ''),
        ))
    return patches


def _durable_draft_evidence_intent_count(draft: MappingDraft | None) -> int:
    count = 0
    for row in list(getattr(draft, 'rows', []) or []):
        if str(getattr(row, 'disposition', '') or '') != 'needs_more_evidence':
            continue
        if (
            list(getattr(row, 'requested_request_types', []) or [])
            or list(getattr(row, 'query_hints', []) or [])
            or list(getattr(row, 'subject_refs', []) or [])
            or list(getattr(row, 'item_refs', []) or [])
            or list(getattr(row, 'local_refs', []) or [])
        ):
            count += 1
    return count


def _next_query_card_index(query_cards: list[QueryCard]) -> int:
    max_index = 0
    for card in list(query_cards or []):
        ref = str(getattr(card, 'ref', '') or '')
        match = re.fullmatch(r'QC(\d+)', ref)
        if match:
            max_index = max(max_index, int(match.group(1)))
    return max_index + 1


def _workspace_with_editor_query_hints(workspace: CaseEvidenceWorkspace, patches: list[MappingDraftPatch]) -> tuple[CaseEvidenceWorkspace, list[str]]:
    visible_local_refs = set(getattr(workspace.visible_refs(), 'local_file_refs', []) or [])
    visible_local_refs.update(str(getattr(card, 'ref', '') or '') for card in list(getattr(workspace, 'local_span_cards', []) or []) if str(getattr(card, 'ref', '') or ''))
    existing_texts = {
        str(getattr(card, 'query_text', '') or '').strip().casefold()
        for card in list(getattr(workspace, 'query_cards', []) or [])
        if str(getattr(card, 'query_text', '') or '').strip()
    }
    next_index = _next_query_card_index(list(getattr(workspace, 'query_cards', []) or []))
    query_cards: list[QueryCard] = []
    query_refs: list[str] = []
    for patch in list(patches or []):
        normalized = normalize_mapping_patch_op(patch)
        if str(getattr(normalized, 'op', '') or '') != 'needs_more_evidence':
            continue
        source_refs = _dedupe_preserve_order([
            str(getattr(normalized, 'local_ref', '') or ''),
            *[str(ref or '') for ref in list(getattr(normalized, 'local_refs', []) or [])],
            *[str(ref or '') for ref in list(getattr(normalized, 'support_refs', []) or [])],
        ])
        source_refs = [ref for ref in source_refs if ref in visible_local_refs]
        if not source_refs and str(getattr(normalized, 'local_ref', '') or '') in visible_local_refs:
            source_refs = [str(getattr(normalized, 'local_ref', '') or '')]
        for raw_hint in list(getattr(normalized, 'query_hints', []) or []):
            query_text = re.sub(r'\s+', ' ', str(raw_hint or '').strip())
            if not query_text:
                continue
            key = query_text.casefold()
            if key in existing_texts:
                continue
            ref = f'QC{next_index}'
            next_index += 1
            existing_texts.add(key)
            query_refs.append(ref)
            query_cards.append(QueryCard(
                ref=ref,
                query_text=query_text,
                query_kind='subject_search',
                query_origin='agent_composed',
                source_refs=source_refs,
                reason='mapping editor requested subject recall query',
                confidence='medium',
            ))
    if not query_cards:
        return workspace, []
    updated = workspace.with_query_cards(query_cards)
    updated = _workspace_with_judge_audit(updated, {
        'note': 'mapping_draft_editor_query_hints_materialized',
        'query_refs': [card.ref for card in query_cards],
        'query_texts': [card.query_text for card in query_cards],
    })
    return updated, query_refs


def _intent_source_refs_for_patch(patch: MappingDraftPatch, materialized_query_refs: list[str]) -> set[str]:
    return {
        ref
        for ref in [
            str(getattr(patch, 'local_ref', '') or ''),
            *[str(value or '') for value in list(getattr(patch, 'local_refs', []) or [])],
            *[str(value or '') for value in list(getattr(patch, 'subject_refs', []) or [])],
            *[str(value or '') for value in list(getattr(patch, 'item_refs', []) or [])],
            *[str(value or '') for value in list(getattr(patch, 'support_refs', []) or [])],
            *materialized_query_refs,
        ]
        if ref
    }


def _request_summary_matches_editor_intent(summary: dict[str, object], patch: MappingDraftPatch, materialized_query_refs: list[str]) -> bool:
    request_type = str(summary.get('request_type') or '')
    requested_types = set(_patch_evidence_request_types(patch))
    if requested_types and request_type not in requested_types:
        return False
    intent_sources = _intent_source_refs_for_patch(patch, materialized_query_refs)
    if not intent_sources:
        return True
    summary_sources = {str(ref or '') for ref in list(summary.get('source_refs') or []) if str(ref or '')}
    return bool(summary_sources & intent_sources)


def _plan_kind_for_editor_request_ids(menu_summaries: list[dict[str, object]], selected_ids: list[str]) -> str:
    request_type_by_id = {
        str(summary.get('request_id') or ''): str(summary.get('request_type') or '')
        for summary in list(menu_summaries or [])
    }
    selected_types = {request_type_by_id.get(request_id, '') for request_id in selected_ids}
    if 'subject_search' in selected_types or 'subject_lookup' in selected_types:
        return 'subject_recall'
    if 'related_expansion' in selected_types:
        return 'special_recall'
    if 'episode_list' in selected_types or 'episode_detail' in selected_types or 'target_detail' in selected_types:
        return 'episode_recall'
    return 'span_proof'


def _editor_evidence_plan_from_patches(workspace: CaseEvidenceWorkspace, patches: list[MappingDraftPatch]) -> tuple[CaseEvidenceWorkspace, EvidencePlannerOutput | None]:
    intent_patches = [
        normalize_mapping_patch_op(patch)
        for patch in list(patches or [])
        if str(getattr(normalize_mapping_patch_op(patch), 'op', '') or '') == 'needs_more_evidence'
    ]
    if not intent_patches:
        return workspace, None
    workspace, materialized_query_refs = _workspace_with_editor_query_hints(workspace, intent_patches)
    requested_request_types = _dedupe_preserve_order([request_type for patch in intent_patches for request_type in _patch_evidence_request_types(patch)])
    if (
        any(request_type in _REQUIRES_SUBJECT_EVIDENCE_TYPES for request_type in requested_request_types)
        and not _workspace_has_bangumi_subjects(workspace)
        and not _workspace_has_bangumi_items(workspace)
        and not _has_pending_composed_subject_search_query(workspace)
        and not materialized_query_refs
    ):
        workspace = _workspace_with_judge_audit(workspace, {
            'note': 'mapping_draft_editor_evidence_intent_deferred_for_query_composer',
            'evidence_phase': 'subject_recall',
            'evidence_intent_count': _mapping_patch_evidence_intent_count(intent_patches),
            'requested_request_types': requested_request_types,
            'deferred_evidence_intent_count': len(intent_patches),
            'target_evidence_blocked_by_missing_subjects_count': len(intent_patches),
        })
        return workspace, None
    menu = build_executable_evidence_menu(workspace)
    summaries = list(menu.get('prompt_summaries') or [])
    if (
        any(request_type in _REQUIRES_SUBJECT_EVIDENCE_TYPES for request_type in requested_request_types)
        and not _workspace_has_bangumi_subjects(workspace)
        and not _workspace_has_bangumi_items(workspace)
        and not any(str(summary.get('request_type') or '') == 'subject_search' for summary in summaries)
    ):
        workspace = _workspace_with_judge_audit(workspace, {
            'note': 'mapping_draft_editor_evidence_intent_deferred_for_query_composer',
            'evidence_phase': 'subject_recall',
            'evidence_intent_count': _mapping_patch_evidence_intent_count(intent_patches),
            'requested_request_types': requested_request_types,
            'deferred_evidence_intent_count': len(intent_patches),
            'target_evidence_blocked_by_missing_subjects_count': len(intent_patches),
            'reason': 'no executable subject_search request is available yet',
        })
        return workspace, None
    selected_ids: list[str] = []
    stale_ids: list[str] = []
    for patch in intent_patches:
        for request_id in list(getattr(patch, 'menu_request_ids', []) or []):
            request_id = str(request_id or '')
            if request_id:
                fresh, stale = _filter_stale_menu_request_ids(workspace, [request_id])
                selected_ids.extend(fresh)
                stale_ids.extend(stale)
        if not _patch_evidence_request_types(patch) and not list(getattr(patch, 'query_hints', []) or []):
            continue
        for summary in summaries:
            request_id = str(summary.get('request_id') or '')
            if not request_id:
                continue
            if _request_summary_matches_editor_intent(summary, patch, materialized_query_refs):
                selected_ids.append(request_id)
    selected_ids = _dedupe_preserve_order(selected_ids)
    selected_ids, phase_audit = _evidence_phase_request_ids_for_editor_intent(
        workspace,
        summaries,
        selected_ids,
        requested_request_types,
        subject_refs=_subject_refs_from_intent_patches(intent_patches),
    )
    stale_ids = _dedupe_preserve_order(stale_ids)
    if stale_ids:
        workspace = _workspace_with_judge_audit(workspace, {
            'note': 'mapping_draft_editor_stale_menu_request_ids_ignored',
            'stale_menu_request_ids': stale_ids,
        })
    if workspace.budget.max_requests_per_batch:
        selected_ids = selected_ids[:workspace.budget.max_requests_per_batch]
    if not selected_ids:
        workspace = _workspace_with_judge_audit(workspace, {
            'note': 'mapping_draft_editor_evidence_intent_no_executable_request',
            'evidence_intent_count': _mapping_patch_evidence_intent_count(intent_patches),
            'requested_request_types': requested_request_types,
            **phase_audit,
        })
        return workspace, None
    plan = EvidencePlan(
        plan_id='EDITOR_INTENT_1',
        plan_kind=_plan_kind_for_editor_request_ids(summaries, selected_ids),
        selected_menu_request_ids=selected_ids,
        plan_status='in_progress',
        goal='execute MappingDraftEditor needs_more_evidence intent',
    )
    workspace = _workspace_with_judge_audit(workspace, {
        'note': 'mapping_draft_editor_evidence_intent_selected',
        'evidence_intent_count': _mapping_patch_evidence_intent_count(intent_patches),
        'selected_menu_request_ids': selected_ids,
        'requested_request_types': requested_request_types,
        'query_refs': materialized_query_refs,
        **phase_audit,
    })
    return workspace, EvidencePlannerOutput(selected_evidence=True, plan=plan)


def _try_execute_editor_requested_evidence(
    workspace: CaseEvidenceWorkspace,
    patches: list[MappingDraftPatch],
    bangumi_client,
    evidence_batches: list[EvidenceBatchResult],
) -> tuple[CaseEvidenceWorkspace, EvidenceBatchResult | None]:
    if bangumi_client is None:
        return workspace, None
    if workspace.budget.max_evidence_batches and workspace.budget.used_evidence_batches >= workspace.budget.max_evidence_batches:
        return workspace, None
    workspace, planner_output = _editor_evidence_plan_from_patches(workspace, patches)
    if planner_output is None or planner_output.plan is None or not planner_output.selected_evidence:
        return workspace, None
    return _execute_menu_request_ids(
        workspace,
        list(planner_output.plan.selected_menu_request_ids or []),
        bangumi_client,
        evidence_batches,
        note='editor_evidence',
        planner_output=planner_output,
    )


def _supplemental_policy_allows_patch(dossier, draft: MappingDraft, patch: MappingDraftPatch) -> bool:
    local_ref = str(getattr(patch, 'local_ref', '') or '')
    if not local_ref:
        return False
    row = next((item for item in list(getattr(draft, 'rows', []) or []) if str(getattr(item, 'local_ref', '') or '') == local_ref), None)
    if row is None:
        return False
    policy_row = row.model_copy(update={
        'disposition': 'non_bangumi_or_supplemental',
        'reason_kind': str(getattr(patch, 'reason_kind', '') or ''),
        'reason': str(getattr(patch, 'reason', '') or ''),
        'support_refs': list(getattr(patch, 'support_refs', []) or []),
    })
    return not supplemental_row_policy_issues(dossier, policy_row)


def _compact_verifier_issues(issues: list[VerifierIssue]) -> list[dict[str, object]]:
    return [
        {
            'ref': str(getattr(issue, 'ref', '') or ''),
            'issue_code': str(getattr(issue, 'issue_code', '') or ''),
            'severity': str(getattr(issue, 'severity', '') or ''),
            'message': str(getattr(issue, 'message', '') or ''),
            'related_refs': list(getattr(issue, 'related_refs', []) or []),
        }
        for issue in list(issues or [])
    ]


def _workspace_with_mapping_patch_issue_audit(workspace: CaseEvidenceWorkspace, patch_issues: list[VerifierIssue]) -> CaseEvidenceWorkspace:
    if not patch_issues:
        return workspace
    return _workspace_with_judge_audit(workspace, {
        'note': 'mapping_draft_patch_issues',
        'issue_count': len(patch_issues),
        'issue_codes': _dedupe_preserve_order([str(getattr(issue, 'issue_code', '') or '') for issue in patch_issues]),
        'issue_refs': _dedupe_preserve_order([str(getattr(issue, 'ref', '') or '') for issue in patch_issues]),
        'issues': _compact_verifier_issues(patch_issues),
    })


def _issue_local_refs(draft: MappingDraft, issues: list[VerifierIssue]) -> set[str]:
    rows_by_ref = {
        key: str(getattr(row, 'local_ref', '') or '')
        for row in list(getattr(draft, 'rows', []) or [])
        for key in (str(getattr(row, 'row_ref', '') or ''), str(getattr(row, 'local_ref', '') or ''))
        if key
    }
    refs: set[str] = set()
    for issue in list(issues or []):
        ref = str(getattr(issue, 'ref', '') or '')
        if ref in rows_by_ref:
            refs.add(rows_by_ref[ref])
        for related in list(getattr(issue, 'related_refs', []) or []):
            related_ref = str(related or '')
            if related_ref in rows_by_ref:
                refs.add(rows_by_ref[related_ref])
    return {ref for ref in refs if ref}


def _workspace_with_repair_base_draft(
    workspace: CaseEvidenceWorkspace,
    draft: MappingDraft,
    patches: list[MappingDraftPatch],
    issues: list[VerifierIssue],
    dossier,
    *,
    note: str,
) -> CaseEvidenceWorkspace:
    issue_local_refs = _issue_local_refs(draft, issues)
    if not issue_local_refs:
        return workspace
    safe_patches: list[MappingDraftPatch] = []
    rows_by_ref = {
        str(getattr(row, 'row_ref', '') or ''): str(getattr(row, 'local_ref', '') or '')
        for row in list(getattr(draft, 'rows', []) or [])
        if str(getattr(row, 'row_ref', '') or '')
    }
    for patch in list(patches or []):
        normalized = normalize_mapping_patch_op(patch)
        local_ref = str(getattr(normalized, 'local_ref', '') or '')
        local_ref = rows_by_ref.get(local_ref, local_ref)
        if not local_ref or local_ref in issue_local_refs:
            continue
        safe_patches.append(patch)
    if not safe_patches:
        return workspace
    repair_base_draft, safe_issues = apply_mapping_patches(draft, safe_patches, dossier)
    if safe_issues:
        return workspace
    return _workspace_with_mapping_draft(workspace, repair_base_draft, patches=safe_patches, note=note)


def _comparison_row_ref(comparison) -> str:
    return str(getattr(comparison, 'ref', '') or '')


def _merge_mapping_draft_candidate_comparisons(existing: list, incoming: list) -> list:
    merged_by_row: dict[str, object] = {}
    passthrough: list[object] = []
    for comparison in [*list(existing or []), *list(incoming or [])]:
        row_ref = _comparison_row_ref(comparison)
        if row_ref:
            merged_by_row[row_ref] = comparison
        else:
            passthrough.append(comparison)
    return [*passthrough, *merged_by_row.values()]


def _mapping_editor_output_with_workspace_comparisons(workspace: CaseEvidenceWorkspace, output):
    existing = list(getattr(workspace, 'mapping_draft_candidate_comparisons', []) or [])
    incoming = list(getattr(output, 'candidate_comparisons', []) or [])
    if not existing:
        return output
    merged = _merge_mapping_draft_candidate_comparisons(existing, incoming)
    if len(merged) == len(incoming) and all(a == b for a, b in zip(merged, incoming)):
        return output
    return output.model_copy(update={'candidate_comparisons': merged})


def _should_repair_mapping_patch_issues(patch_issues: list[VerifierIssue], repair_depth: int) -> bool:
    if repair_depth > _max_mapping_editor_repair_depth() - 1:
        return False
    issue_codes = {
        str(getattr(issue, 'issue_code', '') or '').casefold()
        for issue in list(patch_issues or [])
    }
    repairable_codes = {
        'comparison_patch_conflict',
        'invalid_reason_kind',
        'invalid_explicit_multi_file_mapping',
        'missing_singleton_candidate_comparison',
        'missing_target_ref',
        'missing_support_refs',
        'supplemental_singleton_target_mismatch',
        'unsupported_singleton_comparison_winner',
        'unknown_target_span_ref',
        'unresolved_bangumi_target_absent_candidate',
        'unresolved_open_candidate',
        'unresolved_supplemental_candidate',
        'unresolved_special_candidate',
    }
    return bool(issue_codes) and issue_codes <= repairable_codes


def _supplemental_category_supported_by_text(reason_kind: str, text: str) -> bool:
    return supplemental_category_supported_by_text(reason_kind, text)


def _classify_supplemental_reason(text: str) -> str:
    return classify_supplemental_reason(text)


def _supplemental_reason_from_local_ref(dossier, local_ref: str) -> str:
    return supplemental_reason_from_local_ref(dossier, local_ref)


def _max_mapping_editor_repair_depth() -> int:
    return 5


def _comparison_reason_undermines_winner(comparison) -> bool:
    # The fixed layer only checks comparison refs and winner/patch consistency.
    # Natural-language persuasiveness belongs to the editor.
    return False
    reason = str(getattr(comparison, 'reason', '') or '').strip()
    if not reason:
        return False
    winner_ref = str(getattr(comparison, 'winner_ref', '') or '').casefold()
    text = ' '.join(reason.casefold().split())
    if not text:
        return False

    hard_phrases = (
        'not strong enough',
        'not enough for a firm',
        'not enough for firm',
        'not enough to accept',
        'not enough to map',
        'cannot justify',
        "can't justify",
        'not supportable',
        'not supported',
        'insufficient evidence',
        'needs more evidence',
        'too ambiguous',
        'remains ambiguous',
        'remain ambiguous',
        'cannot safely',
        "can't safely",
        'not safe',
        'fail closed',
        'should not be accepted',
        '不够强',
        '不足以',
        '不能支撑',
        '无法支撑',
        '无法确认',
        '不能确认',
        '证据不足',
        '仍然模糊',
        '仍不明确',
        '需要更多证据',
    )
    soft_phrases = (
        'only loosely',
        'loose match',
        'loosely match',
        'loosely matches',
        'weakly',
        'weak support',
        'weak match',
        'no title overlap',
        'title mismatch',
        'different title',
        'does not match',
        "doesn't match",
        'generic special',
        '松散匹配',
        '弱匹配',
        '标题不匹配',
        '没有标题重合',
        '标题不同',
    )
    sentences = [
        part.strip()
        for part in re.split(r'[\n\r.;!?。！？；]+', text)
        if part.strip()
    ]
    selected_markers = ('winner', 'selected', 'chosen', 'target', 'mapping', '获胜', '选中', '目标', '映射')
    for sentence in sentences:
        clauses = [
            clause.strip()
            for clause in re.split(r'\b(?:while|whereas|but|however|though|although)\b|[,，]', sentence)
            if clause.strip()
        ]
        for clause in clauses or [sentence]:
            if not any(phrase in clause for phrase in (*hard_phrases, *soft_phrases)):
                continue
            winner_scoped = bool(winner_ref and winner_ref in clause)
            selected_scoped = any(marker in clause for marker in selected_markers)
            if winner_scoped or selected_scoped:
                return True
    if len(sentences) <= 1 and any(phrase in text for phrase in hard_phrases):
        return True
    return False


def _unresolved_special_candidate_issues(dossier, draft: MappingDraft) -> list[VerifierIssue]:
    special_refs = set(special_like_item_refs(dossier))
    special_span_refs = {
        str(getattr(card, 'ref', '') or '')
        for card in list(getattr(dossier, 'bangumi_span_cards', []) or [])
        if str(getattr(card, 'ref', '') or '')
        and bool(getattr(card, 'detail_equivalent', False))
        and str(getattr(card, 'item_kind', '') or '') == 'special'
    }
    local_spans = {str(getattr(card, 'ref', '') or ''): card for card in list(getattr(dossier, 'local_span_cards', []) or [])}
    issues: list[VerifierIssue] = []
    for row in list(getattr(draft, 'rows', []) or []):
        if str(getattr(row, 'disposition', '') or '') not in {'needs_more_evidence', 'unaligned_fail_closed', 'open'}:
            continue
        local_ref = str(getattr(row, 'local_ref', '') or '')
        if not is_special_eligible_span(local_spans.get(local_ref), dossier):
            continue
        candidate_refs = [ref for ref in list(getattr(row, 'candidate_target_refs', []) or []) if ref in special_refs or ref in special_span_refs]
        if not candidate_refs:
            continue
        issues.append(VerifierIssue(
            ref=str(getattr(row, 'row_ref', '') or local_ref),
            issue_code='unresolved_special_candidate',
            severity='blocked',
            message='special singleton row remains unresolved despite visible special/movie candidate items; editor must compare candidates or keep fail-closed with specific reason',
            related_refs=[local_ref, *candidate_refs[:8]],
        ))
    return issues


def _unresolved_open_candidate_issues(dossier, draft: MappingDraft) -> list[VerifierIssue]:
    visible_refs = {
        *list(getattr(getattr(dossier, 'visible_refs', None), 'target_refs', []) or []),
        *list(getattr(getattr(dossier, 'visible_refs', None), 'bangumi_item_refs', []) or []),
        *[str(getattr(card, 'ref', '') or '') for card in list(getattr(dossier, 'bangumi_span_cards', []) or []) if bool(getattr(card, 'detail_equivalent', False))],
        *list(getattr(dossier, 'assignable_target_refs', []) or []),
        *list(getattr(dossier, 'detailed_card_refs', []) or []),
        *list(getattr(dossier, 'seen_detail_refs', []) or []),
    }
    issues: list[VerifierIssue] = []
    for row in list(getattr(draft, 'rows', []) or []):
        if str(getattr(row, 'disposition', '') or '') not in {'needs_more_evidence', 'unaligned_fail_closed', 'open'}:
            continue
        candidate_refs = [
            str(ref or '')
            for ref in list(getattr(row, 'candidate_target_refs', []) or [])
            if str(ref or '') and str(ref or '') in visible_refs
        ]
        if not candidate_refs:
            continue
        local_ref = str(getattr(row, 'local_ref', '') or '')
        issues.append(VerifierIssue(
            ref=str(getattr(row, 'row_ref', '') or local_ref),
            issue_code='unresolved_open_candidate',
            severity='blocked',
            message='mapping draft row remains unresolved despite visible candidate target refs; editor must map one candidate, mark target absent/supplemental when allowed, or give a concrete semantic conflict',
            related_refs=_dedupe_preserve_order([local_ref, *candidate_refs[:8]]),
        ))
    return issues


def _unresolved_bangumi_target_absent_candidate_issues(dossier, draft: MappingDraft) -> list[VerifierIssue]:
    local_spans = {str(getattr(card, 'ref', '') or ''): card for card in list(getattr(dossier, 'local_span_cards', []) or [])}
    issues: list[VerifierIssue] = []
    unresolved_dispositions = {'needs_more_evidence', 'unaligned_fail_closed', 'open'}
    for row in list(getattr(draft, 'rows', []) or []):
        if str(getattr(row, 'disposition', '') or '') not in unresolved_dispositions:
            continue
        if list(getattr(row, 'candidate_target_refs', []) or []):
            continue
        local_ref = str(getattr(row, 'local_ref', '') or '')
        span = local_spans.get(local_ref)
        local_text = _local_ref_text_for_supplemental_issue(dossier, local_ref)
        combined = ' '.join([
            str(getattr(row, 'reason_kind', '') or ''),
            str(getattr(row, 'reason', '') or ''),
            local_text,
        ]).casefold()
        target_absent_like = is_special_eligible_span(span, dossier) or any(
            marker in combined
            for marker in ('ova', 'oav', 'oad', 'sp', 'special', 'tokubetsu', '番外', '特别', '特別')
        )
        if not target_absent_like:
            continue
        issues.append(VerifierIssue(
            ref=str(getattr(row, 'row_ref', '') or local_ref),
            issue_code='unresolved_bangumi_target_absent_candidate',
            severity='blocked',
            message='singleton/special row has no visible Bangumi target candidate; if the evidence surface is exhausted, repair this row with mark_non_bangumi_or_supplemental(reason_kind=bangumi_target_absent, support_refs including the local row) so it is accepted as not entering Bangumi mapping',
            related_refs=_dedupe_preserve_order([local_ref, *list(getattr(row, 'support_refs', []) or [])])[:8],
        ))
    return issues


def _local_ref_text_for_supplemental_issue(dossier, local_ref: str) -> str:
    return local_ref_text_for_supplemental_issue(dossier, local_ref)


def _unresolved_supplemental_candidate_issues(dossier, draft: MappingDraft) -> list[VerifierIssue]:
    issues: list[VerifierIssue] = []
    unresolved_dispositions = {'needs_more_evidence', 'unaligned_fail_closed', 'open'}
    for row in list(getattr(draft, 'rows', []) or []):
        if str(getattr(row, 'disposition', '') or '') not in unresolved_dispositions:
            continue
        local_ref = str(getattr(row, 'local_ref', '') or '')
        row_text = ' '.join(
            str(value or '')
            for value in [
                getattr(row, 'reason_kind', ''),
                getattr(row, 'reason', ''),
                *list(getattr(row, 'support_refs', []) or []),
            ]
        )
        local_text = _local_ref_text_for_supplemental_issue(dossier, local_ref)
        combined = f'{row_text} {local_text}'.strip()
        reason_kind = _classify_supplemental_reason(combined)
        if not _supplemental_category_supported_by_text(reason_kind, combined):
            continue
        issues.append(VerifierIssue(
            ref=str(getattr(row, 'row_ref', '') or local_ref),
            issue_code='unresolved_supplemental_candidate',
            severity='blocked',
            message='mapping draft row remains unresolved after visible text identifies it as a supplemental/non-Bangumi extra; editor must either mark it supplemental with visible support refs or explain a concrete fail-closed blocker',
            related_refs=_dedupe_preserve_order([local_ref, *list(getattr(row, 'support_refs', []) or [])])[:8],
        ))
    return issues


def _salvage_unresolved_mapping_patches(
    original_draft: MappingDraft,
    updated_draft: MappingDraft,
    attempted_patches: list[MappingDraftPatch],
    patch_issues: list[VerifierIssue],
    dossier,
) -> list[MappingDraftPatch]:
    hard_codes = {
        'hidden_ref_rejected',
        'unknown_local_ref',
        'unknown_target_ref',
        'target_not_allowed',
        'missing_target_ref',
        'invalid_mapping_mode',
    }
    issue_codes = {str(getattr(issue, 'issue_code', '') or '') for issue in patch_issues}
    if issue_codes & hard_codes:
        return []

    rows_by_local = {row.local_ref: row for row in list(getattr(updated_draft, 'rows', []) or []) if getattr(row, 'local_ref', '')}
    original_rows_by_local = {row.local_ref: row for row in list(getattr(original_draft, 'rows', []) or []) if getattr(row, 'local_ref', '')}
    detail_span_refs = {
        str(getattr(card, 'ref', '') or '')
        for card in list(getattr(dossier, 'bangumi_span_cards', []) or [])
        if str(getattr(card, 'ref', '') or '') and bool(getattr(card, 'detail_equivalent', False))
    }
    row_refs_to_local = {
        str(getattr(row, 'row_ref', '') or ''): str(getattr(row, 'local_ref', '') or '')
        for row in list(getattr(updated_draft, 'rows', []) or [])
        if str(getattr(row, 'row_ref', '') or '') and str(getattr(row, 'local_ref', '') or '')
    }
    issue_refs = _dedupe_preserve_order([
        row_refs_to_local.get(str(getattr(issue, 'ref', '') or ''), str(getattr(issue, 'ref', '') or ''))
        for issue in patch_issues
    ])
    issue_refs = [ref for ref in issue_refs if ref in rows_by_local]
    if not issue_refs:
        return []

    attempted_by_local: dict[str, MappingDraftPatch] = {}
    row_refs = {row.row_ref: row.local_ref for row in list(getattr(original_draft, 'rows', []) or []) if getattr(row, 'row_ref', '')}
    for patch in list(attempted_patches or []):
        normalized = normalize_mapping_patch_op(patch)
        local_ref = row_refs.get(str(getattr(normalized, 'local_ref', '') or ''), str(getattr(normalized, 'local_ref', '') or ''))
        if local_ref:
            attempted_by_local[local_ref] = normalized

    salvage: list[MappingDraftPatch] = []
    for local_ref in issue_refs:
        row = rows_by_local.get(local_ref)
        original_row = original_rows_by_local.get(local_ref)
        attempted = attempted_by_local.get(local_ref)
        if row is None or str(getattr(row, 'disposition', '') or '') != 'open':
            continue
        if original_row is None or str(getattr(original_row, 'disposition', '') or '') != 'open':
            continue
        if attempted is not None and str(getattr(attempted, 'op', '') or '') == 'mark_non_bangumi_or_supplemental':
            attempted_reason_kind = str(getattr(attempted, 'reason_kind', '') or '')
            reason_kind = attempted_reason_kind if attempted_reason_kind in ALLOWED_SUPPLEMENTAL_REASON_KINDS else _supplemental_reason_from_local_ref(dossier, local_ref)
            patch = MappingDraftPatch(
                op='mark_non_bangumi_or_supplemental',
                local_ref=local_ref,
                support_refs=_dedupe_preserve_order([local_ref, *list(getattr(attempted, 'support_refs', []) or [])]),
                reason_kind=reason_kind,
                reason=str(getattr(attempted, 'reason', '') or 'visible local evidence supports supplemental/non-Bangumi accounting'),
            )
            if _supplemental_policy_allows_patch(dossier, original_draft, patch):
                salvage.append(patch)
                continue
            target_absent_patch = MappingDraftPatch(
                op='mark_non_bangumi_or_supplemental',
                local_ref=local_ref,
                support_refs=_dedupe_preserve_order([local_ref, *list(getattr(attempted, 'support_refs', []) or [])]),
                reason_kind='bangumi_target_absent',
                reason='editor attempted supplemental/non-Bangumi accounting after investigation; no visible Bangumi target can be accepted for this extra-like row',
            )
            if _supplemental_policy_allows_patch(dossier, original_draft, target_absent_patch):
                salvage.append(target_absent_patch)
            continue
        attempted_target = str(getattr(attempted, 'target_span_ref', '') or getattr(attempted, 'target_ref', '') or '')
        reason_kind = 'missing_target_span' if attempted_target and attempted_target not in detail_span_refs else 'ambiguous_candidate'
        salvage.append(MappingDraftPatch(
            op='needs_more_evidence',
            local_ref=local_ref,
            reason_kind=reason_kind,
            reason=f'editor patch rejected mechanically: {",".join(sorted(issue_codes))}',
        ))
    return salvage


def _editor_patches_with_comparison_repairs(draft: MappingDraft, output, dossier) -> list:
    from .models import MappingDraftPatch

    original_patches = list(getattr(output, 'patches', []) or [])
    normalized_original = [normalize_mapping_patch_op(patch) for patch in original_patches]
    detail_span_refs = {
        card.ref
        for card in (getattr(dossier, 'bangumi_span_cards', []) or [])
        if getattr(card, 'ref', '') and bool(getattr(card, 'detail_equivalent', False))
    }
    special_item_refs = set(special_like_item_refs(dossier))
    row_refs = {row.row_ref: row for row in list(getattr(draft, 'rows', []) or [])}
    original_by_local: dict[str, MappingDraftPatch] = {}
    for patch in normalized_original:
        raw_local_ref = str(getattr(patch, 'local_ref', '') or '')
        local_ref = row_refs[raw_local_ref].local_ref if raw_local_ref in row_refs else raw_local_ref
        if local_ref:
            original_by_local[local_ref] = patch
    explicit_target_counts: dict[str, int] = {}
    open_local_refs = {
        str(getattr(row, 'local_ref', '') or '')
        for row in list(getattr(draft, 'rows', []) or [])
        if str(getattr(row, 'disposition', '') or '') == 'open' or str(getattr(row, 'status', '') or '') == 'open'
    }
    occupied_special_item_refs = {
        str(getattr(row, 'selected_target_ref', '') or '')
        for row in list(getattr(draft, 'rows', []) or [])
        if str(getattr(row, 'selected_target_ref', '') or '') in special_item_refs
        and str(getattr(row, 'local_ref', '') or '') not in open_local_refs
    }
    for patch in normalized_original:
        if str(getattr(patch, 'op', '') or '') != 'map_to_bangumi':
            continue
        target_ref = str(getattr(patch, 'target_ref', '') or '')
        if target_ref and target_ref in special_item_refs:
            explicit_target_counts[target_ref] = explicit_target_counts.get(target_ref, 0) + 1
    comparison_repairs = {}
    for comparison in list(getattr(output, 'candidate_comparisons', []) or []):
        row = row_refs.get(str(getattr(comparison, 'ref', '') or ''))
        winner_ref = str(getattr(comparison, 'winner_ref', '') or '')
        if row is None or not winner_ref:
            continue
        existing_patch = original_by_local.get(row.local_ref)
        if existing_patch is not None and str(getattr(existing_patch, 'op', '') or '') == 'map_to_bangumi':
            existing_target = str(getattr(existing_patch, 'target_span_ref', '') or getattr(existing_patch, 'target_ref', '') or '')
            if existing_target == winner_ref:
                continue
        if winner_ref not in set(row.candidate_target_refs or []):
            continue
        if winner_ref in detail_span_refs:
            comparison_repairs[row.local_ref] = MappingDraftPatch(
                op='map_to_bangumi',
                local_ref=row.local_ref,
                target_span_ref=winner_ref,
                mapping_mode='span_by_index',
                support_refs=[row.local_ref, winner_ref],
                reason=f'candidate_comparison:{getattr(comparison, "ref", "") or row.row_ref}',
            )
        elif winner_ref in special_item_refs:
            if explicit_target_counts.get(winner_ref, 0) > 1:
                continue
            if winner_ref in occupied_special_item_refs:
                continue
            comparison_repairs[row.local_ref] = MappingDraftPatch(
                op='map_to_bangumi',
                local_ref=row.local_ref,
                target_ref=winner_ref,
                mapping_mode='explicit',
                support_refs=[row.local_ref, winner_ref],
                reason=f'candidate_comparison:{getattr(comparison, "ref", "") or row.row_ref}',
            )
    repaired = []
    repaired_local_refs: set[str] = set()
    for patch in original_patches:
        normalized = normalize_mapping_patch_op(patch)
        raw_local_ref = getattr(normalized, 'local_ref', '')
        local_ref = row_refs[raw_local_ref].local_ref if raw_local_ref in row_refs else raw_local_ref
        if local_ref in comparison_repairs:
            repaired.append(comparison_repairs[local_ref])
        else:
            repaired.append(patch)
        if local_ref:
            repaired_local_refs.add(local_ref)
    for local_ref, patch in comparison_repairs.items():
        if local_ref not in repaired_local_refs:
            repaired.append(patch)
    return repaired


def _comparison_patch_consistency_issues(draft: MappingDraft, output, dossier, patches: list[MappingDraftPatch] | None = None) -> list[VerifierIssue]:
    rows_by_ref = {str(getattr(row, 'row_ref', '') or ''): row for row in list(getattr(draft, 'rows', []) or [])}
    rows_by_local = {str(getattr(row, 'local_ref', '') or ''): row for row in list(getattr(draft, 'rows', []) or [])}
    patch_targets: dict[str, str] = {}
    for patch in list(patches if patches is not None else (getattr(output, 'patches', []) or [])):
        normalized = normalize_mapping_patch_op(patch)
        if str(getattr(normalized, 'op', '') or '') != 'map_to_bangumi':
            continue
        raw_local_ref = str(getattr(normalized, 'local_ref', '') or '')
        row = rows_by_ref.get(raw_local_ref) or rows_by_local.get(raw_local_ref)
        if row is None:
            continue
        target_ref = str(getattr(normalized, 'target_span_ref', '') or getattr(normalized, 'target_ref', '') or '')
        if target_ref:
            patch_targets[str(getattr(row, 'row_ref', '') or '')] = target_ref

    visible_target_refs = {
        *[str(getattr(card, 'ref', '') or '') for card in list(getattr(dossier, 'bangumi_span_cards', []) or [])],
        *[str(getattr(card, 'ref', '') or '') for card in list(getattr(dossier, 'bangumi_items', []) or [])],
        *list(getattr(dossier, 'assignable_target_refs', []) or []),
        *list(getattr(dossier, 'detailed_card_refs', []) or []),
        *list(getattr(dossier, 'seen_detail_refs', []) or []),
    }
    visible_target_refs = {ref for ref in visible_target_refs if ref}
    issues: list[VerifierIssue] = []
    for comparison in list(getattr(output, 'candidate_comparisons', []) or []):
        row = rows_by_ref.get(str(getattr(comparison, 'ref', '') or '')) or rows_by_local.get(str(getattr(comparison, 'ref', '') or ''))
        winner_ref = str(getattr(comparison, 'winner_ref', '') or '')
        if row is None or not winner_ref or winner_ref not in visible_target_refs:
            continue
        selected_ref = patch_targets.get(str(getattr(row, 'row_ref', '') or ''))
        if selected_ref and selected_ref != winner_ref:
            issues.append(VerifierIssue(
                ref=str(getattr(row, 'row_ref', '') or getattr(row, 'local_ref', '') or 'mapping_draft'),
                issue_code='comparison_patch_conflict',
                severity='blocked',
                message='candidate comparison winner conflicts with selected mapping patch',
                related_refs=[str(getattr(row, 'local_ref', '') or ''), selected_ref, winner_ref],
            ))
    return issues


def _comparison_refs_with_undermined_winner(output) -> set[str]:
    refs: set[str] = set()
    for comparison in list(getattr(output, 'candidate_comparisons', []) or []):
        if not _comparison_reason_undermines_winner(comparison):
            continue
        ref = str(getattr(comparison, 'ref', '') or '')
        if ref:
            refs.add(ref)
    return refs


def _final_special_singleton_comparison_issues(dossier, draft: MappingDraft, output) -> list[VerifierIssue]:
    # Single-session Case Agent owns the semantic comparison. The fixed layer
    # only verifies refs/accounting and must not require an extra comparison
    # artifact before accepting a visible explicit BE target.
    return []
    special_item_refs = set(special_like_item_refs(dossier))
    if not special_item_refs:
        return []
    local_spans = {str(getattr(card, 'ref', '') or ''): card for card in list(getattr(dossier, 'local_span_cards', []) or [])}
    bangumi_items_by_ref = {
        str(getattr(card, 'ref', '') or ''): card
        for card in list(getattr(dossier, 'bangumi_items', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }
    rows_by_ref = {str(getattr(row, 'row_ref', '') or ''): row for row in list(getattr(draft, 'rows', []) or [])}
    rows_by_local = {str(getattr(row, 'local_ref', '') or ''): row for row in list(getattr(draft, 'rows', []) or [])}
    winners_by_row_ref: dict[str, set[str]] = {}
    compared_by_row_ref: dict[str, set[str]] = {}
    comparisons_by_row_ref: dict[str, list[object]] = {}
    for comparison in list(getattr(output, 'candidate_comparisons', []) or []):
        row = rows_by_ref.get(str(getattr(comparison, 'ref', '') or '')) or rows_by_local.get(str(getattr(comparison, 'ref', '') or ''))
        if row is None:
            continue
        row_ref = str(getattr(row, 'row_ref', '') or '')
        winner_ref = str(getattr(comparison, 'winner_ref', '') or '')
        if winner_ref:
            winners_by_row_ref.setdefault(row_ref, set()).add(winner_ref)
        compared_refs = [
            str(getattr(comparison, 'left_ref', '') or ''),
            str(getattr(comparison, 'right_ref', '') or ''),
            winner_ref,
        ]
        compared_by_row_ref.setdefault(row_ref, set()).update(ref for ref in compared_refs if ref)
        comparisons_by_row_ref.setdefault(row_ref, []).append(comparison)

    issues: list[VerifierIssue] = []
    for row in list(getattr(draft, 'rows', []) or []):
        if str(getattr(row, 'disposition', '') or '') != 'map_to_bangumi':
            continue
        selected_ref = str(getattr(row, 'selected_target_ref', '') or '')
        if not selected_ref or selected_ref not in special_item_refs:
            continue
        if str(getattr(row, 'mapping_mode', '') or '') != 'explicit':
            continue
        local_ref = str(getattr(row, 'local_ref', '') or '')
        if not is_special_eligible_span(local_spans.get(local_ref), dossier):
            continue
        local_category = _classify_supplemental_reason(_local_ref_text_for_supplemental_issue(dossier, local_ref))
        selected_card = bangumi_items_by_ref.get(selected_ref)
        target_text = ''
        if selected_card is not None:
            target_text = ' '.join(
                str(value or '')
                for value in [
                    getattr(selected_card, 'item_kind', ''),
                    getattr(selected_card, 'kind', ''),
                    getattr(selected_card, 'type', ''),
                    getattr(selected_card, 'title', ''),
                    getattr(selected_card, 'name', ''),
                    getattr(selected_card, 'name_cn', ''),
                    getattr(selected_card, 'desc_short', ''),
                    getattr(selected_card, 'source_form_hint', ''),
                    getattr(selected_card, 'relation_to_main', ''),
                ]
            )
        if False and (
            local_category in {'creditless_op_ed', 'pv_cm', 'menu_or_navigation', 'sample', 'making_of'}
            and not _supplemental_category_supported_by_text(local_category, target_text)
        ):
            row_ref = str(getattr(row, 'row_ref', '') or local_ref or 'mapping_draft')
            issues.append(VerifierIssue(
                ref=row_ref,
                issue_code='supplemental_singleton_target_mismatch',
                severity='blocked',
                message='final explicit singleton mapping target does not expose the same supplemental category as the local singleton; repair must choose a matching visible item or mark the local row supplemental/non-Bangumi',
                related_refs=[local_ref, selected_ref],
            ))
            continue
        candidate_item_refs = [ref for ref in list(getattr(row, 'candidate_target_refs', []) or []) if ref in special_item_refs]
        if selected_ref not in candidate_item_refs:
            candidate_item_refs.append(selected_ref)
        candidate_item_refs = _dedupe_preserve_order(candidate_item_refs)
        if len(candidate_item_refs) <= 1:
            continue
        row_ref = str(getattr(row, 'row_ref', '') or local_ref or 'mapping_draft')
        if selected_ref in winners_by_row_ref.get(row_ref, set()):
            undermining_comparisons = [
                comparison
                for comparison in comparisons_by_row_ref.get(row_ref, [])
                if str(getattr(comparison, 'winner_ref', '') or '') == selected_ref
                and _comparison_reason_undermines_winner(comparison)
            ]
            if undermining_comparisons:
                issues.append(VerifierIssue(
                    ref=row_ref,
                    issue_code='unsupported_singleton_comparison_winner',
                    severity='blocked',
                    message='final explicit singleton mapping winner is undermined by the comparison reason; repair must choose a supportable target or fail closed',
                    related_refs=[local_ref, selected_ref, *candidate_item_refs[:8]],
                ))
            continue
        has_row_comparison = bool(compared_by_row_ref.get(row_ref))
        issues.append(VerifierIssue(
            ref=row_ref,
            issue_code='comparison_patch_conflict' if has_row_comparison else 'missing_singleton_candidate_comparison',
            severity='blocked',
            message=(
                'final explicit singleton mapping conflicts with candidate comparison winner'
                if has_row_comparison
                else 'final explicit singleton mapping with multiple item candidates requires a row comparison naming the selected target as winner'
            ),
            related_refs=[local_ref, selected_ref, *candidate_item_refs[:8]],
        ))
    return issues


def _structural_unique_span_patches(draft: MappingDraft, dossier) -> list:
    from .models import MappingDraftPatch

    local_spans = {card.ref: card for card in (getattr(dossier, 'local_span_cards', []) or []) if getattr(card, 'ref', '')}
    bangumi_spans = {
        card.ref: card
        for card in (getattr(dossier, 'bangumi_span_cards', []) or [])
        if getattr(card, 'ref', '') and bool(getattr(card, 'detail_equivalent', False))
    }
    rows = list(getattr(draft, 'rows', []) or [])
    candidate_rows: list[tuple[object, list[object]]] = []
    for row in rows:
        # Use this as a mechanical repair for either still-open rows or rows whose
        # previous span proposal failed expansion/accounting validation.
        if getattr(row, 'disposition', '') not in {'open', 'map_to_bangumi'}:
            return []
        local_span = local_spans.get(row.local_ref)
        if local_span is None:
            return []
        special_eligible = is_special_eligible_span(local_span, dossier)
        local_count = int(getattr(local_span, 'file_ref_count', 0) or len(getattr(local_span, 'file_refs', []) or []))
        source_bound_refs = {
            span.ref for span in bangumi_spans.values()
            if str(getattr(span, 'source_request_ref', '') or '') == f'REQ_TARGET_SPAN_{row.local_ref}'
            and (not special_eligible or str(getattr(span, 'item_kind', '') or '') == 'special')
        }
        candidate_refs = list(source_bound_refs or set(getattr(row, 'candidate_target_refs', []) or []))
        candidates = []
        for ref in candidate_refs:
            span = bangumi_spans.get(ref)
            if span is None:
                continue
            if special_eligible and str(getattr(span, 'item_kind', '') or '') != 'special':
                continue
            target_count = int(getattr(span, 'target_ref_count', 0) or len(getattr(span, 'target_refs', []) or []))
            if target_count == local_count:
                candidates.append(span)
        if not candidates:
            return []
        candidate_rows.append((row, candidates))
    if not candidate_rows:
        return []
    solutions: list[list[tuple[object, object]]] = []

    def _search(index: int, used_target_refs: set[str], chosen: list[tuple[object, object]]) -> None:
        if len(solutions) > 1:
            return
        if index >= len(candidate_rows):
            solutions.append(list(chosen))
            return
        row, candidates = candidate_rows[index]
        for candidate in candidates:
            target_refs = set(getattr(candidate, 'target_refs', []) or [])
            if used_target_refs & target_refs:
                continue
            _search(index + 1, used_target_refs | target_refs, [*chosen, (row, candidate)])

    _search(0, set(), [])
    if len(solutions) != 1:
        return []
    patches = []
    for row, winner in solutions[0]:
        patches.append(MappingDraftPatch(
            op='map_to_bangumi',
            local_ref=row.local_ref,
            target_span_ref=winner.ref,
            mapping_mode='span_by_index',
            support_refs=[row.local_ref, winner.ref],
            reason='structural_unique_span_candidate',
        ))
    return patches


def _structural_open_span_completion_patches(draft: MappingDraft, dossier) -> list:
    from .models import MappingDraftPatch

    local_spans = {card.ref: card for card in (getattr(dossier, 'local_span_cards', []) or []) if getattr(card, 'ref', '')}
    bangumi_spans = {
        card.ref: card
        for card in (getattr(dossier, 'bangumi_span_cards', []) or [])
        if getattr(card, 'ref', '') and bool(getattr(card, 'detail_equivalent', False))
    }
    used_target_refs: set[str] = set()
    unresolved_rows: list[object] = []
    for row in list(getattr(draft, 'rows', []) or []):
        disposition = str(getattr(row, 'disposition', '') or '')
        if disposition == 'map_to_bangumi':
            target_span = bangumi_spans.get(str(getattr(row, 'selected_target_ref', '') or ''))
            if target_span is not None:
                used_target_refs.update(str(ref or '') for ref in list(getattr(target_span, 'target_refs', []) or []) if ref)
            continue
        if disposition in {'open', 'needs_more_evidence'}:
            unresolved_rows.append(row)
    if not unresolved_rows or not used_target_refs:
        return []

    patches: list[MappingDraftPatch] = []
    for row in unresolved_rows:
        local_span = local_spans.get(str(getattr(row, 'local_ref', '') or ''))
        if local_span is None:
            return []
        special_eligible = is_special_eligible_span(local_span, dossier)
        local_count = int(getattr(local_span, 'file_ref_count', 0) or len(getattr(local_span, 'file_refs', []) or []))
        source_bound_refs = [
            span.ref for span in bangumi_spans.values()
            if str(getattr(span, 'source_request_ref', '') or '') == f'REQ_TARGET_SPAN_{row.local_ref}'
            and (not special_eligible or str(getattr(span, 'item_kind', '') or '') == 'special')
        ]
        candidate_refs = source_bound_refs or list(getattr(row, 'candidate_target_refs', []) or [])
        candidates = []
        for ref in candidate_refs:
            span = bangumi_spans.get(str(ref or ''))
            if span is None:
                continue
            if special_eligible and str(getattr(span, 'item_kind', '') or '') != 'special':
                continue
            target_refs = [str(target_ref or '') for target_ref in list(getattr(span, 'target_refs', []) or []) if target_ref]
            target_count = int(getattr(span, 'target_ref_count', 0) or len(target_refs))
            if target_count != local_count:
                continue
            if used_target_refs & set(target_refs):
                continue
            candidates.append(span)
        if len(candidates) != 1:
            return []
        winner = candidates[0]
        used_target_refs.update(str(ref or '') for ref in list(getattr(winner, 'target_refs', []) or []) if ref)
        patches.append(MappingDraftPatch(
            op='map_to_bangumi',
            local_ref=row.local_ref,
            target_span_ref=winner.ref,
            mapping_mode='span_by_index',
            support_refs=[row.local_ref, winner.ref],
            reason='structural_open_span_completion',
        ))
    return patches


def _structural_supplemental_span_conflict_patches(draft: MappingDraft, dossier, *, force_local_refs: set[str] | None = None) -> list:
    from .models import MappingDraftPatch

    force_local_refs = set(force_local_refs or set())
    rows_by_ref = {
        str(getattr(row, 'row_ref', '') or ''): row
        for row in list(getattr(draft, 'rows', []) or [])
        if str(getattr(row, 'row_ref', '') or '')
    }
    forced_rows: list[object] = []
    for ref in list(force_local_refs):
        row = rows_by_ref.get(ref)
        if row is not None:
            forced_rows.append(row)
    bangumi_spans = {
        card.ref: card
        for card in (getattr(dossier, 'bangumi_span_cards', []) or [])
        if getattr(card, 'ref', '') and bool(getattr(card, 'detail_equivalent', False))
    }
    seen_target_refs: dict[str, object] = {}
    conflict_rows: list[object] = []
    for row in list(getattr(draft, 'rows', []) or []):
        if str(getattr(row, 'disposition', '') or '') != 'map_to_bangumi':
            continue
        if str(getattr(row, 'mapping_mode', '') or '') != 'span_by_index':
            continue
        target_span = bangumi_spans.get(str(getattr(row, 'selected_target_ref', '') or ''))
        if target_span is None:
            continue
        for target_ref in list(getattr(target_span, 'target_refs', []) or []):
            target_ref = str(target_ref or '')
            if not target_ref:
                continue
            previous = seen_target_refs.get(target_ref)
            if previous is not None:
                conflict_rows.extend([previous, row])
            else:
                seen_target_refs[target_ref] = row
    conflict_rows = list({str(getattr(row, 'row_ref', '') or getattr(row, 'local_ref', '') or id(row)): row for row in conflict_rows}.values())
    conflict_rows = list({
        str(getattr(row, 'row_ref', '') or getattr(row, 'local_ref', '') or id(row)): row
        for row in [*conflict_rows, *forced_rows]
    }.values())
    patches: list[MappingDraftPatch] = []
    for row in conflict_rows:
        local_ref = str(getattr(row, 'local_ref', '') or '')
        reason_kind = _supplemental_reason_from_local_ref(dossier, local_ref)
        if reason_kind not in {'creditless_op_ed', 'pv_cm', 'menu_or_navigation', 'sample', 'making_of', 'other_supplemental'}:
            continue
        local_text = _local_ref_text_for_supplemental_issue(dossier, local_ref)
        if not _supplemental_category_supported_by_text(reason_kind, local_text):
            continue
        supplemental_patch = MappingDraftPatch(
            op='mark_non_bangumi_or_supplemental',
            local_ref=local_ref,
            support_refs=[local_ref],
            reason_kind=reason_kind,
            reason='structural supplemental target-conflict repair: visible local text identifies a non-episode extra',
        )
        if not _supplemental_policy_allows_patch(dossier, draft, supplemental_patch):
            continue
        if str(getattr(row, 'disposition', '') or '') == 'map_to_bangumi':
            patches.append(MappingDraftPatch(
                op='retract_mapping',
                local_ref=local_ref,
                reason='structural supplemental target-conflict repair retracts overlapping Bangumi span mapping',
            ))
        patches.append(supplemental_patch)
    return patches


def _structural_special_singleton_mismatch_patches(draft: MappingDraft, dossier) -> list:
    from .models import MappingDraftPatch

    special_item_refs = set(special_like_item_refs(dossier))
    if not special_item_refs:
        return []
    bangumi_items_by_ref = {
        str(getattr(card, 'ref', '') or ''): card
        for card in list(getattr(dossier, 'bangumi_items', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }
    patches: list[MappingDraftPatch] = []
    for row in list(getattr(draft, 'rows', []) or []):
        if str(getattr(row, 'disposition', '') or '') != 'map_to_bangumi':
            continue
        if str(getattr(row, 'mapping_mode', '') or '') != 'explicit':
            continue
        selected_ref = str(getattr(row, 'selected_target_ref', '') or '')
        if selected_ref not in special_item_refs:
            continue
        local_ref = str(getattr(row, 'local_ref', '') or '')
        reason_kind = _supplemental_reason_from_local_ref(dossier, local_ref)
        if reason_kind not in {'creditless_op_ed', 'pv_cm', 'menu_or_navigation', 'sample', 'making_of', 'other_supplemental'}:
            continue
        local_text = _local_ref_text_for_supplemental_issue(dossier, local_ref)
        if not _supplemental_category_supported_by_text(reason_kind, local_text):
            continue
        selected_card = bangumi_items_by_ref.get(selected_ref)
        target_text = ''
        if selected_card is not None:
            target_text = ' '.join(
                str(value or '')
                for value in [
                    getattr(selected_card, 'item_kind', ''),
                    getattr(selected_card, 'kind', ''),
                    getattr(selected_card, 'type', ''),
                    getattr(selected_card, 'title', ''),
                    getattr(selected_card, 'name', ''),
                    getattr(selected_card, 'name_cn', ''),
                    getattr(selected_card, 'desc_short', ''),
                    getattr(selected_card, 'source_form_hint', ''),
                    getattr(selected_card, 'relation_to_main', ''),
                ]
            )
        if _supplemental_category_supported_by_text(reason_kind, target_text):
            continue
        supplemental_patch = MappingDraftPatch(
            op='mark_non_bangumi_or_supplemental',
            local_ref=local_ref,
            support_refs=[local_ref],
            reason_kind=reason_kind,
            reason='structural supplemental explicit-target repair: visible local text identifies a non-episode extra and selected Bangumi singleton does not expose the same form',
        )
        if not _supplemental_policy_allows_patch(dossier, draft, supplemental_patch):
            continue
        patches.append(MappingDraftPatch(
            op='retract_mapping',
            local_ref=local_ref,
            reason='structural supplemental explicit-target repair retracts mismatched Bangumi singleton mapping',
        ))
        patches.append(supplemental_patch)
    return patches


def _structural_unresolved_supplemental_patches(draft: MappingDraft, dossier) -> list:
    from .models import MappingDraftPatch

    patches: list[MappingDraftPatch] = []
    for row in list(getattr(draft, 'rows', []) or []):
        if str(getattr(row, 'disposition', '') or '') not in {'open', 'needs_more_evidence', 'unaligned_fail_closed'}:
            continue
        local_ref = str(getattr(row, 'local_ref', '') or '')
        reason_kind = _supplemental_reason_from_local_ref(dossier, local_ref)
        if reason_kind not in {'creditless_op_ed', 'pv_cm', 'menu_or_navigation', 'sample', 'making_of', 'other_supplemental'}:
            continue
        local_text = _local_ref_text_for_supplemental_issue(dossier, local_ref)
        if not _supplemental_category_supported_by_text(reason_kind, local_text):
            continue
        patch = MappingDraftPatch(
            op='mark_non_bangumi_or_supplemental',
            local_ref=local_ref,
            support_refs=[local_ref],
            reason_kind=reason_kind,
            reason='structural unresolved supplemental repair: visible local text identifies a non-episode extra',
        )
        if not _supplemental_policy_allows_patch(dossier, draft, patch):
            continue
        patches.append(patch)
    return patches


def _structural_target_absent_patches(draft: MappingDraft, dossier) -> list:
    from .models import MappingDraftPatch

    patches: list[MappingDraftPatch] = []
    for row in list(getattr(draft, 'rows', []) or []):
        if str(getattr(row, 'disposition', '') or '') not in {'open', 'needs_more_evidence', 'unaligned_fail_closed', 'non_bangumi_or_supplemental'}:
            continue
        local_ref = str(getattr(row, 'local_ref', '') or '')
        if not local_ref:
            continue
        patch = MappingDraftPatch(
            op='mark_non_bangumi_or_supplemental',
            local_ref=local_ref,
            support_refs=[local_ref],
            reason_kind='bangumi_target_absent',
            reason='structural target-absent repair: investigated evidence has no visible assignable Bangumi target for this extra-like local row',
        )
        if not _supplemental_policy_allows_patch(dossier, draft, patch):
            continue
        if str(getattr(row, 'disposition', '') or '') == 'map_to_bangumi':
            patches.append(MappingDraftPatch(
                op='retract_mapping',
                local_ref=local_ref,
                reason='structural target-absent repair retracts unsupported Bangumi mapping',
            ))
        patches.append(patch)
    return patches


def _draft_covers_all_main_refs(workspace: CaseEvidenceWorkspace, draft: MappingDraft) -> bool:
    contract_main_refs = list(getattr(getattr(workspace, 'contract', None), 'main_file_refs', []) or [])
    if not contract_main_refs:
        return True
    if len(draft.rows) != len(contract_main_refs):
        return False
    row_local_refs = [row.local_ref for row in draft.rows if getattr(row, 'local_ref', '')]
    return set(row_local_refs) == set(contract_main_refs)



def _workspace_with_judge_audit(workspace: CaseEvidenceWorkspace, request_audit: dict | None) -> CaseEvidenceWorkspace:
    if not request_audit:
        return workspace
    audits = list(getattr(workspace, 'judge_request_audits', []) or [])
    audits.append(request_audit)
    return _workspace_preserving_state(workspace, judge_request_audits=audits)


def _workspace_with_notebook_updates(
    workspace: CaseEvidenceWorkspace,
    dossier,
    updates: list,
    *,
    source: str,
) -> CaseEvidenceWorkspace:
    if not updates:
        return workspace
    updated_notebook, issues = apply_notebook_updates(getattr(workspace, 'investigation_notebook', None), updates, dossier)
    audit = {
        'note': 'investigation_notebook_updates_observed',
        'source': source,
        'notebook_update_count': len(updates),
        'accepted_update_count': 0 if issues else len(updates),
        'rejected_update_count': len(updates) if issues else 0,
        'issue_codes': [str(getattr(issue, 'issue_code', '') or '') for issue in issues],
        'issue_refs': [str(getattr(issue, 'ref', '') or '') for issue in issues],
    }
    if issues:
        return _workspace_with_judge_audit(workspace, audit)
    return _workspace_with_judge_audit(
        _workspace_preserving_state(workspace, investigation_notebook=updated_notebook),
        audit,
    )


def _workspace_with_mapping_draft(workspace: CaseEvidenceWorkspace, draft: MappingDraft, *, patches: list | None = None, candidate_comparisons: list | None = None, note: str = '') -> CaseEvidenceWorkspace:
    patches_list = [*list(getattr(workspace, 'mapping_draft_patches', []) or []), *list(patches or [])]
    comparison_list = _merge_mapping_draft_candidate_comparisons(
        list(getattr(workspace, 'mapping_draft_candidate_comparisons', []) or []),
        list(candidate_comparisons or []),
    )
    updated = _workspace_preserving_state(
        workspace,
        mapping_draft=draft,
        mapping_draft_patches=patches_list,
        mapping_draft_candidate_comparisons=comparison_list,
    )
    if patches:
        notebook, notebook_issues = close_notebook_agenda_for_mapping_patches(
            getattr(updated, 'investigation_notebook', None),
            list(patches or []),
            updated.to_dossier(round_context='mapping_draft_notebook_close'),
        )
        if notebook_issues:
            updated = _workspace_with_verifier_issues(updated, CaseVerifierResult(passed=False, issues=notebook_issues, summary='notebook agenda close failed'))
        else:
            updated = _workspace_preserving_state(updated, investigation_notebook=notebook)
    if note:
        coverage = compute_local_span_partition_coverage(updated, draft)
        updated = _workspace_with_judge_audit(updated, {
            'note': note,
            **coverage,
            'mapping_draft_summary': compact_mapping_draft(draft, updated.to_dossier(round_context='mapping_draft_audit')),
        })
    return updated


def _workspace_with_request_normalization_audits(workspace: CaseEvidenceWorkspace, normalization_audits: list[dict[str, object]]) -> CaseEvidenceWorkspace:
    if not normalization_audits:
        return workspace
    audits = list(getattr(workspace, 'judge_request_audits', []) or [])
    audits.extend(normalization_audits)
    updated = workspace
    object.__setattr__(updated, 'judge_request_audits', audits)
    object.__setattr__(updated, 'diagnostics', [*workspace.diagnostics, *[str(a.get('note') or '') for a in normalization_audits if a.get('note')]])
    return updated


def _workspace_with_judge_output_capture(workspace: CaseEvidenceWorkspace, output: CaseJudgeOutput) -> CaseEvidenceWorkspace:
    audits = list(getattr(workspace, 'judge_request_audits', []) or [])
    for audit in reversed(audits):
        if isinstance(audit, dict) and str(audit.get('round_kind') or '') in {'initial', 'policy_retry', 'evidence_rejudge', 'issue_response'} and not str(audit.get('call_name') or '').strip():
            audit['action_actual'] = output.action
            audit['evidence_request_count'] = len(list(getattr(output, 'evidence_requests', []) or []))
            audit['evidence_request_types'] = [str(req.request_type) for req in (getattr(output, 'evidence_requests', []) or []) if str(req.request_type)]
            audit['fail_closed_reason_kinds'] = [str(getattr(reason, 'reason_kind', '') or '') for reason in (getattr(output, 'fail_closed_reasons', []) or []) if str(getattr(reason, 'reason_kind', '') or '')]
            audit['summary'] = str(getattr(output, 'summary', '') or '')
            audit['remaining_evidence_batches'] = max(0, int(getattr(workspace.budget, 'max_evidence_batches', 0) or 0) - int(getattr(workspace.budget, 'used_evidence_batches', 0) or 0))
            audit['remaining_judge_rounds'] = max(0, int(getattr(workspace.budget, 'max_judge_rounds', 0) or 0) - int(getattr(workspace.header, 'round_index', 0) or 0))
            audit['final_opportunity'] = bool(int(getattr(workspace.header, 'max_rounds', 0) or 0) and int(getattr(workspace.header, 'round_index', 0) or 0) >= max(0, int(getattr(workspace.header, 'max_rounds', 0) or 0) - 1))
            audit['output_budget_issues'] = [issue.message for issue in (verify_judge_output(build_bounded_case_dossier(workspace.to_dossier(round_context=str(audit.get('round_kind') or 'initial'))), output).issues or []) if getattr(issue, 'issue_code', '') == 'output_budget_exceeded']
            break
    updated = CaseEvidenceWorkspace.from_cards(
        header=workspace.header,
        budget=workspace.budget,
        contract=workspace.contract,
        local_files=workspace.local_files,
        local_clusters=workspace.local_clusters,
        local_span_cards=workspace.local_span_cards,
        bangumi_subjects=workspace.bangumi_subjects,
        bangumi_relations=workspace.bangumi_relations,
        bangumi_groups=workspace.bangumi_groups,
        bangumi_items=workspace.bangumi_items,
        bangumi_span_cards=workspace.bangumi_span_cards,
        query_cards=workspace.query_cards,
        provenance_cards=workspace.provenance_cards,
        previous_hypotheses=workspace.previous_hypotheses,
        previous_evidence_results=workspace.previous_evidence_results,
        verifier_issues=workspace.verifier_issues,
        diagnostics=workspace.diagnostics,
        plan_state=workspace.plan_state,
        mapping_draft=workspace.mapping_draft,
        mapping_draft_patches=workspace.mapping_draft_patches,
        mapping_draft_candidate_comparisons=getattr(workspace, 'mapping_draft_candidate_comparisons', []),
        case_briefing=getattr(workspace, 'case_briefing', None),
        investigation_notebook=getattr(workspace, 'investigation_notebook', None),
    )
    object.__setattr__(updated, 'judge_request_audits', audits)
    object.__setattr__(updated, 'seen_detail_refs', list(getattr(workspace, 'seen_detail_refs', []) or []))
    return updated


def _workspace_with_guard_decision(workspace: CaseEvidenceWorkspace, guard: dict[str, object]) -> CaseEvidenceWorkspace:
    audits = list(getattr(workspace, 'judge_request_audits', []) or [])
    audits.append({'premature_guard_decision': guard, **guard})
    return _workspace_preserving_state(workspace, judge_request_audits=audits)


def _structured_premature_guard_decision(*, workspace: CaseEvidenceWorkspace, dossier, output: CaseJudgeOutput, round_kind: str, triggered: bool, allowed: bool, reason: str, fail_closed_reason_kinds: list[str] | None = None) -> dict[str, object]:
    request_types_available = list(getattr(dossier, 'available_detail_request_types', []) or [])
    detailed_refs = list(getattr(dossier, 'detailed_card_refs', []) or [])
    seen_refs = list(getattr(dossier, 'seen_detail_refs', []) or [])
    return {
        'triggered': triggered,
        'allowed': allowed,
        'reason': reason,
        'round_kind': round_kind,
        'budget_available': bool(workspace.budget.max_evidence_batches and workspace.budget.used_evidence_batches < workspace.budget.max_evidence_batches),
        'request_types_available': request_types_available,
        'legal_anchor_available': bool(detailed_refs or seen_refs),
        'anchor_count': len(detailed_refs or seen_refs),
        'anchor_samples': (detailed_refs or seen_refs)[:8],
        'judge_no_request_reason': reason if output.action == 'fail_closed' and not output.evidence_requests else '',
        'fail_closed_reason_kinds': fail_closed_reason_kinds or [str(reason_item.reason_kind) for reason_item in (getattr(output, 'fail_closed_reasons', []) or []) if str(getattr(reason_item, 'reason_kind', ''))],
    }


def _workspace_with_evidence_batch_audit(workspace: CaseEvidenceWorkspace, batch_result: EvidenceBatchResult, output: CaseJudgeOutput, round_kind: str) -> CaseEvidenceWorkspace:
    audits = list(getattr(workspace, 'judge_request_audits', []) or [])
    for audit in reversed(audits):
        if isinstance(audit, dict) and str(audit.get('round_kind') or '') == round_kind and str(audit.get('action_actual') or audit.get('action') or '') == output.action:
            audit['evidence_request_count_actual'] = len(list(getattr(output, 'evidence_requests', []) or []))
            audit['evidence_request_types_actual'] = [str(req.request_type) for req in (getattr(output, 'evidence_requests', []) or []) if str(req.request_type)]
            audit['evidence_batch_status'] = batch_result.status
            audit['evidence_batch_count'] = 1
            break
    return _workspace_preserving_state(workspace, judge_request_audits=audits)


def _workspace_with_planner_batch_audit(workspace: CaseEvidenceWorkspace, batch_result: EvidenceBatchResult, planner_output) -> CaseEvidenceWorkspace:
    audits = list(getattr(workspace, 'judge_request_audits', []) or [])
    audits.append({
        'round_kind': 'planner',
        'planner_plan_kind': getattr(getattr(planner_output, 'plan', None), 'plan_kind', ''),
        'planner_plan_id': getattr(getattr(planner_output, 'plan', None), 'plan_id', ''),
        'planner_selected_menu_request_ids': list(getattr(getattr(planner_output, 'plan', None), 'selected_menu_request_ids', []) or []),
        'planner_selected_menu_request_count': len(list(getattr(getattr(planner_output, 'plan', None), 'selected_menu_request_ids', []) or [])),
        'evidence_batch_status': batch_result.status,
    })
    return _workspace_preserving_state(workspace, judge_request_audits=audits)


def _build_initial_mapping_draft(workspace: CaseEvidenceWorkspace) -> MappingDraft | None:
    dossier = workspace.to_dossier(round_context='draft_init')
    draft = build_initial_mapping_draft(dossier)
    if not draft.rows:
        return None
    return draft


def _workspace_with_initial_mapping_draft(workspace: CaseEvidenceWorkspace) -> CaseEvidenceWorkspace:
    if getattr(workspace, 'mapping_draft', None) is not None:
        return workspace
    draft = _build_initial_mapping_draft(workspace)
    if draft is None:
        return workspace
    return _workspace_preserving_state(
        workspace,
        mapping_draft=draft,
        mapping_draft_patches=list(getattr(workspace, 'mapping_draft_patches', []) or []),
        mapping_draft_candidate_comparisons=list(getattr(workspace, 'mapping_draft_candidate_comparisons', []) or []),
        judge_request_audits=[*list(getattr(workspace, 'judge_request_audits', []) or []), {'note': 'mapping_draft_initialized'}],
    )


def _next_round_kind(workspace: CaseEvidenceWorkspace) -> str:
    if 'policy_retry_pending' in (getattr(workspace, 'diagnostics', []) or []):
        return 'policy_retry'
    if workspace.verifier_issues:
        return 'issue_response'
    if workspace.previous_evidence_results:
        return 'evidence_rejudge'
    return 'initial'


def _normalize_judge_output_for_verifier(output: CaseJudgeOutput, *, round_kind: str) -> tuple[CaseJudgeOutput, str, str]:
    if round_kind != 'issue_response':
        if output.action in {'submit_verdict', 'fail_closed'} and output.evidence_requests:
            cleaned = output.model_copy(update={'evidence_requests': []})
            return cleaned, cleaned.action, 'normal'
        return output, output.action, 'normal'

    if output.action == 'request_evidence':
        return output, output.action, 'invalid'

    if output.action == 'submit_verdict':
        if output.evidence_requests:
            output = output.model_copy(update={'evidence_requests': []})
        if output.assignment_intents:
            return output, 'submit_verdict', 'normal'
        if output.fail_closed_reasons:
            normalized = output.model_copy(update={'action': 'fail_closed'})
            return normalized, 'fail_closed', 'normal'
        return output, output.action, 'invalid'

    if output.action == 'fail_closed':
        if output.evidence_requests:
            output = output.model_copy(update={'evidence_requests': []})
        return output, output.action, 'normal'

    if output.action == 'issue_response':
        if output.assignment_intents:
            normalized = output.model_copy(update={'action': 'submit_verdict'})
            return normalized, 'submit_verdict', 'normal'
        if output.fail_closed_reasons:
            normalized = output.model_copy(update={'action': 'fail_closed'})
            return normalized, 'fail_closed', 'normal'
        if output.issue_responses:
            reason = output.summary or 'issue_response could not produce a corrected executable verdict'
            synthesized = FailClosedReason(ref='FR1', reason_kind='insufficient_evidence', description=reason, related_refs=[])
            normalized = output.model_copy(update={'action': 'fail_closed', 'fail_closed_reasons': [synthesized]})
            return normalized, 'fail_closed', 'normal'
        return output, output.action, 'invalid'

    return output, output.action, 'invalid'


def _premature_fail_closed_guard(workspace: CaseEvidenceWorkspace, dossier, output: CaseJudgeOutput, *, round_kind: str) -> str | None:
    if round_kind not in {'initial', 'policy_retry'} or output.action != 'fail_closed' or output.evidence_requests:
        return None
    bounded = build_bounded_case_dossier(dossier) if dossier is not None else None
    counts = getattr(dossier, 'counts', {}) if dossier is not None else {}
    salience = getattr(bounded, 'salience_overview', {}) if bounded is not None else {}
    risk_flags = salience.get('risk_flags') if isinstance(salience, dict) and isinstance(salience.get('risk_flags'), dict) else {}
    detailed_card_count = len(getattr(dossier, 'detailed_card_refs', []) or [])
    assignable_count = len(getattr(dossier, 'assignable_target_refs', []) or [])
    target_count = int(counts.get('visible_target_count') or counts.get('target_count') or len(getattr(workspace, 'bangumi_items', []) or []))
    main_count = int(counts.get('main_file_count') or len(getattr(workspace, 'local_files', []) or []))
    large_case = bool(risk_flags.get('large_case')) or main_count >= 20 or target_count >= 20
    insufficient = bool(risk_flags.get('insufficient_detail_cards')) or detailed_card_count < min(10, max(4, target_count // 4 if target_count else 4)) or assignable_count < detailed_card_count or (target_count >= 20 and detailed_card_count <= 8)
    context_risk = bool(risk_flags.get('target_surface_large')) or bool(risk_flags.get('context_budget_risk')) or (main_count + target_count) >= 50
    risky = large_case and (insufficient or context_risk or target_count >= 20)
    if not risky:
        return None
    available_detail_request_types = getattr(bounded, 'available_detail_request_types', None) if bounded is not None else None
    if not available_detail_request_types:
        return None
    if workspace.budget.max_evidence_batches == 0 or workspace.budget.used_evidence_batches >= workspace.budget.max_evidence_batches:
        return None
    has_legal_anchor = _fail_closed_has_legal_anchor(bounded, workspace)
    if not has_legal_anchor:
        return None
    if round_kind == 'policy_retry' and _policy_retry_requires_request(output, bounded, workspace):
        return 'invalid_premature_fail_closed'
    if _fail_closed_has_unavoidable_no_request_reason(output, bounded, workspace):
        return None
    if round_kind == 'initial':
        return 'policy_retry_required'
    return 'invalid_premature_fail_closed'


def _fail_closed_has_legal_anchor(bounded, workspace: CaseEvidenceWorkspace) -> bool:
    if bounded is None:
        return False
    detail_refs = set(getattr(bounded, 'detailed_card_refs', []) or []) | set(getattr(bounded, 'assignable_target_refs', []) or []) | set(getattr(bounded, 'seen_detail_refs', []) or [])
    if not detail_refs:
        detail_refs |= set(getattr(getattr(workspace, 'contract', None), 'visible_target_refs', []) or [])
    if detail_refs:
        return True
    if getattr(workspace, 'local_files', None):
        return True
    if getattr(workspace, 'bangumi_items', None):
        return True
    return False


def _fail_closed_has_unavoidable_no_request_reason(output: CaseJudgeOutput, bounded, workspace: CaseEvidenceWorkspace) -> bool:
    if workspace.budget.max_evidence_batches == 0 or workspace.budget.used_evidence_batches >= workspace.budget.max_evidence_batches:
        return True
    available_detail_request_types = set(getattr(bounded, 'available_detail_request_types', []) or []) if bounded is not None else set()
    if not available_detail_request_types:
        return True
    detail_refs = set(getattr(bounded, 'detailed_card_refs', []) or []) | set(getattr(bounded, 'assignable_target_refs', []) or []) | set(getattr(bounded, 'seen_detail_refs', []) or [])
    if not detail_refs:
        return True
    reasons = list(getattr(output, 'fail_closed_reasons', []) or [])
    if not reasons:
        return False
    explicit_markers = (
        'no legal anchor',
        'legal anchor unavailable',
        'request types unavailable',
        'request unavailable',
        'cannot request evidence',
        'cannot be requested',
        'budget exhausted',
        'no budget left',
        'no evidence budget',
    )
    for reason in reasons:
        if getattr(reason, 'reason_kind', '') in {'budget_exhausted', 'contradiction'}:
            return True
        description = str(getattr(reason, 'description', '') or '').casefold()
        if any(marker in description for marker in explicit_markers):
            return True
        related_refs = list(getattr(reason, 'related_refs', []) or [])
        if related_refs and not any(ref for ref in related_refs if str(ref).startswith(('BE', 'LF', 'SQ'))):
            return True
        if str(getattr(reason, 'reason_kind', '')).casefold() in {'insufficient_evidence'}:
            continue
    return False


def _policy_retry_requires_request(output: CaseJudgeOutput, bounded, workspace: CaseEvidenceWorkspace) -> bool:
    reasons = list(getattr(output, 'fail_closed_reasons', []) or [])
    if not reasons:
        return False
    reason_kinds = {str(getattr(reason, 'reason_kind', '')).casefold() for reason in reasons}
    if 'insufficient_evidence' not in reason_kinds:
        return False
    descriptions = ' '.join(str(getattr(reason, 'description', '') or '').casefold() for reason in reasons)
    if 'no legal anchor' in descriptions or 'request types unavailable' in descriptions or 'budget exhausted' in descriptions or 'cannot request evidence' in descriptions:
        return False
    if workspace.budget.max_evidence_batches == 0 or workspace.budget.used_evidence_batches >= workspace.budget.max_evidence_batches:
        return False
    if not _fail_closed_has_legal_anchor(bounded, workspace):
        return False
    return True
