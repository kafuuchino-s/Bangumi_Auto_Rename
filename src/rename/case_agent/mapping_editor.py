from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources
from typing import Any

from pydantic import ValidationError

from .mapping_draft import compact_mapping_draft
from .models import CaseDossier, MappingDraft, MappingDraftEditorOutput
from .special_investigation import is_special_eligible_span, is_special_like_item


@dataclass
class MappingDraftEditorCallResult:
    ok: bool
    output: MappingDraftEditorOutput | None
    prompt: str
    error: str
    raw_response: object | None = None


def _jsonable(value: object) -> object:
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    if hasattr(value, '__dataclass_fields__'):
        return {key: _jsonable(getattr(value, key)) for key in value.__dataclass_fields__}
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value


def _sample(values: list[str], *, limit: int = 4) -> list[str]:
    values = [v for v in values if v]
    if len(values) <= limit:
        return values
    head_count = max(1, limit // 2)
    tail_count = max(1, limit - head_count)
    return list(dict.fromkeys([*values[:head_count], *values[-tail_count:]]))[:limit]


def _compact_span_card(card: object) -> dict[str, object]:
    data = card.model_dump(mode='json') if hasattr(card, 'model_dump') else dict(card)
    return {
        'ref': data.get('ref', ''),
        'span_scope': data.get('span_scope', data.get('item_kind', 'unknown')),
        'subject_ref': data.get('subject_ref', ''),
        'group_ref': data.get('group_ref', ''),
        'source_request_ref': data.get('source_request_ref', ''),
        'parent_key': data.get('parent_key', ''),
        'season_cue': data.get('season_cue', ''),
        'count': data.get('file_ref_count', data.get('target_ref_count', data.get('count', 0))),
        'range': data.get('file_ref_range', data.get('target_ref_range', data.get('range', [])))[:6],
        'samples': _sample(list(data.get('file_ref_samples', data.get('target_ref_samples', data.get('samples', []))) or []), limit=6),
        'sort_start': data.get('sort_start', data.get('episode_token_start', None)),
        'sort_end': data.get('sort_end', data.get('episode_token_end', None)),
        'ep_start': data.get('ep_start', None),
        'ep_end': data.get('ep_end', None),
        'item_kind': data.get('item_kind', ''),
        'title_cues': list(data.get('title_cues', data.get('title_samples', [])) or [])[:8],
        'detail_equivalent': data.get('detail_equivalent', False),
    }


def _compact_bangumi_item_card(card: object, subjects_by_ref: dict[str, object] | None = None) -> dict[str, object]:
    data = card.model_dump(mode='json') if hasattr(card, 'model_dump') else dict(card)
    payload = {
        'ref': data.get('ref', ''),
        'subject_ref': data.get('subject_ref', ''),
        'item_kind': data.get('item_kind', ''),
        'kind': data.get('kind', ''),
        'type': data.get('type', ''),
        'sort': data.get('sort', 0),
        'ep': data.get('ep', 0),
        'title': data.get('title', ''),
        'name': data.get('name', ''),
        'name_cn': data.get('name_cn', ''),
        'airdate': data.get('airdate', ''),
        'duration': data.get('duration', ''),
        'duration_seconds': data.get('duration_seconds', 0),
        'desc_short': data.get('desc_short', ''),
        'synthetic': data.get('synthetic', False),
        'subject_level_target': data.get('subject_level_target', ''),
        'source_form_hint': data.get('source_form_hint', ''),
        'relation_to_main': data.get('relation_to_main', ''),
        'parent_refs': _sample(list(data.get('parent_refs', []) or []), limit=4),
    }
    subject_ref = str(data.get('subject_ref', '') or '')
    subject = subjects_by_ref.get(subject_ref) if subjects_by_ref else None
    if subject is not None:
        subject_data = subject.model_dump(mode='json') if hasattr(subject, 'model_dump') else dict(subject)
        payload['subject_card'] = {
            'ref': subject_data.get('ref', ''),
            'subject_id': subject_data.get('subject_id', 0),
            'title': subject_data.get('title', ''),
            'name': subject_data.get('name', ''),
            'name_cn': subject_data.get('name_cn', ''),
            'date': subject_data.get('date', ''),
            'platform': subject_data.get('platform', ''),
            'summary_short': subject_data.get('summary_short', ''),
            'source_form_hint': subject_data.get('source_form_hint', ''),
            'relation_to_main': subject_data.get('relation_to_main', ''),
            'source_role': subject_data.get('source_role', ''),
        }
    return payload


def _compact_bangumi_subject_card(card: object) -> dict[str, object]:
    data = card.model_dump(mode='json') if hasattr(card, 'model_dump') else dict(card)
    return {
        'ref': data.get('ref', ''),
        'subject_id': data.get('subject_id', 0),
        'title': data.get('title', ''),
        'name': data.get('name', ''),
        'name_cn': data.get('name_cn', ''),
        'date': data.get('date', ''),
        'platform': data.get('platform', ''),
        'eps': data.get('eps', 0),
        'total_episodes': data.get('total_episodes', 0),
        'source_form_hint': data.get('source_form_hint', ''),
        'relation_to_main': data.get('relation_to_main', ''),
        'source_role': data.get('source_role', ''),
        'relation_refs': _sample(list(data.get('relation_refs', []) or []), limit=6),
        'item_refs': _sample(list(data.get('item_refs', []) or []), limit=6),
    }


def _compact_bangumi_relation_card(card: object) -> dict[str, object]:
    data = card.model_dump(mode='json') if hasattr(card, 'model_dump') else dict(card)
    return {
        'ref': data.get('ref', ''),
        'relation_kind': data.get('relation_kind', ''),
        'source_subject_ref': data.get('source_subject_ref', ''),
        'target_subject_ref': data.get('target_subject_ref', ''),
        'evidence_refs': _sample(list(data.get('evidence_refs', []) or []), limit=6),
    }


def _bracket_segments(text: str) -> list[str]:
    return [segment.strip() for segment in re.findall(r'\[([^\]]+)\]', str(text or '')) if segment.strip()][:16]


def _filename_anchor_tokens(text: str) -> list[str]:
    tokens = re.split(r'[^0-9A-Za-z]+', str(text or ''))
    return [token for token in tokens if token][:48]


def _title_anchor_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    for segment in _bracket_segments(text):
        cleaned = segment.replace('_', ' ').strip()
        if len(cleaned) >= 6:
            candidates.append(cleaned)
    basename = str(text or '').rsplit('.', 1)[0].replace('_', ' ').strip()
    if basename:
        candidates.append(basename)
    return list(dict.fromkeys(candidates))[:12]


def _local_singleton_context(dossier: CaseDossier, draft: MappingDraft) -> list[dict[str, object]]:
    files_by_ref = {card.ref: card for card in list(getattr(dossier, 'local_files', []) or []) if getattr(card, 'ref', '')}
    spans_by_ref = {card.ref: card for card in list(getattr(dossier, 'local_span_cards', []) or []) if getattr(card, 'ref', '')}
    items_by_ref = {card.ref: card for card in list(getattr(dossier, 'bangumi_items', []) or []) if getattr(card, 'ref', '')}
    subjects_by_ref = {card.ref: card for card in list(getattr(dossier, 'bangumi_subjects', []) or []) if getattr(card, 'ref', '')}
    draft_rows = list(getattr(draft, 'rows', []) or [])
    rows: list[dict[str, object]] = []
    for index, row in enumerate(draft_rows):
        span = spans_by_ref.get(row.local_ref)
        if not is_special_eligible_span(span, dossier):
            continue
        file_refs = list(getattr(span, 'file_refs', []) or [])
        file_cards = [files_by_ref[ref] for ref in file_refs if ref in files_by_ref]
        row_candidate_refs = list(row.candidate_target_refs or [])
        candidate_refs = _sample(row_candidate_refs, limit=12)
        candidate_item_refs = _sample([ref for ref in row_candidate_refs if ref in items_by_ref], limit=12)
        rows.append({
            'row_ref': row.row_ref,
            'local_ref': row.local_ref,
            'status': row.status,
            'disposition': row.disposition,
            'selected_target_ref': row.selected_target_ref,
            'selected_target_kind': row.selected_target_kind,
            'mapping_mode': row.mapping_mode,
            'support_refs': _sample(list(row.support_refs or []), limit=8),
            'reason': row.reason,
            'row_index': index,
            'neighbor_local_refs': [
                getattr(draft_rows[index - 1], 'local_ref', '') if index > 0 else '',
                getattr(draft_rows[index + 1], 'local_ref', '') if index + 1 < len(draft_rows) else '',
            ],
            'candidate_target_refs': candidate_refs,
            'candidate_item_cards': [
                _compact_bangumi_item_card(items_by_ref[ref], subjects_by_ref)
                for ref in candidate_item_refs
            ],
            'candidate_item_ref_count': len([ref for ref in row_candidate_refs if ref in items_by_ref]),
            'span': _compact_span_card(span),
            'files': [
                {
                    'ref': card.ref,
                    'basename': card.basename,
                    'path': card.path,
                    'bracket_segments': _bracket_segments(card.basename),
                    'filename_anchor_tokens': _filename_anchor_tokens(card.basename),
                    'title_anchor_candidates': _title_anchor_candidates(card.basename),
                    'size_bytes': getattr(card, 'size_bytes', 0),
                    'parent_display': card.parent_display,
                    'label': card.label,
                    'file_kind': card.file_kind,
                    'is_main': card.is_main,
                    'related_refs': _sample(list(card.related_refs or []), limit=6),
                }
                for card in file_cards
            ],
        })
    return rows[:12]


def _required_singleton_comparison_rows(dossier: CaseDossier, draft: MappingDraft) -> list[dict[str, object]]:
    spans_by_ref = {card.ref: card for card in list(getattr(dossier, 'local_span_cards', []) or []) if getattr(card, 'ref', '')}
    items_by_ref = {card.ref: card for card in list(getattr(dossier, 'bangumi_items', []) or []) if getattr(card, 'ref', '')}
    issue_codes_by_ref: dict[str, list[str]] = {}
    for issue in list(getattr(dossier, 'verifier_issues', []) or []):
        issue_code = str(getattr(issue, 'issue_code', '') or '')
        if not issue_code:
            continue
        refs = [str(getattr(issue, 'ref', '') or ''), *[str(ref or '') for ref in list(getattr(issue, 'related_refs', []) or [])]]
        for ref in refs:
            if ref:
                issue_codes_by_ref.setdefault(ref, []).append(issue_code)

    rows: list[dict[str, object]] = []
    for row in list(getattr(draft, 'rows', []) or []):
        local_ref = str(getattr(row, 'local_ref', '') or '')
        if not is_special_eligible_span(spans_by_ref.get(local_ref), dossier):
            continue
        candidate_item_refs = [
            ref for ref in list(getattr(row, 'candidate_target_refs', []) or [])
            if ref in items_by_ref and is_special_like_item(items_by_ref[ref])
        ]
        selected_target_ref = str(getattr(row, 'selected_target_ref', '') or '')
        if selected_target_ref and selected_target_ref in items_by_ref and is_special_like_item(items_by_ref[selected_target_ref]) and selected_target_ref not in candidate_item_refs:
            candidate_item_refs.append(selected_target_ref)
        candidate_item_refs = list(dict.fromkeys(candidate_item_refs))
        if len(candidate_item_refs) <= 1:
            continue
        row_ref = str(getattr(row, 'row_ref', '') or '')
        rows.append({
            'row_ref': row_ref,
            'local_ref': local_ref,
            'status': getattr(row, 'status', ''),
            'disposition': getattr(row, 'disposition', ''),
            'selected_target_ref': selected_target_ref,
            'candidate_item_refs': _sample(candidate_item_refs, limit=12),
            'candidate_item_ref_count': len(candidate_item_refs),
            'active_issue_codes': _sample(list(dict.fromkeys([
                *issue_codes_by_ref.get(row_ref, []),
                *issue_codes_by_ref.get(local_ref, []),
            ])), limit=8),
        })
    return rows[:12]


def _singleton_target_conflict_sets(dossier: CaseDossier, draft: MappingDraft) -> list[dict[str, object]]:
    spans_by_ref = {card.ref: card for card in list(getattr(dossier, 'local_span_cards', []) or []) if getattr(card, 'ref', '')}
    items_by_ref = {card.ref: card for card in list(getattr(dossier, 'bangumi_items', []) or []) if getattr(card, 'ref', '')}
    subjects_by_ref = {card.ref: card for card in list(getattr(dossier, 'bangumi_subjects', []) or []) if getattr(card, 'ref', '')}
    rows_by_target: dict[str, list[object]] = {}
    for row in list(getattr(draft, 'rows', []) or []):
        local_ref = str(getattr(row, 'local_ref', '') or '')
        if not is_special_eligible_span(spans_by_ref.get(local_ref), dossier):
            continue
        candidate_refs = [
            str(ref or '')
            for ref in list(getattr(row, 'candidate_target_refs', []) or [])
            if str(ref or '') in items_by_ref and is_special_like_item(items_by_ref[str(ref or '')])
        ]
        selected_ref = str(getattr(row, 'selected_target_ref', '') or '')
        if selected_ref in items_by_ref and is_special_like_item(items_by_ref[selected_ref]):
            candidate_refs.append(selected_ref)
        for target_ref in list(dict.fromkeys(candidate_refs)):
            rows_by_target.setdefault(target_ref, []).append(row)

    conflict_sets: list[dict[str, object]] = []
    for target_ref, rows in rows_by_target.items():
        row_refs = list(dict.fromkeys(str(getattr(row, 'row_ref', '') or '') for row in rows if str(getattr(row, 'row_ref', '') or '')))
        if len(row_refs) <= 1:
            continue
        conflict_sets.append({
            'target_ref': target_ref,
            'target_card': _compact_bangumi_item_card(items_by_ref[target_ref], subjects_by_ref),
            'row_refs': row_refs,
            'local_refs': list(dict.fromkeys(str(getattr(row, 'local_ref', '') or '') for row in rows if str(getattr(row, 'local_ref', '') or ''))),
            'selected_by_row_refs': [
                str(getattr(row, 'row_ref', '') or '')
                for row in rows
                if str(getattr(row, 'selected_target_ref', '') or '') == target_ref
            ],
            'open_row_refs': [
                str(getattr(row, 'row_ref', '') or '')
                for row in rows
                if str(getattr(row, 'status', '') or '') == 'open' or str(getattr(row, 'disposition', '') or '') == 'open'
            ],
        })
    return conflict_sets[:12]


def _sample_cards(cards: list[object], *, limit: int = 12) -> list[object]:
    cards = list(cards or [])
    if len(cards) <= limit:
        return cards
    head = cards[: max(1, limit // 2)]
    tail = cards[-max(1, limit // 2):]
    return [*head, *tail]


def _cards_by_refs(cards: list[object], refs: list[str]) -> list[object]:
    wanted = set(refs)
    return [card for card in list(cards or []) if str(getattr(card, 'ref', '') or '') in wanted]


def _draft_local_span_cards(dossier: CaseDossier, draft: MappingDraft, *, limit: int = 48) -> list[object]:
    local_refs = [str(getattr(row, 'local_ref', '') or '') for row in list(getattr(draft, 'rows', []) or [])]
    package_refs = [
        str(getattr(card, 'ref', '') or '')
        for card in list(getattr(dossier, 'local_span_cards', []) or [])
        if str(getattr(card, 'span_scope', '') or '') == 'package'
    ]
    refs = list(dict.fromkeys([*package_refs, *local_refs]))
    cards = _cards_by_refs(list(getattr(dossier, 'local_span_cards', []) or []), refs)
    return _sample_cards(cards, limit=limit)


def _sampled_draft_candidate_refs(draft: MappingDraft, *, limit_per_row: int = 6) -> list[str]:
    refs: list[str] = []
    for row in list(getattr(draft, 'rows', []) or []):
        refs.extend(_sample(list(getattr(row, 'candidate_target_refs', []) or []), limit=limit_per_row))
        selected = str(getattr(row, 'selected_target_ref', '') or '')
        if selected:
            refs.append(selected)
        refs.extend(list(getattr(row, 'support_refs', []) or []))
    return list(dict.fromkeys(ref for ref in refs if ref))


def _source_bound_bangumi_span_refs(dossier: CaseDossier, draft: MappingDraft) -> list[str]:
    local_refs = {
        str(getattr(row, 'local_ref', '') or '')
        for row in list(getattr(draft, 'rows', []) or [])
        if str(getattr(row, 'local_ref', '') or '')
    }
    request_refs = {f'REQ_TARGET_SPAN_{ref}' for ref in local_refs}
    return [
        str(getattr(card, 'ref', '') or '')
        for card in list(getattr(dossier, 'bangumi_span_cards', []) or [])
        if str(getattr(card, 'ref', '') or '') and str(getattr(card, 'source_request_ref', '') or '') in request_refs
    ]


def _draft_bangumi_span_cards(dossier: CaseDossier, draft: MappingDraft, *, limit: int = 32) -> list[object]:
    refs = list(dict.fromkeys([
        *_source_bound_bangumi_span_refs(dossier, draft),
        *_sampled_draft_candidate_refs(draft),
    ]))
    selected = _cards_by_refs(list(getattr(dossier, 'bangumi_span_cards', []) or []), refs)
    if len(selected) < min(limit, 12):
        existing = {str(getattr(card, 'ref', '') or '') for card in selected}
        for card in _sample_cards(list(getattr(dossier, 'bangumi_span_cards', []) or []), limit=12):
            ref = str(getattr(card, 'ref', '') or '')
            if ref and ref not in existing:
                selected.append(card)
                existing.add(ref)
    return _sample_cards(selected, limit=limit)


def _draft_bangumi_item_cards(dossier: CaseDossier, draft: MappingDraft, *, limit: int = 32) -> list[object]:
    special_item_refs = set()
    for row in list(getattr(draft, 'rows', []) or []):
        special_item_refs.update(ref for ref in list(getattr(row, 'candidate_target_refs', []) or []) if ref)
        selected = str(getattr(row, 'selected_target_ref', '') or '')
        if selected:
            special_item_refs.add(selected)
    bangumi_item_cards = [
        card for card in list(getattr(dossier, 'bangumi_items', []) or [])
        if str(getattr(card, 'ref', '') or '') in special_item_refs or is_special_like_item(card)
    ]
    return _sample_cards(bangumi_item_cards, limit=limit)


def _draft_subject_cards(dossier: CaseDossier, span_cards: list[object], item_cards: list[object], *, limit: int = 24) -> list[object]:
    subject_refs = list(dict.fromkeys([
        *[str(getattr(card, 'subject_ref', '') or '') for card in list(span_cards or [])],
        *[str(getattr(card, 'subject_ref', '') or '') for card in list(item_cards or [])],
    ]))
    cards = _cards_by_refs(list(getattr(dossier, 'bangumi_subjects', []) or []), subject_refs)
    if len(cards) < min(limit, 8):
        existing = {str(getattr(card, 'ref', '') or '') for card in cards}
        for card in _sample_cards(list(getattr(dossier, 'bangumi_subjects', []) or []), limit=8):
            ref = str(getattr(card, 'ref', '') or '')
            if ref and ref not in existing:
                cards.append(card)
                existing.add(ref)
    return _sample_cards(cards, limit=limit)


def _draft_relation_cards(dossier: CaseDossier, subject_cards: list[object], *, limit: int = 24) -> list[object]:
    subject_refs = {
        str(getattr(card, 'ref', '') or '')
        for card in list(subject_cards or [])
        if str(getattr(card, 'ref', '') or '')
    }
    relation_cards = [
        card for card in list(getattr(dossier, 'bangumi_relations', []) or [])
        if str(getattr(card, 'source_subject_ref', '') or '') in subject_refs
        or str(getattr(card, 'target_subject_ref', '') or '') in subject_refs
    ]
    return _sample_cards(relation_cards, limit=limit)


def _compact_mapping_draft(draft: MappingDraft) -> dict[str, object]:
    summary = compact_mapping_draft(draft)
    rows = [
        {
            'row_ref': row.row_ref,
            'local_ref': row.local_ref,
            'local_ref_kind': row.local_ref_kind,
            'status': row.status,
            'disposition': row.disposition,
            'accounted': row.disposition != 'open',
            'selected_target_ref': row.selected_target_ref,
            'selected_target_kind': row.selected_target_kind,
            'mapping_mode': row.mapping_mode,
            'candidate_target_refs': _sample(list(row.candidate_target_refs or []), limit=12),
            'support_refs': _sample(list(row.support_refs or []), limit=8),
            'reason_kind': row.reason_kind,
            'reason': row.reason,
        }
        for row in draft.rows[:40]
    ]
    return {**summary, 'rows': rows}


def _compact_verifier_issue(issue: object) -> dict[str, object]:
    data = issue.model_dump(mode='json') if hasattr(issue, 'model_dump') else dict(issue)
    return {
        'ref': data.get('ref', ''),
        'issue_code': data.get('issue_code', ''),
        'severity': data.get('severity', ''),
        'message': data.get('message', ''),
        'related_refs': _sample(list(data.get('related_refs', []) or []), limit=6),
    }


def render_mapping_draft_editor_prompt(dossier: CaseDossier, draft: MappingDraft, *, round_kind: str = 'draft_edit') -> str:
    template = resources.files(__package__).joinpath('prompts/mapping_draft_editor.md').read_text(encoding='utf-8')
    notebook = getattr(dossier, 'notebook', None)
    local_span_cards = _draft_local_span_cards(dossier, draft)
    bangumi_span_cards = _draft_bangumi_span_cards(dossier, draft)
    bangumi_item_cards = _draft_bangumi_item_cards(dossier, draft)
    bangumi_subject_cards = _draft_subject_cards(dossier, bangumi_span_cards, bangumi_item_cards)
    bangumi_relation_cards = _draft_relation_cards(dossier, bangumi_subject_cards)
    subjects_by_ref = {str(getattr(card, 'ref', '') or ''): card for card in bangumi_subject_cards}
    payload: dict[str, Any] = {
        'round_kind': round_kind,
        'dossier': {
            'case_id': dossier.header.case_id,
            'header': dossier.header.model_dump(mode='json'),
            'budget': dossier.budget.model_dump(mode='json'),
            'local_span_cards': [_compact_span_card(card) for card in local_span_cards],
            'bangumi_subject_cards': [_compact_bangumi_subject_card(card) for card in bangumi_subject_cards],
            'bangumi_relation_cards': [_compact_bangumi_relation_card(card) for card in bangumi_relation_cards],
            'bangumi_span_cards': [_compact_span_card(card) for card in bangumi_span_cards],
            'bangumi_item_cards': [_compact_bangumi_item_card(card, subjects_by_ref) for card in bangumi_item_cards],
            'local_singleton_context': _local_singleton_context(dossier, draft),
            'required_singleton_comparison_rows': _required_singleton_comparison_rows(dossier, draft),
            'singleton_target_conflict_sets': _singleton_target_conflict_sets(dossier, draft),
            'mapping_draft': _compact_mapping_draft(draft),
            'verifier_issues': [_compact_verifier_issue(issue) for issue in list(getattr(dossier, 'verifier_issues', []) or [])[:20]],
            'notebook_summary': notebook if isinstance(notebook, dict) else {},
        },
    }
    return template.replace('{{ROUND_KIND}}', round_kind).replace('{{DOSSIER_JSON}}', json.dumps(_jsonable(payload), ensure_ascii=False, indent=2))


def _call_ai_with_schema(ai_client: object, prompt: str, schema: type[MappingDraftEditorOutput]) -> object:
    if hasattr(ai_client, '_call_with_schema') and callable(getattr(ai_client, '_call_with_schema')):
        return getattr(ai_client, '_call_with_schema')(prompt, schema=schema)
    if hasattr(ai_client, 'call_with_schema') and callable(getattr(ai_client, 'call_with_schema')):
        return getattr(ai_client, 'call_with_schema')(prompt, schema=schema)
    if hasattr(ai_client, 'call_mapping_draft_editor') and callable(getattr(ai_client, 'call_mapping_draft_editor')):
        return getattr(ai_client, 'call_mapping_draft_editor')(prompt, schema)
    if hasattr(ai_client, '_call_openai_simple') and callable(getattr(ai_client, '_call_openai_simple')):
        return getattr(ai_client, '_call_openai_simple')(
            'You are a Mapping Draft Editor. Return strict JSON only.',
            prompt,
            validation_key='mapping_draft_editor',
            schema=schema,
            streaming=False,
        )
    raise AttributeError('ai_client does not provide a schema-aware mapping draft editor call method')


def call_mapping_draft_editor(ai_client: object, dossier: CaseDossier, draft: MappingDraft, *, round_kind: str = 'draft_edit') -> MappingDraftEditorCallResult:
    prompt = render_mapping_draft_editor_prompt(dossier, draft, round_kind=round_kind)
    try:
        response = _call_ai_with_schema(ai_client, prompt, MappingDraftEditorOutput)
        raw_response = getattr(response, 'content', response)
        if isinstance(raw_response, MappingDraftEditorOutput):
            return MappingDraftEditorCallResult(ok=True, output=raw_response, prompt=prompt, error='', raw_response=response)
        try:
            output = MappingDraftEditorOutput.model_validate_json(raw_response) if isinstance(raw_response, str) else MappingDraftEditorOutput.model_validate(raw_response)
        except ValidationError as exc:
            return MappingDraftEditorCallResult(ok=False, output=None, prompt=prompt, error=f'mapping draft editor schema parse error: {exc}', raw_response=response)
        return MappingDraftEditorCallResult(ok=True, output=output, prompt=prompt, error='', raw_response=response)
    except Exception as exc:
        return MappingDraftEditorCallResult(ok=False, output=None, prompt=prompt, error=f'mapping draft editor call failed: {exc}', raw_response=None)
