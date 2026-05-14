from __future__ import annotations

from .models import (
    BangumiSpanCard,
    BlockedMappingIntent,
    CaseDossier,
    EvidenceRequestType,
    MappingDraft,
    MappingDraftPatch,
    MappingIntent,
    MappingIntentCompilerResult,
    MappingDraftRow,
)


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _dedupe_request_types(values: list[EvidenceRequestType]) -> list[EvidenceRequestType]:
    return list(dict.fromkeys(value for value in values if value))


def _visible_refs(dossier: CaseDossier) -> set[str]:
    visible = getattr(dossier, 'visible_refs', None)
    refs = {
        *list(getattr(visible, 'local_file_refs', []) or []),
        *list(getattr(visible, 'local_cluster_refs', []) or []),
        *list(getattr(visible, 'bangumi_subject_refs', []) or []),
        *list(getattr(visible, 'bangumi_relation_refs', []) or []),
        *list(getattr(visible, 'bangumi_group_refs', []) or []),
        *list(getattr(visible, 'bangumi_item_refs', []) or []),
        *list(getattr(visible, 'query_refs', []) or []),
        *list(getattr(visible, 'target_refs', []) or []),
        *[str(getattr(card, 'ref', '') or '') for card in list(getattr(dossier, 'local_span_cards', []) or [])],
        *[str(getattr(card, 'ref', '') or '') for card in list(getattr(dossier, 'bangumi_span_cards', []) or [])],
    }
    return {ref for ref in refs if ref}


def _row_maps(draft: MappingDraft) -> tuple[dict[str, MappingDraftRow], dict[str, MappingDraftRow]]:
    by_local = {
        str(getattr(row, 'local_ref', '') or ''): row
        for row in list(getattr(draft, 'rows', []) or [])
        if str(getattr(row, 'local_ref', '') or '')
    }
    by_row_ref = {
        str(getattr(row, 'row_ref', '') or ''): row
        for row in list(getattr(draft, 'rows', []) or [])
        if str(getattr(row, 'row_ref', '') or '')
    }
    return by_local, by_row_ref


def _resolve_local_ref(intent: MappingIntent, draft: MappingDraft) -> tuple[str, str]:
    by_local, by_row_ref = _row_maps(draft)
    raw_local = str(getattr(intent, 'local_ref', '') or '')
    raw_row = str(getattr(intent, 'row_ref', '') or '')
    if raw_local in by_local:
        return raw_local, str(getattr(by_local[raw_local], 'row_ref', '') or raw_row)
    if raw_row in by_row_ref:
        row = by_row_ref[raw_row]
        return str(getattr(row, 'local_ref', '') or ''), raw_row
    return raw_local, raw_row


def _local_file_count(dossier: CaseDossier, local_ref: str) -> int:
    span = next((card for card in list(getattr(dossier, 'local_span_cards', []) or []) if str(getattr(card, 'ref', '') or '') == local_ref), None)
    if span is not None:
        return int(getattr(span, 'file_ref_count', 0) or len(list(getattr(span, 'file_refs', []) or [])) or 0)
    if any(str(getattr(card, 'ref', '') or '') == local_ref for card in list(getattr(dossier, 'local_files', []) or [])):
        return 1
    return 0


def _blocked(
    intent: MappingIntent,
    *,
    local_ref: str = '',
    row_ref: str = '',
    issue_codes: list[str],
    requested_request_types: list[EvidenceRequestType] | None = None,
    candidate_target_refs: list[str] | None = None,
    reason: str = '',
    recommended_next_observation: str = '',
) -> BlockedMappingIntent:
    return BlockedMappingIntent(
        intent_ref=str(getattr(intent, 'intent_ref', '') or ''),
        local_ref=local_ref or str(getattr(intent, 'local_ref', '') or ''),
        row_ref=row_ref or str(getattr(intent, 'row_ref', '') or ''),
        decision=getattr(intent, 'decision', 'needs_more_evidence') or 'needs_more_evidence',
        issue_codes=_dedupe(issue_codes),
        requested_request_types=_dedupe_request_types(list(requested_request_types or getattr(intent, 'requested_request_types', []) or [])),
        query_hints=list(getattr(intent, 'query_hints', []) or []),
        candidate_target_refs=_dedupe(list(candidate_target_refs or [])),
        subject_refs=_dedupe([str(getattr(intent, 'chosen_subject_ref', '') or ''), *[str(ref or '') for ref in list(getattr(intent, 'subject_refs', []) or [])]]),
        item_refs=_dedupe([str(getattr(intent, 'chosen_item_ref', '') or ''), *[str(ref or '') for ref in list(getattr(intent, 'item_refs', []) or [])]]),
        support_refs=list(getattr(intent, 'support_refs', []) or []),
        reason=reason or str(getattr(intent, 'reason', '') or ''),
        recommended_next_observation=recommended_next_observation,
    )


def _span_matches_intent(span, intent: MappingIntent, local_count: int) -> bool:
    expected_subject = str(getattr(intent, 'chosen_subject_ref', '') or '')
    if expected_subject and str(getattr(span, 'subject_ref', '') or '') != expected_subject:
        return False
    target_count = int(getattr(span, 'target_ref_count', 0) or len(list(getattr(span, 'target_refs', []) or [])) or 0)
    if local_count and target_count and target_count != local_count:
        return False
    start = getattr(intent, 'episode_start', None)
    end = getattr(intent, 'episode_end', None)
    if start is None or end is None:
        return True
    return (
        getattr(span, 'sort_start', None) == start
        and getattr(span, 'sort_end', None) == end
    ) or (
        getattr(span, 'ep_start', None) == start
        and getattr(span, 'ep_end', None) == end
    )


def _sample(values: list[str], *, limit: int = 4) -> list[str]:
    values = [value for value in values if value]
    if len(values) <= limit:
        return list(values)
    edge = max(1, limit // 2)
    return _dedupe([*values[:edge], *values[-edge:]])[:limit]


def _agent_selected_span_from_items(
    dossier: CaseDossier,
    intent: MappingIntent,
    *,
    local_ref: str,
    row_ref: str,
    local_count: int,
    existing_span_refs: set[str],
    generated_index: int,
) -> tuple[BangumiSpanCard | None, BlockedMappingIntent | None]:
    raw_item_refs = _dedupe([str(ref or '') for ref in list(getattr(intent, 'item_refs', []) or [])])
    if not raw_item_refs:
        return None, None
    item_by_ref = {
        str(getattr(item, 'ref', '') or ''): item
        for item in list(getattr(dossier, 'bangumi_items', []) or [])
        if str(getattr(item, 'ref', '') or '')
    }
    unknown_refs = [ref for ref in raw_item_refs if ref not in item_by_ref]
    if unknown_refs:
        return None, _blocked(
            intent,
            local_ref=local_ref,
            row_ref=row_ref,
            issue_codes=['unknown_item_ref'],
            candidate_target_refs=raw_item_refs,
            reason=f'item_refs are not visible Bangumi items: {unknown_refs}',
            recommended_next_observation='execute episode_list/target evidence or use only visible BE item refs',
        )
    if len(raw_item_refs) != local_count:
        return None, _blocked(
            intent,
            local_ref=local_ref,
            row_ref=row_ref,
            issue_codes=['item_ref_count_mismatch'],
            candidate_target_refs=raw_item_refs,
            reason=f'agent selected {len(raw_item_refs)} item refs for {local_count} local files',
            recommended_next_observation='provide exactly one visible BE item per local file, or request target_span evidence',
        )
    chosen_subject = str(getattr(intent, 'chosen_subject_ref', '') or '')
    items = [item_by_ref[ref] for ref in raw_item_refs]
    subjects = _dedupe([str(getattr(item, 'subject_ref', '') or '') for item in items])
    if chosen_subject and any(subject != chosen_subject for subject in subjects):
        return None, _blocked(
            intent,
            local_ref=local_ref,
            row_ref=row_ref,
            issue_codes=['item_subject_mismatch'],
            candidate_target_refs=raw_item_refs,
            reason='selected item_refs do not all belong to chosen_subject_ref',
            recommended_next_observation='choose item refs from the same visible subject or split the local row',
        )
    if len(subjects) > 1:
        return None, _blocked(
            intent,
            local_ref=local_ref,
            row_ref=row_ref,
            issue_codes=['mixed_subject_item_refs'],
            candidate_target_refs=raw_item_refs,
            reason='selected item_refs span multiple Bangumi subjects',
            recommended_next_observation='choose a single subject item sequence or split the local row',
        )
    ref_base = ''.join(ch if ch.isalnum() else '_' for ch in (row_ref or local_ref or 'ROW'))
    span_ref = f'BES_INTENT_{ref_base}_{generated_index}'
    while span_ref in existing_span_refs:
        generated_index += 1
        span_ref = f'BES_INTENT_{ref_base}_{generated_index}'
    item_kinds = [
        'regular' if str(getattr(item, 'item_kind', '') or '') in {'episode', 'regular', 'unknown'} else str(getattr(item, 'item_kind', '') or 'unknown')
        for item in items
    ]
    span_kind = 'regular' if set(item_kinds) <= {'regular'} else ('mixed' if len(set(item_kinds)) > 1 else item_kinds[0])
    return BangumiSpanCard(
        ref=span_ref,
        subject_ref=chosen_subject or (subjects[0] if subjects else ''),
        target_refs=raw_item_refs,
        target_ref_count=len(raw_item_refs),
        target_ref_range=[raw_item_refs[0], raw_item_refs[-1]],
        target_ref_samples=_sample(raw_item_refs),
        sort_start=min((getattr(item, 'sort', None) for item in items if getattr(item, 'sort', None) is not None), default=None),
        sort_end=max((getattr(item, 'sort', None) for item in items if getattr(item, 'sort', None) is not None), default=None),
        ep_start=min((getattr(item, 'ep', None) for item in items if getattr(item, 'ep', None) is not None), default=None),
        ep_end=max((getattr(item, 'ep', None) for item in items if getattr(item, 'ep', None) is not None), default=None),
        item_kind=span_kind if span_kind in {'regular', 'special', 'mixed'} else 'unknown',
        gap_count=0,
        duplicate_count=0,
        special_count=sum(1 for item in items if str(getattr(item, 'item_kind', '') or '') in {'special', 'movie'}),
        title_samples=_sample([str(getattr(item, 'title', '') or getattr(item, 'name_cn', '') or getattr(item, 'name', '') or '') for item in items]),
        detail_equivalent=True,
        source_request_ref='orchestrator_mapping_intent',
    ), None


def _matching_visible_span_refs(dossier: CaseDossier, intent: MappingIntent, local_count: int) -> list[str]:
    return [
        str(getattr(span, 'ref', '') or '')
        for span in list(getattr(dossier, 'bangumi_span_cards', []) or [])
        if str(getattr(span, 'ref', '') or '')
        and bool(getattr(span, 'detail_equivalent', False))
        and _span_matches_intent(span, intent, local_count)
    ]


def _candidate_span_refs_for_row(dossier: CaseDossier, row: MappingDraftRow, intent: MappingIntent, local_count: int) -> list[str]:
    span_by_ref = {
        str(getattr(span, 'ref', '') or ''): span
        for span in list(getattr(dossier, 'bangumi_span_cards', []) or [])
        if str(getattr(span, 'ref', '') or '') and bool(getattr(span, 'detail_equivalent', False))
    }
    return [
        ref
        for ref in list(getattr(row, 'candidate_target_refs', []) or [])
        if ref in span_by_ref and _span_matches_intent(span_by_ref[ref], intent, local_count)
    ]


def _request_types_for_missing_target(dossier: CaseDossier, intent: MappingIntent, local_count: int) -> list[EvidenceRequestType]:
    subject_ref = str(getattr(intent, 'chosen_subject_ref', '') or '')
    subject_items = [
        item for item in list(getattr(dossier, 'bangumi_items', []) or [])
        if subject_ref and str(getattr(item, 'subject_ref', '') or '') == subject_ref
    ]
    requested: list[EvidenceRequestType] = []
    if subject_ref and not subject_items:
        requested.append('episode_list')
        requested.append('subject_lookup')
    if subject_ref and local_count != 1:
        requested.append('target_span')
    elif subject_ref and local_count == 1:
        requested.append('episode_list')
    if not subject_ref:
        requested.append('subject_search')
    return _dedupe_request_types([*list(getattr(intent, 'requested_request_types', []) or []), *requested])


class MappingIntentCompiler:
    def compile(self, dossier: CaseDossier, draft: MappingDraft, intents: list[MappingIntent]) -> MappingIntentCompilerResult:
        visible_refs = _visible_refs(dossier)
        subject_refs = {str(getattr(card, 'ref', '') or '') for card in list(getattr(dossier, 'bangumi_subjects', []) or [])}
        item_refs = {str(getattr(card, 'ref', '') or '') for card in list(getattr(dossier, 'bangumi_items', []) or [])}
        span_refs = {
            str(getattr(card, 'ref', '') or '')
            for card in list(getattr(dossier, 'bangumi_span_cards', []) or [])
            if str(getattr(card, 'ref', '') or '')
        }
        row_by_local, _row_by_ref = _row_maps(draft)
        compiled: list[MappingDraftPatch] = []
        blocked: list[BlockedMappingIntent] = []
        generated_span_cards: list[BangumiSpanCard] = []

        for intent in list(intents or []):
            local_ref, row_ref = _resolve_local_ref(intent, draft)
            decision = str(getattr(intent, 'decision', '') or '')
            if not local_ref or local_ref not in row_by_local:
                blocked.append(_blocked(intent, local_ref=local_ref, row_ref=row_ref, issue_codes=['unknown_local_ref'], recommended_next_observation='choose a visible draft row local_ref or row_ref'))
                continue
            support_refs = _dedupe([
                *[str(ref or '') for ref in list(getattr(intent, 'support_refs', []) or [])],
                local_ref,
            ])
            hidden_support = [ref for ref in support_refs if ref and ref not in visible_refs]
            if hidden_support:
                blocked.append(_blocked(intent, local_ref=local_ref, row_ref=row_ref, issue_codes=['hidden_ref_rejected'], reason=f'hidden support refs: {hidden_support}', recommended_next_observation='retry the intent using only visible refs'))
                continue
            chosen_subject = str(getattr(intent, 'chosen_subject_ref', '') or '')
            chosen_item = str(getattr(intent, 'chosen_item_ref', '') or '')
            chosen_span = str(getattr(intent, 'chosen_span_ref', '') or '')
            if chosen_subject and chosen_subject not in subject_refs:
                blocked.append(_blocked(intent, local_ref=local_ref, row_ref=row_ref, issue_codes=['unknown_subject_ref'], recommended_next_observation='materialize or execute subject_search evidence before choosing this subject'))
                continue
            if chosen_item and chosen_item not in item_refs:
                blocked.append(_blocked(intent, local_ref=local_ref, row_ref=row_ref, issue_codes=['unknown_item_ref'], recommended_next_observation='execute episode_list/related evidence before choosing this BE item'))
                continue
            if chosen_span and chosen_span not in span_refs:
                blocked.append(_blocked(intent, local_ref=local_ref, row_ref=row_ref, issue_codes=['unknown_target_span_ref'], requested_request_types=['target_span'], recommended_next_observation='execute target_span for the chosen subject/local row before choosing this BES span'))
                continue

            local_count = _local_file_count(dossier, local_ref)
            row = row_by_local[local_ref]
            if decision == 'map_explicit_item':
                if not chosen_item:
                    blocked.append(_blocked(intent, local_ref=local_ref, row_ref=row_ref, issue_codes=['missing_chosen_item_ref'], requested_request_types=_request_types_for_missing_target(dossier, intent, local_count), recommended_next_observation='choose a visible BE item or request episode/related evidence'))
                    continue
                if local_count != 1:
                    blocked.append(_blocked(intent, local_ref=local_ref, row_ref=row_ref, issue_codes=['invalid_explicit_multi_file_mapping'], requested_request_types=_request_types_for_missing_target(dossier, intent, local_count), recommended_next_observation='multi-file rows need a BES span intent or target_span evidence'))
                    continue
                compiled.append(MappingDraftPatch(
                    op='map_to_bangumi',
                    local_ref=local_ref,
                    target_ref=chosen_item,
                    mapping_mode='explicit',
                    support_refs=_dedupe([*support_refs, chosen_item, chosen_subject]),
                    reason_kind=str(getattr(intent, 'reason_kind', '') or ''),
                    reason=str(getattr(intent, 'reason', '') or ''),
                ))
                continue

            if decision == 'map_regular_span':
                candidate_span_refs = _candidate_span_refs_for_row(dossier, row, intent, local_count)
                all_matching_span_refs = _matching_visible_span_refs(dossier, intent, local_count)
                target_span = chosen_span
                if not target_span and len(candidate_span_refs) == 1:
                    target_span = candidate_span_refs[0]
                if not target_span and not candidate_span_refs and len(all_matching_span_refs) == 1:
                    target_span = all_matching_span_refs[0]
                if target_span:
                    compiled.append(MappingDraftPatch(
                        op='map_to_bangumi',
                        local_ref=local_ref,
                        target_span_ref=target_span,
                        mapping_mode='span_by_index',
                        support_refs=_dedupe([*support_refs, target_span, chosen_subject]),
                        reason_kind=str(getattr(intent, 'reason_kind', '') or ''),
                        reason=str(getattr(intent, 'reason', '') or ''),
                    ))
                    continue
                generated_span, generated_block = _agent_selected_span_from_items(
                    dossier,
                    intent,
                    local_ref=local_ref,
                    row_ref=row_ref,
                    local_count=local_count,
                    existing_span_refs={*span_refs, *[card.ref for card in generated_span_cards]},
                    generated_index=len(generated_span_cards) + 1,
                )
                if generated_block is not None:
                    blocked.append(generated_block)
                    continue
                if generated_span is not None:
                    generated_span_cards.append(generated_span)
                    span_refs.add(generated_span.ref)
                    compiled.append(MappingDraftPatch(
                        op='map_to_bangumi',
                        local_ref=local_ref,
                        target_span_ref=generated_span.ref,
                        mapping_mode='span_by_index',
                        support_refs=_dedupe([*support_refs, generated_span.ref, *generated_span.target_refs, chosen_subject]),
                        reason_kind=str(getattr(intent, 'reason_kind', '') or ''),
                        reason=str(getattr(intent, 'reason', '') or ''),
                    ))
                    continue
                ambiguous_refs = candidate_span_refs or all_matching_span_refs
                if len(ambiguous_refs) > 1:
                    blocked.append(_blocked(
                        intent,
                        local_ref=local_ref,
                        row_ref=row_ref,
                        issue_codes=['ambiguous_visible_target_candidates'],
                        candidate_target_refs=ambiguous_refs,
                        reason='multiple visible BES candidates match the semantic subject/range; the agent must choose one explicitly',
                        recommended_next_observation='choose one visible candidate_target_ref as chosen_span_ref, or request more evidence if the candidates are semantically indistinguishable',
                    ))
                    continue
                if chosen_item and local_count == 1:
                    compiled.append(MappingDraftPatch(
                        op='map_to_bangumi',
                        local_ref=local_ref,
                        target_ref=chosen_item,
                        mapping_mode='explicit',
                        support_refs=_dedupe([*support_refs, chosen_item, chosen_subject]),
                        reason_kind=str(getattr(intent, 'reason_kind', '') or ''),
                        reason=str(getattr(intent, 'reason', '') or ''),
                    ))
                    continue
                requested = _request_types_for_missing_target(dossier, intent, local_count)
                blocked.append(_blocked(intent, local_ref=local_ref, row_ref=row_ref, issue_codes=['target_span_or_item_not_visible'], requested_request_types=requested, recommended_next_observation='execute the requested Bangumi evidence, then propose the same semantic mapping intent again'))
                continue

            if decision == 'reject_candidate':
                rejected_ref = chosen_span or chosen_item
                if not rejected_ref:
                    blocked.append(_blocked(
                        intent,
                        local_ref=local_ref,
                        row_ref=row_ref,
                        issue_codes=['missing_rejected_candidate_ref'],
                        candidate_target_refs=list(getattr(row, 'candidate_target_refs', []) or []),
                        recommended_next_observation='choose the visible BE/BES candidate to reject with chosen_item_ref or chosen_span_ref',
                    ))
                    continue
                if rejected_ref not in list(getattr(row, 'candidate_target_refs', []) or []):
                    blocked.append(_blocked(
                        intent,
                        local_ref=local_ref,
                        row_ref=row_ref,
                        issue_codes=['candidate_ref_not_on_row'],
                        candidate_target_refs=list(getattr(row, 'candidate_target_refs', []) or []),
                        reason=f'{rejected_ref} is not a current candidate target for the row',
                        recommended_next_observation='reject only visible candidate_target_refs from this row, or map/request evidence instead',
                    ))
                    continue
                compiled.append(MappingDraftPatch(
                    op='reject_candidate',
                    local_ref=local_ref,
                    target_ref=rejected_ref if rejected_ref.startswith('BE') and not rejected_ref.startswith('BES') else '',
                    target_span_ref=rejected_ref if rejected_ref.startswith('BES') else '',
                    support_refs=_dedupe([*support_refs, rejected_ref, chosen_subject]),
                    reason_kind=str(getattr(intent, 'reason_kind', '') or ''),
                    reason=str(getattr(intent, 'reason', '') or ''),
                ))
                continue

            if decision == 'mark_non_bangumi_or_supplemental':
                compiled.append(MappingDraftPatch(
                    op='mark_non_bangumi_or_supplemental',
                    local_ref=local_ref,
                    support_refs=support_refs,
                    reason_kind=str(getattr(intent, 'reason_kind', '') or ''),
                    reason=str(getattr(intent, 'reason', '') or ''),
                ))
                continue

            if decision == 'mark_unaligned_fail_closed':
                compiled.append(MappingDraftPatch(
                    op='mark_unaligned_fail_closed',
                    local_ref=local_ref,
                    support_refs=support_refs,
                    reason_kind=str(getattr(intent, 'reason_kind', '') or ''),
                    reason=str(getattr(intent, 'reason', '') or ''),
                ))
                continue

            compiled.append(MappingDraftPatch(
                op='needs_more_evidence',
                local_ref=local_ref,
                support_refs=support_refs,
                reason_kind=str(getattr(intent, 'reason_kind', '') or 'ambiguous_candidate'),
                requested_request_types=list(getattr(intent, 'requested_request_types', []) or []),
                query_hints=list(getattr(intent, 'query_hints', []) or []),
                subject_refs=_dedupe([chosen_subject, *[str(ref or '') for ref in list(getattr(intent, 'subject_refs', []) or [])]]),
                item_refs=_dedupe([chosen_item, *[str(ref or '') for ref in list(getattr(intent, 'item_refs', []) or [])]]),
                local_refs=_dedupe([local_ref, *[str(ref or '') for ref in list(getattr(intent, 'local_refs', []) or [])]]),
                reason=str(getattr(intent, 'reason', '') or ''),
            ))

        requested_evidence = _dedupe_request_types([
            request_type
            for item in blocked
            for request_type in list(getattr(item, 'requested_request_types', []) or [])
        ])
        recommended = 'apply compiled draft patches or finish_case if accounting is ready'
        if blocked:
            recommended = 'execute requested evidence or revise semantic intents using visible refs'
        return MappingIntentCompilerResult(
            compiled_patches=compiled,
            blocked_intents=blocked,
            generated_span_cards=generated_span_cards,
            requested_evidence=requested_evidence,
            recommended_next_observation=recommended,
        )
