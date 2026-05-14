from __future__ import annotations

from typing import Any

from .models import EvidenceRequest
from .surface_ledger import build_surface_ledger
from .models import CaseDossier
from .special_investigation import is_special_eligible_span, special_eligible_open_row_refs
from .workspace import CaseEvidenceWorkspace


def _coerce_dossier(source: CaseEvidenceWorkspace | CaseDossier):
    return source.to_dossier() if isinstance(source, CaseEvidenceWorkspace) else source


def _window(refs: list[str], width: int) -> list[str]:
    width = max(1, min(width, 4))
    return [ref for ref in refs[:width] if ref]


def _bounded_refs(source: object, dossier: CaseDossier, attr: str, max_width: int) -> list[str]:
    values = list(getattr(getattr(source, 'visible_refs', None), attr, []) or [])
    if not values:
        values = list(getattr(dossier.visible_refs, attr, []) or [])
    return _window(list(dict.fromkeys(values)), max_width)


def _row_subject_refs(mapping_rows: list[object], *, local_ref: str = '', max_width: int = 8) -> list[str]:
    refs: list[str] = []
    for row in list(mapping_rows or []):
        if local_ref and str(getattr(row, 'local_ref', '') or '') != local_ref:
            continue
        refs.extend(str(ref or '') for ref in list(getattr(row, 'subject_refs', []) or []) if str(ref or ''))
    return list(dict.fromkeys(refs))[:max_width]


def _prioritized_subject_refs(
    source: object,
    dossier: CaseDossier,
    mapping_rows: list[object],
    *,
    local_ref: str = '',
    max_width: int = 8,
) -> list[str]:
    explicit_refs = _row_subject_refs(mapping_rows, local_ref=local_ref, max_width=max_width)
    visible_refs = _bounded_refs(source, dossier, 'bangumi_subject_refs', max_width)
    if not visible_refs:
        visible_refs = list(dict.fromkeys(
            str(getattr(item, 'subject_ref', '') or '')
            for item in list(getattr(dossier, 'bangumi_items', []) or [])
            if str(getattr(item, 'subject_ref', '') or '')
        ))[:max_width]
    return list(dict.fromkeys([*explicit_refs, *visible_refs]))[:max_width]


def _request_summary(request: EvidenceRequest) -> dict[str, Any]:
    summary = request.model_dump(mode='json')
    source_refs = []
    if summary.get('request_type') == 'target_span':
        source_refs = [summary.get('local_span_ref', '')]
        source_refs.extend(summary.get('subject_refs', []))
        source_refs.extend(summary.get('group_refs', []))
    else:
        source_refs = [
            *summary.get('anchor_file_refs', []),
            *summary.get('subject_refs', []),
            *summary.get('group_refs', []),
            *summary.get('item_refs', []),
            *summary.get('query_refs', []),
            summary.get('local_span_ref', ''),
        ]
    return {
        'request_id': summary.get('request_ref', ''),
        'request_type': summary.get('request_type', ''),
        'summary': summary.get('reason', '') or summary.get('request_type', ''),
        'source_refs': list(dict.fromkeys(source_refs))[:8],
        'expected_result': summary.get('expected_decision', 'unknown'),
        'neutral': True,
    }


def validate_prompt_summary_ids_subset(prompt_summaries: list[dict[str, Any]], registry: dict[str, EvidenceRequest]) -> list[str]:
    registry_ids = {str(request_id or '') for request_id in registry.keys() if str(request_id or '')}
    return [str(item.get('request_id') or '') for item in prompt_summaries if str(item.get('request_id') or '') and str(item.get('request_id') or '') not in registry_ids]


def _build_target_span_request(local_span, *, subject_refs: list[str], group_refs: list[str]) -> EvidenceRequest:
    span_ref = str(getattr(local_span, 'ref', '') or '')
    file_ref_samples = list(getattr(local_span, 'file_ref_samples', []) or [])
    count = int(getattr(local_span, 'file_ref_count', 0) or 0)
    raw_sort_start = getattr(local_span, 'episode_token_start', None)
    raw_sort_end = getattr(local_span, 'episode_token_end', None)
    sort_start = int(raw_sort_start) if raw_sort_start is not None else 0
    sort_end = int(raw_sort_end) if raw_sort_end is not None else 0
    has_explicit_token_window = bool(
        raw_sort_start is not None
        and raw_sort_end is not None
        and (sort_start > 0 or sort_end > sort_start)
        and sort_end >= sort_start
        and int(getattr(local_span, 'episode_token_count', 0) or 0) == count
        and int(getattr(local_span, 'gap_count', 0) or 0) == 0
        and int(getattr(local_span, 'duplicate_count', 0) or 0) == 0
    )
    return EvidenceRequest(
        request_ref=f'REQ_TARGET_SPAN_{span_ref}',
        request_type='target_span',
        local_span_ref=span_ref,
        anchor_file_refs=file_ref_samples,
        subject_refs=subject_refs,
        group_refs=group_refs,
        item_refs=[],
        expected_count=count,
        local_count=count,
        sort_start=sort_start if has_explicit_token_window else 0,
        sort_end=sort_end if has_explicit_token_window else 0,
        item_kind='regular',
        reason='local span needs span-level proof; explicit local episode token window' if has_explicit_token_window else 'local span needs span-level proof',
        expected_decision='need_more_evidence',
        priority='normal',
    )


def _has_executable_target_span_window(local_span) -> bool:
    count = int(getattr(local_span, 'file_ref_count', 0) or 0)
    raw_sort_start = getattr(local_span, 'episode_token_start', None)
    raw_sort_end = getattr(local_span, 'episode_token_end', None)
    sort_start = int(raw_sort_start) if raw_sort_start is not None else 0
    sort_end = int(raw_sort_end) if raw_sort_end is not None else 0
    return bool(
        count > 0
        and raw_sort_start is not None
        and raw_sort_end is not None
        and (sort_start > 0 or sort_end > sort_start)
        and sort_end >= sort_start
        and int(getattr(local_span, 'episode_token_count', 0) or 0) == count
        and int(getattr(local_span, 'gap_count', 0) or 0) == 0
        and int(getattr(local_span, 'duplicate_count', 0) or 0) == 0
    )


def _can_request_regular_target_span(local_span, dossier: CaseDossier) -> bool:
    return bool(
        _has_executable_target_span_window(local_span)
        and not is_special_eligible_span(local_span, dossier)
    )


def _is_budget_available(source: object, max_attr: str, used_attr: str) -> bool:
    budget = getattr(source, 'budget', None)
    max_value = int(getattr(budget, max_attr, 0) or 0)
    used_value = int(getattr(budget, used_attr, 0) or 0)
    return max_value == 0 or used_value < max_value


def _completed_or_failed_request_refs(source: object, dossier: CaseDossier) -> set[str]:
    refs = set()
    plan_state = getattr(source, 'plan_state', None) or getattr(dossier, 'plan_state', None)
    refs.update(str(ref or '') for ref in list(getattr(plan_state, 'completed_menu_request_ids', []) or []))
    refs.update(str(ref or '') for ref in list(getattr(plan_state, 'failed_menu_request_ids', []) or []))
    for holder in (source, dossier):
        for batch in list(getattr(holder, 'previous_evidence_results', []) or []):
            for result in list(getattr(batch, 'request_results', []) or getattr(batch, 'results', []) or []):
                request_ref = str(getattr(result, 'request_ref', '') or '')
                if request_ref:
                    refs.add(request_ref)
    return {ref for ref in refs if ref}


def _open_row_requests_subject_search(mapping_rows: list[object]) -> bool:
    for row in list(mapping_rows or []):
        disposition = str(getattr(row, 'disposition', '') or '')
        status = str(getattr(row, 'status', '') or '')
        if disposition in {'map_to_bangumi', 'non_bangumi_or_supplemental'} or status == 'verified':
            continue
        if 'subject_search' in [str(value or '') for value in list(getattr(row, 'requested_request_types', []) or [])]:
            return True
    return False


def _agent_composed_subject_search_queries(source: object, dossier: CaseDossier, *, max_width: int) -> list[object]:
    query_cards = list(getattr(source, 'query_cards', []) or []) or list(getattr(dossier, 'query_cards', []) or [])
    completed_or_failed = _completed_or_failed_request_refs(source, dossier)
    candidates = [
        card for card in query_cards
        if str(getattr(card, 'query_kind', '') or '') == 'subject_search'
        and str(getattr(card, 'query_origin', '') or '') == 'agent_composed'
        and str(getattr(card, 'ref', '') or '').startswith('QC')
        and str(getattr(card, 'query_text', '') or '').strip()
        and f'REQ_SUBJECT_SEARCH_{str(getattr(card, "ref", "") or "")}' not in completed_or_failed
    ]
    return candidates[:max_width]


def _build_subject_search_request(query_card: object) -> EvidenceRequest:
    query_ref = str(getattr(query_card, 'ref', '') or '')
    return EvidenceRequest(
        request_ref=f'REQ_SUBJECT_SEARCH_{query_ref}',
        request_type='subject_search',
        query_refs=[query_ref],
        max_subjects=4,
        reason='query card can recall Bangumi subject candidates',
        expected_decision='need_more_evidence',
        priority='normal',
    )


def _build_episode_list_request(subject_ref: str) -> EvidenceRequest:
    return EvidenceRequest(
        request_ref=f'REQ_EPISODE_LIST_{subject_ref}',
        request_type='episode_list',
        subject_refs=[subject_ref],
        include_episode_cards=True,
        max_episode_cards=240,
        reason='subject candidate needs visible episode targets',
        expected_decision='need_more_evidence',
        priority='normal',
    )


def _build_special_episode_list_request(subject_ref: str) -> EvidenceRequest:
    return EvidenceRequest(
        request_ref=f'REQ_SPECIAL_EPISODE_LIST_{subject_ref}',
        request_type='episode_list',
        subject_refs=[subject_ref],
        include_episode_cards=True,
        episode_scope='special',
        max_episode_cards=80,
        reason='special/singleton local row needs visible Bangumi special/movie/subject-level targets',
        expected_decision='need_more_evidence',
        priority='normal',
    )


def _build_special_related_request(subject_ref: str) -> EvidenceRequest:
    return EvidenceRequest(
        request_ref=f'REQ_SPECIAL_RELATED_{subject_ref}',
        request_type='related_expansion',
        subject_refs=[subject_ref],
        max_subjects=6,
        reason='special/singleton local row needs related movie/OVA/special subject evidence',
        expected_decision='need_more_evidence',
        priority='normal',
    )


def _build_special_subject_lookup_request(subject_ref: str) -> EvidenceRequest:
    return EvidenceRequest(
        request_ref=f'REQ_SPECIAL_SUBJECT_LOOKUP_{subject_ref}',
        request_type='subject_lookup',
        subject_refs=[subject_ref],
        reason='special/singleton local row needs subject form/platform detail before singleton target materialization',
        expected_decision='need_more_evidence',
        priority='normal',
    )


def _has_detail_equivalent_candidates(row) -> bool:
    candidates = list(getattr(row, 'candidate_target_refs', []) or [])
    return bool(candidates)


def _row_target_span_request_id(local_span) -> str:
    return f'REQ_TARGET_SPAN_{str(getattr(local_span, "ref", "") or "")}'


def build_executable_evidence_menu(source: CaseEvidenceWorkspace | CaseDossier, *, max_requests: int | None = None) -> dict[str, Any]:
    dossier = _coerce_dossier(source)
    menu = build_recommended_neutral_requests(source)
    summaries: list[dict[str, Any]] = []
    registry: dict[str, EvidenceRequest] = {}
    span_request_ids: list[str] = []
    span_rows_with_candidates = 0
    span_rows_without_candidates = 0

    neutral_index = 1
    for request in menu['recommended_neutral_requests']:
        req = request if isinstance(request, EvidenceRequest) else EvidenceRequest(**request)
        if not req.request_ref:
            req = req.model_copy(update={'request_ref': f'REQ_NEUTRAL_{neutral_index}'})
            neutral_index += 1
        if req.request_ref:
            summaries.append(_request_summary(req))
        registry[req.request_ref] = req

    local_span_cards = list(getattr(source, 'local_span_cards', []) or []) or list(getattr(dossier, 'local_span_cards', []) or [])
    non_package_spans = [card for card in local_span_cards if str(getattr(card, 'span_scope', '') or '') != 'package']
    mapping_rows = list(getattr(getattr(source, 'mapping_draft', None), 'rows', []) or []) or list(getattr(getattr(dossier, 'mapping_draft', None), 'rows', []) or [])
    row_by_local_ref = {str(getattr(row, 'local_ref', '') or ''): row for row in mapping_rows if str(getattr(row, 'local_ref', '') or '')}
    subject_refs = _prioritized_subject_refs(source, dossier, mapping_rows, max_width=8)
    group_refs = _bounded_refs(source, dossier, 'bangumi_group_refs', 4)
    special_row_refs = special_eligible_open_row_refs(getattr(source, 'mapping_draft', None) or getattr(dossier, 'mapping_draft', None), dossier)
    special_subject_refs = list(dict.fromkeys(
        [
            *list(getattr(getattr(dossier, 'visible_refs', None), 'bangumi_subject_refs', []) or []),
            *[
                str(getattr(subject, 'ref', '') or '')
                for subject in list(getattr(dossier, 'bangumi_subjects', []) or [])
                if str(getattr(subject, 'ref', '') or '')
            ],
        ]
    ))[:12]
    if special_row_refs and special_subject_refs:
        subject_by_ref = {
            str(getattr(subject, 'ref', '') or ''): subject
            for subject in list(getattr(dossier, 'bangumi_subjects', []) or [])
            if str(getattr(subject, 'ref', '') or '')
        }
        subject_lookup_needed = {
            str(getattr(subject, 'ref', '') or '')
            for subject in list(getattr(dossier, 'bangumi_subjects', []) or [])
            if str(getattr(subject, 'ref', '') or '') in special_subject_refs
            and not str(getattr(subject, 'source_form_hint', '') or '').strip()
        }
        for subject_ref in special_subject_refs:
            if subject_ref in subject_lookup_needed:
                req = _build_special_subject_lookup_request(subject_ref)
                if req.request_ref not in registry:
                    summaries.append(_request_summary(req))
                registry[req.request_ref] = req
            req = _build_special_episode_list_request(subject_ref)
            if req.request_ref not in registry:
                summaries.append(_request_summary(req))
            registry[req.request_ref] = req
            subject_card = subject_by_ref.get(subject_ref)
            if str(getattr(subject_card, 'source_role', '') or '') != 'related':
                req = _build_special_related_request(subject_ref)
                if req.request_ref not in registry:
                    summaries.append(_request_summary(req))
                registry[req.request_ref] = req

    for local_span in non_package_spans:
        row = row_by_local_ref.get(str(getattr(local_span, 'ref', '') or ''))
        if row is not None and _has_detail_equivalent_candidates(row):
            span_rows_with_candidates += 1
            if _can_request_regular_target_span(local_span, dossier):
                req = _build_target_span_request(
                    local_span,
                    subject_refs=_prioritized_subject_refs(source, dossier, mapping_rows, local_ref=str(getattr(local_span, 'ref', '') or ''), max_width=8),
                    group_refs=group_refs,
                )
                registry.setdefault(req.request_ref, req)
            continue
        span_rows_without_candidates += 1
        if not _can_request_regular_target_span(local_span, dossier):
            continue
        req = _build_target_span_request(
            local_span,
            subject_refs=_prioritized_subject_refs(source, dossier, mapping_rows, local_ref=str(getattr(local_span, 'ref', '') or ''), max_width=8),
            group_refs=group_refs,
        )
        if req.request_ref not in registry:
            span_request_ids.append(req.request_ref)
            summaries.append(_request_summary(req))
        registry[req.request_ref] = req

    if max_requests is not None and max_requests >= 0:
        summaries = summaries[:max_requests]

    planned_span_request_count = len(span_request_ids)
    selected_span_request_count = len([item for item in summaries if str(item.get('request_id') or '').startswith('REQ_TARGET_SPAN_')])
    completed_span_request_count = 0

    unknown_prompt_ids = validate_prompt_summary_ids_subset(summaries, registry)

    return {
        'prompt_summaries': summaries,
        'payload_registry': registry,
        'unknown_prompt_summary_ids': unknown_prompt_ids,
        'audit': {
            'planned_span_request_count': planned_span_request_count,
            'selected_span_request_count': selected_span_request_count,
            'completed_span_request_count': completed_span_request_count,
            'span_rows_with_candidates': span_rows_with_candidates,
            'span_rows_without_candidates': span_rows_without_candidates,
            'special_candidate_row_count': len(special_row_refs),
        },
    }


def build_recommended_neutral_requests(source: CaseEvidenceWorkspace | CaseDossier, *, max_width: int = 4) -> dict[str, Any]:
    dossier = _coerce_dossier(source)
    bounded = source if hasattr(source, 'target_overview') else dossier
    ledger = build_surface_ledger(dossier)
    visible_local = list(getattr(dossier.visible_refs, 'local_file_refs', []) or [])
    mapping_rows = list(getattr(getattr(source, 'mapping_draft', None), 'rows', []) or []) or list(getattr(getattr(dossier, 'mapping_draft', None), 'rows', []) or [])
    subject_refs = _prioritized_subject_refs(source, dossier, mapping_rows, max_width=max(4, max_width))
    seen_targets = [ref for ref in list((ledger.get('seen_detail') or {}).get('sample_refs') or []) if ref.startswith('BE')]
    assignable_targets = [ref for ref in list((ledger.get('assignable') or {}).get('sample_refs') or []) if ref.startswith('BE')]
    target_window = _window(list(dict.fromkeys([*seen_targets, *assignable_targets, *dossier.visible_refs.target_refs[:max_width]])), max_width)
    requests: list[dict[str, Any]] = []
    allow_subject_recall_with_surface = 'weak_subject_recall_retry_pending' in list(getattr(source, 'diagnostics', []) or [])
    allow_subject_recall_for_open_rows = _open_row_requests_subject_search(mapping_rows)
    if (
        (not subject_refs or allow_subject_recall_with_surface or allow_subject_recall_for_open_rows)
        and _is_budget_available(source, 'max_api_calls_per_case', 'used_api_calls')
        and _is_budget_available(source, 'max_subject_searches', 'used_subject_searches')
    ):
        for query_card in _agent_composed_subject_search_queries(source, dossier, max_width=max_width):
            requests.append(_build_subject_search_request(query_card).model_dump(mode='json'))
    if visible_local:
        requests.append({'request_type': 'local_file_detail', 'anchor_file_refs': _window(visible_local, max_width), 'reason': 'visible local refs'})
    if subject_refs:
        requests.append({'request_type': 'subject_lookup', 'subject_refs': _window(subject_refs, max_width), 'reason': 'subject refs'})
        item_subject_refs = {
            str(getattr(item, 'subject_ref', '') or '')
            for item in list(getattr(dossier, 'bangumi_items', []) or [])
            if str(getattr(item, 'subject_ref', '') or '')
        }
        for subject_ref in _window(subject_refs, max_width):
            if subject_ref not in item_subject_refs:
                requests.append(_build_episode_list_request(subject_ref).model_dump(mode='json'))
    if seen_targets:
        requests.append({'request_type': 'target_detail', 'item_refs': _window(seen_targets, max_width), 'reason': 'seen sample target refs'})
    if target_window:
        requests.append({'request_type': 'target_window', 'item_refs': target_window, 'reason': 'bounded sort window', 'sort_start': 0, 'sort_end': len(target_window) - 1})
    local_span_cards = list(getattr(source, 'local_span_cards', []) or []) or list(getattr(dossier, 'local_span_cards', []) or [])
    if local_span_cards:
        non_package_spans = [
            card for card in local_span_cards
            if str(getattr(card, 'span_scope', '') or '') != 'package'
        ]
        request_span_cards = non_package_spans or local_span_cards
        eligible_local_spans = [
            card for card in request_span_cards
            if int(getattr(card, 'file_ref_count', 0) or 0) >= 2
            and _can_request_regular_target_span(card, dossier)
        ]
        for local_span in eligible_local_spans:
            span_subject_refs = _prioritized_subject_refs(
                source,
                dossier,
                mapping_rows,
                local_ref=str(getattr(local_span, 'ref', '') or ''),
                max_width=max(4, max_width),
            )
            if not span_subject_refs:
                continue
            group_refs = _window(list(getattr(dossier.visible_refs, 'bangumi_group_refs', []) or []), max_width)
            requests.append(_build_target_span_request(local_span, subject_refs=span_subject_refs, group_refs=group_refs).model_dump(mode='json'))
    return {
        'recommended_neutral_requests': requests,
        'summary': {
            'count': len(requests),
            'max_width': max_width,
            'no_mapping': True,
            'no_choice_hint': True,
            'no_semantic_score': True,
            'round_context': getattr(bounded, 'round_context', 'initial'),
        },
    }


def recommended_neutral_requests(source: CaseEvidenceWorkspace | CaseDossier, *, max_width: int = 4) -> list[dict[str, Any]]:
    return build_recommended_neutral_requests(source, max_width=max_width)['recommended_neutral_requests']
