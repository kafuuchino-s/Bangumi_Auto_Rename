from __future__ import annotations

import json
import re
import subprocess
import time
from collections import Counter
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any

from src.bangumi.relation_filters import (
    STRICT_RELATED_RELATION_KINDS,
    is_strict_related_relation,
    normalize_relation_name,
    strict_requested_relation_keys,
)

from .dossier import build_bounded_case_dossier
from .models import (
    BangumiItemCard,
    BangumiSubjectCard,
    CaseJudgeOutput,
    CaseVerifierResult,
    FailClosedReason,
    VerifierIssue,
)
from .recipe import (
    CompiledOrganizePlan,
    OrganizeRecipeDraft,
    compile_and_verify_organize_recipe,
    recipe_accounting,
)
from .workspace import CaseEvidenceWorkspace


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _json_clone(value: Any) -> Any:
    return json.loads(json.dumps(_json_safe(value), ensure_ascii=False))


def _counter(values: list[str]) -> dict[str, int]:
    return dict(Counter(value for value in values if value))


_CONTENT_SHAPE_TOKEN_RE = re.compile(
    r'(?i)\b('
    r'OVA|OAV|OAD|ONA|SP|SPECIAL|TVSP|MOVIES?|FILM|GEKIJOU?BAN|EIGA|RECAP|DIGEST|'
    r'SOUSHUUHEN|SOSHUUHEN|OMAKE|EXTRA|BONUS|PART|CD|DVD|DISC|DISK'
    r')\b'
)
_LOCATOR_TOKEN_RE = re.compile(
    r'(?i)'
    r'(S\d{1,2}E\d{1,3}(?:\.\d+)?)'
    r'|((?<![A-Za-z0-9])(?:SP|SPECIAL|OVA|OAV|OAD|ONA|MOVIE|PART|CD|DVD|DISC|DISK)(?=[\s._-]*\d)[\s._-]*[0-9A-Z]{1,4})'
    r'|((?<![A-Za-z0-9])(?:EP|E)(?=[\s._-]*\d)[\s._-]*\d{1,3}(?:\.\d+)?)'
    r'|((?<![A-Za-z0-9])#?\d{1,3}(?:\.\d+)?(?![A-Za-z0-9]))'
)
_SKELETON_LOCATOR_RE = re.compile(
    r'(?i)'
    r'(?P<sxe>S(?P<sxe_season>\d{1,2})E(?P<sxe_ep>\d{1,3})(?:v(?P<sxe_version>\d+))?)'
    r'|(?P<kind_token>(?<![A-Za-z0-9])(?P<kind>SP|SPECIAL|OVA|OAV|OAD|ONA|MOVIE|PART|CD|DVD|DISC|DISK)[\s._-]*(?P<kind_ep>\d{1,4})(?:v(?P<kind_version>\d+))?)'
    r'|(?P<ep_token>(?<![A-Za-z0-9])(?:EP|E)[\s._-]*(?P<ep_ep>\d{1,3})(?:v(?P<ep_version>\d+))?)'
    r'|(?P<num_token>(?<![A-Za-z0-9])#?(?P<num_ep>\d{1,3})(?:v(?P<num_version>\d+))?(?:\.\d+)?(?![A-Za-z0-9]))'
)
_TECH_NUMERIC_TOKENS = {
    '480', '540', '720', '1080', '1440', '1920', '2160', '264', '265', '266',
    '10bit', '8bit',
}
_TECH_VARIATION_RE = re.compile(
    r'(?i)(?:\d+FLAC|FLACx?\d*|x26[456]|H\.?26[456]|HEVC|AVC|AAC|OPUS|TRUEHD|DTS|'
    r'1080p|720p|2160p|10bit|8bit|Ma10p|Hi10P|BDRip|BluRay|WEB[-_ ]?DL|WEBRip)'
)
_HASH_TOKEN_RE = re.compile(r'(?i)\b[A-F0-9]{6,10}\b')
_SKELETON_ASSET_GROUP_RE = re.compile(
    r'(?i)('
    r'\b(?:asset|art|booklet|cast|credit|design|event|gallery|interview|menu|mv|nc(?:ed|op)|pv|scan|'
    r'script|setting|trailer)\b|オリジナル|デザイン|設定|設定画|脚本|絵コンテ|原画|特典|'
    r'イベント|先行|完成披露'
    r')'
)
_SKELETON_EXACT_ANCHOR_RE = re.compile(r'(?i)\b(movie|movies|film|ova|oav|oad|ona|gekijou?ban|eiga)\b|劇場|剧场|映画')
_REVIEW_LONG_EXCLUDED_SECONDS = 15 * 60
_REVIEW_OBVIOUS_EXTRA_RE = re.compile(r'(?i)(?:^|[/\[\]\s_-])iv\d{1,3}(?:$|[/\[\]\s_-])')
_REVIEW_SUPPLEMENTAL_DIR_RE = re.compile(r'(?i)^(?:SPs?|Specials?|Bonus|Extras?)$')
_REVIEW_BRACKETED_BARE_IV_RE = re.compile(r'(?i)\[\s*IV\s*\]')
_STRICT_BANGUMI_RELATION_FILTER_NOTE = (
    'Strict relation filter: only sequel/prequel/side-story/adaptation/collection/spinoff style '
    'relations are traversed. Weak cross-title relations such as role appearances, collaborations, '
    'same-worldview, other, staff, music, books, and games are skipped before graph expansion.'
)
_REVIEW_OBVIOUS_EXTRA_TERMS = [
    'after talk',
    'audio commentary',
    'cast',
    'commentary',
    'drama',
    'event',
    'greeting',
    'interview',
    'journey',
    'location',
    'live',
    'making',
    'memorial',
    'museum',
    'pre-release',
    'pv',
    'recitation',
    'recap',
    'redubbing',
    'stage',
    'special program',
    'summary',
    'talk',
    'tour',
    'travel',
    'travelogue',
    'tv-spot',
    'ロケ',
    '旅',
    '出張',
    '公开直前',
    '公开前',
    '特别节目',
    '特別番組',
    '特番',
    '軌跡',
    '轨迹',
]


def _float_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _stem_for_path(path: str) -> str:
    basename = _norm_path(path).rsplit('/', 1)[-1]
    if '.' in basename:
        return basename.rsplit('.', 1)[0]
    return basename


def _compact_text(value: str, *, limit: int = 140) -> str:
    text = re.sub(r'\s+', ' ', str(value or '')).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + '...'


def _common_suffix(values: list[str]) -> str:
    if not values:
        return ''
    reversed_values = [value[::-1] for value in values if value]
    if not reversed_values:
        return ''
    return _common_prefix(reversed_values)[::-1]


def _common_prefix(values: list[str]) -> str:
    if not values:
        return ''
    prefix = values[0]
    for value in values[1:]:
        while prefix and not value.startswith(prefix):
            prefix = prefix[:-1]
        if not prefix:
            break
    return prefix


def _locator_tokens(stem_or_name: str) -> list[dict[str, Any]]:
    tokens: list[dict[str, Any]] = []
    for match in _LOCATOR_TOKEN_RE.finditer(str(stem_or_name or '')):
        raw = next((group for group in match.groups() if group), '')
        normalized = re.sub(r'\s+', '', raw.upper().replace('_', '').replace('-', '').replace('.', '.'))
        if normalized.casefold() in _TECH_NUMERIC_TOKENS:
            continue
        numeric_match = re.search(r'\d+(?:\.\d+)?', normalized)
        numeric = numeric_match.group(0) if numeric_match else ''
        tokens.append({
            'token': raw,
            'normalized': normalized,
            'number': numeric,
            'start': match.start(),
            'end': match.end(),
        })
    return tokens


def _prefix_before_first_locator(stem: str) -> str:
    tokens = _locator_tokens(stem)
    if not tokens:
        return _compact_text(stem)
    prefix = stem[: int(tokens[0]['start'] or 0)]
    return _compact_text(prefix.strip(' ._-'))


def _content_shape_counts(values: list[str]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for value in values:
        for match in _CONTENT_SHAPE_TOKEN_RE.finditer(str(value or '')):
            token = match.group(1).upper()
            if token == 'MOVIES':
                token = 'MOVIE'
            counter[token] += 1
    return dict(sorted(counter.items()))


def _numeric_run_summary(numbers: list[str]) -> dict[str, Any]:
    parsed_ints: list[int] = []
    decimal_or_other: list[str] = []
    for number in numbers:
        text = str(number or '').strip()
        if not text:
            continue
        if re.fullmatch(r'\d+', text):
            parsed_ints.append(int(text))
        else:
            decimal_or_other.append(text)
    unique_ints = sorted(set(parsed_ints))
    ranges: list[str] = []
    if unique_ints:
        start = prev = unique_ints[0]
        for value in unique_ints[1:]:
            if value == prev + 1:
                prev = value
                continue
            ranges.append(str(start) if start == prev else f'{start}-{prev}')
            start = prev = value
        ranges.append(str(start) if start == prev else f'{start}-{prev}')
    return {
        'count': len(numbers),
        'unique_count': len(set(numbers)),
        'integer_ranges': ranges,
        'other_values_sample': sorted(set(decimal_or_other))[:12],
    }


def _duration_summary(cards: list[Any]) -> dict[str, Any]:
    durations: list[int] = []
    available_count = 0
    for card in cards:
        facts = getattr(card, 'container_facts', {}) or {}
        if str(facts.get('probe_status') or '') == 'available':
            available_count += 1
        try:
            seconds = int(facts.get('duration_seconds') or 0)
        except Exception:
            seconds = 0
        if seconds > 0:
            durations.append(seconds)
    if not durations:
        return {'available_count': available_count, 'with_duration_count': 0}
    values = sorted(durations)
    return {
        'available_count': available_count,
        'with_duration_count': len(values),
        'min_seconds': values[0],
        'max_seconds': values[-1],
        'median_seconds': values[len(values) // 2],
    }


def _duration_seconds_for_card(card: Any) -> float | None:
    if card is None:
        return None
    container = getattr(card, 'container_facts', {}) or {}
    facts = getattr(card, 'fact_summary', {}) or {}
    if isinstance(container, dict):
        value = _float_or_none(container.get('duration_seconds'))
        if value is not None:
            return value
    if isinstance(facts, dict):
        value = _float_or_none(facts.get('duration_seconds'))
        if value is not None:
            return value
    return None


def _looks_like_obvious_extra_for_review(source_path: str, reason: str) -> bool:
    text = f'{source_path} {reason}'.casefold()
    if _REVIEW_OBVIOUS_EXTRA_RE.search(text):
        return True
    if _looks_like_supplemental_dir_bracketed_iv(source_path):
        return True
    return any(term.casefold() in text for term in _REVIEW_OBVIOUS_EXTRA_TERMS)


def _looks_like_supplemental_dir_bracketed_iv(source_path: str) -> bool:
    path = _norm_path(source_path)
    parts = [part for part in path.split('/') if part]
    if len(parts) < 2:
        return False
    if not any(_REVIEW_SUPPLEMENTAL_DIR_RE.fullmatch(part) for part in parts[:-1]):
        return False
    return bool(_REVIEW_BRACKETED_BARE_IV_RE.search(parts[-1]))


def _review_sequence_key_and_number(source_path: str) -> tuple[tuple[str, str, str], int] | None:
    path = _norm_path(source_path)
    if not path:
        return None
    parent, basename = path.rsplit('/', 1) if '/' in path else ('', path)
    stem = basename.rsplit('.', 1)[0] if '.' in basename else basename
    for token in _locator_tokens(stem):
        number_text = str(token.get('number') or '')
        if not number_text or '.' in number_text:
            continue
        try:
            number = int(number_text)
        except ValueError:
            continue
        start = int(token.get('start') or 0)
        end = int(token.get('end') or 0)
        prefix = re.sub(r'\s+', ' ', stem[:start]).strip().casefold()
        suffix = re.sub(r'\s+', ' ', stem[end:]).strip().casefold()
        return (parent.casefold(), prefix, suffix), number
    return None


def _prefix_group_summary(cards: list[Any], *, limit: int = 8) -> list[dict[str, Any]]:
    buckets: dict[str, list[Any]] = {}
    for card in cards:
        path = _norm_path(str(getattr(card, 'path', '') or ''))
        stem = _stem_for_path(path)
        prefix = _prefix_before_first_locator(stem)
        buckets.setdefault(prefix or '(no locator prefix)', []).append(card)
    rows: list[dict[str, Any]] = []
    for prefix, members in sorted(buckets.items(), key=lambda item: (-len(item[1]), item[0]))[:limit]:
        stems = [_stem_for_path(str(getattr(card, 'path', '') or '')) for card in members]
        first_tokens = [_locator_tokens(stem)[0] for stem in stems if _locator_tokens(stem)]
        rows.append({
            'prefix': prefix,
            'file_count': len(members),
            'first_locator_tokens_sample': [token['normalized'] for token in first_tokens[:20]],
            'first_locator_numbers': _numeric_run_summary([str(token.get('number') or '') for token in first_tokens if token.get('number')]),
            'source_paths_sample': [_norm_path(str(getattr(card, 'path', '') or '')) for card in members[:5]],
        })
    return rows


def _local_structure_summary(cards: list[Any]) -> dict[str, Any]:
    visible_cards = [
        card for card in cards
        if bool(getattr(card, 'is_main', False)) and _norm_path(str(getattr(card, 'path', '') or ''))
    ]
    folders: dict[str, list[Any]] = {}
    for card in visible_cards:
        path = _norm_path(str(getattr(card, 'path', '') or ''))
        parent = str(getattr(card, 'parent_display', '') or '')
        if not parent and '/' in path:
            parent = path.rsplit('/', 1)[0]
        folders.setdefault(parent or '.', []).append(card)

    folder_groups: list[dict[str, Any]] = []
    repeated_starts: dict[str, set[str]] = {}
    for folder, members in sorted(folders.items(), key=lambda item: (-len(item[1]), item[0]))[:40]:
        paths = [_norm_path(str(getattr(card, 'path', '') or '')) for card in members]
        basenames = [path.rsplit('/', 1)[-1] for path in paths]
        stems = [_stem_for_path(path) for path in paths]
        first_tokens = [_locator_tokens(stem)[0] for stem in stems if _locator_tokens(stem)]
        for token in first_tokens:
            repeated_starts.setdefault(str(token.get('normalized') or ''), set()).add(folder)
        folder_groups.append({
            'folder': folder,
            'file_count': len(members),
            'common_filename_prefix': _compact_text(_common_prefix(basenames)),
            'common_filename_suffix': _compact_text(_common_suffix(basenames)),
            'content_shape_token_counts': _content_shape_counts([folder, *basenames]),
            'first_locator_tokens_sample': [token['normalized'] for token in first_tokens[:24]],
            'first_locator_numbers': _numeric_run_summary([str(token.get('number') or '') for token in first_tokens if token.get('number')]),
            'duration_seconds': _duration_summary(members),
            'prefix_groups': _prefix_group_summary(members),
            'source_paths_sample': paths[:8],
        })

    repeated_numbering_starts = [
        {
            'first_locator': token,
            'folder_count': len(folders_for_token),
            'folders_sample': sorted(folders_for_token)[:10],
        }
        for token, folders_for_token in sorted(repeated_starts.items())
        if token and len(folders_for_token) > 1
    ]
    return {
        'visible_file_count': len(visible_cards),
        'folder_count': len(folders),
        'folder_groups': folder_groups,
        'repeated_numbering_starts': repeated_numbering_starts[:24],
        'summary_policy': 'Factual grouping aid only. It is not a semantic mapping decision; verify subject identity with Bangumi evidence.',
    }


def _skeleton_locator_tokens(stem_or_name: str) -> list[dict[str, Any]]:
    tokens: list[dict[str, Any]] = []
    for match in _SKELETON_LOCATOR_RE.finditer(str(stem_or_name or '')):
        raw = match.group(0)
        if match.group('sxe'):
            number = str(match.group('sxe_ep') or '')
            version = str(match.group('sxe_version') or '')
            locator_kind = 's00e' if int(match.group('sxe_season') or 0) == 0 else 'sxe'
        elif match.group('kind_token'):
            number = str(match.group('kind_ep') or '')
            version = str(match.group('kind_version') or '')
            kind = str(match.group('kind') or '').casefold()
            locator_kind = 'special' if kind in {'sp', 'special'} else kind
        elif match.group('ep_token'):
            number = str(match.group('ep_ep') or '')
            version = str(match.group('ep_version') or '')
            locator_kind = 'regular'
        else:
            number = str(match.group('num_ep') or '')
            version = str(match.group('num_version') or '')
            locator_kind = 'regular'
        if number.casefold() in _TECH_NUMERIC_TOKENS:
            continue
        number_start = match.start() + raw.find(number)
        number_end = number_start + len(number)
        tokens.append({
            'token': raw,
            'normalized': re.sub(r'\s+', '', raw.upper().replace('_', '').replace('-', '')),
            'number': number.lstrip('0') or '0',
            'number_width': len(number),
            'version_suffix': f'v{version}' if version else '',
            'locator_kind': locator_kind,
            'start': match.start(),
            'end': match.end(),
            'number_start': number_start,
            'number_end': number_end,
        })
    return tokens


def _skeleton_title_hint(prefix: str, fallback: str = '') -> str:
    text = str(prefix or '').strip(' ._-')
    text = re.sub(r'^\[[^\]]+\]\s*', '', text)
    text = re.sub(r'(?i)\[[^\]]*(?:BDRip|BluRay|WEB[-_ ]?DL|WEBRip|x264|x265|HEVC|AVC|FLAC|AAC|Hi10P|Ma10p|10bit|1080p|720p|2160p)[^\]]*\]', ' ', text)
    text = re.sub(r'(?i)\b(BDRip|BluRay|WEB[-_ ]?DL|WEBRip|x264|x265|HEVC|AVC|FLAC|AAC|Hi10P|Ma10p|10bit|1080p|720p|2160p)\b', ' ', text)
    text = re.sub(r'\[\s*\]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip(' ._-[]')
    if text:
        return _compact_text(text)
    return _compact_text(str(fallback or '').strip(' ._-[]'))


def _skeleton_group_key(card: Any) -> tuple[str, str, str]:
    path = _norm_path(str(getattr(card, 'path', '') or ''))
    parent = str(getattr(card, 'parent_display', '') or '')
    if not parent and '/' in path:
        parent = path.rsplit('/', 1)[0]
    stem = _stem_for_path(path)
    tokens = _skeleton_locator_tokens(stem)
    if tokens:
        token = tokens[0]
        prefix = stem[: int(token['start'] or 0)]
        title = _skeleton_title_hint(prefix, parent)
        locator_kind = str(token.get('locator_kind') or 'regular')
    else:
        title = _skeleton_title_hint(stem, parent)
        locator_kind = 'unnumbered'
    return (parent.casefold(), title.casefold(), locator_kind.casefold())


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _path_suffix(path: str) -> str:
    basename = _norm_path(path).rsplit('/', 1)[-1]
    if '.' not in basename:
        return ''
    return basename.rsplit('.', 1)[-1]


def _selector_source_pattern_for_group(members: list[Any]) -> str:
    if not members:
        return ''
    first_path = _norm_path(str(getattr(members[0], 'path', '') or ''))
    first_stem = _stem_for_path(first_path)
    first_tokens = _skeleton_locator_tokens(first_stem)
    if not first_tokens:
        return ''
    token = first_tokens[0]
    basename = first_path.rsplit('/', 1)[-1]
    stem_start = first_path.rfind(basename)
    full_number_start = stem_start + int(token.get('number_start') or 0)
    full_token_end = stem_start + int(token.get('end') or 0)
    width = int(token.get('number_width') or 0)
    ep_placeholder = f'{{ep:0{width}}}' if width > 1 else '{ep}'
    after_token_values: list[str] = []
    for member in members:
        path = _norm_path(str(getattr(member, 'path', '') or ''))
        stem = _stem_for_path(path)
        tokens = _skeleton_locator_tokens(stem)
        if not tokens:
            continue
        member_basename = path.rsplit('/', 1)[-1]
        member_stem_start = path.rfind(member_basename)
        member_token_end = member_stem_start + int(tokens[0].get('end') or 0)
        after_token_values.append(path[member_token_end:])
    suffix = f'.{_path_suffix(first_path)}' if _path_suffix(first_path) else ''
    after = f'*{suffix}' if len(set(after_token_values)) > 1 and suffix else first_path[full_token_end:]
    return first_path[:full_number_start] + ep_placeholder + '{ver}' + after


def _source_pattern_matches(pattern: str, path: str) -> bool:
    if not pattern:
        return False
    try:
        regex = re.compile(_source_pattern_to_regex(pattern), re.IGNORECASE)
    except re.error:
        return False
    basename = _norm_path(path).rsplit('/', 1)[-1]
    return bool(regex.search(basename) or regex.search(_norm_path(path)))


def _selector_preview_for_group(pattern: str, members: list[Any], all_visible_paths: list[str]) -> dict[str, Any]:
    group_paths = sorted({_norm_path(str(getattr(member, 'path', '') or '')) for member in members if _norm_path(str(getattr(member, 'path', '') or ''))})
    matched = sorted(path for path in all_visible_paths if _source_pattern_matches(pattern, path))
    missing = sorted(set(group_paths) - set(matched))
    extra = sorted(set(matched) - set(group_paths))
    safe = bool(group_paths) and not missing and not extra
    return {
        'safe': safe,
        'matched_count': len(matched),
        'expected_count': len(group_paths),
        'matched_paths_sample': matched[:12],
        'missing_paths_sample': missing[:8],
        'extra_paths_sample': extra[:8],
    }


def _variation_notes_for_group(members: list[Any]) -> list[str]:
    versions: set[str] = set()
    tech_tokens_union: set[str] = set()
    tech_token_sets: set[tuple[str, ...]] = set()
    hash_count = 0
    after_locator_values: set[str] = set()
    for member in members:
        path = _norm_path(str(getattr(member, 'path', '') or ''))
        stem = _stem_for_path(path)
        tokens = _skeleton_locator_tokens(stem)
        if tokens:
            version = str(tokens[0].get('version_suffix') or '')
            if version:
                versions.add(version)
            after_locator_values.add(stem[int(tokens[0].get('end') or 0):])
        member_tech_tokens: set[str] = set()
        for match in _TECH_VARIATION_RE.finditer(path):
            member_tech_tokens.add(match.group(0).casefold())
        if member_tech_tokens:
            tech_tokens_union.update(member_tech_tokens)
            tech_token_sets.add(tuple(sorted(member_tech_tokens)))
        if _HASH_TOKEN_RE.search(path):
            hash_count += 1
    notes: list[str] = []
    if versions:
        notes.append(f'version suffixes vary or appear: {sorted(versions)[:6]}')
    if len(tech_token_sets) > 1:
        notes.append(f'technical tokens vary: {sorted(tech_tokens_union)[:10]}')
    if hash_count:
        notes.append('hash/checksum-like tokens appear in filenames')
    if len(after_locator_values) > 1:
        notes.append('suffix after episode locator varies; wildcard selector preview is safer than copying one filename suffix')
    return notes


def _local_recipe_skeleton(cards: list[Any]) -> dict[str, Any]:
    visible_cards = [
        card for card in cards
        if bool(getattr(card, 'is_main', False)) and _norm_path(str(getattr(card, 'path', '') or ''))
    ]
    buckets: dict[tuple[str, str, str], list[Any]] = {}
    for card in visible_cards:
        buckets.setdefault(_skeleton_group_key(card), []).append(card)

    visible_paths = sorted(_norm_path(str(getattr(card, 'path', '') or '')) for card in visible_cards)
    groups: list[dict[str, Any]] = []
    diagnostics: list[str] = []
    for index, (_key, members) in enumerate(sorted(buckets.items(), key=_skeleton_bucket_sort_key), start=1):
        if index > 40:
            diagnostics.append('local_recipe_skeleton_group_limit_reached')
            break
        paths = [_norm_path(str(getattr(card, 'path', '') or '')) for card in members]
        basenames = [path.rsplit('/', 1)[-1] for path in paths]
        folder = str(getattr(members[0], 'parent_display', '') or '')
        if not folder and '/' in paths[0]:
            folder = paths[0].rsplit('/', 1)[0]
        first_stem = _stem_for_path(paths[0])
        first_tokens = _skeleton_locator_tokens(first_stem)
        first_token = first_tokens[0] if first_tokens else {}
        title_prefix = first_stem[: int(first_token.get('start') or 0)] if first_token else first_stem
        title_hint = _skeleton_title_hint(title_prefix, folder)
        numbers = [
            value for value in (_safe_int((_skeleton_locator_tokens(_stem_for_path(path)) or [{}])[0].get('number')) for path in paths)
            if value is not None
        ]
        locator_kind = str(first_token.get('locator_kind') or 'unnumbered')
        source_pattern = _selector_source_pattern_for_group(members)
        preview = _selector_preview_for_group(source_pattern, members, visible_paths) if source_pattern else {
            'safe': False,
            'matched_count': 0,
            'expected_count': len(paths),
            'matched_paths_sample': [],
            'missing_paths_sample': paths[:8],
            'extra_paths_sample': [],
        }
        numbers_are_unique = len(numbers) == len(set(numbers))
        source_paths_payload: dict[str, Any] = {
            'sample': paths[:12],
            'count': len(paths),
        }
        if len(paths) <= 16:
            source_paths_payload['all'] = paths
        else:
            source_paths_payload['omitted_count'] = len(paths) - 12
        selector_hint: dict[str, Any] = {
            'recommended_shape': 'source_pattern' if preview.get('safe') and len(paths) > 1 and numbers_are_unique else 'exact_paths_or_split',
            'source_pattern': source_pattern,
            'exact_paths': paths if len(paths) <= 8 else paths[:8],
            'coverage_preview': preview,
        }
        boundary_warnings: list[str] = [] if preview.get('safe') or len(paths) == 1 else ['selector_hint_source_pattern_is_not_coverage_safe']
        if numbers and not numbers_are_unique:
            boundary_warnings.append('duplicate_episode_numbers_in_group')
        group = {
            'group_ref': f'LG{len(groups) + 1}',
            'group_kind_hint': _local_group_kind_hint(locator_kind, [folder, *basenames], len(paths)),
            'folder': folder or '.',
            'title_hint': title_hint,
            'locator_kind_hint': locator_kind,
            'source_path_count': len(paths),
            'source_paths': source_paths_payload,
            'representative_source_path': paths[0] if paths else '',
            'number_summary': _numeric_run_summary([str(number) for number in numbers]),
            'duration_seconds': _duration_summary(members),
            'content_shape_token_counts': _content_shape_counts([folder, *basenames]),
            'variation_notes': _variation_notes_for_group(members),
            'selector_hint': selector_hint,
            'suggested_title_query': _dedupe_nonempty([title_hint, _query_from_source_path(paths[0]) if paths else ''])[0] if _dedupe_nonempty([title_hint, _query_from_source_path(paths[0]) if paths else '']) else '',
            'boundary_warnings': boundary_warnings,
        }
        groups.append(group)
    return {
        'visible_file_count': len(visible_cards),
        'group_count': len(groups),
        'groups': groups,
        'diagnostics': diagnostics,
        'skeleton_policy': 'Factual local grouping and selector coverage preview only. It does not choose Bangumi subject_id, episode_id, disposition, or supplemental status.',
    }


def _local_recipe_params_scaffold(local_recipe_skeleton: dict[str, Any]) -> dict[str, Any]:
    groups: list[dict[str, Any]] = []
    for group in local_recipe_skeleton.get('groups') or []:
        if not isinstance(group, dict):
            continue
        selector_hint = group.get('selector_hint') if isinstance(group.get('selector_hint'), dict) else {}
        source_paths = group.get('source_paths') if isinstance(group.get('source_paths'), dict) else {}
        coverage_preview = selector_hint.get('coverage_preview') if isinstance(selector_hint.get('coverage_preview'), dict) else {}
        number_summary = group.get('number_summary') if isinstance(group.get('number_summary'), dict) else {}
        integer_ranges = [str(value) for value in (number_summary.get('integer_ranges') or []) if str(value)]
        source_path_count = int(group.get('source_path_count') or 0)

        selector_stub: dict[str, Any] = {}
        if coverage_preview.get('safe') is True and source_path_count > 1 and selector_hint.get('source_pattern'):
            selector_stub['source_pattern'] = str(selector_hint.get('source_pattern') or '')
            if integer_ranges:
                selector_stub['episode_range'] = integer_ranges[0]
                selector_stub['episode_offset'] = 'EP'
                selector_stub['episode_number_field'] = 'sort'
        elif source_path_count == 1 and group.get('representative_source_path'):
            selector_stub['exact_paths'] = [str(group.get('representative_source_path') or '')]
        elif isinstance(source_paths.get('all'), list):
            selector_stub['exact_paths'] = [str(path) for path in source_paths.get('all') or [] if str(path)]
        else:
            selector_stub['selector_note'] = 'Use exact_paths from this group or split the group before validating; no compact coverage-safe selector exists.'
            selector_stub['source_paths_sample'] = [str(path) for path in (source_paths.get('sample') or []) if str(path)]

        params_rule_stub = {
            'name': f"{group.get('group_ref') or 'LG'} {group.get('title_hint') or 'local group'}".strip(),
            'group_ref': group.get('group_ref'),
            **selector_stub,
            'reason': 'Fill after choosing Bangumi evidence.',
        }
        groups.append({
            'group_ref': group.get('group_ref'),
            'group_kind_hint': group.get('group_kind_hint'),
            'title_hint': group.get('title_hint'),
            'locator_kind_hint': group.get('locator_kind_hint'),
            'source_path_count': source_path_count,
            'representative_source_path': group.get('representative_source_path'),
            'number_summary': number_summary,
            'duration_seconds': group.get('duration_seconds'),
            'boundary_warnings': group.get('boundary_warnings') or [],
            'variation_notes': group.get('variation_notes') or [],
            'selector_coverage_safe': bool(coverage_preview.get('safe')),
            'params_rule_stub': params_rule_stub,
            'target_fields_for_mapped_rule': [
                'subject_id',
                'media_kind',
                'episode_type for sequence rules, or episode_id for exact row rules',
            ],
            'supplemental_fields_if_evidence_does_not_support_mapping': {
                'disposition': 'non_bangumi_or_supplemental',
                'reason': 'short evidence-gap reason',
            },
        })
    return {
        'visible_file_count': local_recipe_skeleton.get('visible_file_count', 0),
        'group_count': len(groups),
        'groups': groups,
        'scaffold_policy': (
            'Local selector scaffold only. It copies factual source selectors/ranges from local_recipe_skeleton '
            'and does not choose Bangumi subject_id, episode_id, media_kind, episode_type, disposition, or supplemental status.'
        ),
        'usage_hint': (
            'Copy a params_rule_stub or use its group_ref as a selector shorthand, fill the target fields from Bangumi evidence or set the supplemental disposition, '
            'then call validate_organize_recipe_params as a trial check.'
        ),
    }


def _local_group_kind_hint(locator_kind: str, values: list[str], count: int) -> str:
    tokens = _content_shape_counts(values)
    key = str(locator_kind or '').casefold()
    joined = ' '.join(str(value or '') for value in values)
    if _SKELETON_ASSET_GROUP_RE.search(joined):
        return 'asset_or_bonus_candidate'
    if any(_looks_like_supplemental_folder(str(value or '')) for value in values):
        return 'special_or_bonus_candidate'
    if key in {'special', 'ova', 'oav', 'oad', 'ona', 'movie'} or any(token in tokens for token in ('SP', 'SPECIAL', 'OVA', 'OAD', 'MOVIE', 'FILM', 'RECAP', 'BONUS', 'EXTRA')):
        return 'special_or_bonus_candidate'
    if key == 'unnumbered' or count <= 1:
        return 'exact_or_standalone_candidate'
    return 'numbered_sequence_candidate'


def _skeleton_bucket_sort_key(item: tuple[tuple[str, str, str], list[Any]]) -> tuple[int, int, str]:
    key, members = item
    paths = [_norm_path(str(getattr(card, 'path', '') or '')) for card in members]
    basenames = [path.rsplit('/', 1)[-1] for path in paths]
    folder = str(getattr(members[0], 'parent_display', '') or '') if members else ''
    if not folder and paths and '/' in paths[0]:
        folder = paths[0].rsplit('/', 1)[0]
    locator_kind = key[2] if len(key) >= 3 else ''
    group_kind = _local_group_kind_hint(locator_kind, [folder, *basenames], len(paths))
    rank_by_kind = {
        'numbered_sequence_candidate': 0,
        'exact_or_standalone_candidate': 1,
        'special_or_bonus_candidate': 2,
        'asset_or_bonus_candidate': 3,
    }
    return (rank_by_kind.get(group_kind, 4), -len(members), '|'.join(str(part) for part in key))


def _looks_like_supplemental_folder(folder: str) -> bool:
    normalized = _norm_path(folder).rsplit('/', 1)[-1].strip().casefold()
    return normalized in {'sp', 'sps', 'special', 'specials', 'bonus', 'bonuses', 'extra', 'extras'}


@dataclass
class PiCaseToolState:
    workspace: CaseEvidenceWorkspace
    bangumi_client: Any
    run_dir: Path
    repo_root: Path
    source_path: str = ''
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    final_result: dict[str, Any] | None = None
    last_invalid_submission: dict[str, Any] | None = None
    submit_rejection_count: int = 0
    organize_recipe: OrganizeRecipeDraft | None = None
    compiled_plan: CompiledOrganizePlan | None = None
    recipe_verifier_result: CaseVerifierResult | None = None
    latest_recipe_params_payload: dict[str, Any] | None = None
    latest_recipe_params_draft_payload: dict[str, Any] | None = None
    latest_recipe_group_decisions_payload: dict[str, Any] | None = None
    latest_recipe_params_patch_payload: dict[str, Any] | None = None
    latest_recipe_params_patch_merged_payload: dict[str, Any] | None = None
    latest_recipe_params_patch_accepted: bool = False
    local_recipe_skeleton_cache: dict[str, Any] | None = None
    local_recipe_params_scaffold_cache: dict[str, Any] | None = None
    _request_index: int = 0
    _query_index: int = 0

    _EVIDENCE_BATCH_LIMIT = 24
    _MAX_CASE_BOARD_CONTENT_CHARS = 1600
    _MAX_GROUP_DECISIONS_PER_UPSERT = 4
    _MAX_GROUP_DECISION_REASON_CHARS = 180
    _MAX_GROUP_DECISION_SUMMARY_CHARS = 240
    _MAX_GROUP_DECISION_EXACT_PATHS = 3
    _EVIDENCE_BATCH_TOOLS = {
        'get_case_context',
        'get_local_group_detail',
        'get_local_file_detail',
        'get_local_selector_scaffold',
        'get_local_recipe_params_scaffold',
        'search_bangumi_subjects',
        'lookup_bangumi_subject',
        'expand_related_subjects',
        'expand_related_graph',
        'select_bangumi_anchor_subject',
        'build_bangumi_relation_atlas',
        'find_bangumi_targets_for_local_file',
        'get_episode_list',
        'get_target_window',
        'get_target_detail',
    }
    _WORKPAPER_RESET_TOOLS = {
        'append_case_board_note',
        'select_bangumi_anchor_subject',
        'upsert_recipe_group_decision_one',
        'upsert_recipe_group_decision',
        'clear_recipe_group_decisions',
        'upsert_recipe_params_draft',
        'clear_recipe_params_draft',
        'validate_recipe_params_draft',
        'validate_organize_recipe_params',
        'validate_organize_recipe_params_patch',
        'submit_organize_recipe_params',
        'submit_organize_recipe_params_patch',
        'validate_organize_recipe',
        'submit_organize_recipe',
        'fail_closed',
    }

    def __post_init__(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / 'artifacts').mkdir(parents=True, exist_ok=True)

    @property
    def case_id(self) -> str:
        return str(getattr(self.workspace.header, 'case_id', '') or 'local-bangumi-pi')

    def case_input(self, *, pi_command: str = '', timeout_seconds: int = 0) -> dict[str, Any]:
        artifacts_dir = self.run_dir / 'artifacts'
        return {
            'case_id': self.case_id,
            'case_agent_mode': 'pi_case_agent',
            'task_source_path': self.source_path,
            'case_overview': self._case_overview_payload(),
            'navigation': self._case_navigation_payload(),
            'run_progress': self._run_progress_payload(),
            'local_identity_policy': 'Only source_path values exposed by get_local_group_detail, get_local_file_detail, or context.local_files[].source_path may be passed as local source_path. task_source_path is the original task/sample path, not a local file identity.',
            'pi_command': pi_command,
            'run_dir': str(self.run_dir),
            'runtime_policy': {
                'turn_cap_enabled': False,
                'turn_count_is_audit_only': True,
                'wall_clock_timeout_seconds': max(0, int(timeout_seconds or 0)),
                'suggested_finish_before_seconds': max(0, max(0, int(timeout_seconds or 0)) - 15),
                'finalization_buffer_seconds': 15 if int(timeout_seconds or 0) > 15 else 0,
                'bangumi_evidence_requests_allowed': True,
                'empty_visible_target_refs_means_search_first': True,
            },
            'scratch_paths': {
                'artifacts_dir': str(artifacts_dir),
                'organize_recipe': str(artifacts_dir / 'organize_recipe.json'),
                'recipe_group_decisions': str(artifacts_dir / 'recipe_group_decisions.json'),
                'recipe_params_draft': str(artifacts_dir / 'recipe_params_draft.json'),
                'bangumi_relation_atlas_dir': str(artifacts_dir / 'bangumi_relation_atlas'),
                'notes': str(artifacts_dir / 'notes.md'),
                'helper_check': str(artifacts_dir / 'organize_recipe_helper_check.json'),
            },
            'case_goal': {
                'objective': 'Produce a Python-verifier accepted OrganizeRecipeDraft or fail closed.',
                'done_when': [
                    'organize_recipe scratch artifact is updated by validate or submit; notes.md may hold Pi-owned append-only case board notes for complex investigations',
                    'local skill helper has checked the recipe shape',
                    'trial validation feedback has been used for any needed repair',
                    'submit_organize_recipe_params or submit_organize_recipe_params_patch returns accepted=true',
                    'goal_complete is called only after accepted=true',
                ],
            },
            'tool_semantics': {
                'validate_organize_recipe_params': 'Trial-check semantic params without finalizing the case. First validation does not need to be accepted; invalid/review feedback is meant for repair.',
                'get_case_overview': 'Return the case map: counts, group index, seen Bangumi evidence counts, recipe state, and navigation handles. It is not a route recommendation.',
                'list_local_groups': 'Return the local group index. Pi chooses which group to inspect.',
                'get_local_group_detail': 'Expand one local group by group_ref with source paths and local facts.',
                'get_local_selector_scaffold': 'Return selector/range params stubs for one group_ref or all groups; group_ref may be used as a local selector shorthand, while Pi must fill Bangumi target or supplemental disposition from evidence.',
                'get_local_recipe_params_scaffold': 'Return local selector/range params stubs only; group_ref may be used as a local selector shorthand, while Pi must fill Bangumi target or supplemental disposition from evidence.',
                'get_recipe_state': 'Return latest params, verifier, and submit state without changing the case.',
                'append_case_board_note': 'Append Initial Board or ordinary Board Delta to scratch_paths.notes as Pi-owned working memory. Validation/Patch/Submit snapshots are preferably passed into the params transaction tools. This is audit I/O only; it does not decide targets or recipe state.',
                'get_case_board_notes': 'Read scratch_paths.notes tail/latest content for context recovery. This is audit I/O only; it does not choose next steps.',
                'select_bangumi_anchor_subject': 'Record Pi-selected reliable main anime/video anchor and atomically build the relation atlas. This is evidence bootstrap only; Python does not choose the anchor or any recipe target.',
                'build_bangumi_relation_atlas': 'From one Pi-chosen reliable anime/video anchor, fully traverse strict relation-filtered reachable Bangumi anime/video related subjects with guard limits, hydrate compact episode row surfaces, and write a relation atlas artifact. This is an evidence atlas only; Python does not rank subjects or choose recipe rows.',
                'upsert_recipe_group_decision_one': 'Save exactly one compact Pi-owned group/subcluster semantic decision. Preferred normal path for action-style work. Python mechanically compiles saved decisions into recipe_params_draft but does not choose Bangumi targets or supplemental status.',
                'upsert_recipe_group_decision': 'Save a small batch of Pi-owned group/subcluster semantic decisions; prefer upsert_recipe_group_decision_one for normal action-style work. Python mechanically compiles saved decisions into recipe_params_draft but does not choose Bangumi targets or supplemental status.',
                'get_recipe_group_decisions': 'Read saved group/subcluster decisions plus the compiled recipe_params_draft coverage preview.',
                'clear_recipe_group_decisions': 'Clear saved group decisions and the generated recipe_params_draft.',
                'upsert_recipe_params_draft': 'Save or update Pi-owned partial recipe_params rules before full validation. Partial drafts are expected: one judged group can be saved while other groups remain pending. This is working memory only; it does not run verifier or decide semantic targets.',
                'get_recipe_params_draft': 'Read current partial recipe_params draft and non-semantic local coverage preview. ready_for_full_validation=false is normal while Pi is still adding judged groups.',
                'clear_recipe_params_draft': 'Clear the Pi-owned partial recipe_params draft.',
                'validate_recipe_params_draft': 'Validate the current draft only after it covers every visible local group; this calls validate_organize_recipe_params with validation_snapshot.',
                'submit_organize_recipe_params': 'Finalize only after params validation is accepted and review_warnings are resolved.',
                'validate_organize_recipe_params_patch': 'Trial-check a small patch against the latest params after params validation exists; before first params validation, the same patch shape updates recipe_params_draft and returns coverage preview without running verifier.',
                'submit_organize_recipe_params_patch': 'Finalize a patch after accepted patch validation; reusing the same patch submits the accepted merged params instead of applying append_rules twice.',
            },
            'context': {
                'run_progress': self._run_progress_payload(),
                'local_files': [{'source_path': path} for path in self._visible_main_paths()],
                'startup_evidence_locations': self._startup_evidence_locations(),
                'context_policy': 'Minimal startup context for helper coverage checks. Use navigation tools to expand local groups, selectors, Bangumi evidence, or recipe state.',
            },
        }

    def _local_recipe_skeleton_payload(self) -> dict[str, Any]:
        if self.local_recipe_skeleton_cache is None:
            self.local_recipe_skeleton_cache = _local_recipe_skeleton(list(self.workspace.local_files))
        return self.local_recipe_skeleton_cache

    def _local_recipe_params_scaffold_payload(self) -> dict[str, Any]:
        if self.local_recipe_params_scaffold_cache is None:
            self.local_recipe_params_scaffold_cache = _local_recipe_params_scaffold(self._local_recipe_skeleton_payload())
        return self.local_recipe_params_scaffold_cache

    def _startup_evidence_locations(self) -> dict[str, str]:
        return {
            'case_overview': 'case_input.case_overview or get_case_overview()',
            'local_group_index': 'get_case_overview().local_group_index or list_local_groups(detail=false)',
            'local_group_detail': 'get_local_group_detail(group_ref, detail=false/true)',
            'local_selector_scaffold': 'get_local_selector_scaffold(group_ref) or get_local_recipe_params_scaffold(group_ref)',
            'recipe_state': 'get_recipe_state(detail=false/true)',
            'bangumi_subjects_seen': 'get_case_overview().bangumi_seen; use lookup/search/graph tools to expand evidence',
            'bangumi_episode_window': 'get_target_window(subject_id, sort_start, sort_end) or get_episode_list(subject_id)',
            'case_board_notes': 'get_case_board_notes(mode="tail") or append_case_board_note(section_type, content, next_action) for Initial Board / Board Delta; params tools accept validation_snapshot, patch_delta, submit_snapshot transaction notes',
            'bangumi_anchor_bootstrap': 'select_bangumi_anchor_subject(anchor_subject_id, reason) after Pi chooses one reliable main anime/video anchor; it records the anchor and atomically builds the relation atlas',
            'bangumi_relation_atlas': 'build_bangumi_relation_atlas(anchor_subject_id) debug/manual fallback',
            'recipe_group_decisions': 'upsert_recipe_group_decision_one(decision, board_delta, summary) preferred; upsert_recipe_group_decision(decisions, remove_decision_names, board_delta, summary) small batch only; get_recipe_group_decisions(detail=false)',
            'recipe_params_draft': 'upsert_recipe_params_draft(rules, remove_rule_names, board_delta, summary), get_recipe_params_draft(detail=false), validate_recipe_params_draft(validation_snapshot)',
        }

    def _case_navigation_payload(self) -> dict[str, Any]:
        return {
            'navigation_policy': 'Fixed navigation handles only. They expose maps and pages, not semantic next-step recommendations.',
            'tools': {
                'overview': 'get_case_overview()',
                'local_group_index': 'list_local_groups(detail=false)',
                'local_group_detail': 'get_local_group_detail({"group_ref":"LG1","detail":false})',
                'local_group_file_facts': 'get_local_group_detail({"group_ref":"LG1","detail":true})',
                'selector_scaffold': 'get_local_selector_scaffold({"group_ref":"LG1"})',
                'recipe_state': 'get_recipe_state(detail=false)',
                'bangumi_subject_search': 'search_bangumi_subjects(query)',
                'bangumi_subject_detail': 'lookup_bangumi_subject(subject_ids)',
                'bangumi_relation_graph': 'expand_related_graph(subject_id or subject_ids)',
                'bangumi_anchor_bootstrap': 'select_bangumi_anchor_subject(anchor_subject_id, reason) records Pi anchor choice and writes a full anime/video relation atlas',
                'bangumi_relation_atlas': 'build_bangumi_relation_atlas(anchor_subject_id) debug/manual fallback',
                'bangumi_episode_window': 'get_target_window(subject_id, sort_start, sort_end) or get_episode_list(subject_id)',
                'case_board': 'append_case_board_note(section_type, content, next_action) for Initial Board / Board Delta; get_case_board_notes(mode="tail") for recovery',
                'recipe_group_decisions': 'upsert_recipe_group_decision_one(decision, board_delta, summary) preferred; upsert_recipe_group_decision(decisions, remove_decision_names, board_delta, summary) small batch only; get_recipe_group_decisions(detail=false), clear_recipe_group_decisions(reason)',
                'recipe_params_draft': 'upsert_recipe_params_draft(rules, remove_rule_names, board_delta, summary), get_recipe_params_draft(detail=false), clear_recipe_params_draft(reason)',
                'validate': 'validate_organize_recipe_params(recipe_params, validation_snapshot)',
                'repair_patch': 'validate_organize_recipe_params_patch(recipe_params_patch, patch_delta)',
                'submit': 'submit_organize_recipe_params(recipe_params, submit_snapshot) or submit_organize_recipe_params_patch(recipe_params_patch, submit_snapshot); same patch after accepted patch validation submits the accepted merged params instead of applying it twice',
            },
        }

    def _local_group_index_payload(self) -> dict[str, Any]:
        groups: list[dict[str, Any]] = []
        for group in self._local_recipe_skeleton_payload().get('groups') or []:
            if not isinstance(group, dict):
                continue
            number_summary = group.get('number_summary') if isinstance(group.get('number_summary'), dict) else {}
            duration_summary = group.get('duration_seconds') if isinstance(group.get('duration_seconds'), dict) else {}
            groups.append({
                'group_ref': group.get('group_ref'),
                'title_hint': group.get('title_hint'),
                'group_kind_hint': group.get('group_kind_hint'),
                'locator_kind_hint': group.get('locator_kind_hint'),
                'folder': group.get('folder'),
                'source_path_count': group.get('source_path_count'),
                'number_ranges': number_summary.get('integer_ranges') or [],
                'number_unique_count': number_summary.get('unique_count'),
                'duration_seconds': {
                    'min_seconds': duration_summary.get('min_seconds'),
                    'max_seconds': duration_summary.get('max_seconds'),
                    'median_seconds': duration_summary.get('median_seconds'),
                },
                'representative_source_path': group.get('representative_source_path'),
                'boundary_warnings': group.get('boundary_warnings') or [],
                'expand_handles': {
                    'detail': f'get_local_group_detail({group.get("group_ref")})',
                    'selector_scaffold': f'get_local_selector_scaffold({group.get("group_ref")})',
                },
            })
        return {
            'visible_file_count': self._local_recipe_skeleton_payload().get('visible_file_count', 0),
            'group_count': len(groups),
            'groups': groups,
            'index_policy': 'One compact card per local group. It does not choose subject_id, episode_id, media_kind, disposition, or supplemental status.',
        }

    def _case_overview_payload(self) -> dict[str, Any]:
        group_index = self._local_group_index_payload()
        subjects = [self._subject_payload(card, include_summary=False) for card in self.workspace.bangumi_subjects[:12]]
        episode_subject_ids: list[int] = []
        for item in self.workspace.bangumi_items:
            subject_id = self._subject_id_for_item(item)
            if subject_id and subject_id not in episode_subject_ids:
                episode_subject_ids.append(subject_id)
            if len(episode_subject_ids) >= 12:
                break
        return {
            'case_id': self.case_id,
            'case_type': 'local_to_bangumi_organize_recipe',
            'task_source_path': self.source_path,
            'visible_file_count': group_index['visible_file_count'],
            'local_group_count': group_index['group_count'],
            'run_progress': self._run_progress_payload(),
            'recipe_state': self._recipe_state_payload(detail=False),
            'bangumi_seen': {
                'subject_count': len(self.workspace.bangumi_subjects),
                'episode_count': len(self.workspace.bangumi_items),
                'subject_sample': subjects,
                'episode_subject_ids_sample': episode_subject_ids,
            },
            'navigation': self._case_navigation_payload(),
            'local_group_index': group_index['groups'],
            'overview_policy': 'Case map only. It exposes counts, compact group cards, state, and navigation handles; it does not recommend which group or target Pi should choose next.',
        }

    def _find_local_group_payload(self, group_ref: str) -> dict[str, Any] | None:
        wanted = str(group_ref or '').strip().casefold()
        if not wanted:
            return None
        for group in self._local_recipe_skeleton_payload().get('groups') or []:
            if isinstance(group, dict) and str(group.get('group_ref') or '').casefold() == wanted:
                return group
        return None

    def _find_scaffold_group_payload(self, group_ref: str) -> dict[str, Any] | None:
        wanted = str(group_ref or '').strip().casefold()
        if not wanted:
            return None
        for group in self._local_recipe_params_scaffold_payload().get('groups') or []:
            if isinstance(group, dict) and str(group.get('group_ref') or '').casefold() == wanted:
                return group
        return None

    def _recipe_selector_defaults_for_group_ref(self, group_ref: str, *, index: int) -> dict[str, Any]:
        group = self._find_local_group_payload(group_ref)
        if group is None:
            available = [
                str(row.get('group_ref') or '')
                for row in self._local_recipe_skeleton_payload().get('groups') or []
                if isinstance(row, dict) and str(row.get('group_ref') or '')
            ]
            raise ValueError(
                f'rules[{index - 1}] references unknown group_ref {group_ref!r}; '
                f'available group_ref values: {available}'
            )

        selector_hint = group.get('selector_hint') if isinstance(group.get('selector_hint'), dict) else {}
        coverage_preview = selector_hint.get('coverage_preview') if isinstance(selector_hint.get('coverage_preview'), dict) else {}
        source_paths = group.get('source_paths') if isinstance(group.get('source_paths'), dict) else {}
        source_path_count = int(group.get('source_path_count') or 0)
        source_pattern = str(selector_hint.get('source_pattern') or '')
        canonical_group_ref = str(group.get('group_ref') or group_ref).strip()
        defaults: dict[str, Any] = {'group_ref': group.get('group_ref')}

        if source_pattern and coverage_preview.get('safe') is True and source_path_count > 1:
            defaults['source_pattern'] = source_pattern
            number_summary = group.get('number_summary') if isinstance(group.get('number_summary'), dict) else {}
            integer_ranges = [str(value) for value in (number_summary.get('integer_ranges') or []) if str(value)]
            if integer_ranges:
                defaults['episode_range'] = integer_ranges[0]
                defaults['episode_offset'] = 'EP'
                defaults['episode_number_field'] = 'sort'
            return defaults

        representative = str(group.get('representative_source_path') or '')
        if source_path_count == 1 and representative:
            defaults['exact_paths'] = [representative]
            return defaults

        exact_paths = [
            str(path)
            for path in (source_paths.get('all') or self._local_group_paths_by_ref().get(canonical_group_ref, []))
            if str(path)
        ]
        if exact_paths:
            defaults['exact_paths'] = exact_paths
            return defaults

        sample = [str(path) for path in (source_paths.get('sample') or []) if str(path)]
        raise ValueError(
            f'rules[{index - 1}] group_ref {group_ref!r} cannot be expanded into a coverage-safe selector; '
            f'open get_local_group_detail({group_ref!r}, detail=true) and use exact_paths or split the group. '
            f'source_path sample: {sample[:8]}'
        )

    def _recipe_state_payload(self, *, detail: bool = False) -> dict[str, Any]:
        verifier = self.recipe_verifier_result.model_dump(mode='json') if self.recipe_verifier_result is not None else None
        issues = list(verifier.get('issues') or []) if isinstance(verifier, dict) else []
        review_warnings = list(verifier.get('review_warnings') or []) if isinstance(verifier, dict) else []
        draft_state = self._recipe_params_draft_state_payload(detail=False)
        draft_coverage = draft_state.get('coverage_preview') if isinstance(draft_state.get('coverage_preview'), dict) else {}
        decision_state = self._recipe_group_decisions_state_payload(detail=False)
        atlas_state = self._relation_atlas_state_payload()
        payload: dict[str, Any] = {
            'params_validation_seen': self.latest_recipe_params_payload is not None or self.recipe_verifier_result is not None,
            'recipe_artifact_available': self.organize_recipe is not None,
            'compiled_plan_available': self.compiled_plan is not None,
            'final_result_available': self.final_result is not None,
            'latest_verifier_summary': verifier.get('summary') if isinstance(verifier, dict) else '',
            'latest_verifier_passed': verifier.get('passed') if isinstance(verifier, dict) else None,
            'verifier_issue_count': len(issues),
            'review_warning_count': len(review_warnings),
            'verifier_issue_codes': [str(issue.get('issue_code') or '') for issue in issues[:12] if isinstance(issue, dict)],
            'review_warning_codes': [str(warning.get('code') or '') for warning in review_warnings[:12] if isinstance(warning, dict)],
            'latest_params_rule_count': len(self.latest_recipe_params_payload.get('rules') or []) if isinstance(self.latest_recipe_params_payload, dict) else 0,
            'recipe_group_decision_count': int(decision_state.get('decision_count') or 0),
            'recipe_params_draft_exists': bool(draft_state.get('exists')),
            'recipe_params_draft_rule_count': int(draft_state.get('rule_count') or 0),
            'recipe_params_draft_ready_for_full_validation': bool(draft_state.get('ready_for_full_validation')),
            'recipe_params_draft_covered_group_refs': draft_coverage.get('covered_group_refs') or [],
            'recipe_params_draft_missing_group_refs': draft_coverage.get('missing_group_refs') or [],
            'latest_patch_available': isinstance(self.latest_recipe_params_patch_payload, dict),
            'latest_patch_validation_accepted': bool(self.latest_recipe_params_patch_accepted),
            'submit_rejection_count': self.submit_rejection_count,
            'state_policy': 'Recipe state only. It summarizes verifier/params/final artifacts and does not choose semantic repairs.',
        }
        if detail:
            payload.update({
                'latest_recipe_params': _json_safe(self.latest_recipe_params_payload),
                'latest_recipe_group_decisions': self._recipe_group_decisions_state_payload(detail=True),
                'latest_recipe_params_draft': self._recipe_params_draft_state_payload(detail=True),
                'latest_recipe_params_patch': _json_safe(self.latest_recipe_params_patch_payload),
                'verifier_result': verifier,
                'organize_recipe': self.organize_recipe.model_dump(mode='json') if self.organize_recipe is not None else None,
                'compiled_plan': self.compiled_plan.model_dump(mode='json') if self.compiled_plan is not None else None,
                'final_result': _json_safe(self.final_result),
            })
        return payload

    def _evidence_batch_checkpoint_for_tool(self, tool_name: str) -> dict[str, Any] | None:
        if self.final_result is not None or self.recipe_verifier_result is not None:
            return None
        if tool_name not in self._EVIDENCE_BATCH_TOOLS:
            return None
        evidence_count = 0
        recent_evidence: list[str] = []
        for row in reversed(self.tool_trace):
            existing_tool = str(row.get('tool') or '')
            if existing_tool in self._WORKPAPER_RESET_TOOLS:
                break
            if existing_tool in self._EVIDENCE_BATCH_TOOLS:
                evidence_count += 1
                if len(recent_evidence) < 8:
                    recent_evidence.append(existing_tool)
        if evidence_count < self._EVIDENCE_BATCH_LIMIT:
            return None
        draft_state = self._recipe_params_draft_state_payload(detail=False)
        coverage = draft_state.get('coverage_preview') if isinstance(draft_state.get('coverage_preview'), dict) else {}
        return {
            'ok': False,
            'error': 'evidence_batch_checkpoint_required',
            'workpaper_checkpoint': {
                'next_tool': 'upsert_recipe_group_decision_one',
                'next_tools': [
                    'upsert_recipe_group_decision_one',
                    'upsert_recipe_group_decision',
                    'build_bangumi_relation_atlas',
                    'append_case_board_note',
                    'get_recipe_group_decisions',
                    'get_recipe_params_draft',
                    'validate_recipe_params_draft',
                ],
                'evidence_calls_since_workpaper': evidence_count,
                'limit': self._EVIDENCE_BATCH_LIMIT,
                'attempted_tool': tool_name,
                'recent_evidence_tools': list(reversed(recent_evidence)),
                'draft_rule_count': int(draft_state.get('rule_count') or 0),
                'missing_group_refs': (coverage.get('missing_group_refs') or [])[:12],
                'policy': (
                    'Non-semantic workpaper checkpoint. Python is not choosing a Bangumi target; it is requiring an action-sized work product before more evidence. '
                    'Pi can save one stable mapped/supplemental group decision, '
                    'build a full relation atlas from the reliable main anchor, fetch one targeted subject/episode fact, or append a Board Delta naming the exact blocker.'
                ),
            },
            'repair_hints': [
                'If any group/subcluster is already judged, call upsert_recipe_group_decision_one with that compact row.',
                'If the next move would be broad side-title searching across multiple unresolved frontiers, build_bangumi_relation_atlas from the reliable anchor first, then synthesize the strict atlas directly.',
                'If no row is stable yet, call append_case_board_note(section_type="Board Delta", content=...) naming the blocker and next targeted evidence.',
                'Then continue with only the named targeted evidence or validate the draft if coverage is complete.',
            ],
        }

    def handle_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        started = time.time()
        args = dict(arguments or {})
        trace_row = {
            'index': len(self.tool_trace) + 1,
            'tool': str(name or ''),
            'arguments': _json_safe(args),
            'started_at': started,
        }
        try:
            handler = getattr(self, f'tool_{name}', None)
            if handler is None:
                result = {'ok': False, 'error': f'unknown tool: {name}'}
            else:
                checkpoint = self._evidence_batch_checkpoint_for_tool(str(name or ''))
                if checkpoint is not None:
                    result = checkpoint
                else:
                    result = handler(**args)
                    post_checkpoint = self._evidence_batch_checkpoint_for_tool(str(name or ''))
                    if post_checkpoint is not None and isinstance(result, dict) and result.get('ok'):
                        result['workpaper_advisory'] = post_checkpoint.get('workpaper_checkpoint')
                        existing_hints = list(result.get('repair_hints') or []) if isinstance(result.get('repair_hints'), list) else []
                        result['repair_hints'] = _dedupe_nonempty([*existing_hints, *(post_checkpoint.get('repair_hints') or [])])
        except Exception as exc:
            result = {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}
        if isinstance(result, dict) and result.get('ok'):
            atlas_next_action = self._anchor_atlas_next_action_for_tool(str(name or ''), result)
            if atlas_next_action and 'anchor_atlas_next_action' not in result:
                result['anchor_atlas_next_action'] = atlas_next_action
            draft_next_action = self._recipe_params_draft_next_action_for_tool(str(name or ''))
            if draft_next_action and 'recipe_params_draft_next_action' not in result:
                result['recipe_params_draft_next_action'] = draft_next_action
        trace_row['elapsed_ms'] = int((time.time() - started) * 1000)
        trace_row['ok'] = bool(result.get('ok')) if isinstance(result, dict) else False
        trace_row['result_summary'] = self._compact_result_summary(result)
        self.tool_trace.append(trace_row)
        with (self.run_dir / 'tool_trace.jsonl').open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(_json_safe(trace_row), ensure_ascii=False, sort_keys=True))
            fh.write('\n')
        return _json_safe(result)

    def _run_progress_payload(self) -> dict[str, Any]:
        tool_names = [str(row.get('tool') or '') for row in self.tool_trace]
        draft_state = self._recipe_params_draft_state_payload(detail=False)
        draft_coverage = draft_state.get('coverage_preview') if isinstance(draft_state.get('coverage_preview'), dict) else {}
        decision_state = self._recipe_group_decisions_state_payload(detail=False)
        atlas_state = self._relation_atlas_state_payload()
        params_validation_tools = {
            'validate_organize_recipe_params',
            'validate_organize_recipe_params_patch',
            'submit_organize_recipe_params',
            'submit_organize_recipe_params_patch',
            'validate_recipe_params_draft',
        }
        draft_tools = {
            'upsert_recipe_group_decision_one',
            'upsert_recipe_group_decision',
            'get_recipe_group_decisions',
            'clear_recipe_group_decisions',
            'upsert_recipe_params_draft',
            'get_recipe_params_draft',
            'clear_recipe_params_draft',
            'validate_recipe_params_draft',
        }
        subject_evidence_tools = {
            'search_bangumi_subjects',
            'lookup_bangumi_subject',
            'expand_related_subjects',
            'expand_related_graph',
            'select_bangumi_anchor_subject',
            'build_bangumi_relation_atlas',
            'find_bangumi_targets_for_local_file',
        }
        episode_evidence_tools = {'get_episode_list', 'get_target_window', 'get_target_detail'}
        return {
            'tool_call_count': len(tool_names),
            'params_validation_seen': any(name in params_validation_tools for name in tool_names),
            'verifier_feedback_available': self.recipe_verifier_result is not None,
            'recipe_artifact_available': self.organize_recipe is not None,
            'recipe_params_draft': {
                'exists': bool(draft_state.get('exists')),
                'rule_count': int(draft_state.get('rule_count') or 0),
                'ready_for_full_validation': bool(draft_state.get('ready_for_full_validation')),
                'missing_group_refs': draft_coverage.get('missing_group_refs', []),
            },
            'recipe_group_decisions': {
                'exists': bool(decision_state.get('exists')),
                'decision_count': int(decision_state.get('decision_count') or 0),
            },
            'bangumi_relation_atlas': {
                'atlas_count': int(atlas_state.get('atlas_count') or 0),
                'latest_atlas_ids': atlas_state.get('latest_atlas_ids') or [],
            },
            'draft_tool_call_count': sum(1 for name in tool_names if name in draft_tools),
            'subject_evidence_call_count': sum(1 for name in tool_names if name in subject_evidence_tools),
            'episode_evidence_call_count': sum(1 for name in tool_names if name in episode_evidence_tools),
            'recent_tool_names': tool_names[-8:],
            'note': 'Progress facts only; these counts are not target recommendations or a next-step instruction.',
        }

    def _anchor_atlas_next_action_for_tool(self, tool_name: str, result: dict[str, Any]) -> dict[str, Any] | None:
        if self.final_result is not None or self.recipe_verifier_result is not None:
            return None
        if tool_name in {
            'select_bangumi_anchor_subject',
            'build_bangumi_relation_atlas',
            'upsert_recipe_group_decision_one',
            'upsert_recipe_group_decision',
            'upsert_recipe_params_draft',
            'validate_recipe_params_draft',
            'validate_organize_recipe_params',
            'validate_organize_recipe_params_patch',
            'submit_organize_recipe_params',
            'submit_organize_recipe_params_patch',
            'fail_closed',
        }:
            return None
        if not self._local_case_needs_anchor_atlas():
            return None
        atlas_state = self._relation_atlas_state_payload()
        if int(atlas_state.get('atlas_count') or 0) > 0:
            return None
        evidence_tools = {
            'search_bangumi_subjects',
            'lookup_bangumi_subject',
            'expand_related_subjects',
            'expand_related_graph',
            'find_bangumi_targets_for_local_file',
            'get_episode_list',
            'get_target_window',
            'get_target_detail',
            'get_local_group_detail',
        }
        if tool_name not in evidence_tools:
            return None
        candidate_subject_ids: list[int] = []
        for subject in result.get('subjects') or []:
            if not isinstance(subject, dict):
                continue
            if str(subject.get('subject_type') or '').casefold() == 'anime':
                subject_id = int(subject.get('subject_id') or 0)
                if subject_id > 0 and subject_id not in candidate_subject_ids:
                    candidate_subject_ids.append(subject_id)
        for subject in result.get('relation_subjects') or []:
            if not isinstance(subject, dict):
                continue
            if str(subject.get('subject_type') or '').casefold() == 'anime':
                subject_id = int(subject.get('subject_id') or 0)
                if subject_id > 0 and subject_id not in candidate_subject_ids:
                    candidate_subject_ids.append(subject_id)
        subject_id = int(result.get('subject_id') or 0)
        if subject_id > 0 and subject_id not in candidate_subject_ids:
            candidate_subject_ids.append(subject_id)
        return {
            'next_tool': 'select_bangumi_anchor_subject',
            'candidate_subject_ids_from_current_facts': candidate_subject_ids[:8],
            'reason': (
                'This local package has multiple franchise/side-content-shaped groups and no Bangumi relation atlas yet. '
                'A human workflow records one reliable main anchor and builds the full anime/video relation atlas before more per-visible-title broad searches.'
            ),
            'policy': (
                'Advisory only. Python is not choosing the anchor or target; Pi chooses a reliable anime/video subject from exposed facts, '
                'then calls select_bangumi_anchor_subject(anchor_subject_id, reason) to atomically record the anchor and build the atlas.'
            ),
        }

    def _local_case_needs_anchor_atlas(self) -> bool:
        groups = [
            group for group in (self._local_group_index_payload().get('groups') or [])
            if isinstance(group, dict)
        ]
        if len(groups) >= 4:
            return True
        for group in groups:
            shape_counts = group.get('content_shape_counts') if isinstance(group.get('content_shape_counts'), dict) else {}
            kind = str(group.get('group_kind_hint') or '').casefold()
            locator_kind = str(group.get('locator_kind') or '').casefold()
            title_hint = str(group.get('title_hint') or '').casefold()
            if kind in {'special_or_bonus_candidate', 'exact_or_standalone_candidate'}:
                return True
            if locator_kind in {'special', 'ova', 'oav', 'oad', 'ona', 'movie'}:
                return True
            if any(token in shape_counts for token in ('SP', 'SPECIAL', 'OVA', 'OAD', 'MOVIE', 'FILM', 'RECAP')):
                return True
            if any(token in title_hint for token in (' movie', 'ova', 'oad', 'special', 'recap', 'gekijou', 'eiga')):
                return True
        return False

    def _recipe_params_draft_next_action_for_tool(self, tool_name: str) -> dict[str, Any] | None:
        if self.final_result is not None or self.recipe_verifier_result is not None:
            return None
        draft_tools = {
            'upsert_recipe_group_decision_one',
            'upsert_recipe_group_decision',
            'get_recipe_group_decisions',
            'clear_recipe_group_decisions',
            'upsert_recipe_params_draft',
            'get_recipe_params_draft',
            'clear_recipe_params_draft',
            'validate_recipe_params_draft',
            'validate_organize_recipe_params',
            'validate_organize_recipe_params_patch',
            'submit_organize_recipe_params',
            'submit_organize_recipe_params_patch',
        }
        if tool_name in draft_tools:
            return None
        progress_tools = {
            'search_bangumi_subjects',
            'lookup_bangumi_subject',
            'expand_related_subjects',
            'expand_related_graph',
            'build_bangumi_relation_atlas',
            'find_bangumi_targets_for_local_file',
            'get_episode_list',
            'get_target_window',
            'get_target_detail',
            'get_local_group_detail',
            'get_local_file_detail',
            'get_local_selector_scaffold',
            'get_local_recipe_params_scaffold',
        }
        if tool_name not in progress_tools:
            return None
        draft_state = self._recipe_params_draft_state_payload(detail=False)
        coverage = draft_state.get('coverage_preview') if isinstance(draft_state.get('coverage_preview'), dict) else {}
        rule_count = int(draft_state.get('rule_count') or 0)
        draft_quality_issues = coverage.get('draft_quality_issues') or []
        if rule_count > 0 and draft_quality_issues:
            return {
                'next_tool': 'upsert_recipe_group_decision_one',
                'reason': 'recipe_params_draft contains incomplete rows. Complete, replace, remove, or clear those judgments before relying on the draft. If the missing piece is semantic evidence, continue with one targeted graph/episode lookup rather than broad search.',
                'draft_quality_issue_count': len(draft_quality_issues),
                'affected_rule_names': _dedupe_nonempty([
                    str(issue.get('rule_name') or '')
                    for issue in draft_quality_issues
                    if isinstance(issue, dict)
                ])[:12],
            }
        if bool(draft_state.get('ready_for_full_validation')):
            return {
                'next_tool': 'validate_recipe_params_draft',
                'reason': 'recipe_params_draft already covers every visible local group. If the saved rows reflect your current semantic judgment, run full validation on Pi-owned draft rows.',
            }
        trace_tool_names = [str(row.get('tool') or '') for row in self.tool_trace]
        progress_call_count = sum(1 for existing in [*trace_tool_names, tool_name] if existing in progress_tools)
        if rule_count == 0 and progress_call_count >= 6:
            return {
                'next_tool': 'upsert_recipe_group_decision_one',
                'reason': (
                    'Evidence/local inspection has progressed but no group decisions or recipe_params_draft rules are saved. '
                    'When a group is stable, maps to a subject/row, exact files fit episode IDs, '
                    'or exact files are supplemental after closure, save that stable group/subcluster now. '
                    'Keep only genuinely unresolved side surfaces in targeted evidence.'
                ),
                'partial_draft_policy': (
                    'Partial workpaper rows are for stable judgments only. Do not write uncertain duplicate/supplemental rows just to make draft coverage grow. '
                    'Mechanical field doubts such as media_kind, episode_type, selector wording, or exact row type are usually cheaper to test through the compiled draft and verifier than by rereading skill text.'
                ),
            }
        missing_group_refs = coverage.get('missing_group_refs') or []
        uncovered_path_count = int(coverage.get('uncovered_path_count') or 0)
        if rule_count > 0 and (missing_group_refs or uncovered_path_count):
            return {
                'next_tool': 'upsert_recipe_params_draft',
                'reason': 'recipe_params_draft exists but still misses local coverage. Add only newly judged group/subcluster rows; if the missing group still needs a side-surface check, keep that evidence gap on the board.',
                'missing_group_refs': missing_group_refs[:12],
                'uncovered_path_count': uncovered_path_count,
                'uncovered_path_sample': (coverage.get('uncovered_path_sample') or [])[:6],
                'allowed_next_tools': ['upsert_recipe_params_draft', 'upsert_recipe_group_decision_one', 'upsert_recipe_group_decision', 'get_recipe_params_draft'],
                'draft_repair_policy': 'Use validate_recipe_params_draft only after coverage is complete and the rows reflect current semantic judgment. Selector fixes belong in the draft; unresolved semantic surface checks belong in targeted evidence.',
            }
        return None

    def tool_get_case_overview(self) -> dict[str, Any]:
        return {'ok': True, 'data': self._case_overview_payload()}

    def tool_list_local_groups(self, detail: bool = False) -> dict[str, Any]:
        if not detail:
            return {'ok': True, 'data': self._local_group_index_payload()}
        return {
            'ok': True,
            'data': {
                'visible_file_count': self._local_recipe_skeleton_payload().get('visible_file_count', 0),
                'group_count': self._local_recipe_skeleton_payload().get('group_count', 0),
                'groups': self._local_recipe_skeleton_payload().get('groups') or [],
                'skeleton_policy': self._local_recipe_skeleton_payload().get('skeleton_policy'),
                'list_policy': 'Detailed local group facts only. No Bangumi target or disposition is selected here.',
            },
        }

    def tool_get_local_group_detail(self, group_ref: str, detail: bool = False) -> dict[str, Any]:
        group = self._find_local_group_payload(group_ref)
        if group is None:
            return {
                'ok': False,
                'error': f'unknown group_ref: {group_ref}',
                'available_group_refs': [
                    str(row.get('group_ref') or '')
                    for row in self._local_recipe_skeleton_payload().get('groups') or []
                    if isinstance(row, dict)
                ],
            }
        payload = _json_clone(group)
        paths_payload = payload.get('source_paths') if isinstance(payload.get('source_paths'), dict) else {}
        full_paths = self._local_group_paths_by_ref().get(str(group.get('group_ref') or ''), [])
        if detail and full_paths:
            paths_payload = dict(paths_payload)
            paths_payload['all'] = full_paths
            paths_payload['count'] = len(full_paths)
            paths_payload.pop('omitted_count', None)
            payload['source_paths'] = paths_payload
        paths = full_paths if detail and full_paths else [str(path) for path in (paths_payload.get('all') or paths_payload.get('sample') or []) if str(path)]
        if detail and paths:
            local_by_path = self._local_card_by_path()
            payload['local_files'] = [
                self._local_file_payload(local_by_path[path], detail=True)
                for path in paths
                if path in local_by_path
            ]
        payload['detail_policy'] = 'Expanded local group facts only. Pi decides whether and how this group maps to Bangumi or supplemental disposition.'
        return {'ok': True, 'data': payload}

    def tool_get_case_context(self, detail: bool = False) -> dict[str, Any]:
        return {'ok': True, 'data': self._case_context_payload(detail=bool(detail))}

    def tool_get_recipe_state(self, detail: bool = False) -> dict[str, Any]:
        return {'ok': True, 'data': self._recipe_state_payload(detail=bool(detail))}

    def tool_append_case_board_note(
        self,
        section_type: str,
        content: Any,
        next_action: str = '',
    ) -> dict[str, Any]:
        return self._append_case_board_section(section_type=section_type, content=content, next_action=next_action)

    def _append_case_board_section(
        self,
        *,
        section_type: str,
        content: Any,
        next_action: str = '',
    ) -> dict[str, Any]:
        notes_path = self.run_dir / 'artifacts' / 'notes.md'
        notes_path.parent.mkdir(parents=True, exist_ok=True)
        title = re.sub(r'\s+', ' ', str(section_type or '')).strip() or 'Case Board Note'
        title = title.replace('#', '').strip() or 'Case Board Note'
        if len(title) > 80:
            title = title[:80].rstrip()
        if isinstance(content, str):
            body = content.strip()
        else:
            body = json.dumps(_json_safe(content), ensure_ascii=False, indent=2).strip()
        if not body:
            body = '(empty)'
        next_text = re.sub(r'\s+', ' ', str(next_action or '')).strip()
        body_chars = self._serialized_arg_chars(body)
        if body_chars > self._MAX_CASE_BOARD_CONTENT_CHARS:
            return {
                'ok': False,
                'error': 'case_board_note_too_large',
                'section_type': title,
                'content_chars': body_chars,
                'max_content_chars': self._MAX_CASE_BOARD_CONTENT_CHARS,
                'repair_hints': [
                    'Do not paste local group, atlas, evidence, recipe, or verifier JSON into notes.md.',
                    'Write compact board notes with group refs, target-surface facts, blockers, and the next action only.',
                    'Save recipe rows with upsert_recipe_group_decision_one, validate_recipe_params_draft, or patch tools instead of narrating full tables.',
                ],
                'board_policy': 'Append rejected before writing. Board is compact work memory, not a JSON transcript.',
            }
        existing = notes_path.read_text(encoding='utf-8') if notes_path.exists() else ''
        prefix = '' if not existing else ('\n' if existing.endswith('\n') else '\n\n')
        entry_lines = [f'## {title}', body]
        if next_text:
            entry_lines.append(f'Next: {next_text}')
        entry = prefix + '\n'.join(entry_lines).rstrip() + '\n'
        with notes_path.open('a', encoding='utf-8') as fh:
            fh.write(entry)
        total_chars = len(notes_path.read_text(encoding='utf-8'))
        board_next_action = self._case_board_note_next_action(title, next_text)
        return {
            'ok': True,
            'path': str(notes_path),
            'section_type': title,
            'next_action': next_text,
            'board_next_action': board_next_action,
            'content_chars': body_chars,
            'appended_chars': len(entry),
            'total_chars': total_chars,
            'board_policy': 'Append-only scratch I/O. Pi owns the board; Python does not interpret it as semantic state.',
        }

    def _serialized_arg_chars(self, value: Any) -> int:
        if isinstance(value, str):
            return len(value)
        try:
            return len(json.dumps(_json_safe(value), ensure_ascii=False))
        except Exception:
            return len(str(value))

    def _recipe_group_decision_shape_error(
        self,
        *,
        decisions: list[dict[str, Any]],
        summary: str = '',
        board_delta: Any = '',
    ) -> dict[str, Any] | None:
        if len(decisions) > self._MAX_GROUP_DECISIONS_PER_UPSERT:
            return {
                'ok': False,
                'error': 'too_many_group_decisions_for_one_call',
                'decision_count': len(decisions),
                'max_decisions_per_call': self._MAX_GROUP_DECISIONS_PER_UPSERT,
                'repair_hints': [
                    'Use upsert_recipe_group_decision_one for each stable group/subcluster row.',
                    'If several rows are already stable, save them over multiple short tool calls instead of one large JSON batch.',
                ],
                'decision_policy': 'Rejected before writing. Python is enforcing action-sized work products only; it is not judging semantic targets.',
            }
        if self._serialized_arg_chars(summary) > self._MAX_GROUP_DECISION_SUMMARY_CHARS:
            return {
                'ok': False,
                'error': 'group_decision_summary_too_long',
                'summary_chars': self._serialized_arg_chars(summary),
                'max_summary_chars': self._MAX_GROUP_DECISION_SUMMARY_CHARS,
                'repair_hints': ['Shorten summary to one compact audit sentence; put durable rows in decisions, not prose.'],
                'decision_policy': 'Rejected before writing. Summary length is an output-shape guard only.',
            }
        if board_delta not in (None, '', [], {}):
            board_chars = self._serialized_arg_chars(board_delta)
            if board_chars > self._MAX_CASE_BOARD_CONTENT_CHARS:
                return {
                    'ok': False,
                    'error': 'group_decision_board_delta_too_large',
                    'board_delta_chars': board_chars,
                    'max_board_delta_chars': self._MAX_CASE_BOARD_CONTENT_CHARS,
                    'repair_hints': [
                        'Keep board_delta compact: changed group refs, target-surface fact, blocker, next action.',
                        'Do not paste full local group, atlas, verifier, or recipe JSON into board_delta.',
                    ],
                    'decision_policy': 'Rejected before writing. Board delta size is an output-shape guard only.',
                }
        for index, decision in enumerate(decisions, start=1):
            plural_subject_keys = [
                key for key in ('target_subject_ids', 'subject_ids', 'bangumi_subject_ids')
                if key in decision and decision.get(key) not in (None, '', [])
            ]
            if plural_subject_keys:
                return {
                    'ok': False,
                    'error': 'group_decision_plural_subject_targets_not_supported',
                    'decision_index': index,
                    'decision_name': self._recipe_group_decision_name(decision, index=index),
                    'invalid_fields': plural_subject_keys,
                    'repair_hints': [
                        'A group decision row represents one target surface. Use subject_id for one subject.',
                        'For two movie subjects or mixed side surfaces, call upsert_recipe_group_decision_one once per subcluster with group_ref plus file_numbers/file_number_range/path_contains/exact_paths.',
                        'Do not use target_subject_ids, subject_ids, or bangumi_subject_ids inside a decision row.',
                    ],
                    'decision_policy': 'Rejected before writing. This is a schema/shape guard only; Python is not choosing which subject is correct.',
                }
            reason = str(decision.get('reason') or '').strip()
            if self._serialized_arg_chars(reason) > self._MAX_GROUP_DECISION_REASON_CHARS:
                return {
                    'ok': False,
                    'error': 'group_decision_reason_too_long',
                    'decision_index': index,
                    'decision_name': self._recipe_group_decision_name(decision, index=index),
                    'reason_chars': self._serialized_arg_chars(reason),
                    'max_reason_chars': self._MAX_GROUP_DECISION_REASON_CHARS,
                    'repair_hints': [
                        'Compress reason to one evidence sentence: local shape + Bangumi target surface + confidence/gap.',
                        'Keep detailed evidence in atlas and tool artifacts, not in the decision reason.',
                    ],
                    'decision_policy': 'Rejected before writing. Reason length is an output-shape guard only.',
                }
            exact_paths = _coerce_string_list(_first_present(decision, keys=('exact_paths',)))
            if len(exact_paths) > self._MAX_GROUP_DECISION_EXACT_PATHS:
                return {
                    'ok': False,
                    'error': 'group_decision_exact_paths_too_many',
                    'decision_index': index,
                    'decision_name': self._recipe_group_decision_name(decision, index=index),
                    'exact_path_count': len(exact_paths),
                    'max_exact_paths': self._MAX_GROUP_DECISION_EXACT_PATHS,
                    'repair_hints': [
                        'Use group_ref plus file_numbers, file_number_range, path_contains, or exclude filters for numbered subclusters.',
                        'Reserve exact_paths for a few standalone movie/OVA/SP/mixed-folder exceptions.',
                        'Call get_local_recipe_params_scaffold or get_local_selector_scaffold if you need the compact selector shape.',
                    ],
                    'decision_policy': 'Rejected before writing. Selector compactness is a local coverage/output-shape guard only; Python is not choosing targets.',
                }
        return None

    def _append_optional_case_board_section(
        self,
        *,
        section_type: str,
        content: Any,
        next_action: str = '',
    ) -> dict[str, Any] | None:
        if content is None:
            return None
        if isinstance(content, str) and not content.strip():
            return None
        if isinstance(content, (list, dict)) and not content:
            return None
        return self._append_case_board_section(section_type=section_type, content=content, next_action=next_action)

    def _append_verifier_delta_for_result(self, result: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(result, dict):
            return None
        status = str(result.get('status') or '').strip().casefold()
        if status not in {'invalid', 'review'}:
            return None
        next_action = result.get('case_board_next_action') if isinstance(result.get('case_board_next_action'), dict) else {}
        verifier = result.get('verifier_result') if isinstance(result.get('verifier_result'), dict) else {}
        issues = [
            {
                'issue_code': str(issue.get('issue_code') or ''),
                'ref': str(issue.get('ref') or ''),
                'message': str(issue.get('message') or ''),
                'related_refs': [str(ref) for ref in (issue.get('related_refs') or [])[:6] if str(ref)],
            }
            for issue in (verifier.get('issues') or [])[:12]
            if isinstance(issue, dict)
        ]
        warnings = [
            {
                'code': str(warning.get('code') or ''),
                'source_path': str(warning.get('source_path') or ''),
                'message': str(warning.get('message') or warning.get('summary') or ''),
                'repair_hint': str(warning.get('repair_hint') or ''),
            }
            for warning in (result.get('review_warnings') or [])[:12]
            if isinstance(warning, dict)
        ]
        content = {
            'status': status,
            'summary': str(result.get('summary') or verifier.get('summary') or ''),
            'verifier_passed': verifier.get('passed'),
            'issues': issues,
            'review_warnings': warnings,
            'repair_hints': self._compact_repair_hints(result.get('repair_hints'), limit=4),
            'next_tool': str(next_action.get('next_tool') or ''),
            'note': 'Mechanical verifier/review feedback only. Pi owns semantic target and supplemental decisions.',
        }
        return self._append_case_board_section(
            section_type='Verifier Delta',
            content=content,
            next_action=str(next_action.get('next_tool') or ''),
        )

    def _recipe_artifact_paths_payload(self) -> dict[str, str]:
        artifacts = self.run_dir / 'artifacts'
        return {
            'organize_recipe': str(artifacts / 'organize_recipe.json'),
            'compiled_plan': str(artifacts / 'compiled_plan.json'),
            'recipe_verifier_result': str(artifacts / 'recipe_verifier_result.json'),
            'recipe_params': str(artifacts / 'recipe_params.json'),
            'recipe_params_draft': str(artifacts / 'recipe_params_draft.json'),
            'notes': str(artifacts / 'notes.md'),
        }

    def _compact_repair_hints(self, hints: Any, *, limit: int = 4) -> list[str]:
        result: list[str] = []
        for hint in hints or []:
            text = str(hint or '').strip()
            if not text:
                continue
            first_sentence = re.split(r'(?<=[.!?])\s+', text, maxsplit=1)[0].strip()
            result.append(_compact_text(first_sentence or text, limit=260))
            if len(result) >= limit:
                break
        return _dedupe_nonempty(result)

    def _compact_draft_quality_items(self, items: Any, *, limit: int = 4) -> list[dict[str, Any]]:
        compact: list[dict[str, Any]] = []
        for item in (items or [])[:limit]:
            if not isinstance(item, dict):
                continue
            compact.append({
                'issue_code': str(item.get('issue_code') or ''),
                'rule_name': str(item.get('rule_name') or ''),
                'rule_index': item.get('rule_index'),
                'message': _compact_text(str(item.get('message') or ''), limit=220),
            })
        return compact

    def _compact_draft_coverage_payload(self, coverage: Any) -> dict[str, Any]:
        if not isinstance(coverage, dict):
            return {}
        draft_quality_issues = coverage.get('draft_quality_issues') or []
        draft_quality_warnings = coverage.get('draft_quality_warnings') or []
        return {
            'ready_for_full_validation': bool(coverage.get('ready_for_full_validation')),
            'visible_group_count': coverage.get('visible_group_count'),
            'covered_group_refs': coverage.get('covered_group_refs', []),
            'missing_group_refs': coverage.get('missing_group_refs', []),
            'visible_path_count': coverage.get('visible_path_count'),
            'covered_path_count': coverage.get('covered_path_count'),
            'uncovered_path_count': coverage.get('uncovered_path_count'),
            'uncovered_path_sample': (coverage.get('uncovered_path_sample') or [])[:6],
            'unmatched_exact_path_count': len(coverage.get('unmatched_exact_paths') or []),
            'draft_warning_count': len(coverage.get('draft_warnings') or []),
            'draft_quality_issue_count': len(draft_quality_issues),
            'draft_quality_warning_count': len(draft_quality_warnings),
            'local_coverage_complete': bool(coverage.get('local_coverage_complete')),
            'path_coverage_complete': bool(coverage.get('path_coverage_complete')),
            'draft_quality_issues': self._compact_draft_quality_items(draft_quality_issues, limit=4),
            'draft_quality_warnings': self._compact_draft_quality_items(draft_quality_warnings, limit=4),
            'coverage_policy': 'Local draft coverage only. It is not semantic validation.',
        }

    def _compact_verifier_result_payload(self, verifier: Any) -> dict[str, Any] | None:
        if not isinstance(verifier, dict):
            return None
        compact: dict[str, Any] = {
            'passed': verifier.get('passed'),
            'summary': _compact_text(str(verifier.get('summary') or ''), limit=220),
        }
        issues = []
        for issue in (verifier.get('issues') or [])[:12]:
            if not isinstance(issue, dict):
                continue
            issues.append({
                'issue_code': str(issue.get('issue_code') or ''),
                'severity': str(issue.get('severity') or ''),
                'ref': str(issue.get('ref') or ''),
                'message': _compact_text(str(issue.get('message') or ''), limit=220),
                'related_refs': [str(ref) for ref in (issue.get('related_refs') or [])[:4] if str(ref)],
            })
        compact['issues'] = issues
        compact['issue_count'] = len(verifier.get('issues') or [])
        return compact

    def _compact_review_warnings_payload(self, warnings: Any) -> list[dict[str, Any]]:
        compact: list[dict[str, Any]] = []
        for warning in (warnings or [])[:8]:
            if not isinstance(warning, dict):
                continue
            compact.append({
                'code': str(warning.get('code') or ''),
                'source_path': str(warning.get('source_path') or ''),
                'message': _compact_text(str(warning.get('message') or warning.get('summary') or ''), limit=220),
                'repair_hint': _compact_text(str(warning.get('repair_hint') or ''), limit=220),
            })
        return compact

    def _compact_params_tool_result(self, result: dict[str, Any], *, detail: bool = False) -> dict[str, Any]:
        if detail or not isinstance(result, dict):
            return result
        keys = [
            'ok',
            'accepted',
            'status',
            'error',
            'summary',
            'params_compiled',
            'params_patch_applied',
            'params_patch_reused_from_accepted_validation',
            'validated_from_recipe_params_draft',
            'finalizes_case',
            'submit_rejected',
            'expanded_assignment_count',
            'validation_role',
            'feedback_semantics',
            'next_tool',
        ]
        compact = {key: result.get(key) for key in keys if key in result}
        verifier = self._compact_verifier_result_payload(result.get('verifier_result'))
        if verifier is not None:
            compact['verifier_result'] = verifier
        compact['review_warnings'] = self._compact_review_warnings_payload(result.get('review_warnings'))
        compact['repair_hints'] = self._compact_repair_hints(result.get('repair_hints'), limit=4)
        if 'case_board_next_action' in result:
            compact['case_board_next_action'] = _json_safe(result.get('case_board_next_action'))
        if 'case_board_transaction' in result and isinstance(result.get('case_board_transaction'), dict):
            compact['case_board_transaction'] = {
                key: {
                    'section_type': note.get('section_type'),
                    'next_action': note.get('next_action'),
                    'path': note.get('path'),
                    'appended_chars': note.get('appended_chars'),
                    'total_chars': note.get('total_chars'),
                }
                for key, note in result['case_board_transaction'].items()
                if isinstance(note, dict)
            }
        if 'coverage_preview' in result and isinstance(result.get('coverage_preview'), dict):
            compact['coverage_preview'] = self._compact_draft_coverage_payload(result.get('coverage_preview'))
        if 'repair_mode' in result and isinstance(result.get('repair_mode'), dict):
            compact['repair_mode'] = _json_safe(result.get('repair_mode'))
        compact['artifact_paths'] = self._recipe_artifact_paths_payload()
        compact['detail_available'] = True
        compact['detail_hint'] = 'Pass detail:true to this params tool only when debugging full repair_hints/compiled_plan/organize_recipe output is necessary.'
        return compact

    def tool_get_case_board_notes(self, mode: str = 'tail', max_chars: int = 6000) -> dict[str, Any]:
        notes_path = self.run_dir / 'artifacts' / 'notes.md'
        try:
            limit = int(max_chars)
        except (TypeError, ValueError):
            limit = 6000
        limit = max(200, min(limit, 50000))
        if not notes_path.exists():
            return {
                'ok': True,
                'path': str(notes_path),
                'exists': False,
                'mode': str(mode or 'tail'),
                'content': '',
                'char_count': 0,
                'truncated': False,
                'board_policy': 'No notes.md yet. Use append_case_board_note for Initial Board or later board deltas.',
            }
        text = notes_path.read_text(encoding='utf-8')
        normalized_mode = str(mode or 'tail').strip().casefold()
        truncated = False
        if normalized_mode == 'all':
            content = text
            if len(content) > limit:
                content = content[-limit:]
                truncated = True
        elif normalized_mode == 'latest':
            matches = list(re.finditer(r'(?m)^##\s+', text))
            start = matches[-1].start() if matches else 0
            content = text[start:]
            if len(content) > limit:
                content = content[-limit:]
                truncated = True
        else:
            normalized_mode = 'tail'
            content = text[-limit:]
            truncated = len(text) > limit
        return {
            'ok': True,
            'path': str(notes_path),
            'exists': True,
            'mode': normalized_mode,
            'content': content,
            'char_count': len(text),
            'truncated': truncated,
            'board_policy': 'Read-only scratch recovery. Latest Validation Snapshot or Submit Snapshot is current board state; older deltas are audit.',
        }

    def tool_upsert_recipe_group_decision(
        self,
        decisions: Any = None,
        remove_decision_names: list[str] | None = None,
        board_delta: Any = '',
        summary: str = '',
        detail: bool = False,
    ) -> dict[str, Any]:
        existing = self._read_recipe_group_decisions_artifact() or {'version': 1, 'summary': '', 'decisions': []}
        existing_decisions = [dict(item) for item in (existing.get('decisions') or []) if isinstance(item, dict)]
        remove_names = set(_coerce_string_list(remove_decision_names))
        if remove_names:
            existing_decisions = [item for item in existing_decisions if str(item.get('name') or '') not in remove_names]

        normalized_decisions, error = self._normalize_recipe_group_decisions(decisions)
        if error:
            return {
                'ok': False,
                'error': error,
                'repair_hints': ['Pass decisions as an array of compact group/subcluster decision objects, each with a stable name or group_ref.'],
            }
        shape_error = self._recipe_group_decision_shape_error(
            decisions=normalized_decisions,
            summary=summary,
            board_delta=board_delta,
        )
        if shape_error is not None:
            return shape_error

        by_name: dict[str, dict[str, Any]] = {
            str(item.get('name') or ''): item
            for item in existing_decisions
            if str(item.get('name') or '')
        }
        order = [str(item.get('name') or '') for item in existing_decisions if str(item.get('name') or '')]
        for index, decision in enumerate(normalized_decisions, start=1):
            name = self._recipe_group_decision_name(decision, index=index)
            decision['name'] = name
            if name not in by_name:
                order.append(name)
            by_name[name] = _json_clone(decision)

        payload = {
            'version': int(existing.get('version') or 1),
            'summary': _string_or_default(summary, _string_or_default(existing.get('summary'), 'Pi group decision workpaper.')),
            'decisions': [by_name[name] for name in order if name in by_name],
        }
        self.latest_recipe_group_decisions_payload = payload
        self._write_recipe_group_decisions_artifact()
        self.latest_recipe_params_draft_payload = self._recipe_params_draft_from_group_decisions(payload)
        self._write_recipe_params_draft_artifact()
        board_note = self._append_optional_case_board_section(
            section_type='Board Delta',
            content=board_delta,
            next_action='update group decisions and recipe params draft',
        )
        state = self._recipe_group_decisions_state_payload(detail=bool(detail))
        state.update({
            'ok': True,
            'group_decisions_updated': True,
            'removed_decision_names': sorted(remove_names),
            'upserted_decision_names': [self._recipe_group_decision_name(item, index=index) for index, item in enumerate(normalized_decisions, start=1)],
            'board_delta': board_note,
            'decision_policy': 'Pi-owned semantic decisions only. Python compiled local selectors and Pi-provided targets/dispositions into recipe_params_draft; it did not choose Bangumi targets or supplemental status.',
        })
        return state

    def tool_upsert_recipe_group_decision_one(
        self,
        decision: Any = None,
        board_delta: Any = '',
        summary: str = '',
        detail: bool = False,
    ) -> dict[str, Any]:
        normalized_decisions, error = self._normalize_recipe_group_decisions(decision)
        if error:
            return {
                'ok': False,
                'error': error,
                'repair_hints': ['Pass decision as one compact group/subcluster decision object.'],
            }
        if len(normalized_decisions) != 1:
            return {
                'ok': False,
                'error': 'upsert_recipe_group_decision_one_requires_exactly_one_decision',
                'decision_count': len(normalized_decisions),
                'repair_hints': [
                    'Pass exactly one decision object to decision.',
                    'Use separate upsert_recipe_group_decision_one calls for separate groups/subclusters.',
                ],
                'decision_policy': 'Rejected before writing. One-row tool preserves action-sized work products only.',
            }
        result = self.tool_upsert_recipe_group_decision(
            decisions=normalized_decisions,
            remove_decision_names=None,
            board_delta=board_delta,
            summary=summary,
            detail=detail,
        )
        if isinstance(result, dict):
            result['one_row_decision_tool_used'] = True
            result['decision_policy'] = (
                'Pi-owned one-row semantic decision saved. Python compiled local selectors and Pi-provided '
                'targets/dispositions into recipe_params_draft; it did not choose Bangumi targets or supplemental status.'
            )
        return result

    def tool_get_recipe_group_decisions(self, detail: bool = False) -> dict[str, Any]:
        payload = self._recipe_group_decisions_state_payload(detail=bool(detail))
        payload['ok'] = True
        return payload

    def tool_clear_recipe_group_decisions(self, reason: str = '') -> dict[str, Any]:
        decisions_path = self._recipe_group_decisions_path()
        draft_path = self._recipe_params_draft_path()
        if decisions_path.exists():
            decisions_path.unlink()
        if draft_path.exists():
            draft_path.unlink()
        self.latest_recipe_group_decisions_payload = None
        self.latest_recipe_params_draft_payload = None
        board_note = self._append_optional_case_board_section(
            section_type='Board Delta',
            content={'cleared_recipe_group_decisions': True, 'reason': str(reason or '')},
            next_action='rebuild group decisions',
        )
        return {
            'ok': True,
            'cleared': True,
            'path': str(decisions_path),
            'recipe_params_draft_path': str(draft_path),
            'board_delta': board_note,
            'decision_policy': 'Group decisions and generated draft cleared only; no verifier or semantic decision was run.',
        }

    def tool_upsert_recipe_params_draft(
        self,
        rules: Any = None,
        remove_rule_names: list[str] | None = None,
        board_delta: Any = '',
        summary: str = '',
        detail: bool = False,
    ) -> dict[str, Any]:
        existing = self._read_recipe_params_draft_artifact() or {'version': 1, 'summary': '', 'rules': []}
        existing_rules = [dict(rule) for rule in (existing.get('rules') or []) if isinstance(rule, dict)]
        remove_names = set(_coerce_string_list(remove_rule_names))
        if remove_names:
            existing_rules = [rule for rule in existing_rules if str(rule.get('name') or '') not in remove_names]

        normalized_rules, error = self._normalize_recipe_params_draft_rules(rules)
        if error:
            return {'ok': False, 'error': error, 'repair_hints': ['Pass rules as an array of compact recipe_params rule objects, each with a stable name.']}

        by_name: dict[str, dict[str, Any]] = {str(rule.get('name') or ''): rule for rule in existing_rules if str(rule.get('name') or '')}
        order = [str(rule.get('name') or '') for rule in existing_rules if str(rule.get('name') or '')]
        for rule in normalized_rules:
            name = str(rule.get('name') or '').strip()
            if not name:
                return {'ok': False, 'error': 'draft rules must include a stable name', 'repair_hints': ['Add name to every draft rule so later upserts can replace the intended row.']}
            if name not in by_name:
                order.append(name)
            by_name[name] = _json_clone(rule)

        draft = {
            'version': int(existing.get('version') or 1),
            'summary': _string_or_default(summary, _string_or_default(existing.get('summary'), 'Pi incremental recipe params draft.')),
            'rules': [by_name[name] for name in order if name in by_name],
        }
        self.latest_recipe_params_draft_payload = draft
        self._write_recipe_params_draft_artifact()
        board_note = self._append_optional_case_board_section(
            section_type='Board Delta',
            content=board_delta,
            next_action='update recipe params draft',
        )
        payload = self._recipe_params_draft_state_payload(detail=bool(detail))
        payload.update({
            'ok': True,
            'draft_updated': True,
            'removed_rule_names': sorted(remove_names),
            'upserted_rule_names': [str(rule.get('name') or '') for rule in normalized_rules],
            'board_delta': board_note,
            'draft_policy': 'Pi-owned working draft only. Python saved rules and previewed local coverage; it did not decide Bangumi targets or run verifier.',
        })
        return payload

    def tool_get_recipe_params_draft(self, detail: bool = False) -> dict[str, Any]:
        payload = self._recipe_params_draft_state_payload(detail=bool(detail))
        payload['ok'] = True
        return payload

    def tool_clear_recipe_params_draft(self, reason: str = '') -> dict[str, Any]:
        path = self._recipe_params_draft_path()
        if path.exists():
            path.unlink()
        self.latest_recipe_params_draft_payload = None
        board_note = self._append_optional_case_board_section(
            section_type='Board Delta',
            content={'cleared_recipe_params_draft': True, 'reason': str(reason or '')},
            next_action='rebuild recipe params draft',
        )
        return {
            'ok': True,
            'cleared': True,
            'path': str(path),
            'board_delta': board_note,
            'draft_policy': 'Draft cleared only; no verifier or semantic decision was run.',
        }

    def tool_validate_recipe_params_draft(self, validation_snapshot: Any = '', detail: bool = False) -> dict[str, Any]:
        draft = self._read_recipe_params_draft_artifact()
        coverage = self._recipe_params_draft_coverage(draft)
        if not isinstance(draft, dict) or not isinstance(draft.get('rules'), list) or not draft.get('rules'):
            return {
                'ok': False,
                'accepted': False,
                'status': 'draft_incomplete',
                'error': 'recipe_params_draft is empty; upsert draft rules before validation',
                'coverage_preview': coverage if detail else self._compact_draft_coverage_payload(coverage),
                'repair_hints': ['Use upsert_recipe_params_draft for each group that has a testable mapped or evidence-gap supplemental rule.'],
            }
        draft_quality_issues = coverage.get('draft_quality_issues') or []
        if draft_quality_issues:
            return {
                'ok': False,
                'accepted': False,
                'status': 'draft_quality_incomplete',
                'error': 'recipe_params_draft contains incomplete or non-testable rows; full verifier was not run',
                'coverage_preview': coverage if detail else self._compact_draft_coverage_payload(coverage),
                'draft_quality_issues': draft_quality_issues if detail else self._compact_draft_quality_items(draft_quality_issues, limit=4),
                'missing_group_refs': coverage.get('missing_group_refs', []),
                'repair_hints': [
                    'Complete, replace, remove, or clear the named draft rows before validating.',
                    'A saved draft row needs a local selector plus either a Bangumi target or disposition: "non_bangumi_or_supplemental".',
                    'This is contract hygiene only; Python is not judging whether the Bangumi target is semantically correct.',
                ],
            }
        if not coverage.get('ready_for_full_validation'):
            return {
                'ok': False,
                'accepted': False,
                'status': 'draft_incomplete',
                'error': 'recipe_params_draft does not cover every visible local group; full verifier was not run',
                'coverage_preview': coverage if detail else self._compact_draft_coverage_payload(coverage),
                'missing_group_refs': coverage.get('missing_group_refs', []),
                'uncovered_path_count': coverage.get('uncovered_path_count', 0),
                'uncovered_path_sample': (coverage.get('uncovered_path_sample') or [])[:12 if detail else 6],
                'repair_hints': [
                    'Add mapped or evidence-gap supplemental draft rules for missing_group_refs or uncovered_path_sample, then call validate_recipe_params_draft again.',
                    'This is not partial validation; Python is only reporting draft coverage gaps.',
                ],
            }
        snapshot = validation_snapshot or self._default_recipe_params_draft_validation_snapshot(draft, coverage)
        result = self.tool_validate_organize_recipe_params(
            recipe_params=_canonical_recipe_params_payload_for_validation(draft),
            validation_snapshot=snapshot,
            detail=detail,
        )
        result['validated_from_recipe_params_draft'] = True
        result['coverage_preview'] = coverage if detail else self._compact_draft_coverage_payload(coverage)
        return result

    def tool_get_local_selector_scaffold(self, group_ref: str = '', detail: bool = False) -> dict[str, Any]:
        if group_ref:
            group = self._find_scaffold_group_payload(group_ref)
            if group is None:
                return {
                    'ok': False,
                    'error': f'unknown group_ref: {group_ref}',
                    'available_group_refs': [
                        str(row.get('group_ref') or '')
                        for row in self._local_recipe_params_scaffold_payload().get('groups') or []
                        if isinstance(row, dict)
                    ],
                }
            return {
                'ok': True,
                'data': {
                    'group': _json_clone(group),
                    'scaffold_policy': self._local_recipe_params_scaffold_payload().get('scaffold_policy'),
                    'usage_hint': self._local_recipe_params_scaffold_payload().get('usage_hint'),
                },
            }
        return self.tool_get_local_recipe_params_scaffold(detail=detail)

    def tool_get_local_recipe_params_scaffold(self, detail: bool = False, group_ref: str = '') -> dict[str, Any]:
        if group_ref:
            return self.tool_get_local_selector_scaffold(group_ref=group_ref, detail=detail)
        payload = self._local_recipe_params_scaffold_payload()
        if detail:
            return {'ok': True, 'data': payload}
        compact_groups = []
        for group in payload.get('groups') or []:
            if not isinstance(group, dict):
                continue
            compact_groups.append({
                'group_ref': group.get('group_ref'),
                'group_kind_hint': group.get('group_kind_hint'),
                'title_hint': group.get('title_hint'),
                'source_path_count': group.get('source_path_count'),
                'representative_source_path': group.get('representative_source_path'),
                'number_summary': group.get('number_summary'),
                'selector_coverage_safe': group.get('selector_coverage_safe'),
                'params_rule_stub': group.get('params_rule_stub'),
                'target_fields_for_mapped_rule': group.get('target_fields_for_mapped_rule'),
                'supplemental_fields_if_evidence_does_not_support_mapping': group.get('supplemental_fields_if_evidence_does_not_support_mapping'),
            })
        return {
            'ok': True,
            'data': {
                'visible_file_count': payload.get('visible_file_count', 0),
                'group_count': len(compact_groups),
                'groups': compact_groups,
                'scaffold_policy': payload.get('scaffold_policy'),
                'usage_hint': payload.get('usage_hint'),
            },
        }

    def tool_search_bangumi_subjects(self, query: str, max_subjects: int = 5) -> dict[str, Any]:
        try:
            results = self.bangumi_client.search_subjects(str(query or ''), None)
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}
        limit = max(1, int(max_subjects or 5))
        subjects = [self._subject_card_from_api(subject) for subject in list(results or [])[:limit]]
        self._upsert_subject_cards(subjects)
        return {
            'ok': True,
            'subjects': [self._subject_payload(card) for card in subjects],
            'usage_hint': 'Returned anime subject IDs are factual anchors. In same-franchise bundles, choose the reliable main anchor with select_bangumi_anchor_subject(anchor_subject_id, reason) so Python atomically builds the relation atlas before more side-title searches.',
            'context': self._case_context_payload(detail=False),
        }

    def tool_select_bangumi_anchor_subject(
        self,
        anchor_subject_id: int,
        reason: str = '',
        board_delta: Any = '',
        max_subjects: int = 160,
        hydrate_episode_surfaces: bool = True,
        max_relation_fetches: int = 240,
        emergency_depth: int = 32,
        max_episode_cards_per_subject: int = 160,
        detail: bool = False,
    ) -> dict[str, Any]:
        anchor_id = int(anchor_subject_id or 0)
        if anchor_id <= 0:
            return {
                'ok': False,
                'error': 'anchor_subject_id is required',
                'repair_hints': ['Search or inspect Bangumi subjects first, then pass the Pi-chosen reliable main anime/video subject_id.'],
                'anchor_selection_policy': 'Pi chooses the anchor. Python only records that choice and builds evidence from it.',
            }
        atlas_result = self.tool_build_bangumi_relation_atlas(
            anchor_subject_id=anchor_id,
            max_subjects=max_subjects,
            hydrate_episode_surfaces=hydrate_episode_surfaces,
            max_relation_fetches=max_relation_fetches,
            emergency_depth=emergency_depth,
            max_episode_cards_per_subject=max_episode_cards_per_subject,
        )
        atlas_status = atlas_result.get('traversal_status') if isinstance(atlas_result, dict) and isinstance(atlas_result.get('traversal_status'), dict) else {}
        note_payload = {
            'anchor_subject_id': anchor_id,
            'reason': str(reason or ''),
            'atlas_id': atlas_result.get('atlas_id') if isinstance(atlas_result, dict) else '',
            'atlas_path': atlas_result.get('atlas_path') if isinstance(atlas_result, dict) else '',
            'atlas_markdown_path': atlas_result.get('atlas_markdown_path') if isinstance(atlas_result, dict) else '',
            'traversal_status': {
                'frontier_exhausted': atlas_status.get('frontier_exhausted'),
                'stop_reason': atlas_status.get('stop_reason'),
                'subject_count': atlas_result.get('subject_count') if isinstance(atlas_result, dict) else None,
                'edge_count': atlas_result.get('edge_count') if isinstance(atlas_result, dict) else None,
                'relation_filter': 'strict',
            },
            'extra_note': board_delta,
            'policy': 'Pi selected the anchor. Python only recorded the selection and built a Bangumi relation atlas evidence surface.',
        }
        board_note = self._append_case_board_section(
            section_type='Board Delta',
            content=note_payload,
            next_action='synthesize atlas row surfaces into group decisions, one targeted fact, or fail_closed',
        )
        if not isinstance(atlas_result, dict) or not atlas_result.get('ok'):
            return {
                'ok': False,
                'accepted': False,
                'status': 'anchor_atlas_failed',
                'error': atlas_result.get('error') if isinstance(atlas_result, dict) else 'atlas build failed',
                'selected_anchor_subject_id': anchor_id,
                'reason': str(reason or ''),
                'case_board_transaction': {'anchor_atlas_bootstrap': board_note},
                'atlas_result': _json_safe(atlas_result),
                'anchor_selection_policy': 'Pi chose the anchor. Python did not choose a mapping target.',
            }
        compact = {
            'ok': True,
            'status': 'anchor_atlas_ready',
            'selected_anchor_subject_id': anchor_id,
            'reason': str(reason or ''),
            'atlas_id': atlas_result.get('atlas_id'),
            'atlas_path': atlas_result.get('atlas_path'),
            'atlas_markdown_path': atlas_result.get('atlas_markdown_path'),
            'subject_count': atlas_result.get('subject_count'),
            'edge_count': atlas_result.get('edge_count'),
            'traversal_status': atlas_result.get('traversal_status'),
            'case_board_transaction': {'anchor_atlas_bootstrap': board_note},
            'next_tool_options': [
                'upsert_recipe_group_decision_one for stable groups/subclusters',
                'targeted get_episode_list/get_target_detail only for named gaps',
            ],
            'anchor_selection_policy': (
                'Pi selected this main anchor. Python built the evidence atlas only; it did not rank subjects, '
                'match local groups, choose recipe rows, or decide supplemental status.'
            ),
        }
        if detail:
            compact['atlas_result'] = atlas_result
        return compact

    def tool_lookup_bangumi_subject(self, subject_ids: list[int]) -> dict[str, Any]:
        subjects: list[BangumiSubjectCard] = []
        for value in subject_ids or []:
            subject_id = int(value or 0)
            if subject_id <= 0:
                continue
            try:
                subject = self.bangumi_client.get_subject(subject_id)
            except Exception as exc:
                return {'ok': False, 'error': str(exc), 'subject_id': subject_id}
            if subject is not None:
                subjects.append(self._subject_card_from_api(subject))
        self._upsert_subject_cards(subjects)
        return {'ok': True, 'subjects': [self._subject_payload(card) for card in subjects], 'context': self._case_context_payload(detail=False)}

    def tool_expand_related_subjects(
        self,
        subject_id: int,
        relation_kinds: list[str] | None = None,
        subject_types: list[str] | None = None,
        max_subjects: int = 8,
    ) -> dict[str, Any]:
        subject_id = int(subject_id or 0)
        if subject_id <= 0:
            return {'ok': False, 'error': 'subject_id is required'}
        effective_relation_keys, disallowed_requested_relations = strict_requested_relation_keys(relation_kinds)
        has_requested_relation_filter = bool([value for value in (relation_kinds or []) if str(value or '').strip()])
        wanted_types = {str(kind).strip().casefold() for kind in (subject_types or []) if str(kind).strip()}
        try:
            relations = self.bangumi_client.get_related_subjects(subject_id) or []
        except Exception as exc:
            return {'ok': False, 'error': str(exc), 'subject_id': subject_id}
        subjects: list[BangumiSubjectCard] = []
        rows: list[dict[str, Any]] = []
        compact_rows: list[dict[str, Any]] = []
        skipped: list[str] = []
        for relation in relations:
            relation_kind = normalize_relation_name(getattr(relation, 'relation', '') or '')
            related_id = int(getattr(relation, 'id', 0) or 0)
            if not is_strict_related_relation(relation_kind):
                skipped.append(f'{subject_id}->{related_id or "unknown"}: skipped disallowed relation={relation_kind or "unknown"}')
                continue
            if has_requested_relation_filter and relation_kind.casefold() not in effective_relation_keys:
                skipped.append(f'{subject_id}->{related_id or "unknown"}: skipped outside requested strict relation filter={relation_kind}')
                continue
            if int(getattr(relation, 'type', 0) or 0) != 2:
                skipped.append(f'{subject_id}->{related_id or "unknown"}: skipped non-anime subject_type={getattr(relation, "type", "unknown") or "unknown"}')
                continue
            if related_id <= 0:
                continue
            try:
                detail = self.bangumi_client.get_subject(related_id)
            except Exception:
                detail = None
            card = self._subject_card_from_api(detail or relation, relation_to_main=relation_kind)
            if card.subject_type.casefold() != 'anime':
                skipped.append(f'{subject_id}->{related_id}: skipped subject_type={card.subject_type or "unknown"}')
                continue
            if wanted_types and card.subject_type.casefold() not in wanted_types:
                continue
            subjects.append(card)
            subject_payload = self._subject_payload(card, include_summary=False)
            rows.append({'relation': relation_kind, 'subject': subject_payload})
            compact_rows.append({
                'relation': relation_kind,
                'subject_id': subject_payload['subject_id'],
                'subject_type': subject_payload['subject_type'],
                'title': subject_payload['title'],
                'name': subject_payload['name'],
                'name_cn': subject_payload['name_cn'],
                'platform': subject_payload['platform'],
                'eps': subject_payload['eps'],
                'total_episodes': subject_payload['total_episodes'],
                'date': subject_payload['date'],
            })
            if len(subjects) >= max(1, int(max_subjects or 8)):
                break
        self._upsert_subject_cards(subjects)
        return {
            'ok': True,
            'relation_subjects': compact_rows,
            'relations': rows,
            'skipped': skipped[:40],
            'relation_filter': {
                'policy': _STRICT_BANGUMI_RELATION_FILTER_NOTE,
                'allowed_relation_kinds': sorted(STRICT_RELATED_RELATION_KINDS),
                'disallowed_requested_relation_kinds': disallowed_requested_relations,
            },
            'usage_hint': 'relation_subjects is a strict relation-filtered compact series map fact surface. Episode rows matter for related anime subjects that match visible local groups; local numbering can then be compared with Bangumi sort/ep and split when one subject lacks the needed rows.',
            'context': self._case_context_payload(detail=False),
        }

    def tool_expand_related_graph(
        self,
        subject_id: int = 0,
        subject_ids: list[int] | None = None,
        relation_kinds: list[str] | None = None,
        subject_types: list[str] | None = None,
        max_depth: int = 3,
        max_subjects: int = 32,
    ) -> dict[str, Any]:
        seeds = [int(value or 0) for value in (subject_ids or []) if int(value or 0) > 0]
        if not seeds and int(subject_id or 0) > 0:
            seeds = [int(subject_id or 0)]
        seeds = list(dict.fromkeys(seeds))
        if not seeds:
            return {'ok': False, 'error': 'subject_id or subject_ids is required'}

        effective_relation_keys, disallowed_requested_relations = strict_requested_relation_keys(relation_kinds)
        has_requested_relation_filter = bool([value for value in (relation_kinds or []) if str(value or '').strip()])
        wanted_types = {str(kind).strip().casefold() for kind in (subject_types or []) if str(kind).strip()}
        depth_limit = max(1, min(4, int(max_depth or 3)))
        subject_limit = max(1, min(80, int(max_subjects or 32)))

        queue: list[tuple[int, int]] = [(seed, 0) for seed in seeds]
        visited_for_traversal: set[int] = set()
        relation_checked: set[int] = set()
        seen_subjects: set[int] = set(seeds)
        subject_cards: dict[int, BangumiSubjectCard] = {}
        node_depth: dict[int, int] = {seed: 0 for seed in seeds}
        edges: list[dict[str, Any]] = []
        skipped: list[str] = []
        relation_fetch_failed_subject_ids: list[int] = []

        for seed in seeds:
            try:
                detail = self.bangumi_client.get_subject(seed)
            except Exception:
                detail = None
            if detail is not None:
                subject_cards[seed] = self._subject_card_from_api(detail)

        while queue and len(seen_subjects - set(seeds)) < subject_limit:
            current_id, depth = queue.pop(0)
            if current_id in visited_for_traversal:
                continue
            visited_for_traversal.add(current_id)
            if depth >= depth_limit:
                continue
            try:
                relations = self.bangumi_client.get_related_subjects(current_id) or []
            except Exception as exc:
                skipped.append(f'{current_id}: relation fetch failed: {exc}')
                relation_fetch_failed_subject_ids.append(current_id)
                continue
            relation_checked.add(current_id)
            for relation in relations:
                relation_kind = normalize_relation_name(getattr(relation, 'relation', '') or '')
                related_id = int(getattr(relation, 'id', 0) or 0)
                if not is_strict_related_relation(relation_kind):
                    skipped.append(f'{current_id}->{related_id or "unknown"}: skipped disallowed relation={relation_kind or "unknown"}')
                    continue
                if has_requested_relation_filter and relation_kind.casefold() not in effective_relation_keys:
                    skipped.append(f'{current_id}->{related_id or "unknown"}: skipped outside requested strict relation filter={relation_kind}')
                    continue
                if int(getattr(relation, 'type', 0) or 0) != 2:
                    skipped.append(f'{current_id}->{related_id or "unknown"}: skipped non-anime subject_type={getattr(relation, "type", "unknown") or "unknown"}')
                    continue
                if related_id <= 0:
                    continue
                try:
                    detail = self.bangumi_client.get_subject(related_id)
                except Exception:
                    detail = None
                card = self._subject_card_from_api(detail or relation, relation_to_main=relation_kind)
                if wanted_types and card.subject_type.casefold() not in wanted_types:
                    skipped.append(f'{current_id}->{related_id}: skipped subject_type={card.subject_type or "unknown"}')
                    continue
                edge = {
                    'from_subject_id': current_id,
                    'to_subject_id': related_id,
                    'relation': relation_kind,
                    'depth': depth + 1,
                }
                if edge not in edges:
                    edges.append(edge)
                if related_id not in seen_subjects:
                    if len(seen_subjects - set(seeds)) >= subject_limit:
                        skipped.append(f'{current_id}->{related_id}: skipped over max_subjects')
                        continue
                    seen_subjects.add(related_id)
                    node_depth[related_id] = depth + 1
                    subject_cards[related_id] = card
                    queue.append((related_id, depth + 1))
                elif related_id not in subject_cards:
                    subject_cards[related_id] = card

        ordered_cards = [
            subject_cards[subject_id]
            for subject_id in sorted(subject_cards, key=lambda sid: (node_depth.get(sid, 999), sid))
        ]
        self._upsert_subject_cards(ordered_cards)

        nodes = []
        for card in ordered_cards:
            payload = self._subject_payload(card, include_summary=False)
            payload['depth'] = node_depth.get(int(card.subject_id or 0), 0)
            payload['is_seed'] = int(card.subject_id or 0) in set(seeds)
            nodes.append(payload)

        related_rows = [node for node in nodes if not node.get('is_seed')]
        ordered_seen_ids = sorted(seen_subjects, key=lambda sid: (node_depth.get(sid, 999), sid))
        ordered_checked_ids = sorted(relation_checked, key=lambda sid: (node_depth.get(sid, 999), sid))
        next_expand_subject_ids = [
            subject_id
            for subject_id in ordered_seen_ids
            if subject_id not in relation_checked and subject_id not in relation_fetch_failed_subject_ids
        ]
        hit_subject_limit = bool(queue) or len(seen_subjects - set(seeds)) >= subject_limit
        if relation_fetch_failed_subject_ids:
            stop_reason = 'relation_fetch_failed'
        elif hit_subject_limit:
            stop_reason = 'subject_limit_reached'
        elif next_expand_subject_ids:
            stop_reason = 'depth_limit_reached'
        else:
            stop_reason = 'frontier_exhausted'
        return {
            'ok': True,
            'seed_subject_ids': seeds,
            'max_depth': depth_limit,
            'subject_count': len(nodes),
            'edge_count': len(edges),
            'traversal_status': {
                'frontier_exhausted': stop_reason == 'frontier_exhausted',
                'stop_reason': stop_reason,
                'seen_subject_ids': ordered_seen_ids,
                'new_related_subject_ids': [subject_id for subject_id in ordered_seen_ids if subject_id not in set(seeds)],
                'relation_checked_subject_ids': ordered_checked_ids,
                'next_subject_ids_to_expand': next_expand_subject_ids[:20],
                'relation_fetch_failed_subject_ids': relation_fetch_failed_subject_ids[:20],
                'allowed_relation_kinds': sorted(STRICT_RELATED_RELATION_KINDS),
                'disallowed_requested_relation_kinds': disallowed_requested_relations,
            },
            'relation_subjects': related_rows,
            'subjects': nodes,
            'edges': edges,
            'skipped': skipped[:20],
            'usage_hint': f'This recursive relation graph is strict relation-filtered evidence only. {_STRICT_BANGUMI_RELATION_FILTER_NOTE} traversal_status.next_subject_ids_to_expand is a bounded-frontier fact for named local groups that still need relation context; it is not a recommendation.',
            'context': self._case_context_payload(detail=False),
        }

    def tool_build_bangumi_relation_atlas(
        self,
        anchor_subject_id: int,
        max_subjects: int = 160,
        hydrate_episode_surfaces: bool = True,
        max_relation_fetches: int = 240,
        emergency_depth: int = 32,
        max_episode_cards_per_subject: int = 160,
    ) -> dict[str, Any]:
        anchor_id = int(anchor_subject_id or 0)
        if anchor_id <= 0:
            return {
                'ok': False,
                'error': 'anchor_subject_id is required',
                'atlas_policy': 'Pi chooses the reliable main anchor. Python only traverses and packages Bangumi anime/video relation facts.',
            }
        subject_limit = max(1, min(160, int(max_subjects or 160)))
        relation_fetch_limit = max(1, min(400, int(max_relation_fetches or 240)))
        depth_guard = max(1, min(80, int(emergency_depth or 32)))
        episode_limit = max(1, min(500, int(max_episode_cards_per_subject or 160)))

        try:
            anchor_detail = self.bangumi_client.get_subject(anchor_id)
        except Exception as exc:
            return {'ok': False, 'error': str(exc), 'anchor_subject_id': anchor_id}
        if anchor_detail is None:
            return {'ok': False, 'error': f'unknown anchor_subject_id: {anchor_id}', 'anchor_subject_id': anchor_id}
        anchor_card = self._subject_card_from_api(anchor_detail)
        if anchor_card.subject_type.casefold() != 'anime':
            return {
                'ok': False,
                'error': f'anchor_subject_id is not an anime/video subject: {anchor_id}',
                'anchor_subject': self._subject_payload(anchor_card, include_summary=False),
                'atlas_policy': 'Relation atlas is scoped to Bangumi anime/video subjects only.',
            }

        atlas_id = self._next_relation_atlas_id(anchor_id)
        queue: list[tuple[int, int]] = [(anchor_id, 0)]
        visited_for_traversal: set[int] = set()
        relation_checked: set[int] = set()
        seen_subjects: set[int] = {anchor_id}
        subject_cards: dict[int, BangumiSubjectCard] = {anchor_id: anchor_card}
        node_depth: dict[int, int] = {anchor_id: 0}
        relation_paths: dict[int, list[dict[str, Any]]] = {anchor_id: []}
        edges: list[dict[str, Any]] = []
        skipped: list[str] = []
        relation_fetch_failed_subject_ids: list[int] = []
        hit_subject_limit = False
        hit_relation_fetch_limit = False
        hit_depth_guard = False
        relation_fetch_count = 0

        while queue:
            current_id, depth = queue.pop(0)
            if current_id in visited_for_traversal:
                continue
            if relation_fetch_count >= relation_fetch_limit:
                hit_relation_fetch_limit = True
                break
            if depth >= depth_guard:
                hit_depth_guard = True
                skipped.append(f'{current_id}: emergency depth guard reached at depth={depth}')
                continue
            visited_for_traversal.add(current_id)
            try:
                relations = self.bangumi_client.get_related_subjects(current_id) or []
            except Exception as exc:
                skipped.append(f'{current_id}: relation fetch failed: {exc}')
                relation_fetch_failed_subject_ids.append(current_id)
                continue
            relation_fetch_count += 1
            relation_checked.add(current_id)
            for relation in relations:
                related_id = int(getattr(relation, 'id', 0) or 0)
                relation_kind = normalize_relation_name(getattr(relation, 'relation', '') or '')
                if not is_strict_related_relation(relation_kind):
                    skipped.append(f'{current_id}->{related_id or "unknown"}: skipped disallowed relation={relation_kind or "unknown"}')
                    continue
                if int(getattr(relation, 'type', 0) or 0) != 2:
                    skipped.append(f'{current_id}->{related_id or "unknown"}: skipped non-anime subject_type={getattr(relation, "type", "unknown") or "unknown"}')
                    continue
                if related_id <= 0:
                    continue
                try:
                    detail = self.bangumi_client.get_subject(related_id)
                except Exception:
                    detail = None
                card = self._subject_card_from_api(detail or relation, relation_to_main=relation_kind)
                if card.subject_type.casefold() != 'anime':
                    skipped.append(f'{current_id}->{related_id}: skipped subject_type={card.subject_type or "unknown"}')
                    continue
                edge = {
                    'from_subject_id': current_id,
                    'to_subject_id': related_id,
                    'relation': relation_kind,
                    'depth': depth + 1,
                }
                if edge not in edges:
                    edges.append(edge)
                if related_id not in seen_subjects:
                    if len(seen_subjects) >= subject_limit:
                        hit_subject_limit = True
                        skipped.append(f'{current_id}->{related_id}: skipped over max_subjects')
                        continue
                    seen_subjects.add(related_id)
                    node_depth[related_id] = depth + 1
                    subject_cards[related_id] = card
                    relation_paths[related_id] = [
                        *(relation_paths.get(current_id) or []),
                        {
                            'from_subject_id': current_id,
                            'to_subject_id': related_id,
                            'relation': relation_kind,
                        },
                    ]
                    queue.append((related_id, depth + 1))
                elif related_id not in subject_cards:
                    subject_cards[related_id] = card

        ordered_subject_ids = sorted(seen_subjects, key=lambda sid: (node_depth.get(sid, 999), sid))
        ordered_cards = [subject_cards[sid] for sid in ordered_subject_ids if sid in subject_cards]
        self._upsert_subject_cards(ordered_cards)

        episode_surface_by_subject: dict[int, dict[str, Any]] = {}
        episode_errors: list[dict[str, Any]] = []
        hydrated_item_cards: list[BangumiItemCard] = []
        if hydrate_episode_surfaces:
            for subject_id in ordered_subject_ids:
                try:
                    episodes = list(self.bangumi_client.get_episodes(subject_id) or [])
                except Exception as exc:
                    episode_errors.append({'subject_id': subject_id, 'error': str(exc)})
                    episode_surface_by_subject[subject_id] = {
                        'subject_id': subject_id,
                        'episode_list_error': str(exc),
                        'row_count': 0,
                        'returned_row_count': 0,
                        'row_surface_counts': {},
                        'samples': {},
                    }
                    continue
                cards = sorted(
                    [self._episode_card_from_api(subject_id, episode) for episode in episodes[:episode_limit]],
                    key=_episode_card_order_key,
                )
                hydrated_item_cards.extend(cards)
                episode_surface_by_subject[subject_id] = self._episode_surface_summary(
                    subject_id=subject_id,
                    item_cards=cards,
                    total_available=len(episodes),
                    limit=episode_limit,
                )
        if hydrated_item_cards:
            self._upsert_item_cards(hydrated_item_cards)

        subject_rows: list[dict[str, Any]] = []
        for subject_id in ordered_subject_ids:
            card = subject_cards.get(subject_id)
            if card is None:
                continue
            payload = self._subject_payload(card, include_summary=False)
            payload.update({
                'depth': node_depth.get(subject_id, 0),
                'is_anchor': subject_id == anchor_id,
                'relation_path': relation_paths.get(subject_id) or [],
                'relation_path_text': self._relation_path_text(relation_paths.get(subject_id) or []),
                'episode_surface': episode_surface_by_subject.get(subject_id, {
                    'subject_id': subject_id,
                    'row_count': 0,
                    'returned_row_count': 0,
                    'row_surface_counts': {},
                    'samples': {},
                    'hydrated': False,
                }),
            })
            subject_rows.append(payload)

        if relation_fetch_failed_subject_ids:
            stop_reason = 'relation_fetch_failed'
        elif hit_relation_fetch_limit:
            stop_reason = 'relation_fetch_limit_reached'
        elif hit_subject_limit:
            stop_reason = 'subject_limit_reached'
        elif hit_depth_guard:
            stop_reason = 'emergency_depth_reached'
        else:
            stop_reason = 'frontier_exhausted'
        traversal_status = {
            'frontier_exhausted': stop_reason == 'frontier_exhausted',
            'stop_reason': stop_reason,
            'anchor_subject_id': anchor_id,
            'seen_subject_ids': ordered_subject_ids,
            'new_related_subject_ids': [sid for sid in ordered_subject_ids if sid != anchor_id],
            'relation_checked_subject_ids': sorted(relation_checked, key=lambda sid: (node_depth.get(sid, 999), sid)),
            'relation_fetch_count': relation_fetch_count,
            'max_subjects': subject_limit,
            'max_relation_fetches': relation_fetch_limit,
            'emergency_depth': depth_guard,
            'relation_fetch_failed_subject_ids': relation_fetch_failed_subject_ids[:40],
            'episode_surface_error_count': len(episode_errors),
            'allowed_relation_kinds': sorted(STRICT_RELATED_RELATION_KINDS),
            'relation_filter_policy': _STRICT_BANGUMI_RELATION_FILTER_NOTE,
        }
        atlas = {
            'atlas_id': atlas_id,
            'anchor_subject_id': anchor_id,
            'subject_count': len(subject_rows),
            'edge_count': len(edges),
            'hydrate_episode_surfaces': bool(hydrate_episode_surfaces),
            'traversal_status': traversal_status,
            'subjects': subject_rows,
            'edges': edges,
            'skipped': skipped[:120],
            'episode_surface_errors': episode_errors[:80],
            'atlas_policy': (
                'Evidence atlas only. Python traversed strict relation-filtered reachable Bangumi anime/video related subjects and compact row surfaces. '
                'It did not rank subjects, match local groups, choose recipe rows, or decide supplemental status.'
            ),
        }
        atlas_path, atlas_md_path = self._relation_atlas_paths(atlas_id)
        atlas_path.parent.mkdir(parents=True, exist_ok=True)
        atlas_path.write_text(json.dumps(_json_safe(atlas), ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')
        atlas_md_path.write_text(self._relation_atlas_markdown(atlas), encoding='utf-8')
        return {
            'ok': True,
            'atlas_id': atlas_id,
            'anchor_subject_id': anchor_id,
            'atlas_path': str(atlas_path),
            'atlas_markdown_path': str(atlas_md_path),
            'subject_count': len(subject_rows),
            'edge_count': len(edges),
            'traversal_status': traversal_status,
            'subjects': subject_rows[:40],
            'subjects_truncated': len(subject_rows) > 40,
            'skipped': skipped[:20],
            'episode_surface_errors': episode_errors[:20],
            'next_tool_options': [
                'upsert_recipe_group_decision_one for stable groups',
                'targeted get_episode_list/get_target_detail only for named gaps',
            ],
            'usage_hint': 'For complex packages, read the strict relation-filtered atlas before broad side-title searches. Save stable groups directly; use targeted episode/subject tools only for named gaps.',
            'context': self._case_context_payload(detail=False),
        }

    def tool_get_episode_list(self, subject_id: int, episode_scope: str = 'all_if_small', max_episode_cards: int = 240) -> dict[str, Any]:
        subject_id = int(subject_id or 0)
        if subject_id <= 0:
            return {'ok': False, 'error': 'subject_id is required'}
        try:
            episodes = self.bangumi_client.get_episodes(subject_id) or []
        except Exception as exc:
            return {'ok': False, 'error': str(exc), 'subject_id': subject_id}
        selected = self._select_episodes(list(episodes), str(episode_scope or 'all_if_small'))
        item_cards = sorted(
            [self._episode_card_from_api(subject_id, episode) for episode in selected],
            key=_episode_card_order_key,
        )[: max(1, int(max_episode_cards or 240))]
        self._ensure_subject_known(subject_id)
        self._upsert_item_cards(item_cards)
        return {
            'ok': True,
            'subject_id': subject_id,
            'episodes': [self._episode_payload(card) for card in item_cards],
            'context': self._case_context_payload(detail=False),
        }

    def tool_get_target_detail(self, episode_ids: list[int] | None = None, subject_id: int = 0, sort: int = 0) -> dict[str, Any]:
        episode_ids = [int(value or 0) for value in (episode_ids or []) if int(value or 0) > 0]
        subject_id = int(subject_id or 0)
        if episode_ids:
            existing = [card for card in self.workspace.bangumi_items if int(getattr(card, 'episode_id', 0) or 0) in set(episode_ids)]
            if existing:
                return {'ok': True, 'episodes': [self._episode_payload(card) for card in existing]}
        if subject_id <= 0:
            return {'ok': False, 'error': 'subject_id is required when episode_ids are not already visible'}
        result = self.tool_get_episode_list(subject_id=subject_id, episode_scope='all_if_small')
        if not result.get('ok'):
            return result
        episodes = [
            item for item in result.get('episodes', [])
            if (not episode_ids or int(item.get('episode_id') or 0) in episode_ids)
            and (not sort or int(item.get('sort') or 0) == int(sort))
        ]
        return {'ok': True, 'episodes': episodes}

    def tool_get_local_file_detail(self, paths: list[str]) -> dict[str, Any]:
        wanted = {
            canonical_path
            for path in paths or []
            for _card, canonical_path, _original_path in [self._resolve_local_file_path(path)]
            if canonical_path
        }
        files = [
            self._local_file_payload(card, detail=True)
            for card in self.workspace.local_files
            if not wanted or _norm_path(card.path) in wanted
        ]
        return {'ok': True, 'files': files}

    def tool_find_bangumi_targets_for_local_file(
        self,
        source_path: str,
        title_query: str = '',
        kind_hint: str = '',
        max_subjects: int = 5,
        max_episode_cards: int = 24,
    ) -> dict[str, Any]:
        """Compact fact helper for simple path-to-Bangumi investigations.

        This deliberately returns search and episode facts only. Pi still has to
        choose the semantic target, write a recipe, and pass the verifier gate.
        """
        normalized_path = _norm_path(source_path)
        local_card, normalized_path, original_path = self._resolve_local_file_path(normalized_path)
        if local_card is None:
            visible_paths = self._visible_main_paths()
            basename_matches = [
                path for path in visible_paths
                if _norm_path(path).rsplit('/', 1)[-1] == normalized_path.rsplit('/', 1)[-1]
            ]
            repair_hint = 'Retry with an exact value from visible_source_paths; do not use task_source_path or a sample JSON path as a local file identity.'
            if len(basename_matches) > 1:
                repair_hint = f'This basename is ambiguous; use one exact visible_source_paths value. Matching visible paths: {basename_matches[:8]}'
            return {
                'ok': False,
                'error': f'source_path is not in the visible file universe: {normalized_path}',
                'visible_source_paths': visible_paths,
                'basename_match_count': len(basename_matches),
                'repair_hints': [repair_hint],
            }

        query = str(title_query or '').strip() or _query_from_source_path(normalized_path)
        queries = _dedupe_nonempty([
            query,
            _query_without_terminal_kind(query, kind_hint),
        ])[:2]
        subject_limit = max(1, int(max_subjects or 5))
        episode_limit = max(1, int(max_episode_cards or 24))

        subjects_by_id: dict[int, BangumiSubjectCard] = {}
        search_rows: list[dict[str, Any]] = []
        for search_query in queries:
            try:
                results = self.bangumi_client.search_subjects(search_query, None)
            except Exception as exc:
                return {'ok': False, 'error': str(exc), 'query': search_query}
            cards = [self._subject_card_from_api(subject) for subject in list(results or [])[:subject_limit]]
            self._upsert_subject_cards(cards)
            search_rows.append({'query': search_query, 'subject_ids': [int(card.subject_id or 0) for card in cards if int(card.subject_id or 0) > 0]})
            for card in cards:
                if int(card.subject_id or 0) > 0:
                    subjects_by_id.setdefault(int(card.subject_id or 0), card)
            if len(subjects_by_id) >= subject_limit:
                break

        subject_episode_groups: list[dict[str, Any]] = []
        for subject in list(subjects_by_id.values())[:subject_limit]:
            subject_id = int(subject.subject_id or 0)
            if subject_id <= 0:
                continue
            try:
                episodes = self.bangumi_client.get_episodes(subject_id) or []
            except Exception as exc:
                subject_episode_groups.append({
                    'subject': self._subject_payload(subject),
                    'episodes': [],
                    'episode_list_error': str(exc),
                })
                continue
            episode_values = list(episodes or [])
            item_cards = sorted(
                [self._episode_card_from_api(subject_id, episode) for episode in episode_values],
                key=_episode_card_order_key,
            )[:episode_limit]
            self._ensure_subject_known(subject_id)
            self._upsert_item_cards(item_cards)
            subject_episode_groups.append({
                'subject': self._subject_payload(subject),
                'episodes': [self._episode_payload(episode) for episode in item_cards],
                'episode_count_available': len(episode_values),
                'episode_count_returned': len(item_cards),
                'episode_rows_limited': len(episode_values) > len(item_cards),
            })
        return {
            'ok': True,
            'source_path': normalized_path,
            'source_path_canonicalized_from': original_path,
            'local_file': self._local_file_payload(local_card, detail=True),
            'title_query': query,
            'queries_used': search_rows,
            'subject_episode_groups': subject_episode_groups,
            'episode_order': 'sort, then ep, then episode_id',
            'usage_hint': 'Use these as facts only. episode_rows_limited means this compact helper returned a partial row window; declared subject IDs can later be hydrated by evidence or validation tools without treating this helper as a chosen target.',
            'context': self._case_context_payload(detail=False),
        }

    def tool_get_target_window(self, subject_id: int, sort_start: int = 0, sort_end: int = 0) -> dict[str, Any]:
        subject_id = int(subject_id or 0)
        if subject_id <= 0:
            return {'ok': False, 'error': 'subject_id is required'}
        if not any(self._subject_id_for_item(card) == subject_id for card in self.workspace.bangumi_items):
            result = self.tool_get_episode_list(subject_id=subject_id, episode_scope='all_if_small')
            if not result.get('ok'):
                return result
        episodes = [
            self._episode_payload(card)
            for card in sorted(self.workspace.bangumi_items, key=_episode_card_order_key)
            if self._subject_id_for_item(card) == subject_id
            and (not sort_start or int(getattr(card, 'sort', 0) or 0) >= int(sort_start))
            and (not sort_end or int(getattr(card, 'sort', 0) or 0) <= int(sort_end))
        ]
        return {'ok': True, 'subject_id': subject_id, 'episodes': episodes}

    def tool_validate_organize_recipe(self, organize_recipe: dict[str, Any] | None = None) -> dict[str, Any]:
        recipe, error = self._parse_recipe_payload(organize_recipe)
        if error:
            return {'ok': False, 'accepted': False, 'error': error, 'repair_hints': _parse_error_repair_hints(error, self._visible_main_paths())}
        assert recipe is not None
        self._hydrate_recipe_target_evidence(recipe)
        plan, verifier_result = compile_and_verify_organize_recipe(self.workspace, recipe)
        self.organize_recipe = recipe
        self.compiled_plan = plan
        self.recipe_verifier_result = verifier_result
        repair_hints = self._recipe_repair_hints(verifier_result)
        review_warnings = self._recipe_review_warnings(plan)
        all_hints = _dedupe_nonempty([*repair_hints, *_review_warning_hints(review_warnings)])
        accepted = bool(verifier_result.passed and not review_warnings)
        status = 'accepted' if accepted else ('review' if verifier_result.passed else 'invalid')
        summary = (
            verifier_result.summary
            if accepted or not verifier_result.passed
            else f'accepted mechanically, but {len(review_warnings)} review warning(s) need targeted evidence'
        )
        self._write_recipe_artifacts(recipe, plan, verifier_result, repair_hints=repair_hints, review_warnings=review_warnings)
        case_board_next_action = self._case_board_next_action(
            status=status,
            verifier_result=verifier_result,
            review_warnings=review_warnings,
        )
        return {
            'ok': True,
            'accepted': accepted,
            'status': status,
            'summary': summary,
            'case_board_next_action': case_board_next_action,
            'review_warnings': review_warnings,
            'repair_hints': all_hints,
            'accounting': recipe_accounting(plan),
            'verifier_result': verifier_result.model_dump(mode='json'),
            'compiled_plan': plan.model_dump(mode='json'),
        }

    def tool_validate_organize_recipe_params(
        self,
        recipe_params: dict[str, Any] | None = None,
        validation_snapshot: Any = '',
        detail: bool = False,
    ) -> dict[str, Any]:
        board_note = self._append_optional_case_board_section(
            section_type='Validation Snapshot',
            content=validation_snapshot,
            next_action='validate_organize_recipe_params',
        )
        recipe, error = self._parse_recipe_params_payload(recipe_params)
        if error:
            result = {'ok': False, 'accepted': False, 'error': error, 'repair_hints': _parse_error_repair_hints(error, self._visible_main_paths())}
            if board_note:
                result['case_board_transaction'] = {'validation_snapshot': board_note}
            return self._compact_params_tool_result(result, detail=bool(detail))
        assert recipe is not None
        result = self.tool_validate_organize_recipe(organize_recipe=recipe.model_dump(mode='json'))
        result['organize_recipe'] = recipe.model_dump(mode='json')
        result['params_compiled'] = True
        result['validation_role'] = 'trial_check'
        result['finalizes_case'] = False
        result['feedback_semantics'] = 'invalid and review statuses are verifier feedback for repair; accepted validation still requires submit_organize_recipe_params to finish.'
        self._write_latest_recipe_params_artifact()
        verifier_delta = self._append_verifier_delta_for_result(result)
        if board_note or verifier_delta:
            result['case_board_transaction'] = {
                'validation_snapshot': board_note,
                'verifier_delta': verifier_delta,
            }
        return self._compact_params_tool_result(result, detail=bool(detail))

    def tool_validate_organize_recipe_params_patch(
        self,
        recipe_params_patch: dict[str, Any] | None = None,
        patch: dict[str, Any] | None = None,
        patch_delta: Any = '',
        detail: bool = False,
    ) -> dict[str, Any]:
        board_note = self._append_optional_case_board_section(
            section_type='Patch Delta',
            content=patch_delta,
            next_action='validate_organize_recipe_params_patch',
        )
        normalized_patch, error = self._normalize_recipe_params_patch_payload(recipe_params_patch if recipe_params_patch is not None else patch)
        if error:
            result = {'ok': False, 'accepted': False, 'error': error, 'repair_hints': _parse_error_repair_hints(error, self._visible_main_paths())}
            if board_note:
                result['case_board_transaction'] = {'patch_delta': board_note}
            return self._compact_params_tool_result(result, detail=bool(detail))
        merged, error = self._recipe_params_with_normalized_patch(normalized_patch)
        if error:
            draft_merged, draft_error = self._recipe_params_draft_with_normalized_patch(normalized_patch)
            if draft_merged is not None:
                self.latest_recipe_params_draft_payload = draft_merged
                self._write_recipe_params_draft_artifact()
                payload = self._recipe_params_draft_state_payload(detail=True)
                next_tool = 'validate_recipe_params_draft' if payload.get('ready_for_full_validation') else 'upsert_recipe_params_draft'
                payload.update({
                    'ok': True,
                    'accepted': False,
                    'status': 'draft_patch_applied',
                    'params_patch_applied_to_recipe_params_draft': True,
                    'validation_deferred': True,
                    'next_tool': next_tool,
                    'repair_hints': [
                        'No prior full params validation exists, so this patch was applied to recipe_params_draft instead of the latest validated params.',
                        'When ready_for_full_validation is true, call validate_recipe_params_draft with a validation_snapshot.',
                    ],
                    'draft_patch_policy': 'Mechanical draft merge only. Python saved Pi-provided patch fields into Pi-owned recipe_params_draft; it did not judge Bangumi targets or supplemental status.',
                })
                if board_note:
                    payload['case_board_transaction'] = {'patch_delta': board_note}
                if detail:
                    return payload
                compact_payload = self._recipe_params_draft_state_payload(detail=False)
                compact_payload.update({
                    'ok': True,
                    'accepted': False,
                    'status': 'draft_patch_applied',
                    'params_patch_applied_to_recipe_params_draft': True,
                    'validation_deferred': True,
                    'next_tool': next_tool,
                    'repair_hints': [
                        'Patch applied to recipe_params_draft because no prior full params validation exists.',
                        'When ready_for_full_validation is true, call validate_recipe_params_draft with validation_snapshot.',
                    ],
                    'case_board_transaction': {'patch_delta': board_note} if board_note else {},
                })
                return compact_payload
            if draft_error:
                error = f'{error}; recipe_params_draft patch also failed: {draft_error}'
            result = {'ok': False, 'accepted': False, 'error': error, 'repair_hints': _parse_error_repair_hints(error, self._visible_main_paths())}
            if board_note:
                result['case_board_transaction'] = {'patch_delta': board_note}
            return self._compact_params_tool_result(result, detail=bool(detail))
        result = self.tool_validate_organize_recipe_params(recipe_params=merged, detail=detail)
        result['params_patch_applied'] = True
        self.latest_recipe_params_patch_payload = _json_clone(normalized_patch)
        self.latest_recipe_params_patch_merged_payload = _json_clone(merged)
        self.latest_recipe_params_patch_accepted = bool(result.get('accepted'))
        if board_note:
            transaction = result.get('case_board_transaction') if isinstance(result.get('case_board_transaction'), dict) else {}
            result['case_board_transaction'] = {'patch_delta': board_note, **transaction}
        return result

    def tool_submit_organize_recipe(self, organize_recipe: dict[str, Any] | None = None, summary: str = '') -> dict[str, Any]:
        recipe, error = self._parse_recipe_payload(organize_recipe)
        if error:
            return {'ok': False, 'accepted': False, 'error': error, 'repair_hints': _parse_error_repair_hints(error, self._visible_main_paths())}
        assert recipe is not None
        self._hydrate_recipe_target_evidence(recipe)
        plan, verifier_result = compile_and_verify_organize_recipe(self.workspace, recipe)
        self.organize_recipe = recipe
        self.compiled_plan = plan
        self.recipe_verifier_result = verifier_result
        repair_hints = self._recipe_repair_hints(verifier_result)
        review_warnings = self._recipe_review_warnings(plan)
        self._write_recipe_artifacts(recipe, plan, verifier_result, repair_hints=repair_hints, review_warnings=review_warnings)
        if not verifier_result.passed or review_warnings:
            self.submit_rejection_count += 1
            all_hints = _dedupe_nonempty([*repair_hints, *_review_warning_hints(review_warnings)])
            self.last_invalid_submission = {
                'organize_recipe': recipe.model_dump(mode='json'),
                'compiled_plan': plan.model_dump(mode='json'),
                'verifier_result': verifier_result.model_dump(mode='json'),
                'accounting': recipe_accounting(plan),
                'repair_hints': all_hints,
                'review_warnings': review_warnings,
            }
            summary_text = (
                'Review warnings need targeted evidence; revise and submit again.'
                if verifier_result.passed
                else 'Verifier rejected the recipe; revise and submit again.'
            )
            return {
                'ok': True,
                'accepted': False,
                'status': 'review' if verifier_result.passed else 'invalid',
                'summary': summary_text,
                'case_board_next_action': self._case_board_next_action(
                    status='review' if verifier_result.passed else 'invalid',
                    verifier_result=verifier_result,
                    review_warnings=review_warnings,
                ),
                'review_warnings': review_warnings,
                'repair_hints': all_hints,
                'accounting': recipe_accounting(plan),
                'verifier_result': verifier_result.model_dump(mode='json'),
                'compiled_plan': plan.model_dump(mode='json'),
            }

        final_output = CaseJudgeOutput(
            action='submit_verdict',
            summary=str(summary or 'Pi submitted a verifier-accepted organize recipe.'),
        )
        self.final_result = {
            'ok': True,
            'status': 'accepted',
            'summary': final_output.summary,
            'final_action': 'submit_verdict',
            'final_output': final_output.model_dump(mode='json'),
            'organize_recipe': recipe.model_dump(mode='json'),
            'compiled_plan': plan.model_dump(mode='json'),
            'final_verifier_result': verifier_result.model_dump(mode='json'),
            'accounting': recipe_accounting(plan),
            'expanded_assignment_count': len(plan.assignments),
            'repair_hints': [],
            'review_warnings': review_warnings,
        }
        self._write_final_result()
        return {
            'ok': True,
            'accepted': True,
            'status': 'accepted',
            'summary': final_output.summary,
            'case_board_next_action': {
                'section_type': 'Submit Snapshot',
                'next_tool': 'goal_complete',
                'instruction': 'Accepted submit is final. Call goal_complete after recording any desired final audit note.',
            },
            'review_warnings': review_warnings,
            'repair_hints': [],
            'accounting': recipe_accounting(plan),
            'verifier_result': verifier_result.model_dump(mode='json'),
            'compiled_plan': plan.model_dump(mode='json'),
            'expanded_assignment_count': len(plan.assignments),
        }

    def tool_submit_organize_recipe_params(
        self,
        recipe_params: dict[str, Any] | None = None,
        summary: str = '',
        submit_snapshot: Any = '',
        detail: bool = False,
    ) -> dict[str, Any]:
        board_note = self._append_optional_case_board_section(
            section_type='Submit Snapshot',
            content=submit_snapshot,
            next_action='submit_organize_recipe_params',
        )
        recipe, error = self._parse_recipe_params_payload(recipe_params)
        if error:
            result = {'ok': False, 'accepted': False, 'error': error, 'repair_hints': _parse_error_repair_hints(error, self._visible_main_paths())}
            if board_note:
                result['case_board_transaction'] = {'submit_snapshot': board_note}
            return self._compact_params_tool_result(result, detail=bool(detail))
        assert recipe is not None
        result = self.tool_submit_organize_recipe(organize_recipe=recipe.model_dump(mode='json'), summary=summary)
        result['organize_recipe'] = recipe.model_dump(mode='json')
        result['params_compiled'] = True
        if not result.get('accepted'):
            result['finalizes_case'] = False
            result['submit_rejected'] = True
            result['repair_mode'] = {
                'latest_params_available': True,
                'preferred_tool': 'validate_organize_recipe_params_patch',
                'submit_after_accepted_patch': 'submit_organize_recipe_params_patch',
                'policy': 'Rejected submit is verifier feedback. Repair the named issues with a params patch; fetch more evidence only when a verifier issue or review warning asks for it.',
            }
        verifier_delta = self._append_verifier_delta_for_result(result)
        if board_note or verifier_delta:
            result['case_board_transaction'] = {
                'submit_snapshot': board_note,
                'verifier_delta': verifier_delta,
            }
        self._write_latest_recipe_params_artifact()
        return self._compact_params_tool_result(result, detail=bool(detail))

    def tool_submit_organize_recipe_params_patch(
        self,
        recipe_params_patch: dict[str, Any] | None = None,
        patch: dict[str, Any] | None = None,
        summary: str = '',
        patch_delta: Any = '',
        submit_snapshot: Any = '',
        detail: bool = False,
    ) -> dict[str, Any]:
        board_note = self._append_optional_case_board_section(
            section_type='Patch Delta',
            content=patch_delta,
            next_action='submit_organize_recipe_params_patch',
        )
        normalized_patch, error = self._normalize_recipe_params_patch_payload(recipe_params_patch if recipe_params_patch is not None else patch)
        if error:
            result = {'ok': False, 'accepted': False, 'error': error, 'repair_hints': _parse_error_repair_hints(error, self._visible_main_paths())}
            if board_note:
                result['case_board_transaction'] = {'patch_delta': board_note}
            return self._compact_params_tool_result(result, detail=bool(detail))
        if (
            self.latest_recipe_params_patch_accepted
            and _json_safe(_canonical_recipe_params_patch_for_reuse(normalized_patch)) == _json_safe(_canonical_recipe_params_patch_for_reuse(self.latest_recipe_params_patch_payload))
            and isinstance(self.latest_recipe_params_patch_merged_payload, dict)
        ):
            result = self.tool_submit_organize_recipe_params(
                recipe_params=_json_clone(self.latest_recipe_params_patch_merged_payload),
                summary=summary,
                submit_snapshot=submit_snapshot,
                detail=detail,
            )
            result['params_patch_applied'] = True
            result['params_patch_reused_from_accepted_validation'] = True
            if board_note:
                transaction = result.get('case_board_transaction') if isinstance(result.get('case_board_transaction'), dict) else {}
                result['case_board_transaction'] = {'patch_delta': board_note, **transaction}
            return result
        merged, error = self._recipe_params_with_normalized_patch(normalized_patch)
        if error:
            result = {'ok': False, 'accepted': False, 'error': error, 'repair_hints': _parse_error_repair_hints(error, self._visible_main_paths())}
            if board_note:
                result['case_board_transaction'] = {'patch_delta': board_note}
            return self._compact_params_tool_result(result, detail=bool(detail))
        result = self.tool_submit_organize_recipe_params(recipe_params=merged, summary=summary, submit_snapshot=submit_snapshot, detail=detail)
        result['params_patch_applied'] = True
        result['params_patch_reused_from_accepted_validation'] = False
        if board_note:
            transaction = result.get('case_board_transaction') if isinstance(result.get('case_board_transaction'), dict) else {}
            result['case_board_transaction'] = {'patch_delta': board_note, **transaction}
        return result

    def auto_finalize_accepted_validation(self) -> dict[str, Any]:
        """Finalize params Pi already validated but did not submit.

        Some non-interactive Pi runs can stop after an accepted params
        validation. This recovery path still requires the same deterministic
        helper and submit_organize_recipe_params verifier gate; it only avoids
        spending another model turn to repeat the accepted params.
        """
        if self.final_result:
            return {'ok': True, 'accepted': True, 'skipped': True, 'reason': 'final result already exists'}
        if not isinstance(self.latest_recipe_params_payload, dict):
            return {'ok': False, 'accepted': False, 'skipped': True, 'reason': 'no recipe_params have been validated'}
        if self.organize_recipe is None:
            return {'ok': False, 'accepted': False, 'skipped': True, 'reason': 'no compiled recipe has been validated'}
        if self.recipe_verifier_result is None or not self.recipe_verifier_result.passed:
            return {'ok': False, 'accepted': False, 'skipped': True, 'reason': 'latest recipe verifier result is not accepted'}

        helper_check = self._run_recipe_helper_check()
        if not helper_check.get('ok'):
            return {
                'ok': False,
                'accepted': False,
                'skipped': False,
                'reason': 'helper check rejected the validated recipe',
                'helper_check': helper_check,
                'repair_hints': _helper_check_repair_hints(helper_check),
            }

        result = self.handle_tool(
            'submit_organize_recipe_params',
            {
                'recipe_params': _json_clone(self.latest_recipe_params_payload),
                'summary': 'Runner finalized Pi-validated recipe params after validate_organize_recipe_params returned accepted=true.',
            },
        )
        if result.get('accepted') and self.final_result:
            self.final_result['auto_finalized_from_validated_recipe_params'] = True
            self.final_result['helper_check'] = helper_check
            self._write_final_result()
            result['auto_finalized_from_validated_recipe_params'] = True
            result['helper_check'] = helper_check
        return result

    def auto_fail_closed_no_final_result(self, *, reason: str = '') -> dict[str, Any]:
        if self.final_result:
            return {'ok': True, 'accepted': True, 'skipped': True, 'reason': 'final result already exists'}
        if self.recipe_verifier_result is not None and self.recipe_verifier_result.passed:
            return {'ok': False, 'accepted': False, 'skipped': True, 'reason': 'latest recipe verifier result is accepted'}
        tool_summary = self.tool_summary()
        return self._finalize_fail_closed(
            reason=str(reason or f'Pi ended without submit_organize_recipe_params, submit_organize_recipe_params_patch, or fail_closed after investigation. Tool sequence: {tool_summary["tool_sequence"][:24]}'),
            reason_kind='budget_exhausted',
            related_refs=self._visible_main_paths()[:12],
        )

    def _parse_recipe_payload(self, organize_recipe: dict[str, Any] | None) -> tuple[OrganizeRecipeDraft | None, str]:
        if not isinstance(organize_recipe, dict):
            return None, 'invalid OrganizeRecipeDraft payload: expected object; JSON strings and wrapper objects are not accepted'
        payload = dict(organize_recipe)
        if 'organize_recipe' in payload or 'recipe_params' in payload:
            return None, 'invalid OrganizeRecipeDraft payload: wrapper objects are not accepted; pass the raw OrganizeRecipeDraft object only'
        try:
            return OrganizeRecipeDraft.model_validate(payload), ''
        except Exception as exc:
            return None, f'invalid OrganizeRecipeDraft payload: {exc}'

    def _parse_recipe_params_payload(self, recipe_params: dict[str, Any] | None) -> tuple[OrganizeRecipeDraft | None, str]:
        if not isinstance(recipe_params, dict):
            return None, 'invalid OrganizeRecipeParams payload: expected canonical object; JSON strings and wrapper objects are not accepted'
        payload = dict(recipe_params)
        if 'recipe_params' in payload or 'organize_recipe' in payload:
            return None, 'invalid OrganizeRecipeParams payload: wrapper objects are not accepted; pass the canonical recipe_params object directly'
        try:
            self.latest_recipe_params_payload = _json_clone(payload)
            recipe_payload = self._recipe_payload_from_params(payload)
            return OrganizeRecipeDraft.model_validate(recipe_payload), ''
        except Exception as exc:
            return None, f'invalid OrganizeRecipeParams payload: {exc}'

    def _normalize_recipe_params_patch_payload(self, recipe_params_patch: dict[str, Any] | None) -> tuple[dict[str, Any], str]:
        if not isinstance(recipe_params_patch, dict):
            return {}, 'invalid OrganizeRecipeParamsPatch payload: expected canonical object; JSON strings and wrapper objects are not accepted'
        patch = dict(recipe_params_patch)
        if 'recipe_params_patch' in patch or 'patch' in patch:
            return {}, 'invalid OrganizeRecipeParamsPatch payload: wrapper objects are not accepted; pass patch_rules/replace_rules/append_rules/remove_rule_names directly'
        try:
            _validate_recipe_params_patch_shape(patch)
        except Exception as exc:
            return {}, f'invalid OrganizeRecipeParamsPatch payload: {exc}'
        return patch, ''

    def _recipe_params_with_patch(self, recipe_params_patch: dict[str, Any] | None) -> tuple[dict[str, Any] | None, str]:
        patch, error = self._normalize_recipe_params_patch_payload(recipe_params_patch)
        if error:
            return None, error
        return self._recipe_params_with_normalized_patch(patch)

    def _recipe_params_with_normalized_patch(self, patch: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        base = self.latest_recipe_params_payload or self._read_latest_recipe_params_artifact()
        if not isinstance(base, dict) or not isinstance(base.get('rules'), list) or not base.get('rules'):
            return None, 'no previous recipe_params are available to patch; call validate_organize_recipe_params first'
        try:
            merged = _apply_recipe_params_patch(base, patch)
        except Exception as exc:
            return None, f'invalid OrganizeRecipeParamsPatch payload: {exc}'
        return merged, ''

    def _recipe_params_draft_with_normalized_patch(self, patch: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        base = self._read_recipe_params_draft_artifact()
        if not isinstance(base, dict) or not isinstance(base.get('rules'), list) or not base.get('rules'):
            return None, ''
        try:
            merged = _apply_recipe_params_patch(base, patch)
        except Exception as exc:
            return None, f'invalid OrganizeRecipeParamsPatch payload for recipe_params_draft: {exc}'
        merged['summary'] = _string_or_default(
            patch.get('summary'),
            _string_or_default(base.get('summary'), 'Pi incremental recipe params draft.'),
        )
        return merged, ''

    def _read_latest_recipe_params_artifact(self) -> dict[str, Any] | None:
        path = self.run_dir / 'artifacts' / 'recipe_params.json'
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def _write_latest_recipe_params_artifact(self) -> None:
        if not isinstance(self.latest_recipe_params_payload, dict):
            return
        path = self.run_dir / 'artifacts' / 'recipe_params.json'
        path.write_text(json.dumps(_json_safe(self.latest_recipe_params_payload), ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')

    def _recipe_group_decisions_path(self) -> Path:
        return self.run_dir / 'artifacts' / 'recipe_group_decisions.json'

    def _read_recipe_group_decisions_artifact(self) -> dict[str, Any] | None:
        if isinstance(self.latest_recipe_group_decisions_payload, dict):
            return self.latest_recipe_group_decisions_payload
        path = self._recipe_group_decisions_path()
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return None
        if isinstance(payload, dict):
            self.latest_recipe_group_decisions_payload = payload
            return payload
        return None

    def _write_recipe_group_decisions_artifact(self) -> None:
        if not isinstance(self.latest_recipe_group_decisions_payload, dict):
            return
        path = self._recipe_group_decisions_path()
        path.write_text(json.dumps(_json_safe(self.latest_recipe_group_decisions_payload), ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')

    def _recipe_params_draft_path(self) -> Path:
        return self.run_dir / 'artifacts' / 'recipe_params_draft.json'

    def _read_recipe_params_draft_artifact(self) -> dict[str, Any] | None:
        if isinstance(self.latest_recipe_params_draft_payload, dict):
            return self.latest_recipe_params_draft_payload
        path = self._recipe_params_draft_path()
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return None
        if isinstance(payload, dict):
            self.latest_recipe_params_draft_payload = payload
            return payload
        return None

    def _write_recipe_params_draft_artifact(self) -> None:
        if not isinstance(self.latest_recipe_params_draft_payload, dict):
            return
        path = self._recipe_params_draft_path()
        path.write_text(json.dumps(_json_safe(self.latest_recipe_params_draft_payload), ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')

    def _normalize_recipe_params_draft_rules(self, rules: Any) -> tuple[list[dict[str, Any]], str]:
        if rules is None:
            return [], ''
        if isinstance(rules, str):
            return [], 'invalid recipe_params draft rules: expected canonical object or array; JSON strings are not accepted'
        if isinstance(rules, dict) and ('recipe_params' in rules or 'rules' in rules):
            return [], 'invalid recipe_params draft rules: wrapper objects are not accepted; pass RecipeParamsRule[] or one RecipeParamsRule directly'
        if isinstance(rules, dict):
            rules = [rules]
        if not isinstance(rules, list):
            return [], 'recipe_params draft rules must be an array or a single rule object'
        normalized: list[dict[str, Any]] = []
        for index, rule in enumerate(rules, start=1):
            if not isinstance(rule, dict):
                return [], f'draft rules[{index - 1}] must be an object'
            try:
                _validate_recipe_params_rule_shape(rule, index=index, context='draft rules')
            except Exception as exc:
                return [], str(exc)
            normalized.append(_json_clone(rule))
        return normalized, ''

    def _normalize_recipe_group_decisions(self, decisions: Any) -> tuple[list[dict[str, Any]], str]:
        if decisions is None:
            return [], ''
        if isinstance(decisions, str):
            return [], 'invalid recipe group decisions: expected canonical object or array; JSON strings are not accepted'
        if isinstance(decisions, dict) and ('recipe_group_decisions' in decisions or 'decisions' in decisions):
            return [], 'invalid recipe group decisions: wrapper objects are not accepted; pass RecipeParamsRule[] or one RecipeParamsRule directly'
        if isinstance(decisions, dict):
            decisions = [decisions]
        if not isinstance(decisions, list):
            return [], 'recipe group decisions must be an array or a single decision object'
        normalized: list[dict[str, Any]] = []
        for index, decision in enumerate(decisions, start=1):
            if not isinstance(decision, dict):
                return [], f'decisions[{index - 1}] must be an object'
            try:
                _validate_recipe_params_rule_shape(decision, index=index, context='decisions')
            except Exception as exc:
                return [], str(exc)
            normalized.append(_json_clone(decision))
        return normalized, ''

    def _recipe_group_decision_name(self, decision: dict[str, Any], *, index: int) -> str:
        name = str(decision.get('name') or '').strip()
        if name:
            return name
        group_ref = str(_first_present(decision, keys=('group_ref',)) or '').strip()
        disposition = str(decision.get('disposition') or '').strip()
        target = str(decision.get('subject_id') or decision.get('episode_id') or '').strip()
        parts = [part for part in (group_ref, disposition or 'decision', target) if part]
        return ' '.join(parts) if parts else f'decision_{index}'

    def _recipe_params_draft_from_group_decisions(self, payload: dict[str, Any]) -> dict[str, Any]:
        rules: list[dict[str, Any]] = []
        diagnostics: list[str] = []
        decisions = payload.get('decisions') if isinstance(payload.get('decisions'), list) else []
        for index, decision in enumerate(decisions, start=1):
            if not isinstance(decision, dict):
                diagnostics.append(f'decisions[{index - 1}] is not an object')
                continue
            try:
                rules.extend(self._recipe_params_rules_from_group_decision(decision, index=index))
            except Exception as exc:
                diagnostics.append(f'{self._recipe_group_decision_name(decision, index=index)}: {type(exc).__name__}: {exc}')
                rules.append({
                    'name': self._recipe_group_decision_name(decision, index=index),
                    'reason': f'group decision could not compile: {type(exc).__name__}: {exc}',
                })
        draft: dict[str, Any] = {
            'version': int(payload.get('version') or 1),
            'summary': _string_or_default(payload.get('summary'), 'Pi group decisions compiled to recipe params draft.'),
            'rules': rules,
            'compiled_from_group_decisions': True,
        }
        if diagnostics:
            draft['compile_diagnostics'] = _dedupe_nonempty(diagnostics)
        return draft

    def _recipe_params_rules_from_group_decision(self, decision: dict[str, Any], *, index: int) -> list[dict[str, Any]]:
        name = self._recipe_group_decision_name(decision, index=index)
        selected_paths, selector_payload = self._selector_payload_from_group_decision(decision)
        target_fields = self._target_payload_from_group_decision(decision)
        episode_ids = [
            int(value)
            for value in (decision.get('episode_ids') or [])
            if self._optional_positive_int_for_draft(value)
        ]
        disposition = self._disposition_from_group_decision(decision, target_fields)
        base_rule: dict[str, Any] = {
            'name': name,
            **selector_payload,
            'disposition': disposition,
            'reason': _string_or_default(decision.get('reason'), ''),
        }
        source_unit = _string_or_default(decision.get('source_unit'), '')
        if source_unit:
            base_rule['source_unit'] = source_unit
        for key in ('episode_range', 'episode_offset', 'episode_number_field'):
            if decision.get(key) not in (None, ''):
                base_rule[key] = decision.get(key)
        if 'episode_range' not in base_rule:
            range_start = self._optional_positive_int_for_draft(decision.get('episode_range_start'))
            range_end = self._optional_positive_int_for_draft(decision.get('episode_range_end'))
            if range_start and range_end:
                base_rule['episode_range'] = f'{range_start}-{range_end}' if range_start != range_end else str(range_start)

        if disposition == 'map_to_bangumi':
            base_rule.update(target_fields)

        if episode_ids and selected_paths and len(episode_ids) == len(selected_paths):
            rules: list[dict[str, Any]] = []
            for item_index, (path, episode_id) in enumerate(zip(selected_paths, episode_ids), start=1):
                rule = dict(base_rule)
                rule['name'] = f'{name} #{item_index}'
                rule['exact_paths'] = [path]
                rule['episode_id'] = episode_id
                for field in ('episode_range', 'episode_offset', 'episode_number_field'):
                    rule.pop(field, None)
                rules.append(rule)
            return rules

        if selected_paths:
            has_compact_selector = bool(
                selector_payload.get('source_pattern')
                or selector_payload.get('filename_regex')
                or (selector_payload.get('group_ref') and not selector_payload.get('exact_paths'))
            )
            if not has_compact_selector:
                base_rule['exact_paths'] = selected_paths
            if 'episode_range' not in base_rule:
                number_range = self._selected_path_number_range(selected_paths)
                if number_range and len(selected_paths) > 1 and not base_rule.get('episode_id'):
                    base_rule['episode_range'] = number_range
        return [base_rule]

    def _selector_payload_from_group_decision(self, decision: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        exact_paths = _coerce_string_list(_first_present(decision, keys=('exact_paths',)))
        if exact_paths:
            selected = [self._canonicalize_exact_path(path) for path in exact_paths]
            return selected, {'exact_paths': selected}

        group_ref = _string_or_default(_first_present(decision, keys=('group_ref',)), '')
        source_pattern = _source_pattern_from_params(decision)
        if not group_ref:
            payload: dict[str, Any] = {}
            if source_pattern:
                payload['source_pattern'] = source_pattern
            for key in ('filename_regex', 'exclude_regex'):
                if decision.get(key) not in (None, ''):
                    payload[key] = decision.get(key)
            return [], payload

        group = self._find_local_group_payload(group_ref)
        if group is None:
            payload = {'group_ref': group_ref}
            if source_pattern:
                payload['source_pattern'] = source_pattern
            return [], payload
        selected = self._select_group_paths_from_decision(group, decision)
        if selected:
            if len(selected) > 1:
                selector_hint = group.get('selector_hint') if isinstance(group.get('selector_hint'), dict) else {}
                compact_source_pattern = source_pattern or str(selector_hint.get('source_pattern') or '')
                if compact_source_pattern:
                    return selected, {'group_ref': group.get('group_ref') or group_ref, 'source_pattern': compact_source_pattern}
            return selected, {'exact_paths': selected}
        payload = {'group_ref': group.get('group_ref') or group_ref}
        if source_pattern:
            payload['source_pattern'] = source_pattern
        return [], payload

    def _select_group_paths_from_decision(self, group: dict[str, Any], decision: dict[str, Any]) -> list[str]:
        paths_payload = group.get('source_paths') if isinstance(group.get('source_paths'), dict) else {}
        paths = [_norm_path(str(path)) for path in (paths_payload.get('all') or []) if _norm_path(str(path))]
        if not paths:
            representative = _norm_path(str(group.get('representative_source_path') or ''))
            paths = [representative] if representative else []
        if not paths:
            return []

        include_numbers = self._numbers_from_decision(decision, keys=('file_numbers',))
        include_range_numbers = self._numbers_from_range_value(_first_present(decision, keys=('file_number_range',)))
        include_numbers = sorted(set([*include_numbers, *include_range_numbers]))
        include_contains = _coerce_string_list(_first_present(decision, keys=('path_contains',)))
        exclude_contains = _coerce_string_list(_first_present(decision, keys=('exclude_path_contains',)))
        include_regex = _string_or_default(_first_present(decision, keys=('filename_regex',)), '')
        exclude_regex = _string_or_default(_first_present(decision, keys=('exclude_regex',)), '')

        has_filter = bool(include_numbers or include_contains or exclude_contains or include_regex or exclude_regex)
        if not has_filter:
            return []

        selected: list[str] = []
        include_re = re.compile(include_regex, re.IGNORECASE) if include_regex else None
        exclude_re = re.compile(exclude_regex, re.IGNORECASE) if exclude_regex else None
        for path in paths:
            basename = path.rsplit('/', 1)[-1]
            number = self._local_path_primary_number(path)
            if include_numbers and number not in include_numbers:
                continue
            if include_contains and not any(token.casefold() in path.casefold() or token.casefold() in basename.casefold() for token in include_contains):
                continue
            if include_re is not None and not (include_re.search(basename) or include_re.search(path)):
                continue
            if exclude_contains and any(token.casefold() in path.casefold() or token.casefold() in basename.casefold() for token in exclude_contains):
                continue
            if exclude_re is not None and (exclude_re.search(basename) or exclude_re.search(path)):
                continue
            selected.append(path)
        return selected

    def _target_payload_from_group_decision(self, decision: dict[str, Any]) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        for output_key in ('subject_id', 'media_kind', 'episode_id', 'episode_type', 'sort', 'ep'):
            value = _first_present(decision, keys=(output_key,))
            if value not in (None, ''):
                fields[output_key] = value
        return fields

    def _disposition_from_group_decision(self, decision: dict[str, Any], target_fields: dict[str, Any]) -> str:
        disposition = _string_or_default(decision.get('disposition'), '')
        lowered = disposition.casefold()
        if lowered == 'non_bangumi_or_supplemental':
            return 'non_bangumi_or_supplemental'
        if lowered in {'needs_more_evidence', 'unaligned_fail_closed'}:
            return lowered
        if lowered == 'map_to_bangumi' or target_fields:
            return 'map_to_bangumi'
        return 'non_bangumi_or_supplemental'

    def _numbers_from_decision(self, decision: dict[str, Any], *, keys: tuple[str, ...]) -> list[int]:
        raw = _first_present(decision, keys=keys)
        if raw is None:
            return []
        if isinstance(raw, str):
            range_numbers = self._numbers_from_range_value(raw)
            if range_numbers:
                return range_numbers
        values = raw if isinstance(raw, list) else [raw]
        numbers: list[int] = []
        for value in values:
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            numbers.append(number)
        return sorted(set(numbers))

    def _numbers_from_range_value(self, value: Any) -> list[int]:
        if value in (None, ''):
            return []
        if isinstance(value, list):
            numbers: list[int] = []
            for item in value:
                numbers.extend(self._numbers_from_range_value(item))
            return sorted(set(numbers))
        return _episode_range_numbers(str(value))

    def _local_path_primary_number(self, path: str) -> int | None:
        tokens = _skeleton_locator_tokens(_stem_for_path(path))
        if not tokens:
            return None
        return _safe_int(tokens[0].get('number'))

    def _selected_path_number_range(self, paths: list[str]) -> str:
        numbers = [number for number in (self._local_path_primary_number(path) for path in paths) if number is not None]
        if len(numbers) != len(paths):
            return ''
        ordered = sorted(set(numbers))
        if not ordered:
            return ''
        ranges: list[str] = []
        start = previous = ordered[0]
        for number in ordered[1:]:
            if number == previous + 1:
                previous = number
                continue
            ranges.append(str(start) if start == previous else f'{start}-{previous}')
            start = previous = number
        ranges.append(str(start) if start == previous else f'{start}-{previous}')
        return ','.join(ranges)

    def _relation_atlas_dir(self) -> Path:
        return self.run_dir / 'artifacts' / 'bangumi_relation_atlas'

    def _normalize_relation_atlas_id(self, atlas_id: str) -> str:
        text = str(atlas_id or '').strip()
        if not text:
            return ''
        return re.sub(r'[^A-Za-z0-9._-]+', '-', text).strip('-._')

    def _next_relation_atlas_id(self, anchor_subject_id: int) -> str:
        existing_ids = {
            path.stem
            for path in self._relation_atlas_dir().glob('*.json')
        }
        index = len(existing_ids) + 1
        while True:
            atlas_id = f'atlas-{index:03d}-subject-{int(anchor_subject_id or 0)}'
            if atlas_id not in existing_ids:
                return atlas_id
            index += 1

    def _relation_atlas_paths(self, atlas_id: str) -> tuple[Path, Path]:
        normalized = self._normalize_relation_atlas_id(atlas_id)
        base = self._relation_atlas_dir() / normalized
        return base.with_suffix('.json'), base.with_suffix('.md')

    def _relation_atlas_state_payload(self) -> dict[str, Any]:
        atlas_dir = self._relation_atlas_dir()
        atlas_paths = sorted(
            atlas_dir.glob('*.json'),
            key=lambda path: path.name,
        ) if atlas_dir.exists() else []
        return {
            'path': str(atlas_dir),
            'atlas_count': len(atlas_paths),
            'latest_atlas_ids': [path.stem for path in atlas_paths[-6:]],
            'policy': 'Bangumi relation atlas artifacts are evidence/workpaper surfaces, not fixed-layer semantic decisions.',
        }

    def _relation_path_text(self, path: list[dict[str, Any]]) -> str:
        if not path:
            return 'anchor'
        parts = []
        for edge in path:
            relation = str(edge.get('relation') or 'related')
            to_id = int(edge.get('to_subject_id') or 0)
            parts.append(f'{relation}->{to_id}')
        return ' / '.join(parts)

    def _episode_surface_summary(
        self,
        *,
        subject_id: int,
        item_cards: list[BangumiItemCard],
        total_available: int,
        limit: int,
    ) -> dict[str, Any]:
        counts: Counter[str] = Counter()
        samples: dict[str, list[dict[str, Any]]] = {}
        for card in sorted(item_cards, key=_episode_card_order_key):
            surface = self._recipe_episode_type_for_item(card)
            counts[surface] += 1
            bucket = samples.setdefault(surface, [])
            if len(bucket) >= 6:
                continue
            bucket.append({
                'episode_id': int(getattr(card, 'episode_id', 0) or 0),
                'episode_type': surface,
                'api_item_kind': str(getattr(card, 'item_kind', '') or ''),
                'api_type': str(getattr(card, 'type', '') or ''),
                'sort': int(getattr(card, 'sort', 0) or 0),
                'ep': int(getattr(card, 'ep', 0) or 0),
                'title': _compact_text(str(getattr(card, 'title', '') or getattr(card, 'name_cn', '') or getattr(card, 'name', '') or ''), limit=80),
                'name': _compact_text(str(getattr(card, 'name', '') or ''), limit=80),
                'name_cn': _compact_text(str(getattr(card, 'name_cn', '') or ''), limit=80),
                'duration': _compact_text(str(getattr(card, 'duration', '') or ''), limit=40),
                'source_form_hint': str(getattr(card, 'source_form_hint', '') or ''),
            })
        return {
            'subject_id': int(subject_id or 0),
            'hydrated': True,
            'row_count': int(total_available),
            'returned_row_count': len(item_cards),
            'row_limit': int(limit),
            'rows_limited': int(total_available) > len(item_cards),
            'row_surface_counts': dict(sorted(counts.items())),
            'samples': samples,
        }

    def _relation_atlas_markdown(self, atlas: dict[str, Any], *, max_chars: int = 30000) -> str:
        compact_subjects = []
        for subject in atlas.get('subjects') or []:
            if not isinstance(subject, dict):
                continue
            surface = subject.get('episode_surface') if isinstance(subject.get('episode_surface'), dict) else {}
            compact_subjects.append({
                'subject_id': subject.get('subject_id'),
                'title': subject.get('title'),
                'name': subject.get('name'),
                'name_cn': subject.get('name_cn'),
                'platform': subject.get('platform'),
                'date': subject.get('date'),
                'depth': subject.get('depth'),
                'relation_path_text': subject.get('relation_path_text'),
                'row_surface_counts': surface.get('row_surface_counts') or {},
                'row_count': surface.get('row_count'),
                'samples': surface.get('samples') or {},
            })
        text = '\n'.join([
            '# Bangumi Relation Atlas',
            '',
            f'atlas_id: {atlas.get("atlas_id")}',
            f'anchor_subject_id: {atlas.get("anchor_subject_id")}',
            f'subject_count: {atlas.get("subject_count")}',
            f'edge_count: {atlas.get("edge_count")}',
            '',
            '## Policy',
            str(atlas.get('atlas_policy') or ''),
            '',
            '## Traversal Status',
            '```json',
            json.dumps(_json_safe(atlas.get('traversal_status') or {}), ensure_ascii=False, indent=2, sort_keys=True),
            '```',
            '',
            '## Subjects',
            '```json',
            json.dumps(_json_safe(compact_subjects), ensure_ascii=False, indent=2, sort_keys=True),
            '```',
            '',
            '## Edges',
            '```json',
            json.dumps(_json_safe(atlas.get('edges') or []), ensure_ascii=False, indent=2, sort_keys=True),
            '```',
            '',
        ]).rstrip() + '\n'
        if len(text) <= max_chars:
            return text
        return text[:max_chars].rstrip() + '\n\n[atlas markdown truncated; read the sibling JSON atlas for complete facts]\n'

    def _recipe_group_decisions_state_payload(self, *, detail: bool = False) -> dict[str, Any]:
        payload = self._read_recipe_group_decisions_artifact()
        decisions = payload.get('decisions') if isinstance(payload, dict) and isinstance(payload.get('decisions'), list) else []
        draft_state = self._recipe_params_draft_state_payload(detail=detail)
        result: dict[str, Any] = {
            'exists': isinstance(payload, dict),
            'path': str(self._recipe_group_decisions_path()),
            'summary': str(payload.get('summary') or '') if isinstance(payload, dict) else '',
            'decision_count': len(decisions),
            'decision_names': [str(item.get('name') or '') for item in decisions if isinstance(item, dict)],
            'compiled_recipe_params_draft': {
                'exists': bool(draft_state.get('exists')),
                'rule_count': int(draft_state.get('rule_count') or 0),
                'ready_for_full_validation': bool(draft_state.get('ready_for_full_validation')),
                'coverage_preview': draft_state.get('coverage_preview'),
                'draft_quality_issue_count': int(draft_state.get('draft_quality_issue_count') or 0),
            },
            'decision_policy': 'Pi-owned group/subcluster decisions. Python expands local selectors and copies Pi-provided targets/dispositions into recipe_params_draft only.',
        }
        if detail:
            result['recipe_group_decisions'] = _json_safe(payload)
            result['recipe_params_draft'] = draft_state.get('recipe_params_draft')
        return result

    def _recipe_params_draft_state_payload(self, *, detail: bool = False) -> dict[str, Any]:
        draft = self._read_recipe_params_draft_artifact()
        coverage = self._recipe_params_draft_coverage(draft)
        coverage_payload = coverage if detail else self._compact_draft_coverage_payload(coverage)
        rules = draft.get('rules') if isinstance(draft, dict) and isinstance(draft.get('rules'), list) else []
        draft_quality_issues = coverage.get('draft_quality_issues') or []
        draft_quality_warnings = coverage.get('draft_quality_warnings') or []
        payload: dict[str, Any] = {
            'exists': isinstance(draft, dict),
            'path': str(self._recipe_params_draft_path()),
            'summary': str(draft.get('summary') or '') if isinstance(draft, dict) else '',
            'rule_count': len(rules),
            'rule_names': [str(rule.get('name') or '') for rule in rules if isinstance(rule, dict)],
            'coverage_preview': coverage_payload,
            'ready_for_full_validation': bool(coverage.get('ready_for_full_validation')),
            'draft_quality_issue_count': len(draft_quality_issues),
            'draft_quality_warning_count': len(draft_quality_warnings),
            'draft_policy': 'Pi-owned working draft. Coverage preview is local selector bookkeeping only; it is not semantic validation.',
            'draft_usage_hint': 'Partial drafts are expected. When Pi has judged one group, save that mapped or supplemental recipe row with upsert_recipe_params_draft even if other groups remain unresolved. Do not create local-only coverage rows without a target or disposition judgment. If a saved row is skeletal, complete/replace/remove it before using more broad evidence.',
        }
        if detail:
            payload['draft_quality_issues'] = draft_quality_issues
            payload['draft_quality_warnings'] = draft_quality_warnings
            payload['recipe_params_draft'] = _json_safe(draft)
        return payload

    def _recipe_params_draft_coverage(self, draft: dict[str, Any] | None) -> dict[str, Any]:
        group_paths_by_ref = self._local_group_paths_by_ref()
        all_group_refs = list(group_paths_by_ref.keys())
        path_to_group_refs: dict[str, list[str]] = {}
        for group_ref, paths in group_paths_by_ref.items():
            for path in paths:
                path_to_group_refs.setdefault(path, []).append(group_ref)
        visible_paths = set(self._visible_main_paths())
        rules = draft.get('rules') if isinstance(draft, dict) and isinstance(draft.get('rules'), list) else []
        covered_group_refs: set[str] = set()
        covered_paths: set[str] = set()
        unmatched_exact_paths: list[str] = []
        draft_warnings: list[str] = []
        draft_quality_issues: list[dict[str, Any]] = []
        draft_quality_warnings: list[dict[str, Any]] = []
        rule_coverages: list[dict[str, Any]] = []

        for index, rule in enumerate(rules, start=1):
            if not isinstance(rule, dict):
                draft_warnings.append(f'rules[{index - 1}] is not an object')
                draft_quality_issues.append({
                    'rule_index': index - 1,
                    'issue_code': 'rule_not_object',
                    'message': 'draft row must be an object',
                })
                continue
            rule_name = _string_or_default(rule.get('name'), f'rule_{index}')
            rule_group_refs: set[str] = set()
            rule_paths: set[str] = set()
            selector_kinds: list[str] = []
            rule_quality_issues, rule_quality_warnings = self._recipe_params_draft_rule_quality(rule, index=index)
            draft_quality_issues.extend(rule_quality_issues)
            draft_quality_warnings.extend(rule_quality_warnings)

            group_ref = _string_or_default(_first_present(rule, keys=('group_ref',)), '')
            if group_ref:
                selector_kinds.append('group_ref')
                canonical_group_ref = self._canonical_local_group_ref(group_ref)
                if canonical_group_ref:
                    rule_group_refs.add(canonical_group_ref)
                    rule_paths.update(group_paths_by_ref.get(canonical_group_ref, []))
                else:
                    draft_warnings.append(f'Rule {rule_name!r} references unknown group_ref {group_ref!r}')

            exact_paths = _coerce_string_list(_first_present(rule, keys=('exact_paths',)))
            if exact_paths:
                selector_kinds.append('exact_paths')
            for path in exact_paths:
                canonical_path = self._canonicalize_exact_path(path)
                if canonical_path in visible_paths:
                    rule_paths.add(canonical_path)
                    rule_group_refs.update(path_to_group_refs.get(canonical_path, []))
                else:
                    unmatched_exact_paths.append(path)

            source_pattern = _source_pattern_from_params(rule)
            filename_regex = _source_pattern_to_regex(source_pattern) if source_pattern else _string_or_default(_first_present(rule, keys=('filename_regex',)), '')
            if filename_regex and not exact_paths and not group_ref:
                selector_kinds.append('source_pattern' if source_pattern else 'filename_regex')
                try:
                    regex = re.compile(str(filename_regex or '').replace('(?<', '(?P<'), re.IGNORECASE)
                except re.error as exc:
                    draft_warnings.append(f'Rule {rule_name!r} has invalid filename_regex/source_pattern: {exc}')
                    regex = None
                if regex is not None:
                    for path in visible_paths:
                        basename = path.rsplit('/', 1)[-1]
                        if regex.search(basename) or regex.search(path):
                            rule_paths.add(path)
                            rule_group_refs.update(path_to_group_refs.get(path, []))

            covered_group_refs.update(rule_group_refs)
            covered_paths.update(rule_paths)
            if not rule_group_refs and not rule_paths:
                draft_warnings.append(f'Rule {rule_name!r} does not currently preview-cover any visible local group or exact path')
            rule_coverages.append({
                'rule_name': rule_name,
                'selector_kinds': selector_kinds,
                'covered_group_refs': sorted(rule_group_refs),
                'selected_path_count': len(rule_paths),
                'selected_path_sample': sorted(rule_paths)[:6],
            })

        missing_group_refs = [group_ref for group_ref in all_group_refs if group_ref not in covered_group_refs]
        uncovered_paths = sorted(path for path in visible_paths if path not in covered_paths)
        return {
            'visible_group_count': len(all_group_refs),
            'covered_group_refs': [group_ref for group_ref in all_group_refs if group_ref in covered_group_refs],
            'missing_group_refs': missing_group_refs,
            'covered_path_count': len(covered_paths),
            'visible_path_count': len(visible_paths),
            'uncovered_path_count': len(uncovered_paths),
            'uncovered_path_sample': uncovered_paths[:12],
            'unmatched_exact_paths': _dedupe_nonempty(unmatched_exact_paths),
            'draft_warnings': _dedupe_nonempty(draft_warnings),
            'draft_quality_issues': _dedupe_dicts(draft_quality_issues),
            'draft_quality_warnings': _dedupe_dicts(draft_quality_warnings),
            'draft_quality_policy': 'Mechanical draft completeness only. A row is testable when it has a local selector and either a Bangumi target or supplemental disposition; Python does not judge target semantics here.',
            'rule_coverages': rule_coverages,
            'local_coverage_complete': bool(all_group_refs) and not missing_group_refs,
            'path_coverage_complete': bool(visible_paths) and not uncovered_paths,
            'ready_for_full_validation': bool(all_group_refs) and not missing_group_refs and bool(visible_paths) and not uncovered_paths and not draft_quality_issues,
            'coverage_policy': 'Local draft coverage only. A covered group means Pi wrote a selector for it; Python has not checked target semantics or verifier legality.',
        }

    def _recipe_params_draft_rule_quality(self, rule: dict[str, Any], *, index: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        rule_name = _string_or_default(rule.get('name'), f'rule_{index}')
        issues: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []

        def add_issue(issue_code: str, message: str) -> None:
            issues.append({
                'rule_index': index - 1,
                'rule_name': rule_name,
                'issue_code': issue_code,
                'message': message,
            })

        def add_warning(issue_code: str, message: str) -> None:
            warnings.append({
                'rule_index': index - 1,
                'rule_name': rule_name,
                'issue_code': issue_code,
                'message': message,
            })

        group_ref = _string_or_default(_first_present(rule, keys=('group_ref',)), '')
        exact_paths = _coerce_string_list(_first_present(rule, keys=('exact_paths',)))
        source_pattern = _source_pattern_from_params(rule)
        filename_regex = _string_or_default(_first_present(rule, keys=('filename_regex',)), '')
        has_selector = bool(group_ref or exact_paths or source_pattern or filename_regex)
        if not has_selector:
            add_issue('missing_local_selector', 'draft row needs group_ref, exact_paths, source_pattern, or filename_regex')

        disposition = _string_or_default(rule.get('disposition'), '')
        effective_disposition = disposition or 'map_to_bangumi'
        valid_dispositions = {'map_to_bangumi', 'non_bangumi_or_supplemental', 'needs_more_evidence', 'unaligned_fail_closed'}
        if effective_disposition not in valid_dispositions:
            add_issue('invalid_disposition', f'disposition must be one of {sorted(valid_dispositions)}')
        elif effective_disposition in {'needs_more_evidence', 'unaligned_fail_closed'}:
            add_issue('unresolved_disposition', 'draft rows are testable mapped or supplemental rows; keep unresolved evidence gaps on the board until they become a rule or fail_closed')

        subject_id = self._optional_positive_int_for_draft(_first_present(rule, keys=('subject_id',)))
        episode_id = self._optional_positive_int_for_draft(_first_present(rule, keys=('episode_id',)))
        target_sort = self._optional_int_for_draft(_first_present(rule, keys=('sort',)))
        target_ep = self._optional_int_for_draft(_first_present(rule, keys=('ep',)))
        has_bangumi_target = bool(subject_id or episode_id or target_sort is not None or target_ep is not None)
        if effective_disposition == 'map_to_bangumi' and not has_bangumi_target:
            add_issue('missing_bangumi_target_or_supplemental_disposition', 'mapped draft row needs subject_id/episode_id/sort/ep, or set disposition to non_bangumi_or_supplemental')
        if effective_disposition == 'non_bangumi_or_supplemental' and has_bangumi_target:
            add_warning('supplemental_row_carries_target_fields', 'supplemental draft row does not need Bangumi target fields; remove them unless you intend a mapped rule')
        if not _string_or_default(rule.get('reason'), ''):
            add_warning('missing_reason', 'draft row should include one short evidence reason')

        return issues, warnings

    @staticmethod
    def _optional_positive_int_for_draft(value: Any) -> int:
        try:
            parsed = int(value or 0)
        except (TypeError, ValueError):
            return 0
        return parsed if parsed > 0 else 0

    @staticmethod
    def _optional_int_for_draft(value: Any) -> int | None:
        if value is None or value == '':
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _local_group_paths_by_ref(self) -> dict[str, list[str]]:
        visible_cards = [
            card for card in self.workspace.local_files
            if bool(getattr(card, 'is_main', False)) and _norm_path(str(getattr(card, 'path', '') or ''))
        ]
        buckets: dict[tuple[str, str, str], list[Any]] = {}
        for card in visible_cards:
            buckets.setdefault(_skeleton_group_key(card), []).append(card)

        groups: dict[str, list[str]] = {}
        for index, (_key, members) in enumerate(sorted(buckets.items(), key=_skeleton_bucket_sort_key), start=1):
            if index > 40:
                break
            group_ref = f'LG{len(groups) + 1}'
            paths = [
                _norm_path(str(getattr(member, 'path', '') or ''))
                for member in members
                if _norm_path(str(getattr(member, 'path', '') or ''))
            ]
            groups[group_ref] = paths
        return groups

    def _canonical_local_group_ref(self, group_ref: str) -> str:
        wanted = str(group_ref or '').strip().casefold()
        for existing in self._local_group_paths_by_ref().keys():
            if existing.casefold() == wanted:
                return existing
        return ''

    def _default_recipe_params_draft_validation_snapshot(self, draft: dict[str, Any], coverage: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for rule in draft.get('rules') or []:
            if not isinstance(rule, dict):
                continue
            rows.append({
                'rule': str(rule.get('name') or ''),
                'group_ref': str(_first_present(rule, keys=('group_ref',)) or ''),
                'disposition': str(rule.get('disposition') or 'map_to_bangumi'),
                'target_subject_id': _first_present(rule, keys=('subject_id',)),
                'reason': str(rule.get('reason') or ''),
            })
        return [{
            'source': 'recipe_params_draft',
            'ready_for_full_validation': bool(coverage.get('ready_for_full_validation')),
            'covered_group_refs': coverage.get('covered_group_refs', []),
            'rows': rows,
        }]

    def _recipe_payload_from_params(self, payload: dict[str, Any]) -> dict[str, Any]:
        unknown_top_keys = sorted(set(payload) - _ALLOWED_RECIPE_PARAMS_PAYLOAD_KEYS)
        if unknown_top_keys:
            raise ValueError(
                f'recipe_params uses non-canonical top-level field(s) {unknown_top_keys}; '
                f'allowed top-level fields: {sorted(_ALLOWED_RECIPE_PARAMS_PAYLOAD_KEYS)}'
            )
        rules = payload.get('rules')
        if not isinstance(rules, list) or not rules:
            raise ValueError('recipe_params.rules must be a non-empty array')
        return {
            'version': int(payload.get('version') or 1),
            'summary': _string_or_default(payload.get('summary'), 'Pi generated recipe parameters.'),
            'rules': [self._recipe_rule_payload_from_params(rule, index) for index, rule in enumerate(rules, start=1)],
        }

    def _recipe_rule_payload_from_params(self, rule: Any, index: int) -> dict[str, Any]:
        if not isinstance(rule, dict):
            raise ValueError(f'rules[{index - 1}] must be an object')
        _validate_recipe_params_rule_shape(rule, index=index, context='rules')
        disposition = _string_or_default(rule.get('disposition'), '')
        group_ref = _string_or_default(_first_present(rule, keys=('group_ref',)), '')
        group_selector_defaults = self._recipe_selector_defaults_for_group_ref(group_ref, index=index) if group_ref else {}
        source_pattern = _source_pattern_from_params(rule)
        exact_paths = _coerce_string_list(_first_present(rule, keys=('exact_paths',)))
        if group_selector_defaults and not exact_paths:
            if source_pattern:
                group_paths = self._local_group_paths_by_ref().get(self._canonical_local_group_ref(group_ref), [])
                if group_paths and not any(_source_pattern_matches(source_pattern, path) for path in group_paths):
                    raise ValueError(
                        f'rules[{index - 1}] combines group_ref {group_ref!r} with source_pattern that matches none of that group; '
                        'fix the source_pattern or use group_ref alone'
                    )
            if not source_pattern:
                source_pattern = str(group_selector_defaults.get('source_pattern') or '')
                exact_paths = _coerce_string_list(group_selector_defaults.get('exact_paths'))
        elif group_selector_defaults and exact_paths:
            source_pattern = ''
        if not exact_paths:
            literal_exact_paths = self._literal_exact_paths_from_source_pattern(source_pattern)
            if literal_exact_paths:
                exact_paths = literal_exact_paths
                source_pattern = ''
        subject_id = int(_first_present(rule, keys=('subject_id',)) or 0)
        episode_id = int(_first_present(rule, keys=('episode_id',)) or 0)
        episode_type = _episode_type_from_params(rule)
        if episode_id > 0:
            episode_type = self._recipe_episode_type_for_episode_id(episode_id, subject_id=subject_id) or episode_type
        filename_regex = (
            _source_pattern_to_regex(source_pattern)
            if source_pattern
            else _string_or_default(_first_present(rule, keys=('filename_regex',)), '')
        )
        episode_range = _episode_range_from_params(rule)
        episode_offset = _episode_offset_from_params(rule)
        episode_number_field = _episode_number_field_from_params(rule)
        group_source_pattern = str(group_selector_defaults.get('source_pattern') or '')
        if not episode_range and source_pattern and source_pattern == group_source_pattern:
            episode_range = _string_or_default(group_selector_defaults.get('episode_range'), '')
        if (
            _first_present(rule, keys=('episode_offset',)) is None
            and source_pattern
            and source_pattern == group_source_pattern
        ):
            episode_offset = _string_or_default(group_selector_defaults.get('episode_offset'), episode_offset)
        if (
            _first_present(rule, keys=('episode_number_field',)) is None
            and source_pattern
            and source_pattern == group_source_pattern
        ):
            episode_number_field = _string_or_default(group_selector_defaults.get('episode_number_field'), episode_number_field)
        if source_pattern and not exact_paths and disposition in {'', 'map_to_bangumi'} and not episode_id:
            episode_range, episode_offset = self._normalize_shifted_sequence_params(
                filename_regex=filename_regex,
                subject_id=subject_id,
                episode_type=episode_type,
                episode_range=episode_range,
                episode_offset=episode_offset,
                episode_number_field=episode_number_field,
            )
        return {
            'name': _string_or_default(rule.get('name'), f'rule_{index}'),
            'source_unit': _source_unit_from_params(rule, index=index),
            'select': {
                'path_glob': '**/*.mkv',
                'filename_regex': filename_regex,
                'exact_paths': [self._canonicalize_exact_path(path) for path in exact_paths],
                'exclude_regex': _string_or_default(_first_present(rule, keys=('exclude_regex',)), ''),
            },
            'target': {
                'bangumi_subject_id': subject_id,
                'media_kind': _media_kind_from_params(rule),
                'episode_id': episode_id,
                'episode_type': episode_type,
                'sort': _optional_int(_first_present(rule, keys=('sort',))),
                'ep': _optional_int(_first_present(rule, keys=('ep',))),
            },
            'episode': {
                'capture': 'ep',
                'offset': episode_offset,
                'range': episode_range,
                'number_field': episode_number_field,
            },
            'disposition': disposition or 'map_to_bangumi',
            'reason': _string_or_default(rule.get('reason'), ''),
        }

    def _reject_recipe_params_shape_errors(
        self,
        rule: dict[str, Any],
        *,
        index: int,
    ) -> None:
        _validate_recipe_params_rule_shape(rule, index=index, context='rules')

    def _normalize_shifted_sequence_params(
        self,
        *,
        filename_regex: str,
        subject_id: int,
        episode_type: str,
        episode_range: str,
        episode_offset: str,
        episode_number_field: str,
    ) -> tuple[str, str]:
        offset_delta = _simple_episode_offset_delta(episode_offset)
        if offset_delta is None or offset_delta == 0:
            return episode_range, episode_offset
        target_numbers = _episode_range_numbers(episode_range)
        if not target_numbers:
            return episode_range, episode_offset
        raw_numbers = self._matching_source_pattern_episode_numbers(filename_regex)
        if not raw_numbers or not _is_contiguous_numbers(raw_numbers):
            return episode_range, episode_offset
        if set(raw_numbers) & set(target_numbers):
            return episode_range, episode_offset
        inverted_target_numbers = [number - offset_delta for number in raw_numbers]
        if sorted(inverted_target_numbers) != sorted(target_numbers):
            return episode_range, episode_offset
        if not self._has_exposed_target_numbers(
            subject_id=subject_id,
            numbers=target_numbers,
            episode_type=episode_type,
            episode_number_field=episode_number_field,
        ):
            return episode_range, episode_offset
        return _numbers_to_episode_range(raw_numbers), _numeric_offset_to_expr(-offset_delta)

    def _matching_source_pattern_episode_numbers(self, filename_regex: str) -> list[int]:
        if not filename_regex:
            return []
        try:
            regex = re.compile(str(filename_regex or '').replace('(?<', '(?P<'), re.IGNORECASE)
        except re.error:
            return []
        numbers: list[int] = []
        for path in self._visible_main_paths():
            match = regex.search(path.rsplit('/', 1)[-1]) or regex.search(path)
            if match is None:
                continue
            raw_value = match.groupdict().get('ep') if 'ep' in match.groupdict() else (match.group(1) if match.groups() else None)
            try:
                numbers.append(int(str(raw_value).lstrip('0') or '0'))
            except (TypeError, ValueError):
                return []
        return sorted(set(numbers))

    def _has_exposed_target_numbers(
        self,
        *,
        subject_id: int,
        numbers: list[int],
        episode_type: str,
        episode_number_field: str,
    ) -> bool:
        if subject_id <= 0 or not numbers:
            return False
        wanted = set(numbers)
        visible: set[int] = set()
        for card in self.workspace.bangumi_items:
            if self._subject_id_for_item(card) != subject_id:
                continue
            if not _episode_type_matches_recipe(card, episode_type):
                continue
            value = getattr(card, 'ep', 0) if str(episode_number_field or 'sort') == 'ep' else getattr(card, 'sort', 0)
            try:
                number = int(value or 0)
            except (TypeError, ValueError):
                continue
            if number > 0:
                visible.add(number)
        return wanted.issubset(visible)

    def _recipe_episode_type_for_episode_id(self, episode_id: int, *, subject_id: int = 0) -> str:
        episode_id = int(episode_id or 0)
        subject_id = int(subject_id or 0)
        if episode_id <= 0:
            return ''
        for card in self.workspace.bangumi_items:
            if int(getattr(card, 'episode_id', 0) or 0) != episode_id:
                continue
            if subject_id > 0 and self._subject_id_for_item(card) != subject_id:
                continue
            return self._recipe_episode_type_for_item(card)
        return ''

    def _canonicalize_exact_path(self, source_path: str) -> str:
        _card, canonical_path, _original_path = self._resolve_local_file_path(source_path)
        return canonical_path or _norm_path(source_path)

    def _literal_exact_paths_from_source_pattern(self, source_pattern: str) -> list[str]:
        pattern = _norm_path(source_pattern)
        if (
            not pattern
            or _SOURCE_PATTERN_TOKEN_RE.search(pattern)
            or '*' in pattern
            or '?' in pattern
        ):
            return []
        _card, canonical_path, _original_path = self._resolve_local_file_path(pattern)
        if canonical_path and canonical_path in set(self._visible_main_paths()):
            return [canonical_path]
        return []

    def tool_fail_closed(
        self,
        reason: str,
        reason_kind: str = 'insufficient_evidence',
        related_refs: list[str] | None = None,
        allow_runner_budget_exhausted: bool = False,
    ) -> dict[str, Any]:
        reason_text = str(reason or '')
        reason_lower = reason_text.strip().casefold()
        kind_lower = str(reason_kind or '').strip().casefold()
        if (
            kind_lower == 'budget_exhausted'
            or reason_lower == 'budget_exhausted'
            or bool(allow_runner_budget_exhausted)
        ):
            return {
                'ok': False,
                'accepted': False,
                'error': 'budget_exhausted is runner-only; validate a best-effort recipe or use a semantic fail_closed reason.',
                'repair_hints': [
                    'Call validate_organize_recipe_params with the best supportable params before fail_closed.',
                    'If evidence is insufficient after validation, use reason_kind insufficient_evidence, contradiction, or unknown with a concrete reason.',
                ],
            }
        lifecycle_guard = self._fail_closed_lifecycle_guard(reason=reason_text, reason_kind=kind_lower)
        if lifecycle_guard is not None:
            return lifecycle_guard
        return self._finalize_fail_closed(reason=reason, reason_kind=reason_kind, related_refs=related_refs)

    def _fail_closed_lifecycle_guard(self, *, reason: str, reason_kind: str) -> dict[str, Any] | None:
        if self.final_result is not None:
            return None
        tool_names = [str(row.get('tool') or '') for row in self.tool_trace]
        evidence_seen = any(name in self._EVIDENCE_BATCH_TOOLS for name in tool_names)
        draft_state = self._recipe_params_draft_state_payload(detail=False)
        decision_state = self._recipe_group_decisions_state_payload(detail=False)
        atlas_state = self._relation_atlas_state_payload()
        draft_rule_count = int(draft_state.get('rule_count') or 0)
        decision_count = int(decision_state.get('decision_count') or 0)
        atlas_count = int(atlas_state.get('atlas_count') or 0)
        reason_lower = str(reason or '').casefold()
        no_work_terms = (
            'no stable',
            'no saved',
            'draft rule count is 0',
            'covered_groups=0',
            'missing_groups=',
            'no recipe artifact',
            'validation/submit cannot proceed',
            'no subject/episode evidence',
            'no bangumi evidence',
        )
        malformed_or_provider = any(
            token in reason_lower
            for token in ('malformed', 'unreadable', 'provider', 'auth', 'network', 'api failure')
        )
        if not evidence_seen and not malformed_or_provider and any(token in reason_lower for token in no_work_terms):
            return {
                'ok': False,
                'accepted': False,
                'error': 'fail_closed_requires_evidence',
                'status': 'needs_evidence',
                'reason_kind': reason_kind or 'insufficient_evidence',
                'repair_hints': [
                    'An empty draft or missing recipe artifact is not a semantic fail_closed reason.',
                    'For complex packages, first gather one reliable main-title Bangumi search, then call select_bangumi_anchor_subject.',
                    'If the case input is malformed or provider access failed, call fail_closed with that concrete blocker.',
                ],
                'lifecycle_policy': 'Python is not choosing targets; it only rejects fail_closed reasons that describe missing work rather than exhausted evidence.',
            }
        if (
            atlas_count > 0
            and draft_rule_count == 0
            and decision_count == 0
            and self.recipe_verifier_result is None
            and any(token in reason_lower for token in no_work_terms)
        ):
            return {
                'ok': False,
                'accepted': False,
                'error': 'fail_closed_requires_decision_or_concrete_evidence_gap',
                'status': 'needs_decision_or_targeted_gap',
                'reason_kind': reason_kind or 'insufficient_evidence',
                'repair_hints': [
                    'Atlas evidence is available, but no Pi-owned decision or draft row has been saved.',
                    'Save any stable target-surface judgment with upsert_recipe_group_decision_one; a partial row is useful.',
                    'If no row is stable, use one targeted subject/episode fact for the named unresolved group.',
                    'Only fail_closed with a concrete LG/source-path target-surface contradiction or exhausted evidence gap, not because the draft is empty.',
                ],
                'lifecycle_policy': 'Python does not decide mapping targets; this guard only keeps missing work from masquerading as semantic closure.',
                'atlas_count': atlas_count,
                'draft_rule_count': draft_rule_count,
                'recipe_group_decision_count': decision_count,
            }
        return None

    def _finalize_fail_closed(
        self,
        *,
        reason: str,
        reason_kind: str = 'insufficient_evidence',
        related_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        allowed = {'budget_exhausted', 'contradiction', 'insufficient_evidence', 'unknown'}
        kind = str(reason_kind or 'insufficient_evidence')
        if kind not in allowed:
            kind = 'unknown'
        final_output = CaseJudgeOutput(
            action='fail_closed',
            fail_closed_reasons=[
                FailClosedReason(
                    ref='FC_PI_1',
                    reason_kind=kind,
                    description=str(reason or ''),
                    related_refs=[str(ref) for ref in (related_refs or []) if str(ref)],
                )
            ],
            summary=str(reason or 'Pi failed closed.'),
        )
        self.final_result = {
            'ok': True,
            'status': 'fail_closed',
            'summary': final_output.summary,
            'final_action': 'fail_closed',
            'final_output': final_output.model_dump(mode='json'),
            'final_verifier_result': CaseVerifierResult(passed=True, issues=[], summary='fail_closed').model_dump(mode='json'),
            'accounting': {},
            'expanded_assignment_count': 0,
        }
        self._write_final_result()
        return {'ok': True, 'accepted': True, 'status': 'fail_closed', 'summary': final_output.summary}

    def _case_context_payload(self, *, detail: bool = False, include_startup_evidence: bool = True) -> dict[str, Any]:
        dossier = self.workspace.to_dossier(round_context='pi_context')
        bounded = build_bounded_case_dossier(dossier)
        if not detail:
            return {
                'case_id': self.case_id,
                'task_source_path': self.source_path,
                'counts': bounded.counts,
                'run_progress': self._run_progress_payload(),
                'case_overview': self._case_overview_payload(),
                'navigation': self._case_navigation_payload(),
                'local_group_index': self._local_group_index_payload(),
                'bangumi_seen': self._case_overview_payload().get('bangumi_seen', {}),
                'recipe_state': self._recipe_state_payload(detail=False),
                'startup_evidence_locations': self._startup_evidence_locations(),
                'context_policy': 'Bounded navigation context. Use group/detail/scaffold/evidence tools to expand only the layers Pi chooses.',
            }
        local_structure_summary = _local_structure_summary(list(self.workspace.local_files))
        local_recipe_skeleton = self._local_recipe_skeleton_payload()
        local_recipe_params_scaffold = self._local_recipe_params_scaffold_payload()
        payload = {
            'case_id': self.case_id,
            'task_source_path': self.source_path,
            'counts': bounded.counts,
            'run_progress': self._run_progress_payload(),
            'local_structure_summary': local_structure_summary,
            'local_recipe_params_scaffold': local_recipe_params_scaffold,
            'local_files': [self._local_file_payload(card, detail=detail) for card in self.workspace.local_files if bool(getattr(card, 'is_main', False))],
            'bangumi_subjects': [self._subject_payload(card) for card in self.workspace.bangumi_subjects],
            'bangumi_episodes': [self._episode_payload(card) for card in self.workspace.bangumi_items[:240]],
            'recipe_contract': {
                'identity_policy': 'Use real source_path strings for local files and Bangumi subject_id/episode_id/type/sort/ep for targets.',
                'final_tools': ['get_local_recipe_params_scaffold', 'select_bangumi_anchor_subject', 'build_bangumi_relation_atlas', 'upsert_recipe_group_decision_one', 'upsert_recipe_group_decision', 'get_recipe_group_decisions', 'upsert_recipe_params_draft', 'get_recipe_params_draft', 'validate_recipe_params_draft', 'validate_organize_recipe_params', 'validate_organize_recipe_params_patch', 'submit_organize_recipe_params', 'submit_organize_recipe_params_patch', 'fail_closed'],
                'validation_policy': 'validate_organize_recipe_params is a trial/checkpoint tool: it compiles semantic params, hydrates declared evidence when possible, returns verifier issues or review warnings, and does not finalize the case.',
                'submission_policy': 'submit_organize_recipe_params is the finalization path after accepted validation and resolved review warnings.',
                'scaffold_policy': 'local_recipe_params_scaffold is local selector/range scaffolding only; Pi must fill Bangumi target fields or supplemental disposition from evidence.',
            },
        }
        if include_startup_evidence:
            payload['local_recipe_skeleton'] = local_recipe_skeleton
        else:
            payload['startup_evidence_locations'] = {
                'local_recipe_skeleton': 'case_input.local_recipe_skeleton (selector and verifier-repair aid; not a startup semantic checklist)',
            }
        if detail:
            payload['last_invalid_submission'] = _json_safe(self.last_invalid_submission)
            payload['current_organize_recipe'] = self.organize_recipe.model_dump(mode='json') if self.organize_recipe is not None else None
            payload['current_compiled_plan'] = self.compiled_plan.model_dump(mode='json') if self.compiled_plan is not None else None
        return payload

    def _next_request_ref(self, prefix: str) -> str:
        self._request_index += 1
        return f'REQ_PI_{prefix}_{self._request_index}'

    def _case_board_note_next_action(self, section_type: str, next_action: str = '') -> dict[str, Any]:
        normalized = str(section_type or '').strip().casefold()
        next_text = str(next_action or '').strip()
        if normalized == 'validation snapshot':
            return {
                'next_tool': 'validate_organize_recipe_params',
                'instruction': 'Validation Snapshot is committed from a board note. In new work, pass validation_snapshot directly to validate_organize_recipe_params so the board note and verifier run happen in one transaction.',
            }
        if normalized == 'verifier delta':
            return {
                'next_tool': 'validate_organize_recipe_params_patch',
                'instruction': 'Verifier Delta is committed. Patch the named issue rows/rules first, or fetch only targeted evidence explicitly named by the verifier/review feedback.',
            }
        if normalized == 'patch delta':
            return {
                'next_tool': 'validate_organize_recipe_params_patch',
                'instruction': 'Patch Delta is committed. Validate the patch or submit the same patch only if it was already accepted by patch validation.',
            }
        if normalized == 'submit snapshot':
            return {
                'next_tool': 'submit_organize_recipe_params',
                'instruction': 'Submit Snapshot is committed. Submit the accepted params/recipe next; after accepted submit, call goal_complete.',
            }
        if normalized == 'initial board':
            return {
                'next_tool': '',
                'instruction': next_text or 'Initial Board is committed. Continue with the board next action, usually one main anchor search plus graph/episode evidence.',
            }
        if normalized == 'board delta':
            return {
                'next_tool': '',
                'instruction': next_text or 'Board Delta is committed. Continue with the named next action; if every group now has a writable rule, validate params with validation_snapshot in the same tool call.',
            }
        return {
            'next_tool': '',
            'instruction': next_text or 'Board note committed. Continue from the latest board state.',
        }

    def _case_board_next_action(
        self,
        *,
        status: str,
        verifier_result: CaseVerifierResult,
        review_warnings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        issues = [_json_safe(issue) for issue in (getattr(verifier_result, 'issues', []) or [])]
        warning_refs = [
            str(warning.get('source_path') or warning.get('code') or '')
            for warning in review_warnings
            if isinstance(warning, dict) and str(warning.get('source_path') or warning.get('code') or '')
        ]
        issue_refs = [
            str(issue.get('ref') or issue.get('issue_code') or '')
            for issue in issues
            if isinstance(issue, dict) and str(issue.get('ref') or issue.get('issue_code') or '')
        ]
        normalized_status = str(status or '').strip().casefold()
        if normalized_status == 'accepted':
            return {
                'section_type': 'Submit Snapshot',
                'next_tool': 'submit_organize_recipe_params',
                'instruction': 'Submit the same accepted params with submit_snapshot in the same tool call. Accepted validation does not finalize the case.',
                'issue_refs': [],
                'review_warning_refs': [],
            }
        if issues:
            return {
                'section_type': 'Verifier Delta',
                'next_tool': 'validate_organize_recipe_params_patch',
                'instruction': 'Verifier Delta was auto-recorded for this result. Patch blocking verifier issues first with patch_delta; use targeted evidence only when an issue or review warning names the needed fact.',
                'issue_refs': issue_refs[:12],
                'review_warning_refs': warning_refs[:12],
                'blocking_issue_count': len(issues),
                'review_warning_count': len(review_warnings),
            }
        if review_warnings:
            return {
                'section_type': 'Verifier Delta',
                'next_tool': 'validate_organize_recipe_params',
                'instruction': 'Verifier Delta was auto-recorded for review warnings. Gather only the named targeted evidence or adjust the named rule, then validate again with validation_snapshot.',
                'issue_refs': [],
                'review_warning_refs': warning_refs[:12],
                'blocking_issue_count': 0,
                'review_warning_count': len(review_warnings),
            }
        return {
            'section_type': 'Verifier Delta',
            'next_tool': 'validate_organize_recipe_params_patch',
            'instruction': 'Verifier Delta was auto-recorded for the latest feedback. Choose patch, targeted evidence, fail_closed, or submit from that feedback.',
            'issue_refs': issue_refs[:12],
            'review_warning_refs': warning_refs[:12],
            'blocking_issue_count': len(issues),
            'review_warning_count': len(review_warnings),
        }

    def _compact_result_summary(self, result: Any) -> dict[str, Any]:
        if not isinstance(result, dict):
            return {'type': type(result).__name__}
        keys = ['ok', 'accepted', 'status', 'error', 'summary', 'returncode', 'expanded_assignment_count']
        summary = {key: result.get(key) for key in keys if key in result}
        if 'repair_hints' in result and isinstance(result['repair_hints'], list):
            summary['repair_hint_count'] = len(result['repair_hints'])
        if 'review_warnings' in result and isinstance(result['review_warnings'], list):
            summary['review_warning_count'] = len(result['review_warnings'])
        if 'batch' in result and isinstance(result['batch'], dict):
            summary['batch_status'] = result['batch'].get('status')
            summary['request_count'] = len(result['batch'].get('request_results') or [])
        if 'verifier_result' in result and isinstance(result['verifier_result'], dict):
            summary['verifier_passed'] = result['verifier_result'].get('passed')
            summary['verifier_issue_count'] = len(result['verifier_result'].get('issues') or [])
        if 'section_type' in result:
            summary['section_type'] = result.get('section_type')
        if 'char_count' in result:
            summary['char_count'] = result.get('char_count')
        if 'total_chars' in result:
            summary['total_chars'] = result.get('total_chars')
        if 'case_board_next_action' in result and isinstance(result['case_board_next_action'], dict):
            summary['case_board_next_section'] = result['case_board_next_action'].get('section_type')
            summary['case_board_next_tool'] = result['case_board_next_action'].get('next_tool')
        if 'case_board_transaction' in result and isinstance(result['case_board_transaction'], dict):
            summary['case_board_transaction_sections'] = [
                str(note.get('section_type') or key)
                for key, note in result['case_board_transaction'].items()
                if isinstance(note, dict)
            ]
        if 'board_next_action' in result and isinstance(result['board_next_action'], dict):
            summary['board_next_tool'] = result['board_next_action'].get('next_tool')
        if 'recipe_params_draft_next_action' in result and isinstance(result['recipe_params_draft_next_action'], dict):
            summary['recipe_params_draft_next_tool'] = result['recipe_params_draft_next_action'].get('next_tool')
        if 'workpaper_checkpoint' in result and isinstance(result['workpaper_checkpoint'], dict):
            summary['workpaper_checkpoint_next_tool'] = result['workpaper_checkpoint'].get('next_tool')
            summary['evidence_calls_since_workpaper'] = result['workpaper_checkpoint'].get('evidence_calls_since_workpaper')
            summary['semantic_evidence_calls_since_workpaper'] = result['workpaper_checkpoint'].get('semantic_evidence_calls_since_workpaper')
        if 'workpaper_advisory' in result and isinstance(result['workpaper_advisory'], dict):
            summary['workpaper_advisory_next_tool'] = result['workpaper_advisory'].get('next_tool')
            summary['workpaper_advisory_next_tools'] = result['workpaper_advisory'].get('next_tools')
            summary['evidence_calls_since_workpaper'] = result['workpaper_advisory'].get('evidence_calls_since_workpaper')
        if 'anchor_atlas_next_action' in result and isinstance(result['anchor_atlas_next_action'], dict):
            summary['anchor_atlas_next_tool'] = result['anchor_atlas_next_action'].get('next_tool')
            summary['anchor_atlas_candidate_subject_ids'] = result['anchor_atlas_next_action'].get('candidate_subject_ids_from_current_facts')
        if 'atlas_id' in result:
            summary['bangumi_relation_atlas_id'] = result.get('atlas_id')
        if 'selected_anchor_subject_id' in result:
            summary['selected_anchor_subject_id'] = result.get('selected_anchor_subject_id')
        if 'atlas_path' in result:
            summary['bangumi_relation_atlas_path'] = result.get('atlas_path')
        if 'subject_count' in result:
            summary['atlas_subject_count'] = result.get('subject_count')
        if 'traversal_status' in result and isinstance(result.get('traversal_status'), dict):
            status = result.get('traversal_status') or {}
            summary['atlas_frontier_exhausted'] = status.get('frontier_exhausted')
            summary['atlas_stop_reason'] = status.get('stop_reason')
        if 'decision_count' in result:
            summary['recipe_group_decision_count'] = result.get('decision_count')
        if 'compiled_recipe_params_draft' in result and isinstance(result['compiled_recipe_params_draft'], dict):
            compiled = result['compiled_recipe_params_draft']
            summary['recipe_group_decision_compiled_rule_count'] = compiled.get('rule_count')
            summary['recipe_group_decision_compiled_ready'] = compiled.get('ready_for_full_validation')
        if 'rule_count' in result:
            summary['recipe_params_draft_rule_count'] = result.get('rule_count')
        if 'ready_for_full_validation' in result:
            summary['recipe_params_draft_ready'] = result.get('ready_for_full_validation')
        if 'coverage_preview' in result and isinstance(result['coverage_preview'], dict):
            summary['draft_missing_group_count'] = len(result['coverage_preview'].get('missing_group_refs') or [])
            summary['draft_covered_group_count'] = len(result['coverage_preview'].get('covered_group_refs') or [])
            summary['draft_uncovered_path_count'] = result['coverage_preview'].get('uncovered_path_count')
            summary['draft_quality_issue_count'] = len(result['coverage_preview'].get('draft_quality_issues') or [])
            summary['draft_quality_warning_count'] = len(result['coverage_preview'].get('draft_quality_warnings') or [])
        return summary

    def _visible_main_paths(self) -> list[str]:
        return [
            _norm_path(str(getattr(card, 'path', '') or ''))
            for card in self.workspace.local_files
            if bool(getattr(card, 'is_main', False)) and _norm_path(str(getattr(card, 'path', '') or ''))
        ]

    def _local_card_by_path(self) -> dict[str, Any]:
        return {
            _norm_path(str(getattr(card, 'path', '') or '')): card
            for card in self.workspace.local_files
            if _norm_path(str(getattr(card, 'path', '') or ''))
        }

    def _targeted_evidence_paths(self) -> set[str]:
        paths: set[str] = set()
        for row in self.tool_trace:
            if str(row.get('tool') or '') != 'find_bangumi_targets_for_local_file':
                continue
            arguments = row.get('arguments') if isinstance(row.get('arguments'), dict) else {}
            source_path = _norm_path(str(arguments.get('source_path') or ''))
            if source_path:
                paths.add(source_path)
        return paths

    def _recipe_review_warnings(self, plan: CompiledOrganizePlan) -> list[dict[str, Any]]:
        warnings: list[dict[str, Any]] = []
        local_by_path = self._local_card_by_path()
        targeted_paths = self._targeted_evidence_paths()
        sequence_targeted_paths = self._sequence_targeted_review_paths(plan, targeted_paths)
        for assignment in plan.assignments:
            if assignment.disposition != 'non_bangumi_or_supplemental':
                continue
            source_path = _norm_path(assignment.source_path)
            if not source_path or source_path in targeted_paths:
                continue
            if source_path in sequence_targeted_paths:
                continue
            local = local_by_path.get(source_path)
            duration = _duration_seconds_for_card(local)
            if duration is None or duration < _REVIEW_LONG_EXCLUDED_SECONDS:
                continue
            if _looks_like_obvious_extra_for_review(source_path, assignment.reason):
                continue
            warnings.append({
                'severity': 'review',
                'code': 'long_supplemental_without_targeted_evidence',
                'source_path': source_path,
                'message': 'A long visible file is covered as non_bangumi_or_supplemental before targeted evidence for that exact source_path was recorded.',
                'metrics': {'duration_seconds': duration},
                'repair_hint': f'For {source_path}, call find_bangumi_targets_for_local_file with this exact source_path, or map it if Bangumi evidence appears. If the targeted lookup still exposes no supportable anime target, validate the same supplemental rule again.',
            })
        return warnings

    def _sequence_targeted_review_paths(self, plan: CompiledOrganizePlan, targeted_paths: set[str]) -> set[str]:
        by_rule_and_sequence: dict[tuple[str, tuple[str, str, str]], list[tuple[str, int]]] = {}
        for assignment in plan.assignments:
            if assignment.disposition != 'non_bangumi_or_supplemental':
                continue
            source_path = _norm_path(assignment.source_path)
            sequence = _review_sequence_key_and_number(source_path)
            if source_path and sequence is not None:
                sequence_key, number = sequence
                by_rule_and_sequence.setdefault((str(assignment.rule_name or ''), sequence_key), []).append((source_path, number))

        covered: set[str] = set()
        for members in by_rule_and_sequence.values():
            if len(members) < 2:
                continue
            paths = [path for path, _number in members]
            if not any(path in targeted_paths for path in paths):
                continue
            numbers = [number for _path, number in members]
            if len(set(numbers)) != len(numbers) or not _is_contiguous_numbers(numbers):
                continue
            covered.update(paths)
        return covered

    def _resolve_local_file_path(self, source_path: str) -> tuple[Any | None, str, str]:
        normalized_path = _norm_path(source_path)
        if not normalized_path:
            return None, '', ''
        local_card = next(
            (card for card in self.workspace.local_files if _norm_path(str(getattr(card, 'path', '') or '')) == normalized_path),
            None,
        )
        if local_card is not None:
            return local_card, normalized_path, ''
        basename = normalized_path.rsplit('/', 1)[-1]
        matches = [path for path in self._visible_main_paths() if path.rsplit('/', 1)[-1] == basename]
        if len(matches) != 1:
            return None, normalized_path, ''
        canonical_path = matches[0]
        local_card = next(
            (card for card in self.workspace.local_files if _norm_path(str(getattr(card, 'path', '') or '')) == canonical_path),
            None,
        )
        if local_card is None:
            return None, normalized_path, ''
        return local_card, canonical_path, normalized_path

    def _recipe_repair_hints(self, verifier_result: CaseVerifierResult) -> list[str]:
        visible_paths = self._visible_main_paths()
        hints: list[str] = []
        for issue in verifier_result.issues:
            code = str(getattr(issue, 'issue_code', '') or '')
            if code == 'zero_match':
                hints.append(f'Rule matched no visible files. Use exact_paths from visible source_path values or fix selector. For groups already listed in case_input.local_recipe_skeleton, copy selector_hint.source_pattern exactly instead of hand-writing a regex-like pattern; then set episode_range to the local captured numbers and episode_offset to shift to Bangumi rows if needed. Visible source_path sample: {visible_paths[:8]}')
            elif code == 'unknown_exact_path':
                hints.append(f'exact_paths must use source_path exactly as shown in case context. Visible source_path sample: {visible_paths[:8]}')
            elif code == 'uncovered_path':
                related = list(getattr(issue, 'related_refs', []) or [])
                skeleton_hint = self._repair_hint_for_uncovered_paths(related)
                supplemental_gap_hint = self._repair_hint_for_existing_supplemental_group(related)
                hints.append(f'Every visible main source_path must be covered exactly once. Add or repair one rule for: {related or visible_paths[:8]}. If the missing path belongs to a local group that already has a partial supplemental/exact rule, patch or replace that existing rule so one rule covers the intended group exactly once; do not edit unrelated mapped movie/OVA/special exact rules just to cover the missing path. Do not append an overlapping duplicate rule. If this came from a mapped sequence source_pattern that matched only one file, replace changing release tokens such as CRC/hash/checksum/bracket IDs with a wildcard placeholder like {{hash}} or {{a}}, then validate again. {supplemental_gap_hint} {skeleton_hint}'.strip())
            elif code == 'duplicate_coverage':
                hints.append('A source_path matched more than one rule. Narrow selectors, remove the overlapping rule, or replace a partial supplemental/exact rule with one rule that covers the intended local group exactly once. Do not append a second supplemental rule over paths already covered by an earlier rule.')
            elif code == 'duplicate_target':
                related = list(getattr(issue, 'related_refs', []) or [])
                target = str(getattr(issue, 'ref', '') or 'the same target')
                hints.extend(self._duplicate_target_shape_hints(related))
                hints.append(f'Duplicate Bangumi target {target} is used by these source paths: {related[:6]}. Repair only the affected rules, then validate again. If one affected rule is a multi-file group_ref/source_pattern/exact_paths selector with a fixed episode_id/sort/ep, do not re-search first: patch that rule by unsetting the fixed locator so targets derive from {{ep}}, or replace it with separate exact_path rules that use distinct exposed subject_id/episode_id values. If the affected paths are adjacent numbered files with the same filename template, prefer one source_pattern rule with {{ep}} plus an episode_range/episode_number_field that derives distinct targets from the file numbers. If the duplicate comes from local split/variant locators such as _1/_2, part markers, or version suffixes and no distinct exposed Bangumi target row exists, exclude just those split/variant paths from the mapped sequence and cover them with disposition:"non_bangumi_or_supplemental", then validate a patch; do not fail_closed the whole case for this mechanical duplicate before trying that repair. If a main movie/episode exact file and an SPs/bonus-folder file duplicate the same target, leave the main exact rule intact and patch the side-folder file to a distinct exposed special/side row or to supplemental. For movie or one-file exact rules, replace the incorrect file with a distinct exposed subject_id/episode_id or fail closed if evidence is insufficient.')
            elif code == 'missing_target_episode':
                related = list(getattr(issue, 'related_refs', []) or [])
                special_bonus_hint = self._repair_hint_for_special_bonus_missing_target(related)
                hints.append(f'Target episode is not visible to the verifier for {related[:4] or str(getattr(issue, "ref", "") or "this rule")}. If you already fetched episode rows or target details for this subject in the current run, patch the rule fields first instead of fetching more evidence: check whether episode_id belongs to the selected subject, whether episode_type matches the exposed row, and whether sequence numbers should use sort, ep, or an EP offset. Fetch the smallest missing evidence with find_bangumi_targets_for_local_file, get_episode_list, get_target_window, or get_target_detail only when the row evidence is genuinely absent. If the subject has matching rows but the recipe still misses them, check episode_type: SP filenames and media_kind:"sp" do not imply episode_type:"special"; use the Bangumi row type, often regular, before converting the group to supplemental. For sequence rules, compare the local file number with Bangumi episode sort and ep values: keep episode_number_field:"sort" with offset EP when sort matches local numbering; use episode_number_field:"ep" when local numbering matches Bangumi ep but sort continues across an earlier season/cour; use arithmetic offsets only when the target number field is correct but shifted. If the chosen subject lacks the needed rows, split to a related season/cour/part subject. {special_bonus_hint}'.strip())
            elif code == 'missing_subject_id':
                hints.append('Mapped rules need target.bangumi_subject_id from Bangumi evidence.')
            elif code == 'unknown_subject_id':
                related = list(getattr(issue, 'related_refs', []) or [])
                source_paths = [ref for ref in related if not str(ref).startswith('subject:')]
                subject_refs = [ref for ref in related if str(ref).startswith('subject:')]
                hints.append(f'Unexposed {subject_refs[0] if subject_refs else "bangumi_subject_id"} cannot be used; do not invent subject IDs. For the affected source_path {source_paths[:2] or str(getattr(issue, "ref", "") or "")}, fetch targeted evidence with find_bangumi_targets_for_local_file, search/lookup, or relation graph, replace the ID with an exposed subject_id, then validate again.')
            elif code == 'missing_episode_locator':
                hints.append('For ordinary multi-file runs, add source_pattern/filename_regex with an episode capture and offset. For one exact file that intentionally covers multiple Bangumi episodes, keep the exact path and use source_unit: "single_file_multi_episode" with episode_range; do not degrade it to the first episode_id.')
            elif code == 'invalid_source_unit_selector':
                hints.append('source_unit: "single_file_multi_episode" is only for exactly one visible source_path. Use one exact_paths entry for the merged file, or use an ordinary source_pattern rule for multi-file sequences.')
            elif code == 'invalid_multi_episode_target_locator':
                hints.append('For source_unit: "single_file_multi_episode", remove episode_id/sort/ep and keep subject_id plus episode_range so the verifier can cover every episode in the span.')
            elif code == 'invalid_episode_range':
                hints.append('For source_unit: "single_file_multi_episode", episode_range must name at least two target episode sort numbers, such as "1-3"; apply episode_offset only when the Bangumi sort values restart.')
            elif code == 'missing_multi_episode_evidence':
                hints.append('A single file covering multiple episodes needs mechanical evidence. Check local container_facts for chapter_count/chapter_durations, an explicit filename episode range such as [01-03] matching episode_range, or local duration close to the sum of exposed Bangumi episode durations; if those facts are absent or contradictory, fail_closed instead of mapping it to episode 1.')
            elif code in {'invalid_filename_regex', 'invalid_exclude_regex', 'invalid_episode_offset', 'invalid_episode_capture', 'episode_locator_miss', 'episode_out_of_range'}:
                hints.append('Fix the selector/episode expression, then validate again. episode_offset accepts only EP arithmetic such as EP, EP-10, or EP*2-1; do not use SP as episode_offset. SP belongs in the filename selector or content evidence. Use source_pattern only for repeated file groups with a numeric {ep} capture. For a single movie/OVA/SP/special file, use exact_paths instead of source_pattern/episode_range.')
            elif code == 'unresolved_assignment':
                hints.append('Accepted recipes cannot contain needs_more_evidence or unaligned_fail_closed. Either resolve the path or call fail_closed for the whole case.')
        return _dedupe_nonempty(hints)

    def _repair_hint_for_existing_supplemental_group(self, paths: list[str]) -> str:
        if not isinstance(self.latest_recipe_params_payload, dict):
            return ''
        rules = self.latest_recipe_params_payload.get('rules')
        if not isinstance(rules, list):
            return ''
        target_groups: dict[str, list[str]] = {}
        for path in paths:
            group = self._local_skeleton_group_for_path(path)
            group_ref = str(group.get('group_ref') or '') if isinstance(group, dict) else ''
            if group_ref:
                target_groups.setdefault(group_ref, []).append(path)
        if not target_groups:
            return ''
        supplemental_rules_by_group: dict[str, list[str]] = {group_ref: [] for group_ref in target_groups}
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            disposition = _string_or_default(rule.get('disposition'), '')
            if disposition != 'non_bangumi_or_supplemental':
                continue
            rule_group_ref = _string_or_default(
                _first_present(rule, keys=('group_ref',)),
                '',
            )
            rule_name = _string_or_default(rule.get('name'), 'supplemental rule')
            if rule_group_ref in supplemental_rules_by_group:
                supplemental_rules_by_group[rule_group_ref].append(rule_name)
                continue
            exact_paths = _coerce_string_list(_first_present(rule, keys=('exact_paths',)))
            for exact_path in exact_paths:
                group = self._local_skeleton_group_for_path(exact_path)
                group_ref = str(group.get('group_ref') or '') if isinstance(group, dict) else ''
                if group_ref in supplemental_rules_by_group:
                    supplemental_rules_by_group[group_ref].append(rule_name)
        parts: list[str] = []
        for group_ref, rule_names in supplemental_rules_by_group.items():
            unique_rule_names = _dedupe_nonempty(rule_names)
            if not unique_rule_names:
                continue
            parts.append(
                f'Local group {group_ref} already has supplemental rule(s) {unique_rule_names[:4]} covering sibling paths. '
                f'Patch that supplemental rule to include the missing exact path(s), or replace it with one group_ref/selector supplemental rule for the intended group; leave unrelated mapped exact-path rules unchanged.'
            )
        return ' '.join(parts)

    def _duplicate_target_shape_hints(self, related_paths: list[str]) -> list[str]:
        if not self.compiled_plan or not self.organize_recipe:
            return []
        related_norms = {_norm_path(path) for path in related_paths if _norm_path(path)}
        if not related_norms:
            return []
        recipe_rules = {
            (rule.name or f'rule_{index}'): rule
            for index, rule in enumerate(self.organize_recipe.rules, start=1)
        }
        hints: list[str] = []
        for summary in self.compiled_plan.rule_summaries:
            rule_name = str(summary.rule_name or '')
            matched_related = [
                path
                for path in summary.matched_paths
                if _norm_path(path) in related_norms
            ]
            if len(matched_related) < 2:
                continue
            rule = recipe_rules.get(rule_name)
            if rule is None or rule.disposition != 'map_to_bangumi':
                continue
            fixed_locators: list[str] = []
            if int(rule.target.episode_id or 0) > 0:
                fixed_locators.append(f'episode_id:{int(rule.target.episode_id or 0)}')
            if rule.target.sort is not None:
                fixed_locators.append(f'sort:{rule.target.sort}')
            if rule.target.ep is not None:
                fixed_locators.append(f'ep:{rule.target.ep}')
            if fixed_locators and len(summary.matched_paths) > 1:
                hints.append(
                    f'Rule "{rule_name}" matches {len(summary.matched_paths)} visible files but fixes {", ".join(fixed_locators)}. '
                    f'A multi-file selector cannot reuse one exact Bangumi locator unless every selected file is intentionally the same item. '
                    f'If this is a TV/SP sequence under one subject, patch the rule: unset episode_id/sort/ep and keep episode_range plus episode_number_field so targets derive from {{ep}}. '
                    f'If these files are separate movie/OVA/special items, replace the group rule with separate exact_paths rules using distinct exposed subject_id/episode_id values; cover only unsupported extras as supplemental.'
                )
            rule_assignments = [
                assignment
                for assignment in self.compiled_plan.assignments
                if assignment.rule_name == rule_name
                and _norm_path(assignment.source_path) in related_norms
            ]
            number_counts = Counter(
                assignment.extracted_episode_number
                for assignment in rule_assignments
                if assignment.extracted_episode_number is not None
            )
            duplicate_numbers = [number for number, count in number_counts.items() if count > 1]
            if duplicate_numbers and not fixed_locators:
                duplicate_paths = [
                    assignment.source_path
                    for assignment in rule_assignments
                    if assignment.extracted_episode_number in set(duplicate_numbers)
                ]
                hints.append(
                    f'Rule "{rule_name}" maps multiple paths with the same extracted episode number(s) {sorted(duplicate_numbers)[:4]}: {duplicate_paths[:6]}. '
                    f'Treat this as a duplicate local locator or split/variant case. Keep one file mapped to the exposed row; if no distinct Bangumi row exists for the extra variant, patch the mapped rule with exclude_regex or exact replacement and append an exact supplemental rule for only that extra path.'
                )
        return _dedupe_nonempty(hints)

    def _repair_hint_for_uncovered_paths(self, paths: list[str]) -> str:
        for path in paths:
            group = self._local_skeleton_group_for_path(path)
            if not group:
                continue
            selector = group.get('selector_hint') if isinstance(group.get('selector_hint'), dict) else {}
            source_pattern = str(selector.get('source_pattern') or '')
            recommended_shape = str(selector.get('recommended_shape') or '')
            number_ranges = (group.get('number_summary') or {}).get('integer_ranges') if isinstance(group.get('number_summary'), dict) else []
            warnings = group.get('boundary_warnings') or []
            parts = [
                f'Local skeleton group {group.get("group_ref")} ({group.get("title_hint")}) contains this path.',
                f'recommended_shape={recommended_shape}.',
            ]
            if source_pattern:
                parts.append(f'source_pattern={source_pattern!r}.')
            if number_ranges:
                parts.append(f'local number ranges={number_ranges}; for mapped sequences, episode_range should use these local captured numbers with EP arithmetic only when target rows are shifted. For supplemental repair, prefer group_ref or exact_paths covering the group exactly once and omit episode_range/episode_offset.')
            if warnings:
                parts.append(f'boundary_warnings={warnings}; duplicate-number variants may need exact supplemental coverage or a selector that includes the variant suffix.')
            return ' '.join(parts)
        return ''

    def _repair_hint_for_special_bonus_missing_target(self, paths: list[str]) -> str:
        for path in paths:
            group = self._local_skeleton_group_for_path(path)
            if not group:
                continue
            group_kind = str(group.get('group_kind_hint') or '')
            if group_kind not in {'special_or_bonus_candidate', 'asset_or_bonus_candidate'}:
                continue
            selector = group.get('selector_hint') if isinstance(group.get('selector_hint'), dict) else {}
            source_pattern = str(selector.get('source_pattern') or '')
            compact_selector = f' with the compact source_pattern {source_pattern!r}' if source_pattern else ' with a compact selector or exact paths'
            return (
                f'This path is in local skeleton group {group.get("group_ref")} ({group.get("title_hint")}) '
                f'marked {group_kind}. A related Bangumi subject alone is not enough to map short SP/bonus files; '
                'the selected subject must expose matching episode rows by sort/ep/title/count. '
                f'If targeted episode evidence still does not expose those rows, prefer converting the affected group to '
                f'disposition:"non_bangumi_or_supplemental"{compact_selector} instead of spending more turns force-mapping it.'
            )
        return ''

    def _local_skeleton_group_for_path(self, path: str) -> dict[str, Any] | None:
        normalized = _norm_path(path)
        if not normalized:
            return None
        for group_ref, paths in self._local_group_paths_by_ref().items():
            if normalized in set(paths):
                group = self._find_local_group_payload(group_ref)
                if group is not None:
                    return group
        for group in self._local_recipe_skeleton_payload().get('groups') or []:
            if not isinstance(group, dict):
                continue
            source_paths = group.get('source_paths') if isinstance(group.get('source_paths'), dict) else {}
            listed_paths = set()
            for key in ('all', 'sample'):
                for item in source_paths.get(key) or []:
                    listed_paths.add(_norm_path(str(item or '')))
            if normalized in listed_paths:
                return group
            selector = group.get('selector_hint') if isinstance(group.get('selector_hint'), dict) else {}
            source_pattern = str(selector.get('source_pattern') or '')
            if source_pattern and _source_pattern_matches(source_pattern, normalized):
                return group
        return None

    def _hydrate_recipe_target_evidence(self, recipe: OrganizeRecipeDraft) -> None:
        subject_ids = sorted({
            int(getattr(rule.target, 'bangumi_subject_id', 0) or 0)
            for rule in recipe.rules
            if str(getattr(rule, 'disposition', '') or '') == 'map_to_bangumi'
        })
        for subject_id in subject_ids:
            if subject_id <= 0:
                continue
            try:
                episodes = self.bangumi_client.get_episodes(subject_id) or []
            except Exception:
                continue
            self._ensure_subject_known(subject_id)
            self._upsert_item_cards([self._episode_card_from_api(subject_id, episode) for episode in list(episodes)[:500]])

    def _write_recipe_artifacts(
        self,
        recipe: OrganizeRecipeDraft,
        plan: CompiledOrganizePlan,
        verifier_result: CaseVerifierResult,
        *,
        repair_hints: list[str] | None = None,
        review_warnings: list[dict[str, Any]] | None = None,
    ) -> None:
        artifacts = self.run_dir / 'artifacts'
        (artifacts / 'organize_recipe.json').write_text(json.dumps(recipe.model_dump(mode='json'), ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')
        (artifacts / 'compiled_plan.json').write_text(json.dumps(plan.model_dump(mode='json'), ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')
        verifier_payload = verifier_result.model_dump(mode='json')
        if repair_hints is not None:
            verifier_payload['repair_hints'] = _dedupe_nonempty(repair_hints)
        if review_warnings is not None:
            verifier_payload['review_warnings'] = review_warnings
        (artifacts / 'recipe_verifier_result.json').write_text(json.dumps(verifier_payload, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')

    def _run_recipe_helper_check(self) -> dict[str, Any]:
        artifacts = self.run_dir / 'artifacts'
        recipe_path = artifacts / 'organize_recipe.json'
        case_input_path = self.run_dir / 'case_input.json'
        helper_path = artifacts / 'organize_recipe_helper_check.json'
        script_path = self.repo_root / '.pi' / 'skills' / 'local-bangumi-organize' / 'scripts' / 'check-organize-recipe.mjs'
        if not case_input_path.exists():
            case_input_path.write_text(
                json.dumps(self.case_input(timeout_seconds=0), ensure_ascii=False, indent=2, sort_keys=True),
                encoding='utf-8',
            )
        command = ['node', str(script_path), str(recipe_path), str(case_input_path)]
        try:
            completed = subprocess.run(
                command,
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=30,
                check=False,
            )
            try:
                payload = json.loads(completed.stdout or '{}')
            except json.JSONDecodeError:
                payload = {}
            artifact = {
                **payload,
                'ok': bool(payload.get('ok')) and completed.returncode == 0,
                'helper_command': command,
                'returncode': completed.returncode,
                'timed_out': False,
                'stderr': completed.stderr,
            }
            if not payload and completed.stdout:
                artifact['stdout'] = completed.stdout
        except subprocess.TimeoutExpired as exc:
            artifact = {
                'ok': False,
                'helper_command': command,
                'returncode': None,
                'timed_out': True,
                'stderr': str(exc.stderr or ''),
                'error': 'helper check timed out',
            }
        except Exception as exc:
            artifact = {
                'ok': False,
                'helper_command': command,
                'returncode': None,
                'timed_out': False,
                'stderr': '',
                'error': f'{type(exc).__name__}: {exc}',
            }
        helper_path.write_text(json.dumps(_json_safe(artifact), ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')
        return _json_safe(artifact)

    def _write_final_result(self) -> None:
        if self.final_result is None:
            return
        (self.run_dir / 'final_result.json').write_text(
            json.dumps(_json_safe(self.final_result), ensure_ascii=False, indent=2, sort_keys=True),
            encoding='utf-8',
        )

    def tool_summary(self) -> dict[str, Any]:
        return {
            'tool_trace_count': len(self.tool_trace),
            'tool_call_counts': _counter([str(row.get('tool') or '') for row in self.tool_trace]),
            'tool_sequence': [str(row.get('tool') or '') for row in self.tool_trace],
            'submit_rejection_count': self.submit_rejection_count,
        }

    def _subject_card_from_api(self, subject: Any, *, relation_to_main: str = '') -> BangumiSubjectCard:
        subject_id = int(getattr(subject, 'id', getattr(subject, 'subject_id', 0)) or 0)
        raw_type = getattr(subject, 'type', getattr(subject, 'subject_type', 0))
        return BangumiSubjectCard(
            ref=f'subject:{subject_id}',
            subject_id=subject_id,
            subject_type=_subject_type_from_api(raw_type),
            title=getattr(subject, 'title', '') or getattr(subject, 'name_cn', '') or getattr(subject, 'name', ''),
            name=getattr(subject, 'name', '') or '',
            name_cn=getattr(subject, 'name_cn', '') or '',
            date=getattr(subject, 'date', '') or '',
            summary_short=getattr(subject, 'summary', '') or '',
            platform=getattr(subject, 'platform', '') or '',
            eps=int(getattr(subject, 'eps', 0) or 0),
            total_episodes=int(getattr(subject, 'total_episodes', 0) or 0),
            tags=list(getattr(subject, 'tags', []) or []),
            relation_to_main=relation_to_main,
            retrieval_round=1,
        )

    def _episode_card_from_api(self, subject_id: int, episode: Any) -> BangumiItemCard:
        episode_id = int(getattr(episode, 'id', 0) or 0)
        ep_type = getattr(episode, 'type', 0)
        item_kind = 'episode' if ep_type in (0, '0', 'regular', '') else 'special'
        source_hint = str(getattr(episode, 'source_form_hint', '') or '')
        if source_hint == 'movie':
            item_kind = 'movie'
        return BangumiItemCard(
            ref=f'episode:{episode_id}' if episode_id else f'subject:{subject_id}:sort:{int(getattr(episode, "sort", 0) or 0)}',
            item_kind=item_kind,
            episode_id=episode_id,
            kind=str(getattr(episode, 'kind', '') or ''),
            type=str(ep_type),
            sort=int(getattr(episode, 'sort', 0) or 0),
            ep=int(getattr(episode, 'ep', 0) or 0),
            subject_ref=f'subject:{subject_id}',
            title=getattr(episode, 'title', '') or getattr(episode, 'name_cn', '') or getattr(episode, 'name', '') or '',
            name=getattr(episode, 'name', '') or '',
            name_cn=getattr(episode, 'name_cn', '') or '',
            airdate=getattr(episode, 'airdate', '') or '',
            duration=getattr(episode, 'duration', '') or '',
            duration_seconds=int(getattr(episode, 'duration_seconds', 0) or 0),
            desc_short=(getattr(episode, 'desc', '') or '')[:160],
            synthetic=bool(getattr(episode, 'synthetic', False)),
            source_form_hint=source_hint,
            relation_to_main=getattr(episode, 'relation_to_main', '') or getattr(episode, 'relation', '') or '',
            episode_number=int(getattr(episode, 'ep', 0) or 0),
        )

    def _upsert_subject_cards(self, cards: list[BangumiSubjectCard]) -> None:
        if not cards:
            return
        self.workspace = self.workspace.with_replaced_cards(subjects=cards)

    def _upsert_item_cards(self, cards: list[BangumiItemCard]) -> None:
        if not cards:
            return
        self.workspace = self.workspace.with_replaced_cards(items=cards)

    def _ensure_subject_known(self, subject_id: int) -> None:
        if subject_id <= 0:
            return
        if any(int(getattr(card, 'subject_id', 0) or 0) == subject_id for card in self.workspace.bangumi_subjects):
            return
        try:
            subject = self.bangumi_client.get_subject(subject_id)
        except Exception:
            subject = None
        if subject is not None:
            self._upsert_subject_cards([self._subject_card_from_api(subject)])

    def _select_episodes(self, episodes: list[Any], scope: str) -> list[Any]:
        if scope == 'regular':
            selected = [ep for ep in episodes if getattr(ep, 'type', 0) in (0, '0')]
        elif scope == 'special':
            selected = [ep for ep in episodes if getattr(ep, 'type', 0) not in (0, '0')]
        else:
            selected = list(episodes)
        return selected

    def _subject_payload(self, card: BangumiSubjectCard, *, include_summary: bool = True) -> dict[str, Any]:
        payload = {
            'subject_id': card.subject_id,
            'subject_type': card.subject_type,
            'title': card.title or card.name_cn or card.name,
            'name': card.name,
            'name_cn': card.name_cn,
            'date': card.date,
            'platform': card.platform,
            'eps': card.eps,
            'total_episodes': card.total_episodes,
            'source_form_hint': card.source_form_hint,
            'relation_to_main': card.relation_to_main,
        }
        if include_summary:
            payload['summary_short'] = card.summary_short
        return payload

    def _subject_id_for_item(self, card: BangumiItemCard) -> int:
        subject_ref = str(getattr(card, 'subject_ref', '') or '')
        for subject in self.workspace.bangumi_subjects:
            if subject.ref == subject_ref:
                return int(subject.subject_id or 0)
        if subject_ref.startswith('subject:'):
            try:
                return int(subject_ref.split(':', 1)[1])
            except ValueError:
                return 0
        return 0

    def _episode_payload(self, card: BangumiItemCard, *, kind_hint: str = '') -> dict[str, Any]:
        return {
            'subject_id': self._subject_id_for_item(card),
            'episode_id': card.episode_id,
            'episode_type': self._recipe_episode_type_for_item(card, kind_hint=kind_hint),
            'api_item_kind': card.item_kind,
            'api_type': card.type,
            'api_kind': card.kind,
            'type': card.type,
            'sort': card.sort,
            'ep': card.ep,
            'title': card.title or card.name_cn or card.name,
            'name': card.name,
            'name_cn': card.name_cn,
            'airdate': card.airdate,
            'duration': card.duration,
            'source_form_hint': card.source_form_hint,
            'relation_to_main': card.relation_to_main,
        }

    def _recipe_episode_type_for_item(self, card: BangumiItemCard, *, kind_hint: str = '') -> str:
        source_hint = _normalize_kind_hint(str(getattr(card, 'source_form_hint', '') or ''))
        item_kind = str(getattr(card, 'item_kind', '') or '').casefold()
        if item_kind == 'episode':
            return 'regular'
        if item_kind == 'movie':
            return 'movie'
        if item_kind == 'special':
            hinted = _recipe_episode_type_from_kind(source_hint) if source_hint in {'ova', 'oad'} else _recipe_episode_type_from_kind(kind_hint)
            return hinted if hinted in {'ova', 'oad'} else 'special'
        if item_kind == 'unknown':
            return 'unknown'
        ep_type = str(getattr(card, 'type', '') or '').casefold()
        return 'regular' if ep_type in {'', '0', 'regular', 'main', 'episode'} else 'special'

    def _local_file_payload(self, card: Any, *, detail: bool = False) -> dict[str, Any]:
        payload = {
            'source_path': _norm_path(str(getattr(card, 'path', '') or '')),
            'basename': _norm_path(str(getattr(card, 'path', '') or '')).rsplit('/', 1)[-1],
            'size_bytes': int(getattr(card, 'size_bytes', 0) or 0),
            'is_main': bool(getattr(card, 'is_main', False)),
            'label': str(getattr(card, 'label', '') or ''),
            'parent_display': str(getattr(card, 'parent_display', '') or ''),
            'fact_summary': _json_safe(getattr(card, 'fact_summary', {}) or {}),
        }
        if detail:
            payload.update({
                'path_facts': _json_safe(getattr(card, 'path_facts', {}) or {}),
                'container_facts': _json_safe(getattr(card, 'container_facts', {}) or {}),
                'stream_facts': _json_safe(getattr(card, 'stream_facts', {}) or {}),
                'subtitle_facts': _json_safe(getattr(card, 'subtitle_facts', {}) or {}),
            })
        return payload

def _norm_path(path: str) -> str:
    return str(path or '').replace('\\', '/').strip().lstrip('./')


def _string_or_default(value: Any, default: str = '') -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _coerce_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [_norm_path(value)] if _norm_path(value) else []
    if isinstance(value, list):
        return [_norm_path(str(item)) for item in value if _norm_path(str(item))]
    return []


def _coerce_int_list(value: Any) -> list[int]:
    raw_values: list[Any]
    if value is None:
        raw_values = []
    elif isinstance(value, str):
        raw_values = [part for part in re.split(r'[\s,;]+', value) if part]
    elif isinstance(value, list):
        raw_values = value
    else:
        raw_values = [value]
    numbers: list[int] = []
    for item in raw_values:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number > 0 and number not in numbers:
            numbers.append(number)
    return numbers


def _optional_int(value: Any) -> int | None:
    if value is None or value == '':
        return None
    return int(value)


def _episode_card_order_key(card: BangumiItemCard) -> tuple[int, int, int]:
    sort_value = int(getattr(card, 'sort', 0) or 0)
    ep_value = int(getattr(card, 'ep', 0) or 0)
    episode_id = int(getattr(card, 'episode_id', 0) or 0)
    return (
        sort_value if sort_value > 0 else 999999,
        ep_value if ep_value > 0 else 999999,
        episode_id if episode_id > 0 else 999999999,
    )


def _subject_type_from_api(value: Any) -> str:
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {'anime', 'book', 'game', 'music', 'real'}:
            return normalized
        if normalized.isdigit():
            value = int(normalized)
        else:
            return 'unknown'
    try:
        numeric = int(value or 0)
    except (TypeError, ValueError):
        return 'unknown'
    return {
        1: 'book',
        2: 'anime',
        3: 'music',
        4: 'game',
        6: 'real',
    }.get(numeric, 'unknown')


def _first_present(*mappings: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for mapping in mappings:
        for key in keys:
            if key in mapping:
                return mapping.get(key)
    return None


def _apply_recipe_params_patch(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(patch, dict) or not patch:
        raise ValueError('patch must be a non-empty object')
    _validate_recipe_params_patch_shape(patch)
    merged = _json_clone(base)
    rules = [dict(rule) for rule in merged.get('rules') or [] if isinstance(rule, dict)]
    if not rules:
        raise ValueError('base recipe_params.rules must be a non-empty array')

    remove_names = set(_coerce_string_list(patch.get('remove_rule_names')))
    if remove_names:
        rules = [rule for rule in rules if str(rule.get('name') or '') not in remove_names]

    for replacement in patch.get('replace_rules') or []:
        if not isinstance(replacement, dict):
            raise ValueError('replace_rules entries must be objects')
        name = str(replacement.get('name') or '')
        if not name:
            raise ValueError('replace_rules entries need a name')
        for index, rule in enumerate(rules):
            if str(rule.get('name') or '') == name:
                rules[index] = _json_clone(replacement)
                break
        else:
            rules.append(_json_clone(replacement))

    for rule_patch in patch.get('patch_rules') or []:
        if not isinstance(rule_patch, dict):
            raise ValueError('patch_rules entries must be objects')
        name = str(rule_patch.get('name') or '')
        if not name:
            raise ValueError('patch_rules entries need a name')
        updates = rule_patch.get('updates') if isinstance(rule_patch.get('updates'), dict) else {}
        target = next((rule for rule in rules if str(rule.get('name') or '') == name), None)
        if target is None:
            raise ValueError(f'patch_rules target not found: {name}')
        for key, value in updates.items():
            target[key] = _json_clone(value)
        for key in _coerce_string_list(rule_patch.get('unset')):
            target.pop(key, None)

    for rule in patch.get('append_rules') or []:
        if not isinstance(rule, dict):
            raise ValueError('append_rules entries must be objects')
        rules.append(_json_clone(rule))

    if not rules:
        raise ValueError('patch removed every rule')
    merged['rules'] = rules
    return merged


def _canonical_recipe_params_patch_for_reuse(patch: Any) -> dict[str, Any]:
    if not isinstance(patch, dict):
        return {}
    canonical: dict[str, Any] = {}
    for key, value in patch.items():
        if value is None:
            continue
        if isinstance(value, (list, dict)) and not value:
            continue
        canonical[key] = _json_safe(value)
    return canonical


def _canonical_recipe_params_payload_for_validation(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _json_clone(payload[key])
        for key in ('version', 'summary', 'rules')
        if isinstance(payload, dict) and key in payload
    }


_SOURCE_PATTERN_TOKEN_RE = re.compile(r'\{([A-Za-z_][A-Za-z0-9_]*)(?::0?(\d+)d?)?\}')
_LEGAL_MEDIA_KINDS = {'tv', 'movie', 'ova', 'oad', 'sp', 'special', 'unknown'}
_LEGAL_EPISODE_TYPES = {'main', 'regular', 'special', 'ova', 'oad', 'movie', 'unknown'}
_LEGAL_SOURCE_UNITS = {'single_file', 'single_file_multi_episode'}
_LEGAL_RECIPE_PARAMS_DISPOSITIONS = {
    'map_to_bangumi',
    'non_bangumi_or_supplemental',
    'needs_more_evidence',
    'unaligned_fail_closed',
}
_ALLOWED_RECIPE_PARAMS_PAYLOAD_KEYS = {'version', 'summary', 'rules'}
_ALLOWED_RECIPE_PARAMS_RULE_KEYS = {
    'disposition',
    'ep',
    'episode_id',
    'episode_ids',
    'episode_number_field',
    'episode_offset',
    'episode_range',
    'episode_range_end',
    'episode_range_start',
    'episode_type',
    'exact_paths',
    'exclude_path_contains',
    'exclude_regex',
    'file_number_range',
    'file_numbers',
    'filename_regex',
    'group_ref',
    'media_kind',
    'name',
    'path_contains',
    'reason',
    'sort',
    'source_pattern',
    'subject_id',
    'source_unit',
}
_RAW_RECIPE_RULE_KEYS = {'select', 'target', 'episode'}
_ALLOWED_RECIPE_PARAMS_PATCH_KEYS = {
    'append_rules',
    'patch_rules',
    'remove_rule_names',
    'replace_rules',
}
_ALLOWED_RECIPE_PARAMS_PATCH_RULE_KEYS = {'name', 'updates', 'unset'}
_UNSUPPORTED_DISPOSITION_FLAG_HINTS = {
    'non_bangumi_or_supplemental': 'disposition: "non_bangumi_or_supplemental"',
    'supplemental': 'disposition: "non_bangumi_or_supplemental"',
    'exclude': 'disposition: "non_bangumi_or_supplemental"',
    'excluded': 'disposition: "non_bangumi_or_supplemental"',
    'unmapped': 'disposition: "non_bangumi_or_supplemental"',
    'map_to_bangumi': 'disposition: "map_to_bangumi"',
    'needs_more_evidence': 'disposition: "needs_more_evidence"',
    'unaligned_fail_closed': 'disposition: "unaligned_fail_closed"',
}
_UNSUPPORTED_SOURCE_UNIT_FLAG_HINTS = {
    'single_file_multi_episode': 'source_unit: "single_file_multi_episode"',
    'multi_episode': 'source_unit: "single_file_multi_episode"',
    'multi_episode_file': 'source_unit: "single_file_multi_episode"',
    'merged': 'source_unit: "single_file_multi_episode"',
}


def _validate_recipe_params_rule_shape(rule: dict[str, Any], *, index: int, context: str) -> None:
    location = f'{context}[{index - 1}]'
    raw_keys = sorted(_RAW_RECIPE_RULE_KEYS.intersection(rule))
    if raw_keys:
        raise ValueError(
            f'{location} uses non-canonical nested raw object(s) {raw_keys}; '
            'use flat RecipeParamsRule fields instead'
        )
    disposition = _string_or_default(rule.get('disposition'), '')
    _reject_unsupported_disposition_flags(rule, index=index, disposition=disposition)
    _reject_unsupported_source_unit_flags(rule, index=index)
    unknown_keys = sorted(set(rule) - _ALLOWED_RECIPE_PARAMS_RULE_KEYS)
    if unknown_keys:
        raise ValueError(
            f'{location} uses non-canonical field(s) {unknown_keys}; '
            f'allowed canonical fields: {sorted(_ALLOWED_RECIPE_PARAMS_RULE_KEYS)}'
        )

    _require_optional_string(rule, 'name', location)
    _require_optional_string(rule, 'group_ref', location)
    _require_optional_number_array(rule, 'file_numbers', location)
    _require_optional_string(rule, 'file_number_range', location)
    _require_optional_string_or_string_array(rule, 'path_contains', location)
    _require_optional_string_or_string_array(rule, 'exclude_path_contains', location)
    _require_optional_string_array(rule, 'exact_paths', location)
    _require_optional_string(rule, 'source_pattern', location)
    _require_optional_string(rule, 'filename_regex', location)
    _require_optional_string(rule, 'exclude_regex', location)
    _require_optional_number(rule, 'subject_id', location)
    _require_optional_number(rule, 'episode_id', location)
    _require_optional_number_array(rule, 'episode_ids', location)
    _require_optional_number(rule, 'sort', location)
    _require_optional_number(rule, 'ep', location)
    if isinstance(rule.get('episode_range'), (list, tuple, dict)):
        raise ValueError(
            f'{location} episode_range must be a compact string like "1-13" '
            'or use episode_range_start/episode_range_end; arrays are not accepted'
        )
    _require_optional_string(rule, 'episode_range', location)
    _require_optional_number(rule, 'episode_range_start', location)
    _require_optional_number(rule, 'episode_range_end', location)
    _require_optional_string(rule, 'episode_offset', location)
    _require_optional_string(rule, 'reason', location)

    _require_optional_enum(rule, 'source_unit', _LEGAL_SOURCE_UNITS, location)
    _require_optional_enum(rule, 'media_kind', _LEGAL_MEDIA_KINDS, location)
    _require_optional_enum(rule, 'episode_type', _LEGAL_EPISODE_TYPES, location)
    _require_optional_enum(rule, 'episode_number_field', {'sort', 'ep'}, location)
    _require_optional_enum(rule, 'disposition', _LEGAL_RECIPE_PARAMS_DISPOSITIONS, location)


def _validate_recipe_params_patch_shape(patch: dict[str, Any]) -> None:
    unknown_top_keys = sorted(set(patch) - _ALLOWED_RECIPE_PARAMS_PATCH_KEYS)
    if unknown_top_keys:
        raise ValueError(
            f'patch uses non-canonical field(s) {unknown_top_keys}; '
            'allowed fields: patch_rules, replace_rules, append_rules, remove_rule_names'
        )
    _require_optional_string_array(patch, 'remove_rule_names', 'patch')
    for key in ('replace_rules', 'append_rules'):
        rules = patch.get(key)
        if rules is None:
            continue
        if not isinstance(rules, list):
            raise ValueError(f'patch.{key} must be an array of RecipeParamsRule objects')
        for index, rule in enumerate(rules, start=1):
            if not isinstance(rule, dict):
                raise ValueError(f'patch.{key}[{index - 1}] must be an object')
            _validate_recipe_params_rule_shape(rule, index=index, context=f'patch.{key}')
    patch_rules = patch.get('patch_rules')
    if patch_rules is not None:
        if not isinstance(patch_rules, list):
            raise ValueError('patch.patch_rules must be an array')
        for index, rule_patch in enumerate(patch_rules, start=1):
            location = f'patch.patch_rules[{index - 1}]'
            if not isinstance(rule_patch, dict):
                raise ValueError(f'{location} must be an object')
            unknown_patch_keys = sorted(set(rule_patch) - _ALLOWED_RECIPE_PARAMS_PATCH_RULE_KEYS)
            if unknown_patch_keys:
                raise ValueError(
                    f'{location} uses non-canonical field(s) {unknown_patch_keys}; '
                    'patch rule fields are name, updates, unset'
                )
            _require_required_string(rule_patch, 'name', location)
            updates = rule_patch.get('updates')
            if updates is not None:
                if not isinstance(updates, dict):
                    raise ValueError(f'{location}.updates must be an object')
                _validate_recipe_params_rule_shape(updates, index=1, context=f'{location}.updates')
            unset_values = rule_patch.get('unset')
            if unset_values is not None:
                _require_optional_string_array(rule_patch, 'unset', location)
                invalid_unset = sorted(set(_coerce_string_list(unset_values)) - _ALLOWED_RECIPE_PARAMS_RULE_KEYS)
                if invalid_unset:
                    raise ValueError(f'{location}.unset contains non-canonical field(s) {invalid_unset}')


def _require_required_string(payload: dict[str, Any], key: str, location: str) -> None:
    if not isinstance(payload.get(key), str) or not str(payload.get(key) or '').strip():
        raise ValueError(f'{location}.{key} must be a non-empty string')


def _require_optional_string(payload: dict[str, Any], key: str, location: str) -> None:
    value = payload.get(key)
    if value not in (None, '') and not isinstance(value, str):
        raise ValueError(f'{location}.{key} must be a string')


def _require_optional_string_array(payload: dict[str, Any], key: str, location: str) -> None:
    value = payload.get(key)
    if value is None:
        return
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f'{location}.{key} must be an array of strings')


def _require_optional_string_or_string_array(payload: dict[str, Any], key: str, location: str) -> None:
    value = payload.get(key)
    if value is None:
        return
    if isinstance(value, str):
        return
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return
    raise ValueError(f'{location}.{key} must be a string or an array of strings')


def _require_optional_number(payload: dict[str, Any], key: str, location: str) -> None:
    value = payload.get(key)
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{location}.{key} must be a number')


def _require_optional_number_array(payload: dict[str, Any], key: str, location: str) -> None:
    value = payload.get(key)
    if value is None:
        return
    if not isinstance(value, list) or any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        raise ValueError(f'{location}.{key} must be an array of numbers')


def _require_optional_enum(payload: dict[str, Any], key: str, allowed: set[str], location: str) -> None:
    value = payload.get(key)
    if value in (None, ''):
        return
    if not isinstance(value, str):
        raise ValueError(f'{location}.{key} must be a string enum')
    normalized = value.strip().casefold()
    if normalized not in allowed:
        raise ValueError(f'{location}.{key} {value!r} is not legal; use one of: {", ".join(sorted(allowed))}')


def _source_pattern_from_params(rule: dict[str, Any]) -> str:
    return _string_or_default(_first_present(rule, keys=('source_pattern',)), '')


def _reject_unsupported_disposition_flags(rule: dict[str, Any], *, index: int, disposition: str) -> None:
    flagged = [
        key
        for key in _UNSUPPORTED_DISPOSITION_FLAG_HINTS
        if key in rule
        and isinstance(rule.get(key), bool)
    ]
    if not flagged:
        return
    if disposition:
        raise ValueError(
            f'rules[{index - 1}] mixes disposition with unsupported boolean field(s) {flagged}; '
            'remove the boolean field(s) and keep the single disposition enum.'
        )
    suggested = _UNSUPPORTED_DISPOSITION_FLAG_HINTS.get(flagged[0], 'disposition: "non_bangumi_or_supplemental"')
    raise ValueError(
        f'rules[{index - 1}] uses unsupported boolean field(s) {flagged}. '
        f'Use the disposition enum instead, for example {suggested}.'
    )


def _reject_unsupported_source_unit_flags(rule: dict[str, Any], *, index: int) -> None:
    flagged = [
        key
        for key in _UNSUPPORTED_SOURCE_UNIT_FLAG_HINTS
        if key in rule
        and isinstance(rule.get(key), bool)
    ]
    if not flagged:
        return
    suggested = _UNSUPPORTED_SOURCE_UNIT_FLAG_HINTS[flagged[0]]
    raise ValueError(
        f'rules[{index - 1}] uses unsupported boolean source-unit field(s) {flagged}. '
        f'Use the source_unit enum instead, for example {suggested}.'
    )


def _source_unit_from_params(rule: dict[str, Any], *, index: int) -> str:
    _reject_unsupported_source_unit_flags(rule, index=index)
    value = _string_or_default(_first_present(rule, keys=('source_unit',)), '').casefold()
    return value if value in _LEGAL_SOURCE_UNITS else 'single_file'


def _media_kind_from_params(rule: dict[str, Any]) -> str:
    value = _string_or_default(_first_present(rule, keys=('media_kind',)), '').casefold()
    return value if value in _LEGAL_MEDIA_KINDS else 'unknown'


def _episode_type_from_params(rule: dict[str, Any]) -> str:
    value = _string_or_default(_first_present(rule, keys=('episode_type',)), '').casefold()
    return value if value in _LEGAL_EPISODE_TYPES else 'unknown'


def _source_pattern_to_regex(pattern: str) -> str:
    text = _norm_path(pattern)
    parts: list[str] = []
    index = 0
    for match in _SOURCE_PATTERN_TOKEN_RE.finditer(text):
        parts.append(_escape_source_pattern_segment(text[index:match.start()]))
        name = match.group(1)
        width = int(match.group(2) or 0)
        if name == 'ep':
            parts.append(rf'(?P<{name}>\d{{{width}}})' if width > 0 else rf'(?P<{name}>\d+)')
        else:
            parts.append(r'.*?')
        index = match.end()
    parts.append(_escape_source_pattern_segment(text[index:]))
    return ''.join(parts)


def _escape_source_pattern_segment(segment: str) -> str:
    parts: list[str] = []
    for char in str(segment or ''):
        if char == '*':
            parts.append(r'.*?')
        elif char == '?':
            parts.append(r'.')
        else:
            parts.append(re.escape(char))
    return ''.join(parts)


def _episode_range_from_params(rule: dict[str, Any]) -> str:
    explicit_value = _first_present(rule, keys=('episode_range',))
    explicit = _string_or_default(explicit_value, '')
    if explicit:
        return explicit
    start = _first_present(rule, keys=('episode_range_start',))
    end = _first_present(rule, keys=('episode_range_end',))
    start_text = _string_or_default(start, '')
    end_text = _string_or_default(end, '')
    if start_text and end_text:
        return f'{start_text}-{end_text}'
    return start_text or end_text


def _episode_offset_from_params(rule: dict[str, Any]) -> str:
    value = _first_present(rule, keys=('episode_offset',))
    if value is None:
        return 'EP'
    if isinstance(value, (int, float)) and float(value).is_integer():
        return _numeric_offset_to_expr(int(value))
    text = str(value).strip()
    if not text:
        return 'EP'
    if re.fullmatch(r'[+-]?\d+', text):
        return _numeric_offset_to_expr(int(text))
    if text.casefold() == 'ep':
        return 'EP'
    return text


def _episode_number_field_from_params(rule: dict[str, Any]) -> str:
    value = _string_or_default(
        _first_present(rule, keys=('episode_number_field',)),
        'sort',
    ).casefold()
    return value if value in {'sort', 'ep'} else 'sort'


def _numeric_offset_to_expr(value: int) -> str:
    if value == 0:
        return 'EP'
    if value > 0:
        return f'EP+{value}'
    return f'EP{value}'


def _simple_episode_offset_delta(expr: str) -> int | None:
    text = str(expr or '').replace(' ', '').upper()
    if text == 'EP':
        return 0
    match = re.fullmatch(r'EP([+-]\d+)', text)
    if not match:
        return None
    return int(match.group(1))


def _episode_range_numbers(spec: str) -> list[int]:
    numbers: list[int] = []
    for part in [item.strip() for item in str(spec or '').split(',') if item.strip()]:
        if '-' in part:
            left_text, right_text = part.split('-', 1)
            try:
                left = int(left_text)
                right = int(right_text)
            except ValueError:
                return []
            if right < left:
                return []
            numbers.extend(range(left, right + 1))
        else:
            try:
                numbers.append(int(part))
            except ValueError:
                return []
    return list(dict.fromkeys(numbers))


def _is_contiguous_numbers(numbers: list[int]) -> bool:
    if not numbers:
        return False
    ordered = sorted(set(numbers))
    return ordered == list(range(ordered[0], ordered[-1] + 1))


def _numbers_to_episode_range(numbers: list[int]) -> str:
    ordered = sorted(set(numbers))
    if not ordered:
        return ''
    if len(ordered) == 1:
        return str(ordered[0])
    return f'{ordered[0]}-{ordered[-1]}'


def _episode_type_matches_recipe(card: BangumiItemCard, expected: str) -> bool:
    expected = str(expected or 'unknown')
    if expected in {'', 'unknown'}:
        return True
    item_type = str(getattr(card, 'type', '') or '').casefold()
    item_kind = str(getattr(card, 'kind', '') or '').casefold()
    item_item_kind = str(getattr(card, 'item_kind', '') or '').casefold()
    if expected in {'main', 'regular'}:
        return item_type in {'0', 'regular', 'main', ''} and item_item_kind in {'episode', ''}
    if expected in {'special', 'sp', 'ova', 'oad'}:
        return item_type not in {'0', 'regular', 'main'} or item_kind in {'special', expected} or item_item_kind == 'special'
    if expected == 'movie':
        return item_item_kind == 'movie' or item_kind == 'movie'
    return True


def _parse_error_repair_hints(error: str, visible_paths: list[str]) -> list[str]:
    text = str(error or '')
    hints: list[str] = []
    if 'episode_type' in text and 'episode' in text:
        hints.append("target.episode_type cannot be raw API value 'episode'. Use 'regular' for normal TV episodes, or use 'special', 'ova', 'oad', or 'movie' when evidence supports that shape.")
    if 'media_kind' in text and ('web' in text.casefold() or 'anime' in text.casefold() or 'not legal' in text.casefold()):
        hints.append("target.media_kind cannot be raw source/API values such as 'web' or 'anime'. Use one of tv, movie, ova, oad, sp, special, or unknown.")
    if 'episode_range must be a compact string' in text:
        hints.append('Write episode_range as a string such as "1-13" or use episode_range_start and episode_range_end; do not pass [1,13].')
    if 'source_pattern that matches none of that group' in text:
        hints.append('When combining group_ref with source_pattern, the pattern must match files in that local group and include {ep} for sequence mapping. Fix the template or use group_ref alone.')
    if 'extra_forbidden' in text or 'Extra inputs are not permitted' in text:
        hints.append('Remove unknown fields from the recipe. The verifier accepts only the OrganizeRecipeDraft schema from the skill.')
    if 'unsupported boolean field' in text and 'disposition' in text:
        hints.append('Use a single disposition enum. For supplemental/excluded files, write disposition: "non_bangumi_or_supplemental"; do not write non_bangumi_or_supplemental: true, supplemental: true, or exclude: true.')
    if 'unsupported boolean source-unit field' in text or ('source_unit' in text and 'boolean' in text.casefold()):
        hints.append('Use source_unit: "single_file_multi_episode" for one visible file that intentionally covers multiple Bangumi episodes; do not write boolean flags such as multi_episode: true or merged: true.')
    if 'group_ref' in text:
        hints.append('Use group_ref values from list_local_groups or get_local_selector_scaffold. group_ref only expands a local selector; subject_id, media_kind, episode_type/episode_id, or supplemental disposition still come from Bangumi evidence.')
    if 'rules' in text and ('missing' in text.casefold() or 'Field required' in text):
        hints.append('OrganizeRecipeDraft must include version, summary, and a non-empty rules array.')
    if visible_paths:
        hints.append(f'Use exact source_path strings from the visible local universe when writing exact_paths. Visible source_path sample: {visible_paths[:8]}')
    return _dedupe_nonempty(hints)


def _helper_check_repair_hints(helper_check: dict[str, Any]) -> list[str]:
    if helper_check.get('ok'):
        return []
    hints: list[str] = []
    issues = helper_check.get('issues')
    if isinstance(issues, list) and issues:
        hints.append(f'Local helper check reported issues: {issues[:5]}')
    error = str(helper_check.get('error') or '')
    if error:
        hints.append(f'Local helper check failed: {error}')
    stderr = str(helper_check.get('stderr') or '').strip()
    if stderr:
        hints.append(f'Local helper stderr: {stderr[:500]}')
    if not hints:
        hints.append('Local helper check did not accept the recipe; inspect organize_recipe_helper_check.json and repair the draft.')
    return _dedupe_nonempty(hints)


def _review_warning_hints(review_warnings: list[dict[str, Any]]) -> list[str]:
    hints: list[str] = []
    for warning in review_warnings:
        if not isinstance(warning, dict):
            continue
        hint = str(warning.get('repair_hint') or '').strip()
        if hint:
            hints.append(hint)
    return _dedupe_nonempty(hints)


def _dedupe_nonempty(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = str(value or '').strip()
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def _dedupe_dicts(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        key = json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _normalize_kind_hint(value: str) -> str:
    key = str(value or '').strip().casefold().replace('-', '_').replace(' ', '_')
    aliases = {
        'movie': 'movie',
        'film': 'movie',
        'theatrical': 'movie',
        'ova': 'ova',
        'oad': 'oad',
        'sp': 'special',
        'special': 'special',
        'tvsp': 'special',
        'tv_special': 'special',
        'regular': 'regular',
        'main': 'regular',
        'tv': 'regular',
        'episode': 'regular',
    }
    return aliases.get(key, '')


def _query_from_source_path(source_path: str) -> str:
    basename = _norm_path(source_path).rsplit('/', 1)[-1]
    stem = re.sub(r'\.[^.]+$', '', basename)
    stem = re.sub(r'\[[^\]]+\]|\([^)]+\)', ' ', stem)
    stem = re.sub(r'\b(BDRip|BluRay|BD|WEB[- ]?DL|WEBRip|x264|x265|HEVC|AVC|FLAC|AAC|Hi10P|10bit|1080p|720p|2160p)\b', ' ', stem, flags=re.IGNORECASE)
    stem = re.sub(r'\s+', ' ', stem).strip(' -_')
    return stem or basename


def _query_without_terminal_kind(query: str, kind_hint: str) -> str:
    if not query:
        return ''
    if _normalize_kind_hint(kind_hint) in {'ova', 'oad', 'special', 'movie'}:
        return re.sub(r'\b(OVA|OAD|SP|Special|TVSP|Movie)\s*\d*\b', ' ', query, flags=re.IGNORECASE).strip(' -_')
    return ''


def _recipe_episode_type_from_kind(kind_hint: str) -> str:
    kind = _normalize_kind_hint(kind_hint)
    if kind in {'movie', 'ova', 'oad'}:
        return kind
    if kind == 'special':
        return 'special'
    if kind == 'regular':
        return 'regular'
    return 'unknown'
