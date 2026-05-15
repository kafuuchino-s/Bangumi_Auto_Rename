from __future__ import annotations

from .models import (
    BangumiItemCard,
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
    observation: dict[str, object] | None = None,
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
        observation=dict(observation or {}),
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


def _span_target_refs(span: BangumiSpanCard) -> list[str]:
    return [str(ref or '') for ref in list(getattr(span, 'target_refs', []) or []) if str(ref or '')]


def _span_target_count(span: BangumiSpanCard) -> int:
    refs = _span_target_refs(span)
    return int(getattr(span, 'target_ref_count', 0) or len(refs) or 0)


def _item_by_ref(dossier: CaseDossier) -> dict[str, BangumiItemCard]:
    return {
        str(getattr(item, 'ref', '') or ''): item
        for item in list(getattr(dossier, 'bangumi_items', []) or [])
        if str(getattr(item, 'ref', '') or '')
    }


def _span_shape_observation(dossier: CaseDossier, span: BangumiSpanCard, local_count: int) -> dict[str, object]:
    item_by_ref = _item_by_ref(dossier)
    target_refs = _span_target_refs(span)
    return {
        'local_file_count': local_count,
        'selected_span_ref': str(getattr(span, 'ref', '') or ''),
        'selected_span_target_ref_count': _span_target_count(span),
        'selected_span_target_refs': target_refs[:48],
        'selected_span_subject_ref': str(getattr(span, 'subject_ref', '') or ''),
        'selected_span_sort_start': getattr(span, 'sort_start', None),
        'selected_span_sort_end': getattr(span, 'sort_end', None),
        'selected_span_ep_start': getattr(span, 'ep_start', None),
        'selected_span_ep_end': getattr(span, 'ep_end', None),
        'selected_span_item_title_samples': _sample([
            str(getattr(item_by_ref.get(ref), 'title', '') or getattr(item_by_ref.get(ref), 'name_cn', '') or getattr(item_by_ref.get(ref), 'name', '') or '')
            for ref in target_refs
            if item_by_ref.get(ref) is not None
        ]),
        'valid_shapes': [
            'choose a detail-equivalent BES* whose target_ref_count equals the local row file count',
            'provide exactly one visible BE item_ref per local file so the compiler can generate a detail-equivalent span',
            'repartition the local row if it mixes separate resources',
            'mark_non_bangumi_or_supplemental(reason_kind=bangumi_target_absent) only if Bangumi lacks targets for the handled row',
        ],
    }


def _block_if_span_count_mismatch(
    dossier: CaseDossier,
    intent: MappingIntent,
    *,
    local_ref: str,
    row_ref: str,
    span: BangumiSpanCard,
    local_count: int,
) -> BlockedMappingIntent | None:
    target_count = _span_target_count(span)
    if local_count and target_count and target_count != local_count:
        return _blocked(
            intent,
            local_ref=local_ref,
            row_ref=row_ref,
            issue_codes=['count_mismatch'],
            requested_request_types=_request_types_for_missing_target(dossier, intent, local_count),
            candidate_target_refs=[str(getattr(span, 'ref', '') or '')],
            observation=_span_shape_observation(dossier, span, local_count),
            reason=f'agent selected a span with {target_count} targets for {local_count} local files',
            recommended_next_observation=(
                'the selected BES span cannot expand one-to-one for this local row. '
                'Choose a span with the same target count, provide one BE item_ref per local file, '
                'repartition the local row, or mark target_absent/supplemental if that is the semantic conclusion.'
            ),
        )
    return None


def _agent_selected_span_from_items(
    dossier: CaseDossier,
    intent: MappingIntent,
    *,
    local_ref: str,
    row_ref: str,
    local_count: int,
    existing_span_refs: set[str],
    generated_index: int,
    item_refs_override: list[str] | None = None,
) -> tuple[BangumiSpanCard | None, BlockedMappingIntent | None]:
    raw_item_refs = _dedupe([str(ref or '') for ref in list(item_refs_override if item_refs_override is not None else getattr(intent, 'item_refs', []) or [])])
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
        subject_item_refs = _subject_item_ref_observation(dossier, intent)
        return None, _blocked(
            intent,
            local_ref=local_ref,
            row_ref=row_ref,
            issue_codes=['item_ref_count_mismatch'],
            candidate_target_refs=raw_item_refs,
            reason=f'agent selected {len(raw_item_refs)} item refs for {local_count} local files',
            observation={
                'local_file_count': local_count,
                'selected_item_ref_count': len(raw_item_refs),
                'selected_item_refs': raw_item_refs[:24],
                'same_subject_visible_item_count': len(subject_item_refs),
                'same_subject_visible_item_refs': subject_item_refs[:48],
            },
            recommended_next_observation='provide exactly one visible BE item per local file, split/repartition the local row, mark target_absent/supplemental if Bangumi lacks per-file targets, or request target_span evidence',
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
        source_request_ref=f'INTENT_{local_ref}',
    ), None


def _intent_ref_values(intent: MappingIntent) -> list[str]:
    return _dedupe([
        str(getattr(intent, 'chosen_item_ref', '') or ''),
        str(getattr(intent, 'chosen_span_ref', '') or ''),
        *[str(ref or '') for ref in list(getattr(intent, 'item_refs', []) or [])],
        *[str(ref or '') for ref in list(getattr(intent, 'target_refs', []) or [])],
        *[str(ref or '') for ref in list(getattr(intent, 'candidate_target_refs', []) or [])],
        *[str(ref or '') for ref in list(getattr(intent, 'source_refs', []) or [])],
    ])


def _candidate_rejection_refs(intent: MappingIntent, row: MappingDraftRow) -> list[str]:
    row_candidates = set(str(ref or '') for ref in list(getattr(row, 'candidate_target_refs', []) or []) if str(ref or ''))
    selected = [ref for ref in _intent_ref_values(intent) if ref in row_candidates]
    return _dedupe(selected)


def _item_kind_matches_episode_scope(item_kind: str, episode_scope: str) -> bool:
    item_kind = item_kind or 'unknown'
    if episode_scope == 'special':
        return item_kind in {'special', 'movie', 'unknown'}
    if episode_scope == 'movie':
        return item_kind in {'movie', 'unknown'}
    return item_kind in {'episode', 'regular', 'unknown', ''}


def _agent_selected_item_refs_from_subject_range(dossier: CaseDossier, intent: MappingIntent, local_count: int) -> tuple[list[str], list[str]]:
    subject_ref = str(getattr(intent, 'chosen_subject_ref', '') or '')
    start = getattr(intent, 'episode_start', None)
    end = getattr(intent, 'episode_end', None)
    if not subject_ref or start is None or end is None or local_count <= 0:
        return [], []
    try:
        range_start = int(start)
        range_end = int(end)
    except (TypeError, ValueError):
        return [], []
    if range_end < range_start:
        return [], []
    expected_values = list(range(range_start, range_end + 1))
    if len(expected_values) != local_count:
        return [], []
    episode_scope = str(getattr(intent, 'episode_scope', '') or 'unknown')
    subject_items = [
        item for item in list(getattr(dossier, 'bangumi_items', []) or [])
        if str(getattr(item, 'subject_ref', '') or '') == subject_ref
        and str(getattr(item, 'ref', '') or '')
        and _item_kind_matches_episode_scope(str(getattr(item, 'item_kind', '') or ''), episode_scope)
    ]
    sequences: list[list[str]] = []
    for field_name in ('ep', 'sort'):
        values = []
        for item in subject_items:
            raw_value = getattr(item, field_name, None)
            if raw_value is None:
                continue
            try:
                value = int(raw_value)
            except (TypeError, ValueError):
                continue
            if range_start <= value <= range_end:
                values.append((value, str(getattr(item, 'ref', '') or ''), item))
        ordered = sorted(values, key=lambda value: (value[0], value[1]))
        if [value for value, _ref, _item in ordered] == expected_values:
            sequences.append([ref for _value, ref, _item in ordered])
    unique_sequences: list[list[str]] = []
    for sequence in sequences:
        if sequence and sequence not in unique_sequences:
            unique_sequences.append(sequence)
    if len(unique_sequences) == 1:
        return unique_sequences[0], []
    if len(unique_sequences) > 1:
        return [], _dedupe([ref for sequence in unique_sequences for ref in sequence])
    return [], []


def _subject_item_ref_observation(dossier: CaseDossier, intent: MappingIntent) -> list[str]:
    subject_ref = str(getattr(intent, 'chosen_subject_ref', '') or '')
    if not subject_ref:
        subject_refs = _dedupe([
            str(ref or '')
            for ref in list(getattr(intent, 'subject_refs', []) or [])
            if str(ref or '')
        ])
        if len(subject_refs) == 1:
            subject_ref = subject_refs[0]
    if not subject_ref:
        return []
    allowed_scope = str(getattr(intent, 'episode_scope', '') or 'unknown')
    items = [
        item for item in list(getattr(dossier, 'bangumi_items', []) or [])
        if str(getattr(item, 'subject_ref', '') or '') == subject_ref
        and str(getattr(item, 'ref', '') or '')
        and _item_kind_matches_episode_scope(str(getattr(item, 'item_kind', '') or ''), allowed_scope)
    ]
    return [
        str(getattr(item, 'ref', '') or '')
        for item in sorted(
            items,
            key=lambda item: (
                getattr(item, 'sort', 0) or 0,
                getattr(item, 'ep', 0) or 0,
                str(getattr(item, 'ref', '') or ''),
            ),
        )
    ]


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
        span_by_ref = {
            str(getattr(card, 'ref', '') or ''): card
            for card in list(getattr(dossier, 'bangumi_span_cards', []) or [])
            if str(getattr(card, 'ref', '') or '')
        }
        all_span_refs = {
            str(getattr(card, 'ref', '') or '')
            for card in list(getattr(dossier, 'bangumi_span_cards', []) or [])
            if str(getattr(card, 'ref', '') or '')
        }
        detail_span_refs = {
            str(getattr(card, 'ref', '') or '')
            for card in list(getattr(dossier, 'bangumi_span_cards', []) or [])
            if str(getattr(card, 'ref', '') or '') and bool(getattr(card, 'detail_equivalent', False))
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
            if chosen_span and chosen_span not in all_span_refs:
                blocked.append(_blocked(intent, local_ref=local_ref, row_ref=row_ref, issue_codes=['unknown_target_span_ref'], requested_request_types=['target_span'], recommended_next_observation='execute target_span for the chosen subject/local row before choosing this BES span'))
                continue

            local_count = _local_file_count(dossier, local_ref)
            row = row_by_local[local_ref]
            if decision == 'map_explicit_item':
                if not chosen_item:
                    blocked.append(_blocked(intent, local_ref=local_ref, row_ref=row_ref, issue_codes=['missing_chosen_item_ref'], requested_request_types=_request_types_for_missing_target(dossier, intent, local_count), recommended_next_observation='choose a visible BE item or request episode/related evidence'))
                    continue
                if local_count != 1:
                    subject_item_refs = _subject_item_ref_observation(dossier, intent)
                    blocked.append(_blocked(intent, local_ref=local_ref, row_ref=row_ref, issue_codes=['invalid_explicit_multi_file_mapping'], requested_request_types=_request_types_for_missing_target(dossier, intent, local_count), recommended_next_observation='multi-file rows need a BES span intent or target_span evidence'))
                    blocked[-1] = blocked[-1].model_copy(update={
                        'observation': {
                            'local_file_count': local_count,
                            'selected_item_ref_count': 1 if chosen_item else 0,
                            'selected_item_refs': [chosen_item] if chosen_item else [],
                            'same_subject_visible_item_count': len(subject_item_refs),
                            'same_subject_visible_item_refs': subject_item_refs[:48],
                            'valid_shapes': [
                                'map_regular_span with chosen_span_ref=BES*',
                                'map_regular_span with one visible BE item_ref per local file',
                                'repartition into singleton work units if each local file is a separate movie/special',
                                'mark_non_bangumi_or_supplemental(reason_kind=bangumi_target_absent) if Bangumi has no per-file targets',
                            ],
                        },
                        'recommended_next_observation': (
                            'multi-file rows cannot use one explicit BE item. Use a BES span, provide one BE item_ref per local file, '
                            'repartition into singleton rows if semantically separate, or mark target_absent/supplemental if Bangumi lacks targets.'
                        ),
                    })
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
                if target_span and target_span not in detail_span_refs:
                    generated_span, generated_block = _agent_selected_span_from_items(
                        dossier,
                        intent,
                        local_ref=local_ref,
                        row_ref=row_ref,
                        local_count=local_count,
                        existing_span_refs={*all_span_refs, *[card.ref for card in generated_span_cards]},
                        generated_index=len(generated_span_cards) + 1,
                    )
                    if generated_block is not None:
                        blocked.append(generated_block)
                        continue
                    if generated_span is not None:
                        generated_span_cards.append(generated_span)
                        all_span_refs.add(generated_span.ref)
                        detail_span_refs.add(generated_span.ref)
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
                    blocked.append(_blocked(intent, local_ref=local_ref, row_ref=row_ref, issue_codes=['target_span_not_detail_equivalent'], requested_request_types=['target_span'], recommended_next_observation='execute target_span for this local row and chosen subject before using this BES span as a mapping target, or provide explicit visible BE item_refs for this row'))
                    continue
                if not target_span and len(candidate_span_refs) == 1:
                    target_span = candidate_span_refs[0]
                if not target_span and not candidate_span_refs and len(all_matching_span_refs) == 1:
                    target_span = all_matching_span_refs[0]
                if target_span:
                    span_card = span_by_ref.get(target_span)
                    if span_card is not None:
                        mismatch_block = _block_if_span_count_mismatch(
                            dossier,
                            intent,
                            local_ref=local_ref,
                            row_ref=row_ref,
                            span=span_card,
                            local_count=local_count,
                        )
                        if mismatch_block is not None:
                            blocked.append(mismatch_block)
                            continue
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
                    existing_span_refs={*all_span_refs, *[card.ref for card in generated_span_cards]},
                    generated_index=len(generated_span_cards) + 1,
                )
                if generated_block is not None:
                    blocked.append(generated_block)
                    continue
                if generated_span is not None:
                    generated_span_cards.append(generated_span)
                    all_span_refs.add(generated_span.ref)
                    detail_span_refs.add(generated_span.ref)
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
                range_item_refs, ambiguous_range_refs = _agent_selected_item_refs_from_subject_range(dossier, intent, local_count)
                if ambiguous_range_refs:
                    blocked.append(_blocked(
                        intent,
                        local_ref=local_ref,
                        row_ref=row_ref,
                        issue_codes=['ambiguous_visible_item_range'],
                        candidate_target_refs=ambiguous_range_refs,
                        reason='chosen_subject_ref plus episode_start/episode_end matches multiple visible BE sequences; the agent must choose item_refs explicitly',
                        recommended_next_observation='retry map_regular_span with explicit visible item_refs for the intended sequence',
                    ))
                    continue
                if range_item_refs:
                    generated_span, generated_block = _agent_selected_span_from_items(
                        dossier,
                        intent,
                        local_ref=local_ref,
                        row_ref=row_ref,
                        local_count=local_count,
                        existing_span_refs={*all_span_refs, *[card.ref for card in generated_span_cards]},
                        generated_index=len(generated_span_cards) + 1,
                        item_refs_override=range_item_refs,
                    )
                    if generated_block is not None:
                        blocked.append(generated_block)
                        continue
                    if generated_span is not None:
                        generated_span_cards.append(generated_span)
                        all_span_refs.add(generated_span.ref)
                        detail_span_refs.add(generated_span.ref)
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
                rejected_refs = _candidate_rejection_refs(intent, row)
                provided_candidate_refs = [
                    ref for ref in _intent_ref_values(intent)
                    if ref.startswith(('BE', 'BES'))
                ]
                if not rejected_refs:
                    if provided_candidate_refs:
                        blocked.append(_blocked(
                            intent,
                            local_ref=local_ref,
                            row_ref=row_ref,
                            issue_codes=['candidate_ref_not_on_row'],
                            candidate_target_refs=list(getattr(row, 'candidate_target_refs', []) or []),
                            reason=f'candidate refs are not current candidates for the row: {provided_candidate_refs}',
                            recommended_next_observation='reject only visible candidate_target_refs from this row, or map/request evidence instead',
                        ))
                        continue
                    blocked.append(_blocked(
                        intent,
                        local_ref=local_ref,
                        row_ref=row_ref,
                        issue_codes=['missing_rejected_candidate_ref'],
                        candidate_target_refs=list(getattr(row, 'candidate_target_refs', []) or []),
                        recommended_next_observation='choose one or more visible row candidate_target_refs to reject with chosen_item_ref, chosen_span_ref, item_refs, target_refs, or candidate_target_refs',
                    ))
                    continue
                unmatched_refs = [ref for ref in provided_candidate_refs if ref not in rejected_refs]
                if unmatched_refs:
                    blocked.append(_blocked(
                        intent,
                        local_ref=local_ref,
                        row_ref=row_ref,
                        issue_codes=['candidate_ref_not_on_row'],
                        candidate_target_refs=list(getattr(row, 'candidate_target_refs', []) or []),
                        reason=f'candidate refs are not current candidates for the row: {unmatched_refs}',
                        recommended_next_observation='reject only visible candidate_target_refs from this row, or map/request evidence instead',
                    ))
                    continue
                for rejected_ref in rejected_refs:
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
            *[
                request_type
                for item in blocked
                for request_type in list(getattr(item, 'requested_request_types', []) or [])
            ],
            *[
                request_type
                for patch in compiled
                if str(getattr(patch, 'op', '') or '') == 'needs_more_evidence'
                for request_type in list(getattr(patch, 'requested_request_types', []) or [])
            ],
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
