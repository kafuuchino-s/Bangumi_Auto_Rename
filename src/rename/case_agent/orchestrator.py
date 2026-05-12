from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Literal

from .evidence_broker import EvidenceBroker
from .evidence_menu_resolver import resolve_evidence_menu_requests
from .evidence_request_normalizer import normalize_evidence_requests
from .case_planner import build_child_workspace, call_case_planner, verify_case_planning_output
from .assignment_expander import expand_mapping_draft
from .mapping_draft import apply_mapping_patches, build_initial_mapping_draft, compact_mapping_draft, compute_local_span_partition_coverage, summarize_mapping_draft_coverage
from .mapping_draft import compute_mapping_draft_accounting
from .mapping_draft import normalize_mapping_patch_op
from .mapping_editor import call_mapping_draft_editor
from .planner import build_deterministic_evidence_plan
from .query_composer import call_query_composer
from .local_structure_agent import call_local_structure_agent
from .dossier import build_bounded_case_dossier
from .policy import normalize_fail_closed, build_action_policy
from .judge_client import call_case_judge
from .prompting import _recommended_neutral_requests
from .special_investigation import is_special_eligible_span, special_eligible_open_row_refs, special_eligible_row_refs, special_like_item_refs
from .supplemental_policy import classify_supplemental_reason, local_ref_text_for_supplemental_issue, main_file_refs_for_mapping_row, supplemental_category_supported_by_text, supplemental_reason_from_local_ref, supplemental_row_policy_issues
from .surface_ledger import build_surface_ledger
from .notebook import build_notebook
from .issue_router import route_verifier_issues
from .models import CaseJudgeOutput, CasePlanningOutput, CaseVerifierResult, EvidenceBatchResult, FailClosedReason, Finding, MappingDraftPatch, VerifierIssue
from .models import EvidenceRequest, MappingDraft
from .verifier import verify_judge_output, verify_mapping_draft_accounting
from .workspace import CaseEvidenceWorkspace


InvestigationAction = Literal[
    'compose_queries',
    'plan_evidence',
    'execute_evidence',
    'edit_mapping_draft',
    'verify_mapping_draft',
    'judge_semantic_blocker',
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
    _planning_depth: int = 0,
) -> CaseAgentRunResult:
    workspace = _workspace_with_local_structure(initial_workspace, ai_client)
    planning_output: CasePlanningOutput | None = None
    planning_evidence_batches: list[EvidenceBatchResult] = []

    def _with_planning_output(result: CaseAgentRunResult) -> CaseAgentRunResult:
        if planning_output is not None and result.planning_output is None:
            result.planning_output = planning_output
        return result

    def _result(*args, **kwargs) -> CaseAgentRunResult:
        return _with_planning_output(CaseAgentRunResult(*args, **kwargs))

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
        })
        return _result(True, audited_workspace.header.case_id, 'fail_closed', 'fail_closed', fail_output, fail_verifier, audited_workspace, judge_outputs, evidence_batches, 'no_new_evidence', [*errors, reason])

    def _semantic_target_conflict_fail_closed(
        current_workspace: CaseEvidenceWorkspace,
        verifier_result: CaseVerifierResult,
        *,
        reason: str = 'verifier_rejected_unexecutable_verdict',
    ) -> CaseAgentRunResult:
        issues = list(getattr(verifier_result, 'issues', []) or [])
        fail_output = CaseJudgeOutput(
            action='fail_closed',
            fail_closed_reasons=[
                FailClosedReason(
                    ref=f'FR{index}',
                    reason_kind='contradiction',
                    description=f'{str(getattr(issue, "issue_code", "") or "verifier_issue")}: {str(getattr(issue, "message", "") or reason)}',
                    related_refs=list(getattr(issue, 'related_refs', []) or [])[:8],
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
        fail_verifier = verify_judge_output(current_workspace.to_dossier(round_context='semantic_target_conflict_fail_closed'), fail_output)
        audited_workspace = _workspace_with_judge_audit(current_workspace, {
            'note': 'semantic_target_conflict_fail_closed',
            'reason': reason,
            'source_verifier_issue_count': len(issues),
            'verifier_passed': bool(getattr(fail_verifier, 'passed', False)),
        })
        return _result(True, audited_workspace.header.case_id, 'fail_closed', 'fail_closed', fail_output, fail_verifier, audited_workspace, judge_outputs, evidence_batches, 'semantic_target_conflict', [*errors, reason])

    if _planning_depth == 0:
        planning_phase = _run_case_planning_phase(
            workspace,
            ai_client,
            bangumi_client,
            max_rounds=max_rounds,
            planning_depth=_planning_depth,
        )
        planning_output = planning_phase.planning_output
        if planning_phase.terminal_result is not None:
            return _with_planning_output(planning_phase.terminal_result)
        workspace = planning_phase.workspace
        planning_evidence_batches = list(planning_phase.evidence_batches)
    workspace = _workspace_with_initial_mapping_draft(workspace)
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
    while True:
        workspace = _refresh_mapping_draft_candidates(_workspace_with_initial_mapping_draft(workspace))
        decision = _next_investigation_action(workspace, executed_planner_keys=executed_planner_keys)
        workspace = _workspace_with_judge_audit(workspace, {
            'note': 'investigation_action_selected',
            'action': decision.action,
            'reason': decision.reason,
        })
        if decision.action == 'compose_queries':
            composer_result = call_query_composer(ai_client, workspace.to_dossier(round_context='query_composer'))
            workspace = _workspace_with_judge_audit(workspace, getattr(composer_result, 'request_audit', None))
            if not composer_result.ok:
                error_text = composer_result.error or 'query composer call failed'
                lower = error_text.casefold()
                error_kind = 'context_overflow' if 'exceeds the context window' in lower else ('provider_no_response' if 'no response' in lower else 'provider_error')
                summary = 'query composer context overflow' if error_kind == 'context_overflow' else ('query composer infra no response' if error_kind == 'provider_no_response' else 'query composer infra error')
                return _result(False, workspace.header.case_id, 'error', 'query_composer', final_output, final_verifier_result, workspace, judge_outputs, evidence_batches, summary, [*errors, error_text, f'error_kind={error_kind}'])
            if not composer_result.query_cards:
                workspace = _workspace_with_judge_audit(workspace, {
                    'note': 'query_composer_no_executable_queries',
                    'summary': getattr(composer_result.output, 'summary', '') if composer_result.output is not None else '',
                })
                break
            workspace = workspace.with_query_cards(composer_result.query_cards)
            workspace = _workspace_with_judge_audit(workspace, {
                'note': 'query_composer_added_queries',
                'query_refs': [card.ref for card in composer_result.query_cards],
                'query_texts': [card.query_text for card in composer_result.query_cards],
            })
            continue
        if decision.action == 'edit_mapping_draft':
            draft_result = _try_mapping_draft_editor_acceptance(workspace, ai_client, judge_outputs, evidence_batches)
            if draft_result is not None:
                return _with_planning_output(draft_result)
            decision = _InvestigationDecision(action='judge_semantic_blocker', reason='mapping_draft_editor_no_terminal_result')
        if decision.action == 'accepted':
            draft_result = _try_mapping_draft_editor_acceptance(workspace, ai_client, judge_outputs, evidence_batches)
            if draft_result is not None:
                return _with_planning_output(draft_result)
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
                return _result(True, workspace.header.case_id, 'fail_closed', 'fail_closed', fail_output, verifier_result, workspace, judge_outputs, evidence_batches, 'no_new_evidence', [f'unresolved_count={int(getattr(accounting, "unresolved_count", 0) or 0)}'])
        if decision.action != 'execute_evidence' or decision.planner_output is None:
            break
        planner_output = decision.planner_output
        planned_ids = list(planner_output.plan.selected_menu_request_ids or [])
        planner_key = decision.planner_key or (str(planner_output.plan.plan_kind or ''), tuple(planned_ids))
        if planner_key in executed_planner_keys:
            break
        executed_planner_keys.add(planner_key)
        resolved_requests, selected_menu_request_ids, unknown_menu_request_ids, resolved_menu_request_count = resolve_evidence_menu_requests(workspace, planned_ids)
        if not resolved_requests or unknown_menu_request_ids:
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
        workspace = _refresh_mapping_draft_candidates(_workspace_with_initial_mapping_draft(workspace))
        if _should_try_mapping_editor(workspace):
            draft_result = _try_mapping_draft_editor_acceptance(workspace, ai_client, judge_outputs, evidence_batches)
            if draft_result is not None:
                return _with_planning_output(draft_result)
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
                    })
                    if not deduped_requests:
                        workspace = _refresh_mapping_draft_candidates(_workspace_with_initial_mapping_draft(workspace))
                        if _should_try_mapping_editor(workspace):
                            draft_result = _try_mapping_draft_editor_acceptance(workspace, ai_client, judge_outputs, evidence_batches)
                            if draft_result is not None:
                                return _with_planning_output(draft_result)
                        continue
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
            workspace = _refresh_mapping_draft_candidates(_workspace_with_initial_mapping_draft(workspace))
            if _should_try_mapping_editor(workspace):
                draft_result = _try_mapping_draft_editor_acceptance(workspace, ai_client, judge_outputs, evidence_batches)
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
                    'reason': 'judge claimed no assignable target while assignable/detail refs are visible',
                    'assignable_target_count': len(getattr(dossier, 'assignable_target_refs', []) or []),
                    'seen_detail_ref_count': len(getattr(dossier, 'seen_detail_refs', []) or []),
                    'detail_equivalent_target_span_count': len(_detail_equivalent_span_refs(workspace)),
                })
                workspace = _refresh_mapping_draft_candidates(_workspace_with_initial_mapping_draft(workspace))
                if _should_try_mapping_editor(workspace):
                    draft_result = _try_mapping_draft_editor_acceptance(workspace, ai_client, judge_outputs, evidence_batches)
                    if draft_result is not None:
                        return _with_planning_output(draft_result)
                if 'contradictory_fail_closed_retry_used' not in (getattr(workspace, 'diagnostics', []) or []):
                    workspace = _workspace_preserving_state(workspace, diagnostics=[*workspace.diagnostics, 'contradictory_fail_closed_retry_used'])
                    continue
                return _result(True, workspace.header.case_id, 'fail_closed', final_action, final_output, final_verifier_result, workspace, judge_outputs, evidence_batches, 'semantic_target_conflict', [*errors, contradiction_reason])

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


def _run_case_planning_phase(
    workspace: CaseEvidenceWorkspace,
    ai_client,
    bangumi_client,
    *,
    max_rounds: int | None,
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
        fail_output = CaseJudgeOutput(
            action='fail_closed',
            fail_closed_reasons=[
                FailClosedReason(
                    ref='FR1',
                    reason_kind='insufficient_evidence',
                    description='unknown_case_planning_menu_request_id: planner selected menu ids that are not executable in the current evidence menu',
                    related_refs=[],
                )
            ],
            summary='case planning evidence menu request was not executable',
        )
        verifier_result = verify_judge_output(workspace.to_dossier(round_context='case_planning_unknown_menu_request'), fail_output)
        return workspace, [], CaseAgentRunResult(
            ok=True,
            case_id=workspace.header.case_id,
            status='fail_closed',
            final_action='fail_closed',
            final_output=fail_output,
            final_verifier_result=verifier_result,
            final_workspace=workspace,
            summary='no_new_evidence',
            errors=['unknown_case_planning_menu_request_id'],
            planning_output=output,
        )
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
            fail_output = CaseJudgeOutput(
                action='fail_closed',
                fail_closed_reasons=[FailClosedReason(ref='FR1', reason_kind='insufficient_evidence', description='case planning evidence request returned no usable evidence', related_refs=[])],
                summary='case planning evidence returned no usable evidence',
            )
            verifier_result = verify_judge_output(new_workspace.to_dossier(round_context='case_planning'), fail_output)
            return new_workspace, [batch_result], CaseAgentRunResult(
                ok=True,
                case_id=new_workspace.header.case_id,
                status='fail_closed',
                final_action='fail_closed',
                final_output=fail_output,
                final_verifier_result=verifier_result,
                final_workspace=new_workspace,
                evidence_batches=[batch_result],
                summary='case planning evidence returned no usable evidence',
                errors=['case_planning_no_usable_evidence'],
                planning_output=output,
            )
        fail_output = CaseJudgeOutput(
            action='fail_closed',
            fail_closed_reasons=[FailClosedReason(ref='FR1', reason_kind='insufficient_evidence', description='case planning evidence request returned no usable evidence', related_refs=[])],
            summary='case planning evidence returned no usable evidence',
        )
        verifier_result = verify_judge_output(new_workspace.to_dossier(round_context='case_planning'), fail_output)
        return new_workspace, [batch_result], CaseAgentRunResult(
            ok=True,
            case_id=new_workspace.header.case_id,
            status='fail_closed',
            final_action='fail_closed',
            final_output=fail_output,
            final_verifier_result=verifier_result,
            final_workspace=new_workspace,
            evidence_batches=[batch_result],
            summary='case planning evidence returned no usable evidence',
            errors=['case_planning_no_usable_evidence'],
            planning_output=output,
        )
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
    return CaseAgentRunResult(True, workspace.header.case_id, 'fail_closed', final_action, final_output, final_verifier_result, workspace, judge_outputs, evidence_batches, 'coverage_gap_unresolved', [*errors, 'coverage_gap_unresolved'])


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
    return [row for row in list(getattr(draft, 'rows', []) or []) if getattr(row, 'status', '') == 'open' or getattr(row, 'disposition', '') == 'open']


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
    changed = False
    updated = draft.model_copy(deep=True)
    for row in updated.rows:
        if getattr(row, 'status', '') != 'open' and getattr(row, 'disposition', '') != 'open':
            continue
        linked = [
            span.ref for span in detail_spans
            if str(getattr(span, 'source_request_ref', '') or '') == f'REQ_TARGET_SPAN_{row.local_ref}'
        ]
        if not linked:
            local_span = next((card for card in getattr(dossier, 'local_span_cards', []) or [] if getattr(card, 'ref', '') == row.local_ref), None)
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
                    if int(getattr(span, 'target_ref_count', 0) or len(getattr(span, 'target_refs', []) or [])) == local_count
                    and getattr(span, 'sort_start', None) == local_start
                    and getattr(span, 'sort_end', None) == local_end
                ]
            if not linked and local_count == 1 and len(detail_spans) == 1:
                linked = [
                    span.ref for span in detail_spans
                    if int(getattr(span, 'target_ref_count', 0) or len(getattr(span, 'target_refs', []) or [])) == local_count
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
            local_span = next((card for card in getattr(dossier, 'local_span_cards', []) or [] if getattr(card, 'ref', '') == row.local_ref), None)
            if is_special_eligible_span(local_span, dossier):
                special_linked = list(special_item_refs)
        before = list(row.candidate_target_refs or [])
        merged = list(dict.fromkeys([*before, *linked, *special_linked]))
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
    raw_issue_refs = {
        str(getattr(issue, 'ref', '') or '')
        for issue in list(issues or [])
        if str(getattr(issue, 'ref', '') or '')
    }
    for issue in list(issues or []):
        for related_ref in list(getattr(issue, 'related_refs', []) or []):
            related_ref = str(related_ref or '')
            if related_ref:
                raw_issue_refs.add(related_ref)
    rows = list(getattr(draft, 'rows', []) or [])
    rows_by_ref = {
        key: row
        for row in rows
        for key in (str(getattr(row, 'row_ref', '') or ''), str(getattr(row, 'local_ref', '') or ''))
        if key
    }
    rows_by_selected_target: dict[str, list[object]] = {}
    for row in rows:
        target_ref = str(getattr(row, 'selected_target_ref', '') or '')
        if target_ref:
            rows_by_selected_target.setdefault(target_ref, []).append(row)
    issue_refs = set(raw_issue_refs)
    for ref in list(raw_issue_refs):
        if ref in rows_by_ref:
            row = rows_by_ref[ref]
            target_ref = str(getattr(row, 'selected_target_ref', '') or '')
            if target_ref:
                issue_refs.add(target_ref)
                for target_row in rows_by_selected_target.get(target_ref, []):
                    issue_refs.add(str(getattr(target_row, 'row_ref', '') or ''))
                    issue_refs.add(str(getattr(target_row, 'local_ref', '') or ''))
        for target_row in rows_by_selected_target.get(ref, []):
            issue_refs.add(str(getattr(target_row, 'row_ref', '') or ''))
            issue_refs.add(str(getattr(target_row, 'local_ref', '') or ''))
    if not issue_refs:
        return workspace
    issue_row_refs: set[str] = set()
    for issue_ref in issue_refs:
        row = rows_by_ref.get(issue_ref)
        row_ref = str(getattr(row, 'row_ref', '') or '') if row is not None else ''
        if row_ref:
            issue_row_refs.add(row_ref)
    updated = draft.model_copy(deep=True)
    changed = False
    for row in list(getattr(updated, 'rows', []) or []):
        if str(getattr(row, 'row_ref', '') or '') not in issue_refs and str(getattr(row, 'local_ref', '') or '') not in issue_refs:
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


def _should_try_mapping_editor(workspace: CaseEvidenceWorkspace) -> bool:
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None or not getattr(draft, 'rows', None):
        return False
    if not _draft_open_rows(draft):
        return False
    if _open_regular_rows_waiting_for_span_proof(workspace):
        return False
    if _pending_special_request_ids(workspace):
        return False
    if not _detail_equivalent_span_refs(workspace) and not _open_special_rows_with_candidates(workspace):
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
    rows: list[str] = []
    for row in list(getattr(draft, 'rows', []) or []):
        local_ref = str(getattr(row, 'local_ref', '') or '')
        if local_ref not in eligible:
            continue
        if any(ref in special_refs for ref in list(getattr(row, 'candidate_target_refs', []) or [])):
            rows.append(local_ref)
    return rows


def _pending_special_request_ids(workspace: CaseEvidenceWorkspace) -> list[str]:
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None:
        return []
    dossier = workspace.to_dossier(round_context='special_pending_gate')
    if not special_eligible_open_row_refs(draft, dossier):
        return []
    completed_or_failed = set(getattr(workspace.plan_state, 'completed_menu_request_ids', []) or []) | set(getattr(workspace.plan_state, 'failed_menu_request_ids', []) or [])
    from .evidence_menu import build_executable_evidence_menu

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


def _contradictory_fail_closed_guard(workspace: CaseEvidenceWorkspace, dossier, output: CaseJudgeOutput) -> str | None:
    if not _fail_closed_claims_no_assignable_target(output):
        return None
    if not _has_assignable_or_detail_surface(workspace, dossier):
        return None
    return 'contradictory_fail_closed'


def _next_investigation_action(workspace: CaseEvidenceWorkspace, *, executed_planner_keys: set[tuple[str, tuple[str, ...]]] | None = None) -> _InvestigationDecision:
    workspace = _refresh_mapping_draft_candidates(_workspace_with_initial_mapping_draft(workspace))
    draft = getattr(workspace, 'mapping_draft', None)
    accounting, accounting_verifier_result = _mapping_draft_accounting_result(workspace)
    if accounting is not None and bool(getattr(accounting, 'accepted_accounting_ready', False)) and accounting_verifier_result is not None and accounting_verifier_result.passed:
        return _InvestigationDecision(action='accepted', reason='mapping_draft_accounting_ready')

    open_rows = _draft_open_rows(draft)
    open_rows_without_candidates = _open_rows_without_candidates(draft)
    open_regular_rows_waiting_for_span_proof = _open_regular_rows_waiting_for_span_proof(workspace)
    detail_refs = _detail_equivalent_span_refs(workspace)
    special_candidate_rows = _open_special_rows_with_candidates(workspace)
    special_rows_without_candidates = []
    if draft is not None and open_rows:
        dossier_for_special = workspace.to_dossier(round_context='investigation_special_gate')
        eligible_special_rows = set(special_eligible_open_row_refs(draft, dossier_for_special))
        special_rows_without_candidates = [
            row.local_ref
            for row in open_rows
            if str(getattr(row, 'local_ref', '') or '') in eligible_special_rows
            and str(getattr(row, 'local_ref', '') or '') not in set(special_candidate_rows)
        ]
    pending_special_request_ids = _pending_special_request_ids(workspace)
    if draft is not None and open_rows and _mapping_draft_has_complete_local_coverage(workspace, draft):
        if special_candidate_rows and not open_rows_without_candidates and not pending_special_request_ids:
            return _InvestigationDecision(action='edit_mapping_draft', reason='open_special_singleton_rows_with_assignable_items')
        if detail_refs and not special_rows_without_candidates and not open_regular_rows_waiting_for_span_proof and not pending_special_request_ids:
            return _InvestigationDecision(action='edit_mapping_draft', reason='open_draft_rows_with_detail_equivalent_spans')

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
    if can_search_subjects and not _has_composed_subject_search_query(workspace):
        return _InvestigationDecision(action='compose_queries', reason='subject_recall_requires_agent_composed_queries')

    next_planner_output = build_deterministic_evidence_plan(workspace)
    if next_planner_output and next_planner_output.selected_evidence and next_planner_output.plan and (workspace.budget.max_evidence_batches == 0 or workspace.budget.used_evidence_batches < workspace.budget.max_evidence_batches):
        planned_ids = list(next_planner_output.plan.selected_menu_request_ids or [])
        planner_key = (str(next_planner_output.plan.plan_kind or ''), tuple(planned_ids))
        if planner_key not in (executed_planner_keys or set()):
            return _InvestigationDecision(action='execute_evidence', reason='deterministic_planner_selected_evidence', planner_output=next_planner_output, planner_key=planner_key)

    return _InvestigationDecision(action='judge_semantic_blocker', reason='no_deterministic_investigation_action')


def _try_mapping_draft_editor_acceptance(workspace: CaseEvidenceWorkspace, ai_client, judge_outputs: list[CaseJudgeOutput], evidence_batches: list[EvidenceBatchResult], *, repair_depth: int = 0, editor_retry_depth: int = 0) -> CaseAgentRunResult | None:
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None or not getattr(draft, 'rows', None):
        workspace = _workspace_with_judge_audit(workspace, {'note': 'mapping_draft_editor_skipped', 'reason': 'no_draft'})
        return None
    workspace = _refresh_mapping_draft_candidates(workspace)
    draft = getattr(workspace, 'mapping_draft', None)
    dossier = workspace.to_dossier(round_context='mapping_draft_edit')
    detail_spans = [card for card in (getattr(dossier, 'bangumi_span_cards', []) or []) if bool(getattr(card, 'detail_equivalent', False))]
    special_candidate_rows = _open_special_rows_with_candidates(workspace)
    if not detail_spans and not special_candidate_rows:
        workspace = _workspace_with_judge_audit(workspace, {'note': 'mapping_draft_editor_skipped', 'reason': 'no_detail_equivalent_span_or_special_item_candidate'})
        return None
    if not any(row.status == 'open' for row in draft.rows):
        workspace = _workspace_with_judge_audit(workspace, {'note': 'mapping_draft_editor_skipped', 'reason': 'no_open_rows'})
        return None
    if _pending_special_request_ids(workspace):
        workspace = _workspace_with_judge_audit(workspace, {
            'note': 'mapping_draft_editor_skipped',
            'reason': 'pending_special_investigation_requests',
            'pending_special_request_ids': _pending_special_request_ids(workspace),
        })
        return None

    coverage_issue = _mapping_draft_local_coverage_issue(workspace, draft)
    if coverage_issue is not None:
        workspace = _workspace_with_judge_audit(workspace, coverage_issue)
        reason = 'mapping_draft_incomplete_local_coverage'
        return CaseAgentRunResult(False, workspace.header.case_id, 'invalid', 'submit_verdict', None, None, workspace, judge_outputs, evidence_batches, reason, [reason])

    editor_result = call_mapping_draft_editor(ai_client, dossier, draft, round_kind='mapping_draft_edit', max_provider_retries=0)
    workspace = _workspace_with_judge_audit(workspace, getattr(editor_result, 'request_audit', None))
    workspace = _workspace_with_judge_audit(workspace, {
        'note': 'mapping_draft_editor_called',
        'ok': editor_result.ok,
        'error': editor_result.error,
        'mapping_editor_call_count': 1,
        'mapping_editor_output_bytes': len(str(getattr(editor_result, 'raw_response', '') or '')),
    })
    if not editor_result.ok or editor_result.output is None:
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
                return _try_mapping_draft_editor_acceptance(
                    retry_workspace,
                    ai_client,
                    judge_outputs,
                    evidence_batches,
                    repair_depth=repair_depth,
                    editor_retry_depth=editor_retry_depth + 1,
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
            return CaseAgentRunResult(False, workspace.header.case_id, 'error', 'edit_mapping_draft', None, verifier_result, workspace, judge_outputs, evidence_batches, 'mapping_draft_editor_unavailable', [editor_result.error])
        return None

    output = editor_result.output
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
    comparison_issues = _comparison_patch_consistency_issues(draft, output, dossier)
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
        repair_result = _try_mapping_draft_editor_acceptance(
            repair_workspace,
            ai_client,
            judge_outputs=judge_outputs,
            evidence_batches=evidence_batches,
            repair_depth=repair_depth + 1,
        )
        if repair_result is not None:
            return repair_result
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
        return CaseAgentRunResult(True, workspace.header.case_id, 'fail_closed', 'fail_closed', fail_output, verifier_result, workspace, judge_outputs, evidence_batches, 'semantic_target_conflict', ['mapping_draft_comparison_conflict'])
    updated_draft, patch_issues = apply_mapping_patches(draft, editor_patches, dossier)
    workspace = _workspace_with_mapping_draft(workspace, updated_draft, patches=editor_patches, candidate_comparisons=list(output.candidate_comparisons or []), note='mapping_draft_editor_patches_applied')
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
            repair_result = _try_mapping_draft_editor_acceptance(
                repair_workspace,
                ai_client,
                judge_outputs=judge_outputs,
                evidence_batches=evidence_batches,
                repair_depth=repair_depth + 1,
            )
            if repair_result is not None:
                return repair_result
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
                return _finish_mapping_draft_after_patches(
                    workspace,
                    dossier,
                    salvaged_draft,
                    output,
                    judge_outputs,
                    evidence_batches,
                    ai_client=ai_client,
                    repair_depth=repair_depth,
                )
        structural_patches = _structural_unique_span_patches(draft, dossier)
        if structural_patches:
            repaired_draft, repaired_issues = apply_mapping_patches(draft, structural_patches, dossier)
            if not repaired_issues:
                workspace = _workspace_with_judge_audit(workspace, {
                    'note': 'mapping_draft_structural_unique_span_repair',
                    'patch_count': len(structural_patches),
                })
                workspace = _workspace_with_mapping_draft(workspace, repaired_draft, patches=structural_patches, note='mapping_draft_structural_patches_applied')
                return _finish_mapping_draft_after_patches(
                    workspace,
                    dossier,
                    repaired_draft,
                    output,
                    judge_outputs,
                    evidence_batches,
                    ai_client=ai_client,
                    repair_depth=repair_depth,
                )
        verifier_result = CaseVerifierResult(passed=False, issues=patch_issues, summary='mapping draft patch rejected')
        fail_output = CaseJudgeOutput(
            action='fail_closed',
            findings=[],
            candidate_comparisons=[],
            fail_closed_reasons=[FailClosedReason(ref='FR1', reason_kind='insufficient_evidence', description='mapping draft patch rejected by mechanical validator: ' + ','.join(_dedupe_preserve_order([str(getattr(issue, 'issue_code', '') or '') for issue in patch_issues])), related_refs=[])],
            summary='mapping draft patch rejected',
        )
        verifier_result = verify_judge_output(dossier, fail_output)
        return CaseAgentRunResult(True, workspace.header.case_id, 'fail_closed', 'fail_closed', fail_output, verifier_result, workspace, judge_outputs, evidence_batches, 'no_new_evidence', ['mapping_draft_patch_rejected'])

    return _finish_mapping_draft_after_patches(
        workspace,
        dossier,
        updated_draft,
        output,
        judge_outputs,
        evidence_batches,
        ai_client=ai_client,
        repair_depth=repair_depth,
    )


def _finish_mapping_draft_after_patches(workspace: CaseEvidenceWorkspace, dossier, updated_draft: MappingDraft, output, judge_outputs: list[CaseJudgeOutput], evidence_batches: list[EvidenceBatchResult], *, ai_client=None, repair_depth: int = 0) -> CaseAgentRunResult:
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
        )
        if structural_result is not None:
            return structural_result
        if _has_special_investigation_rows(workspace) and any(
            str(getattr(issue, 'issue_code', '')).casefold() == 'duplicate_target'
            for issue in list(getattr(accounting_verifier_result, 'issues', []) or [])
        ):
            pending_special = _pending_special_request_ids(workspace)
            if pending_special:
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
                return CaseAgentRunResult(True, workspace.header.case_id, 'fail_closed', 'fail_closed', fail_output, verifier_result, workspace, judge_outputs, evidence_batches, 'special_evidence_pending_target_conflict', ['special_evidence_pending_target_conflict'])
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
            )
            if structural_result is not None:
                return structural_result
            unresolved_special_issues = [
                *_unresolved_special_candidate_issues(dossier, updated_draft),
                *_unresolved_supplemental_candidate_issues(dossier, updated_draft),
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
                )
                if repair_result is not None:
                    return repair_result
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
        repairable_expansion_codes = {'duplicate_target', 'count_mismatch', 'missing_support_refs', 'invalid_target', 'missing_span_ref', 'duplicate_local_span'}
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

    findings = [
        finding.model_copy(update={'evidence_refs': _filter_refs(list(getattr(finding, 'evidence_refs', []) or []))})
        for finding in list(getattr(output, 'findings', []) or [])
    ]
    comparisons = [
        comparison.model_copy(update={'evidence_refs': _filter_refs(list(getattr(comparison, 'evidence_refs', []) or []))})
        for comparison in list(getattr(output, 'candidate_comparisons', []) or [])
    ]
    self_checks = []
    for self_check in list(getattr(output, 'self_checks', []) or []):
        check_findings = [
            finding.model_copy(update={'evidence_refs': _filter_refs(list(getattr(finding, 'evidence_refs', []) or []))})
            for finding in list(getattr(self_check, 'findings', []) or [])
        ]
        self_checks.append(self_check.model_copy(update={'findings': check_findings}))
    return output.model_copy(update={
        'findings': findings,
        'candidate_comparisons': comparisons,
        'self_checks': self_checks,
    })


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
        related_refs = _dedupe_preserve_order([local_ref, *file_refs[:6], *support_refs[:6], *candidate_refs[:8]])
        reasons.append(FailClosedReason(
            ref=str(getattr(row, 'row_ref', '') or local_ref or f'FR{len(reasons) + 1}'),
            reason_kind='insufficient_evidence' if disposition in {'needs_more_evidence', 'open'} else 'contradiction',
            description='; '.join(description_parts),
            related_refs=related_refs[:12],
        ))
    return reasons[:12]


def _try_structural_mapping_draft_repair(workspace: CaseEvidenceWorkspace, dossier, draft: MappingDraft, output, judge_outputs: list[CaseJudgeOutput], evidence_batches: list[EvidenceBatchResult], *, reason_note: str) -> CaseAgentRunResult | None:
    if any(isinstance(audit, dict) and audit.get('note') == reason_note for audit in list(getattr(workspace, 'judge_request_audits', []) or [])):
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
    repaired_draft, repaired_issues = apply_mapping_patches(draft, structural_patches, dossier)
    if repaired_issues:
        return None
    workspace = _workspace_with_judge_audit(workspace, {
        'note': reason_note,
        'patch_count': len(structural_patches),
    })
    workspace = _workspace_with_mapping_draft(workspace, repaired_draft, patches=structural_patches, note='mapping_draft_structural_patches_applied')
    return _finish_mapping_draft_after_patches(
        workspace,
        dossier,
        repaired_draft,
        output,
        judge_outputs,
        evidence_batches,
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
        'missing_singleton_candidate_comparison',
        'missing_target_ref',
        'missing_support_refs',
        'supplemental_singleton_target_mismatch',
        'unsupported_singleton_comparison_winner',
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
    local_spans = {str(getattr(card, 'ref', '') or ''): card for card in list(getattr(dossier, 'local_span_cards', []) or [])}
    issues: list[VerifierIssue] = []
    for row in list(getattr(draft, 'rows', []) or []):
        if str(getattr(row, 'disposition', '') or '') not in {'needs_more_evidence', 'unaligned_fail_closed', 'open'}:
            continue
        local_ref = str(getattr(row, 'local_ref', '') or '')
        if not is_special_eligible_span(local_spans.get(local_ref), dossier):
            continue
        candidate_refs = [ref for ref in list(getattr(row, 'candidate_target_refs', []) or []) if ref in special_refs]
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
        'unknown_target_span_ref',
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
    issue_refs = _dedupe_preserve_order([str(getattr(issue, 'ref', '') or '') for issue in patch_issues])
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
            patch = MappingDraftPatch(
                op='mark_non_bangumi_or_supplemental',
                local_ref=local_ref,
                support_refs=_dedupe_preserve_order([local_ref, *list(getattr(attempted, 'support_refs', []) or [])]),
                reason_kind=_supplemental_reason_from_local_ref(dossier, local_ref),
                reason=str(getattr(attempted, 'reason', '') or 'visible local evidence supports supplemental/non-Bangumi accounting'),
            )
            if _supplemental_policy_allows_patch(dossier, original_draft, patch):
                salvage.append(patch)
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


def _comparison_patch_consistency_issues(draft: MappingDraft, output, dossier) -> list[VerifierIssue]:
    rows_by_ref = {str(getattr(row, 'row_ref', '') or ''): row for row in list(getattr(draft, 'rows', []) or [])}
    rows_by_local = {str(getattr(row, 'local_ref', '') or ''): row for row in list(getattr(draft, 'rows', []) or [])}
    patch_targets: dict[str, str] = {}
    for patch in list(getattr(output, 'patches', []) or []):
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
    special_item_refs = set(special_like_item_refs(dossier))
    local_spans = {str(getattr(card, 'ref', '') or ''): card for card in list(getattr(dossier, 'local_span_cards', []) or [])}
    winners_by_row: dict[str, set[str]] = {}
    for comparison in list(getattr(output, 'candidate_comparisons', []) or []):
        row = rows_by_ref.get(str(getattr(comparison, 'ref', '') or '')) or rows_by_local.get(str(getattr(comparison, 'ref', '') or ''))
        winner_ref = str(getattr(comparison, 'winner_ref', '') or '')
        if row is not None and winner_ref:
            winners_by_row.setdefault(str(getattr(row, 'row_ref', '') or ''), set()).add(winner_ref)
    for row_ref, selected_ref in patch_targets.items():
        row = rows_by_ref.get(row_ref)
        if row is None or selected_ref not in special_item_refs:
            continue
        candidate_item_refs = [ref for ref in list(getattr(row, 'candidate_target_refs', []) or []) if ref in special_item_refs]
        if len(candidate_item_refs) <= 1:
            continue
        if not is_special_eligible_span(local_spans.get(str(getattr(row, 'local_ref', '') or '')), dossier):
            continue
        if selected_ref not in winners_by_row.get(row_ref, set()):
            issues.append(VerifierIssue(
                ref=row_ref,
                issue_code='missing_singleton_candidate_comparison',
                severity='blocked',
                message='explicit singleton mapping with multiple item candidates requires a row comparison naming the selected target as winner',
                related_refs=[str(getattr(row, 'local_ref', '') or ''), selected_ref, *candidate_item_refs[:8]],
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
        if (
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
        local_count = int(getattr(local_span, 'file_ref_count', 0) or len(getattr(local_span, 'file_refs', []) or []))
        source_bound_refs = {
            span.ref for span in bangumi_spans.values()
            if str(getattr(span, 'source_request_ref', '') or '') == f'REQ_TARGET_SPAN_{row.local_ref}'
        }
        candidate_refs = list(source_bound_refs or set(getattr(row, 'candidate_target_refs', []) or []))
        candidates = []
        for ref in candidate_refs:
            span = bangumi_spans.get(ref)
            if span is None:
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
        local_count = int(getattr(local_span, 'file_ref_count', 0) or len(getattr(local_span, 'file_refs', []) or []))
        source_bound_refs = [
            span.ref for span in bangumi_spans.values()
            if str(getattr(span, 'source_request_ref', '') or '') == f'REQ_TARGET_SPAN_{row.local_ref}'
        ]
        candidate_refs = source_bound_refs or list(getattr(row, 'candidate_target_refs', []) or [])
        candidates = []
        for ref in candidate_refs:
            span = bangumi_spans.get(str(ref or ''))
            if span is None:
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
