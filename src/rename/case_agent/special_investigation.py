from __future__ import annotations

from .models import BangumiItemCard, CaseDossier, LocalSpanCard, MappingDraft
from .source_form import SINGLETON_SOURCE_FORM_HINTS

import re


SPECIAL_ASSIGNABLE_ITEM_KINDS = {'special', 'movie'}


def is_special_like_item(card: BangumiItemCard) -> bool:
    source_hint = str(getattr(card, 'source_form_hint', '') or '').casefold()
    subject_level = str(getattr(card, 'subject_level_target', '') or '').casefold() == 'true'
    return bool(
        str(getattr(card, 'item_kind', '') or '').casefold() in SPECIAL_ASSIGNABLE_ITEM_KINDS
        or (bool(getattr(card, 'synthetic', False)) and subject_level)
        or source_hint in SINGLETON_SOURCE_FORM_HINTS
    )


def special_like_item_refs(dossier: CaseDossier) -> list[str]:
    detailed = set(getattr(dossier, 'assignable_target_refs', []) or []) | set(getattr(dossier, 'detailed_card_refs', []) or []) | set(getattr(dossier, 'seen_detail_refs', []) or [])
    refs: list[str] = []
    for card in list(getattr(dossier, 'bangumi_items', []) or []):
        ref = str(getattr(card, 'ref', '') or '')
        if not ref or not is_special_like_item(card):
            continue
        if detailed and ref not in detailed:
            continue
        refs.append(ref)
    return list(dict.fromkeys(refs))


def _span_file_refs(span: LocalSpanCard) -> list[str]:
    refs = list(getattr(span, 'file_refs', []) or [])
    if not refs and int(getattr(span, 'file_ref_count', 0) or 0) == 1:
        refs = list(getattr(span, 'file_ref_samples', []) or [])[:1]
    return [str(ref or '') for ref in refs if str(ref or '')]


def _span_local_text(span: LocalSpanCard, dossier: CaseDossier) -> str:
    files_by_ref = {
        str(getattr(card, 'ref', '') or ''): card
        for card in list(getattr(dossier, 'local_files', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }
    parts = [
        str(getattr(span, 'span_scope', '') or ''),
        str(getattr(span, 'parent_key', '') or ''),
        str(getattr(span, 'season_cue', '') or ''),
        *[str(value or '') for value in list(getattr(span, 'title_cues', []) or [])],
        *[str(value or '') for value in list(getattr(span, 'confidence_facts', []) or [])],
    ]
    for ref in _span_file_refs(span):
        card = files_by_ref.get(ref)
        if card is None:
            continue
        parts.extend([
            str(getattr(card, 'path', '') or ''),
            str(getattr(card, 'basename', '') or ''),
            str(getattr(card, 'label', '') or ''),
            str(getattr(card, 'parent_display', '') or ''),
        ])
    return ' '.join(part for part in parts if part)


def is_special_release_marker_text(text: str) -> bool:
    lowered = str(text or '').casefold()
    if not lowered:
        return False
    if any(marker in lowered for marker in ('tokubetsu', 'special', 'ova', 'oav', 'oad', 'ona', '番外', '特别', '特別')):
        return True
    return bool(re.search(r'(?<![a-z0-9])sp[\s._-]*\d{0,3}(?![a-z0-9])', lowered))


def is_special_eligible_span(span: LocalSpanCard | None, dossier: CaseDossier) -> bool:
    if span is None:
        return False
    span_scope = str(getattr(span, 'span_scope', '') or '')
    if span_scope not in {'directory', 'token_segment', 'residual', 'unpartitioned'}:
        return False
    file_refs = _span_file_refs(span)
    file_count = int(getattr(span, 'file_ref_count', 0) or len(file_refs))
    if file_count <= 0 or file_count != len(file_refs):
        return False
    main_refs = set(getattr(getattr(dossier, 'contract', None), 'main_file_refs', []) or [])
    if main_refs and any(ref not in main_refs for ref in file_refs):
        return False
    file_cards = [
        card for ref in file_refs
        for card in list(getattr(dossier, 'local_files', []) or [])
        if getattr(card, 'ref', '') == ref
    ]
    if len(file_cards) != len(file_refs):
        return False
    if any(not bool(getattr(card, 'is_main', False)) for card in file_cards):
        return False
    if any(str(getattr(card, 'file_kind', '') or 'unknown') == 'subtitle' for card in file_cards):
        return False

    if file_count == 1 and span_scope in {'residual', 'unpartitioned'}:
        if int(getattr(span, 'episode_token_count', 0) or 0) == 0 and getattr(span, 'episode_token_start', None) is None and getattr(span, 'episode_token_end', None) is None:
            return True

    if file_count <= 12 and is_special_release_marker_text(_span_local_text(span, dossier)):
        return True
    return False


def special_eligible_open_row_refs(draft: MappingDraft | None, dossier: CaseDossier) -> list[str]:
    if draft is None:
        return []
    spans = {card.ref: card for card in list(getattr(dossier, 'local_span_cards', []) or []) if getattr(card, 'ref', '')}
    refs: list[str] = []
    for row in list(getattr(draft, 'rows', []) or []):
        if str(getattr(row, 'disposition', '') or '') != 'open' and str(getattr(row, 'status', '') or '') != 'open':
            continue
        if is_special_eligible_span(spans.get(str(getattr(row, 'local_ref', '') or '')), dossier):
            refs.append(str(getattr(row, 'local_ref', '') or ''))
    return refs


def special_eligible_row_refs(draft: MappingDraft | None, dossier: CaseDossier) -> list[str]:
    if draft is None:
        return []
    spans = {card.ref: card for card in list(getattr(dossier, 'local_span_cards', []) or []) if getattr(card, 'ref', '')}
    refs: list[str] = []
    for row in list(getattr(draft, 'rows', []) or []):
        if is_special_eligible_span(spans.get(str(getattr(row, 'local_ref', '') or '')), dossier):
            refs.append(str(getattr(row, 'local_ref', '') or ''))
    return refs
