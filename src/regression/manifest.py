from __future__ import annotations

import json
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from .models import ManifestSnapshot, RenameSample


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST_PATH = (
    PROJECT_ROOT / 'tests' / 'sample_pool' / 'manifest' / 'manifest.json'
)

CHANGED_PATH_TAG_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ('src/rename/ai_processor.py', ('tv_strict_mapping', 'episode_dedupe', 'season_numbering')),
    ('src/rename/process.py', ('movie_resolution', 'mixed_route', 'tv_strict_mapping')),
    ('src/rename/get_info.py', ('movie_resolution',)),
    ('src/rename/cleaner.py', ('movie_resolution', 'tv_strict_mapping')),
    ('src/rename/filename_builder.py', ('mixed_route',)),
    ('src/rename/trans.py', ('mixed_route',)),
    ('src/ai/*', ('tv_strict_mapping', 'movie_resolution', 'mixed_route')),
    ('src/bangumi/*', ('tv_strict_mapping', 'season_numbering')),
    ('src/regression/compare/rename.py', ('compare_normalization',)),
    ('src/regression/*.py', ('compare_normalization',)),
)


def _match_changed_path_rules(normalized_path: str) -> list[tuple[str, tuple[str, ...]]]:
    return [
        (pattern, tags)
        for pattern, tags in CHANGED_PATH_TAG_RULES
        if fnmatch(normalized_path, pattern)
    ]


def _load_json(path: Path) -> dict[str, Any]:
    with open(path, 'r', encoding='utf-8') as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f'Invalid manifest root: {path}')
    return data


def load_manifest(path: Path | None = None) -> tuple[str, list[RenameSample]]:
    manifest_path = path or DEFAULT_MANIFEST_PATH
    payload = _load_json(manifest_path)
    version = str(payload.get('manifest_version') or payload.get('version') or '1')
    raw_entries = payload.get('entries') or []
    if not isinstance(raw_entries, list):
        raise ValueError(f'Invalid manifest entries: {manifest_path}')
    entries = [RenameSample(**entry) for entry in raw_entries if isinstance(entry, dict)]
    return version, entries


def _normalize_changed_path(path: str) -> str:
    return str(path or '').replace('\\', '/').lstrip('./')


def infer_risk_tags_from_changed_paths(
    changed_paths: list[str],
) -> tuple[list[str], list[dict[str, Any]]]:
    inferred_tags: list[str] = []
    inference: list[dict[str, Any]] = []

    for raw_path in changed_paths:
        normalized_path = _normalize_changed_path(raw_path)
        if not normalized_path:
            continue
        matched_tags: list[str] = []
        matched_rules: list[str] = []
        for pattern, tags in _match_changed_path_rules(normalized_path):
            matched_rules.append(pattern)
            for tag in tags:
                if tag not in matched_tags:
                    matched_tags.append(tag)
                if tag not in inferred_tags:
                    inferred_tags.append(tag)
        if matched_tags:
            inference.append(
                {
                    'path': normalized_path,
                    'matched_tags': matched_tags,
                    'matched_rules': matched_rules,
                }
            )

    return inferred_tags, inference


def is_changed_path_relevant(path: str) -> bool:
    normalized_path = _normalize_changed_path(path)
    if not normalized_path:
        return False
    return bool(_match_changed_path_rules(normalized_path))


def filter_manifest_entries(
    entries: list[RenameSample],
    *,
    mode: str,
    sample_id: str | list[str] | tuple[str, ...] | None = None,
    max_samples: int | None = None,
) -> tuple[list[RenameSample], list[str]]:
    notes: list[str] = []
    filtered = list(entries)

    requested_sample_ids: list[str] = []
    if isinstance(sample_id, str):
        requested_sample_ids = [sample_id] if sample_id else []
    elif sample_id:
        requested_sample_ids = [item for item in sample_id if item]

    if requested_sample_ids:
        requested_sample_id_set = set(requested_sample_ids)
        filtered = [entry for entry in filtered if entry.sample_id in requested_sample_id_set]
        notes.append('sample_id filter applied: ' + ', '.join(requested_sample_ids))

    if mode == 'check':
        filtered = [entry for entry in filtered if entry.check]
        notes.append('check filter applied')
    elif mode == 'full':
        notes.append('full selection applied')
    elif mode == 'update-baseline':
        notes.append('update-baseline selection applied')

    if max_samples is not None and max_samples >= 0:
        filtered = filtered[:max_samples]
        notes.append(f'max_samples limited to {max_samples}')

    return filtered, notes


def expand_protected_samples(
    all_entries: list[RenameSample],
    requested_entries: list[RenameSample],
    *,
    inferred_risk_tags: list[str] | None = None,
    changed_paths: list[str] | None = None,
) -> tuple[list[RenameSample], list[dict[str, Any]], list[str]]:
    entry_by_id = {entry.sample_id: entry for entry in all_entries}
    selected_ids = {entry.sample_id for entry in requested_entries}
    scope_expansion: list[dict[str, Any]] = []
    auto_added_ids: list[str] = []
    inferred_tag_set = set(inferred_risk_tags or [])
    normalized_changed_paths = [_normalize_changed_path(path) for path in (changed_paths or []) if path]

    for entry in requested_entries:
        for related_id in entry.always_with:
            related_entry = entry_by_id.get(related_id)
            if related_entry is None or related_id in selected_ids:
                continue
            selected_ids.add(related_id)
            auto_added_ids.append(related_id)
            scope_expansion.append(
                {
                    'requested_sample_id': entry.sample_id,
                    'added_sample_id': related_id,
                    'reason': 'always_with',
                }
            )

        entry_tags = set(entry.tags)
        if not entry_tags:
            continue
        for candidate in all_entries:
            if candidate.sample_id in selected_ids:
                continue
            matched_tags = sorted(entry_tags & set(candidate.protects))
            if not matched_tags:
                continue
            selected_ids.add(candidate.sample_id)
            auto_added_ids.append(candidate.sample_id)
            scope_expansion.append(
                {
                    'requested_sample_id': entry.sample_id,
                    'added_sample_id': candidate.sample_id,
                    'reason': 'protects',
                    'matched_tags': matched_tags,
                }
            )

    if inferred_tag_set:
        for candidate in all_entries:
            if candidate.sample_id in selected_ids:
                continue
            matched_tags = sorted(inferred_tag_set & set(candidate.protects))
            if not matched_tags:
                continue
            selected_ids.add(candidate.sample_id)
            auto_added_ids.append(candidate.sample_id)
            scope_expansion.append(
                {
                    'added_sample_id': candidate.sample_id,
                    'reason': 'changed_paths',
                    'matched_tags': matched_tags,
                    'changed_paths': normalized_changed_paths,
                }
            )

    expanded_entries = [entry for entry in all_entries if entry.sample_id in selected_ids]
    return expanded_entries, scope_expansion, auto_added_ids


def build_snapshot(
    *,
    manifest_version: str,
    mode: str,
    entries: list[RenameSample],
    requested_sample_ids: list[str],
    auto_added_sample_ids: list[str],
    changed_paths: list[str],
    inferred_risk_tags: list[str],
    changed_path_inference: list[dict[str, Any]],
    scope_expansion: list[dict[str, Any]],
    selection_notes: list[str],
) -> ManifestSnapshot:
    return ManifestSnapshot(
        manifest_version=manifest_version,
        mode=mode,
        selected_count=len(entries),
        selected_sample_ids=[entry.sample_id for entry in entries],
        samples=[entry.to_dict() for entry in entries],
        requested_sample_ids=requested_sample_ids,
        auto_added_sample_ids=auto_added_sample_ids,
        changed_paths=changed_paths,
        inferred_risk_tags=inferred_risk_tags,
        changed_path_inference=changed_path_inference,
        scope_expansion=scope_expansion,
        selection_notes=selection_notes,
    )
