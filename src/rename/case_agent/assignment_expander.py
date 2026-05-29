from __future__ import annotations

from collections.abc import Iterable

from .models import (
    AssignmentIntent,
    BangumiItemCard,
    BangumiSpanCard,
    BulkAssignmentIntent,
    CaseDossier,
    CaseJudgeOutput,
    MappingDraft,
    MappingDraftRow,
    LocalFileCard,
    LocalSpanCard,
    VerifierIssue,
)
from .supplemental_policy import supplemental_row_policy_issues


def _issue(ref: str, issue_code: str, message: str, *, related_refs: list[str] | None = None) -> VerifierIssue:
    return VerifierIssue(ref=ref, issue_code=issue_code, severity='blocked', message=message, related_refs=list(related_refs or []))


def _as_map(cards: Iterable[LocalSpanCard | BangumiSpanCard | BangumiItemCard | LocalFileCard]) -> dict[str, LocalSpanCard | BangumiSpanCard | BangumiItemCard | LocalFileCard]:
    return {card.ref: card for card in cards if getattr(card, 'ref', '')}


def _local_files_for_row(row: MappingDraftRow, local_span_cards: dict[str, LocalSpanCard], local_file_cards: dict[str, LocalFileCard]) -> list[str]:
    if row.local_ref_kind == 'file' or row.local_ref in local_file_cards:
        return [row.local_ref] if row.local_ref in local_file_cards else []
    local_span = local_span_cards.get(row.local_ref)
    if local_span is None:
        return []
    return list(getattr(local_span, 'file_refs', []) or [])


def _span_overlap_issues(*, ref: str, span_kind: str, span_ref: str, span_refs: list[str], seen_refs: set[str], seen_ref_sources: dict[str, str], issue_code: str) -> list[VerifierIssue]:
    issues: list[VerifierIssue] = []
    current = set(span_refs)
    overlap = sorted(seen_refs & current)
    if overlap:
        prior_row_refs = [seen_ref_sources.get(item, '') for item in overlap]
        issues.append(_issue(
            ref,
            issue_code,
            f'{span_kind} span refs must not overlap across rows',
            related_refs=list(dict.fromkeys([ref, *prior_row_refs, span_ref, *overlap[:8]])),
        ))
    seen_refs.update(current)
    for item in current:
        seen_ref_sources.setdefault(item, ref)
    return issues


def expand_mapping_draft(dossier: CaseDossier, draft: MappingDraft) -> tuple[list[AssignmentIntent], list[VerifierIssue]]:
    issues: list[VerifierIssue] = []
    expanded: list[AssignmentIntent] = []

    local_span_cards = _as_map(dossier.local_span_cards)
    local_file_cards = _as_map(dossier.local_files)
    bangumi_span_cards = _as_map(dossier.bangumi_span_cards)
    bangumi_item_cards = _as_map(dossier.bangumi_items)
    assignable_item_refs = {
        *[card.ref for card in getattr(dossier, 'bangumi_items', []) or [] if getattr(card, 'ref', '')],
        *list(getattr(getattr(dossier, 'visible_refs', None), 'bangumi_item_refs', []) or []),
        *list(getattr(getattr(dossier, 'visible_refs', None), 'target_refs', []) or []),
        *list(getattr(dossier, 'assignable_target_refs', []) or []),
        *list(getattr(dossier, 'detailed_card_refs', []) or []),
        *list(getattr(dossier, 'seen_detail_refs', []) or []),
    }
    seen_local_file_refs: set[str] = set()
    seen_target_refs: set[str] = set()
    seen_local_ref_sources: dict[str, str] = {}
    seen_target_ref_sources: dict[str, str] = {}
    main_file_refs = set(getattr(getattr(dossier, 'contract', None), 'main_file_refs', []) or [])

    for row in draft.rows:
        # Accept the compact row shape used by draft patch aliases.
        alias_span_mapping = row.disposition == 'open' and row.status == 'proposed' and row.mapping_mode == 'span_by_index'
        if row.disposition != 'map_to_bangumi' and not alias_span_mapping:
            if row.disposition == 'non_bangumi_or_supplemental':
                policy_issues = supplemental_row_policy_issues(dossier, row)
                if policy_issues:
                    issues.extend(policy_issues)
                    continue
                file_refs = [
                    ref for ref in _local_files_for_row(row, local_span_cards, local_file_cards)
                    if not main_file_refs or ref in main_file_refs
                ]
                if not file_refs:
                    continue
                support = set(row.support_refs or [])
                if row.local_ref not in support and not (support & set(file_refs)):
                    issues.append(_issue(row.row_ref, 'missing_support_refs', 'supplemental row support_refs must include row local_ref or covered file refs'))
                    continue
                for file_ref in file_refs:
                    if file_ref in seen_local_file_refs:
                        issues.append(_issue(
                            row.row_ref,
                            'duplicate_local_span',
                            'local file refs must not overlap across rows',
                            related_refs=list(dict.fromkeys([row.row_ref, seen_local_ref_sources.get(file_ref, ''), file_ref])),
                        ))
                        continue
                    seen_local_file_refs.add(file_ref)
                    seen_local_ref_sources.setdefault(file_ref, row.row_ref)
                    support_card_refs = list(dict.fromkeys([*(row.support_refs or []), file_ref]))
                    expanded.append(AssignmentIntent(
                        ref=f'MDA_{row.row_ref}_{len(expanded) + 1}',
                        file_ref=file_ref,
                        target_ref='UNALIGNED',
                        support_finding_refs=[],
                        support_card_refs=support_card_refs,
                        reason=f'mapping_draft:{draft.draft_ref}:supplemental:{row.reason_kind or "other_supplemental"}',
                    ))
            continue
        if row.mapping_mode == 'explicit' and row.selected_target_kind == 'item':
            target_ref = str(getattr(row, 'selected_target_ref', '') or '')
            file_refs = _local_files_for_row(row, local_span_cards, local_file_cards)
            if len(file_refs) != 1:
                issues.append(_issue(row.row_ref, 'count_mismatch', 'explicit item mapping requires exactly one local file'))
                continue
            if not target_ref or target_ref not in assignable_item_refs or (bangumi_item_cards and target_ref not in bangumi_item_cards):
                issues.append(_issue(row.row_ref, 'invalid_target', 'explicit item mapping requires a visible assignable BE target'))
                continue
            support = set(row.support_refs or [])
            if row.local_ref not in support or target_ref not in support:
                issues.append(_issue(row.row_ref, 'missing_support_refs', 'explicit item mapping support_refs must include row local_ref and target_ref'))
                continue
            file_ref = file_refs[0]
            if file_ref in seen_local_file_refs:
                issues.append(_issue(
                    row.row_ref,
                    'duplicate_local_span',
                    'local file refs must not overlap across rows',
                    related_refs=list(dict.fromkeys([row.row_ref, seen_local_ref_sources.get(file_ref, ''), file_ref])),
                ))
                continue
            if target_ref in seen_target_refs:
                issues.append(_issue(
                    row.row_ref,
                    'duplicate_target',
                    'target refs must not overlap across rows',
                    related_refs=list(dict.fromkeys([row.row_ref, seen_target_ref_sources.get(target_ref, ''), target_ref])),
                ))
                continue
            seen_local_file_refs.add(file_ref)
            seen_local_ref_sources.setdefault(file_ref, row.row_ref)
            seen_target_refs.add(target_ref)
            seen_target_ref_sources.setdefault(target_ref, row.row_ref)
            support_card_refs = list(dict.fromkeys([*(row.support_refs or []), file_ref, target_ref]))
            expanded.append(AssignmentIntent(
                ref=f'MDA_{row.row_ref}_1',
                file_ref=file_ref,
                target_ref=target_ref,
                support_finding_refs=[],
                support_card_refs=support_card_refs,
                risk_flags=['synthetic_singleton'] if bool(getattr(bangumi_item_cards.get(target_ref), 'synthetic', False)) else [],
                reason=f'mapping_draft:{draft.draft_ref}',
            ))
            continue

        if row.mapping_mode != 'span_by_index' or row.selected_target_kind != 'span':
            issues.append(_issue(row.row_ref, 'invalid_mapping_mode', 'map_to_bangumi row must be explicit item mapping or span_by_index span mapping'))
            continue

        local_span = local_span_cards.get(row.local_ref)
        target_span = bangumi_span_cards.get(row.selected_target_ref)
        if local_span is None:
            issues.append(_issue(row.row_ref, 'missing_span_ref', 'local_ref must exist in dossier.local_span_cards'))
            continue
        if target_span is None:
            issues.append(_issue(row.row_ref, 'missing_span_ref', 'selected_target_ref must exist in dossier.bangumi_span_cards'))
            continue
        if not target_span.detail_equivalent:
            issues.append(_issue(row.row_ref, 'invalid_span_alignment', 'selected target span must be detail_equivalent'))
            continue
        if local_span.file_ref_count != target_span.target_ref_count:
            issues.append(_issue(row.row_ref, 'count_mismatch', 'local span file count must match target span count'))
            continue

        issues.extend(_span_overlap_issues(ref=row.row_ref, span_kind='local', span_ref=local_span.ref, span_refs=list(local_span.file_refs), seen_refs=seen_local_file_refs, seen_ref_sources=seen_local_ref_sources, issue_code='duplicate_local_span'))
        issues.extend(_span_overlap_issues(ref=row.row_ref, span_kind='target', span_ref=target_span.ref, span_refs=list(target_span.target_refs), seen_refs=seen_target_refs, seen_ref_sources=seen_target_ref_sources, issue_code='duplicate_target'))
        if any(issue.ref == row.row_ref for issue in issues[-2:]):
            continue

        for idx, (file_ref, target_ref) in enumerate(zip(local_span.file_refs, target_span.target_refs, strict=True), start=1):
            support_card_refs = list(dict.fromkeys([*(row.support_refs or []), file_ref, target_ref]))
            expanded.append(AssignmentIntent(
                ref=f'MDA_{row.row_ref}_{idx}',
                file_ref=file_ref,
                target_ref=target_ref,
                support_finding_refs=[],
                support_card_refs=support_card_refs,
                reason=f'mapping_draft:{draft.draft_ref}',
            ))

    return expanded, issues


def expand_bulk_assignment_intents(dossier: CaseDossier, output: CaseJudgeOutput) -> tuple[list[AssignmentIntent], list[VerifierIssue]]:
    issues: list[VerifierIssue] = []
    expanded: list[AssignmentIntent] = []

    local_span_cards = _as_map(dossier.local_span_cards)
    bangumi_span_cards = _as_map(dossier.bangumi_span_cards)
    alignment_claims = {claim.ref: claim for claim in output.span_alignment_claims if getattr(claim, 'ref', '')}
    finding_refs = {finding.ref for finding in output.findings if getattr(finding, 'ref', '')}
    explicit_file_refs = {assignment.file_ref for assignment in output.assignment_intents if getattr(assignment, 'file_ref', '')}
    covered_local_file_refs: set[str] = set()
    covered_target_refs: set[str] = set()
    seen_bulk_pairs: set[tuple[str, str]] = set()

    for bulk in output.bulk_assignment_intents:
        pair = (bulk.local_span_ref, bulk.bangumi_span_ref)
        if pair in seen_bulk_pairs:
            issues.append(_issue(bulk.ref, 'duplicate_span_alignment', 'bulk intents must not duplicate the same span pair'))
            continue
        seen_bulk_pairs.add(pair)

        local_span = local_span_cards.get(bulk.local_span_ref)
        bangumi_span = bangumi_span_cards.get(bulk.bangumi_span_ref)
        alignment = alignment_claims.get(bulk.alignment_ref)

        if local_span is None:
            issues.append(_issue(bulk.ref, 'missing_span_ref', 'local_span_ref must exist in dossier.local_span_cards'))
            continue
        if bangumi_span is None:
            issues.append(_issue(bulk.ref, 'missing_span_ref', 'bangumi_span_ref must exist in dossier.bangumi_span_cards'))
            continue
        if alignment is None:
            issues.append(_issue(bulk.ref, 'missing_span_ref', 'alignment_ref must exist in output.span_alignment_claims'))
            continue

        if not bangumi_span.detail_equivalent:
            issues.append(_issue(bulk.ref, 'invalid_span_alignment', 'bangumi span must be detail_equivalent'))
            continue

        if bulk.support_card_refs and (bulk.local_span_ref not in bulk.support_card_refs or bulk.bangumi_span_ref not in bulk.support_card_refs):
            issues.append(_issue(bulk.ref, 'missing_support', 'bulk support_card_refs must include local_span_ref and bangumi_span_ref'))
        elif not bulk.support_card_refs:
            issues.append(_issue(bulk.ref, 'missing_support', 'bulk support_card_refs required'))

        if bulk.support_finding_refs and any(ref not in finding_refs for ref in bulk.support_finding_refs):
            issues.append(_issue(bulk.ref, 'missing_support', 'bulk support_finding_refs must reference output.findings only'))
        elif not bulk.support_finding_refs:
            issues.append(_issue(bulk.ref, 'missing_support', 'bulk support_finding_refs required'))

        if len(local_span.file_refs) != len(bangumi_span.target_refs):
            issues.append(_issue(bulk.ref, 'count_mismatch', 'local_span and bangumi_span counts must be equal'))
            continue

        if len(set(bangumi_span.target_refs)) != len(bangumi_span.target_refs):
            issues.append(_issue(bulk.ref, 'duplicate_target', 'bangumi target_refs must not contain duplicates'))
            continue
        if any(not ref.startswith('BE') for ref in bangumi_span.target_refs):
            issues.append(_issue(bulk.ref, 'invalid_target', 'bangumi target_refs must all be BE refs'))
            continue

        if len(set(local_span.file_refs)) != len(local_span.file_refs):
            issues.append(_issue(bulk.ref, 'coverage_error', 'local_span file_refs must not contain duplicates'))
            continue

        if covered_local_file_refs & set(local_span.file_refs):
            issues.append(_issue(bulk.ref, 'coverage_error', 'local_span file_refs must not overlap across bulk intents'))
            continue
        if covered_target_refs & set(bangumi_span.target_refs):
            issues.append(_issue(bulk.ref, 'duplicate_target', 'bangumi target_refs must not overlap across bulk intents'))
            continue

        if explicit_file_refs & set(local_span.file_refs):
            issues.append(_issue(bulk.ref, 'coverage_error', 'bulk assignments must not overlap explicit assignment_intents'))
            continue

        if alignment.local_span_ref != bulk.local_span_ref or alignment.bangumi_span_ref != bulk.bangumi_span_ref:
            issues.append(_issue(bulk.ref, 'invalid_span_alignment', 'alignment_ref must match span refs'))
            continue

        if len(bulk.support_card_refs) and (bulk.local_span_ref not in bulk.support_card_refs or bulk.bangumi_span_ref not in bulk.support_card_refs):
            issues.append(_issue(bulk.ref, 'missing_support', 'bulk support_card_refs must include span refs'))
            continue

        for idx, (file_ref, target_ref) in enumerate(zip(local_span.file_refs, bangumi_span.target_refs, strict=True), start=1):
            expanded.append(AssignmentIntent(
                ref=f'{bulk.ref}:{idx}',
                file_ref=file_ref,
                target_ref=target_ref,
                support_finding_refs=list(bulk.support_finding_refs),
                support_card_refs=list(dict.fromkeys([*bulk.support_card_refs, bulk.local_span_ref, bulk.bangumi_span_ref, file_ref, target_ref])),
                reason=f'bulk:{bulk.ref}',
            ))

        covered_local_file_refs.update(local_span.file_refs)
        covered_target_refs.update(bangumi_span.target_refs)

    return expanded, issues
