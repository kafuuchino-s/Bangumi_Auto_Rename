from __future__ import annotations

from .broker_budget import BudgetLedger, BudgetExceeded
from .broker_episodes import execute_episode_detail, execute_episode_list
from .broker_related import execute_related_expansion
from .broker_search import execute_subject_search
from .broker_subject import execute_subject_lookup
from .models import BangumiItemCard, BangumiSpanCard, EvidenceBatchResult, EvidenceRequest, EvidenceRequestResult
from .notebook import close_notebook_agenda_for_evidence_results
from .special_investigation import is_special_eligible_span
from .workspace import CaseEvidenceWorkspace


PROMPT_DETAIL_CARD_CAP = 16


def _ref_samples(refs: list[str], *, limit: int = 5) -> list[str]:
    if len(refs) <= limit * 2:
        return list(refs)
    return [*refs[:limit], *refs[-limit:]]


def _summarize_window(refs: list[str]) -> str:
    if not refs:
        return 'empty window'
    if len(refs) == 1:
        return f'window {refs[0]}'
    return f'window {refs[0]}..{refs[-1]} ({len(refs)} refs)'


def _build_result(request, *, cards: list, notes: list[str], prompt_cap: int = PROMPT_DETAIL_CARD_CAP) -> EvidenceRequestResult:
    response_refs = [card.ref for card in cards]
    truncated = len(cards) > prompt_cap
    prompt_cards = cards[:prompt_cap]
    return EvidenceRequestResult(
        request_ref=request.request_ref,
        request_type=request.request_type,
        accepted=bool(cards),
        response_refs=response_refs,
        response_ref_count=len(response_refs),
        response_ref_samples=_ref_samples(response_refs),
        returned_card_count=len(cards),
        returned_card_count_range=f'{response_refs[0]}..{response_refs[-1]}' if response_refs else '',
        returned_card_count_samples=[card.ref for card in prompt_cards[:5]],
        returned_card_count_summary=_summarize_window(response_refs) if len(response_refs) > prompt_cap else '',
        returned_card_count_truncated=truncated,
        truncated_for_prompt=truncated,
        notes=[*notes, *(['Judge request narrower window'] if truncated else [])],
    )


def _span_samples(refs: list[str], limit: int = 5) -> list[str]:
    if len(refs) <= limit:
        return list(refs)
    return [*refs[:limit], *refs[-limit:]]


def _build_target_span_result(request, cards: list[BangumiSpanCard], notes: list[str]) -> EvidenceRequestResult:
    response_refs = [card.ref for card in cards]
    return EvidenceRequestResult(
        request_ref=request.request_ref,
        request_type=request.request_type,
        accepted=bool(cards),
        response_refs=response_refs,
        response_ref_count=len(response_refs),
        response_ref_samples=_span_samples(response_refs),
        returned_card_count=len(cards),
        returned_card_count_range=f'{response_refs[0]}..{response_refs[-1]}' if response_refs else '',
        returned_card_count_samples=response_refs[:5],
        bangumi_span_cards=cards,
        notes=notes,
    )


def _target_span_candidate_cards(workspace: CaseEvidenceWorkspace, request: EvidenceRequest) -> list[BangumiSpanCard]:
    request_ref = str(getattr(request, 'request_ref', '') or '')
    local_span_ref = str(getattr(request, 'local_span_ref', '') or '')
    if local_span_ref:
        dossier = workspace.to_dossier(round_context='target_span_broker')
        local_span = next((span for span in list(getattr(dossier, 'local_span_cards', []) or []) if str(getattr(span, 'ref', '') or '') == local_span_ref), None)
        if is_special_eligible_span(local_span, dossier):
            return []
    explicit_window = bool(
        getattr(request, 'sort_start', 0)
        or getattr(request, 'sort_end', 0)
        or getattr(request, 'item_refs', [])
    )
    spans = [
        span for span in getattr(workspace, 'bangumi_span_cards', []) or []
        if bool(getattr(span, 'detail_equivalent', False))
        and (not getattr(span, 'source_request_ref', '') or getattr(span, 'source_request_ref', '') == request_ref)
        and (not request.subject_refs or span.subject_ref in request.subject_refs)
        and (not request.group_refs or getattr(span, 'group_ref', '') in request.group_refs)
        and getattr(span, 'item_kind', 'unknown') == 'regular'
    ]
    if explicit_window:
        requested_items = set(getattr(request, 'item_refs', []) or [])
        spans = [
            span for span in spans
            if (
                not requested_items
                or requested_items.issubset(set(getattr(span, 'target_refs', []) or []))
            )
            and (
                not getattr(request, 'sort_start', 0)
                or (
                    getattr(span, 'sort_start', None) is not None
                    and int(getattr(span, 'sort_start', 0) or 0) >= int(getattr(request, 'sort_start', 0) or 0)
                )
            )
            and (
                not getattr(request, 'sort_end', 0)
                or (
                    getattr(span, 'sort_end', None) is not None
                    and int(getattr(span, 'sort_end', 0) or 0) <= int(getattr(request, 'sort_end', 0) or 0)
                )
            )
        ]
    else:
        spans = [span for span in spans if str(getattr(span, 'source_request_ref', '') or '') == request_ref]
    ordered = sorted(spans, key=lambda s: (getattr(s, 'sort_start', 0) or 0, getattr(s, 'sort_end', 0) or 0, getattr(s, 'target_ref_count', 0), getattr(s, 'ref', '')))
    if not request.expected_count:
        return ordered[:PROMPT_DETAIL_CARD_CAP]
    matched = [span for span in ordered if int(getattr(span, 'target_ref_count', 0) or len(getattr(span, 'target_refs', []) or [])) == int(request.expected_count)]
    return matched[:PROMPT_DETAIL_CARD_CAP]


def _is_special_eligible_target_span_request(workspace: CaseEvidenceWorkspace, request: EvidenceRequest) -> bool:
    local_span_ref = str(getattr(request, 'local_span_ref', '') or '')
    if not local_span_ref:
        return False
    dossier = workspace.to_dossier(round_context='target_span_broker')
    local_span = next((span for span in list(getattr(dossier, 'local_span_cards', []) or []) if str(getattr(span, 'ref', '') or '') == local_span_ref), None)
    return is_special_eligible_span(local_span, dossier)


def _windowed_item_span_candidates(workspace: CaseEvidenceWorkspace, request: EvidenceRequest) -> list[BangumiSpanCard]:
    expected_count = int(getattr(request, 'expected_count', 0) or 0)
    if expected_count <= 0:
        return []
    requested_refs = list(dict.fromkeys(getattr(request, 'item_refs', []) or []))
    has_sort_window = bool(
        getattr(request, 'sort_start', 0)
        or getattr(request, 'sort_end', 0)
        or (
            getattr(request, 'sort_start', None) is not None
            and getattr(request, 'sort_end', None) is not None
            and int(getattr(request, 'sort_end', 0) or 0) > int(getattr(request, 'sort_start', 0) or 0)
        )
    )
    items = [
        item for item in list(getattr(workspace, 'bangumi_items', []) or [])
        if (not request.subject_refs or item.subject_ref in request.subject_refs)
        and str(getattr(item, 'item_kind', '') or 'unknown') in {'episode', 'unknown'}
        and getattr(item, 'ref', '')
    ]
    if requested_refs:
        requested = set(requested_refs)
        items = [item for item in items if item.ref in requested]
    if has_sort_window:
        items = [
            item for item in items
            if item.sort >= int(getattr(request, 'sort_start', 0) or 0)
            and item.sort <= int(getattr(request, 'sort_end', 0) or 0)
        ]
    by_subject: dict[str, list[BangumiItemCard]] = {}
    for item in items:
        by_subject.setdefault(item.subject_ref, []).append(item)

    cards: list[BangumiSpanCard] = []
    for subject_ref, subject_items in by_subject.items():
        ordered = sorted(subject_items, key=lambda item: (item.sort or 0, item.ep or 0, item.ref))
        if requested_refs:
            windows = [ordered]
        elif has_sort_window:
            matching = [
                item for item in ordered
                if item.sort >= int(getattr(request, 'sort_start', 0) or 0)
                and item.sort <= int(getattr(request, 'sort_end', 0) or 0)
            ]
            windows = [matching]
        elif len(ordered) == expected_count:
            windows = [ordered]
        elif len(ordered) > expected_count:
            windows = []
        else:
            windows = []
        for window in windows:
            if len(window) < expected_count:
                continue
            if len(window) > expected_count:
                continue
            refs = [item.ref for item in window]
            cards.append(BangumiSpanCard(
                ref=f"BES_{request.local_span_ref or 'SPAN'}_{len(cards) + 1}",
                subject_ref=subject_ref,
                group_ref='',
                target_refs=refs,
                target_ref_count=len(refs),
                target_ref_range=[refs[0], refs[-1]],
                target_ref_samples=_span_samples(refs),
                sort_start=min((item.sort for item in window), default=None),
                sort_end=max((item.sort for item in window), default=None),
                ep_start=min((item.ep for item in window), default=None),
                ep_end=max((item.ep for item in window), default=None),
                item_kind='regular',
                gap_count=0,
                duplicate_count=0,
                special_count=0,
                title_samples=_span_samples([item.title or item.name or item.name_cn for item in window if (item.title or item.name or item.name_cn)]),
                detail_equivalent=True,
                source_request_ref=request.request_ref,
            ))
    return cards[:PROMPT_DETAIL_CARD_CAP]


def _request_failure_reason(request: EvidenceRequest, notes: list[str]) -> str:
    text = ' '.join(str(note).casefold() for note in notes)
    if any(marker in text for marker in ('invalid anchor', 'invalid subject', 'invalid item', 'invalid query', 'unknown request_type')):
        return 'evidence_request_invalid_anchor'
    if 'no matching local files' in text:
        return 'evidence_request_no_matching_local_files'
    if 'no matching target window' in text or 'target_window too wide' in text:
        return 'evidence_request_window_too_wide'
    if 'no matching targets' in text:
        return 'evidence_request_no_matching_targets'
    return 'evidence_request_no_usable_evidence'


class EvidenceBroker:
    def __init__(self, bangumi_client):
        self.bangumi_client = bangumi_client

    def execute_batch(self, workspace: CaseEvidenceWorkspace, requests: list[EvidenceRequest]) -> tuple[CaseEvidenceWorkspace, EvidenceBatchResult]:
        batch_ref = f"EB{workspace.header.evidence_batches_used + 1}"
        if not requests:
            result = EvidenceBatchResult(batch_ref=batch_ref, round_index=workspace.header.round_index, status='empty', budget_after=workspace.budget.model_copy(deep=True))
            return workspace, result

        ledger = BudgetLedger(workspace.budget)
        if workspace.budget.max_evidence_batches == 0 or not ledger.can_add_evidence_batch():
            result = EvidenceBatchResult(batch_ref=batch_ref, round_index=workspace.header.round_index, status='rejected', budget_after=workspace.budget.model_copy(deep=True))
            return workspace, result
        try:
            ledger = ledger.add_evidence_batch()
        except BudgetExceeded:
            result = EvidenceBatchResult(batch_ref=batch_ref, round_index=workspace.header.round_index, status='rejected', budget_after=workspace.budget.model_copy(deep=True))
            return workspace, result

        added_subjects = []
        added_relations = []
        added_groups = []
        added_items = []
        added_provenance = []
        enriched_subjects = []
        enriched_items = []
        request_results: list[EvidenceRequestResult] = []
        status = 'accepted'

        current_ws = workspace
        for request in requests:
            validation = self._validate_request(current_ws, request)
            if validation is not None:
                request_results.append(validation.model_copy(update={'request_type': request.request_type}))
                status = 'partial'
                continue
            produced_ws, request_result, delta = self._execute_request(current_ws, request, ledger)
            ledger = delta
            current_ws = produced_ws
            request_results.append(request_result.model_copy(update={'request_type': request.request_type}))
            if not request_result.accepted:
                status = 'partial'
            added_subjects.extend(getattr(request_result, '_added_subjects', []))
            added_relations.extend(getattr(request_result, '_added_relations', []))
            added_groups.extend(getattr(request_result, '_added_groups', []))
            added_items.extend(getattr(request_result, '_added_items', []))
            added_provenance.extend(getattr(request_result, '_added_provenance', []))
            enriched_subjects.extend(getattr(request_result, '_enriched_subjects', []))
            enriched_items.extend(getattr(request_result, '_enriched_items', []))

        if status == 'accepted' and any(not r.accepted for r in request_results):
            status = 'partial'

        updated_budget = ledger.to_budget()
        updated_header = current_ws.header.model_copy(update={'evidence_batches_used': workspace.header.evidence_batches_used + 1})
        plan_state = current_ws.plan_state
        selected_ids = [str(req.request_ref or '') for req in requests if str(req.request_ref or '')]
        completed_ids = [str(rr.request_ref or '') for rr in request_results if getattr(rr, 'accepted', False) and str(rr.request_ref or '')]
        failed_ids = [str(rr.request_ref or '') for rr in request_results if not getattr(rr, 'accepted', False) and str(rr.request_ref or '')]
        ready_span_refs = list(dict.fromkeys([*(getattr(plan_state, 'ready_span_refs', []) or []), *[card.ref for rr in request_results for card in (getattr(rr, 'bangumi_span_cards', []) or []) if bool(getattr(card, 'detail_equivalent', False))]]))
        plan_status = 'completed' if selected_ids and len(completed_ids) == len(selected_ids) and not failed_ids else ('blocked' if failed_ids and not completed_ids else ('in_progress' if selected_ids else getattr(plan_state, 'plan_status', 'idle')))
        completed_span_request_count = len([rid for rid in completed_ids if rid.startswith('REQ_TARGET_SPAN_')])
        updated_notebook = close_notebook_agenda_for_evidence_results(getattr(current_ws, 'investigation_notebook', None), request_results)
        current_ws = CaseEvidenceWorkspace.from_cards(
            header=updated_header,
            budget=updated_budget,
            contract=current_ws.contract,
            local_files=current_ws.local_files,
            local_clusters=current_ws.local_clusters,
            local_span_cards=current_ws.local_span_cards,
            bangumi_subjects=current_ws.bangumi_subjects,
            bangumi_relations=current_ws.bangumi_relations,
            bangumi_groups=current_ws.bangumi_groups,
            bangumi_items=current_ws.bangumi_items,
            bangumi_span_cards=[
                *current_ws.bangumi_span_cards,
                *[card for rr in request_results for card in (getattr(rr, 'bangumi_span_cards', []) or [])],
            ],
            query_cards=current_ws.query_cards,
            provenance_cards=current_ws.provenance_cards,
            previous_hypotheses=current_ws.previous_hypotheses,
            previous_evidence_results=current_ws.previous_evidence_results,
            verifier_issues=current_ws.verifier_issues,
            diagnostics=current_ws.diagnostics,
            mapping_draft=current_ws.mapping_draft,
            mapping_draft_patches=current_ws.mapping_draft_patches,
            mapping_draft_candidate_comparisons=getattr(current_ws, 'mapping_draft_candidate_comparisons', []),
            case_briefing=getattr(current_ws, 'case_briefing', None),
            investigation_notebook=updated_notebook,
            plan_state=plan_state.model_copy(update={
                'selected_menu_request_ids': list(dict.fromkeys([*(getattr(plan_state, 'selected_menu_request_ids', []) or []), *selected_ids])),
                'completed_menu_request_ids': list(dict.fromkeys([*(getattr(plan_state, 'completed_menu_request_ids', []) or []), *completed_ids])),
                'failed_menu_request_ids': list(dict.fromkeys([*(getattr(plan_state, 'failed_menu_request_ids', []) or []), *failed_ids])),
                'planned_span_request_count': int(getattr(plan_state, 'planned_span_request_count', 0) or 0),
                'selected_span_request_count': len([rid for rid in selected_ids if rid.startswith('REQ_TARGET_SPAN_')]),
                'completed_span_request_count': int(getattr(plan_state, 'completed_span_request_count', 0) or 0) + completed_span_request_count,
                'span_rows_with_candidates': int(getattr(plan_state, 'span_rows_with_candidates', 0) or 0),
                'span_rows_without_candidates': int(getattr(plan_state, 'span_rows_without_candidates', 0) or 0),
                'ready_span_refs': ready_span_refs,
                'plan_status': plan_status,
            }),
        )
        object.__setattr__(current_ws, 'seen_detail_refs', list(dict.fromkeys([*workspace.seen_detail_refs, *current_ws.seen_detail_refs])))
        object.__setattr__(current_ws, 'judge_request_audits', list(getattr(workspace, 'judge_request_audits', []) or []))

        result = EvidenceBatchResult(
            batch_ref=batch_ref,
            round_index=workspace.header.round_index,
            status=status,
            request_results=request_results,
            results=request_results,
            provenance_refs=[card.ref for card in added_provenance],
            added_subject_cards=added_subjects,
            added_relation_cards=added_relations,
            added_group_cards=added_groups,
            added_item_cards=added_items,
            added_provenance_cards=added_provenance,
            enriched_subject_cards=enriched_subjects,
            enriched_item_cards=enriched_items,
            budget_after=updated_budget,
        )
        object.__setattr__(current_ws, 'seen_detail_refs', list(dict.fromkeys([*workspace.seen_detail_refs, *current_ws.seen_detail_refs, *[ref for rr in request_results for ref in (rr.response_refs or [])]])))
        object.__setattr__(current_ws, 'previous_evidence_results', [*workspace.previous_evidence_results, result])
        object.__setattr__(current_ws, 'judge_request_audits', list(getattr(workspace, 'judge_request_audits', []) or []))
        return current_ws, result

    def _validate_request(self, workspace: CaseEvidenceWorkspace, request: EvidenceRequest) -> EvidenceRequestResult | None:
        visible = workspace.visible_refs()
        visible_subject_refs = set(visible.bangumi_subject_refs) | {card.subject_ref for card in workspace.bangumi_items if getattr(card, 'subject_ref', '')}
        if request.anchor_file_refs and any(ref not in visible.local_file_refs for ref in request.anchor_file_refs):
            return EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=['invalid anchor_file_refs'])
        if request.request_type in ('subject_lookup', 'related_expansion', 'episode_list') and request.subject_refs and any(ref not in visible_subject_refs for ref in request.subject_refs):
            return EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=['invalid subject_refs'])
        if request.request_type == 'episode_detail' and request.item_refs and any(ref not in visible.bangumi_item_refs for ref in request.item_refs):
            return EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=['invalid item_refs'])
        if request.request_type == 'subject_search' and request.query_refs and any(ref not in visible.query_refs for ref in request.query_refs):
            return EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=['invalid query_refs'])
        if request.request_type == 'local_file_detail' and request.anchor_file_refs and any(ref not in visible.local_file_refs for ref in request.anchor_file_refs):
            return EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=['invalid anchor_file_refs'])
        if request.request_type == 'target_detail' and request.item_refs and any(ref not in visible.bangumi_item_refs for ref in request.item_refs):
            return EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=['invalid item_refs'])
        if request.request_type == 'target_window' and request.subject_refs and any(ref not in visible_subject_refs for ref in request.subject_refs):
            return EvidenceRequestResult(request_ref=request.request_ref, accepted=False, notes=['invalid subject_refs'])
        return None

    def _execute_request(self, workspace: CaseEvidenceWorkspace, request: EvidenceRequest, ledger: BudgetLedger):
        if request.request_type == 'subject_lookup':
            subjects, provenance, rr, ledger = execute_subject_lookup(request, workspace, None_to_registry(workspace), ledger, self.bangumi_client)
            rr._added_subjects = subjects; rr._added_provenance = provenance
            return workspace.with_replaced_cards(subjects=subjects, provenance=provenance, evidence_results=[rr]), rr, ledger
        if request.request_type == 'subject_search':
            subjects, provenance, rr, ledger = execute_subject_search(request, workspace, None_to_registry(workspace), ledger, self.bangumi_client)
            rr._added_subjects = subjects; rr._added_provenance = provenance
            return workspace.with_added_evidence(subjects=subjects, provenance=provenance, evidence_results=[rr]), rr, ledger
        if request.request_type == 'related_expansion':
            subjects, relations, provenance, rr, ledger = execute_related_expansion(request, workspace, None_to_registry(workspace), ledger, self.bangumi_client)
            rr._added_subjects = subjects; rr._added_relations = relations; rr._added_provenance = provenance
            return workspace.with_added_evidence(subjects=subjects, relations=relations, provenance=provenance, evidence_results=[rr]), rr, ledger
        if request.request_type == 'episode_list':
            groups, items, provenance, rr, ledger = execute_episode_list(request, workspace, None_to_registry(workspace), ledger, self.bangumi_client)
            rr._added_groups = groups; rr._added_items = items; rr._added_provenance = provenance
            return workspace.with_added_evidence(groups=groups, items=items, provenance=provenance, evidence_results=[rr]), rr, ledger
        if request.request_type == 'episode_detail':
            items, provenance, rr, ledger = execute_episode_detail(request, workspace, None_to_registry(workspace), ledger, self.bangumi_client)
            rr._enriched_items = items
            return workspace.with_replaced_cards(items=items, evidence_results=[rr]).with_seen_detail_refs([card.ref for card in items]), rr, ledger
        if request.request_type == 'local_file_detail':
            cards = [card for card in workspace.local_files if card.ref in request.anchor_file_refs]
            notes = [] if cards else ['no matching local files']
            rr = _build_result(request, cards=cards, notes=notes)
            rr = rr.model_copy(update={'notes': [*rr.notes, _request_failure_reason(request, notes)]} if not cards else rr.model_dump(mode='json')) if False else rr
            return workspace.with_replaced_cards(evidence_results=[rr]).with_seen_detail_refs([card.ref for card in cards]), rr, ledger
        if request.request_type == 'target_detail':
            cards = [card for card in workspace.bangumi_items if card.ref in request.item_refs]
            notes = [] if cards else ['no matching targets']
            rr = _build_result(request, cards=cards, notes=notes)
            return workspace.with_replaced_cards(items=cards, evidence_results=[rr]).with_seen_detail_refs([card.ref for card in cards]), rr, ledger
        if request.request_type == 'target_window':
            items = [card for card in workspace.bangumi_items if (not request.subject_refs or card.subject_ref in request.subject_refs)]
            if request.sort_start or request.sort_end:
                items = [card for card in items if (not request.sort_start or card.sort >= request.sort_start) and (not request.sort_end or card.sort <= request.sort_end)]
            notes = [] if items else ['no matching target window']
            if len(items) > PROMPT_DETAIL_CARD_CAP:
                notes = [*notes, 'target_window too wide; request narrower window']
            if items:
                notes = [*notes, 'returned refs are sparse; only returned refs are assignable']
            rr = _build_result(request, cards=items, notes=notes)
            return workspace.with_replaced_cards(items=items, evidence_results=[rr]).with_seen_detail_refs([card.ref for card in items]), rr, ledger
        if request.request_type == 'target_span':
            if request.local_span_ref == 'LS_PACKAGE':
                rr = EvidenceRequestResult(request_ref=request.request_ref, request_type=request.request_type, accepted=False, notes=['package_span_requires_child_span_requests'])
                return workspace.with_replaced_cards(evidence_results=[rr]), rr, ledger
            if _is_special_eligible_target_span_request(workspace, request):
                notes = [f'special-like local span uses special/movie evidence path, not regular target_span: {request.local_span_ref or ""}']
                rr = _build_target_span_result(request, [], notes)
                return workspace.with_replaced_cards(evidence_results=[rr]), rr, ledger
            spans = _target_span_candidate_cards(workspace, request)
            if not spans:
                spans = _windowed_item_span_candidates(workspace, request)
            if not spans:
                notes = [f'no matching span for local_span_ref={request.local_span_ref or ""}']
                rr = _build_target_span_result(request, [], notes)
                return workspace.with_replaced_cards(evidence_results=[rr]), rr, ledger
            notes = [f'covered expected_count={request.expected_count}', f'local_span_ref={request.local_span_ref or ""}', 'detail_equivalent=true']
            rr = _build_target_span_result(request, spans, notes)
            span_detail_refs = list(dict.fromkeys([*[card.ref for card in spans], *[target_ref for card in spans for target_ref in (getattr(card, 'target_refs', []) or [])]]))
            return workspace.with_replaced_cards(evidence_results=[rr]).with_seen_detail_refs(span_detail_refs), rr, ledger
        return workspace, EvidenceRequestResult(request_ref=request.request_ref, request_type=request.request_type, accepted=False, notes=['unknown request_type']), ledger


def None_to_registry(workspace: CaseEvidenceWorkspace):
    from .broker_registry import EvidenceCardRegistry
    return EvidenceCardRegistry.from_workspace(workspace)
