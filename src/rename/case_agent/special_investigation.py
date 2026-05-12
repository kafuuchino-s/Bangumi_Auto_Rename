from __future__ import annotations

from .models import BangumiItemCard, CaseDossier, LocalSpanCard, MappingDraft
from .source_form import SINGLETON_SOURCE_FORM_HINTS


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


def is_special_eligible_span(span: LocalSpanCard | None, dossier: CaseDossier) -> bool:
    if span is None:
        return False
    if str(getattr(span, 'span_scope', '') or '') not in {'residual', 'unpartitioned'}:
        return False
    file_refs = list(getattr(span, 'file_refs', []) or [])
    if not file_refs and int(getattr(span, 'file_ref_count', 0) or 0) == 1:
        file_refs = list(getattr(span, 'file_ref_samples', []) or [])[:1]
    if int(getattr(span, 'file_ref_count', 0) or len(file_refs)) != 1 or len(file_refs) != 1:
        return False
    if int(getattr(span, 'episode_token_count', 0) or 0) != 0:
        return False
    if getattr(span, 'episode_token_start', None) is not None or getattr(span, 'episode_token_end', None) is not None:
        return False
    main_refs = set(getattr(getattr(dossier, 'contract', None), 'main_file_refs', []) or [])
    file_ref = file_refs[0]
    if main_refs and file_ref not in main_refs:
        return False
    file_card = next((card for card in list(getattr(dossier, 'local_files', []) or []) if getattr(card, 'ref', '') == file_ref), None)
    if file_card is None:
        return False
    if not bool(getattr(file_card, 'is_main', False)):
        return False
    return str(getattr(file_card, 'file_kind', '') or 'unknown') != 'subtitle'


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
