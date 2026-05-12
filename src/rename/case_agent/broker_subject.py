from __future__ import annotations

from typing import Any

from src.bangumi.models import BangumiSubject

from .broker_budget import BudgetLedger
from .broker_registry import EvidenceCardRegistry, build_provenance_card
from .models import BangumiSubjectCard, EvidenceRequest, EvidenceRequestResult
from .source_form import infer_source_form_hint
from .workspace import CaseEvidenceWorkspace


def execute_subject_lookup(
    request: EvidenceRequest,
    workspace: CaseEvidenceWorkspace,
    registry: EvidenceCardRegistry,
    budget: BudgetLedger,
    bangumi_client: Any,
) -> tuple[list[BangumiSubjectCard], list, EvidenceRequestResult, BudgetLedger]:
    if request.request_type != 'subject_lookup':
        raise ValueError('request_type must be subject_lookup')

    if not request.subject_refs:
        return [], [], EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=['missing subject refs']), budget

    for subject_ref in request.subject_refs:
        if not subject_ref.startswith('BS') or subject_ref not in workspace.visible_refs().bangumi_subject_refs:
            return [], [], EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=[f'invisible subject ref: {subject_ref}']), budget

    if not budget.can_consume_api_calls(1):
        return [], [], EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=['api budget exhausted']), budget

    budget = budget.consume_api_calls(1)
    enriched_subjects: list[BangumiSubjectCard] = []
    provenance_cards = []

    for subject_ref in request.subject_refs:
        subject_id = _subject_id_from_workspace(workspace, subject_ref)
        if subject_id <= 0:
            return [], [], EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=[f'missing subject id for {subject_ref}']), budget

        api_subject = bangumi_client.get_subject(subject_id)
        if api_subject is None:
            return [], [], EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=[f'empty subject response for {subject_ref}']), budget

        provenance_ref = registry.allocate_provenance_ref()
        provenance = build_provenance_card(
            provenance_ref,
            retrieval_round=request.sort_start or 0,
            request_ref=request.request_ref,
            source_operation='subject_lookup',
            api_subject_id=subject_id,
            parent_refs=[subject_ref],
        )
        provenance_cards.append(provenance)
        enriched_subjects.append(_build_subject_card(subject_ref, api_subject, provenance_ref, request.sort_start or 0))

    result = EvidenceRequestResult(request_ref=request.request_ref, accepted=True, response_refs=[card.ref for card in enriched_subjects])
    return enriched_subjects, provenance_cards, result, budget


def _subject_id_from_workspace(workspace: CaseEvidenceWorkspace, subject_ref: str) -> int:
    for card in workspace.bangumi_subjects:
        if card.ref == subject_ref:
            return card.subject_id
    return 0


def _build_subject_card(ref: str, subject: BangumiSubject, provenance_ref: str, retrieval_round: int) -> BangumiSubjectCard:
    facts = []
    if subject.infobox:
        facts = [str(item) for item in subject.infobox]
    return BangumiSubjectCard(
        ref=ref,
        subject_id=subject.id,
        name=subject.name or '',
        name_cn=subject.name_cn or '',
        date=subject.date or '',
        summary_short=subject.summary or '',
        platform=subject.platform or '',
        eps=subject.eps or 0,
        total_episodes=subject.total_episodes or 0,
        tags=list(subject.tags or []),
        infobox_facts=facts,
        source_form_hint=infer_source_form_hint(
            platform=subject.platform or '',
            tags=list(subject.tags or []),
            name=subject.name or '',
            name_cn=subject.name_cn or '',
            total_episodes=subject.total_episodes or 0,
            eps=subject.eps or 0,
        ),
        source_form_evidence=[f'platform={subject.platform}'] if subject.platform else [],
        relation_to_main='',
        retrieval_round=retrieval_round,
        provenance_ref=provenance_ref,
    )
