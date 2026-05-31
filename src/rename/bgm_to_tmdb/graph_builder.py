from __future__ import annotations

import re
import unicodedata
from typing import Any

from ..metadata_resolver import (
    collect_movie_metadata_alias_titles,
    collect_tv_metadata_alias_titles,
)
from .compiler import build_tmdb_legal_graph
from .models import (
    TmdbCandidateCard,
    TmdbLegalGraph,
    TmdbLegalNode,
    TmdbSeasonCard,
    movie_legal_node_id,
    tv_legal_node_id,
)


def build_tmdb_legal_graph_from_payloads(
    *,
    tv_payloads: list[dict[str, Any]] | None = None,
    movie_payloads: list[dict[str, Any]] | None = None,
    generated_by: str = 'tmdb_payloads',
) -> TmdbLegalGraph:
    candidates: list[TmdbCandidateCard] = []
    for payload in tv_payloads or []:
        candidates.append(build_tmdb_tv_candidate_card(payload))
    for payload in movie_payloads or []:
        candidates.append(build_tmdb_movie_candidate_card(payload))
    return build_tmdb_legal_graph(candidates, generated_by=generated_by)


def build_tmdb_tv_candidate_card(
    tv_info: dict[str, Any],
    *,
    alternative_titles: dict[str, Any] | None = None,
    translations: dict[str, Any] | None = None,
    slug: str = '',
    web_url: str = '',
    expand_episode_count: bool = True,
) -> TmdbCandidateCard:
    tmdb_id = int(tv_info.get('id') or 0)
    display_title = _first_text(tv_info, 'name', 'title')
    original_name = _first_text(tv_info, 'original_name', 'original_title')
    semantic_title = display_title or original_name
    semantic_slug = slug or _semantic_slug(tmdb_id, semantic_title)
    legal_nodes: list[TmdbLegalNode] = []
    season_cards: list[TmdbSeasonCard] = []

    for season in _dicts(tv_info.get('seasons')):
        season_number = _int_or_none(season.get('season_number'))
        if season_number is None:
            continue
        season_node_ids: list[str] = []
        episodes = _dicts(season.get('episodes'))
        if episodes:
            for episode in episodes:
                episode_number = _int_or_none(episode.get('episode_number'))
                if episode_number is None or episode_number <= 0:
                    continue
                node_id = tv_legal_node_id(tmdb_id, season_number, episode_number)
                season_node_ids.append(node_id)
                legal_nodes.append(
                    TmdbLegalNode(
                        legal_node_id=node_id,
                        media_type='tv',
                        tmdb_id=tmdb_id,
                        season_number=season_number,
                        episode_number=episode_number,
                        episode_type=str(episode.get('episode_type') or ''),
                        title=str(episode.get('name') or episode.get('title') or ''),
                        air_date=str(episode.get('air_date') or ''),
                        runtime=_int_or_none(episode.get('runtime')),
                        overview=str(episode.get('overview') or ''),
                    )
                )
        elif expand_episode_count:
            count = _int_or_none(season.get('episode_count')) or 0
            for episode_number in range(1, count + 1):
                node_id = tv_legal_node_id(tmdb_id, season_number, episode_number)
                season_node_ids.append(node_id)
                legal_nodes.append(
                    TmdbLegalNode(
                        legal_node_id=node_id,
                        media_type='tv',
                        tmdb_id=tmdb_id,
                        season_number=season_number,
                        episode_number=episode_number,
                    )
                )
        season_cards.append(
            TmdbSeasonCard(
                season_number=season_number,
                name=str(season.get('name') or ''),
                episode_count=_int_or_none(season.get('episode_count')) or len(season_node_ids),
                year=_year_from_date(str(season.get('air_date') or '')),
                overview=str(season.get('overview') or ''),
                legal_node_ids=season_node_ids,
            )
        )

    aliases = _dedupe_titles([
        *_list_texts(tv_info.get('_metadata_alias_titles')),
        *collect_tv_metadata_alias_titles(
            tv_info,
            alternative_titles=alternative_titles,
            translations=translations,
        ),
    ])
    return TmdbCandidateCard(
        media_type='tv',
        tmdb_id=tmdb_id,
        display_title=display_title,
        original_name=original_name,
        slug=semantic_slug,
        web_url=web_url or _tmdb_web_url('tv', semantic_slug),
        year=_year_from_date(str(tv_info.get('first_air_date') or '')),
        overview=str(tv_info.get('overview') or ''),
        aliases=aliases,
        season_cards=season_cards,
        legal_nodes=legal_nodes,
    )


def build_tmdb_movie_candidate_card(
    movie_info: dict[str, Any],
    *,
    alternative_titles: dict[str, Any] | None = None,
    translations: dict[str, Any] | None = None,
    slug: str = '',
    web_url: str = '',
) -> TmdbCandidateCard:
    tmdb_id = int(movie_info.get('id') or 0)
    display_title = _first_text(movie_info, 'title', 'name')
    original_title = _first_text(movie_info, 'original_title', 'original_name')
    semantic_title = display_title or original_title
    semantic_slug = slug or _semantic_slug(tmdb_id, semantic_title)
    aliases = _dedupe_titles([
        *_list_texts(movie_info.get('_metadata_alias_titles')),
        *collect_movie_metadata_alias_titles(
            movie_info,
            alternative_titles=alternative_titles,
            translations=translations,
        ),
    ])
    return TmdbCandidateCard(
        media_type='movie',
        tmdb_id=tmdb_id,
        display_title=display_title,
        original_title=original_title,
        slug=semantic_slug,
        web_url=web_url or _tmdb_web_url('movie', semantic_slug),
        year=_year_from_date(str(movie_info.get('release_date') or '')),
        overview=str(movie_info.get('overview') or ''),
        aliases=aliases,
        legal_nodes=[
            TmdbLegalNode(
                legal_node_id=movie_legal_node_id(tmdb_id),
                media_type='movie',
                tmdb_id=tmdb_id,
                title=display_title,
                air_date=str(movie_info.get('release_date') or ''),
                runtime=_int_or_none(movie_info.get('runtime')),
                overview=str(movie_info.get('overview') or ''),
            )
        ],
    )


def _semantic_slug(tmdb_id: int, title: str) -> str:
    normalized = unicodedata.normalize('NFKD', str(title or ''))
    ascii_title = normalized.encode('ascii', 'ignore').decode('ascii')
    slug = re.sub(r'[^a-zA-Z0-9]+', '-', ascii_title).strip('-').lower()
    return f'{int(tmdb_id)}-{slug}' if slug else str(int(tmdb_id))


def _tmdb_web_url(media_type: str, slug: str) -> str:
    if not slug:
        return ''
    return f'https://www.themoviedb.org/{media_type}/{slug}'


def _first_text(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''


def _dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _list_texts(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _year_from_date(value: str) -> int | None:
    match = re.match(r'^(\d{4})', str(value or ''))
    return int(match.group(1)) if match else None


def _dedupe_titles(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        title = str(value or '').strip()
        key = re.sub(r'\s+', ' ', title).casefold()
        if not title or key in seen:
            continue
        seen.add(key)
        result.append(title)
    return result
