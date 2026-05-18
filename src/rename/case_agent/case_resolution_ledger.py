from __future__ import annotations

from .mapping_intent_compiler import MappingIntentCompiler
from .models import (
    BlockedLedgerRow,
    CaseDossier,
    CaseResolutionLedger,
    CaseResolutionLedgerCompilerResult,
    CaseResolutionLedgerRow,
    EvidenceRequestType,
    MappingDraft,
    MappingDraftPatch,
    MappingDraftRow,
    MappingIntent,
    VerifierIssue,
)
from .supplemental_policy import ALLOWED_SUPPLEMENTAL_REASON_KINDS, main_file_refs_for_mapping_row


_UNALIGNED_REASON_KINDS = {
    'ambiguous_ownership',
    'special_regular_conflict',
    'coverage_gap_unresolved',
    'insufficient_evidence',
}


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _issue(ref: str, code: str, message: str, related_refs: list[str] | None = None) -> VerifierIssue:
    return VerifierIssue(
        ref=ref,
        issue_code=code,
        severity='blocked',
        message=message,
        related_refs=list(related_refs or []),
    )


def _blocked(
    row: CaseResolutionLedgerRow,
    issue_codes: list[str],
    *,
    requested_request_types: list[EvidenceRequestType] | None = None,
    observation: dict[str, object] | None = None,
    recommended_next_observation: str = '',
) -> BlockedLedgerRow:
    return BlockedLedgerRow(
        ledger_row_ref=str(getattr(row, 'ledger_row_ref', '') or ''),
        row_ref=str(getattr(row, 'row_ref', '') or ''),
        plan_row_refs=list(getattr(row, 'plan_row_refs', []) or []),
        local_ref=str(getattr(row, 'local_ref', '') or ''),
        outcome=getattr(row, 'outcome', 'needs_evidence'),
        issue_codes=_dedupe([str(code or '') for code in issue_codes]),
        requested_request_types=list(requested_request_types or []),
        query_hints=list(getattr(row, 'query_hints', []) or []),
        subject_refs=list(getattr(row, 'subject_refs', []) or []),
        item_refs=list(getattr(row, 'item_refs', []) or []),
        target_refs=list(getattr(row, 'target_refs', []) or []),
        support_refs=list(getattr(row, 'support_refs', []) or []),
        observation=dict(observation or {}),
        reason=str(getattr(row, 'reason', '') or ''),
        recommended_next_observation=recommended_next_observation,
    )


def _row_by_ref(draft: MappingDraft) -> dict[str, MappingDraftRow]:
    return {
        str(getattr(row, 'row_ref', '') or ''): row
        for row in list(getattr(draft, 'rows', []) or [])
        if str(getattr(row, 'row_ref', '') or '')
    }


def _row_by_local_ref(draft: MappingDraft) -> dict[str, MappingDraftRow]:
    return {
        str(getattr(row, 'local_ref', '') or ''): row
        for row in list(getattr(draft, 'rows', []) or [])
        if str(getattr(row, 'local_ref', '') or '')
    }


def _plan_row_by_ref(dossier: CaseDossier) -> dict[str, object]:
    return {
        str(getattr(row, 'plan_row_ref', '') or ''): row
        for row in list(getattr(dossier, 'recorded_split_plan_rows', []) or [])
        if str(getattr(row, 'plan_row_ref', '') or '')
    }


def _local_ref_sets(dossier: CaseDossier) -> tuple[set[str], set[str], set[str]]:
    local_files = {
        str(getattr(card, 'ref', '') or '')
        for card in list(getattr(dossier, 'local_files', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }
    local_spans = {
        str(getattr(card, 'ref', '') or '')
        for card in list(getattr(dossier, 'local_span_cards', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }
    main_files = {
        str(ref or '')
        for ref in list(getattr(getattr(dossier, 'contract', None), 'main_file_refs', []) or [])
        if str(ref or '')
    }
    return local_files, local_spans, main_files


def _target_ref_set(dossier: CaseDossier) -> set[str]:
    visible = getattr(dossier, 'visible_refs', None)
    return {
        *[str(ref or '') for ref in list(getattr(visible, 'bangumi_subject_refs', []) or [])],
        *[str(ref or '') for ref in list(getattr(visible, 'bangumi_relation_refs', []) or [])],
        *[str(ref or '') for ref in list(getattr(visible, 'bangumi_group_refs', []) or [])],
        *[str(ref or '') for ref in list(getattr(visible, 'bangumi_item_refs', []) or [])],
        *[str(ref or '') for ref in list(getattr(visible, 'target_refs', []) or [])],
        *[
            str(getattr(card, 'ref', '') or '')
            for card in list(getattr(dossier, 'bangumi_span_cards', []) or [])
            if str(getattr(card, 'ref', '') or '')
        ],
    }


def _query_ref_set(dossier: CaseDossier) -> set[str]:
    visible = getattr(dossier, 'visible_refs', None)
    return {str(ref or '') for ref in list(getattr(visible, 'query_refs', []) or []) if str(ref or '')}


def _leaf_parent_key_for_local_file(card) -> str:
    path = str(getattr(card, 'path', '') or getattr(card, 'label', '') or '').replace('\\', '/')
    parts = [part for part in path.split('/') if part]
    if len(parts) > 1:
        return '/'.join(parts[:-1])
    return str(getattr(card, 'parent_display', '') or '<root>')


def _local_group_ref_set(dossier: CaseDossier) -> set[str]:
    main_refs = [
        str(ref or '')
        for ref in list(getattr(getattr(dossier, 'contract', None), 'main_file_refs', []) or [])
        if str(ref or '')
    ]
    main_ref_set = set(main_refs)
    ordered_files = [
        card for card in list(getattr(dossier, 'local_files', []) or [])
        if str(getattr(card, 'ref', '') or '') in main_ref_set or (not main_ref_set and bool(getattr(card, 'is_main', False)))
    ]
    by_ref = {str(getattr(card, 'ref', '') or ''): card for card in ordered_files}
    if main_refs:
        ordered_files = [by_ref[ref] for ref in main_refs if ref in by_ref]
    group_keys: list[str] = []
    for card in ordered_files:
        key = _leaf_parent_key_for_local_file(card)
        if key not in group_keys:
            group_keys.append(key)
    return {f'LG{index}' for index, _key in enumerate(group_keys, start=1)}


def _span_file_refs(dossier: CaseDossier, span_ref: str) -> list[str]:
    for span in list(getattr(dossier, 'local_span_cards', []) or []):
        if str(getattr(span, 'ref', '') or '') == span_ref:
            return [str(ref or '') for ref in list(getattr(span, 'file_refs', []) or []) if str(ref or '')]
    return []


def _plan_row_file_refs(dossier: CaseDossier, plan_row_refs: list[str]) -> list[str]:
    by_ref = _plan_row_by_ref(dossier)
    refs: list[str] = []
    for plan_ref in [str(ref or '') for ref in list(plan_row_refs or []) if str(ref or '')]:
        plan_row = by_ref.get(plan_ref)
        if plan_row is None:
            continue
        refs.extend([
            str(ref or '')
            for ref in list(getattr(plan_row, 'main_file_refs', []) or [])
            if str(ref or '')
        ])
    return _dedupe(refs)


def _row_main_refs(
    dossier: CaseDossier,
    draft: MappingDraft,
    row: CaseResolutionLedgerRow,
) -> tuple[list[str], str]:
    draft_by_ref = _row_by_ref(draft)
    draft_by_local = _row_by_local_ref(draft)
    row_ref = str(getattr(row, 'row_ref', '') or '')
    if row_ref and row_ref in draft_by_ref:
        draft_row = draft_by_ref[row_ref]
        return main_file_refs_for_mapping_row(dossier, draft_row), str(getattr(draft_row, 'local_ref', '') or '')
    local_ref = str(getattr(row, 'local_ref', '') or '')
    if local_ref and local_ref in draft_by_local:
        draft_row = draft_by_local[local_ref]
        return main_file_refs_for_mapping_row(dossier, draft_row), local_ref

    local_files, local_spans, main_files = _local_ref_sets(dossier)
    plan_main_refs = _plan_row_file_refs(dossier, list(getattr(row, 'plan_row_refs', []) or []))
    refs = _dedupe([
        local_ref,
        *[str(ref or '') for ref in list(getattr(row, 'local_refs', []) or [])],
        *[str(ref or '') for ref in list(getattr(row, 'file_refs', []) or [])],
        *[str(ref or '') for ref in list(getattr(row, 'span_refs', []) or [])],
    ])
    main_refs: list[str] = []
    resolved_local_ref = local_ref
    for ref in refs:
        if ref in local_files:
            main_refs.append(ref)
            resolved_local_ref = resolved_local_ref or ref
        elif ref in local_spans:
            main_refs.extend(_span_file_refs(dossier, ref))
            resolved_local_ref = resolved_local_ref or ref
    main_refs = _dedupe([*plan_main_refs, *main_refs])
    return [ref for ref in main_refs if not main_files or ref in main_files], resolved_local_ref


def _draft_row_for_ledger_row(
    dossier: CaseDossier,
    draft: MappingDraft,
    row: CaseResolutionLedgerRow,
) -> tuple[MappingDraftRow | None, str]:
    by_ref = _row_by_ref(draft)
    by_local = _row_by_local_ref(draft)
    row_ref = str(getattr(row, 'row_ref', '') or '')
    local_ref = str(getattr(row, 'local_ref', '') or '')
    if row_ref and row_ref in by_ref:
        return by_ref[row_ref], str(getattr(by_ref[row_ref], 'local_ref', '') or '')
    if local_ref and local_ref in by_local:
        return by_local[local_ref], local_ref
    main_refs, resolved_local_ref = _row_main_refs(dossier, draft, row)
    target_set = set(main_refs)
    if target_set:
        matches = [
            draft_row for draft_row in list(getattr(draft, 'rows', []) or [])
            if set(main_file_refs_for_mapping_row(dossier, draft_row)) == target_set
        ]
        if len(matches) == 1:
            return matches[0], str(getattr(matches[0], 'local_ref', '') or resolved_local_ref)
    return None, resolved_local_ref


def validate_case_resolution_ledger(
    dossier: CaseDossier,
    draft: MappingDraft,
    ledger: CaseResolutionLedger,
) -> list[VerifierIssue]:
    issues: list[VerifierIssue] = []
    local_files, local_spans, main_files = _local_ref_sets(dossier)
    target_refs = _target_ref_set(dossier)
    query_refs = _query_ref_set(dossier)
    local_group_refs = _local_group_ref_set(dossier)
    row_refs = set(_row_by_ref(draft))
    plan_rows = _plan_row_by_ref(dossier)
    seen_main_refs: list[str] = []

    if not list(getattr(ledger, 'rows', []) or []):
        return [_issue('CRL1', 'ledger_empty', 'case resolution ledger must contain at least one row')]

    for index, row in enumerate(list(getattr(ledger, 'rows', []) or []), start=1):
        ref = str(getattr(row, 'ledger_row_ref', '') or f'CRLR{index}')
        row_ref = str(getattr(row, 'row_ref', '') or '')
        if row_ref and row_ref not in row_refs:
            issues.append(_issue(ref, 'ledger_unknown_row_ref', 'ledger row_ref must cite a visible mapping draft row. For Agent-recorded split plan rows, use plan_row_refs=RSP* instead of inventing category row_ref labels.', [row_ref]))
        for value in [str(item or '') for item in list(getattr(row, 'plan_row_refs', []) or [])]:
            if value and value not in plan_rows:
                issues.append(_issue(ref, 'ledger_unknown_plan_row_refs', 'plan_row_refs must cite visible recorded_split_plan RSP* refs', [value]))
        local_fields = [
            ('local_ref', [str(getattr(row, 'local_ref', '') or '')]),
            ('local_refs', [str(value or '') for value in list(getattr(row, 'local_refs', []) or [])]),
            ('file_refs', [str(value or '') for value in list(getattr(row, 'file_refs', []) or [])]),
            ('span_refs', [str(value or '') for value in list(getattr(row, 'span_refs', []) or [])]),
        ]
        for field, values in local_fields:
            for value in values:
                if not value:
                    continue
                allowed = local_files if field == 'file_refs' else local_files | local_spans
                if field == 'span_refs':
                    allowed = local_spans
                if value not in allowed:
                    issues.append(_issue(ref, f'ledger_unknown_{field}', f'{field} must cite visible local LF/LS refs', [value]))
        for field in ('chosen_subject_ref', 'chosen_item_ref', 'chosen_span_ref'):
            value = str(getattr(row, field, '') or '')
            if value and value not in target_refs:
                issues.append(_issue(ref, f'ledger_unknown_{field}', f'{field} must cite visible Bangumi target evidence refs', [value]))
        for field in ('subject_refs', 'item_refs', 'target_refs'):
            for value in [str(item or '') for item in list(getattr(row, field, []) or [])]:
                if value and value not in target_refs:
                    issues.append(_issue(ref, f'ledger_unknown_{field}', f'{field} must cite visible Bangumi refs', [value]))
        for value in [str(item or '') for item in list(getattr(row, 'query_refs', []) or [])]:
            if value and value not in query_refs:
                issues.append(_issue(ref, 'ledger_unknown_query_refs', 'query_refs must cite visible QC refs', [value]))
        support_allowed = local_files | local_spans | local_group_refs | target_refs | query_refs
        for value in [str(item or '') for item in [*list(getattr(row, 'support_refs', []) or []), *list(getattr(row, 'source_refs', []) or [])]]:
            if value and value not in support_allowed:
                issues.append(_issue(ref, 'ledger_unknown_support_ref', 'support/source refs must cite visible evidence refs', [value]))
        main_refs, _local_ref = _row_main_refs(dossier, draft, row)
        if not main_refs:
            issues.append(_issue(ref, 'ledger_row_no_main_refs', 'ledger row must resolve to at least one current main LF ref'))
        seen_main_refs.extend(main_refs)

    if main_files:
        seen_counts: dict[str, int] = {}
        for ref in seen_main_refs:
            seen_counts[ref] = seen_counts.get(ref, 0) + 1
        missing = [ref for ref in sorted(main_files) if seen_counts.get(ref, 0) == 0]
        duplicate = [ref for ref, count in sorted(seen_counts.items()) if count > 1 and ref in main_files]
        extra = [ref for ref in sorted(seen_counts) if ref not in main_files]
        if missing:
            issues.append(_issue('CRL1', 'ledger_missing_main_refs', 'ledger rows must cover every current main LF exactly once', missing[:24]))
        if duplicate:
            issues.append(_issue('CRL1', 'ledger_duplicate_main_refs', 'ledger rows must not overlap current main LF refs', duplicate[:24]))
        if extra:
            issues.append(_issue('CRL1', 'ledger_extra_main_refs', 'ledger rows include refs outside current main LF contract', extra[:24]))
    return issues


class CaseResolutionLedgerCompiler:
    def compile(
        self,
        dossier: CaseDossier,
        draft: MappingDraft,
        ledger: CaseResolutionLedger,
    ) -> CaseResolutionLedgerCompilerResult:
        validation_issues = validate_case_resolution_ledger(dossier, draft, ledger)
        if validation_issues:
            return CaseResolutionLedgerCompilerResult(
                blocked_rows=[
                    BlockedLedgerRow(
                        ledger_row_ref='CRL1',
                        issue_codes=_dedupe([str(getattr(issue, 'issue_code', '') or '') for issue in validation_issues]),
                        observation={
                            'issues': [issue.model_dump(mode='json') for issue in validation_issues[:16]],
                            'available_plan_row_refs': [
                                str(getattr(row, 'plan_row_ref', '') or '')
                                for row in list(getattr(dossier, 'recorded_split_plan_rows', []) or [])
                                if str(getattr(row, 'plan_row_ref', '') or '')
                            ],
                            'available_mapping_row_refs': sorted(_row_by_ref(draft)),
                        },
                        recommended_next_observation='revise propose_case_resolution_ledger with visible MDR row_ref/local_ref or recorded_split_plan plan_row_refs=RSP*, exact main LF coverage, and no overlap',
                    )
                ],
                recommended_next_observation='ledger validation failed; revise refs or coverage. Use plan_row_refs=RSP* for recorded split plan rows instead of category labels in row_ref.',
            )

        intents: list[MappingIntent] = []
        blocked_rows: list[BlockedLedgerRow] = []
        requested_evidence: list[EvidenceRequestType] = []
        for index, row in enumerate(list(getattr(ledger, 'rows', []) or []), start=1):
            draft_row, resolved_local_ref = _draft_row_for_ledger_row(dossier, draft, row)
            local_ref = resolved_local_ref or str(getattr(row, 'local_ref', '') or '')
            row_ref = str(getattr(row, 'row_ref', '') or (getattr(draft_row, 'row_ref', '') if draft_row is not None else ''))
            intent_ref = str(getattr(row, 'ledger_row_ref', '') or f'CRLR{index}')
            support_refs = _dedupe([
                *[str(ref or '') for ref in list(getattr(row, 'support_refs', []) or [])],
                *[str(ref or '') for ref in list(getattr(row, 'source_refs', []) or [])],
            ])
            outcome = str(getattr(row, 'outcome', '') or 'needs_evidence')
            if draft_row is None:
                blocked_rows.append(_blocked(
                    row,
                    ['ledger_row_no_matching_draft_row'],
                    observation={'local_ref': local_ref, 'row_ref': row_ref},
                    recommended_next_observation='cite the visible MDR row_ref or local_ref for this ledger row',
                ))
                continue
            if outcome == 'map_to_bangumi':
                decision = 'map_regular_span'
                if str(getattr(row, 'chosen_item_ref', '') or '') and not list(getattr(row, 'item_refs', []) or []):
                    decision = 'map_explicit_item'
                intent = MappingIntent(
                    intent_ref=intent_ref,
                    decision=decision,
                    row_ref=row_ref,
                    local_ref=local_ref,
                    chosen_subject_ref=str(getattr(row, 'chosen_subject_ref', '') or ''),
                    chosen_item_ref=str(getattr(row, 'chosen_item_ref', '') or ''),
                    chosen_span_ref=str(getattr(row, 'chosen_span_ref', '') or ''),
                    episode_scope=getattr(row, 'episode_scope', 'unknown'),
                    episode_start=getattr(row, 'episode_start', None),
                    episode_end=getattr(row, 'episode_end', None),
                    mapping_mode=getattr(row, 'mapping_mode', 'unresolved'),
                    support_refs=support_refs,
                    reason_kind=str(getattr(row, 'reason_kind', '') or ''),
                    requested_request_types=list(getattr(row, 'requested_request_types', []) or []),
                    query_hints=list(getattr(row, 'query_hints', []) or []),
                    subject_refs=list(getattr(row, 'subject_refs', []) or []),
                    item_refs=list(getattr(row, 'item_refs', []) or []),
                    target_refs=list(getattr(row, 'target_refs', []) or []),
                    local_refs=list(getattr(row, 'local_refs', []) or []),
                    source_refs=list(getattr(row, 'source_refs', []) or []),
                    notebook_refs=list(getattr(row, 'notebook_refs', []) or []),
                    confidence=getattr(row, 'confidence', 'unknown'),
                    reason=str(getattr(row, 'reason', '') or ''),
                )
                intents.append(intent)
            elif outcome in {'target_absent', 'supplemental'}:
                reason_kind = str(getattr(row, 'reason_kind', '') or '')
                if outcome == 'target_absent':
                    reason_kind = reason_kind or 'bangumi_target_absent'
                elif reason_kind not in ALLOWED_SUPPLEMENTAL_REASON_KINDS:
                    blocked_rows.append(_blocked(
                        row,
                        ['ledger_missing_or_invalid_supplemental_reason_kind'],
                        observation={'allowed_reason_kinds': sorted(ALLOWED_SUPPLEMENTAL_REASON_KINDS)},
                        recommended_next_observation='for supplemental outcome, provide one allowed supplemental reason_kind',
                    ))
                    continue
                intents.append(MappingIntent(
                    intent_ref=intent_ref,
                    decision='mark_non_bangumi_or_supplemental',
                    row_ref=row_ref,
                    local_ref=local_ref,
                    support_refs=support_refs,
                    reason_kind=reason_kind,
                    requested_request_types=list(getattr(row, 'requested_request_types', []) or []),
                    query_hints=list(getattr(row, 'query_hints', []) or []),
                    subject_refs=list(getattr(row, 'subject_refs', []) or []),
                    item_refs=list(getattr(row, 'item_refs', []) or []),
                    target_refs=list(getattr(row, 'target_refs', []) or []),
                    local_refs=list(getattr(row, 'local_refs', []) or []),
                    source_refs=list(getattr(row, 'source_refs', []) or []),
                    notebook_refs=list(getattr(row, 'notebook_refs', []) or []),
                    confidence=getattr(row, 'confidence', 'unknown'),
                    reason=str(getattr(row, 'reason', '') or ''),
                ))
            elif outcome == 'needs_evidence':
                reqs = list(getattr(row, 'requested_request_types', []) or [])
                requested_evidence.extend(reqs)
                intents.append(MappingIntent(
                    intent_ref=intent_ref,
                    decision='needs_more_evidence',
                    row_ref=row_ref,
                    local_ref=local_ref,
                    support_refs=support_refs,
                    reason_kind=str(getattr(row, 'reason_kind', '') or ''),
                    requested_request_types=reqs,
                    query_hints=list(getattr(row, 'query_hints', []) or []),
                    subject_refs=list(getattr(row, 'subject_refs', []) or []),
                    item_refs=list(getattr(row, 'item_refs', []) or []),
                    target_refs=list(getattr(row, 'target_refs', []) or []),
                    local_refs=list(getattr(row, 'local_refs', []) or []),
                    source_refs=list(getattr(row, 'source_refs', []) or []),
                    notebook_refs=list(getattr(row, 'notebook_refs', []) or []),
                    confidence=getattr(row, 'confidence', 'unknown'),
                    reason=str(getattr(row, 'reason', '') or ''),
                ))
            elif outcome == 'fail_blocker':
                reason_kind = str(getattr(row, 'reason_kind', '') or '')
                if reason_kind not in _UNALIGNED_REASON_KINDS:
                    blocked_rows.append(_blocked(
                        row,
                        ['ledger_missing_or_invalid_fail_blocker_reason_kind'],
                        observation={'allowed_reason_kinds': sorted(_UNALIGNED_REASON_KINDS)},
                        recommended_next_observation='for fail_blocker outcome, provide one allowed fail_closed reason_kind',
                    ))
                    continue
                intents.append(MappingIntent(
                    intent_ref=intent_ref,
                    decision='mark_unaligned_fail_closed',
                    row_ref=row_ref,
                    local_ref=local_ref,
                    support_refs=support_refs,
                    reason_kind=reason_kind,
                    local_refs=list(getattr(row, 'local_refs', []) or []),
                    source_refs=list(getattr(row, 'source_refs', []) or []),
                    notebook_refs=list(getattr(row, 'notebook_refs', []) or []),
                    confidence=getattr(row, 'confidence', 'unknown'),
                    reason=str(getattr(row, 'reason', '') or ''),
                ))
            elif outcome == 'split_needed':
                blocked_rows.append(_blocked(
                    row,
                    ['ledger_row_split_needed'],
                    observation={'local_ref': local_ref, 'row_ref': row_ref},
                    recommended_next_observation='call split_into_child_cases with explicit child file refs if this case needs child sessions',
                ))

        compiler_result = MappingIntentCompiler().compile(dossier, draft, intents)
        blocked_rows.extend([
            BlockedLedgerRow(
                ledger_row_ref=str(getattr(blocked, 'intent_ref', '') or ''),
                row_ref=str(getattr(blocked, 'row_ref', '') or ''),
                local_ref=str(getattr(blocked, 'local_ref', '') or ''),
                outcome='needs_evidence',
                issue_codes=list(getattr(blocked, 'issue_codes', []) or []),
                requested_request_types=list(getattr(blocked, 'requested_request_types', []) or []),
                query_hints=list(getattr(blocked, 'query_hints', []) or []),
                subject_refs=list(getattr(blocked, 'subject_refs', []) or []),
                item_refs=list(getattr(blocked, 'item_refs', []) or []),
                target_refs=list(getattr(blocked, 'candidate_target_refs', []) or []),
                support_refs=list(getattr(blocked, 'support_refs', []) or []),
                observation=dict(getattr(blocked, 'observation', {}) or {}),
                reason=str(getattr(blocked, 'reason', '') or ''),
                recommended_next_observation=str(getattr(blocked, 'recommended_next_observation', '') or ''),
            )
            for blocked in list(getattr(compiler_result, 'blocked_intents', []) or [])
        ])
        requested_evidence.extend(list(getattr(compiler_result, 'requested_evidence', []) or []))
        recommended = str(getattr(compiler_result, 'recommended_next_observation', '') or '')
        if blocked_rows and not recommended:
            recommended = 'revise ledger rows, execute requested evidence, or split if ledger says split_needed'
        elif not blocked_rows:
            recommended = 'ledger compiled; finish_case when accounting is accepted-ready or continue with remaining rows'
        return CaseResolutionLedgerCompilerResult(
            compiled_patches=list(getattr(compiler_result, 'compiled_patches', []) or []),
            blocked_rows=blocked_rows,
            generated_span_cards=list(getattr(compiler_result, 'generated_span_cards', []) or []),
            requested_evidence=_dedupe([str(value or '') for value in requested_evidence]),
            recommended_next_observation=recommended,
        )
