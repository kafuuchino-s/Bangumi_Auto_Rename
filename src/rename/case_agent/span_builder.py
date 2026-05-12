from __future__ import annotations

from collections import Counter

from .models import BangumiItemCard, BangumiSpanCard, CaseDossier, LocalSpanCard


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _collect_sample(values: list[str], *, sample_limit: int = 4) -> list[str]:
    values = [value for value in values if value]
    if len(values) <= sample_limit:
        return list(values)
    return _dedupe_preserve_order([*values[: max(1, sample_limit // 2)], *values[-max(1, sample_limit // 2):]])


def build_local_span_cards(dossier: CaseDossier, *, min_count: int = 24) -> list[LocalSpanCard]:
    """Compatibility shell only.

    Local working spans are produced by LocalStructureAgent. This helper no
    longer interprets filename numbers or partitions package semantics.
    """
    main_refs = list(dict.fromkeys(list(getattr(dossier.contract, 'main_file_refs', []) or [])))
    if len(main_refs) < min_count:
        return []
    return [
        LocalSpanCard(
            ref='LS_PACKAGE',
            span_scope='package',
            file_refs=main_refs,
            file_ref_count=len(main_refs),
            file_ref_range=[main_refs[0], main_refs[-1]],
            file_ref_samples=_collect_sample(main_refs),
            ordering_basis='path_order',
            confidence_facts=['compatibility raw local coverage shell; no filename ordinal inference'],
        ),
        LocalSpanCard(
            ref='LS1',
            span_scope='unpartitioned',
            file_refs=main_refs,
            file_ref_count=len(main_refs),
            file_ref_range=[main_refs[0], main_refs[-1]],
            file_ref_samples=_collect_sample(main_refs),
            ordering_basis='path_order',
            confidence_facts=['compatibility raw local coverage shell; LocalStructureAgent should refine'],
        ),
    ]


def build_bangumi_span_cards(*, bangumi_items: list[BangumiItemCard] | None = None) -> list[BangumiSpanCard]:
    items = list(bangumi_items or [])
    if not items:
        return []
    target_refs = [item.ref for item in items if item.ref]
    if not target_refs:
        return []
    return [
        BangumiSpanCard(
            ref='BES1',
            subject_ref=items[0].subject_ref,
            group_ref='',
            target_refs=target_refs,
            target_ref_count=len(target_refs),
            target_ref_range=[target_refs[0], target_refs[-1]],
            target_ref_samples=_collect_sample(target_refs),
            sort_start=min((item.sort for item in items), default=None),
            sort_end=max((item.sort for item in items), default=None),
            ep_start=min((item.ep for item in items), default=None),
            ep_end=max((item.ep for item in items), default=None),
            item_kind='mixed' if len({item.item_kind for item in items}) > 1 else ('regular' if (items[0].item_kind or 'unknown') == 'episode' else (items[0].item_kind or 'unknown')),
            gap_count=0,
            duplicate_count=sum(count - 1 for count in Counter(target_refs).values() if count > 1),
            special_count=sum(1 for item in items if item.item_kind == 'special'),
            title_samples=_collect_sample([item.title or item.name or item.name_cn for item in items]),
        )
    ]


def compact_span_card(card: LocalSpanCard | BangumiSpanCard) -> dict[str, object]:
    if isinstance(card, LocalSpanCard):
        return {
            'ref': card.ref,
            'span_scope': card.span_scope,
            'parent_key': card.parent_key,
            'season_cue': card.season_cue,
            'file_ref_count': card.file_ref_count,
            'file_ref_range': list(card.file_ref_range),
            'file_ref_samples': list(card.file_ref_samples),
            'ordering_basis': card.ordering_basis,
            'episode_token_start': card.episode_token_start,
            'episode_token_end': card.episode_token_end,
            'episode_token_count': card.episode_token_count,
            'gap_count': card.gap_count,
            'duplicate_count': card.duplicate_count,
            'title_cues': list(card.title_cues),
            'release_group_cues': list(card.release_group_cues),
        }
    return {
        'ref': card.ref,
        'target_ref_count': card.target_ref_count,
        'target_ref_range': list(card.target_ref_range),
        'target_ref_samples': list(card.target_ref_samples),
        'sort_start': card.sort_start,
        'sort_end': card.sort_end,
        'ep_start': card.ep_start,
        'ep_end': card.ep_end,
        'item_kind': card.item_kind,
        'gap_count': card.gap_count,
        'duplicate_count': card.duplicate_count,
        'special_count': card.special_count,
        'title_samples': list(card.title_samples),
    }
