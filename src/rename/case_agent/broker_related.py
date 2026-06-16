from __future__ import annotations

from math import inf
from typing import Any

from src.bangumi.relation_filters import (
    is_strict_related_relation,
    normalize_relation_name,
    strict_requested_relation_keys,
)

from .broker_budget import BudgetExceeded, BudgetLedger
from .broker_registry import EvidenceCardRegistry, build_provenance_card
from .models import (
    BangumiRelationCard,
    BangumiSubjectCard,
    EvidenceRequest,
    EvidenceRequestResult,
    ProvenanceCard,
)
from .source_form import infer_source_form_hint
from .workspace import CaseEvidenceWorkspace

_RELATION_KIND_TO_CARD_KIND = {
    '\u524d\u4f20': 'prequel',
    '\u7eed\u96c6': 'sequel',
    '\u756a\u5916\u7bc7': 'side_story',
    '\u4e0d\u540c\u6f14\u7ece': 'adaptation',
    '\u6f14\u7ece': 'parent',
    '\u884d\u751f': 'child',
    '\u603b\u96c6\u7bc7': 'unknown',
    'prequel': 'prequel',
    'sequel': 'sequel',
    'side_story': 'side_story',
    'side story': 'side_story',
    'adaptation': 'adaptation',
    'parent': 'parent',
    'child': 'child',
    'special': 'side_story',
}


def _normalize_relation_name(value: str) -> str:
    return normalize_relation_name(value)


def execute_related_expansion(
    request: EvidenceRequest,
    workspace: CaseEvidenceWorkspace,
    registry: EvidenceCardRegistry,
    budget: BudgetLedger,
    bangumi_client,
) -> tuple[
    list[BangumiSubjectCard],
    list[BangumiRelationCard],
    list[ProvenanceCard],
    EvidenceRequestResult,
    BudgetLedger,
]:
    if request.request_type != 'related_expansion':
        raise ValueError('request_type must be related_expansion')

    visible_subject_refs = set(workspace.visible_refs().bangumi_subject_refs)
    anchor_refs = list(request.subject_refs or [])
    if not anchor_refs or any(ref not in visible_subject_refs for ref in anchor_refs):
        return [], [], [], EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=['anchor not visible']), budget

    anchor_ref = anchor_refs[0]
    anchor_card = next((card for card in workspace.bangumi_subjects if card.ref == anchor_ref), None)
    if anchor_card is None or anchor_card.subject_id <= 0:
        return [], [], [], EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=['anchor subject missing']), budget

    allowed_relation_keys, disallowed_requested_relations = strict_requested_relation_keys(request.relation_kinds)
    if disallowed_requested_relations:
        notes = [f'skipped requested disallowed relation {value}' for value in disallowed_requested_relations]
    else:
        notes = []
    if request.relation_kinds and not allowed_relation_keys:
        return [], [], [], EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=notes), budget
    max_new_subjects = request.max_subjects if request.max_subjects > 0 else inf
    if budget.budget.max_new_subject_cards > 0:
        max_new_subjects = min(max_new_subjects, budget.budget.max_new_subject_cards - budget.budget.used_new_subject_cards)
    if max_new_subjects < 0:
        max_new_subjects = 0

    subject_cards: list[BangumiSubjectCard] = []
    relation_cards: list[BangumiRelationCard] = []
    provenance_cards: list[ProvenanceCard] = []
    new_subjects_created = 0

    try:
        budget = budget.consume_api_calls(1)
    except BudgetExceeded:
        return [], [], [], EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=['api budget exhausted']), budget

    relations = bangumi_client.get_related_subjects(anchor_card.subject_id) or []
    for relation in relations:
        relation_name = _normalize_relation_name(getattr(relation, 'relation', '') or '')
        if getattr(relation, 'type', 0) != 2:
            notes.append(f'skipped non-anime {relation.id}')
            continue
        if not is_strict_related_relation(relation_name) or relation_name.casefold() not in allowed_relation_keys:
            notes.append(f'skipped disallowed relation {relation_name or relation.id}')
            continue
        if max_new_subjects != inf and new_subjects_created >= int(max_new_subjects) and relation.id not in registry.subject_id_to_ref:
            notes.append(f'skipped over max_subjects {relation.id}')
            continue
        try:
            budget = budget.consume_api_calls(1)
        except BudgetExceeded:
            notes.append('api budget exhausted during relation fetch')
            break
        detail = bangumi_client.get_subject(relation.id)
        if not detail:
            notes.append(f'skipped missing subject {relation.id}')
            continue

        to_ref, is_new = registry.allocate_subject_ref(detail.id)
        if is_new:
            new_subjects_created += 1
            try:
                budget = budget.add_subject_cards(1)
            except BudgetExceeded:
                notes.append('subject budget exhausted')
                break
            provenance_ref = registry.allocate_provenance_ref()
            provenance_card = build_provenance_card(provenance_ref, 1, request.request_ref, 'related_expansion', api_subject_id=detail.id)
            provenance_cards.append(provenance_card)
            source_form_hint = infer_source_form_hint(
                platform=getattr(detail, 'platform', '') or '',
                tags=list(getattr(detail, 'tags', []) or []),
                name=getattr(detail, 'name', '') or '',
                name_cn=getattr(detail, 'name_cn', '') or '',
                relation=relation_name,
                relation_to_main=relation_name,
                total_episodes=getattr(detail, 'total_episodes', 0) or 0,
                eps=getattr(detail, 'eps', 0) or 0,
            )
            subject_cards.append(
                BangumiSubjectCard(
                    ref=to_ref,
                    subject_id=detail.id,
                    subject_type='anime' if getattr(detail, 'type', 0) == 2 else 'unknown',
                    name=getattr(detail, 'name', '') or '',
                    name_cn=getattr(detail, 'name_cn', '') or '',
                    date=getattr(detail, 'date', '') or '',
                    summary_short=(getattr(detail, 'summary', '') or '')[:120],
                    platform=getattr(detail, 'platform', '') or '',
                    eps=getattr(detail, 'eps', 0) or 0,
                    total_episodes=getattr(detail, 'total_episodes', 0) or 0,
                    source_form_hint=source_form_hint,
                    source_form_evidence=[f'platform={getattr(detail, "platform", "")}'] if getattr(detail, 'platform', '') else [],
                    source_role='related',
                    relation_to_main=relation_name,
                    relation_path_refs=[],
                    retrieval_round=1,
                    provenance_ref=provenance_ref,
                    relation_refs=[],
                )
            )
        else:
            provenance_ref = registry.allocate_provenance_ref()
            provenance_cards.append(build_provenance_card(provenance_ref, 1, request.request_ref, 'related_expansion', api_subject_id=detail.id))

        relation_ref, _ = registry.allocate_relation_ref(anchor_ref, to_ref, relation_name)
        relation_cards.append(
            BangumiRelationCard(
                ref=relation_ref,
                relation_kind=_RELATION_KIND_TO_CARD_KIND.get(relation_name, 'unknown'),
                source_subject_ref=anchor_ref,
                target_subject_ref=to_ref,
                provenance_ref=provenance_ref,
            )
        )
        if is_new:
            subject_cards[-1].relation_refs.append(relation_ref)

    result = EvidenceRequestResult(
        request_ref=request.request_ref,
        accepted=bool(subject_cards or relation_cards),
        response_refs=[card.ref for card in [*subject_cards, *relation_cards, *provenance_cards]],
        notes=notes,
    )
    return subject_cards, relation_cards, provenance_cards, result, budget
