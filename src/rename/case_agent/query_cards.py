from __future__ import annotations

from collections import OrderedDict

from .models import BangumiSubjectCard, LocalClusterCard, LocalFileCard, QueryCard


def _dedupe_preserve_order(cards: list[QueryCard]) -> list[QueryCard]:
    return list(OrderedDict((card.query_text, card) for card in cards).values())


def _basename(path: str) -> str:
    return path.rsplit('\\', 1)[-1].rsplit('/', 1)[-1]


def _add_card(cards: list[QueryCard], query_text: str, source_ref: str, index: int) -> int:
    text = query_text.strip()
    if not text:
        return index
    for card in cards:
        if card.query_text == text:
            if source_ref and source_ref not in card.source_refs:
                card.source_refs.append(source_ref)
            return index
    cards.append(
        QueryCard(
            ref=f'SQ{index}',
            query_text=text,
            query_kind='subject_search',
            query_origin='local_raw',
            source_refs=[source_ref] if source_ref else [],
        )
    )
    return index + 1


def build_query_cards(
    local_files: list[LocalFileCard],
    local_clusters: list[LocalClusterCard],
    bangumi_subjects: list[BangumiSubjectCard] | None = None,
) -> list[QueryCard]:
    cards: list[QueryCard] = []
    index = 1

    for card in local_files:
        basename = card.basename if hasattr(card, 'basename') else _basename(card.path)
        index = _add_card(cards, basename, card.ref, index)
        if card.parent_display:
            index = _add_card(cards, card.parent_display, card.ref, index)

    for card in local_clusters:
        for cue in card.title_cues:
            index = _add_card(cards, cue, card.ref, index)

    for card in bangumi_subjects or []:
        for text in (card.title if hasattr(card, 'title') else '', card.name, card.name_cn):
            index = _add_card(cards, text, card.ref, index)

    return _dedupe_preserve_order(cards)
