from __future__ import annotations

from typing import Any

from .broker_budget import BudgetLedger
from .broker_registry import EvidenceCardRegistry, build_provenance_card
from .models import BangumiSubjectCard, EvidenceRequest, EvidenceRequestResult, ProvenanceCard, QueryCard
from .source_form import infer_source_form_hint
from .workspace import CaseEvidenceWorkspace


def _get_query_card(workspace: CaseEvidenceWorkspace, ref: str) -> QueryCard | None:
    for card in workspace.query_cards:
        if card.ref == ref:
            return card
    return None


def _subject_kind(fake_subject: Any) -> str:
    value = getattr(fake_subject, 'type', None)
    if value in (2, '2', 'anime', 'Anime'):
        return 'anime'
    if getattr(fake_subject, 'subject_type', None) == 'anime':
        return 'anime'
    return 'unknown'


def execute_subject_search(
    request: EvidenceRequest,
    workspace: CaseEvidenceWorkspace,
    registry: EvidenceCardRegistry,
    budget: BudgetLedger,
    bangumi_client,
) -> tuple[list[BangumiSubjectCard], list[ProvenanceCard], EvidenceRequestResult, BudgetLedger]:
    if request.request_type != 'subject_search':
        return [], [], EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=['request_type must be subject_search']), budget
    if not request.query_refs:
        return [], [], EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=['query_refs required']), budget

    query_cards: list[QueryCard] = []
    for ref in request.query_refs:
        card = _get_query_card(workspace, ref)
        if card is None:
            return [], [], EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=[f'unknown query ref: {ref}']), budget
        if card.query_kind != 'subject_search':
            return [], [], EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=[f'query ref not allowed: {ref}']), budget
        query_cards.append(card)

    if not budget.can_consume_api_calls(1) or not budget.can_use_subject_search(1):
        return [], [], EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=['budget exceeded']), budget

    produced_subjects: list[BangumiSubjectCard] = []
    provenance_cards: list[ProvenanceCard] = []
    result_refs: list[str] = []
    try:
        search_text = query_cards[0].query_text
        year_hint = None
        results = bangumi_client.search_subjects(search_text, year_hint)
    except Exception as exc:
        return [], [], EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=[str(exc)]), budget.use_subject_search(1).consume_api_calls(1)

    max_results = request.max_subjects or len(results)
    max_results = min(max_results, len(results))
    if budget.budget.max_new_subject_cards:
        max_results = min(max_results, max(0, budget.budget.max_new_subject_cards - budget.budget.used_new_subject_cards))

    next_budget = budget.use_subject_search(1).consume_api_calls(1)

    for idx, result in enumerate(results, start=1):
        if len(produced_subjects) >= max_results:
            break
        if _subject_kind(result) != 'anime':
            continue
        subject_id = int(getattr(result, 'id', getattr(result, 'subject_id', 0)) or 0)
        subject_ref, is_new = registry.allocate_subject_ref(subject_id)
        if not is_new:
            result_refs.append(subject_ref)
            continue
        provenance_ref = registry.allocate_provenance_ref()
        provenance = build_provenance_card(
            ref=provenance_ref,
            retrieval_round=workspace.header.round_index,
            request_ref=request.request_ref,
            source_operation='subject_search',
            api_subject_id=subject_id,
            raw_response_count=1,
        )
        provenance_cards.append(provenance)
        produced_subjects.append(
            BangumiSubjectCard(
                ref=subject_ref,
                subject_id=subject_id,
                subject_type='anime',
                title=getattr(result, 'title', '') or getattr(result, 'name', ''),
                name=getattr(result, 'name', ''),
                name_cn=getattr(result, 'name_cn', ''),
                platform=getattr(result, 'platform', '') or '',
                eps=int(getattr(result, 'eps', 0) or 0),
                total_episodes=int(getattr(result, 'total_episodes', 0) or 0),
                source_form_hint=infer_source_form_hint(
                    platform=getattr(result, 'platform', '') or '',
                    tags=list(getattr(result, 'tags', []) or []),
                    name=getattr(result, 'name', '') or '',
                    name_cn=getattr(result, 'name_cn', '') or '',
                    total_episodes=int(getattr(result, 'total_episodes', 0) or 0),
                    eps=int(getattr(result, 'eps', 0) or 0),
                ),
                search_query_ref=query_cards[0].ref,
                search_rank=getattr(result, 'search_rank', idx),
                retrieval_round=workspace.header.round_index,
                provenance_ref=provenance_ref,
            )
        )
        result_refs.append(subject_ref)

    if not produced_subjects and not result_refs:
        return [], [], EvidenceRequestResult(request_ref=request.request_ref, accepted=True, notes=['empty']), next_budget

    return produced_subjects, provenance_cards, EvidenceRequestResult(request_ref=request.request_ref, accepted=True, response_refs=result_refs), next_budget
