from __future__ import annotations

import re
from collections import Counter, OrderedDict
from typing import Iterable

from .models import (
    BangumiGroupCard,
    BangumiItemCard,
    BangumiRelationCard,
    BangumiSubjectCard,
    CaseBudget,
    CaseBriefingOutput,
    CaseResolutionLedger,
    CaseContract,
    CaseDossier,
    BoundedCaseDossier,
    CaseHeader,
    LocalClusterCard,
    LocalFileCard,
    LocalSpanCard,
    ProvenanceCard,
    QueryCard,
    EvidenceBatchResult,
    InvestigationNotebook,
    VerifierIssue,
    VisibleRefCatalog,
)
from .span_builder import build_bangumi_span_cards, compact_span_card
from .salience import build_salience_overview
from .notebook import compact_case_briefing, compact_investigation_notebook


def _dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    return list(OrderedDict.fromkeys(v for v in values if v))


def _dedupe_cards(cards):
    deduped = []
    seen = set()
    for card in cards:
        ref = getattr(card, 'ref', '')
        if ref and ref not in seen:
            seen.add(ref)
            deduped.append(card)
    return deduped


def _normalize_local_span_cards(cards) -> list[LocalSpanCard]:
    normalized: list[LocalSpanCard] = []
    for card in list(cards or []):
        if isinstance(card, LocalSpanCard):
            normalized.append(card)
        elif hasattr(card, 'model_dump'):
            normalized.append(LocalSpanCard.model_validate(card.model_dump(mode='json')))
        elif isinstance(card, dict):
            normalized.append(LocalSpanCard.model_validate(card))
    return normalized


def _bounded_detailed_target_refs(items: list[BangumiItemCard], visible_card_limit: int) -> list[BangumiItemCard]:
    if len(items) <= visible_card_limit:
        return list(items)
    head = items[: max(1, visible_card_limit // 2)]
    tail = items[-max(1, visible_card_limit // 2):]
    return _dedupe_cards([*head, *tail])


def build_visible_ref_catalog(
    local_files: list[LocalFileCard],
    local_clusters: list[LocalClusterCard],
    bangumi_subjects: list[BangumiSubjectCard],
    bangumi_relations: list[BangumiRelationCard],
    bangumi_groups: list[BangumiGroupCard],
    bangumi_items: list[BangumiItemCard],
    query_cards: list[QueryCard],
    contract: CaseContract | None = None,
) -> VisibleRefCatalog:
    target_refs = contract.visible_target_refs if contract else [card.ref for card in bangumi_items]
    return VisibleRefCatalog(
        local_file_refs=_dedupe_preserve_order(card.ref for card in local_files),
        local_cluster_refs=_dedupe_preserve_order(card.ref for card in local_clusters),
        bangumi_subject_refs=_dedupe_preserve_order(card.ref for card in bangumi_subjects),
        bangumi_relation_refs=_dedupe_preserve_order(card.ref for card in bangumi_relations),
        bangumi_group_refs=_dedupe_preserve_order(card.ref for card in bangumi_groups),
        bangumi_item_refs=_dedupe_preserve_order(card.ref for card in bangumi_items),
        query_refs=_dedupe_preserve_order(card.ref for card in query_cards),
        target_refs=_dedupe_preserve_order(target_refs),
    )


def build_default_contract(
    local_files: list[LocalFileCard],
    bangumi_items: list[BangumiItemCard],
) -> CaseContract:
    main_file_refs = [card.ref for card in local_files if getattr(card, 'is_main', False)]
    supplemental_file_refs = [card.ref for card in local_files if not getattr(card, 'is_main', False)]
    allowed_file_refs = _dedupe_preserve_order(card.ref for card in local_files)
    visible_target_refs = []
    visible_target_refs = _dedupe_preserve_order(card.ref for card in bangumi_items)
    summary = 'Default case contract generated from local main files and visible Bangumi targets.'
    return CaseContract(
        summary=summary,
        expected_outcome='unknown',
        main_file_refs=main_file_refs,
        supplemental_file_refs=supplemental_file_refs,
        allowed_file_refs=allowed_file_refs,
        visible_target_refs=visible_target_refs,
        final_target_rule='BE* or UNALIGNED',
        coverage_rule='Each main file exactly once',
        duplicate_rule='No duplicate non-UNALIGNED target',
        support_rule='Each assignment must cite supporting findings and cards',
        constraints=[
            f'main_file_refs={main_file_refs}',
            f'supplemental_file_refs={supplemental_file_refs}',
            f'allowed_file_refs={allowed_file_refs}',
            'target_rule=BE* or UNALIGNED',
        ],
    )


def build_query_cards_from_local_cards(
    local_files: list[LocalFileCard],
    local_clusters: list[LocalClusterCard],
) -> list[QueryCard]:
    cards: list[QueryCard] = []
    seen_texts: set[str] = set()
    source_refs_by_text: dict[str, list[str]] = {}
    index = 1

    def add_query(text: str, source_refs: list[str]) -> None:
        nonlocal index
        text = text.strip()
        if not text or text in seen_texts:
            if text in source_refs_by_text:
                merged = _dedupe_preserve_order([*source_refs_by_text[text], *source_refs])
                source_refs_by_text[text] = merged
                for card in cards:
                    if card.query_text == text:
                        card.source_refs = merged
                        break
            return
        seen_texts.add(text)
        source_refs_by_text[text] = list(source_refs)
        cards.append(
            QueryCard(
                ref=f'SQ{index}',
                query_text=text,
                query_kind='subject_search',
                query_origin='local_raw',
                source_refs=list(source_refs),
            )
        )
        index += 1

    for card in local_files:
        basename = card.path.rsplit('\\', 1)[-1].rsplit('/', 1)[-1]
        parent_display = getattr(card, 'parent_display', '')
        for text in [parent_display, basename]:
            add_query(text, [card.ref])

    for card in local_clusters:
        title_cues = getattr(card, 'title_cues', []) or []
        for text in [card.cluster_name, *title_cues]:
            add_query(text, list(card.file_refs))

    return [card.model_copy(update={'source_refs': list(card.source_refs), 'result_refs': []}) for card in cards]


def build_case_dossier(
    header: CaseHeader,
    budget: CaseBudget,
    local_files: list[LocalFileCard],
    local_clusters: list[LocalClusterCard],
    bangumi_subjects: list[BangumiSubjectCard],
    bangumi_relations: list[BangumiRelationCard],
    bangumi_groups: list[BangumiGroupCard],
    bangumi_items: list[BangumiItemCard],
    query_cards: list[QueryCard],
    provenance_cards: list[ProvenanceCard],
    contract: CaseContract | None = None,
    previous_hypotheses=None,
    evidence_results=None,
    verifier_issues=None,
    case_briefing: CaseBriefingOutput | None = None,
    investigation_notebook: InvestigationNotebook | None = None,
    case_resolution_ledger: CaseResolutionLedger | None = None,
) -> CaseDossier:
    query_cards_final = query_cards or build_query_cards_from_local_cards(local_files, local_clusters)
    contract_final = contract or build_default_contract(local_files, bangumi_items)
    visible_refs = build_visible_ref_catalog(
        local_files,
        local_clusters,
        bangumi_subjects,
        bangumi_relations,
        bangumi_groups,
        bangumi_items,
        query_cards_final,
        contract_final,
    )
    bangumi_span_cards = build_bangumi_span_cards(bangumi_items=bangumi_items)
    previous_evidence_results = [] if evidence_results is None else evidence_results
    for batch in previous_evidence_results:
        for rr in getattr(batch, 'request_results', []) or []:
            bangumi_span_cards = [*bangumi_span_cards, *(getattr(rr, 'bangumi_span_cards', []) or [])]
    return CaseDossier(
        header=header,
        budget=budget,
        visible_refs=visible_refs,
        local_files=local_files,
        local_clusters=local_clusters,
        bangumi_subjects=bangumi_subjects,
        bangumi_relations=bangumi_relations,
        bangumi_groups=bangumi_groups,
        bangumi_items=bangumi_items,
        query_cards=query_cards_final,
        provenance_cards=provenance_cards,
        contract=contract_final,
        previous_hypotheses=[] if previous_hypotheses is None else previous_hypotheses,
        previous_evidence_results=previous_evidence_results,
        verifier_issues=[] if verifier_issues is None else verifier_issues,
        local_span_cards=[],
        bangumi_span_cards=bangumi_span_cards,
        case_briefing=case_briefing,
        investigation_notebook=investigation_notebook or InvestigationNotebook(),
        case_resolution_ledger=case_resolution_ledger,
    )


def _tail_label(path: str) -> str:
    tail = str(path or '').replace('\\', '/').rsplit('/', 1)[-1]
    return tail or str(path or '')


def _sample_refs(cards: list, limit: int = 2) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for card in cards[:limit]:
        data = card.model_dump(mode='json') if hasattr(card, 'model_dump') else dict(card)
        out.append({k: data.get(k) for k in ('ref', 'title', 'name', 'name_cn', 'sort', 'ep', 'subject_ref', 'item_kind') if k in data})
    return out


def _extract_release_group(text: str) -> str:
    raw = str(text or '').strip()
    m = re.match(r'^\[(?P<group>[^\]]+)\]\s*(?P<title>.+)$', raw)
    return m.group('group').strip() if m else ''


def _get_value(obj, name: str, default=None):
    value = getattr(obj, name, default)
    return value() if callable(value) else value


def _coerce_visible_refs(visible_refs):
    if isinstance(visible_refs, VisibleRefCatalog):
        return visible_refs
    if hasattr(visible_refs, 'model_dump'):
        return VisibleRefCatalog.model_validate(visible_refs.model_dump(mode='json'))
    if isinstance(visible_refs, dict):
        return VisibleRefCatalog.model_validate(visible_refs)
    if hasattr(visible_refs, '__dict__'):
        return VisibleRefCatalog.model_validate(vars(visible_refs))
    return VisibleRefCatalog.model_validate(visible_refs)


def build_bounded_case_dossier(dossier: CaseDossier, *, title_cue_limit: int = 5, group_cue_limit: int = 5, query_sample_limit: int = 8, visible_card_limit: int = 8) -> BoundedCaseDossier:
    local_files = list(_get_value(dossier, 'local_files'))
    local_clusters = list(_get_value(dossier, 'local_clusters'))
    local_span_cards = _normalize_local_span_cards(_get_value(dossier, 'local_span_cards', []))
    bangumi_subjects = list(_get_value(dossier, 'bangumi_subjects'))
    bangumi_groups = list(_get_value(dossier, 'bangumi_groups'))
    bangumi_items = list(_get_value(dossier, 'bangumi_items'))
    query_cards = list(_get_value(dossier, 'query_cards'))
    contract = _get_value(dossier, 'contract')
    visible_refs = _get_value(dossier, 'visible_refs')
    header = _get_value(dossier, 'header')
    budget = _get_value(dossier, 'budget')
    inferred_main_refs = list(contract.main_file_refs) or [card.ref for card in local_files if getattr(card, 'is_main', False)]
    visible_target_refs = _dedupe_preserve_order(list(contract.visible_target_refs or []))
    catalog_refs = VisibleRefCatalog(
        local_file_refs=_dedupe_preserve_order(card.ref for card in local_files),
        local_cluster_refs=_dedupe_preserve_order(card.ref for card in local_clusters),
        bangumi_subject_refs=_dedupe_preserve_order(card.ref for card in bangumi_subjects),
        bangumi_relation_refs=_dedupe_preserve_order(card.ref for card in _get_value(dossier, 'bangumi_relations', [])),
        bangumi_group_refs=_dedupe_preserve_order(card.ref for card in bangumi_groups),
        bangumi_item_refs=_dedupe_preserve_order(card.ref for card in bangumi_items),
        query_refs=_dedupe_preserve_order(card.ref for card in query_cards),
        target_refs=visible_target_refs,
    )
    detailed_visible_cards = _bounded_detailed_target_refs(list(dossier.bangumi_items), visible_card_limit)
    requested_target_refs = [ref for ref in dossier.seen_detail_refs if ref.startswith('BE')]
    requested_local_refs = [ref for ref in dossier.seen_detail_refs if ref.startswith('LF')]
    extra_target_cards = [card for card in bangumi_items if card.ref in requested_target_refs and card.ref not in {c.ref for c in detailed_visible_cards}]
    detailed_visible_cards = _dedupe_cards(detailed_visible_cards + extra_target_cards)
    detailed_local_file_cards = [card for card in local_files if card.ref in requested_local_refs]
    detailed_card_refs = _dedupe_preserve_order([card.ref for card in detailed_visible_cards])
    assignable_target_refs = _dedupe_preserve_order([card.ref for card in detailed_visible_cards if card.ref in visible_target_refs])
    seen_detail_refs = _dedupe_preserve_order([*dossier.seen_detail_refs, *detailed_card_refs, *requested_local_refs])
    verifier_issue_summary = [getattr(issue, 'message', str(issue)) for issue in list(_get_value(dossier, 'verifier_issues'))]
    previous_evidence_results = [batch for batch in list(_get_value(dossier, 'previous_evidence_results')) if isinstance(batch, EvidenceBatchResult)]
    if hasattr(dossier, 'seen_detail_refs'):
        seen_detail_refs = _dedupe_preserve_order([*getattr(dossier, 'seen_detail_refs', []), *seen_detail_refs])
    for batch in previous_evidence_results:
        seen_detail_refs = _dedupe_preserve_order([*seen_detail_refs, *(getattr(batch, 'provenance_refs', []) or []), *(getattr(batch, 'response_refs', []) or [])])
    counts = {
        'local_file_count': len(local_files),
        'main_file_count': len(inferred_main_refs),
        'visible_target_count': len(visible_target_refs),
        'subject_count': len(bangumi_subjects),
        'group_count': len(bangumi_groups),
        'item_count': len(bangumi_items),
    }
    primary_title_cues = _dedupe_preserve_order([cue for card in local_clusters for cue in getattr(card, 'title_cues', [])] + [str(getattr(card, 'parent_display', '') or '').strip() for card in local_files])[:title_cue_limit]
    release_group_cues = _dedupe_preserve_order([_extract_release_group(getattr(card, 'parent_display', '')) for card in local_files if _extract_release_group(getattr(card, 'parent_display', ''))])[:group_cue_limit]
    query_card_sample = [card.model_copy() for card in query_cards[:query_sample_limit]]
    main_files = [card for card in local_files if card.ref in inferred_main_refs]
    main_file_overview = {
        'main_file_ref_range': [main_files[0].ref, main_files[-1].ref] if main_files else [],
        'path_samples': [(_tail_label(card.path), card.label, card.is_main) for card in (main_files[:2] + main_files[-2:] if len(main_files) > 2 else main_files)],
        'raw_order_summary': {
            'count': len(main_files),
            'main_file_refs': list(dossier.contract.main_file_refs[:10]),
            'note': 'raw local order only; filename numbering is interpreted by LocalStructureAgent',
        },
    }
    groups: dict[str, dict[str, object]] = {}
    for item in bangumi_items:
        key = item.subject_ref or item.source_form_hint or item.name_cn or item.name or item.title or 'unknown'
        entry = groups.setdefault(key, {'subject_ref': item.subject_ref, 'title': item.name_cn or item.name or item.title, 'count': 0, 'sort_range': [item.sort, item.sort], 'ep_range': [item.ep, item.ep], 'samples': []})
        entry['count'] = int(entry['count']) + 1
        entry['sort_range'][0] = min(entry['sort_range'][0], item.sort)
        entry['sort_range'][1] = max(entry['sort_range'][1], item.sort)
        entry['ep_range'][0] = min(entry['ep_range'][0], item.ep)
        entry['ep_range'][1] = max(entry['ep_range'][1], item.ep)
        if len(entry['samples']) < 2:
            entry['samples'].append({'ref': item.ref, 'title': item.title, 'sort': item.sort, 'ep': item.ep, 'subject_ref': item.subject_ref})
    target_overview = list(groups.values())
    salience_overview = build_salience_overview(CaseDossier(
        header=header,
        budget=budget,
        visible_refs=_coerce_visible_refs(visible_refs),
        local_files=local_files,
        local_clusters=local_clusters,
        bangumi_subjects=bangumi_subjects,
        bangumi_relations=list(_get_value(dossier, 'bangumi_relations')),
        bangumi_groups=bangumi_groups,
        bangumi_items=bangumi_items,
        query_cards=query_cards,
        provenance_cards=list(_get_value(dossier, 'provenance_cards')),
        contract=contract,
        detailed_card_refs=detailed_card_refs,
        assignable_target_refs=assignable_target_refs,
        seen_detail_refs=seen_detail_refs,
        previous_hypotheses=list(_get_value(dossier, 'previous_hypotheses')),
        previous_evidence_results=previous_evidence_results,
        verifier_issues=list(_get_value(dossier, 'verifier_issues')),
        case_briefing=_get_value(dossier, 'case_briefing', None),
        investigation_notebook=_get_value(dossier, 'investigation_notebook', InvestigationNotebook()),
        case_resolution_ledger=_get_value(dossier, 'case_resolution_ledger', None),
    ))
    ledger = _get_value(dossier, 'case_resolution_ledger', None)
    return BoundedCaseDossier(
        counts=counts,
        primary_title_cues=primary_title_cues,
        release_group_cues=release_group_cues,
        query_card_sample=query_card_sample,
        main_file_overview=main_file_overview,
        target_overview=target_overview,
        detailed_visible_cards=detailed_visible_cards,
        available_detail_request_types=['subject_lookup', 'subject_search', 'related_expansion', 'episode_list', 'episode_detail', 'local_file_detail', 'target_detail', 'target_window'],
        catalog_refs=catalog_refs,
        detailed_card_refs=detailed_card_refs,
        assignable_target_refs=assignable_target_refs,
        seen_detail_refs=seen_detail_refs,
        previous_evidence_results=previous_evidence_results,
        verifier_issue_summary=verifier_issue_summary,
        round_context=str(getattr(dossier, 'round_context', 'initial') or 'initial'),
        salience_overview=salience_overview,
        detailed_local_file_cards=detailed_local_file_cards,
        requested_detail_refs=_dedupe_preserve_order(dossier.seen_detail_refs),
        visible_refs=_coerce_visible_refs(visible_refs),
        contract=contract,
        header=header,
        budget=budget,
        local_span_cards=[card.model_dump(mode='json') for card in local_span_cards],
        case_briefing=compact_case_briefing(_get_value(dossier, 'case_briefing', None)),
        investigation_notebook=compact_investigation_notebook(_get_value(dossier, 'investigation_notebook', InvestigationNotebook())),
        case_resolution_ledger=ledger.model_dump(mode='json') if hasattr(ledger, 'model_dump') else {},
    )


def build_initial_compact_projection(bounded: BoundedCaseDossier, *, detailed_card_limit: int = 10, assignable_limit: int = 12, target_group_sample_limit: int = 5, query_source_sample_limit: int = 3) -> dict[str, object]:
    def _compact_visible_card(card) -> dict[str, object]:
        data = card.model_dump(mode='json') if hasattr(card, 'model_dump') else dict(card)
        parent_refs = list(data.get('parent_refs') or [])
        return {
            'ref': data.get('ref', ''),
            'subject_ref': data.get('subject_ref', ''),
            'sort': data.get('sort', ''),
            'ep': data.get('ep', ''),
            'kind': data.get('kind', ''),
            'item_kind': data.get('item_kind', ''),
            'title': data.get('title', ''),
            'name': data.get('name', ''),
            'name_cn': data.get('name_cn', ''),
            'source_form_hint': data.get('source_form_hint', ''),
            'synthetic': data.get('synthetic', False),
            'parent_refs_count': len(parent_refs),
            'parent_refs_samples': parent_refs[:3],
        }

    def _compact_local_file_card(card) -> dict[str, object]:
        data = card.model_dump(mode='json') if hasattr(card, 'model_dump') else dict(card)
        path = str(data.get('path', '') or '').replace('\\', '/')
        return {
            'ref': data.get('ref', ''),
            'basename': path.rsplit('/', 1)[-1],
            'path_tail': path.rsplit('/', 2)[-1] if '/' in path else path,
            'parent_display': str(data.get('parent_display', '') or '')[:120],
            'label': data.get('label', ''),
            'kind': data.get('file_kind', data.get('kind', '')),
            'is_main': data.get('is_main', False),
        }

    def _compact_local_span_card(card) -> dict[str, object]:
        data = card.model_dump(mode='json') if hasattr(card, 'model_dump') else dict(card)
        file_refs = list(data.get('file_refs') or [])
        return {
            'ref': data.get('ref', ''),
            'span_scope': data.get('span_scope', 'unknown'),
            'parent_key': data.get('parent_key', ''),
            'season_cue': data.get('season_cue', ''),
            'count': data.get('file_ref_count', len(file_refs)),
            'range': list(data.get('file_ref_range') or []),
            'samples': list(data.get('file_ref_samples') or []),
            'ordering_basis': data.get('ordering_basis', ''),
            'gap_count': data.get('gap_count', 0),
            'duplicate_count': data.get('duplicate_count', 0),
            'title_cues': list(data.get('title_cues') or []),
            'release_group_cues': list(data.get('release_group_cues') or []),
        }
    def _compact_ref_summary(values: list[str], *, sample_limit: int = 5) -> dict[str, object]:
        values = [ref for ref in values if ref]
        if len(values) <= 20:
            return {'count': len(values), 'refs': list(values)}
        head = values[: max(1, sample_limit // 2)]
        tail = values[-max(1, sample_limit // 2):]
        return {'count': len(values), 'range': [values[0], values[-1]], 'sample_refs': list(dict.fromkeys([*head, *tail]))}

    def _compact_query(card: QueryCard) -> dict[str, object]:
        data = card.model_dump(mode='json') if hasattr(card, 'model_dump') else dict(card)
        source_refs = list(data.get('source_refs') or [])
        return {
            'ref': data.get('ref', ''),
            'query_text': data.get('query_text', ''),
            'query_kind': data.get('query_kind', ''),
            'query_origin': data.get('query_origin', ''),
            'source_ref_count': len(source_refs),
            'source_ref_samples': _dedupe_preserve_order([*source_refs[:query_source_sample_limit], *source_refs[-query_source_sample_limit:]]),
        }

    target_groups = []
    for group in bounded.target_overview[:target_group_sample_limit]:
        target_groups.append({
            'subject_ref': group.get('subject_ref', ''),
            'title': group.get('title', ''),
            'count': int(group.get('count') or 0),
            'sort_range': list(group.get('sort_range') or []),
            'ep_range': list(group.get('ep_range') or []),
            'sample_refs': list(group.get('samples') or []),
        })

    detailed_cards = [_compact_visible_card(card) for card in bounded.detailed_visible_cards[:detailed_card_limit]]
    detailed_local_cards = [_compact_local_file_card(card) for card in bounded.detailed_local_file_cards[:detailed_card_limit]]
    projection = {
        'case_id': bounded.header.case_id,
        'round_context': bounded.round_context,
        'counts': bounded.counts,
        'salience_overview': bounded.salience_overview,
        'primary_title_cues': bounded.primary_title_cues,
        'release_group_cues': bounded.release_group_cues,
        'main_file_overview': bounded.main_file_overview,
        'catalog_summary': {
            'target_ref_count': len(bounded.catalog_refs.target_refs),
            'target_ref_samples': _dedupe_preserve_order([*bounded.catalog_refs.target_refs[:4], *bounded.catalog_refs.target_refs[-4:]]),
            'subject_ref_count': len(bounded.catalog_refs.bangumi_subject_refs),
            'subject_ref_samples': _dedupe_preserve_order([*bounded.catalog_refs.bangumi_subject_refs[:4], *bounded.catalog_refs.bangumi_subject_refs[-4:]]),
            'target_groups': target_groups,
        },
        'contract_overview': {
            'main_file_count': len(bounded.contract.main_file_refs),
            'main_file_ref_samples': _dedupe_preserve_order([*bounded.contract.main_file_refs[:4], *bounded.contract.main_file_refs[-4:]]),
            'coverage_rule': bounded.contract.coverage_rule,
            'assignment_policy': 'targets must be in assignable_target_refs; seen_detail_refs are audit/detail evidence only',
            'target_count': len(bounded.contract.visible_target_refs),
        },
        'query_card_sample': [_compact_query(card) for card in bounded.query_card_sample],
        'detailed_visible_cards': detailed_cards,
        'detailed_local_file_cards': detailed_local_cards,
        'local_span_cards': [_compact_local_span_card(card) for card in list(getattr(bounded, 'local_span_cards', []) or [])[:10]],
        'assignable_target_refs': _compact_ref_summary(list(bounded.assignable_target_refs)),
        'seen_detail_refs': _compact_ref_summary(list(bounded.seen_detail_refs)),
        'available_detail_request_types': bounded.available_detail_request_types,
        'budget': bounded.budget.model_dump(mode='json') if hasattr(bounded.budget, 'model_dump') else bounded.budget,
        'verifier_issue_summary': bounded.verifier_issue_summary,
        'previous_evidence_results_summary': [
            {'batch_ref': getattr(batch, 'batch_ref', ''), 'status': getattr(batch, 'status', ''), 'request_types': [getattr(rr, 'request_type', '') for rr in (getattr(batch, 'request_results', []) or []) if getattr(rr, 'request_type', '')], 'response_refs': _compact_ref_summary([ref for rr in (getattr(batch, 'request_results', []) or []) for ref in (getattr(rr, 'response_refs', []) or [])])}
            for batch in (bounded.previous_evidence_results or [])[:3]
        ],
        'round_budget': {
            'max_judge_rounds': bounded.budget.max_judge_rounds,
            'max_evidence_batches': bounded.budget.max_evidence_batches,
            'max_issue_response_rounds': bounded.budget.max_issue_response_rounds,
            'max_requests_per_batch': bounded.budget.max_requests_per_batch,
        },
    }
    return projection
