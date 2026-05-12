from __future__ import annotations

from typing import Iterable


SINGLETON_SOURCE_FORM_HINTS = {'movie', 'ova', 'special'}


_MOVIE_MARKERS = (
    'movie',
    'film',
    'theater',
    'theatre',
    'gekijou',
    'gekijo',
    '\u5287\u573a',
    '\u5287\u5834',
    '\u6620\u753b',
)
_OVA_MARKERS = ('ova', 'oav')
_SPECIAL_MARKERS = (
    'special',
    'side_story',
    'spinoff',
    'spin-off',
    'extra',
    '\u756a\u5916',
    '\u7279\u522b',
    '\u7279\u5225',
    '\u5916\u4f20',
    '\u5916\u50b3',
    '\u603b\u96c6',
    '\u7dcf\u96c6',
)


def infer_source_form_hint(
    *,
    platform: str = '',
    tags: Iterable[str] = (),
    name: str = '',
    name_cn: str = '',
    relation: str = '',
    relation_to_main: str = '',
    total_episodes: int = 0,
    eps: int = 0,
) -> str:
    text = ' '.join(
        str(value or '').casefold()
        for value in [
            platform,
            name,
            name_cn,
            relation,
            relation_to_main,
            *list(tags or []),
        ]
        if str(value or '').strip()
    )
    platform_key = str(platform or '').strip().casefold()
    episode_count = max(int(total_episodes or 0), int(eps or 0))
    if any(marker in text for marker in _MOVIE_MARKERS):
        return 'movie'
    if platform_key in {'ova', 'oav'} or any(marker in text for marker in _OVA_MARKERS):
        return 'ova'
    if any(marker in text for marker in _SPECIAL_MARKERS):
        return 'special'
    if platform_key in {'tv', 'tv series', 'tv_series'} or episode_count > 1:
        return 'tv_series'
    return 'unknown'


def subject_card_source_form_hint(card: object) -> str:
    explicit = str(getattr(card, 'source_form_hint', '') or '').strip()
    if explicit:
        return explicit
    return infer_source_form_hint(
        platform=str(getattr(card, 'platform', '') or ''),
        tags=list(getattr(card, 'tags', []) or []),
        name=str(getattr(card, 'name', '') or ''),
        name_cn=str(getattr(card, 'name_cn', '') or ''),
        relation=str(getattr(card, 'relation_to_main', '') or ''),
        relation_to_main=str(getattr(card, 'relation_to_main', '') or ''),
        total_episodes=int(getattr(card, 'total_episodes', 0) or 0),
        eps=int(getattr(card, 'eps', 0) or 0),
    )


def is_subject_level_singleton_source(card: object) -> bool:
    hint = subject_card_source_form_hint(card)
    if hint not in SINGLETON_SOURCE_FORM_HINTS:
        return False
    episode_count = max(int(getattr(card, 'total_episodes', 0) or 0), int(getattr(card, 'eps', 0) or 0))
    return episode_count <= 1 or hint == 'movie'
