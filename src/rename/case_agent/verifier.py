from __future__ import annotations

from collections import Counter
import re

from .assignment_expander import expand_mapping_draft
from .mapping_draft import compute_mapping_draft_accounting
from .assignment_expander import expand_bulk_assignment_intents
from .models import CaseDossier, CaseJudgeOutput, CaseVerifierResult, MappingDraft, VerifierIssue, iter_case_judge_ref_lists
from .supplemental_policy import ALLOWED_SUPPLEMENTAL_REASON_KINDS, supplemental_row_policy_issues


def _sanitize_fail_closed_aux_refs(output: CaseJudgeOutput, dossier: CaseDossier) -> tuple[CaseJudgeOutput, list[VerifierIssue]]:
    if output.action != 'fail_closed':
        return output, []
    visible = _visible(dossier)
    detail_refs = _detail_surface_refs(dossier)
    prior_evidence_refs = _prior_evidence_refs(dossier)
    allowed = visible | detail_refs | prior_evidence_refs | {r.ref for r in output.fail_closed_reasons}
    issues: list[VerifierIssue] = []
    changed = False

    def _note_sanitized() -> None:
        issues.append(VerifierIssue(ref='fail_closed', issue_code='auxiliary_ref_sanitized', severity='info', message='auxiliary refs sanitized for fail_closed'))

    def _filter_refs(values: list[str]) -> list[str]:
        nonlocal changed
        kept = [ref for ref in values if not ref or ref in allowed]
        if len(kept) != len(values):
            changed = True
            _note_sanitized()
        return kept

    def _filter_fail_closed_related_refs(values: list[str]) -> list[str]:
        nonlocal changed
        kept: list[str] = []
        for ref in values:
            if not ref or ref in allowed:
                kept.append(ref)
                continue
            changed = True
            _note_sanitized()
        compact = _compact_fail_closed_related_refs(kept)
        if compact != kept:
            changed = True
            _note_sanitized()
        return compact

    updates = {
        'findings': [item.model_copy(update={'evidence_refs': _filter_refs(list(getattr(item, 'evidence_refs', []) or []))}) for item in output.findings],
        'hypotheses': [item.model_copy(update={'evidence_refs': _filter_refs(list(getattr(item, 'evidence_refs', []) or []))}) for item in output.hypotheses],
        'evidence_gaps': [item.model_copy(update={'needed_refs': _filter_refs(list(getattr(item, 'needed_refs', []) or []))}) for item in output.evidence_gaps],
        'candidate_comparisons': [item.model_copy(update={'evidence_refs': _filter_refs(list(getattr(item, 'evidence_refs', []) or []))}) for item in output.candidate_comparisons],
        'rejected_candidates': [item.model_copy(update={'evidence_refs': _filter_refs(list(getattr(item, 'evidence_refs', []) or []))}) for item in output.rejected_candidates],
        'contradictions': [item.model_copy(update={'evidence_refs': _filter_refs(list(getattr(item, 'evidence_refs', []) or []))}) for item in output.contradictions],
        'fail_closed_reasons': [item.model_copy(update={'related_refs': _filter_fail_closed_related_refs(list(getattr(item, 'related_refs', []) or []))}) for item in output.fail_closed_reasons],
        'issue_responses': [item.model_copy(update={'related_refs': _filter_refs(list(getattr(item, 'related_refs', []) or []))}) for item in output.issue_responses],
    }
    updated = output.model_copy(update=updates) if changed else output
    return updated, issues


def _compact_fail_closed_related_refs(values: list[str], *, max_refs: int = 4) -> list[str]:
    local_refs: list[str] = []
    target_refs: list[str] = []
    other_refs: list[str] = []
    for ref in list(dict.fromkeys(ref for ref in values if ref)):
        if ref.startswith(('LF', 'LS', 'LC')):
            local_refs.append(ref)
        elif ref.startswith(('BE', 'BES', 'BS', 'BR')):
            target_refs.append(ref)
        else:
            other_refs.append(ref)
    compact = [*local_refs[:2], *target_refs[:1], *other_refs[:1]]
    if len(compact) < max_refs:
        compact.extend(ref for ref in [*local_refs[3:], *target_refs[2:], *other_refs[1:]] if ref not in compact)
    return compact[:max_refs]


def _issue(ref: str, issue_code: str, message: str, *, related_refs: list[str] | None = None) -> VerifierIssue:
    return VerifierIssue(ref=ref, issue_code=issue_code, severity='blocked', message=message, related_refs=[ref for ref in list(related_refs or []) if ref])


def _tmdb_like(ref: str) -> bool:
    low = ref.lower()
    return low.startswith('tmdb') or 'tmdb' in low


def _visible(dossier: CaseDossier) -> set[str]:
    return (
        set(dossier.visible_refs.local_file_refs)
        | set(dossier.visible_refs.local_cluster_refs)
        | {card.ref for card in getattr(dossier, 'local_span_cards', []) or [] if getattr(card, 'ref', '')}
        | set(dossier.visible_refs.bangumi_subject_refs)
        | set(dossier.visible_refs.bangumi_relation_refs)
        | set(dossier.visible_refs.bangumi_group_refs)
        | set(dossier.visible_refs.bangumi_item_refs)
        | set(dossier.visible_refs.query_refs)
        | set(dossier.visible_refs.target_refs)
        | {card.ref for card in getattr(dossier, 'bangumi_span_cards', []) or [] if getattr(card, 'ref', '')}
        | _detail_surface_refs(dossier)
    )


def _visible_cards(dossier: CaseDossier) -> set[str]:
    return _visible(dossier)


def _hypothesis_refs(dossier: CaseDossier, output: CaseJudgeOutput) -> set[str]:
    return {h.ref for h in getattr(dossier, 'previous_hypotheses', []) or []} | {h.ref for h in output.hypotheses}


def _detail_surface_refs(dossier: CaseDossier) -> set[str]:
    refs: set[str] = set()
    for attr in ('detailed_card_refs', 'assignable_target_refs', 'seen_detail_refs'):
        refs |= set(getattr(dossier, attr, []) or [])
    return refs


def _prior_evidence_refs(dossier: CaseDossier) -> set[str]:
    refs: set[str] = set()
    for batch in getattr(dossier, 'previous_evidence_results', []) or []:
        batch_ref = getattr(batch, 'batch_ref', '')
        if batch_ref:
            refs.add(batch_ref)
        for result in getattr(batch, 'request_results', []) or []:
            request_ref = getattr(result, 'request_ref', '')
            if request_ref:
                refs.add(request_ref)
            refs.update(ref for ref in (getattr(result, 'response_refs', []) or []) if ref)
    return refs


def _detail_surface_summary(dossier: CaseDossier) -> dict[str, list[str]]:
    return {
        'detailed_card_refs': list(getattr(dossier, 'detailed_card_refs', []) or []),
        'assignable_target_refs': list(getattr(dossier, 'assignable_target_refs', []) or []),
        'seen_detail_refs': list(getattr(dossier, 'seen_detail_refs', []) or []),
    }


def _assignable_target_refs(dossier: CaseDossier) -> set[str]:
    return set(getattr(dossier, 'assignable_target_refs', []) or []) | set(getattr(dossier, 'detailed_card_refs', []) or []) | set(getattr(dossier, 'seen_detail_refs', []) or [])


def _output_refs(output: CaseJudgeOutput) -> set[str]:
    return (
        {f.request_ref for f in output.evidence_requests}
        | {f.ref for f in output.findings}
        | {h.ref for h in output.hypotheses}
        | {g.ref for g in output.evidence_gaps}
        | {c.ref for c in output.candidate_comparisons}
        | {c.ref for c in output.contradictions}
        | {r.ref for r in output.rejected_candidates}
        | {d.ref for d in output.local_partition_decisions}
        | {a.ref for a in output.assignment_intents}
        | {r.ref for r in output.fail_closed_reasons}
        | {r.ref for r in output.issue_responses}
        | {s.ref for s in output.self_checks}
    )


def _longest_consecutive_span(refs: list[str]) -> tuple[int, str]:
    parsed: list[tuple[str, int]] = []
    for ref in refs:
        m = re.match(r'^(BE|F)(\d+)$', ref)
        if not m:
            return 0, ''
        parsed.append((m.group(1), int(m.group(2))))
    if not parsed:
        return 0, ''
    prefix = parsed[0][0]
    if any(p != prefix for p, _ in parsed):
        return 0, ''
    nums = sorted(set(n for _, n in parsed))
    best = run = 1
    best_end = nums[0]
    for left, right in zip(nums, nums[1:]):
        if right == left + 1:
            run += 1
        else:
            run = 1
        if run > best:
            best = run
            best_end = right
    if best < 2:
        return 0, ''
    return best, f'{prefix}{best_end - best + 1}..{prefix}{best_end}'


def _output_budget_issues(output: CaseJudgeOutput) -> list[VerifierIssue]:
    issues: list[VerifierIssue] = []
    non_assignment_total = 0
    for list_name, refs in iter_case_judge_ref_lists(output):
        clean_refs = [ref for ref in refs if ref]
        if len(clean_refs) > 20:
            issues.append(_issue(list_name, 'output_budget_exceeded', f'{list_name} exceeds per-list output budget'))
        if not list_name.startswith('assignment_intents.'):
            non_assignment_total += len(clean_refs)
        span, ref_range = _longest_consecutive_span(clean_refs)
        if span >= 30:
            issues.append(_issue(list_name, 'output_budget_exceeded', f'{list_name} contains oversized consecutive ref span {ref_range}'))
    if non_assignment_total > 50:
        issues.append(_issue('', 'output_budget_exceeded', 'non-assignment refs exceed output budget'))
    return issues


def _oversized_output_summary(output: CaseJudgeOutput) -> str:
    issues = _output_budget_issues(output)
    if not issues:
        return ''
    details = [f'{issue.ref or "output"}: {issue.message}' for issue in issues if issue.issue_code == 'output_budget_exceeded']
    return '; '.join(dict.fromkeys(details))


def _is_mapping_draft_supplemental_assignment(assignment) -> bool:
    reason = str(getattr(assignment, 'reason', '') or '')
    return (
        str(getattr(assignment, 'target_ref', '') or '') == 'UNALIGNED'
        and reason.startswith('mapping_draft:')
        and ':supplemental:' in reason
    )


def _ensure_known_refs(issues: list[VerifierIssue], *, ref: str, values: list[str], allowed: set[str], issue_code: str, message: str) -> None:
    if any(value and value not in allowed for value in values):
        issues.append(_issue(ref, issue_code, message))


def verify_judge_output(dossier: CaseDossier, output: CaseJudgeOutput) -> CaseVerifierResult:
    issues: list[VerifierIssue] = []
    output, sanitize_issues = _sanitize_fail_closed_aux_refs(output, dossier)
    issues.extend(sanitize_issues)
    expanded_assignment_intents, bulk_issues = expand_bulk_assignment_intents(dossier, output)
    issues.extend(bulk_issues)
    visible = _visible(dossier)
    detail_refs = _detail_surface_refs(dossier)
    output_refs = _output_refs(output)
    hypothesis_refs = _hypothesis_refs(dossier, output)
    prior_evidence_refs = _prior_evidence_refs(dossier)
    known_refs = visible | detail_refs | output_refs | hypothesis_refs | prior_evidence_refs

    issues.extend(_output_budget_issues(output))

    effective_assignment_intents = list(output.assignment_intents) + list(expanded_assignment_intents)

    if output.model_dump().get('case_id', '') not in ('', dossier.header.case_id):
        issues.append(_issue('', 'case_id_mismatch', 'case_id must match dossier header case_id'))

    if output.action == 'request_evidence':
        if not output.evidence_requests:
            issues.append(_issue('', 'action_inconsistent', 'request_evidence requires evidence_requests'))
        if effective_assignment_intents:
            issues.append(_issue('', 'action_inconsistent', 'request_evidence must not contain assignments'))
    elif output.action == 'submit_verdict':
        if not dossier.contract.main_file_refs and any(file_card.is_main for file_card in dossier.local_files):
            issues.append(_issue('', 'coverage_error', 'contract main_file_refs missing for executable main files'))
        if output.evidence_requests:
            issues.append(_issue('', 'action_inconsistent', 'submit_verdict must not contain evidence_requests'))
        if not effective_assignment_intents:
            issues.append(_issue('', 'action_inconsistent', 'submit_verdict requires assignments'))
        counts = Counter(a.file_ref for a in effective_assignment_intents)
        if set(counts) != set(dossier.contract.main_file_refs):
            for main_ref in dossier.contract.main_file_refs:
                if counts.get(main_ref, 0) == 0:
                    issues.append(_issue(main_ref, 'coverage_error', 'each main file must appear exactly once'))
        for main_ref, count in counts.items():
            if main_ref in dossier.contract.main_file_refs and count != 1:
                issues.append(_issue(main_ref, 'coverage_error', 'each main file must appear exactly once'))
        for assignment in effective_assignment_intents:
            if assignment.file_ref not in dossier.contract.allowed_file_refs:
                issues.append(_issue(assignment.ref, 'coverage_error', 'assignment file_ref must be allowed'))
            if assignment.file_ref not in dossier.contract.main_file_refs:
                issues.append(_issue(assignment.ref, 'coverage_error', 'assignment file_ref must belong to main_file_refs'))
            if assignment.file_ref in dossier.contract.supplemental_file_refs:
                issues.append(_issue(assignment.ref, 'coverage_error', 'supplemental file refs are not allowed in assignment_intents'))
        if dossier.contract.main_file_refs and not effective_assignment_intents:
            issues.append(_issue('', 'coverage_error', 'accepted verdict requires assignments for main_file_refs'))
    elif output.action == 'fail_closed':
        if not output.fail_closed_reasons:
            issues.append(_issue('', 'action_inconsistent', 'fail_closed requires fail_closed_reasons'))
        if output.assignment_intents or output.evidence_requests:
            issues.append(_issue('', 'action_inconsistent', 'fail_closed must not contain assignments or evidence_requests'))
    elif output.action == 'issue_response':
        if not output.issue_responses:
            issues.append(_issue('', 'action_inconsistent', 'issue_response requires issue_responses'))
        if not (output.assignment_intents or output.fail_closed_reasons):
            issues.append(_issue('', 'action_inconsistent', 'issue_response requires assignments or fail_closed_reasons'))

    if output.action == 'submit_verdict':
        unaligned_assignments = [
            a for a in effective_assignment_intents
            if a.target_ref == 'UNALIGNED' and not _is_mapping_draft_supplemental_assignment(a)
        ]
        if unaligned_assignments:
            issues.append(_issue(
                unaligned_assignments[0].ref,
                'unaligned_not_accepted',
                'accepted verdicts must not contain UNALIGNED assignment targets; use fail_closed or mapping draft supplemental accounting before submit_verdict',
                related_refs=[a.file_ref for a in unaligned_assignments[:8]],
            ))

    def _assignment_target_refs(assignment: AssignmentIntent) -> list[str]:
        target_refs = [
            str(ref or '')
            for ref in list(getattr(assignment, 'target_refs', []) or [])
            if str(ref or '')
        ]
        if target_refs:
            return list(dict.fromkeys(target_refs))
        target_ref = str(getattr(assignment, 'target_ref', '') or '')
        return [target_ref] if target_ref and target_ref != 'UNALIGNED' else []

    targets = Counter(
        target_ref
        for assignment in effective_assignment_intents
        for target_ref in _assignment_target_refs(assignment)
    )
    assignable_refs = _assignable_target_refs(dossier)
    for target, count in targets.items():
        if count > 1:
            issues.append(_issue(target, 'duplicate_target', 'duplicate non-UNALIGNED target'))
        if target.startswith(('BS', 'BR')) or _tmdb_like(target):
            issues.append(_issue(target, 'invalid_target', 'target must not be BS/BR/TMDB-like'))
        if target == 'UNALIGNED':
            continue
        if not target.startswith('BE') or (target not in visible and target not in detail_refs):
            issues.append(_issue(target, 'invalid_target', 'target must be visible BE*'))
        elif target not in assignable_refs:
            issues.append(_issue(target, 'invalid_target', 'assignment target must be in assignable_target_refs'))

    finding_refs = {f.ref for f in output.findings}
    for a in effective_assignment_intents:
        is_expanded_bulk = ':' in a.ref and str(getattr(a, 'reason', '')).startswith('bulk:')
        assignment_target_refs = _assignment_target_refs(a)
        if getattr(a, 'target_refs', None):
            if a.target_ref not in assignment_target_refs:
                issues.append(_issue(a.ref, 'invalid_target', 'target_ref must be included in target_refs for composite assignments'))
        if not a.support_finding_refs:
            issues.append(_issue(a.ref, 'missing_support', 'support_finding_refs required'))
        required_support_refs = [a.file_ref, *assignment_target_refs]
        if (
            not a.support_card_refs
            or any(ref and ref not in a.support_card_refs for ref in required_support_refs)
        ):
            message = (
                'support_card_refs must include file_ref and all target refs'
                if len(assignment_target_refs) > 1
                else 'support_card_refs must include file_ref and target_ref'
            )
            issues.append(_issue(a.ref, 'missing_support', message))
        if any(ref not in finding_refs for ref in a.support_finding_refs):
            issues.append(_issue(a.ref, 'missing_support', 'support_finding_refs must reference output.findings only'))
        support_card_refs = set(a.support_card_refs)
        if any(ref.startswith('H') for ref in support_card_refs):
            issues.append(_issue(a.ref, 'invalid_ref_role', 'hypothesis refs may not be used as assignment support_card_refs'))
        if not is_expanded_bulk and any(ref not in _visible_cards(dossier) for ref in support_card_refs):
            issues.append(_issue(a.ref, 'missing_support', 'support_card_refs must reference visible dossier cards only'))
        if any(ref in finding_refs for ref in a.support_card_refs):
            issues.append(_issue(a.ref, 'missing_support', 'support_card_refs must not include finding refs'))
        if any(ref in (_visible_cards(dossier) - finding_refs) for ref in a.support_finding_refs):
            issues.append(_issue(a.ref, 'missing_support', 'support_finding_refs must not include visible card refs'))
        if assignment_target_refs and any(ref.startswith('BE') and ref not in a.support_card_refs for ref in assignment_target_refs):
            issues.append(_issue(a.ref, 'missing_support', 'BE targets require target refs in support_card_refs'))
        if a.target_ref == 'UNALIGNED' and 'UNALIGNED' in a.support_card_refs:
            issues.append(_issue(a.ref, 'missing_support', 'UNALIGNED target does not require UNALIGNED support card'))
        for target_ref in assignment_target_refs:
            if target_ref not in assignable_refs:
                issues.append(_issue(a.ref, 'invalid_target', 'assignment target must be in assignable_target_refs'))

    if expanded_assignment_intents:
        expanded_file_counts = Counter(a.file_ref for a in expanded_assignment_intents)
        for file_ref, count in expanded_file_counts.items():
            if count > 1:
                issues.append(_issue(file_ref, 'coverage_error', 'bulk assignments must not overlap across local spans'))
        expanded_target_counts = Counter(a.target_ref for a in expanded_assignment_intents if a.target_ref != 'UNALIGNED')
        for target_ref, count in expanded_target_counts.items():
            if count > 1:
                issues.append(_issue(target_ref, 'duplicate_target', 'bulk assignments must not overlap across target spans'))

    if output.action == 'submit_verdict':
        explicit_files = {a.file_ref for a in output.assignment_intents}
        bulk_files = {a.file_ref for a in expanded_assignment_intents}
        if explicit_files & bulk_files:
            issues.append(_issue('', 'coverage_error', 'explicit assignments and bulk assignments must not overlap'))

    if output.action != 'fail_closed':
        for hypothesis in output.hypotheses:
            _ensure_known_refs(issues, ref=hypothesis.ref, values=getattr(hypothesis, 'evidence_refs', []), allowed=known_refs, issue_code='unknown_ref', message='hypothesis evidence_refs must reference known refs')
        for finding in output.findings:
            _ensure_known_refs(issues, ref=finding.ref, values=getattr(finding, 'evidence_refs', []), allowed=known_refs, issue_code='unknown_ref', message='finding evidence_refs must reference known refs')
        for rejected in output.rejected_candidates:
            _ensure_known_refs(issues, ref=rejected.ref, values=getattr(rejected, 'evidence_refs', []), allowed=known_refs, issue_code='unknown_ref', message='rejected candidate evidence_refs must reference known refs')
            if rejected.candidate_ref and rejected.candidate_ref not in known_refs:
                issues.append(_issue(rejected.ref, 'unknown_ref', 'rejected candidate candidate_ref must reference known refs'))
        for comparison in output.candidate_comparisons:
            _ensure_known_refs(issues, ref=comparison.ref, values=getattr(comparison, 'evidence_refs', []), allowed=known_refs, issue_code='unknown_ref', message='candidate comparison evidence_refs must reference known refs')
        for contradiction in output.contradictions:
            _ensure_known_refs(issues, ref=contradiction.ref, values=getattr(contradiction, 'evidence_refs', []), allowed=known_refs, issue_code='unknown_ref', message='contradiction evidence_refs must reference known refs')
        # evidence_gaps.needed_refs is a requested/sampled evidence surface, not
        # executable support. Keep its size/span bounded via _output_budget_issues
        # but do not reject a verdict just because a desired ref is not currently
        # hydrated.
    for response in output.issue_responses:
        if output.action != 'fail_closed':
            _ensure_known_refs(issues, ref=response.ref, values=getattr(response, 'related_refs', []), allowed=known_refs, issue_code='unknown_ref', message='issue response related_refs must reference known refs')

    for sc in output.self_checks:
        if not sc.passed:
            if output.action == 'fail_closed' and sc.check_kind == 'coverage' and not output.assignment_intents:
                issues.append(_issue(sc.ref, 'self_check_misuse', 'coverage self_check cannot fail only because fail_closed has no assignments'))
            else:
                issues.append(_issue(sc.ref, 'self_check_failed', 'self_check blocked'))

    allowed_related_refs = visible | detail_refs | prior_evidence_refs | output_refs
    for ref in known_refs:
        if ref and ref not in visible and ref not in {a.ref for a in effective_assignment_intents} and ref not in {r.ref for r in output.fail_closed_reasons} and ref not in {r.ref for r in output.issue_responses}:
            if ref.startswith(('BE', 'BS', 'BR')) or _tmdb_like(ref):
                issues.append(_issue(ref, 'unknown_ref', 'unknown visible ref'))

    output = _sanitize_fail_closed_aux_refs(output, dossier)[0] if output.action == 'fail_closed' else output
    for reason in output.fail_closed_reasons:
        if any(ref not in allowed_related_refs for ref in reason.related_refs):
            issues.append(_issue(reason.ref, 'unknown_ref', 'fail_closed related_refs must be visible dossier refs or output refs'))

    blocking_issues = [issue for issue in issues if getattr(issue, 'severity', 'blocked') == 'blocked']
    return CaseVerifierResult(passed=not blocking_issues, issues=issues)


def verify_mapping_draft_accounting(dossier: CaseDossier, draft: MappingDraft) -> CaseVerifierResult:
    accounting = compute_mapping_draft_accounting(draft, dossier)
    issues: list[VerifierIssue] = []

    seen_local_refs: set[str] = set()
    seen_target_refs: set[str] = set()
    seen_target_row_refs: dict[str, str] = {}
    bangumi_span_refs = {card.ref for card in getattr(dossier, 'bangumi_span_cards', []) or [] if getattr(card, 'ref', '')}
    bangumi_item_refs = {card.ref for card in getattr(dossier, 'bangumi_items', []) or [] if getattr(card, 'ref', '')}
    local_file_refs = {card.ref for card in getattr(dossier, 'local_files', []) or [] if getattr(card, 'ref', '')}
    local_spans = {card.ref: card for card in getattr(dossier, 'local_span_cards', []) or [] if getattr(card, 'ref', '')}
    visible_item_refs = set(getattr(getattr(dossier, 'visible_refs', None), 'bangumi_item_refs', []) or []) | set(getattr(getattr(dossier, 'visible_refs', None), 'target_refs', []) or []) | set(getattr(dossier, 'assignable_target_refs', []) or []) | set(getattr(dossier, 'detailed_card_refs', []) or []) | set(getattr(dossier, 'seen_detail_refs', []) or [])
    for row in draft.rows:
        if row.disposition == 'map_to_bangumi':
            span_by_index_target = row.mapping_mode == 'span_by_index' and row.selected_target_ref in bangumi_span_refs
            explicit_item_target = row.mapping_mode == 'explicit' and row.selected_target_ref in (bangumi_item_refs | visible_item_refs)
            if not span_by_index_target and not explicit_item_target:
                issues.append(_issue(row.row_ref, 'invalid_mapping_mode', 'mapped rows must be expandable to a visible Bangumi span or item target'))
            if row.mapping_mode == 'explicit' and not explicit_item_target:
                issues.append(_issue(row.row_ref, 'invalid_target', 'explicit mapped rows require a visible Bangumi item target'))
            if row.mapping_mode == 'explicit':
                if row.local_ref in local_file_refs:
                    local_count = 1
                else:
                    span = local_spans.get(row.local_ref)
                    local_count = int(getattr(span, 'file_ref_count', 0) or len(getattr(span, 'file_refs', []) or [])) if span is not None else 0
                if local_count != 1:
                    issues.append(_issue(row.row_ref, 'invalid_explicit_multi_file_mapping', 'explicit mapped rows require exactly one local file'))
                support_refs = set(row.support_refs or [])
                if not row.support_refs or row.local_ref not in support_refs or row.selected_target_ref not in support_refs:
                    issues.append(_issue(row.row_ref, 'missing_support_refs', 'explicit mapped rows require support_refs containing local_ref and selected_target_ref'))
            if row.selected_target_ref and not span_by_index_target:
                if row.selected_target_ref in seen_target_refs:
                    issues.append(_issue(
                        row.row_ref,
                        'duplicate_target',
                        'duplicate mapped target',
                        related_refs=[seen_target_row_refs.get(row.selected_target_ref, ''), row.row_ref, row.selected_target_ref],
                    ))
                seen_target_refs.add(row.selected_target_ref)
                seen_target_row_refs.setdefault(row.selected_target_ref, row.row_ref)
        elif row.disposition == 'non_bangumi_or_supplemental':
            if row.selected_target_ref or row.selected_target_kind != 'none':
                issues.append(_issue(row.row_ref, 'invalid_target', 'supplemental rows must not carry target assignment'))
            if row.reason_kind not in ALLOWED_SUPPLEMENTAL_REASON_KINDS:
                issues.append(_issue(row.row_ref, 'invalid_reason_kind', 'supplemental rows require allowlisted reason_kind'))
            if not row.support_refs:
                issues.append(_issue(row.row_ref, 'missing_support_refs', 'supplemental rows require support_refs'))
            issues.extend(supplemental_row_policy_issues(dossier, row))
        elif row.disposition == 'needs_more_evidence':
            issues.append(_issue(row.row_ref, 'not_ready', 'needs_more_evidence rows keep accounting unresolved'))
        elif row.disposition == 'unaligned_fail_closed':
            issues.append(_issue(row.row_ref, 'fail_closed', 'unaligned rows keep accounting unresolved'))
        else:
            issues.append(_issue(row.row_ref, 'not_ready', 'open rows are not accounted for'))

        if row.local_ref in seen_local_refs:
            issues.append(_issue(row.row_ref, 'duplicate_local_ref', 'duplicate local disposition'))
        seen_local_refs.add(row.local_ref)

    if accounting.accounted_for_count != accounting.main_file_count:
        issues.append(_issue('accounting', 'coverage_error', 'accounted_for_count must equal main_file_count'))
    if accounting.unresolved_count != 0:
        issues.append(_issue('accounting', 'not_ready', 'unresolved_count must be 0 for accepted readiness'))
    if not accounting.accepted_accounting_ready:
        issues.append(_issue('accounting', 'not_ready', 'mapping draft accounting is not ready'))

    if issues:
        return CaseVerifierResult(passed=False, issues=issues)

    expanded, expansion_issues = expand_mapping_draft(dossier, draft)
    return CaseVerifierResult(passed=not expansion_issues, issues=expansion_issues)
