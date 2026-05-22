from __future__ import annotations

import json
from importlib import resources

from .dossier import build_bounded_case_dossier, build_initial_compact_projection
from .surface_ledger import build_surface_ledger
from .evidence_menu import build_executable_evidence_menu, build_recommended_neutral_requests as build_recommended_neutral_requests_bundle
from .policy import build_action_policy
from .notebook import build_notebook
from .issue_router import route_verifier_issues
from .models import CaseDossier
from ..local_fact_surface import compact_file_fact_for_card


def _jsonable(value):
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    if hasattr(value, '__dataclass_fields__'):
        return {key: _jsonable(getattr(value, key)) for key in value.__dataclass_fields__}
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value


def _sample_values(values: list[str], *, limit: int = 5) -> list[str]:
    if len(values) <= limit:
        return list(values)
    head = values[: max(1, limit // 2)]
    tail = values[-max(1, limit // 2):]
    return list(dict.fromkeys([*head, *tail]))


def _compact_ref_summary(values: list[str], *, sample_limit: int = 5) -> dict[str, object]:
    values = [ref for ref in values if ref]
    if len(values) <= 20:
        return {'count': len(values), 'refs': list(values)}
    return {
        'count': len(values),
        'range': [values[0], values[-1]],
        'sample_refs': _sample_values(values, limit=sample_limit),
    }


def _assignable_target_surface_summary(bounded) -> dict[str, object]:
    refs = list(getattr(bounded, 'assignable_target_refs', []) or [])
    seen = list(getattr(bounded, 'seen_detail_refs', []) or [])
    gaps = []
    if refs:
        sorted_refs = [r for r in refs if r]
        if len(sorted_refs) >= 2:
            gaps = [f'{sorted_refs[i]}..{sorted_refs[i+1]}' for i in range(len(sorted_refs) - 1) if sorted_refs[i] and sorted_refs[i+1]]
    return {
        'count': len(refs),
        'sample_refs': _sample_values(refs, limit=5),
        'is_sparse': len(refs) < len(seen) or len(refs) < 8,
        'missing_ref_gaps': gaps[:6],
        'rule': 'only explicitly visible/seen/detailed/assignable refs are assignable; BE refs are opaque identifiers, not numeric episode sequences',
    }


def _compact_visible_card(card) -> dict[str, object]:
    data = card.model_dump(mode='json') if hasattr(card, 'model_dump') else dict(card)
    parent_refs = list(data.get('parent_refs') or [])
    parent_subject_refs = list(data.get('parent_subject_refs') or [])
    parent_item_refs = list(data.get('parent_item_refs') or [])
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
        'parent_refs_samples': _sample_values(parent_refs, limit=3),
        'parent_subject_refs_count': len(parent_subject_refs),
        'parent_subject_refs_samples': _sample_values(parent_subject_refs, limit=3),
        'parent_item_refs_count': len(parent_item_refs),
        'parent_item_refs_samples': _sample_values(parent_item_refs, limit=3),
    }


def _compact_local_file_card(card) -> dict[str, object]:
    data = card.model_dump(mode='json') if hasattr(card, 'model_dump') else dict(card)
    path = str(data.get('path', '') or '')
    parent_display = str(data.get('parent_display', '') or '')
    compact = {
        'ref': data.get('ref', ''),
        'basename': path.replace('\\', '/').rsplit('/', 1)[-1],
        'path_tail': path.replace('\\', '/').rsplit('/', 2)[-1] if '/' in path.replace('\\', '/') else path,
        'parent_display': parent_display[:120],
        'label': data.get('label', ''),
        'kind': data.get('file_kind', data.get('kind', '')),
        'is_main': data.get('is_main', False),
        'episode': data.get('episode', data.get('ep', '')),
        'title': data.get('title', ''),
        'title_cn': data.get('title_cn', ''),
        'cues': _sample_values([str(v) for v in (data.get('title_cues') or [])], limit=3),
        'fact_summary': data.get('fact_summary') or {},
    }
    fact_card = compact_file_fact_for_card(data, detail=False)
    if fact_card:
        compact['local_facts'] = fact_card
    return compact


def _compact_local_span_card(card) -> dict[str, object]:
    data = card.model_dump(mode='json') if hasattr(card, 'model_dump') else dict(card)
    file_refs = list(data.get('file_refs') or [])
    return {
        'ref': data.get('ref', ''),
        'span_scope': data.get('span_scope', ''),
        'parent_key': data.get('parent_key', ''),
        'season_cue': data.get('season_cue', ''),
        'count': data.get('file_ref_count', data.get('count', len(file_refs))),
        'range': data.get('file_ref_range', data.get('range', [])),
        'samples': data.get('file_ref_samples', data.get('samples', [])),
        'ordering_basis': data.get('ordering_basis', ''),
        'episode_token_start': data.get('episode_token_start'),
        'episode_token_end': data.get('episode_token_end'),
        'episode_token_count': data.get('episode_token_count', 0),
        'gap': data.get('gap_count', data.get('gap', 0)),
        'duplicate': data.get('duplicate_count', data.get('duplicate', 0)),
        'title_cues': data.get('title_cues', []),
        'release_cues': data.get('release_group_cues', data.get('release_cues', [])),
    }


def _compact_local_span_cards(cards, *, include_ref_samples: bool = True) -> list[dict[str, object]]:
    compacted = [_compact_local_span_card(card) for card in list(cards or [])[:8]]
    for card in compacted:
        if not include_ref_samples:
            card['samples'] = []
            card['range'] = []
            continue
        samples = list(card.get('samples') or [])
        if len(samples) > 2:
            card['samples'] = [samples[0], samples[-1]]
        ref_range = list(card.get('range') or [])
        if len(ref_range) > 2:
            card['range'] = [ref_range[0], ref_range[-1]]
    return compacted


def _compact_bangumi_span_card(card) -> dict[str, object]:
    data = card.model_dump(mode='json') if hasattr(card, 'model_dump') else dict(card)
    target_refs = list(data.get('target_refs') or [])
    return {
        'ref': data.get('ref', ''),
        'subject_ref': data.get('subject_ref', ''),
        'group_ref': data.get('group_ref', ''),
        'count': data.get('target_ref_count', data.get('count', len(target_refs))),
        'range': data.get('target_ref_range', data.get('range', [])),
        'samples': data.get('target_ref_samples', data.get('samples', [])),
        'sort': [data.get('sort_start'), data.get('sort_end')],
        'ep': [data.get('ep_start'), data.get('ep_end')],
        'kind': data.get('item_kind', data.get('kind', '')),
        'detail_equivalent': data.get('detail_equivalent', False),
        'source_request_ref': data.get('source_request_ref', ''),
        'title_samples': data.get('title_samples', []),
    }


def _compact_previous_evidence_summary(bounded) -> list[dict[str, object]]:
    summary = []
    for batch in (bounded.previous_evidence_results or [])[:3]:
        request_results = list(getattr(batch, 'request_results', []) or [])
        response_refs = [ref for rr in request_results for ref in (getattr(rr, 'response_refs', []) or []) if ref]
        request_types = [getattr(rr, 'request_type', '') for rr in request_results if getattr(rr, 'request_type', '')]
        summary.append({
            'batch_ref': getattr(batch, 'batch_ref', ''),
            'status': getattr(batch, 'status', ''),
            'request_type_count': len(request_types),
            'request_type_samples': _sample_values(request_types, limit=4),
            'response_refs': _compact_ref_summary(response_refs, sample_limit=5),
        })
    return summary


def _recommended_neutral_requests(bounded) -> list[dict[str, object]]:
    return list(build_recommended_neutral_requests_bundle(bounded)['recommended_neutral_requests'])


def _phase_g_compact_sections(dossier: CaseDossier, bounded, *, include_ref_samples: bool = True) -> dict[str, object]:
    can_request_more = bool(
        bounded.budget.max_evidence_batches == 0
        or bounded.budget.used_evidence_batches < bounded.budget.max_evidence_batches
    )
    final_opportunity = bool(
        bounded.header.max_rounds
        and bounded.header.round_index >= max(0, bounded.header.max_rounds - 1)
    )
    surface_ledger = build_surface_ledger(dossier)
    evidence_menu = build_recommended_neutral_requests_bundle(bounded)
    executable_evidence_menu = _executable_evidence_menu(bounded)
    local_span_cards = _compact_local_span_cards(getattr(bounded, 'local_span_cards', []) or [], include_ref_samples=include_ref_samples)
    bangumi_span_cards = [_compact_bangumi_span_card(card) for card in list(getattr(bounded, 'bangumi_span_cards', []) or [])[:10]]
    if not include_ref_samples:
        surface_ledger = {
            'case_id': surface_ledger.get('case_id', ''),
            'summary': surface_ledger.get('summary', {}),
            'ref_kind_counts': surface_ledger.get('ref_kind_counts', {}),
            'compact_initial_only': True,
        }
        evidence_menu = {
            'summary': evidence_menu.get('summary', {}),
            'recommended_neutral_request_types': [
                item.get('request_type', '')
                for item in evidence_menu.get('recommended_neutral_requests', [])
                if item.get('request_type', '')
            ],
            'recommended_neutral_request_count': len(evidence_menu.get('recommended_neutral_requests', []) or []),
            'compact_initial_only': True,
        }
        executable_evidence_menu = {
            **executable_evidence_menu,
            'compact_initial_only': True,
        }
    return _jsonable({
        'surface_ledger_summary': surface_ledger,
        'evidence_menu': evidence_menu,
        'executable_evidence_menu': executable_evidence_menu,
        'local_span_cards': local_span_cards,
        'bangumi_span_cards': bangumi_span_cards,
        'span_assignment_policy': 'large continuous packages should use span proof; fixed verifier expands by index; do not full dump refs; request target_span when span proof is insufficient',
        'action_policy': build_action_policy(
            has_evidence=bool(bounded.previous_evidence_results),
            can_request_more=can_request_more,
            final_opportunity=final_opportunity,
            budget=bounded.budget.model_dump(mode='json'),
        ),
        'notebook_compact': build_notebook(dossier),
        'issue_router_summary': route_verifier_issues(list(getattr(dossier, 'verifier_issues', []) or [])),
        'fail_closed_auxiliary_ref_rule': 'fail_closed auxiliary evidence_refs must use visible/prior evidence refs only; leave uncertain refs empty and do not reference output refs',
    })


def _dump_projection(projection: dict[str, object]) -> str:
    return json.dumps(_jsonable(projection), ensure_ascii=False, indent=2)




def _recommended_neutral_request_summary(bounded) -> dict[str, object]:
    requests = _recommended_neutral_requests(bounded)
    request_types = [str(item.get('request_type') or '') for item in requests if str(item.get('request_type') or '')]
    samples = []
    for item in requests:
        if item.get('request_type') == 'target_detail':
            samples.append({'request_type': 'target_detail', 'item_refs': list(item.get('item_refs') or [])[:2], 'reason': item.get('reason', '')})
        elif item.get('request_type') == 'local_file_detail':
            samples.append({'request_type': 'local_file_detail', 'anchor_file_refs': list(item.get('anchor_file_refs') or [])[:2], 'reason': item.get('reason', '')})
        elif item.get('request_type') == 'target_window':
            samples.append({'request_type': 'target_window', 'item_refs': list(item.get('item_refs') or [])[:2], 'reason': item.get('reason', '')})
    anchors_used = {
        'anchor_count': len(list(getattr(bounded, 'detailed_card_refs', []) or []) or list(getattr(bounded, 'seen_detail_refs', []) or []) or list(getattr(getattr(bounded, 'visible_refs', None), 'target_refs', []) or [])),
        'anchor_samples': list(dict.fromkeys([*(getattr(bounded, 'detailed_card_refs', []) or []), *(getattr(bounded, 'seen_detail_refs', []) or []), *(getattr(getattr(bounded, 'visible_refs', None), 'target_refs', []) or [])]))[:8],
    }
    return {
        'recommended_neutral_requests_count': len(requests),
        'recommended_neutral_request_types': request_types,
        'recommended_neutral_request_samples': samples[:6],
        'anchors_used': anchors_used,
        'recommended_neutral_requests_truncated': len(requests) > 6,
    }


def _executable_evidence_menu(bounded) -> dict[str, object]:
    evidence_menu = build_executable_evidence_menu(bounded)
    request_summaries = [
        {
            'request_id': str(item.get('request_id') or ''),
            'request_type': str(item.get('request_type') or ''),
            'summary': str(item.get('summary') or ''),
            'expected_result': str(item.get('expected_result') or ''),
            'neutral': bool(item.get('neutral', True)),
        }
        for item in list(evidence_menu.get('prompt_summaries') or [])
    ]
    request_ids = [str(item.get('request_id') or '') for item in request_summaries if str(item.get('request_id') or '')]
    request_types = []
    for item in list(build_recommended_neutral_requests_bundle(bounded).get('recommended_neutral_requests') or []):
        request_type = str(item.get('request_type') or '')
        if request_type:
            request_types.append(request_type)
    return {
        'request_summaries': request_summaries,
        'evidence_menu_request_ids': request_ids,
        'recommended_neutral_request_types': list(dict.fromkeys(request_types)),
        'summary': evidence_menu.get('summary', {}),
        'unknown_prompt_summary_ids': list(evidence_menu.get('unknown_prompt_summary_ids') or []),
    }


def render_local_bangumi_judge_prompt(dossier: CaseDossier, *, round_kind: str = "judge") -> str:
    template = resources.files(__package__).joinpath("prompts/local_bangumi_judge.md").read_text(encoding="utf-8")
    bounded = build_bounded_case_dossier(dossier)
    if round_kind == 'initial':
        projection = build_initial_compact_projection(bounded)
        projection.update(_phase_g_compact_sections(dossier, bounded, include_ref_samples=False))
        payload = _dump_projection(projection)
    elif round_kind == 'evidence_rejudge':
        projection = {
            'case_id': bounded.header.case_id,
            'case_type': bounded.header.case_type,
            'round_context': bounded.round_context,
            'counts': bounded.counts,
            'salience_overview': bounded.salience_overview,
            'primary_title_cues': bounded.primary_title_cues,
            'release_group_cues': bounded.release_group_cues,
            'previous_evidence_results_summary': _compact_previous_evidence_summary(bounded),
            'detailed_visible_cards': [_compact_visible_card(card) for card in bounded.detailed_visible_cards[:10]],
            'detailed_local_file_cards': [_compact_local_file_card(card) for card in bounded.detailed_local_file_cards[:10]],
            'assignable_target_refs': _compact_ref_summary(list(bounded.assignable_target_refs)),
            'assignable_target_surface': _assignable_target_surface_summary(bounded),
            'seen_detail_refs': _compact_ref_summary(list(bounded.seen_detail_refs)),
            'available_detail_request_types': bounded.available_detail_request_types,
            'recommended_neutral_requests': _recommended_neutral_requests(bounded),
            **_recommended_neutral_request_summary(bounded),
            'budget': bounded.budget.model_dump(mode='json'),
            'verifier_issue_summary': bounded.verifier_issue_summary,
        }
        projection.update(_phase_g_compact_sections(dossier, bounded))
        payload = _dump_projection(projection)
    elif round_kind == 'issue_response':
        projection = {
            'case_id': bounded.header.case_id,
            'case_type': bounded.header.case_type,
            'round_context': bounded.round_context,
            'counts': bounded.counts,
            'salience_overview': bounded.salience_overview,
            'primary_title_cues': bounded.primary_title_cues,
            'release_group_cues': bounded.release_group_cues,
            'verifier_issue_summary': bounded.verifier_issue_summary,
            'issue_router_summary': route_verifier_issues(list(getattr(dossier, 'verifier_issues', []) or [])),
            'previous_evidence_results_summary': _compact_previous_evidence_summary(bounded),
            'detailed_visible_cards': [_compact_visible_card(card) for card in bounded.detailed_visible_cards[:8]],
            'detailed_local_file_cards': [_compact_local_file_card(card) for card in bounded.detailed_local_file_cards[:8]],
            'assignable_target_refs': _compact_ref_summary(list(bounded.assignable_target_refs)),
            'assignable_target_surface': _assignable_target_surface_summary(bounded),
            'seen_detail_refs': _compact_ref_summary(list(bounded.seen_detail_refs)),
            'available_detail_request_types': bounded.available_detail_request_types,
            'recommended_neutral_requests': _recommended_neutral_requests(bounded),
            **_recommended_neutral_request_summary(bounded),
            'budget': bounded.budget.model_dump(mode='json'),
        }
        projection.update(_phase_g_compact_sections(dossier, bounded))
        payload = _dump_projection(projection)
    else:
        projection = {
            'case_id': bounded.header.case_id,
            'case_type': bounded.header.case_type,
            'round_context': bounded.round_context,
            'counts': bounded.counts,
            'salience_overview': bounded.salience_overview,
            'primary_title_cues': bounded.primary_title_cues,
            'release_group_cues': bounded.release_group_cues,
            'previous_evidence_results_summary': _compact_previous_evidence_summary(bounded),
            'verifier_issue_summary': bounded.verifier_issue_summary,
            **_phase_g_compact_sections(dossier, bounded),
            'detailed_visible_cards': [_compact_visible_card(card) for card in bounded.detailed_visible_cards[:8]],
            'detailed_local_file_cards': [_compact_local_file_card(card) for card in bounded.detailed_local_file_cards[:8]],
            'assignable_target_refs': _compact_ref_summary(list(bounded.assignable_target_refs)),
            'assignable_target_surface': _assignable_target_surface_summary(bounded),
            'seen_detail_refs': _compact_ref_summary(list(bounded.seen_detail_refs)),
            'available_detail_request_types': bounded.available_detail_request_types,
            'recommended_neutral_requests': _recommended_neutral_requests(bounded),
            **_recommended_neutral_request_summary(bounded),
            'budget': bounded.budget.model_dump(mode='json'),
            'round_budget': {
                'max_judge_rounds': bounded.budget.max_judge_rounds,
                'max_evidence_batches': bounded.budget.max_evidence_batches,
                'max_issue_response_rounds': bounded.budget.max_issue_response_rounds,
                'max_requests_per_batch': bounded.budget.max_requests_per_batch,
            },
        }
        payload = _dump_projection(projection)
    return template.replace("{{ROUND_KIND}}", round_kind).replace("{{DOSSIER_JSON}}", payload)
