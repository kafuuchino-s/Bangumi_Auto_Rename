from __future__ import annotations

from typing import Any

from src.bangumi.models import BangumiEpisode

from .broker_budget import BudgetLedger
from .broker_registry import EvidenceCardRegistry, build_provenance_card
from .models import BangumiGroupCard, BangumiItemCard, BangumiSpanCard, EvidenceRequest, EvidenceRequestResult, ProvenanceCard
from .source_form import is_subject_level_singleton_source, subject_card_source_form_hint
from .workspace import CaseEvidenceWorkspace


def execute_episode_list(
    request: EvidenceRequest,
    workspace: CaseEvidenceWorkspace,
    registry: EvidenceCardRegistry,
    budget: BudgetLedger,
    bangumi_client: Any,
) -> tuple[list[BangumiGroupCard], list[BangumiItemCard], list[ProvenanceCard], EvidenceRequestResult, BudgetLedger]:
    if request.request_type != 'episode_list':
        return [], [], [], EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=['request_type must be episode_list']), budget
    if not request.subject_refs:
        return [], [], [], EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=['subject_refs required']), budget
    for subject_ref in request.subject_refs:
        if not subject_ref.startswith('BS') or subject_ref not in workspace.visible_refs().bangumi_subject_refs:
            return [], [], [], EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=[f'invisible subject ref: {subject_ref}']), budget

    subject_ref = request.subject_refs[0]
    subject_id = _subject_id_from_workspace(workspace, subject_ref)
    if subject_id <= 0:
        return [], [], [], EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=['missing subject id']), budget
    if not budget.can_consume_api_calls(1):
        return [], [], [], EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=['api budget exhausted']), budget

    episodes = bangumi_client.get_episodes(subject_id)
    next_budget = budget.consume_api_calls(1)
    scope = request.episode_scope or 'all_if_small'
    selected = _select_list_episodes(scope, episodes, request)
    if scope == 'all_if_small' and request.max_episode_cards and len(episodes) > request.max_episode_cards:
        return [], [], [], EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=['episode list too large']), next_budget

    synthetic_singleton = None
    if not selected and scope == 'special':
        synthetic_singleton = _build_subject_level_singleton_episode(workspace, subject_ref, subject_id)
        selected = [synthetic_singleton] if synthetic_singleton is not None else []

    if not selected:
        return [], [], [], EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=['empty selection']), next_budget

    groups, items, provenance, next_budget = _materialize_new_items(
        request=request,
        workspace=workspace,
        registry=registry,
        budget=next_budget,
        subject_ref=subject_ref,
        subject_id=subject_id,
        episodes=selected,
        source_operation='episode_list',
    )
    result_refs = [card.ref for card in items]
    visible_selected_items = _selected_visible_items(workspace, selected, subject_ref)
    span_cards = _build_episode_list_span_cards(request, subject_ref, [*visible_selected_items, *items])
    return groups, items, provenance, EvidenceRequestResult(request_ref=request.request_ref, accepted=True, response_refs=list(dict.fromkeys([*[card.ref for card in visible_selected_items], *result_refs])), bangumi_span_cards=span_cards), next_budget


def execute_episode_detail(
    request: EvidenceRequest,
    workspace: CaseEvidenceWorkspace,
    registry: EvidenceCardRegistry,
    budget: BudgetLedger,
    bangumi_client: Any,
) -> tuple[list[BangumiItemCard], list[ProvenanceCard], EvidenceRequestResult, BudgetLedger]:
    if request.request_type != 'episode_detail':
        return [], [], EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=['request_type must be episode_detail']), budget

    visible_item_refs = [ref for ref in request.item_refs if ref in workspace.visible_refs().bangumi_item_refs]
    if request.item_refs and not visible_item_refs:
        return [], [], EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=['no visible item refs']), budget

    subject_ref = request.subject_refs[0] if request.subject_refs else _subject_ref_from_item_refs(workspace, visible_item_refs)
    if not subject_ref or subject_ref not in workspace.visible_refs().bangumi_subject_refs:
        return [], [], EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=['invisible or missing subject ref']), budget

    subject_id = _subject_id_from_workspace(workspace, subject_ref)
    if subject_id <= 0:
        return [], [], EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=['missing subject id']), budget
    if not budget.can_consume_api_calls(1):
        return [], [], EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=['api budget exhausted']), budget

    episodes = bangumi_client.get_episodes(subject_id)
    next_budget = budget.consume_api_calls(1)
    selected = _select_detail_episodes(request, workspace, episodes, visible_item_refs)
    enriched_items, provenance, next_budget = _enrich_existing_items(request, workspace, registry, next_budget, selected)
    result_refs = [card.ref for card in enriched_items]
    return enriched_items, provenance, EvidenceRequestResult(request_ref=request.request_ref, accepted=True, response_refs=result_refs), next_budget


def _select_list_episodes(scope: str, episodes: list[BangumiEpisode], request: EvidenceRequest) -> list[BangumiEpisode]:
    regular = [ep for ep in episodes if ep.type == 0]
    special = [ep for ep in episodes if ep.type != 0]
    if scope == 'regular':
        return regular
    if scope == 'special':
        return special
    if scope == 'tail_window':
        cap = request.max_episode_cards or 8
        return episodes[-cap:]
    if scope == 'explicit_sort_window':
        return [ep for ep in episodes if request.sort_start <= ep.sort <= request.sort_end]
    if scope == 'all_if_small':
        return episodes
    return episodes


def _select_detail_episodes(request: EvidenceRequest, workspace: CaseEvidenceWorkspace, episodes: list[BangumiEpisode], visible_item_refs: list[str]) -> list[BangumiEpisode]:
    if visible_item_refs:
        wanted_ids = { _episode_id_from_workspace(workspace, ref) for ref in visible_item_refs }
        return [ep for ep in episodes if ep.id in wanted_ids]
    if request.sort_start or request.sort_end:
        return [ep for ep in episodes if request.sort_start <= ep.sort <= request.sort_end]
    return episodes


def _materialize_new_items(
    *,
    request: EvidenceRequest,
    workspace: CaseEvidenceWorkspace,
    registry: EvidenceCardRegistry,
    budget: BudgetLedger,
    subject_ref: str,
    subject_id: int,
    episodes: list[BangumiEpisode],
    source_operation: str,
) -> tuple[list[BangumiGroupCard], list[BangumiItemCard], list[ProvenanceCard], BudgetLedger]:
    groups: list[BangumiGroupCard] = []
    items: list[BangumiItemCard] = []
    provenance: list[ProvenanceCard] = []
    new_count = 0
    subject_card = next((card for card in workspace.bangumi_subjects if card.ref == subject_ref), None)
    subject_singleton_source = is_subject_level_singleton_source(subject_card) if subject_card is not None else False
    for ep in episodes:
        synthetic_key = ''
        if bool(getattr(ep, 'synthetic', False)) or bool(getattr(ep, 'subject_level_target', False)):
            synthetic_key = f'{subject_ref}:subject_singleton:{ep.source_form_hint}:{ep.title or ep.name_cn or ep.name}'
        item_ref, is_new = registry.allocate_item_ref(ep.id, synthetic_key=synthetic_key)
        if not is_new:
            continue
        group_kind = 'special_group' if ep.type != 0 or subject_singleton_source else 'season_group'
        group_ref, group_new = registry.allocate_group_ref(subject_ref, group_kind)
        if group_new:
            groups.append(BangumiGroupCard(ref=group_ref, group_kind=group_kind, subject_refs=[subject_ref], item_refs=[item_ref]))
        prov_ref = registry.allocate_provenance_ref()
        provenance.append(build_provenance_card(prov_ref, workspace.header.round_index, request.request_ref, source_operation, api_subject_id=subject_id, api_episode_id=ep.id, parent_refs=[subject_ref]))
        items.append(_build_episode_card(item_ref, subject_ref, ep, prov_ref, subject_card=subject_card))
        new_count += 1
    return groups, items, provenance, budget.add_episode_cards(new_count)


def _enrich_existing_items(
    request: EvidenceRequest,
    workspace: CaseEvidenceWorkspace,
    registry: EvidenceCardRegistry,
    budget: BudgetLedger,
    episodes: list[BangumiEpisode],
) -> tuple[list[BangumiItemCard], list[ProvenanceCard], BudgetLedger]:
    items: list[BangumiItemCard] = []
    provenance: list[ProvenanceCard] = []
    for ep in episodes:
        item_ref, _ = registry.allocate_item_ref(ep.id)
        prov_ref = registry.allocate_provenance_ref()
        subject_ref = _subject_ref_from_workspace_item(workspace, item_ref)
        subject_card = next((card for card in workspace.bangumi_subjects if card.ref == subject_ref), None)
        provenance.append(build_provenance_card(prov_ref, workspace.header.round_index, request.request_ref, 'episode_detail', api_subject_id=ep.subject_id, api_episode_id=ep.id, parent_refs=[item_ref]))
        items.append(_build_episode_card(item_ref, subject_ref, ep, prov_ref, subject_card=subject_card))
    return items, provenance, budget


def _build_episode_card(ref: str, subject_ref: str, episode: BangumiEpisode, provenance_ref: str, *, subject_card: object | None = None) -> BangumiItemCard:
    subject_level_target = bool(getattr(episode, 'subject_level_target', False))
    source_form_hint = getattr(episode, 'source_form_hint', '') or ''
    if not source_form_hint or source_form_hint == 'unknown':
        source_form_hint = subject_card_source_form_hint(subject_card) if subject_card is not None else 'unknown'
    source_form_hint = source_form_hint or 'unknown'
    subject_singleton_source = is_subject_level_singleton_source(subject_card) if subject_card is not None else False
    if subject_level_target and source_form_hint == 'movie':
        item_kind = 'movie'
    elif getattr(episode, 'type', 0) != 0 or subject_level_target:
        item_kind = 'special'
    elif subject_singleton_source and source_form_hint == 'movie':
        item_kind = 'movie'
    elif subject_singleton_source and source_form_hint in {'ova', 'special'}:
        item_kind = 'special'
    else:
        item_kind = 'episode'
    return BangumiItemCard(
        ref=ref,
        item_kind=item_kind,
        episode_id=getattr(episode, 'id', 0) or 0,
        kind=str(getattr(episode, 'kind', '') or ''),
        type=str(getattr(episode, 'type', '')),
        sort=getattr(episode, 'sort', 0) or 0,
        ep=getattr(episode, 'ep', 0) or 0,
        subject_ref=subject_ref,
        title=getattr(episode, 'title', '') or getattr(episode, 'name_cn', '') or getattr(episode, 'name', ''),
        name=getattr(episode, 'name', '') or '',
        name_cn=getattr(episode, 'name_cn', '') or '',
        airdate=getattr(episode, 'airdate', '') or '',
        duration=getattr(episode, 'duration', '') or '',
        duration_seconds=getattr(episode, 'duration_seconds', 0) or 0,
        desc_short=(getattr(episode, 'desc', '') or '')[:160],
        synthetic=bool(getattr(episode, 'synthetic', False)),
        subject_level_target='true' if subject_level_target else 'false',
        source_form_hint=source_form_hint,
        relation_to_main=getattr(episode, 'relation_to_main', '') or getattr(episode, 'relation', '') or getattr(subject_card, 'relation_to_main', '') or '',
        provenance_ref=provenance_ref,
        episode_number=getattr(episode, 'ep', 0) or 0,
    )


def _build_episode_list_span_cards(request: EvidenceRequest, subject_ref: str, items: list[BangumiItemCard]) -> list[BangumiSpanCard]:
    items = list({str(getattr(item, 'ref', '') or ''): item for item in items if str(getattr(item, 'ref', '') or '')}.values())
    items = sorted(items, key=lambda item: (int(getattr(item, 'sort', 0) or 0), int(getattr(item, 'ep', 0) or 0), str(getattr(item, 'ref', '') or '')))
    if len(items) <= 1:
        return []
    item_kind = 'special' if all(str(getattr(item, 'item_kind', '') or '') in {'special', 'movie'} for item in items) else 'regular'
    if item_kind != 'special' and str(getattr(request, 'episode_scope', '') or '') != 'regular':
        return []
    refs = [item.ref for item in items if item.ref]
    if len(refs) != len(items):
        return []
    return [
        BangumiSpanCard(
            ref=f'BES_{request.request_ref}_{1}',
            subject_ref=subject_ref,
            group_ref='',
            target_refs=refs,
            target_ref_count=len(refs),
            target_ref_range=[refs[0], refs[-1]],
            target_ref_samples=_sample_refs(refs),
            sort_start=min((item.sort for item in items), default=None),
            sort_end=max((item.sort for item in items), default=None),
            ep_start=min((item.ep for item in items), default=None),
            ep_end=max((item.ep for item in items), default=None),
            item_kind=item_kind,
            gap_count=0,
            duplicate_count=0,
            special_count=sum(1 for item in items if str(getattr(item, 'item_kind', '') or '') in {'special', 'movie'}),
            title_samples=_sample_refs([item.title or item.name or item.name_cn for item in items if (item.title or item.name or item.name_cn)]),
            detail_equivalent=True,
            source_request_ref=request.request_ref,
        )
    ]


def _sample_refs(values: list[str], *, limit: int = 10) -> list[str]:
    values = [value for value in values if value]
    if len(values) <= limit:
        return list(values)
    half = max(1, limit // 2)
    return list(dict.fromkeys([*values[:half], *values[-half:]]))


def _selected_visible_items(workspace: CaseEvidenceWorkspace, selected: list[BangumiEpisode], subject_ref: str) -> list[BangumiItemCard]:
    selected_episode_ids = {int(getattr(ep, 'id', 0) or 0) for ep in selected if int(getattr(ep, 'id', 0) or 0) > 0}
    if not selected_episode_ids:
        return []
    return [
        item for item in list(getattr(workspace, 'bangumi_items', []) or [])
        if str(getattr(item, 'subject_ref', '') or '') == subject_ref
        and int(getattr(item, 'episode_id', 0) or 0) in selected_episode_ids
    ]


def _build_subject_level_singleton_episode(workspace: CaseEvidenceWorkspace, subject_ref: str, subject_id: int) -> BangumiEpisode | None:
    subject = next((card for card in workspace.bangumi_subjects if card.ref == subject_ref), None)
    if subject is None or not is_subject_level_singleton_source(subject):
        return None
    source_hint = subject_card_source_form_hint(subject)
    title = subject.name_cn or subject.title or subject.name or f'subject:{subject_id}'
    return BangumiEpisode(
        id=0,
        subject_id=subject_id,
        type=1,
        sort=1,
        ep=1,
        synthetic=True,
        synthetic_reason='subject_singleton_no_episode_items',
        subject_level_target=True,
        kind='subject_singleton',
        title=title,
        name=subject.name or title,
        name_cn=subject.name_cn or title,
        airdate=subject.date or '',
        desc=subject.summary_short or '',
        source_form_hint=source_hint if source_hint in {'movie', 'ova', 'special'} else 'special',
        relation_to_main=subject.relation_to_main or '',
        source_role=subject.source_role or '',
    )


def _subject_id_from_workspace(workspace: CaseEvidenceWorkspace, subject_ref: str) -> int:
    for card in workspace.bangumi_subjects:
        if card.ref == subject_ref:
            return card.subject_id
    return 0


def _episode_id_from_workspace(workspace: CaseEvidenceWorkspace, item_ref: str) -> int:
    for card in workspace.bangumi_items:
        if card.ref == item_ref:
            return card.episode_id
    return 0


def _subject_ref_from_item_refs(workspace: CaseEvidenceWorkspace, item_refs: list[str]) -> str:
    for card in workspace.bangumi_items:
        if card.ref in item_refs:
            return card.subject_ref
    return ''


def _subject_ref_from_workspace_item(workspace: CaseEvidenceWorkspace, item_ref: str) -> str:
    for card in workspace.bangumi_items:
        if card.ref == item_ref:
            return card.subject_ref
    return ''
