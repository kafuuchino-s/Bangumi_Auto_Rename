from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .path import METADATA_CACHE_PATH

SCHEMA_VERSION = 1
DEFAULT_TTL_DAYS = 30
DEFAULT_NEGATIVE_TTL_HOURS = 6
VALID_MODES = {'read-write', 'cache-only', 'refresh', 'off'}
_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


class MetadataCacheMiss(RuntimeError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _strip_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _strip_none(v) for k, v in sorted(value.items()) if v is not None}
    if isinstance(value, list):
        return [_strip_none(item) for item in value]
    return value


def get_cache_mode() -> str:
    mode = os.environ.get('BAR_METADATA_CACHE_MODE', 'read-write').strip().lower()
    return mode if mode in VALID_MODES else 'read-write'


def get_cache_root() -> Path:
    override = os.environ.get('BAR_METADATA_CACHE_DIR')
    return Path(override).expanduser() if override else METADATA_CACHE_PATH


def _ttl_for_payload(payload: Any) -> timedelta:
    if payload in ([], {}):
        hours = float(os.environ.get('BAR_METADATA_CACHE_NEGATIVE_TTL_HOURS', DEFAULT_NEGATIVE_TTL_HOURS))
        return timedelta(hours=max(1.0, hours))
    days = float(os.environ.get('BAR_METADATA_CACHE_TTL_DAYS', DEFAULT_TTL_DAYS))
    return timedelta(days=max(1.0, days))


def build_key(*, provider: str, endpoint: str, params: dict[str, Any] | None = None, body: Any = None) -> dict[str, Any]:
    return {
        'provider': provider,
        'schema': 'v1',
        'endpoint': endpoint,
        'params': _strip_none(params or {}),
        'body': _strip_none(body),
    }


def _key_hash(key: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(key).encode('utf-8')).hexdigest()


def _entry_path(root: Path, key: dict[str, Any]) -> Path:
    digest = _key_hash(key)
    provider = str(key.get('provider') or 'unknown')
    endpoint = str(key.get('endpoint') or 'unknown').replace('/', '_')
    return root / provider / 'v1' / endpoint / digest[:2] / f'{digest}.json'


def _lock_path(root: Path, key: dict[str, Any]) -> Path:
    digest = _key_hash(key)
    return root / '_locks' / f'{digest}.lock'


def _get_thread_lock(lock_name: str) -> threading.Lock:
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(lock_name)
        if lock is None:
            lock = threading.Lock()
            _THREAD_LOCKS[lock_name] = lock
        return lock


@contextlib.contextmanager
def _file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as file:
        if os.name == 'nt':
            import msvcrt

            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(file.fileno(), fcntl.LOCK_UN)


def _read_entry(path: Path, *, allow_expired: bool = False) -> tuple[Any, bool]:
    try:
        with path.open('r', encoding='utf-8') as file:
            entry = json.load(file)
    except Exception:
        return None, False
    if not isinstance(entry, dict) or entry.get('schema_version') != SCHEMA_VERSION:
        return None, False
    expires_at = entry.get('expires_at')
    if isinstance(expires_at, str) and not allow_expired:
        try:
            if datetime.fromisoformat(expires_at.replace('Z', '+00:00')) <= _utc_now():
                return None, False
        except ValueError:
            return None, False
    return entry.get('payload'), True


def _atomic_write(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            json.dump(entry, file, ensure_ascii=False, indent=2)
            file.write('\n')
            file.flush()
            os.fsync(file.fileno())
        for attempt in range(5):
            try:
                os.replace(tmp_name, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)


def get_or_fetch(
    *,
    provider: str,
    endpoint: str,
    params: dict[str, Any] | None = None,
    body: Any = None,
    fetcher: Callable[[], Any],
) -> Any:
    mode = get_cache_mode()
    if mode == 'off':
        return fetcher()

    key = build_key(provider=provider, endpoint=endpoint, params=params, body=body)
    root = get_cache_root()
    path = _entry_path(root, key)
    lock_name = _key_hash(key)

    if mode != 'refresh':
        payload, hit = _read_entry(path)
        if hit:
            return payload
        if mode == 'cache-only':
            raise MetadataCacheMiss(f'metadata cache miss: {provider}:{endpoint}')

    thread_lock = _get_thread_lock(lock_name)
    with thread_lock:
        with _file_lock(_lock_path(root, key)):
            if mode != 'refresh':
                payload, hit = _read_entry(path)
                if hit:
                    return payload
                if mode == 'cache-only':
                    raise MetadataCacheMiss(f'metadata cache miss: {provider}:{endpoint}')

            payload = fetcher()
            if payload is None:
                return None
            ttl = _ttl_for_payload(payload)
            now = _utc_now()
            entry = {
                'schema_version': SCHEMA_VERSION,
                'key': key,
                'created_at': now.isoformat().replace('+00:00', 'Z'),
                'expires_at': (now + ttl).isoformat().replace('+00:00', 'Z'),
                'payload': payload,
            }
            _atomic_write(path, entry)
            return payload
