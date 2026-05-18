from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
import time
from typing import Literal

from .evidence_broker import EvidenceBroker
from .evidence_menu import build_executable_evidence_menu
from .evidence_menu_resolver import resolve_evidence_menu_requests
from .evidence_request_normalizer import normalize_evidence_requests
from .assignment_expander import expand_mapping_draft
from .mapping_draft import apply_mapping_patches, build_initial_mapping_draft, compact_mapping_draft, compute_local_span_partition_coverage, summarize_mapping_draft_coverage
from .mapping_draft import compute_mapping_draft_accounting
from .mapping_draft import normalize_mapping_patch_op
from .mapping_intent_compiler import MappingIntentCompiler
from .case_resolution_ledger import CaseResolutionLedgerCompiler, validate_case_resolution_ledger
from .case_planner import build_child_workspace
from .orchestrator_agent import (
    ExecuteEvidenceToolArgs,
    FinishCaseToolArgs,
    MaterializeQueriesToolArgs,
    OrchestratorAgentSession,
    OrchestratorAgentToolCall,
    ProposeCaseUnderstandingToolArgs,
    ProposeCaseResolutionLedgerToolArgs,
    ProposeMappingIntentsToolArgs,
    ReconsiderSplitToolArgs,
    SplitIntoChildCasesToolArgs,
    UpdateNotebookToolArgs,
    call_orchestrator_agent,
    record_orchestrator_tool_output,
    orchestrator_session_audit,
    _allowed_tool_names_for_workspace,
    _global_outcome_projection_for_agent,
    _latest_blocked_evidence_agenda_for_agent,
    _work_unit_resolution_board_focus_for_agent,
)
from .query_composer import call_query_composer
from .special_investigation import special_like_item_refs
from .supplemental_policy import ALLOWED_SUPPLEMENTAL_REASON_KINDS, classify_supplemental_reason, local_ref_text_for_supplemental_issue, main_file_refs_for_mapping_row, supplemental_category_supported_by_text, supplemental_reason_from_local_ref, supplemental_row_policy_issues
from .notebook import apply_notebook_updates, build_initial_investigation_notebook, build_notebook, close_notebook_agenda_for_mapping_patches, human_next_action_blockers, validate_case_briefing_refs
from .models import AssignmentIntent, CaseBriefingOutput, CaseBriefingWorkUnit, CaseJudgeOutput, CasePlanningOutput, CaseResolutionLedger, CaseVerifierResult, EvidenceBatchResult, EvidencePlan, EvidencePlannerOutput, FailClosedReason, Finding, LocalSpanCard, MappingDraftPatch, MappingDraftRow, QueryCard, SplitCaseSpec, VerifierIssue
from .models import EvidenceRequest, MappingDraft
from .verifier import _compact_fail_closed_related_refs, verify_judge_output, verify_mapping_draft_accounting
from .workspace import CaseEvidenceWorkspace


InvestigationAction = Literal[
    'propose_case_understanding',
    'compose_queries',
    'execute_evidence',
    'propose_case_resolution_ledger',
    'propose_mapping_intents',
    'update_notebook',
    'reconsider_split',
    'split_into_child_cases',
    'fail_closed',
    'accepted',
]

MAX_ORCHESTRATOR_SPLIT_DEPTH = 3
ORCHESTRATOR_PROGRESS_PATH_ENV = 'LOCAL_BANGUMI_CASE_AGENT_PROGRESS_PATH'
MAX_CHILD_CASES_PER_TOOL_CALL = 2
LEGACY_ORCHESTRATOR_HELPER_ENV = 'LOCAL_BANGUMI_ALLOW_LEGACY_ORCHESTRATOR_HELPER'


def _trace_orchestrator(message: str) -> None:
    if os.environ.get('BAR_CASE_AGENT_TRACE') != '1':
        return
    print(f'[case-agent-trace] {message}', file=sys.stderr, flush=True)


def _progress_jsonable(value, *, depth: int = 0):
    if depth > 4:
        return str(value)[:500]
    if hasattr(value, 'model_dump'):
        return _progress_jsonable(value.model_dump(mode='json'), depth=depth + 1)
    if isinstance(value, dict):
        compact: dict[str, object] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 40:
                compact['__truncated_keys__'] = len(value) - index
                break
            compact[str(key)] = _progress_jsonable(item, depth=depth + 1)
        return compact
    if isinstance(value, (list, tuple)):
        compact_list = [_progress_jsonable(item, depth=depth + 1) for item in list(value)[:30]]
        if len(value) > 30:
            compact_list.append({'__truncated_items__': len(value) - 30})
        return compact_list
    if isinstance(value, str):
        return value[:2000]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return str(value)[:500]


def _recent_progress_audits(workspace: CaseEvidenceWorkspace) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for audit in list(getattr(workspace, 'judge_request_audits', []) or [])[-12:]:
        if not isinstance(audit, dict):
            continue
        row: dict[str, object] = {}
        for key in (
            'note',
            'tool_name',
            'status',
            'reason',
            'error_kind',
            'turn_count',
            'accepted',
            'workspace_changed',
            'target_surface_changed',
            'recommended_next_observation',
        ):
            if key in audit:
                row[key] = _progress_jsonable(audit.get(key))
        if row:
            rows.append(row)
    return rows


def _orchestrator_progress_payload(
    workspace: CaseEvidenceWorkspace,
    session: OrchestratorAgentSession,
    *,
    phase: str,
    turn_index: int,
    max_turns: int,
    tool_name: str = '',
    observation: dict[str, object] | None = None,
) -> dict[str, object]:
    draft = getattr(workspace, 'mapping_draft', None)
    accounting = None
    if draft is not None:
        try:
            accounting = compute_mapping_draft_accounting(draft, workspace)
        except Exception:
            accounting = None
    ledger = getattr(workspace, 'case_resolution_ledger', None)
    return {
        'kind': 'local_bangumi_orchestrator_progress',
        'updated_at_ms': int(time.time() * 1000),
        'case_id': str(getattr(getattr(workspace, 'header', None), 'case_id', '') or ''),
        'phase': phase,
        'turn_index': turn_index,
        'max_turns': max_turns,
        'tool_name': tool_name,
        'session': _progress_jsonable(orchestrator_session_audit(session)),
        'workspace_counts': {
            'main_file_count': len(list(getattr(getattr(workspace, 'contract', None), 'main_file_refs', []) or [])),
            'local_file_count': len(list(getattr(workspace, 'local_files', []) or [])),
            'query_card_count': len(list(getattr(workspace, 'query_cards', []) or [])),
            'bangumi_subject_count': len(list(getattr(workspace, 'bangumi_subjects', []) or [])),
            'bangumi_item_count': len(list(getattr(workspace, 'bangumi_items', []) or [])),
            'bangumi_span_count': len(list(getattr(workspace, 'bangumi_span_cards', []) or [])),
            'audit_count': len(list(getattr(workspace, 'judge_request_audits', []) or [])),
            'patch_count': len(list(getattr(workspace, 'mapping_draft_patches', []) or [])),
        },
        'draft_accounting': _progress_jsonable(accounting),
        'case_resolution_ledger': {
            'row_count': len(list(getattr(ledger, 'rows', []) or [])) if ledger is not None else 0,
            'summary': str(getattr(ledger, 'summary', '') or '') if ledger is not None else '',
        },
        'recent_audits': _recent_progress_audits(workspace),
        'last_observation': _progress_jsonable(observation or {}),
    }


def _write_orchestrator_progress(
    workspace: CaseEvidenceWorkspace,
    session: OrchestratorAgentSession,
    *,
    phase: str,
    turn_index: int,
    max_turns: int,
    tool_name: str = '',
    observation: dict[str, object] | None = None,
) -> None:
    path_text = os.environ.get(ORCHESTRATOR_PROGRESS_PATH_ENV, '').strip()
    if not path_text:
        return
    try:
        path = Path(path_text)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = _orchestrator_progress_payload(
            workspace,
            session,
            phase=phase,
            turn_index=turn_index,
            max_turns=max_turns,
            tool_name=tool_name,
            observation=observation,
        )
        tmp_path = path.with_suffix(path.suffix + '.tmp')
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        tmp_path.replace(path)
    except Exception as exc:
        _trace_orchestrator(f'failed_to_write_progress={exc}')


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
        case_resolution_ledger=updates.get('case_resolution_ledger', getattr(workspace, 'case_resolution_ledger', None)),
    )
    object.__setattr__(updated, 'seen_detail_refs', list(updates.get('seen_detail_refs', getattr(workspace, 'seen_detail_refs', []) or [])))
    object.__setattr__(updated, 'judge_request_audits', list(updates.get('judge_request_audits', getattr(workspace, 'judge_request_audits', []) or [])))
    return updated


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

_EVIDENCE_REQUEST_TYPE_COMPATIBILITY: dict[str, set[str]] = {
    'subject_search': {'subject_search'},
    'subject_lookup': {'subject_lookup', 'episode_list', 'related_expansion'},
    'related_expansion': {'related_expansion', 'subject_lookup', 'episode_list'},
    'episode_list': {'episode_list', 'subject_lookup', 'related_expansion'},
    'episode_detail': {'episode_detail', 'target_detail', 'target_window', 'episode_list', 'subject_lookup', 'related_expansion'},
    'target_detail': {'target_detail', 'target_window', 'episode_detail', 'episode_list', 'subject_lookup', 'related_expansion'},
    'target_window': {'target_window', 'target_detail', 'episode_detail', 'episode_list', 'subject_lookup', 'related_expansion'},
    'target_span': {'target_span', 'target_window', 'target_detail', 'episode_detail', 'episode_list', 'subject_lookup', 'related_expansion'},
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


def _remaining_query_evidence_request_ids(workspace: CaseEvidenceWorkspace) -> list[str]:
    """Fresh subject_search requests created from visible QC cards."""
    return [
        str(item.get('request_id') or '')
        for item in _remaining_executable_menu_summaries(workspace, target_side_only=True)
        if str(item.get('request_id') or '')
        and str(item.get('request_type') or '') == 'subject_search'
        and any(str(ref or '').startswith('QC') for ref in list(item.get('source_refs') or []))
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


def _compatible_executable_request_types(requested_types: list[str] | set[str]) -> set[str]:
    compatible: set[str] = set()
    for requested_type in list(requested_types or []):
        value = str(requested_type or '')
        if not value:
            continue
        compatible.update(_EVIDENCE_REQUEST_TYPE_COMPATIBILITY.get(value, {value}))
    return compatible


def _request_type_matches_requested(request_type: str, requested_types: list[str] | set[str]) -> bool:
    value = str(request_type or '')
    if not value:
        return False
    return value in _compatible_executable_request_types(requested_types)


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


def _subject_refs_from_evidence_tool_args(args: ExecuteEvidenceToolArgs, workspace: CaseEvidenceWorkspace | None = None) -> list[str]:
    refs = [str(ref or '') for ref in list(getattr(args, 'subject_refs', []) or []) if str(ref or '')]
    visible_subject_refs: set[str] = set()
    if workspace is not None:
        visible_subject_refs = {
            str(getattr(subject, 'ref', '') or '')
            for subject in list(getattr(workspace, 'bangumi_subjects', []) or [])
            if str(getattr(subject, 'ref', '') or '')
        }
        item_subject_by_ref = {
            str(getattr(item, 'ref', '') or ''): str(getattr(item, 'subject_ref', '') or '')
            for item in list(getattr(workspace, 'bangumi_items', []) or [])
            if str(getattr(item, 'ref', '') or '')
        }
        span_subject_by_ref = {
            str(getattr(span, 'ref', '') or ''): str(getattr(span, 'subject_ref', '') or '')
            for span in list(getattr(workspace, 'bangumi_span_cards', []) or [])
            if str(getattr(span, 'ref', '') or '')
        }
        refs = [
            item_subject_by_ref.get(ref, span_subject_by_ref.get(ref, ref))
            for ref in refs
        ]
    for ref in list(getattr(args, 'item_refs', []) or []):
        value = str(ref or '')
        if workspace is not None and value:
            value = next(
                (
                    str(getattr(item, 'subject_ref', '') or '')
                    for item in list(getattr(workspace, 'bangumi_items', []) or [])
                    if str(getattr(item, 'ref', '') or '') == value
                ),
                value,
            )
        if value.startswith('BS'):
            refs.append(value)
    refs = _dedupe_preserve_order(refs)
    if workspace is not None and visible_subject_refs:
        refs = [ref for ref in refs if ref in visible_subject_refs]
    return refs


def _subject_refs_from_intent_patches(patches: list[MappingDraftPatch]) -> list[str]:
    return _dedupe_preserve_order([
        str(ref or '')
        for patch in list(patches or [])
        for ref in list(getattr(patch, 'subject_refs', []) or [])
        if str(ref or '')
    ])


def _orchestrator_requested_evidence_observation(
    workspace: CaseEvidenceWorkspace,
    *,
    requested_types: list[str],
    subject_refs: list[str],
) -> dict[str, object]:
    menu = build_executable_evidence_menu(workspace, max_requests=32)
    summaries = list(menu.get('prompt_summaries') or [])
    registry = menu.get('payload_registry') if isinstance(menu.get('payload_registry'), dict) else {}
    selected_ids: list[str] = []
    if requested_types:
        requested_subject_refs = {str(ref or '') for ref in list(subject_refs or []) if str(ref or '')}
        for summary in summaries:
            request_id = str(summary.get('request_id') or '')
            request_type = str(summary.get('request_type') or '')
            source_refs = set(_request_summary_source_refs(summary))
            if not request_id or request_id not in registry:
                continue
            if not _request_type_matches_requested(request_type, requested_types):
                continue
            if requested_subject_refs and not requested_subject_refs.intersection(source_refs):
                continue
            selected_ids.append(request_id)
    summaries, registry, augmented_request_ids = _augment_menu_with_agent_subject_requests(
        summaries,
        registry,
        subject_refs=subject_refs,
        request_types=requested_types,
    )
    if augmented_request_ids:
        for request_id in augmented_request_ids:
            request = registry.get(request_id)
            request_type = str(getattr(request, 'request_type', '') or '')
            if request_id and _request_type_matches_requested(request_type, requested_types):
                selected_ids.append(request_id)
    selected_ids = [
        request_id for request_id in selected_ids
        if request_id in registry and str(getattr(registry.get(request_id), 'request_type', '') or '') in _TARGET_SIDE_EVIDENCE_REQUEST_TYPES
    ]
    selected_ids, stale_ids = _filter_stale_menu_request_ids(workspace, _dedupe_preserve_order(selected_ids))
    phase_selected_ids, phase_audit = _evidence_phase_request_ids_for_editor_intent(
        workspace,
        summaries,
        selected_ids,
        requested_types,
        subject_refs=subject_refs,
    )
    phase_selected_ids = [
        request_id for request_id in phase_selected_ids
        if request_id in registry and not str(request_id).startswith('REQ_NEUTRAL_')
        and str(getattr(registry.get(request_id), 'request_type', '') or '') in _TARGET_SIDE_EVIDENCE_REQUEST_TYPES
    ]
    phase_selected_ids, phase_stale_ids = _filter_stale_menu_request_ids(workspace, phase_selected_ids)
    selected_ids = _dedupe_preserve_order(phase_selected_ids)
    stale_ids = _dedupe_preserve_order([*stale_ids, *phase_stale_ids])
    request_types_by_id = _request_summary_type_by_id(summaries)
    return {
        'requested_request_types': _dedupe_preserve_order(requested_types),
        'agenda_subject_refs': _dedupe_preserve_order(subject_refs),
        'matching_executable_request_ids': selected_ids,
        'matching_executable_request_types': _dedupe_preserve_order([
            request_types_by_id.get(request_id, '')
            for request_id in selected_ids
            if request_types_by_id.get(request_id, '')
        ]),
        'augmented_menu_request_ids': augmented_request_ids,
        'stale_matching_request_ids': stale_ids,
        'has_matching_executable_evidence': bool(selected_ids),
        **phase_audit,
    }


def _latest_requested_evidence_observation(workspace: CaseEvidenceWorkspace) -> dict[str, object]:
    requested_types, subject_refs = _latest_blocked_evidence_agenda(workspace)
    if not requested_types:
        return {
            'requested_request_types': [],
            'agenda_subject_refs': [],
            'matching_executable_request_ids': [],
            'matching_executable_request_types': [],
            'stale_matching_request_ids': [],
            'has_matching_executable_evidence': False,
        }
    return _orchestrator_requested_evidence_observation(
        workspace,
        requested_types=requested_types,
        subject_refs=subject_refs,
    )


def _mapping_intents_use_visible_target_choice(args: ProposeMappingIntentsToolArgs) -> bool:
    for intent in list(getattr(args, 'mapping_intents', []) or []):
        target_refs = [
            str(getattr(intent, 'chosen_item_ref', '') or ''),
            str(getattr(intent, 'chosen_span_ref', '') or ''),
            *[str(ref or '') for ref in list(getattr(intent, 'item_refs', []) or [])],
            *[str(ref or '') for ref in list(getattr(intent, 'target_refs', []) or [])],
        ]
        if any(ref.startswith(('BE', 'BES')) for ref in target_refs):
            return True
    return False


def _mapping_intents_make_terminal_or_target_progress(args: ProposeMappingIntentsToolArgs) -> bool:
    if _mapping_intents_use_visible_target_choice(args):
        return True
    terminal_or_progress_decisions = {
        'reject_candidate',
        'mark_non_bangumi_or_supplemental',
        'mark_unaligned_fail_closed',
    }
    return any(
        str(getattr(intent, 'decision', '') or '') in terminal_or_progress_decisions
        for intent in list(getattr(args, 'mapping_intents', []) or [])
    )


def _mapping_intents_can_bypass_requested_evidence(workspace: CaseEvidenceWorkspace, args: ProposeMappingIntentsToolArgs) -> bool:
    terminal_decisions = {
        'reject_candidate',
        'mark_non_bangumi_or_supplemental',
        'mark_unaligned_fail_closed',
    }
    draft_rows_by_ref = {
        ref: row
        for row in list(getattr(getattr(workspace, 'mapping_draft', None), 'rows', []) or [])
        for ref in (str(getattr(row, 'row_ref', '') or ''), str(getattr(row, 'local_ref', '') or ''))
        if ref
    }
    for intent in list(getattr(args, 'mapping_intents', []) or []):
        if str(getattr(intent, 'decision', '') or '') in terminal_decisions:
            return True
        if str(getattr(intent, 'chosen_span_ref', '') or '').startswith('BES'):
            return True
        item_refs = _dedupe_preserve_order([
            str(ref or '')
            for ref in [
                *list(getattr(intent, 'item_refs', []) or []),
                str(getattr(intent, 'chosen_item_ref', '') or ''),
            ]
            if str(ref or '').startswith('BE')
        ])
        raw_local_ref = str(getattr(intent, 'local_ref', '') or '')
        row = draft_rows_by_ref.get(raw_local_ref)
        local_ref = str(getattr(row, 'local_ref', '') or raw_local_ref)
        local_count = len(_local_file_refs_for_understanding_ref(workspace, local_ref))
        if item_refs and local_count > 0 and len(item_refs) == local_count:
            return True
    return False


def _latest_mapping_intent_blocker_is_structural(workspace: CaseEvidenceWorkspace) -> bool:
    structural_codes = {
        'invalid_explicit_multi_file_mapping',
        'item_ref_count_mismatch',
        'count_mismatch',
    }
    for audit in reversed(list(getattr(workspace, 'judge_request_audits', []) or [])):
        if not isinstance(audit, dict):
            continue
        if audit.get('note') != 'orchestrator_mapping_intents_result':
            continue
        issue_codes = {
            str(code or '')
            for code in list(audit.get('blocked_intent_issue_codes') or [])
            if str(code or '')
        }
        return bool(issue_codes & structural_codes)
    return False


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
        if isinstance(audit, dict) and audit.get('note') in {'orchestrator_mapping_intents_result', 'orchestrator_case_resolution_ledger_result'}:
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
    blocked_ledger_rows = list(latest.get('blocked_ledger_rows') or [])
    open_rows = list(latest.get('open_rows') or [])
    row_requested_types = [
        str(request_type or '')
        for row in open_rows
        if isinstance(row, dict)
        for request_type in list(row.get('requested_request_types') or [])
        if str(request_type or '')
    ]
    requested_types = _dedupe_preserve_order([*requested_types, *row_requested_types])
    subject_refs = _dedupe_preserve_order([
        str(ref or '')
        for item in blocked_intents
        if isinstance(item, dict)
        for ref in list(item.get('subject_refs') or [])
        if str(ref or '')
    ] + [
        str(ref or '')
        for item in blocked_ledger_rows
        if isinstance(item, dict)
        for ref in list(item.get('subject_refs') or [])
        if str(ref or '')
    ] + [
        str(ref or '')
        for row in open_rows
        if isinstance(row, dict)
        for ref in list(row.get('subject_refs') or [])
        if str(ref or '')
    ])
    return requested_types, subject_refs


def _has_deferred_subject_recall_agenda(workspace: CaseEvidenceWorkspace) -> bool:
    """Return whether agent/compiler state still asks for subject-side evidence.

    This is a finish-gate legality check, not a semantic decision: it only
    prevents no-new-evidence closure while an explicit subject recall agenda is
    still recorded in the notebook or latest mapping-intent compiler output.
    """
    subject_side_types = {'subject_search', 'subject_lookup', 'episode_list'}
    requested_types, subject_refs = _latest_blocked_evidence_agenda(workspace)
    if set(requested_types) & subject_side_types:
        return True
    if subject_refs and set(requested_types) & _REQUIRES_ITEM_EVIDENCE_TYPES:
        return True
    notebook = getattr(workspace, 'investigation_notebook', None)
    if notebook is not None:
        for question in list(getattr(notebook, 'open_questions', []) or []):
            if str(getattr(question, 'status', '') or '') != 'open':
                continue
            request_types = {str(value or '') for value in list(getattr(question, 'requested_request_types', []) or [])}
            if request_types & subject_side_types:
                return True
            if list(getattr(question, 'query_hints', []) or []):
                return True
        for action in list(getattr(notebook, 'next_actions', []) or []):
            if str(getattr(action, 'status', '') or '') != 'open':
                continue
            action_type = str(getattr(action, 'action_type', '') or '')
            request_types = {str(value or '') for value in list(getattr(action, 'requested_request_types', []) or [])}
            if action_type in {'subject_recall', 'episode_recall'}:
                return True
            if request_types & subject_side_types:
                return True
            if list(getattr(action, 'query_hints', []) or []):
                return True
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is not None:
        for row in list(getattr(draft, 'rows', []) or []):
            if str(getattr(row, 'disposition', '') or '') != 'needs_more_evidence':
                continue
            request_types = {str(value or '') for value in list(getattr(row, 'requested_request_types', []) or [])}
            if request_types & subject_side_types:
                return True
            if list(getattr(row, 'query_hints', []) or []):
                return True
    return False


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
    explicit_requested_subject_refs = _dedupe_preserve_order([str(ref or '') for ref in list(subject_refs or [])])
    selected_source_subject_refs = _dedupe_preserve_order([
            ref
            for request_id in list(selected_ids or [])
            for ref in _request_summary_source_refs(next((summary for summary in summaries if str(summary.get('request_id') or '') == request_id), {}))
            if str(ref or '').startswith('BS')
    ])
    requested_subject_refs = explicit_requested_subject_refs or selected_source_subject_refs
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
        if note == 'case_understanding_rejected':
            issue_codes = {
                str(code or '')
                for code in list(audit.get('issue_codes') or [])
                if str(code or '')
            }
            if 'case_understanding_noop_repartition' in issue_codes:
                return False
        if note in {'case_understanding_repartition_requested', 'orchestrator_reconsider_split_requested'}:
            return True
        if note == 'orchestrator_reconsider_split_observation' and bool(audit.get('repartition_requested')):
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


def _structural_case_understanding_repartition_requested(workspace: CaseEvidenceWorkspace) -> bool:
    structural_codes = {'invalid_explicit_multi_file_mapping', 'item_ref_count_mismatch', 'count_mismatch'}
    for audit in reversed(list(getattr(workspace, 'judge_request_audits', []) or [])):
        if not isinstance(audit, dict):
            continue
        note = str(audit.get('note') or '')
        if note in {'case_understanding_applied', 'case_understanding_revised'}:
            break
        if note == 'case_understanding_rejected':
            issue_codes = {
                str(code or '')
                for code in list(audit.get('issue_codes') or [])
                if str(code or '')
            }
            if 'case_understanding_noop_repartition' in issue_codes:
                return False
        if note == 'case_understanding_repartition_requested':
            issue_codes = {str(code or '') for code in list(audit.get('issue_codes') or [])}
            if issue_codes & structural_codes:
                return True
    return False


def _case_understanding_revision_available_for_tool_call(workspace: CaseEvidenceWorkspace) -> bool:
    if not _case_understanding_applied(workspace):
        return False
    if _case_understanding_repartition_requested(workspace):
        return True
    audits = list(getattr(workspace, 'judge_request_audits', []) or [])
    latest_reconsider_index = max(
        [
            index for index, audit in enumerate(audits)
            if isinstance(audit, dict) and audit.get('note') == 'orchestrator_reconsider_split_observation'
        ],
        default=-1,
    )
    latest_understanding_index = max(
        [
            index for index, audit in enumerate(audits)
            if isinstance(audit, dict) and audit.get('note') in {'case_understanding_compiled', 'case_understanding_applied', 'case_understanding_revised'}
        ],
        default=-1,
    )
    if latest_reconsider_index >= 0 and latest_understanding_index < latest_reconsider_index:
        return True
    return False


def _split_decision_required(workspace: CaseEvidenceWorkspace) -> bool:
    audits = list(getattr(workspace, 'judge_request_audits', []) or [])
    latest_required_index = max(
        [
            index for index, audit in enumerate(audits)
            if isinstance(audit, dict) and audit.get('note') == 'orchestrator_split_decision_required'
        ],
        default=-1,
    )
    if latest_required_index < 0:
        return False
    for audit in audits[latest_required_index + 1:]:
        if not isinstance(audit, dict):
            continue
        note = str(audit.get('note') or '')
        if (
            note == 'orchestrator_tool_output_rejected'
            and str(audit.get('tool_name') or '') == 'split_into_child_cases'
            and str(audit.get('reason') or '') == 'split_depth_limit_reached'
        ):
            return False
        if note in {
            'orchestrator_split_into_child_cases_result',
            'orchestrator_selected_child_cases_result',
            'orchestrator_split_decision_deferred_by_mapping_progress',
            'finish_case_fail_closed_verified',
            'finish_case_accepted_accounting_checked',
        }:
            return False
        if note == 'orchestrator_mapping_intents_result':
            if str(audit.get('status') or '') in {'accepted_verified'}:
                return False
    return True


def _workspace_planning_depth(workspace: CaseEvidenceWorkspace) -> int:
    for audit in reversed(list(getattr(workspace, 'judge_request_audits', []) or [])):
        if not isinstance(audit, dict):
            continue
        if audit.get('note') != 'orchestrator_case_session_started':
            continue
        try:
            return max(0, int(audit.get('planning_depth') or 0))
        except (TypeError, ValueError):
            return 0
    return 0


def _default_orchestrator_max_turns_for_workspace(workspace: CaseEvidenceWorkspace) -> int:
    base = max(1, int(getattr(getattr(workspace, 'budget', None), 'max_judge_rounds', 0) or 12))
    main_count = len(list(getattr(getattr(workspace, 'contract', None), 'main_file_refs', []) or []))
    if main_count >= 100:
        return max(base, 18)
    if main_count >= 50:
        return max(base, 16)
    if main_count >= 20:
        return max(base, 14)
    return base


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


def _normalize_case_understanding_unit_refs(
    workspace: CaseEvidenceWorkspace,
    units: list[CaseBriefingWorkUnit],
) -> list[CaseBriefingWorkUnit]:
    visible_local_span_refs = {
        str(getattr(card, 'ref', '') or '')
        for card in list(getattr(workspace, 'local_span_cards', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }
    normalized: list[CaseBriefingWorkUnit] = []
    for unit in list(units or []):
        explicit_file_refs = _dedupe_preserve_order([
            file_ref
            for ref in list(getattr(unit, 'file_refs', []) or [])
            for file_ref in _local_file_refs_for_understanding_ref(workspace, ref)
        ])
        if not explicit_file_refs:
            normalized.append(unit)
            continue
        local_refs = [
            str(ref or '')
            for ref in list(getattr(unit, 'local_refs', []) or [])
            if str(ref or '') and (
                str(ref or '') in explicit_file_refs
                or str(ref or '') in visible_local_span_refs
            )
        ]
        span_refs = [
            str(ref or '')
            for ref in list(getattr(unit, 'span_refs', []) or [])
            if str(ref or '') and str(ref or '') in visible_local_span_refs
        ]
        normalized.append(unit.model_copy(update={
            'file_refs': explicit_file_refs,
            'local_refs': _dedupe_preserve_order(local_refs),
            'span_refs': _dedupe_preserve_order(span_refs),
        }))
    return normalized


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


def _leaf_parent_key_for_local_file(card) -> str:
    path = str(getattr(card, 'path', '') or getattr(card, 'label', '') or '').replace('\\', '/')
    parts = [part for part in path.split('/') if part]
    if len(parts) > 1:
        return '/'.join(parts[:-1])
    return str(getattr(card, 'parent_display', '') or '<root>')


def _local_main_file_group_index(workspace: CaseEvidenceWorkspace) -> dict[str, dict[str, object]]:
    main_refs = list(getattr(getattr(workspace, 'contract', None), 'main_file_refs', []) or [])
    main_ref_set = set(main_refs)
    ordered_files = [
        card for card in list(getattr(workspace, 'local_files', []) or [])
        if str(getattr(card, 'ref', '') or '') in main_ref_set or (not main_ref_set and bool(getattr(card, 'is_main', False)))
    ]
    by_ref = {str(getattr(card, 'ref', '') or ''): card for card in ordered_files}
    if main_refs:
        ordered_files = [by_ref[ref] for ref in main_refs if ref in by_ref]
    groups_by_key: dict[str, dict[str, object]] = {}
    groups_by_ref: dict[str, dict[str, object]] = {}
    for card in ordered_files:
        ref = str(getattr(card, 'ref', '') or '')
        if not ref:
            continue
        group_key = _leaf_parent_key_for_local_file(card)
        entry = groups_by_key.get(group_key)
        if entry is None:
            entry = {
                'group_ref': f'LG{len(groups_by_key) + 1}',
                'group_key': group_key,
                'file_refs': [],
            }
            groups_by_key[group_key] = entry
            groups_by_ref[str(entry['group_ref'])] = entry
        file_refs = entry.get('file_refs')
        if isinstance(file_refs, list):
            file_refs.append(ref)
    return groups_by_ref


def _expand_split_group_refs(
    workspace: CaseEvidenceWorkspace,
    split_cases: list[SplitCaseSpec],
) -> tuple[list[SplitCaseSpec], dict[str, object]]:
    group_index = _local_main_file_group_index(workspace)
    expanded: list[SplitCaseSpec] = []
    expanded_by_child: dict[str, dict[str, list[str]]] = {}
    unknown_group_refs: list[str] = []
    for spec in split_cases:
        child_ref = str(getattr(spec, 'child_case_ref', '') or 'split_case')
        main_refs = _dedupe_preserve_order([
            str(ref or '')
            for ref in list(getattr(spec, 'main_file_refs', []) or [])
            if str(ref or '')
        ])
        supplemental_refs = _dedupe_preserve_order([
            str(ref or '')
            for ref in list(getattr(spec, 'supplemental_file_refs', []) or [])
            if str(ref or '')
        ])
        child_expanded_main: list[str] = []
        child_expanded_supplemental: list[str] = []
        for group_ref in list(getattr(spec, 'main_group_refs', []) or []):
            value = str(group_ref or '')
            if not value:
                continue
            entry = group_index.get(value)
            if entry is None:
                unknown_group_refs.append(value)
                continue
            refs = [str(ref or '') for ref in list(entry.get('file_refs') or []) if str(ref or '')]
            main_refs = _dedupe_preserve_order([*main_refs, *refs])
            child_expanded_main.extend(refs)
        for group_ref in list(getattr(spec, 'supplemental_group_refs', []) or []):
            value = str(group_ref or '')
            if not value:
                continue
            entry = group_index.get(value)
            if entry is None:
                unknown_group_refs.append(value)
                continue
            refs = [str(ref or '') for ref in list(entry.get('file_refs') or []) if str(ref or '')]
            supplemental_refs = _dedupe_preserve_order([*supplemental_refs, *refs])
            child_expanded_supplemental.extend(refs)
        if child_expanded_main or child_expanded_supplemental:
            expanded_by_child[child_ref] = {
                'main_file_refs': _sample_refs(_dedupe_preserve_order(child_expanded_main), limit=12),
                'supplemental_file_refs': _sample_refs(_dedupe_preserve_order(child_expanded_supplemental), limit=12),
            }
        expanded.append(spec.model_copy(update={
            'main_file_refs': main_refs,
            'supplemental_file_refs': supplemental_refs,
        }))
    return expanded, {
        'split_group_refs_expanded': bool(expanded_by_child),
        'expanded_group_file_refs_by_child': expanded_by_child,
        'unknown_split_group_refs': _sample_refs(_dedupe_preserve_order(unknown_group_refs), limit=24),
        'available_local_group_refs': [
            {
                'group_ref': group_ref,
                'group_key': str(entry.get('group_key') or ''),
                'file_ref_count': len(list(entry.get('file_refs') or [])),
                'file_ref_range': _sample_refs(list(entry.get('file_refs') or []), limit=2),
            }
            for group_ref, entry in list(group_index.items())[:24]
        ],
    }


def _split_case_skeleton_from_work_units(
    workspace: CaseEvidenceWorkspace,
    compiled_units: list[CaseBriefingWorkUnit],
) -> list[dict[str, object]]:
    group_index = _local_main_file_group_index(workspace)
    ordered_groups = list(group_index.items())
    skeleton: list[dict[str, object]] = []
    for index, unit in enumerate(compiled_units, start=1):
        file_refs = _dedupe_preserve_order([
            str(ref or '')
            for ref in list(getattr(unit, 'file_refs', []) or [])
            if str(ref or '')
        ])
        file_ref_set = set(file_refs)
        group_refs: list[str] = []
        covered_refs: set[str] = set()
        for group_ref, group in ordered_groups:
            group_files = [
                str(ref or '')
                for ref in list(group.get('file_refs') or [])
                if str(ref or '')
            ]
            if group_files and set(group_files).issubset(file_ref_set):
                group_refs.append(group_ref)
                covered_refs.update(group_files)
        remaining_file_refs = [ref for ref in file_refs if ref not in covered_refs]
        skeleton.append({
            'child_case_ref': f'SPLIT{index}',
            'main_group_refs': group_refs,
            'main_file_refs': remaining_file_refs,
            'expanded_main_file_count': len(file_refs),
            'expanded_main_file_range': [file_refs[0], file_refs[-1]] if file_refs else [],
            'title_hints': list(getattr(unit, 'title_hints', []) or [])[:6],
            'query_hints': list(getattr(unit, 'query_hints', []) or [])[:6],
            'reason': str(getattr(unit, 'reason', '') or getattr(unit, 'label', '') or ''),
        })
    return skeleton[:24]


def _shape_categories_for_local_file(card) -> set[str]:
    text = ' '.join([
        str(getattr(card, 'path', '') or ''),
        str(getattr(card, 'label', '') or ''),
    ]).casefold()
    categories: set[str] = set()
    if re.search(r'(?i)(?:^|[\[\]\s._-])((?:nc)?op\d{0,2}|(?:nc)?ed\d{0,2}|sp\d{0,3}|ova\d{0,3}|oad\d{0,3}|oav\d{0,3}|menu|pv\d{0,3}|cm\d{0,3}|preview|trailer)(?:$|[\[\]\s._-])', text):
        categories.add('extra_marker')
    if re.search(r'(?i)(?:^|[\[\]\s._-])(\d{1,3})(?:$|[\[\]\s._-])', text):
        categories.add('numbered')
    return categories


def _mixed_leaf_group_shape_issue(
    workspace: CaseEvidenceWorkspace,
    unit_ref: str,
    file_refs: list[str],
) -> VerifierIssue | None:
    if len(file_refs) < 2:
        return None
    file_by_ref = {
        str(getattr(card, 'ref', '') or ''): card
        for card in list(getattr(workspace, 'local_files', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }
    leaf_groups: dict[str, set[str]] = {}
    for file_ref in file_refs:
        card = file_by_ref.get(str(file_ref or ''))
        if card is None:
            continue
        group_key = _leaf_parent_key_for_local_file(card)
        leaf_groups.setdefault(group_key, set()).update(_shape_categories_for_local_file(card))
    if len(leaf_groups) <= 1:
        return None
    has_extra_leaf = any('extra_marker' in categories for categories in leaf_groups.values())
    has_numbered_leaf = any('numbered' in categories for categories in leaf_groups.values())
    if not (has_extra_leaf and has_numbered_leaf):
        return None
    return VerifierIssue(
        ref=unit_ref or 'case_understanding',
        issue_code='case_understanding_mixed_leaf_groups',
        severity='blocked',
        message=(
            'work unit mixes multiple leaf parent directories with regular-numbered and extra/SP/menu markers; '
            'split these leaf groups into separate work_units so each row can receive one legal accounting outcome'
        ),
        related_refs=file_refs[:24],
    )


def _compile_case_understanding(
    workspace: CaseEvidenceWorkspace,
    args: ProposeCaseUnderstandingToolArgs,
) -> tuple[CaseEvidenceWorkspace, dict[str, object]]:
    was_revision = _case_understanding_applied(workspace)
    raw_units = _normalize_case_understanding_unit_refs(workspace, list(getattr(args, 'work_units', []) or []))
    main_refs = list(dict.fromkeys(list(getattr(getattr(workspace, 'contract', None), 'main_file_refs', []) or [])))
    repartition_requested = _case_understanding_repartition_requested(workspace)
    structural_repartition_requested = _structural_case_understanding_repartition_requested(workspace)
    terminal_covered_refs: list[str] = []
    if was_revision and repartition_requested and getattr(workspace, 'mapping_draft', None) is not None:
        for row in list(getattr(workspace.mapping_draft, 'rows', []) or []):
            if (
                str(getattr(row, 'disposition', '') or '') in {'map_to_bangumi', 'non_bangumi_or_supplemental'}
                or str(getattr(row, 'status', '') or '') == 'verified'
            ):
                terminal_covered_refs.extend(_local_file_refs_for_understanding_ref(workspace, str(getattr(row, 'local_ref', '') or '')))
    terminal_covered_ref_set = set(terminal_covered_refs)
    open_row_main_refs: list[str] = []
    existing_open_partitions: list[list[str]] = []
    if was_revision and repartition_requested:
        open_rows = _open_rows_observation(workspace, limit=64)
        for row in open_rows:
            if not isinstance(row, dict):
                continue
            refs = _local_file_refs_for_understanding_ref(workspace, str(row.get('local_ref') or ''))
            if not refs:
                refs = [str(ref or '') for ref in list(row.get('file_ref_samples') or []) if str(ref or '')]
            for ref in refs:
                if str(ref or '') and str(ref or '') not in terminal_covered_ref_set and str(ref or '') not in open_row_main_refs:
                    open_row_main_refs.append(str(ref))
        open_ref_set_for_partition = set(open_row_main_refs)
        if open_ref_set_for_partition:
            for row in open_rows:
                if not isinstance(row, dict):
                    continue
                refs = _local_file_refs_for_understanding_ref(workspace, str(row.get('local_ref') or ''))
                if not refs:
                    refs = [str(ref or '') for ref in list(row.get('file_ref_samples') or []) if str(ref or '')]
                scoped = _dedupe_preserve_order([
                    str(ref or '')
                    for ref in refs
                    if str(ref or '') in open_ref_set_for_partition
                ])
                if scoped:
                    existing_open_partitions.append(scoped)
    required_main_refs = open_row_main_refs if open_row_main_refs else main_refs
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
        mixed_leaf_issue = _mixed_leaf_group_shape_issue(workspace, unit_ref, file_refs)
        if mixed_leaf_issue is not None:
            issues.append(mixed_leaf_issue)

    if was_revision and _split_decision_required(workspace):
        current_units = list(getattr(getattr(workspace, 'case_briefing', None), 'work_units', []) or [])
        if current_units:
            main_ref_order = {ref: index for index, ref in enumerate(main_refs)}

            def _partition_signature(partitions: list[list[str]]) -> tuple[tuple[str, ...], ...]:
                normalized: list[tuple[str, ...]] = []
                for partition in partitions:
                    scoped = [
                        str(ref or '')
                        for ref in list(partition or [])
                        if str(ref or '') in main_ref_order
                    ]
                    if not scoped:
                        continue
                    normalized.append(tuple(sorted(
                        set(scoped),
                        key=lambda value: main_ref_order.get(value, len(main_ref_order)),
                    )))
                return tuple(sorted(
                    normalized,
                    key=lambda item: main_ref_order.get(item[0], len(main_ref_order)) if item else len(main_ref_order),
                ))

            current_partitions = [
                _understanding_unit_file_refs(workspace, unit)
                for unit in current_units
            ]
            proposed_partitions = [file_refs for _unit, file_refs in expanded_by_unit]
            if (
                _partition_signature(current_partitions)
                and _partition_signature(current_partitions) == _partition_signature(proposed_partitions)
            ):
                issues.append(VerifierIssue(
                    ref='case_understanding',
                    issue_code='case_understanding_repeats_pending_boundary_without_partition_change',
                    severity='blocked',
                    message=(
                        'a package boundary decision is still pending, but this understanding revision kept the same '
                        'work-unit file partition. This is a mechanical no-op; choose split_into_child_cases, '
                        'propose_case_resolution_ledger, execute concrete evidence, mapping intents, or submit a '
                        'changed partition instead.'
                    ),
                    related_refs=[ref for partition in proposed_partitions for ref in partition][:24],
                ))

    if was_revision and structural_repartition_requested and len(required_main_refs) > 1:
        normalized_required = set(required_main_refs)
        required_order = {ref: index for index, ref in enumerate(required_main_refs)}

        def _partition_signature(partitions: list[list[str]]) -> tuple[tuple[str, ...], ...]:
            normalized: list[tuple[str, ...]] = []
            for partition in partitions:
                scoped = [
                    ref for ref in partition
                    if ref in normalized_required
                ]
                if not scoped:
                    continue
                normalized.append(tuple(sorted(
                    set(scoped),
                    key=lambda value: required_order.get(value, len(required_order)),
                )))
            return tuple(sorted(
                normalized,
                key=lambda item: required_order.get(item[0], len(required_order)) if item else len(required_order),
            ))

        revised_open_partitions = [
            [ref for ref in file_refs if ref in normalized_required]
            for _unit, file_refs in expanded_by_unit
        ]
        no_op_repartition = (
            len(expanded_by_unit) == 1
            and set(expanded_by_unit[0][1]) == normalized_required
        )
        if not no_op_repartition and existing_open_partitions:
            no_op_repartition = (
                _partition_signature(existing_open_partitions)
                == _partition_signature(revised_open_partitions)
            )
        if no_op_repartition:
            issues.append(VerifierIssue(
                ref='case_understanding',
                issue_code='case_understanding_noop_repartition',
                severity='blocked',
                message='repartition was requested after a shape/count mismatch, but the revision kept the same open-row file partition',
                related_refs=required_main_refs[:24],
            ))

    if main_refs:
        missing = [ref for ref in required_main_refs if ref not in ownership]
        duplicates = [ref for ref, owners in ownership.items() if len(owners) > 1]
        if missing:
            message = 'work units must cover every currently open row main file ref exactly once' if open_row_main_refs else 'work units must cover every main file ref exactly once'
            issues.append(VerifierIssue(ref='case_understanding', issue_code='case_understanding_missing_main_refs', severity='blocked', message=message, related_refs=missing[:12]))
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
    if open_row_main_refs:
        issues = [
            issue for issue in issues
            if str(getattr(issue, 'issue_code', '') or '') != 'briefing_main_refs_uncovered'
        ]
    if issues:
        issue_codes = _dedupe_preserve_order([str(getattr(issue, 'issue_code', '') or '') for issue in issues])
        workspace = _workspace_with_judge_audit(workspace, {
            'note': 'case_understanding_rejected',
            'issue_codes': issue_codes,
            'issues': [issue.model_dump(mode='json') for issue in issues[:12]],
            'reason': str(getattr(args, 'reason', '') or ''),
            'existing_open_partitions': existing_open_partitions[:12],
            'required_main_refs': required_main_refs[:32],
        })
        return workspace, {
            'status': 'rejected',
            'reason': 'case_understanding_contract_failed',
            'issue_codes': issue_codes,
            'issues': [issue.model_dump(mode='json') for issue in issues[:12]],
            'existing_open_partitions': existing_open_partitions[:12],
            'required_main_refs': required_main_refs[:32],
            'recommended_next_observation': (
                'retry propose_case_understanding with work units that cite visible LF/LS refs and cover every main LF exactly once. '
                'If this was requested after a shape/count mismatch, the revision must actually change the open-row partition: split the open multi-file row into smaller work_units or singleton files instead of returning the same unit.'
            ),
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
    preserve_existing_case_memory = was_revision and getattr(workspace, 'mapping_draft', None) is not None and not repartition_requested
    partial_repartition = bool(was_revision and repartition_requested and open_row_main_refs and len(open_row_main_refs) < len(main_refs))
    if partial_repartition and getattr(workspace, 'mapping_draft', None) is not None:
        preserved_rows = [
            row for row in list(getattr(workspace.mapping_draft, 'rows', []) or [])
            if str(getattr(row, 'disposition', '') or '') in {'map_to_bangumi', 'non_bangumi_or_supplemental'}
            or str(getattr(row, 'status', '') or '') == 'verified'
        ]
        next_row_index = max([
            int(str(getattr(row, 'row_ref', '') or 'MDR0').replace('MDR', '') or 0)
            for row in preserved_rows
            if str(getattr(row, 'row_ref', '') or '').startswith('MDR')
        ] or [0]) + 1
        open_ref_set = set(open_row_main_refs)
        repartition_unit_indexes = [
            unit_index
            for unit_index, (_unit, file_refs) in enumerate(expanded_by_unit, start=1)
            if not open_ref_set or bool(open_ref_set.intersection(file_refs))
        ]
        repartition_rows = [
            MappingDraftRow(
                row_ref=f'MDR{next_row_index + row_offset}',
                local_ref=f'LS{unit_index}',
                local_ref_kind='span',
                disposition='open',
                status='open',
            )
            for row_offset, unit_index in enumerate(repartition_unit_indexes)
        ]
        preserved_mapping_draft = MappingDraft(rows=[*preserved_rows, *repartition_rows], version=int(getattr(workspace.mapping_draft, 'version', 0) or 0) + 1)
        preserved_mapping_draft_patches = list(getattr(workspace, 'mapping_draft_patches', []) or [])
        preserved_mapping_draft_comparisons = list(getattr(workspace, 'mapping_draft_candidate_comparisons', []) or [])
    else:
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
    if not partial_repartition:
        staged = _workspace_with_initial_mapping_draft(staged)
    staged = _refresh_mapping_draft_candidates(staged)
    staged = _workspace_with_tool_accounting_audit(staged, note='case_understanding_mapping_draft_accounting')
    split_case_skeleton = _split_case_skeleton_from_work_units(staged, compiled_units)
    staged = _workspace_with_judge_audit(staged, {
        'note': 'case_understanding_revised' if was_revision else 'case_understanding_applied',
        'work_unit_count': len(compiled_units),
        'local_span_refs': [span.ref for span in compiled_spans],
        'split_case_skeleton_from_work_units': split_case_skeleton,
        'title_hypothesis_count': len(list(getattr(briefing, 'title_hypotheses', []) or [])),
        'open_question_count': len(list(getattr(getattr(staged, 'investigation_notebook', None), 'open_questions', []) or [])),
        'repartition_requested': repartition_requested,
        'partial_repartition': partial_repartition,
        'preserved_mapping_draft': preserve_existing_case_memory,
        'preserved_notebook': preserve_existing_case_memory,
        'reason': str(getattr(args, 'reason', '') or ''),
    })
    planning_depth = _workspace_planning_depth(staged)
    multi_unit_split_pending = (
        not was_revision
        and planning_depth < MAX_ORCHESTRATOR_SPLIT_DEPTH
        and len(compiled_units) >= 2
        and any(len(list(getattr(unit, 'file_refs', []) or [])) > 1 for unit in compiled_units)
    )
    if multi_unit_split_pending:
        staged = _workspace_with_judge_audit(staged, {
            'note': 'orchestrator_split_decision_required',
            'reason': 'case understanding produced multiple non-singleton work units without a later split or terminal root resolution',
            'work_unit_count': len(compiled_units),
            'main_file_count': len(main_refs),
            'split_case_skeleton_from_work_units': split_case_skeleton,
            'recommended_next_observation': (
                'This is a boundary observation only: for a large package with multiple non-singleton work units, '
                'a human-like continuation often records a split plan or runs selected child cases from split_case_skeleton_from_work_units. '
                'The agent may still continue root-level evidence/ledger/mapping/finish; if it does, the root ledger should make '
                'each regular work-unit outcome concrete enough to preserve target ownership focus.'
            ),
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
        'split_decision_required': multi_unit_split_pending,
        'split_case_skeleton_from_work_units': split_case_skeleton,
        'recommended_next_observation': (
            'multi-unit package with non-singleton work units: a human-like next action often records a split plan or runs selected child cases from split_case_skeleton_from_work_units. Root evidence/ledger/mapping remains legal; if chosen, it should concretely resolve every regular work unit without broad target_absent/supplemental shortcuts.'
            if multi_unit_split_pending
            else 'materialize clean title queries or execute visible evidence; if enough Bangumi target surface is already visible, propose mapping intents'
        ),
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
    # Local->Bangumi primary path is the human-like locator/tool runtime.  The
    # old OrchestratorAgent helpers below stay importable for focused tests and
    # migration diagnostics, but they are not a fallback path from this entry.
    from .human_case_agent import run_human_case_agent

    return run_human_case_agent(
        initial_workspace,
        ai_client,
        bangumi_client,
        max_rounds=max_rounds,
        orchestrator_context_soft_token_limit=orchestrator_context_soft_token_limit,
        orchestrator_context_hard_token_limit=orchestrator_context_hard_token_limit,
        planning_depth=_planning_depth,
    )




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


def _local_file_label_samples_for_refs(
    workspace: CaseEvidenceWorkspace,
    file_refs: list[str],
    *,
    limit: int = 8,
) -> list[dict[str, object]]:
    file_by_ref = {
        str(getattr(card, 'ref', '') or ''): card
        for card in list(getattr(workspace, 'local_files', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }
    samples: list[dict[str, object]] = []
    for ref in list(file_refs or []):
        card = file_by_ref.get(str(ref or ''))
        if card is None:
            continue
        path = str(getattr(card, 'path', '') or '')
        label = str(getattr(card, 'label', '') or path.rsplit('\\', 1)[-1].rsplit('/', 1)[-1])
        bracket_title_tokens: list[str] = []
        for token in re.findall(r'\[([^\[\]]{2,80})\]', label):
            normalized = str(token or '').strip()
            folded = normalized.casefold()
            if (
                not normalized
                or folded in {'vcb-studio', 'ma10p_1080p', 'x265_flac', 'x265_flac_aac'}
                or re.fullmatch(r'\d{1,3}', normalized)
                or re.search(r'(?i)(1080p|720p|x26[45]|flac|aac|hevc|avc|ma10p)', normalized)
            ):
                continue
            bracket_title_tokens.append(normalized[:80])
        samples.append({
            'ref': str(ref or ''),
            'label': label[:180],
            'path': path[:240],
            'bracket_title_tokens': _dedupe_preserve_order(bracket_title_tokens)[:4],
        })
        if len(samples) >= limit:
            break
    return samples


def _local_ref_brief(workspace: CaseEvidenceWorkspace, local_ref: str) -> dict[str, object]:
    for span in list(getattr(workspace, 'local_span_cards', []) or []):
        if str(getattr(span, 'ref', '') or '') == local_ref:
            span_file_refs = [str(ref or '') for ref in list(getattr(span, 'file_refs', []) or []) if str(ref or '')]
            return {
                'local_ref': local_ref,
                'local_ref_kind': 'span',
                'file_ref_count': int(getattr(span, 'file_ref_count', 0) or len(span_file_refs) or 0),
                'file_ref_samples': list(getattr(span, 'file_ref_samples', []) or [])[:8] or span_file_refs[:8],
                'file_label_samples': _local_file_label_samples_for_refs(workspace, span_file_refs, limit=8),
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
    subject_title_by_ref = {
        str(getattr(subject, 'ref', '') or ''): str(getattr(subject, 'title', '') or getattr(subject, 'name_cn', '') or getattr(subject, 'name', '') or '')
        for subject in list(getattr(workspace, 'bangumi_subjects', []) or [])
        if str(getattr(subject, 'ref', '') or '')
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
                'subject_title': subject_title_by_ref.get(str(getattr(span, 'subject_ref', '') or ''), ''),
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
                'subject_title': subject_title_by_ref.get(str(getattr(item, 'subject_ref', '') or ''), ''),
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


def _verifier_issue_target_ownership_observation(workspace: CaseEvidenceWorkspace, issues: list[VerifierIssue], *, limit: int = 32) -> list[dict[str, object]]:
    target_refs = _dedupe_preserve_order([
        str(ref or '')
        for issue in list(issues or [])
        for ref in [str(getattr(issue, 'ref', '') or ''), *list(getattr(issue, 'related_refs', []) or [])]
        if str(ref or '').startswith(('BE', 'BES'))
    ])
    if not target_refs:
        return []
    return _target_ref_ownership_observation(workspace, target_refs, limit=limit)


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
    sequence_filters: list[tuple[str, set[str]]] = [
        ('regular', {'episode', 'regular', 'unknown', ''}),
        ('special', {'special', 'movie'}),
    ]
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
            all_item_refs_unowned = bool(refs and not occupied_refs)
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
                'all_item_refs_unowned': all_item_refs_unowned,
                'mapping_legality': 'available' if all_item_refs_unowned else 'occupied_by_existing_rows',
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


def _non_progress_needs_more_evidence_observations(
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
            and int(sequence.get('unowned_item_ref_count') or 0) == int(sequence.get('item_ref_count') or 0)
            for sequence in sequences
        )
        if not has_span_candidate and not has_actionable_visible_sequence:
            continue
        issues.append(VerifierIssue(
            ref=str(getattr(row, 'row_ref', '') or local_ref or 'mapping_draft_row'),
            issue_code='non_progress_needs_more_evidence_with_visible_candidates',
            severity='warning',
            message='needs_more_evidence may not make progress while this open row already has visible candidate targets or same-count item sequences; if more evidence is still needed, the agent must name the missing fact/request type',
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
        target_briefs = _target_ref_briefs(workspace, candidate_refs, limit=12)
        unowned_candidate_refs = _unowned_candidate_target_refs_for_row(workspace, row)[:12]
        visible_sequences = _visible_subject_item_sequences_for_row(
            workspace,
            row,
            file_count=file_count,
            candidate_refs=candidate_refs,
        )
        same_count_sequences = [
            sequence for sequence in visible_sequences
            if isinstance(sequence, dict) and bool(sequence.get('matches_local_file_count'))
        ]
        legal_same_count_sequences = [
            sequence for sequence in same_count_sequences
            if not bool(sequence.get('item_refs_truncated'))
            and int(sequence.get('unowned_item_ref_count') or 0) == int(sequence.get('item_ref_count') or 0)
        ]
        occupied_same_count_sequences = [
            sequence for sequence in same_count_sequences
            if sequence not in legal_same_count_sequences
        ]
        occupied_same_count_owner_rows = _dedupe_preserve_order([
            str(owner or '')
            for sequence in occupied_same_count_sequences
            for owner in list(sequence.get('owner_row_refs') or [])
            if str(owner or '')
        ])
        singleton_candidate_refs = [
            str(brief.get('ref') or '')
            for brief in target_briefs
            if str(brief.get('ref') or '') in set(unowned_candidate_refs)
            and str(brief.get('target_kind') or '') == 'item'
            and str(brief.get('item_kind') or '') in {'movie', 'special'}
        ]
        multi_singleton_candidate_pool = {
            'choose_exactly': file_count,
            'candidate_item_refs': singleton_candidate_refs[:12],
            'candidate_target_briefs': [
                brief for brief in target_briefs
                if str(brief.get('ref') or '') in set(singleton_candidate_refs)
            ][:12],
            'valid_intent_shape': 'map_regular_span with item_refs containing exactly one BE per local file',
        } if file_count > 1 and len(singleton_candidate_refs) >= file_count else {}
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
        elif occupied_same_count_sequences and not legal_same_count_sequences:
            recommended = (
                'same-count visible item sequences exist, but their BE refs are already occupied by existing rows '
                f'{occupied_same_count_owner_rows[:8]}. Do not reuse those occupied targets. If the owner row is semantically wrong, '
                'revise that owner row; otherwise choose a non-overlapping target, repartition/split, request concrete evidence, '
                'or mark target_absent/supplemental if that is your semantic conclusion.'
            )
        elif candidate_refs:
            if file_count > 1 and any(str(ref or '').startswith('BES') for ref in candidate_refs):
                recommended = 'for this multi-file row, propose map_regular_span with chosen_span_ref set to the visible BES* candidate, or use target_absent/supplemental if you judge the visible candidates do not correspond'
            elif multi_singleton_candidate_pool:
                recommended = 'for this multi-file row made of singleton movie/special candidates, choose exactly one visible BE per local file with map_regular_span item_refs, repartition if the files need separate rows, or use target_absent/supplemental if candidates do not correspond'
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
            'candidate_target_briefs': target_briefs,
            'candidate_target_conflicts': _candidate_target_conflicts_for_row(workspace, row),
            'unowned_candidate_target_refs': unowned_candidate_refs,
            'multi_singleton_candidate_pool': multi_singleton_candidate_pool,
            'requested_request_types': requested_types[:8],
            'query_hints': list(getattr(row, 'query_hints', []) or [])[:8],
            'subject_refs': list(getattr(row, 'subject_refs', []) or [])[:8],
            'item_refs': list(getattr(row, 'item_refs', []) or [])[:8],
            'visible_subject_item_sequences': visible_sequences,
            **local_brief,
            'protocol_warning': protocol_warning,
            'recommended_next': recommended,
        })
    return observations


def _row_has_same_count_target_surface(workspace: CaseEvidenceWorkspace, row: MappingDraftRow) -> bool:
    local_ref = str(getattr(row, 'local_ref', '') or '')
    local_brief = _local_ref_brief(workspace, local_ref)
    file_count = int(local_brief.get('file_ref_count') or 0)
    if file_count <= 0:
        return False
    if file_count == 1:
        return True
    candidate_refs = [str(ref or '') for ref in list(getattr(row, 'candidate_target_refs', []) or []) if str(ref or '')]
    span_by_ref = {
        str(getattr(span, 'ref', '') or ''): span
        for span in list(getattr(workspace, 'bangumi_span_cards', []) or [])
        if str(getattr(span, 'ref', '') or '')
    }
    for candidate_ref in candidate_refs:
        span = span_by_ref.get(candidate_ref)
        if span is not None and bool(getattr(span, 'detail_equivalent', False)):
            target_count = int(getattr(span, 'target_ref_count', 0) or len(list(getattr(span, 'target_refs', []) or [])) or 0)
            if target_count == file_count:
                return True
    sequences = _visible_subject_item_sequences_for_row(
        workspace,
        row,
        file_count=file_count,
        candidate_refs=candidate_refs,
    )
    return any(
        isinstance(sequence, dict)
        and bool(sequence.get('matches_local_file_count'))
        and not bool(sequence.get('item_refs_truncated'))
        and int(sequence.get('unowned_item_ref_count') or 0) == int(sequence.get('item_ref_count') or 0)
        for sequence in sequences
    )


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
        *list(_local_main_file_group_index(workspace)),
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
_QUERY_SEARCH_META_RE = re.compile(
    r'(?i)(?:^|[\s._-]+)(Bangumi|BGM|Subject|Subjects|Anime|TV\s+Series)(?=$|[\s._-]+)'
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
        meta_terms = [match.group(1) for match in _QUERY_SEARCH_META_RE.finditer(value)]
        if meta_terms:
            value = _QUERY_SEARCH_META_RE.sub(' ', value)
            value = re.sub(r'\s+', ' ', value).strip(' ._-')
            removed.extend(term for term in meta_terms if term)
            changed = True
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
        executable_summary = _executable_menu_observation(workspace)
        required_next_tools = ['execute_evidence'] if int(executable_summary.get('request_count') or 0) > 0 else []
        workspace = _workspace_with_judge_audit(workspace, {
            'note': 'orchestrator_materialize_queries_noop',
            'reason': str(getattr(args, 'reason', '') or ''),
            'dropped_queries': dropped,
            'required_next_tools': required_next_tools,
        })
        return workspace, {
            'status': 'rejected',
            'reason': 'no_new_query_cards',
            'workspace_changed': False,
            'dropped_queries': dropped,
            'executable_menu_summary': executable_summary,
            'required_next_tools': required_next_tools,
            'recommended_next_observation': 'no new query cards were created because all proposed query texts were duplicates or normalized away; execute existing evidence menu requests or continue with mapping intents instead of repeating the same query materialization',
        }
    workspace = workspace.with_query_cards(query_cards)
    required_next_tools = ['execute_evidence']
    workspace = _workspace_with_judge_audit(workspace, {
        'note': 'orchestrator_queries_materialized',
        'query_refs': [card.ref for card in query_cards],
        'query_texts': [card.query_text for card in query_cards],
        'dropped_queries': dropped,
        'required_next_tools': required_next_tools,
    })
    return workspace, {
        'status': 'ok',
        'workspace_changed': True,
        'query_refs': [card.ref for card in query_cards],
        'query_texts': [card.query_text for card in query_cards],
        'dropped_queries': dropped,
        'executable_menu_summary': _executable_menu_observation(workspace),
        'required_next_tools': required_next_tools,
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

    non_progress_observations = _non_progress_needs_more_evidence_observations(workspace, draft, compiled_patches)
    updated_draft, patch_issues = apply_mapping_patches(draft, compiled_patches, dossier)
    patch_issues = [*non_progress_observations, *patch_issues]
    workspace = _workspace_with_mapping_draft(
        workspace,
        updated_draft,
        patches=compiled_patches,
        note='orchestrator_mapping_intents_compiled',
    )
    if patch_issues:
        workspace = _workspace_with_mapping_patch_issue_audit(workspace, patch_issues)
    workspace, materialized_query_refs = _workspace_with_editor_query_hints(workspace, compiled_patches)
    workspace, ledger_sync_updates = _workspace_with_case_resolution_ledger_synced_from_patches(workspace, compiled_patches)
    workspace = _workspace_with_tool_accounting_audit(workspace)
    accounting_observation = _mapping_draft_observation(workspace)
    reopened_accounting_issue_codes: list[str] = []
    reopened_accounting_issue_refs: list[str] = []
    reopened_target_ownership: list[dict[str, object]] = []
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
            reopened_target_ownership = _verifier_issue_target_ownership_observation(workspace, repairable_issues)
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
                    'target_ownership': reopened_target_ownership,
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
        recommended_next = 'repeating needs_more_evidence for this row may not add information while visible candidates or same-count item sequences remain. Useful continuations include mapping the visible sequence if semantically correct, revising/repartitioning if ownership is wrong, marking bangumi_target_absent/supplemental if Bangumi lacks the corresponding target, or finishing fail_closed with a concrete semantic blocker'
    elif patch_issue_codes:
        recommended_next = 'revise semantic intents or execute missing evidence shown in patch issues'
    executable_summary = _executable_menu_observation(workspace)
    executable_types = set(str(value or '') for value in list(executable_summary.get('request_types') or []))
    compatible_requested_evidence = _compatible_executable_request_types(requested_evidence)
    has_matching_executable_evidence = bool(compatible_requested_evidence & executable_types)
    blocked_subject_refs = _dedupe_preserve_order([
        str(ref or '')
        for blocked in blocked_intents
        for ref in list(getattr(blocked, 'subject_refs', []) or [])
        if str(ref or '')
    ])
    requested_evidence_agenda = _orchestrator_requested_evidence_observation(
        workspace,
        requested_types=requested_evidence,
        subject_refs=blocked_subject_refs,
    ) if requested_evidence else {
        'requested_request_types': [],
        'agenda_subject_refs': [],
        'matching_executable_request_ids': [],
        'matching_executable_request_types': [],
        'stale_matching_request_ids': [],
        'has_matching_executable_evidence': False,
    }
    structural_shape_blocked = bool(set(blocked_issue_codes) & {
        'invalid_explicit_multi_file_mapping',
        'item_ref_count_mismatch',
        'count_mismatch',
    })
    invalid_reason_blocked = 'invalid_reason_kind' in blocked_issue_codes
    blocked_rows_by_local = {
        str(getattr(blocked, 'local_ref', '') or ''): row
        for blocked in blocked_intents
        for row in list(getattr(draft, 'rows', []) or [])
        if str(getattr(row, 'local_ref', '') or '') == str(getattr(blocked, 'local_ref', '') or '')
    }
    rows_with_same_count_surface = [
        local_ref for local_ref, row in blocked_rows_by_local.items()
        if _row_has_same_count_target_surface(workspace, row)
    ]
    if blocked_intents and structural_shape_blocked:
        if rows_with_same_count_surface and len(rows_with_same_count_surface) == len(blocked_rows_by_local):
            recommended_next = (
                'the selected semantic target shape is mechanically illegal, but same-count visible BE/BES target surface exists for the affected row. '
                'Repartitioning without changing the semantic work unit would not address the mechanical issue. Use map_regular_span with an explicit same-count visible item_refs/chosen_span_ref if semantically correct, '
                'reject/request better evidence if the visible candidates are wrong, or mark_non_bangumi_or_supplemental if Bangumi lacks the corresponding target.'
            )
        else:
            recommended_next = (
                'the selected semantic target shape is mechanically illegal for this local row. '
                'Repeating the same explicit-item intent or executing evidence only to retry it would be a no-op. '
                'Useful continuations include map_regular_span with a same-count visible BE sequence/BES span, repartition/split if the local row mixes separate files, '
                'or mark_non_bangumi_or_supplemental(reason_kind=bangumi_target_absent/bonus_video/non_episode_video/other_supplemental) if that is your semantic conclusion.'
            )
    elif blocked_intents and invalid_reason_blocked and rows_with_same_count_surface:
        recommended_next = (
            'the non-Bangumi/supplemental reason_kind is mechanically invalid, and the affected row also has same-count visible Bangumi item surface. '
            'If you judge that sequence semantically matches, use map_regular_span with the visible BE item_refs/chosen_span_ref. '
            'If you judge Bangumi lacks the target or this is supplemental, retry mark_non_bangumi_or_supplemental with one allowlisted reason_kind. '
            'If your reason was actually unresolved/fail-closed, switch decision family to mark_unaligned_fail_closed instead of converting it to accepted supplemental.'
        )
    elif blocked_intents and requested_evidence and has_matching_executable_evidence:
        recommended_next = 'execute requested evidence, then propose the same semantic mapping intent again'
    elif blocked_intents and invalid_reason_blocked:
        recommended_next = (
            'the chosen decision family has an invalid reason_kind. '
            'Use mark_non_bangumi_or_supplemental only for accepted target_absent/supplemental rows with one allowed_supplemental_reason_kinds value. '
            'If the row is unresolved or should keep the case fail_closed, use mark_unaligned_fail_closed with an allowed fail reason instead.'
        )
    elif blocked_intents and requested_evidence:
        recommended_next = (
            'the compiler requested evidence, but no matching executable evidence request is currently available. '
            'Revise the semantic intent using visible refs, materialize a clean title query, repartition if the row is too broad, '
            'or mark target_absent/supplemental if that is your investigated conclusion.'
        )
    elif blocked_intents:
        recommended_next = 'revise semantic intents using visible refs or finish only if evidence is genuinely exhausted'
    required_next_tools: list[str] = []
    if requested_evidence and has_matching_executable_evidence:
        required_next_tools = ['execute_evidence']
    accounting_payload = accounting_observation.get('draft_accounting')
    unresolved_count = int((accounting_payload or {}).get('unresolved_count') or 0) if isinstance(accounting_payload, dict) else 0
    accepted_ready = bool((accounting_payload or {}).get('accepted_accounting_ready')) if isinstance(accounting_payload, dict) else False
    finish_review_flags: list[str] = []
    if accepted_ready and isinstance(accounting_payload, dict):
        main_file_count = int(accounting_payload.get('main_file_count') or 0)
        mapped_file_count = int(accounting_payload.get('mapped_file_count') or 0)
        excluded_file_count = int(accounting_payload.get('excluded_file_count') or 0)
        if main_file_count and mapped_file_count == 0:
            finish_review_flags.append('accepted_projection_maps_no_main_files')
        if main_file_count and excluded_file_count / max(1, main_file_count) >= 0.5:
            finish_review_flags.append('accepted_projection_excludes_majority_of_main_files')
    open_rows_payload = accounting_observation.get('open_rows') if isinstance(accounting_observation.get('open_rows'), list) else []
    terminal_fail_rows = [
        row for row in open_rows_payload
        if isinstance(row, dict) and str(row.get('disposition') or '') == 'unaligned_fail_closed'
    ]
    if accepted_ready and not blocked_intents and not patch_issue_codes and not reopened_accounting_issue_codes:
        if finish_review_flags:
            recommended_next = (
                'accepted accounting is mechanically ready, but finish review flags are present: '
                f'{", ".join(finish_review_flags)}. These are factual count flags, not semantic blockers. '
                'Call finish_case only if this all/mostly-unmapped projection is truly your whole-case semantic conclusion; '
                'otherwise revise mapping intents, split into child cases, or gather concrete target evidence before finishing.'
            )
        else:
            recommended_next = 'accepted accounting is mechanically ready; call finish_case if this projection matches your semantic whole-case review'
    elif unresolved_count > 0 and not blocked_intents and not patch_issue_codes:
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
            'An unchanged finish_case call would still be rejected. Inspect reopened_target_ownership and candidate_target_conflicts: duplicate targets already owned by another row remain mechanical conflicts. '
            'If your previous owner row was semantically wrong, revise that owner row; if the reopened row has no non-overlapping Bangumi target, '
            'mark it target_absent/supplemental; otherwise propose revised non-duplicate mapping intents for open_rows.'
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
        'ledger_synced_row_count': len(ledger_sync_updates),
        'ledger_synced_rows': ledger_sync_updates[:24],
        'reopened_accounting_issue_codes': reopened_accounting_issue_codes,
        'reopened_accounting_issue_refs': reopened_accounting_issue_refs,
        'reopened_target_ownership': reopened_target_ownership,
        'reopened_accounting_issue_row_count': reopened_accounting_issue_row_count,
        'draft_accounting': accounting_observation.get('draft_accounting'),
        'open_rows': open_rows_payload,
        'terminal_fail_closed_row_count': len(terminal_fail_rows),
        'finish_review_flags': finish_review_flags,
        'finish_gate': _finish_gate_observation(workspace),
        'executable_menu_summary': executable_summary,
        'requested_evidence_agenda': requested_evidence_agenda,
        'matching_requested_evidence_available': has_matching_executable_evidence,
        'required_next_tools': required_next_tools,
        'recommended_next_observation': recommended_next,
    })
    structural_repartition_codes = {
        'item_ref_count_mismatch',
        'count_mismatch',
    }
    if blocked_issue_codes and structural_repartition_codes.intersection(blocked_issue_codes):
        rows_by_local = {
            str(getattr(row, 'local_ref', '') or ''): row
            for row in list(getattr(draft, 'rows', []) or [])
            if str(getattr(row, 'local_ref', '') or '')
        }
        repartition_blocked_intents = [
            blocked for blocked in blocked_intents
            if str(getattr(blocked, 'local_ref', '') or '')
            and not _row_has_same_count_target_surface(
                workspace,
                rows_by_local.get(str(getattr(blocked, 'local_ref', '') or '')) or MappingDraftRow(),
            )
        ]
    else:
        repartition_blocked_intents = []
    if repartition_blocked_intents:
        workspace = _workspace_with_judge_audit(workspace, {
            'note': 'case_understanding_repartition_requested',
            'reason': 'mapping intent compiler found a mechanical row-shape/count mismatch; OrchestratorAgent should reconsider work-unit boundaries or provide a same-count target sequence',
            'issue_codes': _dedupe_preserve_order([
                code for code in blocked_issue_codes if code in structural_repartition_codes
            ]),
            'local_refs': _dedupe_preserve_order([
                str(getattr(blocked, 'local_ref', '') or '')
                for blocked in repartition_blocked_intents
                if str(getattr(blocked, 'local_ref', '') or '')
            ]),
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
        'ledger_synced_row_count': len(ledger_sync_updates),
        'ledger_synced_rows': ledger_sync_updates[:24],
        'reopened_accounting_issue_codes': reopened_accounting_issue_codes,
        'reopened_accounting_issue_refs': reopened_accounting_issue_refs,
        'reopened_target_ownership': reopened_target_ownership,
        'reopened_accounting_issue_row_count': reopened_accounting_issue_row_count,
        'patch_issue_refs': _dedupe_preserve_order([str(getattr(issue, 'ref', '') or '') for issue in patch_issues]),
        'materialized_query_refs': materialized_query_refs,
        'draft_accounting': accounting_observation.get('draft_accounting'),
        'open_rows': open_rows_payload,
        'terminal_fail_closed_rows': terminal_fail_rows,
        'finish_review_flags': finish_review_flags,
        'accounting_verifier_passed': accounting_observation.get('accounting_verifier_passed'),
        'accounting_issue_codes': accounting_observation.get('accounting_issue_codes'),
        'executable_menu_summary': executable_summary,
        'requested_evidence_agenda': requested_evidence_agenda,
        'finish_gate': _finish_gate_observation(workspace),
        'matching_requested_evidence_available': has_matching_executable_evidence,
        'required_next_tools': required_next_tools,
        'recommended_next_observation': recommended_next,
    }




def _run_orchestrator_propose_case_resolution_ledger_tool(
    workspace: CaseEvidenceWorkspace,
    args: ProposeCaseResolutionLedgerToolArgs,
) -> tuple[CaseEvidenceWorkspace, dict[str, object]]:
    workspace = _refresh_mapping_draft_candidates(_workspace_with_initial_mapping_draft(workspace))
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None or not getattr(draft, 'rows', None):
        workspace = _workspace_with_judge_audit(workspace, {'note': 'case_resolution_ledger_skipped', 'reason': 'no_draft'})
        return workspace, {'status': 'rejected', 'reason': 'no_draft', 'executable_menu_summary': _executable_menu_observation(workspace)}
    ledger_rows = list(getattr(args, 'ledger_rows', []) or [])
    ledger = CaseResolutionLedger(
        ledger_ref=str(getattr(getattr(workspace, 'case_resolution_ledger', None), 'ledger_ref', '') or 'CRL1'),
        rows=ledger_rows,
        summary=str(getattr(args, 'summary', '') or getattr(args, 'reason', '') or ''),
        version=int(getattr(getattr(workspace, 'case_resolution_ledger', None), 'version', 0) or 0) + 1,
    )
    workspace = _workspace_with_judge_audit(workspace, {
        'note': 'orchestrator_case_resolution_ledger_called',
        'ledger_row_count': len(ledger_rows),
        'reason': str(getattr(args, 'reason', '') or ''),
    })
    if not ledger_rows:
        return workspace, {
            'status': 'rejected',
            'reason': 'empty_case_resolution_ledger',
            **_mapping_draft_observation(workspace),
            'executable_menu_summary': _executable_menu_observation(workspace),
            'recommended_next_observation': 'propose_case_resolution_ledger must include at least one row covering current main LF refs',
        }

    dossier = workspace.to_dossier(round_context='case_resolution_ledger_compile')
    compiler_result = CaseResolutionLedgerCompiler().compile(dossier, draft, ledger)
    blocked_rows = list(getattr(compiler_result, 'blocked_rows', []) or [])
    compiled_patches = list(getattr(compiler_result, 'compiled_patches', []) or [])
    generated_span_cards = list(getattr(compiler_result, 'generated_span_cards', []) or [])
    requested_evidence = _dedupe_preserve_order([
        str(value or '')
        for value in list(getattr(compiler_result, 'requested_evidence', []) or [])
        if str(value or '')
    ])
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
        dossier = workspace.to_dossier(round_context='case_resolution_ledger_generated_spans')
        workspace = _workspace_with_judge_audit(workspace, {
            'note': 'orchestrator_case_resolution_ledger_generated_spans',
            'span_refs': [str(getattr(card, 'ref', '') or '') for card in generated_span_cards],
            'target_ref_counts': [int(getattr(card, 'target_ref_count', 0) or 0) for card in generated_span_cards],
        })
    patch_issues: list[VerifierIssue] = []
    if compiled_patches:
        compiled_patches, dropped_non_open_patches = _filter_mapping_patches_for_agent_revision(draft, compiled_patches)
        if dropped_non_open_patches:
            workspace = _workspace_with_judge_audit(workspace, {
                'note': 'orchestrator_case_resolution_ledger_non_open_patches_ignored',
                'patch_count': len(dropped_non_open_patches),
            })
        updated_draft, patch_issues = apply_mapping_patches(draft, compiled_patches, dossier)
        workspace = _workspace_with_mapping_draft(
            workspace,
            updated_draft,
            patches=compiled_patches,
            note='orchestrator_case_resolution_ledger_compiled',
        )
        if patch_issues:
            workspace = _workspace_with_mapping_patch_issue_audit(workspace, patch_issues)
        workspace, _materialized_query_refs = _workspace_with_editor_query_hints(workspace, compiled_patches)
    workspace = _workspace_preserving_state(workspace, case_resolution_ledger=ledger)
    workspace = _workspace_with_tool_accounting_audit(workspace, note='orchestrator_case_resolution_ledger_accounting')

    accounting_observation = _mapping_draft_observation(workspace)
    executable_summary = _executable_menu_observation(workspace)
    executable_types = set(str(value or '') for value in list(executable_summary.get('request_types') or []))
    has_matching_executable_evidence = bool(_compatible_executable_request_types(requested_evidence) & executable_types)
    blocked_subject_refs = _dedupe_preserve_order([
        str(ref or '')
        for blocked in blocked_rows
        for ref in list(getattr(blocked, 'subject_refs', []) or [])
        if str(ref or '')
    ])
    requested_evidence_agenda = _orchestrator_requested_evidence_observation(
        workspace,
        requested_types=requested_evidence,
        subject_refs=blocked_subject_refs,
    ) if requested_evidence else {
        'requested_request_types': [],
        'agenda_subject_refs': [],
        'matching_executable_request_ids': [],
        'matching_executable_request_types': [],
        'stale_matching_request_ids': [],
        'has_matching_executable_evidence': False,
    }
    required_next_tools = ['execute_evidence'] if requested_evidence and has_matching_executable_evidence else []
    blocked_issue_codes = _dedupe_preserve_order([
        str(code or '')
        for blocked in blocked_rows
        for code in list(getattr(blocked, 'issue_codes', []) or [])
        if str(code or '')
    ])
    patch_issue_codes = _dedupe_preserve_order([
        str(getattr(issue, 'issue_code', '') or '')
        for issue in patch_issues
        if str(getattr(issue, 'issue_code', '') or '')
    ])
    outcome_counts: dict[str, int] = {}
    for row in ledger_rows:
        outcome = str(getattr(row, 'outcome', '') or 'unknown')
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
    status = 'ok'
    if blocked_rows and not compiled_patches:
        status = 'blocked_ledger_rows'
    elif blocked_rows:
        status = 'partial'
    elif patch_issues:
        status = 'patch_issues'
    recommended_next = str(getattr(compiler_result, 'recommended_next_observation', '') or '')
    if patch_issue_codes:
        recommended_next = 'ledger compiled to draft patches but verifier returned mechanical issues; revise ledger rows or mapping intents using visible refs'
    elif requested_evidence:
        recommended_next = recommended_next or 'execute requested evidence from the ledger, then update the ledger or mapping intents'
    workspace = _workspace_with_judge_audit(workspace, {
        'note': 'orchestrator_case_resolution_ledger_result',
        'status': status,
        'ledger_row_count': len(ledger_rows),
        'ledger_outcome_counts': outcome_counts,
        'compiled_patch_count': len(compiled_patches),
        'blocked_ledger_row_count': len(blocked_rows),
        'blocked_ledger_issue_codes': blocked_issue_codes,
        'blocked_ledger_rows': [
            item.model_dump(mode='json') if hasattr(item, 'model_dump') else item
            for item in blocked_rows[:8]
        ],
        'requested_evidence': requested_evidence,
        'patch_issue_codes': patch_issue_codes,
        'draft_accounting': accounting_observation.get('draft_accounting'),
        'finish_gate': _finish_gate_observation(workspace),
        'requested_evidence_agenda': requested_evidence_agenda,
        'matching_requested_evidence_available': has_matching_executable_evidence,
        'required_next_tools': required_next_tools,
        'recommended_next_observation': recommended_next,
    })
    return workspace, {
        'status': status,
        'workspace_changed': bool(compiled_patches or generated_span_cards or ledger_rows),
        'target_surface_changed': bool(generated_span_cards),
        'case_resolution_ledger_row_count': len(ledger_rows),
        'ledger_outcome_counts': outcome_counts,
        'ledger_compiled_patch_count': len(compiled_patches),
        'compiled_patch_count': len(compiled_patches),
        'ledger_blocked_row_count': len(blocked_rows),
        'blocked_ledger_rows': [
            item.model_dump(mode='json') if hasattr(item, 'model_dump') else item
            for item in blocked_rows
        ],
        'blocked_ledger_issue_codes': blocked_issue_codes,
        'requested_evidence': requested_evidence,
        'ledger_requested_evidence_count': len(requested_evidence),
        'patch_issue_codes': patch_issue_codes,
        'generated_span_count': len(generated_span_cards),
        'generated_span_refs': [str(getattr(card, 'ref', '') or '') for card in generated_span_cards],
        'draft_accounting': accounting_observation.get('draft_accounting'),
        'open_rows': accounting_observation.get('open_rows'),
        'accounting_verifier_passed': accounting_observation.get('accounting_verifier_passed'),
        'accounting_issue_codes': accounting_observation.get('accounting_issue_codes'),
        'finish_gate': _finish_gate_observation(workspace),
        'executable_menu_summary': executable_summary,
        'requested_evidence_agenda': requested_evidence_agenda,
        'matching_requested_evidence_available': has_matching_executable_evidence,
        'required_next_tools': required_next_tools,
        'recommended_next_observation': recommended_next,
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
        'repartition_requested': True,
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
        'open_rows': _open_rows_observation(workspace),
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
        'recommended_next_observation': 'repartition is now explicitly requested. Call propose_case_understanding once with revised work units for the unresolved open rows; preserve already-settled rows unless the issue is target ownership.',
    }


def _workspace_with_child_split_span(
    workspace: CaseEvidenceWorkspace,
    spec: SplitCaseSpec,
) -> CaseEvidenceWorkspace:
    child_main_refs = _dedupe_preserve_order([str(ref or '') for ref in list(getattr(spec, 'main_file_refs', []) or [])])
    if not child_main_refs:
        return workspace
    title_hints = _dedupe_preserve_order([str(value or '') for value in list(getattr(spec, 'title_hints', []) or [])])
    split_span = LocalSpanCard(
        ref='LS1',
        span_scope='token_segment',
        file_refs=child_main_refs,
        file_ref_count=len(child_main_refs),
        file_ref_samples=child_main_refs[:12],
        file_ref_range=child_main_refs,
        ordering_basis='path_order',
        episode_token_count=len(child_main_refs),
        title_cues=title_hints,
        confidence_facts=[
            'mechanical child split span from OrchestratorAgent split_into_child_cases file refs',
        ],
    )
    local_span_cards = [split_span]
    updated = _workspace_preserving_state(
        workspace,
        local_span_cards=local_span_cards,
        mapping_draft=None,
        mapping_draft_patches=[],
        mapping_draft_candidate_comparisons=[],
    )
    updated = _workspace_with_initial_mapping_draft(updated)
    return _workspace_with_judge_audit(updated, {
        'note': 'orchestrator_child_ref_scope_initialized',
        'child_case_ref': str(getattr(spec, 'child_case_ref', '') or ''),
        'local_span_refs': ['LS1'],
        'visible_local_file_refs': child_main_refs[:32],
        'parent_refs_not_visible': True,
        'reason': 'child case uses canonical child-local refs; parent LS refs are intentionally not visible',
    })


def _split_case_validation_issues(
    workspace: CaseEvidenceWorkspace,
    split_cases: list[SplitCaseSpec],
    *,
    require_complete_coverage: bool = True,
) -> list[VerifierIssue]:
    issues: list[VerifierIssue] = []
    if not split_cases:
        return [
            VerifierIssue(
                ref='split_cases',
                issue_code='split_empty',
                severity='blocked',
                message='split_into_child_cases requires at least one child case',
            )
        ]
    visible_refs = _workspace_visible_ref_set(workspace)
    main_refs = set(getattr(workspace.contract, 'main_file_refs', []) or [])
    supplemental_refs = set(getattr(workspace.contract, 'supplemental_file_refs', []) or [])
    allowed_file_refs = set(getattr(workspace.contract, 'allowed_file_refs', []) or []) | main_refs | supplemental_refs
    allowed_group_refs = set(_local_main_file_group_index(workspace))
    child_case_refs = [str(getattr(spec, 'child_case_ref', '') or '') for spec in split_cases]
    if any(not ref for ref in child_case_refs) or len(set(child_case_refs)) != len(child_case_refs):
        issues.append(VerifierIssue(
            ref='split_cases',
            issue_code='split_child_ref_invalid',
            severity='blocked',
            message='child_case_ref must be non-empty and unique',
        ))
    main_counts: dict[str, int] = {}
    supplemental_counts: dict[str, int] = {}
    for spec in split_cases:
        child_ref = str(getattr(spec, 'child_case_ref', '') or 'split_case')
        child_main_refs = [str(ref or '') for ref in list(getattr(spec, 'main_file_refs', []) or []) if str(ref or '')]
        child_supplemental_refs = [str(ref or '') for ref in list(getattr(spec, 'supplemental_file_refs', []) or []) if str(ref or '')]
        child_main_group_refs = [str(ref or '') for ref in list(getattr(spec, 'main_group_refs', []) or []) if str(ref or '')]
        child_supplemental_group_refs = [str(ref or '') for ref in list(getattr(spec, 'supplemental_group_refs', []) or []) if str(ref or '')]
        if not child_main_refs:
            issues.append(VerifierIssue(
                ref=child_ref,
                issue_code='split_child_empty',
                severity='blocked',
                message='child case must contain at least one main file ref',
            ))
        hidden_main_refs = [ref for ref in child_main_refs if ref not in main_refs]
        if hidden_main_refs:
            issues.append(VerifierIssue(
                ref=child_ref,
                issue_code='split_hidden_main_ref',
                severity='blocked',
                message='child main_file_refs must come from root contract.main_file_refs',
                related_refs=hidden_main_refs[:12],
            ))
        hidden_supplemental_refs = [ref for ref in child_supplemental_refs if ref not in allowed_file_refs]
        if hidden_supplemental_refs:
            issues.append(VerifierIssue(
                ref=child_ref,
                issue_code='split_hidden_supplemental_ref',
                severity='blocked',
                message='child supplemental_file_refs must be visible allowed file refs',
                related_refs=hidden_supplemental_refs[:12],
            ))
        hidden_main_group_refs = [ref for ref in child_main_group_refs if ref not in allowed_group_refs]
        if hidden_main_group_refs:
            issues.append(VerifierIssue(
                ref=child_ref,
                issue_code='split_unknown_main_group_ref',
                severity='blocked',
                message='child main_group_refs must use visible LG refs from local_main_file_groups',
                related_refs=hidden_main_group_refs[:12],
            ))
        hidden_supplemental_group_refs = [ref for ref in child_supplemental_group_refs if ref not in allowed_group_refs]
        if hidden_supplemental_group_refs:
            issues.append(VerifierIssue(
                ref=child_ref,
                issue_code='split_unknown_supplemental_group_ref',
                severity='blocked',
                message='child supplemental_group_refs must use visible LG refs from local_main_file_groups',
                related_refs=hidden_supplemental_group_refs[:12],
            ))
        main_as_supplemental = [ref for ref in child_supplemental_refs if ref in main_refs]
        if main_as_supplemental:
            issues.append(VerifierIssue(
                ref=child_ref,
                issue_code='split_main_as_supplemental',
                severity='blocked',
                message='root main refs must be assigned as child main refs, not supplemental refs',
                related_refs=main_as_supplemental[:12],
            ))
        unknown_support_refs = [
            str(ref or '')
            for ref in list(getattr(spec, 'support_refs', []) or [])
            if str(ref or '') and str(ref or '') not in visible_refs and str(ref or '') not in allowed_group_refs
        ]
        if unknown_support_refs:
            issues.append(VerifierIssue(
                ref=child_ref,
                issue_code='split_unknown_support_ref',
                severity='blocked',
                message='split support_refs must be visible refs',
                related_refs=unknown_support_refs[:12],
            ))
        for ref in child_main_refs:
            main_counts[ref] = main_counts.get(ref, 0) + 1
        for ref in child_supplemental_refs:
            supplemental_counts[ref] = supplemental_counts.get(ref, 0) + 1
    assigned_main_refs = set(main_counts)
    missing_main_refs = sorted(main_refs - assigned_main_refs)
    extra_main_refs = sorted(assigned_main_refs - main_refs)
    duplicate_main_refs = sorted(ref for ref, count in main_counts.items() if count > 1)
    duplicate_supplemental_refs = sorted(ref for ref, count in supplemental_counts.items() if count > 1)
    main_supplemental_overlap = sorted(set(main_counts) & set(supplemental_counts))
    if require_complete_coverage and missing_main_refs:
        issues.append(VerifierIssue(
            ref='split_cases',
            issue_code='split_missing_main_ref',
            severity='blocked',
            message='split_into_child_cases must cover every root main file exactly once',
            related_refs=missing_main_refs[:24],
        ))
    if extra_main_refs:
        issues.append(VerifierIssue(
            ref='split_cases',
            issue_code='split_hidden_main_ref',
            severity='blocked',
            message='split_into_child_cases includes main refs outside the root contract',
            related_refs=extra_main_refs[:24],
        ))
    if duplicate_main_refs:
        issues.append(VerifierIssue(
            ref='split_cases',
            issue_code='split_duplicate_main_ref',
            severity='blocked',
            message='root main refs may appear in only one child main_file_refs list',
            related_refs=duplicate_main_refs[:24],
        ))
    if duplicate_supplemental_refs:
        issues.append(VerifierIssue(
            ref='split_cases',
            issue_code='split_duplicate_supplemental_ref',
            severity='blocked',
            message='supplemental refs may appear in only one child supplemental_file_refs list',
            related_refs=duplicate_supplemental_refs[:24],
        ))
    if main_supplemental_overlap:
        issues.append(VerifierIssue(
            ref='split_cases',
            issue_code='split_duplicate_file_ref',
            severity='blocked',
            message='a file ref cannot be both child main and child supplemental',
            related_refs=main_supplemental_overlap[:24],
        ))
    return issues


def _split_case_validation_diagnostics(
    workspace: CaseEvidenceWorkspace,
    split_cases: list[SplitCaseSpec],
) -> dict[str, object]:
    main_refs = set(getattr(workspace.contract, 'main_file_refs', []) or [])
    supplemental_refs = set(getattr(workspace.contract, 'supplemental_file_refs', []) or [])
    allowed_file_refs = set(getattr(workspace.contract, 'allowed_file_refs', []) or []) | main_refs | supplemental_refs
    group_index = _local_main_file_group_index(workspace)
    allowed_group_refs = set(group_index)
    main_counts: dict[str, int] = {}
    supplemental_counts: dict[str, int] = {}
    child_summaries: list[dict[str, object]] = []
    for spec in split_cases:
        child_ref = str(getattr(spec, 'child_case_ref', '') or 'split_case')
        child_main_refs = [
            str(ref or '')
            for ref in list(getattr(spec, 'main_file_refs', []) or [])
            if str(ref or '')
        ]
        child_supplemental_refs = [
            str(ref or '')
            for ref in list(getattr(spec, 'supplemental_file_refs', []) or [])
            if str(ref or '')
        ]
        child_main_group_refs = [
            str(ref or '')
            for ref in list(getattr(spec, 'main_group_refs', []) or [])
            if str(ref or '')
        ]
        child_supplemental_group_refs = [
            str(ref or '')
            for ref in list(getattr(spec, 'supplemental_group_refs', []) or [])
            if str(ref or '')
        ]
        for ref in child_main_refs:
            main_counts[ref] = main_counts.get(ref, 0) + 1
        for ref in child_supplemental_refs:
            supplemental_counts[ref] = supplemental_counts.get(ref, 0) + 1
        child_summaries.append({
            'child_case_ref': child_ref,
            'main_ref_count': len(child_main_refs),
            'main_ref_samples': _sample_refs(child_main_refs, limit=8),
            'supplemental_ref_count': len(child_supplemental_refs),
            'supplemental_ref_samples': _sample_refs(child_supplemental_refs, limit=6),
            'hidden_main_ref_samples': _sample_refs([ref for ref in child_main_refs if ref not in main_refs], limit=8),
            'hidden_supplemental_ref_samples': _sample_refs([ref for ref in child_supplemental_refs if ref not in allowed_file_refs], limit=6),
            'main_group_refs': _sample_refs(child_main_group_refs, limit=8),
            'supplemental_group_refs': _sample_refs(child_supplemental_group_refs, limit=6),
            'unknown_main_group_ref_samples': _sample_refs([ref for ref in child_main_group_refs if ref not in allowed_group_refs], limit=8),
            'unknown_supplemental_group_ref_samples': _sample_refs([ref for ref in child_supplemental_group_refs if ref not in allowed_group_refs], limit=6),
        })
    assigned_main_refs = set(main_counts)
    return {
        'root_main_ref_count': len(main_refs),
        'assigned_main_ref_count': len(assigned_main_refs),
        'missing_main_refs': _sample_refs(sorted(main_refs - assigned_main_refs), limit=24),
        'extra_main_refs': _sample_refs(sorted(assigned_main_refs - main_refs), limit=24),
        'duplicate_main_refs': _sample_refs(sorted(ref for ref, count in main_counts.items() if count > 1), limit=24),
        'duplicate_supplemental_refs': _sample_refs(sorted(ref for ref, count in supplemental_counts.items() if count > 1), limit=12),
        'main_supplemental_overlap_refs': _sample_refs(sorted(set(main_counts) & set(supplemental_counts)), limit=12),
        'child_ref_counts': child_summaries[:24],
        'available_local_group_refs': [
            {
                'group_ref': group_ref,
                'group_key': str(entry.get('group_key') or ''),
                'file_ref_count': len(list(entry.get('file_refs') or [])),
                'file_ref_range': _sample_refs(list(entry.get('file_refs') or []), limit=2),
            }
            for group_ref, entry in list(group_index.items())[:24]
        ],
    }


def _canonicalize_split_main_refs(
    workspace: CaseEvidenceWorkspace,
    split_cases: list[SplitCaseSpec],
) -> tuple[list[SplitCaseSpec], dict[str, object]]:
    main_refs = set(getattr(workspace.contract, 'main_file_refs', []) or [])
    canonicalized: list[SplitCaseSpec] = []
    moved_by_child: dict[str, list[str]] = {}
    for spec in split_cases:
        child_main_refs = _dedupe_preserve_order([
            str(ref or '')
            for ref in list(getattr(spec, 'main_file_refs', []) or [])
            if str(ref or '')
        ])
        child_supplemental_refs: list[str] = []
        moved: list[str] = []
        for ref in list(getattr(spec, 'supplemental_file_refs', []) or []):
            value = str(ref or '')
            if not value:
                continue
            if value in main_refs:
                if value not in child_main_refs:
                    child_main_refs.append(value)
                    moved.append(value)
                continue
            child_supplemental_refs.append(value)
        if moved:
            moved_by_child[str(getattr(spec, 'child_case_ref', '') or 'split_case')] = moved
        canonicalized.append(spec.model_copy(update={
            'main_file_refs': child_main_refs,
            'supplemental_file_refs': _dedupe_preserve_order(child_supplemental_refs),
        }))
    return canonicalized, {
        'split_main_refs_canonicalized': bool(moved_by_child),
        'moved_main_refs_from_supplemental_by_child': moved_by_child,
    }


def _child_result_fail_closed_reason(child_result: CaseAgentRunResult, *, index: int) -> FailClosedReason:
    child_refs = list(getattr(getattr(child_result.final_workspace, 'contract', None), 'main_file_refs', []) or [])[:12]
    child_summary = str(getattr(child_result, 'summary', '') or getattr(getattr(child_result, 'final_output', None), 'summary', '') or '')
    child_reasons = list(getattr(getattr(child_result, 'final_output', None), 'fail_closed_reasons', []) or [])
    if child_reasons:
        reason = child_reasons[0]
        return FailClosedReason(
            ref=f'FR_CHILD_{index}',
            reason_kind=str(getattr(reason, 'reason_kind', '') or 'insufficient_evidence'),
            description=f'child {child_result.case_id} fail_closed: {str(getattr(reason, "description", "") or child_summary)}',
            related_refs=_dedupe_preserve_order([*child_refs, *[str(ref or '') for ref in list(getattr(reason, 'related_refs', []) or [])]])[:24],
        )
    return FailClosedReason(
        ref=f'FR_CHILD_{index}',
        reason_kind='insufficient_evidence',
        description=f'child {child_result.case_id} fail_closed: {child_summary or "unresolved child case"}',
        related_refs=child_refs,
    )


def _workspace_with_child_target_surface(
    workspace: CaseEvidenceWorkspace,
    child_results: list[CaseAgentRunResult],
) -> CaseEvidenceWorkspace:
    existing_subject_refs = {str(getattr(card, 'ref', '') or '') for card in list(getattr(workspace, 'bangumi_subjects', []) or [])}
    existing_relation_refs = {str(getattr(card, 'ref', '') or '') for card in list(getattr(workspace, 'bangumi_relations', []) or [])}
    existing_group_refs = {str(getattr(card, 'ref', '') or '') for card in list(getattr(workspace, 'bangumi_groups', []) or [])}
    existing_item_refs = {str(getattr(card, 'ref', '') or '') for card in list(getattr(workspace, 'bangumi_items', []) or [])}
    existing_query_refs = {str(getattr(card, 'ref', '') or '') for card in list(getattr(workspace, 'query_cards', []) or [])}
    existing_provenance_refs = {str(getattr(card, 'ref', '') or '') for card in list(getattr(workspace, 'provenance_cards', []) or [])}
    existing_span_refs = {str(getattr(card, 'ref', '') or '') for card in list(getattr(workspace, 'bangumi_span_cards', []) or [])}
    subjects = list(getattr(workspace, 'bangumi_subjects', []) or [])
    relations = list(getattr(workspace, 'bangumi_relations', []) or [])
    groups = list(getattr(workspace, 'bangumi_groups', []) or [])
    items = list(getattr(workspace, 'bangumi_items', []) or [])
    queries = list(getattr(workspace, 'query_cards', []) or [])
    provenance = list(getattr(workspace, 'provenance_cards', []) or [])
    spans = list(getattr(workspace, 'bangumi_span_cards', []) or [])
    seen_refs = list(getattr(workspace, 'seen_detail_refs', []) or [])
    previous_results = list(getattr(workspace, 'previous_evidence_results', []) or [])
    scoped_target_refs: list[str] = []
    for child_index, child in enumerate(child_results, start=1):
        child_workspace = getattr(child, 'final_workspace', None)
        if child_workspace is None:
            continue
        for card in list(getattr(child_workspace, 'bangumi_subjects', []) or []):
            ref = str(getattr(card, 'ref', '') or '')
            if ref and ref not in existing_subject_refs:
                existing_subject_refs.add(ref)
                subjects.append(card)
        for card in list(getattr(child_workspace, 'bangumi_relations', []) or []):
            ref = str(getattr(card, 'ref', '') or '')
            if ref and ref not in existing_relation_refs:
                existing_relation_refs.add(ref)
                relations.append(card)
        for card in list(getattr(child_workspace, 'bangumi_groups', []) or []):
            ref = str(getattr(card, 'ref', '') or '')
            if ref and ref not in existing_group_refs:
                existing_group_refs.add(ref)
                groups.append(card)
        for card in list(getattr(child_workspace, 'bangumi_items', []) or []):
            ref = str(getattr(card, 'ref', '') or '')
            scoped_ref = _scoped_child_target_ref(child_index, ref)
            if scoped_ref and scoped_ref not in existing_item_refs:
                existing_item_refs.add(scoped_ref)
                scoped_target_refs.append(scoped_ref)
                items.append(card.model_copy(update={'ref': scoped_ref}))
        for card in list(getattr(child_workspace, 'query_cards', []) or []):
            ref = str(getattr(card, 'ref', '') or '')
            if ref and ref not in existing_query_refs:
                existing_query_refs.add(ref)
                queries.append(card)
        for card in list(getattr(child_workspace, 'provenance_cards', []) or []):
            ref = str(getattr(card, 'ref', '') or '')
            if ref and ref not in existing_provenance_refs:
                existing_provenance_refs.add(ref)
                provenance.append(card)
        for card in list(getattr(child_workspace, 'bangumi_span_cards', []) or []):
            ref = str(getattr(card, 'ref', '') or '')
            scoped_ref = _scoped_child_target_ref(child_index, ref)
            if scoped_ref and scoped_ref not in existing_span_refs:
                existing_span_refs.add(scoped_ref)
                scoped_target_refs.append(scoped_ref)
                scoped_targets = [
                    _scoped_child_target_ref(child_index, target_ref)
                    for target_ref in list(getattr(card, 'target_refs', []) or [])
                ]
                spans.append(card.model_copy(update={
                    'ref': scoped_ref,
                    'target_refs': scoped_targets,
                    'target_ref_range': [
                        _scoped_child_target_ref(child_index, target_ref)
                        for target_ref in list(getattr(card, 'target_ref_range', []) or [])
                    ],
                    'target_ref_samples': [
                        _scoped_child_target_ref(child_index, target_ref)
                        for target_ref in list(getattr(card, 'target_ref_samples', []) or [])
                    ],
                }))
        seen_refs.extend(str(ref or '') for ref in list(getattr(child_workspace, 'seen_detail_refs', []) or []) if str(ref or ''))
        previous_results.extend(list(getattr(child_workspace, 'previous_evidence_results', []) or []))
    visible_target_refs = _dedupe_preserve_order([
        *list(getattr(workspace.contract, 'visible_target_refs', []) or []),
        *[str(getattr(item, 'ref', '') or '') for item in items],
    ])
    updated_contract = workspace.contract.model_copy(update={'visible_target_refs': visible_target_refs})
    updated = _workspace_preserving_state(
        workspace,
        contract=updated_contract,
        bangumi_subjects=subjects,
        bangumi_relations=relations,
        bangumi_groups=groups,
        bangumi_items=items,
        bangumi_span_cards=spans,
        query_cards=queries,
        provenance_cards=provenance,
        previous_evidence_results=previous_results,
        seen_detail_refs=_dedupe_preserve_order([*seen_refs, *scoped_target_refs]),
    )
    return updated


def _scoped_child_target_ref(child_index: int, ref: str) -> str:
    value = str(ref or '')
    if value.startswith('BES'):
        return f'BESCH{child_index}_{value[3:] or "0"}'
    if value.startswith('BE'):
        return f'BECH{child_index}_{value[2:] or "0"}'
    return value


def _remap_child_assignment_refs(child_index: int, assignment: AssignmentIntent) -> AssignmentIntent:
    target_ref = str(getattr(assignment, 'target_ref', '') or '')
    if target_ref == 'UNALIGNED':
        return assignment
    remapped_target = _scoped_child_target_ref(child_index, target_ref)
    support_card_refs = [
        _scoped_child_target_ref(child_index, str(ref or ''))
        for ref in list(getattr(assignment, 'support_card_refs', []) or [])
    ]
    return assignment.model_copy(update={
        'target_ref': remapped_target,
        'support_card_refs': _dedupe_preserve_order(support_card_refs),
    })


def _child_assignment_target_source_map(
    child_results: list[CaseAgentRunResult],
) -> dict[tuple[str, str], tuple[str, str]]:
    result: dict[tuple[str, str], tuple[str, str]] = {}
    for child_index, child in enumerate(child_results, start=1):
        child_output = getattr(child, 'final_output', None)
        if child_output is None:
            continue
        for assignment in list(getattr(child_output, 'assignment_intents', []) or []):
            file_ref = str(getattr(assignment, 'file_ref', '') or '')
            target_ref = str(getattr(assignment, 'target_ref', '') or '')
            if not file_ref or not target_ref:
                continue
            child_workspace = getattr(child, 'final_workspace', None)
            if child_workspace is None:
                continue
            item = next(
                (
                    card for card in list(getattr(child_workspace, 'bangumi_items', []) or [])
                    if str(getattr(card, 'ref', '') or '') == target_ref
                ),
                None,
            )
            if item is not None:
                subject_ref = str(getattr(item, 'subject_ref', '') or '')
                episode_id = str(getattr(item, 'episode_id', '') or '')
                subject_card = next(
                    (
                        card for card in list(getattr(child_workspace, 'bangumi_subjects', []) or [])
                        if str(getattr(card, 'ref', '') or '') == subject_ref
                    ),
                    None,
                )
                subject_id = int(getattr(subject_card, 'subject_id', 0) or 0) if subject_card is not None else 0
                sort_value = int(getattr(item, "sort", 0) or 0)
                ep_value = int(getattr(item, "ep", 0) or 0)
                item_kind = str(getattr(item, "item_kind", "") or "")
                title_value = str(getattr(item, "title", "") or getattr(item, "name_cn", "") or getattr(item, "name", "") or "")
                if episode_id:
                    source_key = f'episode_id:{episode_id}'
                elif subject_id > 0 and (sort_value or ep_value or item_kind or title_value):
                    source_key = f'subject_id:{subject_id}:{sort_value}:{ep_value}:{item_kind}:{title_value}'
                else:
                    source_key = f'{child_index}:{target_ref}'
            else:
                source_key = f'{child_index}:{target_ref}'
            child_case_id = str(getattr(child, 'case_id', '') or '')
            result[(file_ref, target_ref)] = (child_case_id, source_key)
            result[(file_ref, _scoped_child_target_ref(child_index, target_ref))] = (child_case_id, source_key)
    return result


def _aggregate_child_case_results(
    workspace: CaseEvidenceWorkspace,
    split_cases: list[SplitCaseSpec],
    child_results: list[CaseAgentRunResult],
    judge_outputs: list[CaseJudgeOutput],
    evidence_batches: list[EvidenceBatchResult],
    planning_output: CasePlanningOutput,
) -> tuple[CaseAgentRunResult, CaseEvidenceWorkspace, dict[str, object]]:
    child_statuses = [str(getattr(child, 'status', '') or '') for child in child_results]
    workspace = _workspace_with_child_target_surface(workspace, child_results)
    workspace = _workspace_with_judge_audit(workspace, {
        'note': 'orchestrator_split_into_child_cases_result',
        'child_case_count': len(child_results),
        'child_case_ids': [str(getattr(child, 'case_id', '') or '') for child in child_results],
        'child_statuses': child_statuses,
    })
    for child in child_results:
        if child.status in {'invalid', 'error'}:
            verifier_result = child.final_verifier_result or CaseVerifierResult(
                passed=False,
                issues=[VerifierIssue(
                    ref=str(getattr(child, 'case_id', '') or 'child_case'),
                    issue_code=f'child_case_{child.status}',
                    severity='blocked',
                    message=str(getattr(child, 'summary', '') or f'child case {child.status}'),
                )],
                summary=str(getattr(child, 'summary', '') or f'child case {child.status}'),
            )
            result = CaseAgentRunResult(
                False,
                workspace.header.case_id,
                child.status,
                'split_into_child_cases',
                child.final_output,
                verifier_result,
                workspace,
                judge_outputs,
                evidence_batches,
                f'child case {child.case_id} {child.status}',
                [f'child_case_{child.status}', str(getattr(child, 'summary', '') or '')],
                planning_output=planning_output,
                child_results=child_results,
            )
            return result, workspace, {
                'status': child.status,
                'reason': f'child_case_{child.status}',
                'child_case_id': child.case_id,
                'child_statuses': child_statuses,
            }
    failed_children = [child for child in child_results if child.status == 'fail_closed']
    if failed_children:
        fail_output = CaseJudgeOutput(
            action='fail_closed',
            fail_closed_reasons=[
                _child_result_fail_closed_reason(child, index=index + 1)
                for index, child in enumerate(failed_children)
            ],
            summary='one or more split child cases failed closed',
        )
        verifier_result = verify_judge_output(workspace.to_dossier(round_context='split_child_fail_closed'), fail_output)
        result = CaseAgentRunResult(
            True,
            workspace.header.case_id,
            'fail_closed',
            'split_into_child_cases',
            fail_output,
            verifier_result,
            workspace,
            judge_outputs,
            evidence_batches,
            'child_case_unresolved',
            ['child_case_unresolved', *[child.case_id for child in failed_children]],
            planning_output=planning_output,
            child_results=child_results,
        )
        return result, workspace, {
            'status': 'fail_closed',
            'reason': 'child_case_unresolved',
            'child_case_ids': [child.case_id for child in failed_children],
            'child_statuses': child_statuses,
        }
    assignment_intents: list[AssignmentIntent] = []
    findings: list[Finding] = []
    for child_index, child in enumerate(child_results, start=1):
        child_output = child.final_output
        if child_output is None or child_output.action != 'submit_verdict':
            fail_output = CaseJudgeOutput(
                action='fail_closed',
                fail_closed_reasons=[FailClosedReason(
                    ref=f'FR_CHILD_{child_index}',
                    reason_kind='insufficient_evidence',
                    description=f'child {child.case_id} did not produce an accepted verdict',
                    related_refs=list(getattr(getattr(child.final_workspace, 'contract', None), 'main_file_refs', []) or [])[:12],
                )],
                summary='split child accepted aggregation missing child verdict',
            )
            verifier_result = verify_judge_output(workspace.to_dossier(round_context='split_child_missing_verdict'), fail_output)
            result = CaseAgentRunResult(
                True,
                workspace.header.case_id,
                'fail_closed',
                'split_into_child_cases',
                fail_output,
                verifier_result,
                workspace,
                judge_outputs,
                evidence_batches,
                'child_case_unresolved',
                ['child_case_missing_verdict', child.case_id],
                planning_output=planning_output,
                child_results=child_results,
            )
            return result, workspace, {
                'status': 'fail_closed',
                'reason': 'child_case_missing_verdict',
                'child_case_id': child.case_id,
                'child_statuses': child_statuses,
            }
        child_finding_map: dict[str, str] = {}
        for finding in list(getattr(child_output, 'findings', []) or []):
            old_ref = str(getattr(finding, 'ref', '') or '')
            new_ref = f'CF{child_index}_{old_ref or len(findings) + 1}'
            child_finding_map[old_ref] = new_ref
            findings.append(finding.model_copy(update={'ref': new_ref}))
        for assignment in list(getattr(child_output, 'assignment_intents', []) or []):
            assignment = _remap_child_assignment_refs(child_index, assignment)
            support_finding_refs = [
                child_finding_map.get(str(ref or ''), str(ref or ''))
                for ref in list(getattr(assignment, 'support_finding_refs', []) or [])
                if str(ref or '')
            ]
            assignment_intents.append(assignment.model_copy(update={
                'ref': f'CA{child_index}_{str(getattr(assignment, "ref", "") or len(assignment_intents) + 1)}',
                'support_finding_refs': support_finding_refs,
            }))
    if not findings:
        findings = [Finding(ref='CF_SPLIT1', finding_kind='pass', description='all child cases accepted')]
        assignment_intents = [
            assignment.model_copy(update={'support_finding_refs': ['CF_SPLIT1']})
            for assignment in assignment_intents
        ]
    # Child BE/BES refs are deliberately scoped before root aggregation.  Once a
    # child verdict has passed its own verifier, the root aggregator must not
    # rewrite semantic target choices across children; it only verifies the
    # scoped aggregate contract.
    accepted_output = CaseJudgeOutput(
        action='submit_verdict',
        findings=findings,
        assignment_intents=assignment_intents,
        summary='accepted from split child cases',
    )
    verifier_result = verify_judge_output(workspace.to_dossier(round_context='split_child_accepted'), accepted_output)
    if not verifier_result.passed:
        workspace = _workspace_with_verifier_issues(workspace, verifier_result)
        result = CaseAgentRunResult(
            False,
            workspace.header.case_id,
            'invalid',
            'split_into_child_cases',
            accepted_output,
            verifier_result,
            workspace,
            judge_outputs,
            evidence_batches,
            'split child aggregation verifier rejected',
            _dedupe_preserve_order([str(getattr(issue, 'issue_code', '') or '') for issue in list(verifier_result.issues or [])]),
            planning_output=planning_output,
            child_results=child_results,
        )
        return result, workspace, {
            'status': 'invalid',
            'reason': 'split_child_aggregation_verifier_rejected',
            'verifier_issue_codes': _dedupe_preserve_order([str(getattr(issue, 'issue_code', '') or '') for issue in list(verifier_result.issues or [])]),
            'child_statuses': child_statuses,
        }
    result = CaseAgentRunResult(
        True,
        workspace.header.case_id,
        'accepted',
        'split_into_child_cases',
        accepted_output,
        verifier_result,
        workspace,
        judge_outputs,
        evidence_batches,
        'accepted from split child cases',
        [],
        planning_output=planning_output,
        child_results=child_results,
    )
    return result, workspace, {
        'status': 'accepted_verified',
        'assignment_count': len(assignment_intents),
        'child_statuses': child_statuses,
    }


def _latest_recorded_split_case_specs(workspace: CaseEvidenceWorkspace) -> dict[str, SplitCaseSpec]:
    for audit in reversed(list(getattr(workspace, 'judge_request_audits', []) or [])):
        if not isinstance(audit, dict):
            continue
        if audit.get('note') != 'orchestrator_split_plan_recorded':
            continue
        specs: dict[str, SplitCaseSpec] = {}
        for item in list(audit.get('split_cases') or []):
            if not isinstance(item, dict):
                continue
            try:
                spec = SplitCaseSpec.model_validate({
                    key: item.get(key)
                    for key in (
                        'child_case_ref',
                        'main_file_refs',
                        'main_group_refs',
                        'supplemental_file_refs',
                        'supplemental_group_refs',
                        'support_refs',
                        'reason',
                        'title_hints',
                        'query_hints',
                    )
                    if key in item
                })
            except Exception:
                continue
            child_ref = str(getattr(spec, 'child_case_ref', '') or '')
            if child_ref:
                specs[child_ref] = spec
        return specs
    return {}


def _split_cases_from_recorded_child_refs(
    workspace: CaseEvidenceWorkspace,
    child_refs: list[str],
) -> tuple[list[SplitCaseSpec], dict[str, object]]:
    requested_refs = _dedupe_preserve_order([str(ref or '') for ref in list(child_refs or []) if str(ref or '')])
    if not requested_refs:
        return [], {'recorded_child_case_refs_used': [], 'recorded_child_case_refs_missing': []}
    recorded_by_ref = _latest_recorded_split_case_specs(workspace)
    selected = [recorded_by_ref[ref] for ref in requested_refs if ref in recorded_by_ref]
    missing = [ref for ref in requested_refs if ref not in recorded_by_ref]
    return selected, {
        'recorded_child_case_refs_used': [str(getattr(spec, 'child_case_ref', '') or '') for spec in selected],
        'recorded_child_case_refs_missing': missing,
        'recorded_child_case_ref_count': len(selected),
    }


def _run_orchestrator_split_into_child_cases_tool(
    workspace: CaseEvidenceWorkspace,
    args: SplitIntoChildCasesToolArgs,
    ai_client,
    bangumi_client,
    judge_outputs: list[CaseJudgeOutput],
    evidence_batches: list[EvidenceBatchResult],
    *,
    planning_depth: int,
    max_rounds: int | None,
    orchestrator_context_soft_token_limit: int | None,
    orchestrator_context_hard_token_limit: int | None,
) -> tuple[CaseAgentRunResult | None, CaseEvidenceWorkspace, dict[str, object]]:
    execution_mode = str(getattr(args, 'execution_mode', '') or 'run_child_cases')
    coverage_mode = str(getattr(args, 'coverage_mode', '') or 'complete_root_coverage')
    require_complete_coverage = coverage_mode != 'selected_child_cases'
    raw_split_cases = list(getattr(args, 'split_cases', []) or [])
    recorded_child_refs = _dedupe_preserve_order([
        str(ref or '')
        for ref in list(getattr(args, 'recorded_child_case_refs', []) or [])
        if str(ref or '')
    ])
    recorded_split_cases, recorded_split_audit = _split_cases_from_recorded_child_refs(workspace, recorded_child_refs)
    explicit_split_case_count = len(raw_split_cases)
    if recorded_child_refs and not raw_split_cases:
        raw_split_cases = recorded_split_cases
    elif recorded_child_refs and raw_split_cases:
        existing_child_refs = {
            str(getattr(spec, 'child_case_ref', '') or '')
            for spec in raw_split_cases
            if str(getattr(spec, 'child_case_ref', '') or '')
        }
        raw_split_cases = [
            *raw_split_cases,
            *[
                spec for spec in recorded_split_cases
                if str(getattr(spec, 'child_case_ref', '') or '') not in existing_child_refs
            ],
        ]
    if (
        recorded_child_refs
        and explicit_split_case_count == 0
        and recorded_split_audit.get('recorded_child_case_refs_missing')
    ):
        return None, workspace, {
            'status': 'rejected',
            'reason': 'recorded_child_case_refs_not_found',
            **recorded_split_audit,
            'recommended_next_observation': (
                'Use child_case_ref values visible in recorded_split_plan.child_case_refs, '
                'or provide explicit split_cases with visible LF/LG refs.'
            ),
        }
    expanded_split_cases, split_group_expand_audit = _expand_split_group_refs(workspace, raw_split_cases)
    split_cases, split_canonicalize_audit = _canonicalize_split_main_refs(workspace, expanded_split_cases)
    issues = _split_case_validation_issues(
        workspace,
        split_cases,
        require_complete_coverage=require_complete_coverage,
    )
    planning_output = CasePlanningOutput(
        action='split_into_cases',
        split_cases=split_cases,
        summary=str(getattr(args, 'reason', '') or 'OrchestratorAgent split_into_child_cases'),
    )
    workspace = _workspace_with_judge_audit(workspace, {
        'note': 'orchestrator_split_into_child_cases_requested',
        'reason': str(getattr(args, 'reason', '') or ''),
        'execution_mode': execution_mode,
        'coverage_mode': coverage_mode,
        'split_case_count': len(split_cases),
        'child_case_refs': [str(getattr(spec, 'child_case_ref', '') or '') for spec in split_cases],
        'main_file_ref_counts': [len(list(getattr(spec, 'main_file_refs', []) or [])) for spec in split_cases],
        **recorded_split_audit,
        **split_group_expand_audit,
        **split_canonicalize_audit,
    })
    if issues:
        issue_codes = _dedupe_preserve_order([str(getattr(issue, 'issue_code', '') or '') for issue in issues])
        compact_issues = _compact_verifier_issues(issues)
        diagnostics = _split_case_validation_diagnostics(workspace, split_cases)
        workspace = _workspace_with_verifier_issues(workspace, CaseVerifierResult(
            passed=False,
            issues=issues,
            summary='split_into_child_cases rejected',
        ))
        return None, workspace, {
            'status': 'rejected',
            'reason': 'split_validation_failed',
            'issue_codes': issue_codes,
            'issues': compact_issues,
            'verifier_issue_codes': issue_codes,
            'verifier_issues': compact_issues,
            **recorded_split_audit,
            **diagnostics,
            **split_group_expand_audit,
            **split_canonicalize_audit,
            'recommended_next_observation': (
                'revise split_into_child_cases so child main_file_refs or main_group_refs are visible, non-empty, '
                'and non-overlapping. If coverage_mode=complete_root_coverage, cover every root main LF exactly once; '
                'if coverage_mode=selected_child_cases, leave unselected LF refs for root ledger/mapping intents.'
            ),
        }
    diagnostics = _split_case_validation_diagnostics(workspace, split_cases)
    if execution_mode == 'record_split_plan_only':
        workspace = _workspace_with_judge_audit(workspace, {
            'note': 'orchestrator_split_plan_recorded',
            'reason': str(getattr(args, 'reason', '') or ''),
            'execution_mode': execution_mode,
            'coverage_mode': coverage_mode,
            'split_case_count': len(split_cases),
            'child_case_refs': [str(getattr(spec, 'child_case_ref', '') or '') for spec in split_cases],
            'split_cases': [
                {
                    'plan_row_ref': f'RSP{index}',
                    'child_case_ref': str(getattr(spec, 'child_case_ref', '') or ''),
                    'main_file_refs': list(getattr(spec, 'main_file_refs', []) or []),
                    'main_group_refs': list(getattr(spec, 'main_group_refs', []) or [])[:24],
                    'supplemental_file_refs': list(getattr(spec, 'supplemental_file_refs', []) or []),
                    'title_hints': list(getattr(spec, 'title_hints', []) or [])[:8],
                    'query_hints': list(getattr(spec, 'query_hints', []) or [])[:8],
                    'support_refs': list(getattr(spec, 'support_refs', []) or [])[:12],
                    'reason': str(getattr(spec, 'reason', '') or '')[:360],
                }
                for index, spec in enumerate(split_cases, start=1)
            ],
            'main_file_ref_counts': [len(list(getattr(spec, 'main_file_refs', []) or [])) for spec in split_cases],
            'missing_main_refs': list(diagnostics.get('missing_main_refs') or []),
            'duplicate_main_refs': list(diagnostics.get('duplicate_main_refs') or []),
            'extra_main_refs': list(diagnostics.get('extra_main_refs') or []),
            **recorded_split_audit,
            **split_group_expand_audit,
            **split_canonicalize_audit,
            'recommended_next_observation': (
                'split boundary plan recorded without running child sessions. Continue with root ledger/intents, '
                'or call split_into_child_cases(execution_mode=run_child_cases, coverage_mode=selected_child_cases) '
                'for selected major units if focused child context is useful.'
            ),
        })
        return None, workspace, {
            'status': 'split_plan_recorded',
            'workspace_changed': True,
            'target_surface_changed': False,
            'execution_mode': execution_mode,
            'coverage_mode': coverage_mode,
            'split_case_count': len(split_cases),
            'child_case_refs': [str(getattr(spec, 'child_case_ref', '') or '') for spec in split_cases],
            **diagnostics,
            **recorded_split_audit,
            **split_group_expand_audit,
            **split_canonicalize_audit,
            'draft_accounting': _mapping_draft_observation(workspace).get('draft_accounting'),
            'executable_menu_summary': _executable_menu_observation(workspace),
            'recommended_next_observation': (
                'Boundary plan is now an observation only; no child results were imported. '
                'Resolve the root rows with case_resolution_ledger/mapping intents or run selected child cases explicitly.'
            ),
        }
    if len(split_cases) > MAX_CHILD_CASES_PER_TOOL_CALL:
        workspace = _workspace_with_judge_audit(workspace, {
            'note': 'orchestrator_child_execution_budget_rejected',
            'reason': str(getattr(args, 'reason', '') or ''),
            'execution_mode': execution_mode,
            'coverage_mode': coverage_mode,
            'split_case_count': len(split_cases),
            'max_child_cases_per_tool_call': MAX_CHILD_CASES_PER_TOOL_CALL,
            'child_case_refs': [str(getattr(spec, 'child_case_ref', '') or '') for spec in split_cases],
            **recorded_split_audit,
            **diagnostics,
            'recommended_next_observation': (
                'child execution batch is too large for one tool call. This is a runtime budget guard only, not a semantic split decision. '
                'Record the split plan, resolve rows in root ledger with plan_row_refs=RSP*, or run at most '
                f'{MAX_CHILD_CASES_PER_TOOL_CALL} selected child cases that really need focused context.'
            ),
        })
        return None, workspace, {
            'status': 'rejected',
            'reason': 'child_execution_batch_too_large',
            'workspace_changed': True,
            'target_surface_changed': False,
            'execution_mode': execution_mode,
            'coverage_mode': coverage_mode,
            'split_case_count': len(split_cases),
            'max_child_cases_per_tool_call': MAX_CHILD_CASES_PER_TOOL_CALL,
            'child_case_refs': [str(getattr(spec, 'child_case_ref', '') or '') for spec in split_cases],
            **recorded_split_audit,
            **diagnostics,
            'draft_accounting': _mapping_draft_observation(workspace).get('draft_accounting'),
            'executable_menu_summary': _executable_menu_observation(workspace),
            'recommended_next_observation': (
                'Too many child sessions were requested at once. Choose a smaller selected_child_cases batch, '
                'use record_split_plan_only, or resolve package rows in root ledger with RSP plan_row_refs.'
            ),
        }
    if planning_depth >= MAX_ORCHESTRATOR_SPLIT_DEPTH:
        return None, workspace, {
            'status': 'rejected',
            'reason': 'split_depth_limit_reached',
            'planning_depth': planning_depth,
            'max_split_depth': MAX_ORCHESTRATOR_SPLIT_DEPTH,
            'recommended_next_observation': 'this child is already nested too deeply; handle it as one case with mapping intents or fail_closed if evidence is exhausted',
        }
    child_results: list[CaseAgentRunResult] = []
    if max_rounds is None:
        child_round_limit = max(16, min(20, int(getattr(workspace.budget, 'max_judge_rounds', 0) or 16)))
    else:
        # A split child is an independent case, not a remaining slice of the
        # root turn budget. Keep explicit audit caps from starving children
        # before they can execute query/evidence/intent/finish once.
        child_round_limit = min(20, max(16, int(max_rounds)))
    for spec in split_cases:
        child_workspace = build_child_workspace(workspace, spec)
        child_workspace = _workspace_preserving_state(
            child_workspace,
            case_briefing=None,
            investigation_notebook=None,
            case_resolution_ledger=None,
            mapping_draft=None,
            mapping_draft_patches=[],
            mapping_draft_candidate_comparisons=[],
        )
        child_workspace = _workspace_with_child_split_span(child_workspace, spec)
        child_workspace = _workspace_with_judge_audit(child_workspace, {
            'note': 'orchestrator_child_case_started',
            'parent_case_id': workspace.header.case_id,
            'child_case_ref': str(getattr(spec, 'child_case_ref', '') or ''),
            'title_hints': list(getattr(spec, 'title_hints', []) or []),
            'query_hints': list(getattr(spec, 'query_hints', []) or []),
        })
        # This is retained legacy OrchestratorAgent tool behavior.  The public
        # run_local_bangumi_case_agent entry now routes to HumanCaseAgent and
        # must not become an implicit child-session fallback.
        child_result = _run_orchestrator_agent_main_loop(
            child_workspace,
            ai_client,
            bangumi_client,
            planning_output=None,
            planning_evidence_batches=[],
            max_rounds=child_round_limit,
            orchestrator_context_soft_token_limit=orchestrator_context_soft_token_limit,
            orchestrator_context_hard_token_limit=orchestrator_context_hard_token_limit,
            planning_depth=planning_depth + 1,
            allow_legacy_orchestrator_helper=True,
        )
        child_results.append(child_result)
    if coverage_mode == 'selected_child_cases':
        workspace = _workspace_with_child_target_surface(workspace, child_results)
        child_statuses = [str(getattr(child, 'status', '') or '') for child in child_results]
        terminal_child_blockers = [
            {
                'child_case_id': str(getattr(child, 'case_id', '') or ''),
                'status': str(getattr(child, 'status', '') or ''),
                'summary': str(getattr(child, 'summary', '') or ''),
                'errors': list(getattr(child, 'errors', []) or [])[:8],
            }
            for child in child_results
            if str(getattr(child, 'status', '') or '') in {'fail_closed', 'invalid', 'error'}
        ]
        workspace = _workspace_with_judge_audit(workspace, {
            'note': 'orchestrator_selected_child_cases_result',
            'reason': str(getattr(args, 'reason', '') or ''),
            'coverage_mode': coverage_mode,
            'split_case_count': len(split_cases),
            'child_case_ids': [str(getattr(child, 'case_id', '') or '') for child in child_results],
            'child_statuses': child_statuses,
            'terminal_child_blockers': terminal_child_blockers[:8],
            'missing_main_refs': list(diagnostics.get('missing_main_refs') or []),
            'recommended_next_observation': (
                'selected child sessions completed as observations, not as root terminal verdict. '
                'Use imported child target surface plus root-visible LF/LS refs in propose_case_resolution_ledger '
                'or propose_mapping_intents; close unselected root rows in the root case.'
            ),
        })
        status = 'selected_child_cases_completed'
        if terminal_child_blockers:
            status = 'selected_child_cases_completed_with_blockers'
        observation = {
            'status': status,
            'workspace_changed': True,
            'target_surface_changed': True,
            'execution_mode': execution_mode,
            'coverage_mode': coverage_mode,
            'split_case_count': len(split_cases),
            'child_case_ids': [str(getattr(child, 'case_id', '') or '') for child in child_results],
            'child_statuses': child_statuses,
            'terminal_child_blockers': terminal_child_blockers,
            **diagnostics,
            **recorded_split_audit,
            **split_group_expand_audit,
            **split_canonicalize_audit,
            'draft_accounting': _mapping_draft_observation(workspace).get('draft_accounting'),
            'executable_menu_summary': _executable_menu_observation(workspace),
            'recommended_next_observation': (
                'Selected child cases are observations only. Continue root-level resolution with ledger/intents; '
                'do not assume child accepted outputs automatically finish root accounting.'
            ),
        }
        return None, workspace, observation
    result, workspace, observation = _aggregate_child_case_results(
        workspace,
        split_cases,
        child_results,
        judge_outputs,
        evidence_batches,
        planning_output,
    )
    observation = {
        **observation,
        'workspace_changed': True,
        'split_case_count': len(split_cases),
        'child_case_ids': [str(getattr(child, 'case_id', '') or '') for child in child_results],
        'child_statuses': [str(getattr(child, 'status', '') or '') for child in child_results],
        **recorded_split_audit,
        **split_group_expand_audit,
        **split_canonicalize_audit,
    }
    return result, workspace, observation


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


def _draft_progress_signature(workspace: CaseEvidenceWorkspace) -> tuple[int, int, int, int, int, int]:
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None:
        return (0, 0, 0, 0, 0, 0)
    dossier = workspace.to_dossier(round_context='orchestrator_turn_health')
    accounting = compute_mapping_draft_accounting(draft, dossier)
    return (
        int(getattr(accounting, 'mapped_file_count', 0) or 0),
        int(getattr(accounting, 'excluded_file_count', 0) or 0),
        int(getattr(accounting, 'accounted_for_count', 0) or 0),
        int(getattr(accounting, 'unresolved_count', 0) or 0),
        len(list(getattr(workspace, 'mapping_draft_patches', []) or [])),
        len(list(getattr(workspace, 'bangumi_items', []) or [])) + len(list(getattr(workspace, 'bangumi_span_cards', []) or [])),
    )


def _observation_made_progress(
    observation: dict[str, object],
    before_signature: tuple[int, int, int, int, int, int],
    after_signature: tuple[int, int, int, int, int, int],
) -> bool:
    if str(observation.get('status') or '') in {'rejected', 'error'}:
        return False
    if bool(observation.get('workspace_changed')) or bool(observation.get('target_surface_changed')):
        return True
    if after_signature != before_signature:
        return True
    for key in ('compiled_patch_count', 'new_query_refs', 'new_subject_refs', 'new_item_refs', 'new_span_refs', 'split_case_count'):
        value = observation.get(key)
        if isinstance(value, int) and value > 0:
            return True
        if isinstance(value, list) and value:
            return True
    return False


def _record_turn_health(
    workspace: CaseEvidenceWorkspace,
    session: OrchestratorAgentSession,
    observation: dict[str, object],
    *,
    max_turns: int,
    before_signature: tuple[int, int, int, int, int, int],
    after_signature: tuple[int, int, int, int, int, int],
) -> tuple[CaseEvidenceWorkspace, OrchestratorAgentSession]:
    turn_count = int(getattr(session, 'turn_count', 0) or 0)
    turn_budget_ratio = (turn_count / max(1, int(max_turns or 1)))
    near_turn_limit = turn_budget_ratio >= 0.8
    progressed = _observation_made_progress(observation, before_signature, after_signature)
    consecutive_stall_count = 0 if progressed else int(getattr(session, 'consecutive_stall_count', 0) or 0) + 1
    stall_suspected = consecutive_stall_count >= 2 and str(observation.get('status') or '') not in {'terminal'}
    updated_session = replace(
        session,
        consecutive_stall_count=consecutive_stall_count,
        near_turn_limit_unhealthy_count=int(getattr(session, 'near_turn_limit_unhealthy_count', 0) or 0) + (1 if near_turn_limit else 0),
        stall_suspected_count=int(getattr(session, 'stall_suspected_count', 0) or 0) + (1 if stall_suspected else 0),
    )
    if near_turn_limit or stall_suspected:
        workspace = _workspace_with_judge_audit(workspace, {
            'note': 'orchestrator_turn_health',
            'turn_count': turn_count,
            'max_turns': max_turns,
            'turn_budget_ratio': turn_budget_ratio,
            'near_turn_limit_unhealthy': near_turn_limit,
            'stall_suspected': stall_suspected,
            'consecutive_stall_count': consecutive_stall_count,
            'tool_status': str(observation.get('status') or ''),
            'workspace_changed': bool(observation.get('workspace_changed')),
            'target_surface_changed': bool(observation.get('target_surface_changed')),
            'recommended_next_observation': (
                'This is diagnostic only: the agent should pursue a new concrete observation, revise a specific mapping intent, split/repartition, or finish legally instead of repeating a non-progressing action.'
            ),
        })
    return workspace, updated_session


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


def _latest_blocked_intent_observations_by_local(workspace: CaseEvidenceWorkspace) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for audit in reversed(list(getattr(workspace, 'judge_request_audits', []) or [])):
        if not isinstance(audit, dict) or audit.get('note') != 'orchestrator_mapping_intents_result':
            continue
        for blocked in list(audit.get('blocked_intents') or []):
            if not isinstance(blocked, dict):
                continue
            local_ref = str(blocked.get('local_ref') or '')
            if not local_ref or local_ref in result:
                continue
            result[local_ref] = {
                'issue_codes': list(blocked.get('issue_codes') or [])[:8],
                'requested_request_types': list(blocked.get('requested_request_types') or [])[:8],
                'candidate_target_refs': list(blocked.get('candidate_target_refs') or [])[:8],
                'subject_refs': list(blocked.get('subject_refs') or [])[:8],
                'item_refs': list(blocked.get('item_refs') or [])[:8],
                'reason': str(blocked.get('reason') or '')[:360],
                'recommended_next_observation': str(blocked.get('recommended_next_observation') or '')[:360],
            }
        if result:
            break
    return result


def _fail_closed_reasons_from_workspace(workspace: CaseEvidenceWorkspace, *, finish_kind: str, reason: str) -> list[FailClosedReason]:
    draft = getattr(workspace, 'mapping_draft', None)
    dossier = workspace.to_dossier(round_context=f'finish_case_{finish_kind}')
    reasons = _mapping_draft_unresolved_fail_closed_reasons(draft, dossier, workspace=workspace) if draft is not None else []
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
    allow_turn_budget: bool = False,
) -> tuple[CaseAgentRunResult | None, CaseEvidenceWorkspace, dict[str, object]]:
    budget_exhausted = bool(
        getattr(workspace.budget, 'max_evidence_batches', 0)
        and workspace.budget.used_evidence_batches >= workspace.budget.max_evidence_batches
    )
    turn_budget_exhausted = bool(allow_turn_budget and finish_kind == 'budget_exhausted')
    budget_exhausted = bool(budget_exhausted or turn_budget_exhausted)
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
    if (
        finish_kind == 'budget_exhausted'
        and turn_budget_exhausted
        and (
            int(no_new_audit.get('remaining_target_side_executable_request_count') or 0) > 0
            or int(no_new_audit.get('durable_draft_evidence_intent_count') or 0) > 0
            or int(no_new_audit.get('human_next_action_blocked_no_new_evidence_count') or 0) > 0
        )
    ):
        return None, workspace, {
            'status': 'rejected',
            'reason': 'turn_budget_exhausted_with_executable_agenda',
            **no_new_audit,
            **_mapping_draft_observation(workspace),
            'finish_gate': _finish_gate_observation(workspace),
            'executable_menu_summary': _executable_menu_observation(workspace),
            'recommended_next_observation': (
                'the turn limit was reached while executable evidence or open agenda remains; '
                'raise the procedural turn cap or execute the listed requests instead of recording fail_closed'
            ),
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
        'turn_budget_exhausted': turn_budget_exhausted,
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
    planning_depth: int = 0,
    allow_legacy_orchestrator_helper: bool = False,
) -> CaseAgentRunResult:
    if not allow_legacy_orchestrator_helper and os.environ.get(LEGACY_ORCHESTRATOR_HELPER_ENV) != '1':
        workspace = _workspace_with_judge_audit(workspace, {
            'note': 'legacy_orchestrator_helper_blocked',
            'case_agent_mode': 'human_case_agent',
            'reason': (
                'Local->Bangumi product primary path is HumanCaseAgent. '
                'The retained OrchestratorAgent loop may only be run by explicit legacy tests or migration diagnostics.'
            ),
        })
        return _orchestrator_error_result(
            workspace,
            summary='legacy orchestrator helper is not a product fallback',
            error_kind='legacy_orchestrator_helper_blocked',
            planning_output=planning_output,
            evidence_batches=list(planning_evidence_batches or []),
        )
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
    workspace = _workspace_with_judge_audit(workspace, {
        'note': 'orchestrator_case_session_started',
        'planning_depth': planning_depth,
        'max_split_depth': MAX_ORCHESTRATOR_SPLIT_DEPTH,
    })
    soft_limit = max(8192, int(orchestrator_context_soft_token_limit or 180000))
    hard_limit = max(soft_limit + 1024, int(orchestrator_context_hard_token_limit or 300000))
    max_turns = max(1, int(max_rounds)) if max_rounds is not None else _default_orchestrator_max_turns_for_workspace(workspace)
    tool_rejection_limit = 12
    consecutive_tool_rejections = 0
    transport_failure_limit = 8
    consecutive_transport_failures = 0
    turn_index = 0
    accepted_finish_grace_used = False
    _write_orchestrator_progress(
        workspace,
        orchestrator_session,
        phase='started',
        turn_index=turn_index,
        max_turns=max_turns,
    )
    while turn_index < max_turns or (
        not accepted_finish_grace_used
        and bool(_finish_gate_observation(workspace).get('accepted_finish_allowed'))
    ):
        is_finish_grace_turn = turn_index >= max_turns
        if is_finish_grace_turn:
            accepted_finish_grace_used = True
            workspace = _workspace_with_judge_audit(workspace, {
                'note': 'orchestrator_accepted_finish_grace_turn',
                'turn_count': turn_index + 1,
                'base_max_turns': max_turns,
                'finish_gate': _finish_gate_observation(workspace),
                'reason': 'accepted accounting became ready at the turn budget boundary; allow one extra Agent turn to call finish_case',
            })
        turn_label = max_turns + 1 if is_finish_grace_turn else max_turns
        _trace_orchestrator(f'{workspace.header.case_id} turn={turn_index + 1}/{turn_label} call_agent')
        workspace = _prepare_workspace_for_orchestrator_agent_turn(workspace)
        _write_orchestrator_progress(
            workspace,
            orchestrator_session,
            phase='calling_agent',
            turn_index=turn_index + 1,
            max_turns=max_turns,
        )
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
            _trace_orchestrator(f'{workspace.header.case_id} turn={turn_index + 1}/{turn_label} agent_error={agent_result.error or "no_tool_call"}')
            _write_orchestrator_progress(
                workspace,
                orchestrator_session,
                phase='agent_error',
                turn_index=turn_index + 1,
                max_turns=max_turns,
                observation={'status': 'error', 'reason': agent_result.error or 'no_tool_call'},
            )
            if agent_result.error == 'orchestrator_agent_transport_unavailable':
                error_result = _orchestrator_error_result(
                    workspace,
                    summary='orchestrator agent transport unavailable',
                    error_kind='orchestrator_agent_unavailable',
                    planning_output=planning_output,
                    evidence_batches=evidence_batches,
                )
                return _finalize_orchestrator_result(error_result, workspace, orchestrator_session)
            if (
                agent_result.error == 'orchestrator_agent_no_response'
                and bool((agent_result.audit or {}).get('transport_failure'))
                and agent_result.tool_call is None
            ):
                consecutive_transport_failures += 1
                workspace = _workspace_with_judge_audit(workspace, {
                    'note': 'orchestrator_agent_transport_retry',
                    'error_kind': agent_result.error,
                    'consecutive_transport_failures': consecutive_transport_failures,
                    'transport_failure_limit': transport_failure_limit,
                    'turn_index_unchanged': turn_index,
                    'reason': 'provider returned no response; retry without consuming a semantic OrchestratorAgent turn',
                })
                if consecutive_transport_failures >= transport_failure_limit:
                    error_result = _orchestrator_error_result(
                        workspace,
                        summary='orchestrator agent provider unavailable',
                        error_kind='orchestrator_agent_provider_unavailable',
                        planning_output=planning_output,
                        evidence_batches=evidence_batches,
                    )
                    return _finalize_orchestrator_result(error_result, workspace, orchestrator_session)
                continue
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
            turn_index += 1
            continue
        tool_call = agent_result.tool_call
        consecutive_transport_failures = 0
        _trace_orchestrator(f'{workspace.header.case_id} turn={turn_index + 1}/{turn_label} selected={tool_call.tool_name}')
        _write_orchestrator_progress(
            workspace,
            orchestrator_session,
            phase='tool_selected',
            turn_index=turn_index + 1,
            max_turns=max_turns,
            tool_name=tool_call.tool_name,
        )
        workspace, decision, tool_acceptance = _decision_from_orchestrator_tool_call(workspace, tool_call)
        workspace = _workspace_with_judge_audit(workspace, {
            'note': 'orchestrator_tool_selected',
            'tool_name': tool_call.tool_name,
            'tool_call_id': tool_call.call_id,
            'accepted': bool(tool_acceptance.get('accepted')),
            'ref_issue_codes': [
                str(issue.get('issue') or '')
                for issue in list(tool_acceptance.get('ref_issues') or [])
                if isinstance(issue, dict) and str(issue.get('issue') or '')
            ],
            'ref_issue_refs': [
                str(issue.get('ref') or '')
                for issue in list(tool_acceptance.get('ref_issues') or [])
                if isinstance(issue, dict) and str(issue.get('ref') or '')
            ],
            'ref_corrections': [
                str(issue.get('correction') or '')
                for issue in list(tool_acceptance.get('ref_issues') or [])[:8]
                if isinstance(issue, dict) and str(issue.get('correction') or '')
            ],
            **tool_acceptance,
        })
        if decision is None:
            _trace_orchestrator(f'{workspace.header.case_id} turn={turn_index + 1}/{turn_label} rejected={tool_call.tool_name} reason={tool_acceptance.get("reason") or ""}')
            consecutive_tool_rejections += 1
            orchestrator_session = replace(
                orchestrator_session,
                tool_rejection_count=orchestrator_session.tool_rejection_count + 1,
            )
            rejection_signature = _draft_progress_signature(workspace)
            orchestrator_session = record_orchestrator_tool_output(
                orchestrator_session,
                tool_call,
                {'status': 'rejected', **tool_acceptance},
            )
            workspace, orchestrator_session = _record_turn_health(
                workspace,
                orchestrator_session,
                {'status': 'rejected', **tool_acceptance},
                max_turns=max_turns,
                before_signature=rejection_signature,
                after_signature=rejection_signature,
            )
            _write_orchestrator_progress(
                workspace,
                orchestrator_session,
                phase='tool_rejected',
                turn_index=turn_index + 1,
                max_turns=max_turns,
                tool_name=tool_call.tool_name,
                observation={'status': 'rejected', **tool_acceptance},
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
            turn_index += 1
            continue
        result: CaseAgentRunResult | None = None
        observation: dict[str, object]
        before_progress_signature = _draft_progress_signature(workspace)
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
        elif decision.action == 'propose_case_resolution_ledger':
            args = tool_call.arguments if isinstance(tool_call.arguments, ProposeCaseResolutionLedgerToolArgs) else ProposeCaseResolutionLedgerToolArgs()
            workspace, observation = _run_orchestrator_propose_case_resolution_ledger_tool(workspace, args)
        elif decision.action == 'propose_mapping_intents':
            args = tool_call.arguments if isinstance(tool_call.arguments, ProposeMappingIntentsToolArgs) else ProposeMappingIntentsToolArgs()
            workspace, observation = _run_orchestrator_propose_mapping_intents_tool(workspace, args)
        elif decision.action == 'update_notebook':
            args = tool_call.arguments if isinstance(tool_call.arguments, UpdateNotebookToolArgs) else UpdateNotebookToolArgs()
            workspace, observation = _run_orchestrator_update_notebook_tool(workspace, args)
        elif decision.action == 'reconsider_split':
            args = tool_call.arguments if isinstance(tool_call.arguments, ReconsiderSplitToolArgs) else ReconsiderSplitToolArgs()
            workspace, observation = _run_orchestrator_reconsider_split_tool(workspace, args)
        elif decision.action == 'split_into_child_cases':
            args = tool_call.arguments if isinstance(tool_call.arguments, SplitIntoChildCasesToolArgs) else SplitIntoChildCasesToolArgs()
            result, workspace, observation = _run_orchestrator_split_into_child_cases_tool(
                workspace,
                args,
                ai_client,
                bangumi_client,
                judge_outputs,
                evidence_batches,
                planning_depth=planning_depth,
                max_rounds=max_rounds,
                orchestrator_context_soft_token_limit=orchestrator_context_soft_token_limit,
                orchestrator_context_hard_token_limit=orchestrator_context_hard_token_limit,
            )
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
        after_progress_signature = _draft_progress_signature(workspace)
        _trace_orchestrator(
            f'{workspace.header.case_id} turn={turn_index + 1}/{turn_label} output={tool_call.tool_name} '
            f'status={observation.get("status") or ""} changed={bool(observation.get("workspace_changed"))} '
            f'target_changed={bool(observation.get("target_surface_changed"))}'
        )
        if result is not None:
            orchestrator_session = record_orchestrator_tool_output(
                orchestrator_session,
                tool_call,
                {'status': 'terminal', 'terminal_status': result.status, **(observation or {})},
            )
            _write_orchestrator_progress(
                workspace,
                orchestrator_session,
                phase='terminal',
                turn_index=turn_index + 1,
                max_turns=max_turns,
                tool_name=tool_call.tool_name,
                observation={'status': 'terminal', 'terminal_status': result.status, **(observation or {})},
            )
            return _finalize_orchestrator_result(result, workspace, orchestrator_session)
        if str(observation.get('status') or '') in {'rejected', 'error'}:
            workspace = _workspace_with_judge_audit(workspace, {
                'note': 'orchestrator_tool_output_rejected',
                'tool_name': tool_call.tool_name,
                'reason': str(observation.get('reason') or ''),
                'recommended_next_observation': str(observation.get('recommended_next_observation') or ''),
                'verifier_issue_codes': observation.get('verifier_issue_codes') if isinstance(observation.get('verifier_issue_codes'), list) else [],
                'issue_codes': observation.get('issue_codes') if isinstance(observation.get('issue_codes'), list) else [],
                'issues': observation.get('issues') if isinstance(observation.get('issues'), list) else [],
                'verifier_issues': observation.get('verifier_issues') if isinstance(observation.get('verifier_issues'), list) else [],
                'missing_main_refs': observation.get('missing_main_refs') if isinstance(observation.get('missing_main_refs'), list) else [],
                'duplicate_main_refs': observation.get('duplicate_main_refs') if isinstance(observation.get('duplicate_main_refs'), list) else [],
                'extra_main_refs': observation.get('extra_main_refs') if isinstance(observation.get('extra_main_refs'), list) else [],
                'child_ref_counts': observation.get('child_ref_counts') if isinstance(observation.get('child_ref_counts'), list) else [],
                'patch_issue_codes': observation.get('patch_issue_codes') if isinstance(observation.get('patch_issue_codes'), list) else [],
                'accounting_issue_codes': observation.get('accounting_issue_codes') if isinstance(observation.get('accounting_issue_codes'), list) else [],
                'required_next_tools': observation.get('required_next_tools') if isinstance(observation.get('required_next_tools'), list) else [],
                'ref_issue_codes': observation.get('ref_issue_codes') if isinstance(observation.get('ref_issue_codes'), list) else [],
                'ref_issue_refs': observation.get('ref_issue_refs') if isinstance(observation.get('ref_issue_refs'), list) else [],
                'ref_corrections': observation.get('ref_corrections') if isinstance(observation.get('ref_corrections'), list) else [],
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
        workspace, orchestrator_session = _record_turn_health(
            workspace,
            orchestrator_session,
            observation,
            max_turns=max_turns,
            before_signature=before_progress_signature,
            after_signature=after_progress_signature,
        )
        _write_orchestrator_progress(
            workspace,
            orchestrator_session,
            phase='tool_output',
            turn_index=turn_index + 1,
            max_turns=max_turns,
            tool_name=tool_call.tool_name,
            observation=observation,
        )
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
        turn_index += 1
    result, workspace, _observation = _build_orchestrator_fail_closed_result(
        workspace,
        judge_outputs,
        evidence_batches,
        planning_output=planning_output,
        finish_kind='budget_exhausted',
        reason=f'orchestrator turn limit reached: {max_turns}',
        allow_tool_loop=False,
        allow_turn_budget=True,
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
    _write_orchestrator_progress(
        workspace,
        orchestrator_session,
        phase='turn_limit',
        turn_index=turn_index,
        max_turns=max_turns,
        observation={'status': 'error', 'reason': f'orchestrator turn limit reached: {max_turns}'},
    )
    return _finalize_orchestrator_result(error_result, workspace, orchestrator_session)


def _prepare_workspace_for_orchestrator_agent_turn(workspace: CaseEvidenceWorkspace) -> CaseEvidenceWorkspace:
    if not _case_understanding_applied(workspace):
        return workspace
    finish_gate = _finish_gate_observation(workspace)
    if bool(finish_gate.get('accepted_finish_allowed')):
        return workspace
    return _refresh_mapping_draft_candidates(_workspace_with_initial_mapping_draft(workspace))


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
        row_disposition = str(getattr(row, 'disposition', '') or '')
        row_status = str(getattr(row, 'status', '') or '')
        if (
            row_disposition in {'map_to_bangumi', 'non_bangumi_or_supplemental', 'unaligned_fail_closed'}
            or row_status == 'verified'
        ):
            continue
        if (
            row_status not in {'open', 'unresolved'}
            and row_disposition not in {'open', 'needs_more_evidence'}
        ):
            continue
        row_local_ref = str(getattr(row, 'local_ref', '') or '')
        local_span = next((card for card in getattr(dossier, 'local_span_cards', []) or [] if getattr(card, 'ref', '') == row_local_ref), None)
        linked = [
            span.ref for span in detail_spans
            if str(getattr(span, 'source_request_ref', '') or '') == f'REQ_TARGET_SPAN_{row_local_ref}'
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
            if has_exact_window:
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
            if not linked and local_count == 1 and len(detail_spans) == 1:
                linked = [
                    span.ref for span in detail_spans
                    if not str(getattr(span, 'ref', '') or '').startswith('BES_INTENT_')
                    and int(getattr(span, 'target_ref_count', 0) or len(getattr(span, 'target_refs', []) or [])) == local_count
                ]
            if not linked and local_count == 1 and local_start == 0 and local_end == 0:
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
            special_linked = list(special_item_refs)
        special_span_linked: list[str] = []
        local_count_for_special = int(getattr(local_span, 'file_ref_count', 0) or len(getattr(local_span, 'file_refs', []) or [])) if local_span is not None else 0
        special_span_linked = [
            span.ref for span in detail_spans
            if str(getattr(span, 'item_kind', '') or '') == 'special'
            and not str(getattr(span, 'ref', '') or '').startswith('BES_INTENT_')
            and int(getattr(span, 'target_ref_count', 0) or len(getattr(span, 'target_refs', []) or [])) == local_count_for_special
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
        merged = list(dict.fromkeys([*before, *linked, *special_span_linked, *special_linked]))
        if merged and row_disposition == 'needs_more_evidence':
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
            related = str(related_ref or '')
            row = rows_by_ref.get(related)
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
        if isinstance(audit, dict) and audit.get('note') in {'orchestrator_mapping_intents_result', 'orchestrator_case_resolution_ledger_result'}
    )


def _no_new_evidence_precondition_audit(workspace: CaseEvidenceWorkspace) -> dict[str, object]:
    executable_ids = _remaining_executable_menu_request_ids(workspace)
    target_side_ids = _remaining_executable_menu_request_ids(workspace, target_side_only=True)
    editor_calls = _editor_call_count_after_latest_evidence(workspace)
    mapping_intent_calls = _mapping_intent_call_count_after_latest_evidence(workspace)
    semantic_decision_calls = editor_calls + mapping_intent_calls
    deferred_subject_recall = _has_deferred_subject_recall_agenda(workspace)
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


def _expected_namespace_for_ref_field(field_path: str) -> str:
    if 'plan_row_refs' in field_path:
        return 'RSP'
    if 'main_group_refs' in field_path or 'supplemental_group_refs' in field_path:
        return 'LG'
    if 'chosen_item_ref' in field_path or 'item_refs' in field_path:
        return 'BE'
    if 'chosen_subject_ref' in field_path or 'subject_refs' in field_path:
        return 'BS'
    if 'chosen_span_ref' in field_path:
        return 'BES'
    if 'candidate_target_refs' in field_path:
        return 'BS/BE/BES'
    if 'target_refs' in field_path or 'target_ref' in field_path or 'target_span_ref' in field_path:
        return 'BS/BE/BES/BER/BG'
    if 'query_refs' in field_path:
        return 'QC/SQ'
    if 'row_ref' in field_path or 'row_refs' in field_path:
        return 'MDR'
    if 'file_refs' in field_path:
        return 'LF'
    if 'span_refs' in field_path:
        return 'LS'
    if 'local_ref' in field_path or 'local_refs' in field_path:
        return 'LF/LS/LC'
    if 'support_refs' in field_path or 'source_refs' in field_path:
        return 'visible LF/LS/LC/LG/BS/BE/BES/BER/BG/QC/SQ'
    return 'visible'


def _tool_ref_validation_issues(workspace: CaseEvidenceWorkspace, tool_call: OrchestratorAgentToolCall) -> list[dict[str, object]]:
    if tool_call.tool_name == 'propose_case_understanding':
        return []
    visible = workspace.to_dossier(round_context='orchestrator_tool_validation').visible_refs
    allowed_local = {
        *list(getattr(visible, 'local_file_refs', []) or []),
        *list(getattr(visible, 'local_cluster_refs', []) or []),
        *[str(getattr(card, 'ref', '') or '') for card in list(getattr(workspace, 'local_span_cards', []) or [])],
    }
    allowed_local_files = set(getattr(visible, 'local_file_refs', []) or [])
    allowed_local_groups = set(_local_main_file_group_index(workspace))
    allowed_local_spans = {
        str(getattr(card, 'ref', '') or '')
        for card in list(getattr(workspace, 'local_span_cards', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }
    allowed_query = set(getattr(visible, 'query_refs', []) or [])
    allowed_subject = set(getattr(visible, 'bangumi_subject_refs', []) or [])
    allowed_item = set(getattr(visible, 'bangumi_item_refs', []) or [])
    allowed_row = {
        str(getattr(row, 'row_ref', '') or '')
        for row in list(getattr(getattr(workspace, 'mapping_draft', None), 'rows', []) or [])
        if str(getattr(row, 'row_ref', '') or '')
    }
    allowed_plan_rows = {
        str(getattr(row, 'plan_row_ref', '') or '')
        for row in list(getattr(workspace.to_dossier(round_context='orchestrator_tool_validation'), 'recorded_split_plan_rows', []) or [])
        if str(getattr(row, 'plan_row_ref', '') or '')
    }
    allowed_span = {
        str(getattr(card, 'ref', '') or '')
        for card in list(getattr(workspace, 'local_span_cards', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }
    allowed_target = {
        *set(getattr(visible, 'target_refs', []) or []),
        *set(getattr(visible, 'bangumi_subject_refs', []) or []),
        *set(getattr(visible, 'bangumi_relation_refs', []) or []),
        *set(getattr(visible, 'bangumi_group_refs', []) or []),
        *set(getattr(visible, 'bangumi_item_refs', []) or []),
        *[str(getattr(card, 'ref', '') or '') for card in list(getattr(workspace, 'bangumi_span_cards', []) or [])],
    }
    allowed_assignment_target = {
        *allowed_item,
        *[str(getattr(card, 'ref', '') or '') for card in list(getattr(workspace, 'bangumi_span_cards', []) or [])],
    }
    args = tool_call.raw_arguments
    issues: list[dict[str, object]] = []

    def _ref_context_from_mapping(value: dict[str, object]) -> dict[str, object]:
        context: dict[str, object] = {}
        row_ref = str(value.get('row_ref') or '')
        local_ref = str(value.get('local_ref') or '')
        if row_ref:
            context['row_ref'] = row_ref
        if local_ref:
            context['local_ref'] = local_ref
        for key in ('plan_row_refs', 'local_refs', 'file_refs', 'span_refs'):
            refs = _dedupe_preserve_order([
                str(ref or '')
                for ref in list(value.get(key) or [])
                if str(ref or '')
            ])
            if refs:
                context[key] = refs[:12]
        return context

    def issue_for_ref(path: str, ref: str, expected: str, context: dict[str, object] | None = None) -> dict[str, object]:
        value = str(ref or '')
        prefix_match = re.match(r'^[A-Za-z]+', value)
        prefix = prefix_match.group(0) if prefix_match else ''
        correction = ''
        if prefix in {'LF', 'LS'} and ('item_refs' in path or 'chosen_item_ref' in path):
            correction = (
                'LF*/LS* are local refs. Put them in local_ref/local_refs/support_refs/source_refs; '
                'item_refs/chosen_item_ref must use visible BE* Bangumi item refs only.'
            )
        elif prefix == 'BS' and ('item_refs' in path or 'chosen_item_ref' in path):
            correction = (
                'BS* is a Bangumi subject ref. Put it in chosen_subject_ref/subject_refs/support_refs; '
                'item_refs/chosen_item_ref must use visible BE* item refs.'
            )
        elif prefix == 'BES' and ('item_refs' in path or 'chosen_item_ref' in path):
            correction = (
                'BES* is a Bangumi span ref. Put it in chosen_span_ref/target_refs/support_refs; '
                'item_refs/chosen_item_ref must use BE* refs.'
            )
        elif prefix in {'LF', 'LS'} and ('target_refs' in path or 'chosen_span_ref' in path):
            correction = (
                'LF*/LS* are local refs, not Bangumi targets. Use local_ref/local_refs for local rows; '
                'chosen_span_ref must be a visible BES* ref and target_refs must be visible Bangumi refs.'
            )
        elif prefix == 'RSP' and ('row_ref' in path or 'row_refs' in path):
            correction = (
                'RSP* is a recorded_split_plan row ref. Put it in plan_row_refs; '
                'row_ref/row_refs must use visible MDR* mapping draft row refs.'
            )
        else:
            article = '' if str(expected or '').startswith('visible ') else 'visible '
            correction = f'use a {article}{expected} ref for {path}, or move this ref to the correct field'
        context = dict(context or {})
        context_refs = _dedupe_preserve_order([
            str(context.get('row_ref') or ''),
            str(context.get('local_ref') or ''),
            *[str(ref or '') for ref in list(context.get('plan_row_refs') or [])],
            *[str(ref or '') for ref in list(context.get('local_refs') or [])],
            *[str(ref or '') for ref in list(context.get('file_refs') or [])],
            *[str(ref or '') for ref in list(context.get('span_refs') or [])],
        ])
        issue = {
            'issue': 'hidden_or_wrong_ref_namespace',
            'field': path,
            'ref': value,
            'ref_prefix': prefix,
            'expected_ref_namespace': expected,
            'correction': correction,
        }
        if context:
            issue['ref_context'] = context
        if context_refs:
            issue['context_refs'] = context_refs
        return issue

    def check_refs(key: str, allowed: set[str]) -> None:
        for ref in list(args.get(key) or []):
            value = str(ref or '')
            if value and value not in allowed:
                if (
                    tool_call.tool_name == 'execute_evidence'
                    and key == 'subject_refs'
                    and re.match(r'^BS\d+$', value)
                ):
                    continue
                issues.append(issue_for_ref(key, value, _expected_namespace_for_ref_field(key)))

    def check_ref_value(key: str, value: object, allowed: set[str], context: dict[str, object] | None = None) -> None:
        ref = str(value or '')
        if ref and ref not in allowed:
            if (
                tool_call.tool_name == 'execute_evidence'
                and 'subject_refs' in key
                and re.match(r'^BS\d+$', ref)
            ):
                return
            issues.append(issue_for_ref(key, ref, _expected_namespace_for_ref_field(key), context))

    def walk_nested(value: object, prefix: str = '', context: dict[str, object] | None = None) -> None:
        if isinstance(value, dict):
            current_context = dict(context or {})
            current_context.update(_ref_context_from_mapping(value))
            for key, item in value.items():
                path = f'{prefix}.{key}' if prefix else str(key)
                if tool_call.tool_name == 'split_into_child_cases' and key in {'main_file_refs', 'supplemental_file_refs'} and isinstance(item, list):
                    for ref in item:
                        check_ref_value(path, ref, allowed_local, current_context)
                elif tool_call.tool_name == 'split_into_child_cases' and key in {'main_group_refs', 'supplemental_group_refs'} and isinstance(item, list):
                    for ref in item:
                        check_ref_value(path, ref, allowed_local_groups, current_context)
                elif tool_call.tool_name == 'split_into_child_cases' and key == 'support_refs' and isinstance(item, list):
                    for ref in item:
                        check_ref_value(path, ref, allowed_local | allowed_local_groups | allowed_target | allowed_query, current_context)
                elif key == 'file_refs' and isinstance(item, list):
                    for ref in item:
                        check_ref_value(path, ref, allowed_local, current_context)
                elif key == 'span_refs' and isinstance(item, list):
                    for ref in item:
                        check_ref_value(path, ref, allowed_span, current_context)
                elif key in {'local_ref'}:
                    check_ref_value(path, item, allowed_local, current_context)
                elif key in {'row_ref'}:
                    check_ref_value(path, item, allowed_row, current_context)
                elif key == 'plan_row_refs' and isinstance(item, list):
                    for ref in item:
                        check_ref_value(path, ref, allowed_plan_rows, current_context)
                elif key == 'file_refs' and isinstance(item, list):
                    for ref in item:
                        check_ref_value(path, ref, allowed_local_files, current_context)
                elif key == 'span_refs' and isinstance(item, list):
                    for ref in item:
                        check_ref_value(path, ref, allowed_local_spans, current_context)
                elif key in {'chosen_subject_ref'}:
                    check_ref_value(path, item, allowed_subject, current_context)
                elif key in {'chosen_item_ref'}:
                    check_ref_value(path, item, allowed_item, current_context)
                elif key in {'chosen_span_ref'}:
                    check_ref_value(path, item, allowed_assignment_target, current_context)
                elif key == 'candidate_target_refs' and isinstance(item, list):
                    for ref in item:
                        check_ref_value(path, ref, allowed_subject | allowed_assignment_target, current_context)
                elif key in {'target_ref', 'target_span_ref', 'left_ref', 'right_ref', 'winner_ref'}:
                    check_ref_value(path, item, allowed_target | allowed_local, current_context)
                elif key in {'local_refs', 'source_refs', 'support_refs'} and isinstance(item, list):
                    for ref in item:
                        check_ref_value(path, ref, allowed_local | allowed_local_groups | allowed_target | allowed_query, current_context)
                elif key == 'query_refs' and isinstance(item, list):
                    for ref in item:
                        check_ref_value(path, ref, allowed_query, current_context)
                elif key == 'subject_refs' and isinstance(item, list):
                    subject_allowed = allowed_subject | allowed_target if tool_call.tool_name == 'execute_evidence' else allowed_subject
                    for ref in item:
                        check_ref_value(path, ref, subject_allowed, current_context)
                elif key == 'item_refs' and isinstance(item, list):
                    for ref in item:
                        check_ref_value(path, ref, allowed_item, current_context)
                else:
                    walk_nested(item, path, current_context)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk_nested(item, f'{prefix}[{index}]', context)

    check_refs('local_refs', allowed_local)
    check_refs('query_refs', allowed_query)
    check_refs('subject_refs', allowed_subject | allowed_target if tool_call.tool_name == 'execute_evidence' else allowed_subject)
    check_refs('item_refs', allowed_item)
    check_refs('target_refs', allowed_target)
    check_refs('row_refs', allowed_row)
    check_refs('plan_row_refs', allowed_plan_rows)
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
    explicit_subject_refs = _subject_refs_from_evidence_tool_args(args, workspace)
    if not explicit_subject_refs and agenda_subject_refs:
        explicit_subject_refs = agenda_subject_refs
    if not selected_ids and not request_types:
        has_visible_items = bool(list(getattr(workspace, 'bangumi_items', []) or []) or list(getattr(workspace, 'bangumi_span_cards', []) or []))
        if list(getattr(workspace, 'bangumi_subjects', []) or []) and not has_visible_items:
            request_types = ['episode_list', 'subject_lookup', 'related_expansion']
        elif not list(getattr(workspace, 'bangumi_subjects', []) or []):
            request_types = ['subject_search']
    summaries, registry, augmented_request_ids = _augment_menu_with_agent_subject_requests(
        summaries,
        registry,
        subject_refs=explicit_subject_refs,
        request_types=request_types,
    )
    selected_ids = list(dict.fromkeys(selected_ids))
    selected_ids, stale_ids = _filter_stale_menu_request_ids(workspace, selected_ids)
    if not selected_ids and request_types:
        requested_subject_refs = set(explicit_subject_refs)
        for summary in summaries:
            request_id = str(summary.get('request_id') or '')
            request_type = str(summary.get('request_type') or '')
            source_refs = set(_request_summary_source_refs(summary))
            if request_id and _request_type_matches_requested(request_type, request_types) and (not requested_subject_refs or requested_subject_refs & source_refs):
                selected_ids.append(request_id)
    selected_ids = list(dict.fromkeys(selected_ids))
    unknown_ids = [request_id for request_id in selected_ids if request_id not in registry]
    selected_ids = [request_id for request_id in selected_ids if request_id in registry]
    selected_ids, post_registry_stale_ids = _filter_stale_menu_request_ids(workspace, selected_ids)
    stale_ids = _dedupe_preserve_order([*stale_ids, *post_registry_stale_ids])
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


def _finish_outcome_kind_for_draft_row(row: MappingDraftRow | None) -> str:
    if row is None:
        return ''
    disposition = str(getattr(row, 'disposition', '') or '')
    reason_kind = str(getattr(row, 'reason_kind', '') or '')
    if disposition == 'map_to_bangumi':
        return 'mapped'
    if disposition == 'non_bangumi_or_supplemental':
        return 'target_absent' if reason_kind == 'bangumi_target_absent' else 'supplemental'
    if disposition == 'unaligned_fail_closed':
        return 'fail_closed'
    return 'open'


def _draft_row_for_ledger_row_for_agent(
    workspace: CaseEvidenceWorkspace,
    row,
    *,
    draft: MappingDraft | None = None,
) -> MappingDraftRow | None:
    draft = draft or getattr(workspace, 'mapping_draft', None)
    if draft is None:
        return None
    row_ref = str(getattr(row, 'row_ref', '') or '')
    local_ref = str(getattr(row, 'local_ref', '') or '')
    draft_rows = list(getattr(draft, 'rows', []) or [])
    if row_ref:
        for draft_row in draft_rows:
            if str(getattr(draft_row, 'row_ref', '') or '') == row_ref:
                return draft_row
    if local_ref:
        for draft_row in draft_rows:
            if str(getattr(draft_row, 'local_ref', '') or '') == local_ref:
                return draft_row
    ledger_files = set(_ledger_row_file_refs_for_agent(workspace, row, draft=draft))
    if ledger_files:
        dossier = workspace.to_dossier(round_context='ledger_row_draft_projection_for_agent')
        matches = [
            draft_row for draft_row in draft_rows
            if set(main_file_refs_for_mapping_row(dossier, draft_row)) == ledger_files
        ]
        if len(matches) == 1:
            return matches[0]
    return None


def _finish_review_template_for_agent(workspace: CaseEvidenceWorkspace, accounting) -> dict[str, object]:
    draft = getattr(workspace, 'mapping_draft', None)
    draft_rows = list(getattr(draft, 'rows', []) or []) if draft is not None else []
    visible_refs = set(workspace.all_visible_ref_set())

    review_rows: list[dict[str, object]] = []
    for row in draft_rows:
        local_ref = str(getattr(row, 'local_ref', '') or '')
        row_ref = str(getattr(row, 'row_ref', '') or '')
        file_refs = _local_file_refs_for_understanding_ref(workspace, local_ref)
        support_refs = _dedupe_preserve_order([
            ref
            for ref in [
                local_ref,
                *[str(value or '') for value in list(getattr(row, 'support_refs', []) or [])],
                str(getattr(row, 'selected_target_ref', '') or ''),
            ]
            if ref and ref in visible_refs
        ])
        if not support_refs and local_ref:
            support_refs = [local_ref]
        review_rows.append({
            'row_ref': row_ref,
            'local_ref': local_ref,
            'outcome_kind': _finish_outcome_kind_for_draft_row(row),
            'file_count': len(file_refs),
            'support_refs': support_refs,
            'reason': (
                'Review this row against the current draft/accounting. Keep or revise outcome_kind according to '
                'your semantic conclusion; the fixed layer only checks refs and counts.'
            ),
        })
    return {
        'status': 'accepted',
        'finish_kind': 'accepted',
        'reviewed_outcome_projection': True,
        'acknowledged_mapped_file_count': int(getattr(accounting, 'mapped_file_count', 0) or 0),
        'acknowledged_excluded_file_count': int(getattr(accounting, 'excluded_file_count', 0) or 0),
        'acknowledged_open_file_count': int(getattr(accounting, 'open_file_count', 0) or 0),
        'acknowledged_unresolved_count': int(getattr(accounting, 'unresolved_count', 0) or 0),
        'work_unit_reviews': review_rows,
        'final_case_review': (
            'Replace this with your whole-case review. If these mapped/excluded counts do not match your semantic '
            'conclusion, do not finish; revise ledger, mapping intents, split, or evidence first.'
        ),
    }


def _ledger_row_file_refs_for_agent(
    workspace: CaseEvidenceWorkspace,
    row,
    *,
    draft: MappingDraft | None = None,
) -> list[str]:
    draft = draft or getattr(workspace, 'mapping_draft', None)
    dossier = workspace.to_dossier(round_context='ledger_row_file_refs_for_agent')
    if draft is not None:
        row_ref = str(getattr(row, 'row_ref', '') or '')
        local_ref = str(getattr(row, 'local_ref', '') or '')
        for draft_row in list(getattr(draft, 'rows', []) or []):
            if row_ref and str(getattr(draft_row, 'row_ref', '') or '') == row_ref:
                return main_file_refs_for_mapping_row(dossier, draft_row)
            if local_ref and str(getattr(draft_row, 'local_ref', '') or '') == local_ref:
                return main_file_refs_for_mapping_row(dossier, draft_row)
    span_file_refs = {
        str(getattr(span, 'ref', '') or ''): [
            str(ref or '')
            for ref in list(getattr(span, 'file_refs', []) or [])
            if str(ref or '')
        ]
        for span in list(getattr(workspace, 'local_span_cards', []) or [])
        if str(getattr(span, 'ref', '') or '')
    }
    file_refs: list[str] = []
    for ref in _dedupe_preserve_order([
        str(getattr(row, 'local_ref', '') or ''),
        *[str(ref or '') for ref in list(getattr(row, 'local_refs', []) or [])],
        *[str(ref or '') for ref in list(getattr(row, 'file_refs', []) or [])],
        *[str(ref or '') for ref in list(getattr(row, 'span_refs', []) or [])],
    ]):
        if ref.startswith('LF'):
            file_refs.append(ref)
        elif ref in span_file_refs:
            file_refs.extend(span_file_refs.get(ref, []))
    main_refs = {
        str(ref or '')
        for ref in list(getattr(getattr(workspace, 'contract', None), 'main_file_refs', []) or [])
        if str(ref or '')
    }
    return [
        ref for ref in _dedupe_preserve_order(file_refs)
        if not main_refs or ref in main_refs
    ]


def _case_resolution_ledger_unresolved_rows_for_agent(
    workspace: CaseEvidenceWorkspace,
    *,
    limit: int = 16,
) -> list[dict[str, object]]:
    ledger = getattr(workspace, 'case_resolution_ledger', None)
    if ledger is None:
        return []
    draft = getattr(workspace, 'mapping_draft', None)
    latest_agenda = _latest_blocked_evidence_agenda_for_agent(workspace)
    agenda_rows = [
        item for item in list(latest_agenda.get('blocked_rows') or [])
        if isinstance(item, dict)
    ]
    file_by_ref = {
        str(getattr(card, 'ref', '') or ''): card
        for card in list(getattr(workspace, 'local_files', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }
    result: list[dict[str, object]] = []
    for row in list(getattr(ledger, 'rows', []) or []):
        outcome = str(getattr(row, 'outcome', '') or '')
        if outcome not in {'needs_evidence', 'split_needed', 'fail_blocker'}:
            continue
        ledger_row_ref = str(getattr(row, 'ledger_row_ref', '') or '')
        row_ref = str(getattr(row, 'row_ref', '') or '')
        local_ref = str(getattr(row, 'local_ref', '') or '')
        file_refs = _ledger_row_file_refs_for_agent(workspace, row, draft=draft)
        draft_row = _draft_row_for_ledger_row_for_agent(workspace, row, draft=draft)
        current_draft_outcome_kind = _finish_outcome_kind_for_draft_row(draft_row)
        current_draft_support_refs = [
            str(ref or '')
            for ref in list(getattr(draft_row, 'support_refs', []) or [])[:12]
            if str(ref or '')
        ] if draft_row is not None else []
        current_draft_selected_target_ref = str(getattr(draft_row, 'selected_target_ref', '') or '') if draft_row is not None else ''
        ledger_stale_against_terminal_draft = bool(
            current_draft_outcome_kind in {'mapped', 'target_absent', 'supplemental', 'fail_closed'}
        )
        matching_agenda_rows = [
            item for item in agenda_rows
            if (
                (ledger_row_ref and str(item.get('ledger_row_ref') or '') == ledger_row_ref)
                or (row_ref and str(item.get('row_ref') or '') == row_ref)
                or (local_ref and str(item.get('local_ref') or '') == local_ref)
            )
        ]
        result.append({
            'ledger_row_ref': ledger_row_ref,
            'row_ref': row_ref,
            'local_ref': local_ref,
            'outcome': outcome,
            'role': str(getattr(row, 'role', '') or ''),
            'file_count': len(file_refs),
            'file_ref_samples': file_refs[:12],
            'file_label_samples': [
                str(getattr(file_by_ref.get(ref), 'label', '') or getattr(file_by_ref.get(ref), 'path', '') or ref)
                for ref in file_refs[:8]
            ],
            'requested_request_types': [str(value or '') for value in list(getattr(row, 'requested_request_types', []) or []) if str(value or '')],
            'query_hints': [str(value or '') for value in list(getattr(row, 'query_hints', []) or []) if str(value or '')][:8],
            'subject_refs': [str(value or '') for value in list(getattr(row, 'subject_refs', []) or []) if str(value or '')][:8],
            'item_refs': [str(value or '') for value in list(getattr(row, 'item_refs', []) or []) if str(value or '')][:8],
            'target_refs': [str(value or '') for value in list(getattr(row, 'target_refs', []) or []) if str(value or '')][:8],
            'support_refs': [str(value or '') for value in list(getattr(row, 'support_refs', []) or []) if str(value or '')][:8],
            'current_draft_row_ref': str(getattr(draft_row, 'row_ref', '') or '') if draft_row is not None else '',
            'current_draft_local_ref': str(getattr(draft_row, 'local_ref', '') or '') if draft_row is not None else '',
            'current_draft_outcome_kind': current_draft_outcome_kind,
            'current_draft_disposition': str(getattr(draft_row, 'disposition', '') or '') if draft_row is not None else '',
            'current_draft_reason_kind': str(getattr(draft_row, 'reason_kind', '') or '') if draft_row is not None else '',
            'current_draft_selected_target_ref': current_draft_selected_target_ref,
            'current_draft_support_refs': current_draft_support_refs,
            'ledger_stale_against_terminal_draft': ledger_stale_against_terminal_draft,
            'ledger_sync_protocol': (
                'If current_draft_outcome_kind matches your current semantic conclusion, revise this ledger row to the same terminal outcome. '
                'If it does not, revise mapping intents, request evidence, split, or use a legal fail_closed basis.'
            ) if ledger_stale_against_terminal_draft else (
                'This ledger row is still unresolved and the matching draft row is not terminal; provide evidence, mapping intent, split, or fail_closed basis.'
            ),
            'matching_executable_request_ids': _dedupe_preserve_order([
                str(request_id or '')
                for item in matching_agenda_rows
                for request_id in list(item.get('matching_executable_request_ids') or [])
                if str(request_id or '')
            ])[:12],
            'matching_executable_request_types': _dedupe_preserve_order([
                str(request_type or '')
                for item in matching_agenda_rows
                for request_type in list(item.get('matching_executable_request_types') or [])
                if str(request_type or '')
            ])[:12],
            'reason_kind': str(getattr(row, 'reason_kind', '') or ''),
            'reason': str(getattr(row, 'reason', '') or '')[:360],
        })
        if len(result) >= limit:
            break
    return result


def _ledger_row_update_from_terminal_patch(
    row,
    patch: MappingDraftPatch,
) -> dict[str, object]:
    op = str(getattr(patch, 'op', '') or '')
    support_refs = _dedupe_preserve_order([
        *[str(ref or '') for ref in list(getattr(row, 'support_refs', []) or [])],
        *[str(ref or '') for ref in list(getattr(patch, 'support_refs', []) or [])],
    ])
    common: dict[str, object] = {
        'support_refs': support_refs,
        'reason': str(getattr(patch, 'reason', '') or getattr(row, 'reason', '') or ''),
        'reason_kind': str(getattr(patch, 'reason_kind', '') or getattr(row, 'reason_kind', '') or ''),
    }
    if op == 'map_to_bangumi':
        target_ref = str(getattr(patch, 'target_ref', '') or getattr(patch, 'target_span_ref', '') or '')
        item_refs = [str(ref or '') for ref in list(getattr(patch, 'item_refs', []) or []) if str(ref or '')]
        target_refs = _dedupe_preserve_order([
            *[str(ref or '') for ref in list(getattr(row, 'target_refs', []) or [])],
            target_ref,
            *item_refs,
        ])
        chosen_item_ref = target_ref if target_ref.startswith('BE') else str(getattr(row, 'chosen_item_ref', '') or '')
        chosen_span_ref = target_ref if target_ref.startswith('BES') else str(getattr(row, 'chosen_span_ref', '') or '')
        return {
            **common,
            'outcome': 'map_to_bangumi',
            'chosen_subject_ref': (
                [str(ref or '') for ref in list(getattr(patch, 'subject_refs', []) or []) if str(ref or '')]
                or [str(getattr(row, 'chosen_subject_ref', '') or '')]
            )[0],
            'chosen_item_ref': chosen_item_ref,
            'chosen_span_ref': chosen_span_ref,
            'mapping_mode': str(getattr(patch, 'mapping_mode', '') or getattr(row, 'mapping_mode', '') or 'unresolved'),
            'item_refs': _dedupe_preserve_order([*list(getattr(row, 'item_refs', []) or []), *item_refs]),
            'target_refs': target_refs,
            'requested_request_types': [],
        }
    if op == 'mark_non_bangumi_or_supplemental':
        reason_kind = str(getattr(patch, 'reason_kind', '') or getattr(row, 'reason_kind', '') or '')
        return {
            **common,
            'outcome': 'target_absent' if reason_kind == 'bangumi_target_absent' else 'supplemental',
            'reason_kind': reason_kind,
            'requested_request_types': [],
        }
    if op == 'mark_unaligned_fail_closed':
        return {
            **common,
            'outcome': 'fail_blocker',
            'requested_request_types': [],
        }
    return {}


def _workspace_with_case_resolution_ledger_synced_from_patches(
    workspace: CaseEvidenceWorkspace,
    patches: list[MappingDraftPatch],
) -> tuple[CaseEvidenceWorkspace, list[dict[str, object]]]:
    ledger = getattr(workspace, 'case_resolution_ledger', None)
    if ledger is None or not list(getattr(ledger, 'rows', []) or []):
        return workspace, []
    terminal_patches = [
        patch for patch in list(patches or [])
        if str(getattr(patch, 'op', '') or '') in {
            'map_to_bangumi',
            'mark_non_bangumi_or_supplemental',
            'mark_unaligned_fail_closed',
        }
    ]
    if not terminal_patches:
        return workspace, []
    patch_by_local = {
        str(getattr(patch, 'local_ref', '') or ''): patch
        for patch in terminal_patches
        if str(getattr(patch, 'local_ref', '') or '')
    }
    if not patch_by_local:
        return workspace, []
    draft = getattr(workspace, 'mapping_draft', None)
    synced_rows = []
    sync_updates: list[dict[str, object]] = []
    for row in list(getattr(ledger, 'rows', []) or []):
        draft_row = _draft_row_for_ledger_row_for_agent(workspace, row, draft=draft)
        local_ref = str(getattr(draft_row, 'local_ref', '') or getattr(row, 'local_ref', '') or '')
        patch = patch_by_local.get(local_ref)
        if patch is None:
            synced_rows.append(row)
            continue
        update = _ledger_row_update_from_terminal_patch(row, patch)
        if not update:
            synced_rows.append(row)
            continue
        before_outcome = str(getattr(row, 'outcome', '') or '')
        updated_row = row.model_copy(update=update)
        synced_rows.append(updated_row)
        sync_updates.append({
            'ledger_row_ref': str(getattr(row, 'ledger_row_ref', '') or ''),
            'row_ref': str(getattr(row, 'row_ref', '') or getattr(draft_row, 'row_ref', '') or ''),
            'local_ref': local_ref,
            'before_outcome': before_outcome,
            'after_outcome': str(getattr(updated_row, 'outcome', '') or ''),
            'source_patch_op': str(getattr(patch, 'op', '') or ''),
        })
    if not sync_updates:
        return workspace, []
    updated_ledger = ledger.model_copy(update={
        'rows': synced_rows,
        'version': int(getattr(ledger, 'version', 0) or 0) + 1,
    })
    workspace = _workspace_preserving_state(workspace, case_resolution_ledger=updated_ledger)
    workspace = _workspace_with_judge_audit(workspace, {
        'note': 'orchestrator_case_resolution_ledger_synced_from_mapping_intents',
        'synced_row_count': len(sync_updates),
        'synced_rows': sync_updates[:24],
        'reason': (
            'mechanical sync from Agent-authored terminal mapping intents to the Agent-authored case_resolution_ledger; '
            'the fixed layer did not choose semantic outcomes'
        ),
    })
    return workspace, sync_updates


def _finish_case_review_rejection_count(workspace: CaseEvidenceWorkspace) -> int:
    count = 0
    for audit in reversed(list(getattr(workspace, 'judge_request_audits', []) or [])):
        if not isinstance(audit, dict):
            continue
        if (
            str(audit.get('note') or '') == 'orchestrator_tool_selected'
            and str(audit.get('tool_name') or '') == 'finish_case'
            and not bool(audit.get('accepted'))
            and str(audit.get('reason') or '') == 'finish_case_review_required'
        ):
            count += 1
            continue
        if str(audit.get('note') or '') in {'orchestrator_agent_session_summary'}:
            continue
        break
    return count


def _accepted_finish_review_issues(
    workspace: CaseEvidenceWorkspace,
    args: FinishCaseToolArgs,
    accounting,
) -> list[dict[str, object]]:
    issues: list[dict[str, object]] = []
    if not bool(getattr(args, 'reviewed_outcome_projection', False)):
        issues.append({
            'issue_code': 'finish_review_projection_not_acknowledged',
            'message': 'accepted finish requires reviewed_outcome_projection=true after inspecting global_outcome_projection',
        })
    expected_counts = {
        'acknowledged_mapped_file_count': int(getattr(accounting, 'mapped_file_count', 0) or 0),
        'acknowledged_excluded_file_count': int(getattr(accounting, 'excluded_file_count', 0) or 0),
        'acknowledged_open_file_count': int(getattr(accounting, 'open_file_count', 0) or 0),
        'acknowledged_unresolved_count': int(getattr(accounting, 'unresolved_count', 0) or 0),
    }
    for field_name, expected in expected_counts.items():
        actual = getattr(args, field_name, None)
        if actual is None:
            issues.append({
                'issue_code': f'{field_name}_missing',
                'message': f'accepted finish requires {field_name}={expected}',
                'expected': expected,
            })
            continue
        try:
            actual_int = int(actual)
        except (TypeError, ValueError):
            actual_int = -1
        if actual_int != expected:
            issues.append({
                'issue_code': f'{field_name}_mismatch',
                'message': f'{field_name} must match current mapping draft accounting',
                'expected': expected,
                'actual': actual,
            })
    if not str(getattr(args, 'final_case_review', '') or '').strip():
        issues.append({
            'issue_code': 'finish_review_missing_final_case_review',
            'message': 'accepted finish requires final_case_review summarizing the agent whole-case review',
        })
    draft = getattr(workspace, 'mapping_draft', None)
    draft_rows = list(getattr(draft, 'rows', []) or []) if draft is not None else []
    required_row_keys = {
        str(getattr(row, 'row_ref', '') or '') or str(getattr(row, 'local_ref', '') or '')
        for row in draft_rows
    }
    required_local_refs = {
        str(getattr(row, 'local_ref', '') or '')
        for row in draft_rows
        if str(getattr(row, 'local_ref', '') or '')
    }
    review_rows = list(getattr(args, 'work_unit_reviews', []) or [])
    visible_refs = set(workspace.all_visible_ref_set())
    reviewed_keys = {
        key
        for review in review_rows
        for key in (
            str(getattr(review, 'row_ref', '') or ''),
            str(getattr(review, 'local_ref', '') or ''),
        )
        if key
    }
    missing_rows = sorted([
        str(getattr(row, 'row_ref', '') or getattr(row, 'local_ref', '') or '')
        for row in draft_rows
        if str(getattr(row, 'row_ref', '') or '') not in reviewed_keys
        and str(getattr(row, 'local_ref', '') or '') not in reviewed_keys
    ])
    if draft_rows and missing_rows:
        issues.append({
            'issue_code': 'finish_review_missing_work_unit_reviews',
            'message': 'accepted finish requires one work_unit_reviews entry for each current mapping draft row',
            'missing_row_refs': missing_rows[:24],
            'required_row_count': len(draft_rows),
            'reviewed_row_count': len(review_rows),
        })
    for review in review_rows:
        row_ref = str(getattr(review, 'row_ref', '') or '')
        local_ref = str(getattr(review, 'local_ref', '') or '')
        if row_ref and row_ref not in required_row_keys:
            issues.append({
                'issue_code': 'finish_review_unknown_row_ref',
                'message': 'work_unit_reviews.row_ref must cite a current mapping draft row',
                'ref': row_ref,
            })
        if local_ref and local_ref not in required_local_refs:
            issues.append({
                'issue_code': 'finish_review_unknown_local_ref',
                'message': 'work_unit_reviews.local_ref must cite a current mapping draft row local_ref',
                'ref': local_ref,
            })
        if int(getattr(review, 'file_count', 0) or 0) < 0:
            issues.append({
                'issue_code': 'finish_review_invalid_file_count',
                'message': 'work_unit_reviews.file_count must be non-negative',
                'ref': row_ref or local_ref,
            })
        support_refs = [str(ref or '') for ref in list(getattr(review, 'support_refs', []) or []) if str(ref or '')]
        if not support_refs:
            issues.append({
                'issue_code': 'finish_review_missing_support_refs',
                'message': 'work_unit_reviews.support_refs must cite visible evidence refs for the reviewed outcome',
                'ref': row_ref or local_ref,
            })
        unknown_support_refs = [ref for ref in support_refs if ref not in visible_refs]
        if unknown_support_refs:
            issues.append({
                'issue_code': 'finish_review_unknown_support_ref',
                'message': 'work_unit_reviews.support_refs must cite visible LF/LS/BS/BE/BES/QC refs',
                'ref': row_ref or local_ref,
                'unknown_refs': unknown_support_refs[:8],
            })

    main_file_count = int(getattr(accounting, 'main_file_count', 0) or 0)
    mapped_file_count = int(getattr(accounting, 'mapped_file_count', 0) or 0)
    excluded_file_count = int(getattr(accounting, 'excluded_file_count', 0) or 0)
    work_unit_count = len(list(getattr(getattr(workspace, 'case_briefing', None), 'work_units', []) or []))
    draft_row_count = len(draft_rows)
    split_plan_seen = any(
        isinstance(audit, dict)
        and audit.get('note') == 'orchestrator_selected_child_cases_result'
        for audit in list(getattr(workspace, 'judge_request_audits', []) or [])
    )
    complex_all_or_mostly_unmapped = bool(
        main_file_count > 0
        and excluded_file_count > 0
        and (
            (
                mapped_file_count == 0
                and (main_file_count > 2 or draft_row_count > 1 or work_unit_count > 1 or split_plan_seen)
            )
            or (
                main_file_count >= 10
                and excluded_file_count / max(1, main_file_count) >= 0.75
            )
        )
    )
    ledger = getattr(workspace, 'case_resolution_ledger', None)
    ledger_rows = list(getattr(ledger, 'rows', []) or []) if ledger is not None else []
    if complex_all_or_mostly_unmapped:
        if ledger is None or not ledger_rows:
            issues.append({
                'issue_code': 'finish_review_large_unmapped_projection_requires_ledger',
                'message': (
                    'accepted finish with all/mostly unmapped main files in a multi-file or multi-row package '
                    'requires an explicit case_resolution_ledger covering the package outcome. This is a mechanical '
                    'provenance check; the fixed layer does not choose targets or decide target_absent semantics.'
                ),
                'main_file_count': main_file_count,
                'mapped_file_count': mapped_file_count,
                'excluded_file_count': excluded_file_count,
                'draft_row_count': draft_row_count,
                'work_unit_count': work_unit_count,
            })
        else:
            ledger_validation = validate_case_resolution_ledger(
                workspace.to_dossier(round_context='finish_case_large_unmapped_review'),
                draft,
                ledger,
            )
            if ledger_validation:
                issues.append({
                    'issue_code': 'finish_review_case_resolution_ledger_invalid',
                    'message': 'accepted finish requires a valid case_resolution_ledger when most main files are unmapped',
                    'ledger_issue_codes': _dedupe_preserve_order([
                        str(getattr(issue, 'issue_code', '') or '')
                        for issue in ledger_validation
                    ]),
                })
    ledger_requires_terminal_review = bool(
        ledger_rows
        and (
            main_file_count >= 10
            or draft_row_count > 1
            or work_unit_count > 1
            or split_plan_seen
        )
    )
    unresolved_ledger_outcomes = _dedupe_preserve_order([
        str(getattr(row, 'outcome', '') or '')
        for row in ledger_rows
        if str(getattr(row, 'outcome', '') or '') in {'needs_evidence', 'split_needed', 'fail_blocker'}
    ])
    if ledger_requires_terminal_review and unresolved_ledger_outcomes and not any(
        issue.get('issue_code') == 'finish_review_unresolved_ledger_outcomes'
        for issue in issues
    ):
        unresolved_rows = _case_resolution_ledger_unresolved_rows_for_agent(workspace, limit=24)
        issues.append({
            'issue_code': 'finish_review_unresolved_ledger_outcomes',
            'message': (
                'accepted finish for a multi-row or large package cannot leave the Agent-authored '
                'case_resolution_ledger with needs_evidence/split_needed/fail_blocker outcomes'
            ),
            'ledger_outcomes': unresolved_ledger_outcomes,
            'unresolved_case_resolution_ledger_rows': unresolved_rows,
        })
    return issues


def _decision_from_orchestrator_tool_call(
    workspace: CaseEvidenceWorkspace,
    tool_call: OrchestratorAgentToolCall,
    *,
    enforce_available_tools: bool = True,
) -> tuple[CaseEvidenceWorkspace, _InvestigationDecision | None, dict[str, object]]:
    if _case_understanding_applied(workspace):
        workspace = _refresh_mapping_draft_candidates(_workspace_with_initial_mapping_draft(workspace))
    tool_name = tool_call.tool_name
    allowed_tool_names = _allowed_tool_names_for_workspace(workspace)
    if enforce_available_tools and tool_name not in allowed_tool_names:
        return workspace, None, {
            'accepted': False,
            'reason': 'unknown_public_tool',
            'tool_name': tool_name,
            'available_tool_names': sorted(allowed_tool_names),
            **_mapping_draft_observation(workspace),
            'finish_gate': _finish_gate_observation(workspace),
            'executable_menu_summary': _executable_menu_observation(workspace),
            'recommended_next_observation': 'Choose one of the public tool names. Fixed-layer rejection here is for unknown tool names, not semantic phase ordering.',
        }
    ref_issues = _tool_ref_validation_issues(workspace, tool_call)
    if ref_issues:
        issue_codes = _dedupe_preserve_order([
            str(issue.get('issue') or '')
            for issue in ref_issues
            if isinstance(issue, dict) and str(issue.get('issue') or '')
        ])
        issue_refs = _dedupe_preserve_order([
            str(issue.get('ref') or '')
            for issue in ref_issues
            if isinstance(issue, dict) and str(issue.get('ref') or '')
        ])
        issue_context_refs = _dedupe_preserve_order([
            str(ref or '')
            for issue in ref_issues
            if isinstance(issue, dict)
            for ref in list(issue.get('context_refs') or [])
            if str(ref or '')
        ])
        correction_samples = _dedupe_preserve_order([
            str(issue.get('correction') or '')
            for issue in ref_issues
            if isinstance(issue, dict) and str(issue.get('correction') or '')
        ])[:4]
        return workspace, None, {
            'accepted': False,
            'reason': 'hidden_or_unknown_refs',
            'ref_issues': ref_issues[:64],
            'ref_issue_count': len(ref_issues),
            'ref_issue_codes': issue_codes,
            'ref_issue_refs': issue_refs[:64],
            'ref_issue_context_refs': issue_context_refs[:64],
            'ref_corrections': correction_samples,
            **_mapping_draft_observation(workspace),
            'finish_gate': _finish_gate_observation(workspace),
            'executable_menu_summary': _executable_menu_observation(workspace),
            'latest_blocked_evidence_agenda': _latest_blocked_evidence_agenda_for_agent(workspace),
            'work_unit_resolution_board_focus': _work_unit_resolution_board_focus_for_agent(
                workspace,
                [*issue_refs, *issue_context_refs],
            ),
            'recommended_next_observation': (
                'Repair the rejected tool call using the ref namespace corrections. '
                'Do not repeat the same arguments. In particular, LF*/LS* are local refs; '
                'BS* are subject refs; item_refs/chosen_item_ref must be visible BE* Bangumi item refs. '
                'If latest_blocked_evidence_agenda exposes matching executable request ids, execute those ids instead of guessing BE refs.'
            ),
        }
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
            'reason': 'finish_case_preconditions_not_met',
            'tool_name': tool_name,
            **_mapping_draft_observation(workspace),
            'finish_gate': finish_gate,
            'executable_menu_summary': _executable_menu_observation(workspace),
            'recommended_next_observation': 'finish_case is a public capability, but this finish request is rejected because accounting/fail_closed preconditions are not met. Continue with evidence or mapping intents, or provide a legal fail_closed basis.',
        }
    reason = str(getattr(tool_call.arguments, 'reason', '') or tool_name)
    if tool_name == 'propose_case_understanding':
        return workspace, _InvestigationDecision(action='propose_case_understanding', reason=reason), {'accepted': True}
    if tool_name == 'materialize_queries':
        return workspace, _InvestigationDecision(action='compose_queries', reason=reason), {'accepted': True}
    if tool_name == 'propose_mapping_intents':
        args = tool_call.arguments
        if not isinstance(args, ProposeMappingIntentsToolArgs):
            return workspace, None, {'accepted': False, 'reason': 'wrong_tool_args'}
        return workspace, _InvestigationDecision(action='propose_mapping_intents', reason=reason), {'accepted': True}
    if tool_name == 'propose_case_resolution_ledger':
        args = tool_call.arguments
        if not isinstance(args, ProposeCaseResolutionLedgerToolArgs):
            return workspace, None, {'accepted': False, 'reason': 'wrong_tool_args'}
        return workspace, _InvestigationDecision(action='propose_case_resolution_ledger', reason=reason), {
            'accepted': True,
            'ledger_row_count': len(list(getattr(args, 'ledger_rows', []) or [])),
        }
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
        raw_args = tool_call.raw_arguments if isinstance(tool_call.raw_arguments, dict) else {}
        raw_finish_kind = raw_args.get('finish_kind')
        raw_finish_kind_present = 'finish_kind' in raw_args and str(raw_finish_kind or '').strip() != ''
        finish_kind = str(getattr(args, 'finish_kind', '') or '')
        finish_kind_defaulted_to_accepted = False
        if status == 'accepted' and finish_kind != 'accepted' and not raw_finish_kind_present:
            finish_kind = 'accepted'
            finish_kind_defaulted_to_accepted = True
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
        if status == 'accepted':
            draft = getattr(workspace, 'mapping_draft', None)
            accounting = compute_mapping_draft_accounting(draft, workspace.to_dossier(round_context='finish_case_review_gate')) if draft is not None else None
            review_issues = _accepted_finish_review_issues(workspace, args, accounting) if accounting is not None else [{
                'issue_code': 'finish_review_no_mapping_draft',
                'message': 'accepted finish requires a current mapping draft',
            }]
            if review_issues:
                unresolved_ledger_rows = _case_resolution_ledger_unresolved_rows_for_agent(workspace, limit=24)
                unresolved_refs = _dedupe_preserve_order([
                    str(row.get('ledger_row_ref') or '')
                    for row in unresolved_ledger_rows
                    if isinstance(row, dict)
                ] + [
                    str(row.get('row_ref') or '')
                    for row in unresolved_ledger_rows
                    if isinstance(row, dict)
                ] + [
                    str(row.get('local_ref') or '')
                    for row in unresolved_ledger_rows
                    if isinstance(row, dict)
                ])
                previous_finish_review_rejections = _finish_case_review_rejection_count(workspace)
                finish_retry_is_noop = previous_finish_review_rejections > 0
                stale_unresolved_ledger_rows = [
                    row for row in unresolved_ledger_rows
                    if isinstance(row, dict) and bool(row.get('ledger_stale_against_terminal_draft'))
                ]
                recommended_next = (
                    'accepted finish cannot pass while case_resolution_ledger still has unresolved rows. '
                    'This is a mechanical provenance check, not a semantic verdict. If an unresolved row has '
                    'ledger_stale_against_terminal_draft=true and current_draft_outcome_kind matches your conclusion, '
                    'revise the ledger row to that terminal outcome; otherwise execute matching evidence, propose mapping intents, split, '
                    'or provide a legal fail_closed basis before retrying finish_case.'
                    if unresolved_ledger_rows
                    else (
                        'accepted finish requires the agent to inspect global_outcome_projection and explicitly acknowledge the current mapped/excluded/open/unresolved counts plus one work_unit_reviews entry per draft row. '
                        'A copyable finish_case_review_template is included. If the projection is not semantically acceptable, revise split/ledger/evidence/mapping intents instead of finishing.'
                    )
                )
                return workspace, None, {
                    'accepted': False,
                    'reason': 'finish_case_review_required',
                    'status': status,
                    'finish_kind': finish_kind,
                    'finish_kind_defaulted_to_accepted': finish_kind_defaulted_to_accepted,
                    'review_issue_codes': _dedupe_preserve_order([str(issue.get('issue_code') or '') for issue in review_issues]),
                    'review_issues': review_issues[:24],
                    'unresolved_case_resolution_ledger_row_count': len(unresolved_ledger_rows),
                    'unresolved_case_resolution_ledger_rows': unresolved_ledger_rows[:24],
                    'stale_unresolved_ledger_row_count': len(stale_unresolved_ledger_rows),
                    'case_resolution_ledger_sync_observation': {
                        'stale_unresolved_row_count': len(stale_unresolved_ledger_rows),
                        'protocol': (
                            'Ledger rows are Agent-authored package provenance. The fixed layer does not infer semantic outcomes from the draft. '
                            'When a later draft row is terminal but the ledger row still says needs_evidence/split_needed/fail_blocker, '
                            'the Agent must either revise the ledger to its intended terminal outcome or revise the draft/evidence path.'
                        ),
                    },
                    'finish_retry_is_noop': finish_retry_is_noop,
                    'previous_finish_review_rejection_count': previous_finish_review_rejections,
                    'current_finish_review_rejection_count': previous_finish_review_rejections + 1,
                    'global_outcome_projection': _global_outcome_projection_for_agent(workspace),
                    'work_unit_resolution_board_focus': _work_unit_resolution_board_focus_for_agent(workspace, unresolved_refs),
                    'latest_blocked_evidence_agenda': _latest_blocked_evidence_agenda_for_agent(workspace),
                    'finish_case_review_template': _finish_review_template_for_agent(workspace, accounting),
                    **_mapping_draft_observation(workspace),
                    'finish_gate': _finish_gate_observation(workspace),
                    'recommended_next_observation': recommended_next,
                }
        return workspace, _InvestigationDecision(action=('accepted' if status == 'accepted' else 'fail_closed'), reason=reason), {
            'accepted': True,
            'status': status,
            'finish_kind': finish_kind,
            'finish_kind_defaulted_to_accepted': finish_kind_defaulted_to_accepted,
            'finish_review_present': bool(str(getattr(args, 'final_case_review', '') or '').strip()),
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
    if tool_name == 'split_into_child_cases':
        args = tool_call.arguments
        if not isinstance(args, SplitIntoChildCasesToolArgs):
            return workspace, None, {'accepted': False, 'reason': 'wrong_tool_args'}
        return workspace, _InvestigationDecision(action='split_into_child_cases', reason=reason), {
            'accepted': True,
            'split_case_count': len(list(getattr(args, 'split_cases', []) or [])),
            'recorded_child_case_ref_count': len(list(getattr(args, 'recorded_child_case_refs', []) or [])),
        }
    return workspace, None, {'accepted': False, 'reason': f'unknown_tool:{tool_name}'}


def _mapping_draft_unresolved_fail_closed_reasons(draft: MappingDraft, dossier, *, workspace: CaseEvidenceWorkspace | None = None) -> list[FailClosedReason]:
    local_spans = {
        str(getattr(card, 'ref', '') or ''): card
        for card in list(getattr(dossier, 'local_span_cards', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }
    latest_blockers = _latest_blocked_intent_observations_by_local(workspace) if workspace is not None else {}
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
        requested_types = _dedupe_preserve_order([str(value or '') for value in list(getattr(row, 'requested_request_types', []) or [])])
        query_hints = _dedupe_preserve_order([str(value or '') for value in list(getattr(row, 'query_hints', []) or [])])
        title_cues = _dedupe_preserve_order([str(value or '') for value in list(getattr(span, 'title_cues', []) or [])]) if span is not None else []
        blocker = latest_blockers.get(local_ref, {})
        description_parts = [
            f'{local_ref} remains {disposition}',
            f'reason_kind={reason_kind or "unknown"}',
        ]
        if span is not None:
            file_count = int(getattr(span, 'file_ref_count', 0) or len(file_refs) or 0)
            description_parts.append(f'work_unit_file_count={file_count}')
        if title_cues:
            description_parts.append(f'title_cues={",".join(title_cues[:4])}')
        if row_reason:
            description_parts.append(f'reason={row_reason}')
        if requested_types:
            description_parts.append(f'requested_evidence={",".join(requested_types[:6])}')
        if query_hints:
            description_parts.append(f'query_hints={",".join(query_hints[:4])}')
        if candidate_refs:
            description_parts.append(f'candidate_refs={",".join(candidate_refs[:8])}')
        blocker_issue_codes = [str(value or '') for value in list(blocker.get('issue_codes') or []) if str(value or '')]
        if blocker_issue_codes:
            description_parts.append(f'latest_blocker={",".join(blocker_issue_codes[:6])}')
        blocker_requested = [str(value or '') for value in list(blocker.get('requested_request_types') or []) if str(value or '')]
        if blocker_requested:
            description_parts.append(f'latest_requested_evidence={",".join(blocker_requested[:6])}')
        blocker_reason = str(blocker.get('reason') or '').strip()
        if blocker_reason:
            description_parts.append(f'latest_blocker_reason={blocker_reason}')
        related_refs = _compact_fail_closed_related_refs(_dedupe_preserve_order([local_ref, *file_refs[:6], *support_refs[:6], *candidate_refs[:8]]))
        reasons.append(FailClosedReason(
            ref=str(getattr(row, 'row_ref', '') or local_ref or f'FR{len(reasons) + 1}'),
            reason_kind='insufficient_evidence' if disposition in {'needs_more_evidence', 'open'} else 'contradiction',
            description='; '.join(description_parts),
            related_refs=related_refs,
        ))
    return reasons[:12]


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


def _compact_verifier_issues(issues: list[VerifierIssue]) -> list[dict[str, object]]:
    return [
        {
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


def _classify_supplemental_reason(text: str) -> str:
    return classify_supplemental_reason(text)


def _supplemental_reason_from_local_ref(dossier, local_ref: str) -> str:
    return supplemental_reason_from_local_ref(dossier, local_ref)


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
