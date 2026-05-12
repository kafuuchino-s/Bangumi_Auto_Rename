from __future__ import annotations

from collections import Counter
from typing import Any

from .models import BangumiSpanCard, CaseDossier, LocalSpanCard, MappingDraft, MappingDraftCoverageSummary, MappingDraftPatch, MappingDraftRow, VerifierIssue
from .supplemental_policy import ALLOWED_SUPPLEMENTAL_REASON_KINDS, supplemental_row_policy_issues


_EXCLUSION_REASON_KINDS = ALLOWED_SUPPLEMENTAL_REASON_KINDS
_NEEDS_EVIDENCE_REASON_KINDS = {
    'missing_target_span', 'missing_target_detail', 'missing_local_detail', 'ambiguous_candidate', 'insufficient_span_evidence', 'budget_exhausted',
}
_UNALIGNED_REASON_KINDS = {
    'no_legal_target', 'ambiguous_ownership', 'special_regular_conflict', 'coverage_gap_unresolved', 'insufficient_evidence',
}


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    return list(dict.fromkeys(v for v in values if v))


def _issue(ref: str, issue_code: str, message: str, *, related_refs: list[str] | None = None) -> VerifierIssue:
    return VerifierIssue(ref=ref, issue_code=issue_code, severity='blocked', message=message, related_refs=list(related_refs or []))


def _visible_refs(dossier: CaseDossier) -> set[str]:
    refs: set[str] = set()
    refs.update(card.ref for card in getattr(dossier, 'local_files', []) or [])
    refs.update(card.ref for card in getattr(dossier, 'bangumi_items', []) or [])
    refs.update(card.ref for card in getattr(dossier, 'local_span_cards', []) or [])
    refs.update(card.ref for card in getattr(dossier, 'bangumi_span_cards', []) or [])
    refs.update(card.ref for card in getattr(dossier, 'query_cards', []) or [])
    refs.update(card.ref for card in getattr(dossier, 'local_clusters', []) or [])
    refs.update(card.ref for card in getattr(dossier, 'bangumi_subjects', []) or [])
    refs.update(card.ref for card in getattr(dossier, 'bangumi_relations', []) or [])
    refs.update(card.ref for card in getattr(dossier, 'bangumi_groups', []) or [])
    refs.update(getattr(dossier, 'assignable_target_refs', []) or [])
    refs.update(getattr(dossier, 'detailed_card_refs', []) or [])
    refs.update(getattr(dossier, 'seen_detail_refs', []) or [])
    return {ref for ref in refs if ref}


def build_initial_mapping_draft(dossier: CaseDossier) -> MappingDraft:
    bangumi_span_cards = [card for card in getattr(dossier, 'bangumi_span_cards', []) or [] if bool(getattr(card, 'detail_equivalent', False))]
    detail_target_refs = _dedupe_preserve_order([card.ref for card in bangumi_span_cards])
    child_local_spans = [card for card in list(getattr(dossier, 'local_span_cards', []) or []) if str(getattr(card, 'span_scope', '') or '') != 'package']
    rows: list[MappingDraftRow] = []
    for card in child_local_spans:
        rows.append(MappingDraftRow(
            row_ref=f'MDR{len(rows) + 1}',
            local_ref=card.ref,
            local_ref_kind='span',
            candidate_target_refs=list(detail_target_refs),
        ))
    if not rows:
        package_span = next((card for card in list(getattr(dossier, 'local_span_cards', []) or []) if str(getattr(card, 'span_scope', '') or '') == 'package'), None)
        if package_span is not None:
            rows.append(MappingDraftRow(
                row_ref='MDR1',
                local_ref=package_span.ref,
                local_ref_kind='span',
                candidate_target_refs=list(detail_target_refs),
            ))
    return MappingDraft(rows=rows, version=1)


def summarize_mapping_draft_coverage(dossier: CaseDossier, draft: MappingDraft) -> MappingDraftCoverageSummary:
    coverage = compute_local_span_partition_coverage(dossier, draft)
    return MappingDraftCoverageSummary(
        main_file_count=coverage['main_file_count'],
        covered_main_file_count=coverage['covered_main_file_count'],
        missing_main_file_count=coverage['missing_main_file_count'],
        overlap_count=coverage['overlap_count'],
        partition_complete=coverage['partition_complete'],
    )


def compute_local_span_partition_coverage(dossier_or_workspace: CaseDossier, draft: MappingDraft | None = None) -> dict[str, int | bool]:
    dossier = dossier_or_workspace
    contract = getattr(dossier, 'contract', None)
    contract_main_refs = list(getattr(contract, 'main_file_refs', []) or [])
    main_files = [card for card in getattr(dossier, 'local_files', []) or [] if getattr(card, 'is_main', False) and getattr(card, 'ref', '')]
    main_file_refs = contract_main_refs or [card.ref for card in main_files]
    local_spans = [card for card in getattr(dossier, 'local_span_cards', []) or [] if getattr(card, 'ref', '')]
    child_spans = [card for card in local_spans if str(getattr(card, 'span_scope', '') or '') != 'package']
    span_main_file_counts: Counter[str] = Counter()
    for span in child_spans:
        refs = [ref for ref in list(getattr(span, 'file_refs', []) or []) if ref in main_file_refs]
        span_main_file_counts.update(_dedupe_preserve_order(refs))
    span_covered_main_file_refs = set(span_main_file_counts)
    span_overlap_count = sum(count - 1 for count in span_main_file_counts.values() if count > 1)
    span_covered_main_file_count = len(span_covered_main_file_refs)
    span_missing_main_file_count = max(len(main_file_refs) - span_covered_main_file_count, 0)
    draft_rows = list((draft or getattr(dossier, 'mapping_draft', None) or MappingDraft()).rows)
    draft_covered_main_file_counts: Counter[str] = Counter()
    for row in draft_rows:
        span = next((card for card in local_spans if card.ref == row.local_ref), None)
        if span is None:
            refs = [row.local_ref] if row.local_ref in main_file_refs else []
        else:
            refs = [ref for ref in list(getattr(span, 'file_refs', []) or []) if ref in main_file_refs]
            if row.local_ref in main_file_refs and row.local_ref not in refs:
                refs.append(row.local_ref)
        draft_covered_main_file_counts.update(_dedupe_preserve_order(refs))
    draft_covered_main_file_refs = set(draft_covered_main_file_counts)
    draft_overlap_count = sum(count - 1 for count in draft_covered_main_file_counts.values() if count > 1)
    draft_covered_main_file_count = len(draft_covered_main_file_refs)
    draft_missing_main_file_count = max(len(main_file_refs) - draft_covered_main_file_count, 0)
    has_draft_rows = bool(draft_rows)
    coverage_source = 'mapping_draft' if has_draft_rows else 'local_spans'
    covered_main_file_count = draft_covered_main_file_count if has_draft_rows else span_covered_main_file_count
    missing_main_file_count = draft_missing_main_file_count if has_draft_rows else span_missing_main_file_count
    overlap_count = draft_overlap_count if has_draft_rows else span_overlap_count
    return {
        'main_file_count': len(main_file_refs),
        'local_child_span_count': len(child_spans),
        'covered_main_file_count': covered_main_file_count,
        'missing_main_file_count': missing_main_file_count,
        'overlap_count': overlap_count,
        'partition_complete': bool(main_file_refs) and missing_main_file_count == 0 and overlap_count == 0,
        'coverage_source': coverage_source,
        'span_covered_main_file_count': span_covered_main_file_count,
        'span_missing_main_file_count': span_missing_main_file_count,
        'span_overlap_count': span_overlap_count,
        'span_partition_complete': bool(main_file_refs) and span_missing_main_file_count == 0 and span_overlap_count == 0,
        'mapping_draft_row_count': len(draft_rows),
        'mapping_draft_covered_main_count': draft_covered_main_file_count,
        'mapping_draft_missing_main_count': draft_missing_main_file_count,
        'mapping_draft_overlap_count': draft_overlap_count,
        'mapping_draft_partition_complete': bool(main_file_refs) and draft_missing_main_file_count == 0 and draft_overlap_count == 0,
    }


def compact_mapping_draft(draft: MappingDraft, dossier: CaseDossier | None = None) -> dict[str, Any]:
    status_counts = Counter(row.status for row in draft.rows)
    mapping_mode_counts = Counter(row.mapping_mode for row in draft.rows)
    candidate_counts = [len(row.candidate_target_refs) for row in draft.rows]
    coverage_summary = summarize_mapping_draft_coverage(dossier, draft) if dossier is not None else None
    accounting = compute_mapping_draft_accounting(draft, dossier) if dossier is not None else None
    return {
        'draft_ref': draft.draft_ref,
        'version': draft.version,
        'row_count': len(draft.rows),
        'status_counts': dict(status_counts),
        'mapping_mode_counts': dict(mapping_mode_counts),
        'candidate_target_ref_count_total': sum(candidate_counts),
        'candidate_target_ref_count_max': max(candidate_counts) if candidate_counts else 0,
        'row_ref_samples': [row.row_ref for row in draft.rows[:4]],
        'local_ref_samples': [row.local_ref for row in draft.rows[:4]],
        'coverage_summary': coverage_summary.model_dump(mode='json') if coverage_summary is not None else None,
        'accounting_summary': accounting.model_dump(mode='json') if accounting is not None else None,
    }


def _known_local_span_refs(dossier: CaseDossier) -> set[str]:
    return {card.ref for card in getattr(dossier, 'local_span_cards', []) or [] if getattr(card, 'ref', '') and str(getattr(card, 'span_scope', '') or '') != 'package'}


def _bangumi_detail_span_refs(dossier: CaseDossier) -> set[str]:
    return {card.ref for card in getattr(dossier, 'bangumi_span_cards', []) or [] if getattr(card, 'ref', '') and bool(getattr(card, 'detail_equivalent', False))}


def _visible_bangumi_item_refs(dossier: CaseDossier) -> set[str]:
    refs = {card.ref for card in getattr(dossier, 'bangumi_items', []) or [] if getattr(card, 'ref', '')}
    refs.update(getattr(getattr(dossier, 'visible_refs', None), 'bangumi_item_refs', []) or [])
    refs.update(getattr(getattr(dossier, 'visible_refs', None), 'target_refs', []) or [])
    refs.update(getattr(dossier, 'assignable_target_refs', []) or [])
    refs.update(getattr(dossier, 'detailed_card_refs', []) or [])
    refs.update(getattr(dossier, 'seen_detail_refs', []) or [])
    return {ref for ref in refs if ref and ref.startswith('BE') and not ref.startswith('BES')}


def _row_by_local_ref(draft: MappingDraft, local_ref: str) -> MappingDraftRow | None:
    for row in draft.rows:
        if row.local_ref == local_ref:
            return row
    return None


def _row_ref_to_local_ref(draft: MappingDraft, ref: str) -> str:
    for row in draft.rows:
        if ref == row.row_ref:
            return row.local_ref
    return ref


def normalize_mapping_patch_op(patch: MappingDraftPatch) -> MappingDraftPatch:
    normalized = patch.model_copy(deep=True)
    if normalized.op in ('propose_span_mapping', 'propose_explicit_mapping'):
        if not normalized.mapping_mode or normalized.mapping_mode == 'unresolved':
            normalized.mapping_mode = 'span_by_index' if normalized.op == 'propose_span_mapping' else 'explicit'
        normalized.op = 'map_to_bangumi'
    elif normalized.op == 'mark_unresolved':
        normalized.op = 'needs_more_evidence'
    return normalized


def _mapping_patch_effective_op(patch: MappingDraftPatch) -> str:
    return normalize_mapping_patch_op(patch).op


def _canonicalize_patch_for_dossier(patch: MappingDraftPatch, dossier: CaseDossier, draft: MappingDraft | None = None) -> MappingDraftPatch:
    normalized = normalize_mapping_patch_op(patch)
    effective_draft = draft or getattr(dossier, 'mapping_draft', None)
    if effective_draft is not None:
        normalized = normalized.model_copy(update={
            'local_ref': _row_ref_to_local_ref(effective_draft, normalized.local_ref),
            'support_refs': _dedupe_preserve_order([_row_ref_to_local_ref(effective_draft, ref) for ref in list(normalized.support_refs or [])]),
        })
    bangumi_span_refs = _bangumi_detail_span_refs(dossier)
    bangumi_item_refs = _visible_bangumi_item_refs(dossier)
    if normalized.op == 'map_to_bangumi' and normalized.target_ref in bangumi_span_refs and (not normalized.target_span_ref or normalized.target_span_ref == normalized.target_ref):
        normalized = normalized.model_copy(update={
            'target_span_ref': normalized.target_ref,
            'target_ref': '',
            'mapping_mode': 'span_by_index',
        })
    if normalized.op == 'map_to_bangumi' and normalized.target_span_ref in bangumi_span_refs and (not normalized.target_ref or normalized.target_ref == normalized.target_span_ref):
        normalized = normalized.model_copy(update={
            'target_ref': '',
            'mapping_mode': 'span_by_index',
        })
    if normalized.op == 'map_to_bangumi' and normalized.target_ref in bangumi_item_refs and not normalized.target_span_ref:
        normalized = normalized.model_copy(update={'mapping_mode': 'explicit'})
    if normalized.op == 'map_to_bangumi' and normalized.target_span_ref in bangumi_item_refs and normalized.target_span_ref not in bangumi_span_refs and not normalized.target_ref:
        normalized = normalized.model_copy(update={
            'target_ref': normalized.target_span_ref,
            'target_span_ref': '',
            'mapping_mode': 'explicit',
        })
    if normalized.op == 'map_to_bangumi' and normalized.target_span_ref and normalized.mapping_mode == 'unresolved':
        normalized = normalized.model_copy(update={'mapping_mode': 'span_by_index'})
    if normalized.op == 'map_to_bangumi':
        target = normalized.target_span_ref or normalized.target_ref
        normalized = normalized.model_copy(update={
            'support_refs': _dedupe_preserve_order([*list(normalized.support_refs or []), normalized.local_ref, target]),
        })
    if normalized.op == 'needs_more_evidence' and normalized.reason_kind not in _NEEDS_EVIDENCE_REASON_KINDS:
        normalized = normalized.model_copy(update={'reason_kind': 'ambiguous_candidate'})
    return normalized


def _require_reason_kind(reason_kind: str, allowed: set[str], default_kind: str = '') -> str:
    return reason_kind or default_kind if (reason_kind or default_kind) in allowed else ''


def validate_mapping_patch(patch: MappingDraftPatch, dossier: CaseDossier, draft: MappingDraft) -> list[VerifierIssue]:
    issues: list[VerifierIssue] = []
    patch = _canonicalize_patch_for_dossier(patch, dossier, draft)
    row_by_ref = {row.row_ref: row for row in draft.rows if getattr(row, 'row_ref', '')}
    if patch.local_ref in row_by_ref:
        patch = patch.model_copy(update={'local_ref': row_by_ref[patch.local_ref].local_ref})
    row = _row_by_local_ref(draft, patch.local_ref)
    local_span_refs = _known_local_span_refs(dossier)
    bangumi_span_refs = _bangumi_detail_span_refs(dossier)
    bangumi_item_refs = _visible_bangumi_item_refs(dossier)
    visible_refs = _visible_refs(dossier)

    if not patch.local_ref or patch.local_ref not in local_span_refs or row is None:
        issues.append(_issue(patch.local_ref or 'mapping_patch', 'unknown_local_ref', 'local_ref must exist in draft rows and dossier local spans'))
        return issues

    support_refs = list(patch.support_refs or [])
    if any(ref not in visible_refs for ref in support_refs if ref):
        issues.append(_issue(patch.local_ref, 'hidden_ref_rejected', 'support_refs must be visible refs', related_refs=[ref for ref in support_refs if ref not in visible_refs]))

    if patch.op == 'map_to_bangumi':
        if not patch.target_ref and not patch.target_span_ref:
            issues.append(_issue(patch.local_ref, 'missing_target_ref', 'target_ref or target_span_ref must be provided for mapping'))
        if patch.target_span_ref and patch.target_span_ref not in bangumi_span_refs:
            issues.append(_issue(patch.local_ref, 'unknown_target_span_ref', 'target_span_ref must exist in a detail_equivalent BangumiSpanCard'))
        if patch.target_ref and patch.target_ref not in visible_refs and patch.target_ref not in bangumi_item_refs:
            issues.append(_issue(patch.local_ref, 'unknown_target_ref', 'target_ref must exist for explicit mapping'))
        if patch.target_ref and patch.mapping_mode == 'explicit' and patch.target_ref not in bangumi_item_refs:
            issues.append(_issue(patch.local_ref, 'unknown_target_ref', 'explicit target_ref must be a visible Bangumi item ref'))
        if patch.mapping_mode not in ('explicit', 'span_by_index'):
            issues.append(_issue(patch.local_ref, 'invalid_mapping_mode', 'mapping_mode must be explicit or span_by_index'))
    elif patch.op == 'mark_non_bangumi_or_supplemental':
        if patch.target_ref or patch.target_span_ref:
            issues.append(_issue(patch.local_ref, 'target_not_allowed', 'supplemental/exclusion patches must not carry target refs'))
        if patch.reason_kind not in _EXCLUSION_REASON_KINDS:
            issues.append(_issue(patch.local_ref, 'invalid_reason_kind', 'reason_kind must be in exclusion allowlist'))
        if not support_refs:
            issues.append(_issue(patch.local_ref, 'missing_support_refs', 'support_refs are required for supplemental/exclusion patches'))
        policy_row = row.model_copy(update={
            'reason_kind': patch.reason_kind,
            'reason': patch.reason or row.reason,
            'support_refs': support_refs,
            'disposition': 'non_bangumi_or_supplemental',
        })
        issues.extend(supplemental_row_policy_issues(dossier, policy_row))
    elif patch.op == 'needs_more_evidence':
        if patch.reason_kind and patch.reason_kind not in _NEEDS_EVIDENCE_REASON_KINDS:
            issues.append(_issue(patch.local_ref, 'invalid_reason_kind', 'reason_kind must be in needs-evidence allowlist'))
    elif patch.op == 'mark_unaligned_fail_closed':
        if patch.reason_kind not in _UNALIGNED_REASON_KINDS:
            issues.append(_issue(patch.local_ref, 'invalid_reason_kind', 'reason_kind must be in fail-closed allowlist'))
        if not support_refs:
            issues.append(_issue(patch.local_ref, 'missing_support_refs', 'support_refs are required for fail-closed patches'))
    elif patch.op in ('add_candidate', 'reject_candidate', 'retract_mapping'):
        pass

    return issues


def apply_mapping_patches(draft: MappingDraft, patches: list[MappingDraftPatch], dossier: CaseDossier) -> tuple[MappingDraft, list[VerifierIssue]]:
    issues: list[VerifierIssue] = []
    updated = draft.model_copy(deep=True)
    for patch in patches:
        row_by_ref = {row.row_ref: row for row in updated.rows if getattr(row, 'row_ref', '')}
        if getattr(patch, 'local_ref', '') in row_by_ref:
            patch = patch.model_copy(update={'local_ref': row_by_ref[getattr(patch, 'local_ref', '')].local_ref})
        patch_issues = validate_mapping_patch(patch, dossier, updated)
        issues.extend(patch_issues)
        if patch_issues:
            continue
        patch = _canonicalize_patch_for_dossier(patch, dossier, updated)
        row = _row_by_local_ref(updated, patch.local_ref)
        if row is None:
            continue
        if patch.op == 'map_to_bangumi':
            row.selected_target_ref = patch.target_span_ref or patch.target_ref
            row.selected_target_kind = 'span' if patch.target_span_ref else 'item'
            row.mapping_mode = patch.mapping_mode if patch.mapping_mode in ('explicit', 'span_by_index') else ('span_by_index' if patch.target_span_ref else 'explicit')
            row.status = 'proposed'
            row.disposition = 'map_to_bangumi'
            row.support_refs = list(patch.support_refs or [])
            row.reason_kind = patch.reason_kind or row.reason_kind
            row.reason = patch.reason or row.reason
        elif patch.op == 'mark_non_bangumi_or_supplemental':
            if row.disposition == 'map_to_bangumi':
                continue
            row.disposition = 'non_bangumi_or_supplemental'
            row.status = 'proposed'
            row.selected_target_ref = ''
            row.selected_target_kind = 'none'
            row.mapping_mode = 'unresolved'
            row.support_refs = list(patch.support_refs or [])
            row.reason_kind = patch.reason_kind or row.reason_kind
            row.reason = patch.reason or row.reason
        elif patch.op == 'needs_more_evidence':
            row.disposition = 'needs_more_evidence'
            row.status = 'unresolved'
            row.mapping_mode = 'unresolved'
            row.reason_kind = patch.reason_kind or row.reason_kind
            row.reason = patch.reason or row.reason
        elif patch.op == 'mark_unaligned_fail_closed':
            row.disposition = 'unaligned_fail_closed'
            row.status = 'unresolved'
            row.support_refs = list(patch.support_refs or [])
            row.reason_kind = patch.reason_kind or row.reason_kind
            row.reason = patch.reason or row.reason
        elif patch.op == 'add_candidate':
            row.candidate_target_refs = _dedupe_preserve_order([*row.candidate_target_refs, *(patch.support_refs or []), patch.target_ref, patch.target_span_ref])
        elif patch.op == 'reject_candidate':
            reject_refs = {ref for ref in [patch.target_ref, patch.target_span_ref, *list(patch.support_refs or [])] if ref}
            row.candidate_target_refs = [ref for ref in row.candidate_target_refs if ref not in reject_refs]
            row.reason = patch.reason or row.reason
        elif patch.op == 'retract_mapping':
            row.selected_target_ref = ''
            row.selected_target_kind = 'none'
            row.mapping_mode = 'unresolved'
            row.status = 'open'
            row.disposition = 'open'
            row.reason = patch.reason or row.reason
        updated.version += 1
    return updated, issues


def compute_mapping_draft_accounting(draft: MappingDraft, dossier_or_workspace: CaseDossier) -> Any:
    contract = getattr(dossier_or_workspace, 'contract', None)
    main_file_refs = list(getattr(contract, 'main_file_refs', []) or [])
    if not main_file_refs:
        main_file_refs = [card.ref for card in getattr(dossier_or_workspace, 'local_files', []) or [] if getattr(card, 'is_main', False) and getattr(card, 'ref', '')]
    main_file_count = len(main_file_refs)
    local_file_refs = {card.ref for card in getattr(dossier_or_workspace, 'local_files', []) or [] if getattr(card, 'is_main', False)}
    accounted: set[str] = set()
    duplicates = 0
    overlap = 0
    mapped = excluded = needs = unaligned = open_count = 0
    seen_local_refs: set[str] = set()
    for row in draft.rows:
        local_refs = set()
        if row.local_ref in {card.ref for card in getattr(dossier_or_workspace, 'local_span_cards', []) or []}:
            span = next((card for card in getattr(dossier_or_workspace, 'local_span_cards', []) or [] if card.ref == row.local_ref), None)
            local_refs = {ref for ref in list(getattr(span, 'file_refs', []) or []) if ref in main_file_refs}
            if row.local_ref in main_file_refs:
                local_refs.add(row.local_ref)
        elif row.local_ref in local_file_refs:
            local_refs = {row.local_ref}
        elif row.local_ref in main_file_refs:
            local_refs = {row.local_ref}
        if row.local_ref in seen_local_refs:
            duplicates += 1
        seen_local_refs.add(row.local_ref)
        if row.disposition == 'map_to_bangumi':
            mapped += len(local_refs)
        elif row.disposition == 'non_bangumi_or_supplemental':
            excluded += len(local_refs)
        elif row.disposition == 'needs_more_evidence':
            needs += len(local_refs)
        elif row.disposition == 'unaligned_fail_closed':
            unaligned += len(local_refs)
        else:
            open_count += len(local_refs)
        overlap += len(accounted.intersection(local_refs))
        accounted.update(local_refs)
    accounted_for_count = len(accounted)
    missing = max(main_file_count - accounted_for_count, 0)
    unresolved_count = needs + unaligned + open_count
    from .models import MappingDraftAccounting
    return MappingDraftAccounting(
        main_file_count=main_file_count,
        draft_row_count=len(draft.rows),
        mapped_file_count=mapped,
        excluded_file_count=excluded,
        needs_more_evidence_file_count=needs,
        unaligned_file_count=unaligned,
        open_file_count=open_count,
        accounted_for_count=accounted_for_count,
        unresolved_count=unresolved_count,
        duplicate_local_ref_count=duplicates,
        missing_main_file_count=missing,
        overlap_main_file_count=overlap,
        accepted_accounting_ready=accounted_for_count == main_file_count and unresolved_count == 0 and missing == 0 and overlap == 0 and duplicates == 0,
    )
