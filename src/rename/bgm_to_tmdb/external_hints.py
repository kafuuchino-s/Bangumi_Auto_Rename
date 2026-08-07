from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from ...config.config_manager import cm

REPO_ROOT = Path(__file__).resolve().parents[3]
_MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
_TMDB_LOCATOR_RE = re.compile(
    r"^(?P<media_type>tv|movie)[/:](?P<tmdb_id>\d+)"
    r"(?:/season/(?P<season>\d+))?"
    r"(?:/episode/(?P<episode>\d+))?$",
    re.IGNORECASE,
)

ExternalHintProvider = Literal['BangumiExtLinker', 'FribbAnimeLists']


class ExternalMappingHintError(ValueError):
    """Raised only for malformed caller-supplied mapping hint values."""


@dataclass(frozen=True)
class ExternalTmdbHint:
    provider: ExternalHintProvider
    bangumi_subject_id: int
    anidb_id: int | None
    tmdb_ref: str
    season_number: int | None
    episode_offset: int | None
    locator: str
    source_revision: str
    match_basis: str

    def payload(self) -> dict[str, Any]:
        return {
            'provider': self.provider,
            'bangumi_subject_id': self.bangumi_subject_id,
            'anidb_id': self.anidb_id,
            'tmdb_ref': self.tmdb_ref,
            'season_number': self.season_number,
            'episode_offset': self.episode_offset,
            'locator': self.locator,
            'source_revision': self.source_revision,
            'match_basis': self.match_basis,
            'evidence_only': True,
        }


@dataclass(frozen=True)
class ExternalMappingIndex:
    hints_by_subject: dict[int, tuple[ExternalTmdbHint, ...]]
    provider_status: dict[str, dict[str, Any]]
    issues: tuple[str, ...] = ()

    @classmethod
    def empty(cls) -> 'ExternalMappingIndex':
        return cls(hints_by_subject={}, provider_status={}, issues=())

    def hints_for_subject(self, bangumi_subject_id: int) -> tuple[ExternalTmdbHint, ...]:
        return self.hints_by_subject.get(int(bangumi_subject_id), ())

    @property
    def hint_count(self) -> int:
        return sum(len(hints) for hints in self.hints_by_subject.values())

    @property
    def subject_count(self) -> int:
        return len(self.hints_by_subject)

    def audit_payload(self) -> dict[str, Any]:
        return {
            'subject_count': self.subject_count,
            'hint_count': self.hint_count,
            'provider_status': self.provider_status,
            'issues': list(self.issues),
        }


@dataclass(frozen=True)
class _Snapshot:
    provider: ExternalHintProvider
    path: str
    revision: str
    records: tuple[dict[str, Any], ...]
    status: dict[str, Any]
    issues: tuple[str, ...]


def load_configured_external_mapping_index() -> tuple[str, ExternalMappingIndex]:
    mode = str(cm.get_config('rename_bgm_external_hints_mode') or 'off').strip().casefold()
    if mode not in {'off', 'shadow', 'assist'}:
        mode = 'off'
    if mode == 'off':
        return mode, ExternalMappingIndex.empty()
    return mode, load_external_mapping_index(
        extlinker_path=str(cm.get_config('rename_bgm_extlinker_snapshot_path') or ''),
        fribb_path=str(cm.get_config('rename_bgm_fribb_snapshot_path') or ''),
    )


def load_external_mapping_index(*, extlinker_path: str = '', fribb_path: str = '') -> ExternalMappingIndex:
    ext_signature = _path_signature(extlinker_path)
    fribb_signature = _path_signature(fribb_path)
    return _load_external_mapping_index_cached(
        extlinker_path,
        ext_signature,
        fribb_path,
        fribb_signature,
    )


def clear_external_mapping_index_cache() -> None:
    _load_external_mapping_index_cached.cache_clear()


@lru_cache(maxsize=8)
def _load_external_mapping_index_cached(
    extlinker_path: str,
    ext_signature: tuple[str, int, int],
    fribb_path: str,
    fribb_signature: tuple[str, int, int],
) -> ExternalMappingIndex:
    del ext_signature, fribb_signature
    ext_snapshot = _read_snapshot('BangumiExtLinker', extlinker_path)
    fribb_snapshot = _read_snapshot('FribbAnimeLists', fribb_path)

    hints_by_subject: dict[int, list[ExternalTmdbHint]] = {}
    issues = list(ext_snapshot.issues) + list(fribb_snapshot.issues)

    ext_records_by_bgm: dict[int, dict[str, Any]] = {}
    ext_missing_ids = 0
    for record in ext_snapshot.records:
        bgm_id = _positive_int(record.get('bgm_id'))
        if bgm_id is None:
            ext_missing_ids += 1
            continue
        ext_records_by_bgm.setdefault(bgm_id, record)
        for hint in _extlinker_hints(record, ext_snapshot.revision):
            hints_by_subject.setdefault(bgm_id, []).append(hint)
    if ext_missing_ids:
        issues.append(f'BangumiExtLinker: ignored {ext_missing_ids} rows without positive bgm_id')

    fribb_by_anidb: dict[int, dict[str, Any]] = {}
    fribb_missing_ids = 0
    for record in fribb_snapshot.records:
        anidb_id = _positive_int(record.get('anidb_id'))
        if anidb_id is None:
            fribb_missing_ids += 1
            continue
        fribb_by_anidb.setdefault(anidb_id, record)
    if fribb_missing_ids:
        issues.append(f'FribbAnimeLists: ignored {fribb_missing_ids} rows without positive anidb_id')

    for bgm_id, ext_record in ext_records_by_bgm.items():
        anidb_id = _positive_int(ext_record.get('anidb_id'))
        if anidb_id is None:
            continue
        fribb_record = fribb_by_anidb.get(anidb_id)
        if fribb_record is None:
            continue
        for hint in _fribb_hints(
            fribb_record,
            bangumi_subject_id=bgm_id,
            anidb_id=anidb_id,
            source_revision=fribb_snapshot.revision,
        ):
            hints_by_subject.setdefault(bgm_id, []).append(hint)

    normalized: dict[int, tuple[ExternalTmdbHint, ...]] = {}
    for bgm_id, hints in hints_by_subject.items():
        unique: dict[tuple[str, str, int | None, int | None], ExternalTmdbHint] = {}
        for hint in hints:
            key = (hint.provider, hint.tmdb_ref, hint.season_number, hint.episode_offset)
            unique.setdefault(key, hint)
        normalized[bgm_id] = tuple(
            sorted(
                unique.values(),
                key=lambda hint: (
                    hint.tmdb_ref,
                    hint.season_number is None,
                    hint.season_number or -1,
                    hint.provider,
                ),
            )
        )

    provider_status = {
        snapshot.provider: snapshot.status
        for snapshot in (ext_snapshot, fribb_snapshot)
        if snapshot.path
    }
    return ExternalMappingIndex(
        hints_by_subject=normalized,
        provider_status=provider_status,
        issues=tuple(_dedupe_nonempty(issues)),
    )


def _read_snapshot(provider: ExternalHintProvider, configured_path: str) -> _Snapshot:
    path_text = str(configured_path or '').strip()
    if not path_text:
        return _Snapshot(provider, '', '', (), {'status': 'not_configured'}, ())
    path = Path(path_text)
    if not path.is_absolute():
        path = REPO_ROOT / path
    try:
        stat = path.stat()
    except OSError as exc:
        issue = f'{provider}: snapshot unavailable: {path.name}: {type(exc).__name__}'
        return _Snapshot(provider, str(path), '', (), {'status': 'unavailable'}, (issue,))
    if stat.st_size > _MAX_SNAPSHOT_BYTES:
        issue = f'{provider}: snapshot exceeds {_MAX_SNAPSHOT_BYTES} bytes: {path.name}'
        return _Snapshot(provider, str(path), '', (), {'status': 'too_large'}, (issue,))
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode('utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        issue = f'{provider}: invalid snapshot {path.name}: {type(exc).__name__}'
        return _Snapshot(provider, str(path), '', (), {'status': 'invalid'}, (issue,))
    if not isinstance(payload, list):
        issue = f'{provider}: snapshot root must be a JSON array: {path.name}'
        return _Snapshot(provider, str(path), '', (), {'status': 'invalid'}, (issue,))
    records = tuple(item for item in payload if isinstance(item, dict))
    ignored = len(payload) - len(records)
    issues = () if ignored == 0 else (f'{provider}: ignored {ignored} non-object rows',)
    revision = f'sha256:{hashlib.sha256(raw).hexdigest()[:16]}'
    status = {
        'status': 'loaded',
        'record_count': len(records),
        'revision': revision,
    }
    return _Snapshot(provider, str(path), revision, records, status, issues)


def _extlinker_hints(record: dict[str, Any], source_revision: str) -> list[ExternalTmdbHint]:
    bgm_id = _positive_int(record.get('bgm_id'))
    if bgm_id is None:
        return []
    raw_locator = str(record.get('tmdb_id') or '').strip()
    parsed = _parse_tmdb_locator(raw_locator)
    if parsed is None:
        return []
    tmdb_ref, season_number, _ = parsed
    return [
        ExternalTmdbHint(
            provider='BangumiExtLinker',
            bangumi_subject_id=bgm_id,
            anidb_id=_positive_int(record.get('anidb_id')),
            tmdb_ref=tmdb_ref,
            season_number=season_number,
            episode_offset=None,
            locator=raw_locator,
            source_revision=source_revision,
            match_basis='bangumi_subject_id',
        )
    ]


def _fribb_hints(
    record: dict[str, Any],
    *,
    bangumi_subject_id: int,
    anidb_id: int,
    source_revision: str,
) -> list[ExternalTmdbHint]:
    raw_tmdb = record.get('themoviedb_id')
    if not isinstance(raw_tmdb, dict):
        return []
    season_number = _nested_int(record.get('season'), 'tmdb')
    episode_offset = _nested_int(record.get('episode_offset'), 'tmdb')
    hints: list[ExternalTmdbHint] = []
    for media_type in ('tv', 'movie'):
        values = raw_tmdb.get(media_type)
        if not isinstance(values, list):
            values = [values]
        for value in values:
            parsed = _parse_tmdb_locator(value, media_type=media_type)
            if parsed is None:
                continue
            tmdb_ref, locator_season, _ = parsed
            hints.append(
                ExternalTmdbHint(
                    provider='FribbAnimeLists',
                    bangumi_subject_id=bangumi_subject_id,
                    anidb_id=anidb_id,
                    tmdb_ref=tmdb_ref,
                    season_number=locator_season if locator_season is not None else season_number,
                    episode_offset=episode_offset,
                    locator=f'{media_type}:{value}',
                    source_revision=source_revision,
                    match_basis='bangumi_subject_id→anidb_id',
                )
            )
    return hints


def _parse_tmdb_locator(
    value: Any,
    *,
    media_type: str = '',
) -> tuple[str, int | None, int | None] | None:
    raw = str(value or '').strip()
    if not raw:
        return None
    match = _TMDB_LOCATOR_RE.fullmatch(raw)
    if match is None and media_type:
        match = _TMDB_LOCATOR_RE.fullmatch(f'{media_type}/{raw}')
    if match is None:
        return None
    normalized_type = match.group('media_type').casefold()
    tmdb_id = int(match.group('tmdb_id'))
    season = match.group('season')
    episode = match.group('episode')
    return (
        f'{normalized_type}:{tmdb_id}',
        int(season) if season is not None else None,
        int(episode) if episode is not None else None,
    )


def _path_signature(configured_path: str) -> tuple[str, int, int]:
    path_text = str(configured_path or '').strip()
    if not path_text:
        return ('', 0, 0)
    path = Path(path_text)
    if not path.is_absolute():
        path = REPO_ROOT / path
    try:
        stat = path.stat()
    except OSError:
        return (str(path), 0, 0)
    return (str(path), stat.st_mtime_ns, stat.st_size)


def _nested_int(value: Any, key: str) -> int | None:
    raw = value.get(key) if isinstance(value, dict) else value
    return _integer(raw)


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _positive_int(value: Any) -> int | None:
    number = _integer(value)
    return number if number is not None and number > 0 else None


def _dedupe_nonempty(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or '').strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result
