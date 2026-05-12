from __future__ import annotations

import re
from typing import Any


def normalize_metadata_title(value: str) -> str:
    normalized = re.sub(r'[：:·•|｜/\\\-\._]+', ' ', value or '')
    normalized = re.sub(r'\s+', ' ', normalized)
    return normalized.strip().casefold()


def collect_tv_metadata_alias_titles(
    tv_info: dict[str, Any],
    *,
    alternative_titles: dict[str, Any] | None = None,
    translations: dict[str, Any] | None = None,
) -> list[str]:
    """Collect TV title evidence from TMDB metadata.

    This intentionally does not contain project/work-specific aliases. All
    aliases must come from metadata payloads supplied by TMDB/Bangumi/user data.
    """

    titles: list[str] = []
    for key in ('name', 'original_name'):
        value = tv_info.get(key)
        if isinstance(value, str) and value.strip():
            titles.append(value.strip())

    raw_results = (
        alternative_titles.get('results')
        if isinstance(alternative_titles, dict)
        else None
    )
    if isinstance(raw_results, list):
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            for key in ('title', 'name'):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    titles.append(value.strip())

    raw_translations = (
        translations.get('translations')
        if isinstance(translations, dict)
        else None
    )
    if isinstance(raw_translations, list):
        for item in raw_translations:
            if not isinstance(item, dict):
                continue
            data = item.get('data')
            if not isinstance(data, dict):
                continue
            for key in ('name', 'title'):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    titles.append(value.strip())

    normalized_seen: set[str] = set()
    unique_titles: list[str] = []
    for title in titles:
        normalized = normalize_metadata_title(title)
        if not normalized or normalized in normalized_seen:
            continue
        normalized_seen.add(normalized)
        unique_titles.append(title)

    return unique_titles


def collect_movie_metadata_alias_titles(
    movie_info: dict[str, Any],
    *,
    alternative_titles: dict[str, Any] | None = None,
    translations: dict[str, Any] | None = None,
) -> list[str]:
    """Collect movie title evidence from TMDB metadata without work-specific aliases."""

    titles: list[str] = []
    for key in ('title', 'original_title'):
        value = movie_info.get(key)
        if isinstance(value, str) and value.strip():
            titles.append(value.strip())

    raw_titles = (
        alternative_titles.get('titles')
        if isinstance(alternative_titles, dict)
        else None
    )
    if not isinstance(raw_titles, list) and isinstance(alternative_titles, dict):
        raw_titles = alternative_titles.get('results')
    if isinstance(raw_titles, list):
        for item in raw_titles:
            if not isinstance(item, dict):
                continue
            for key in ('title', 'name'):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    titles.append(value.strip())

    raw_translations = (
        translations.get('translations')
        if isinstance(translations, dict)
        else None
    )
    if isinstance(raw_translations, list):
        for item in raw_translations:
            if not isinstance(item, dict):
                continue
            data = item.get('data')
            if not isinstance(data, dict):
                continue
            for key in ('title', 'name'):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    titles.append(value.strip())

    normalized_seen: set[str] = set()
    unique_titles: list[str] = []
    for title in titles:
        normalized = normalize_metadata_title(title)
        if not normalized or normalized in normalized_seen:
            continue
        normalized_seen.add(normalized)
        unique_titles.append(title)

    return unique_titles
