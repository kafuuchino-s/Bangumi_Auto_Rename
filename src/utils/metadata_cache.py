"""元数据缓存 —— SQLite(diskcache)后端。

历史:曾用「一个 API 响应一个碎 JSON + 每个 key 一个 lock 文件」布局,
实测产生数千碎文件 + 等量空 lock 占 inode,且过期条目只忽略不删、
`_locks/` 永不清理。现改为 diskcache(SQLite 索引 + 大 blob 外存),
内建 LRU eviction / expire / 跨进程原子互斥,首启自动迁移旧碎 JSON 树。

对外接口完全不变:`get_or_fetch` / `MetadataCacheMiss` / 4 态 mode / env。
调用方(`src/bangumi/client.py`、`src/rename/get_info.py`)零改动。

Windows 注意:SQLite WAL 在 NTFS 上易被杀毒拖慢,建议把 `data/cache/`
加入杀毒排除项(代码不处理,文档化)。
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import threading
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import diskcache

from ..config.config_manager import cm
from ..logger import logger
from .path import METADATA_CACHE_PATH

SCHEMA_VERSION = 1
DEFAULT_TTL_DAYS = 30
DEFAULT_NEGATIVE_TTL_HOURS = 6
VALID_MODES = {'read-write', 'cache-only', 'refresh', 'off'}

# 大 payload 外存阈值:32KB 以下内联 SQLite,避免再回到碎文件布局。
_DISK_MIN_FILE_SIZE = 32 * 1024
# 跨进程 advisory lock marker 的过期时间(秒):持锁进程崩溃后自愈上限。
_LOCK_EXPIRE_SECONDS = 120
# 等锁时轮询 read 的最大次数与间隔:约 3s 内等他人写入即返回。
_LOCK_WAIT_ATTEMPTS = 60
_LOCK_WAIT_INTERVAL = 0.05
# 旧碎 JSON 树迁移完成后写的哨兵文件。
_MIGRATED_SENTINEL = '.migrated_v1'

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


def _env_or_config_str(env_key: str, config_key: str, default: str) -> str:
    """env 优先（测试/override），否则读 config_manager，再否则硬编码默认。

    生产链路下前端改 config → cm.get_config 返回新值；测试用 monkeypatch 设 env
    覆盖；env 未设且 config 缺 key 时回落默认。cache_dir 不走此函数（路径语义不同）。
    """
    env_val = os.environ.get(env_key)
    if env_val is not None and env_val != '':
        return env_val
    cfg_val = cm.get_config(config_key)
    if isinstance(cfg_val, str) and cfg_val != '':
        return cfg_val
    return default


def _env_or_config_float(env_key: str, config_key: str, default: float) -> float:
    env_val = os.environ.get(env_key)
    if env_val is not None and env_val != '':
        try:
            return float(env_val)
        except (TypeError, ValueError):
            return default
    cfg_val = cm.get_config(config_key)
    if isinstance(cfg_val, (int, float)) and not isinstance(cfg_val, bool):
        return float(cfg_val)
    if isinstance(cfg_val, str) and cfg_val != '':
        try:
            return float(cfg_val)
        except ValueError:
            return default
    return default


def get_cache_mode() -> str:
    mode = _env_or_config_str('BAR_METADATA_CACHE_MODE', 'metadata_cache_mode', 'read-write')
    mode = mode.strip().lower()
    return mode if mode in VALID_MODES else 'read-write'


def get_cache_root() -> Path:
    override = os.environ.get('BAR_METADATA_CACHE_DIR')
    return Path(override).expanduser() if override else METADATA_CACHE_PATH


def _size_limit_bytes() -> int:
    mb = _env_or_config_float('BAR_METADATA_CACHE_MAX_SIZE_MB', 'metadata_cache_max_size_mb', 500.0)
    return int(max(1.0, mb) * 1024 * 1024)


def _ttl_for_payload(payload: Any) -> timedelta:
    if payload in ([], {}):
        hours = _env_or_config_float(
            'BAR_METADATA_CACHE_NEGATIVE_TTL_HOURS', 'metadata_cache_negative_ttl_hours', DEFAULT_NEGATIVE_TTL_HOURS
        )
        return timedelta(hours=max(1.0, hours))
    days = _env_or_config_float(
        'BAR_METADATA_CACHE_TTL_DAYS', 'metadata_cache_ttl_days', DEFAULT_TTL_DAYS
    )
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


def _get_thread_lock(lock_name: str) -> threading.Lock:
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(lock_name)
        if lock is None:
            lock = threading.Lock()
            _THREAD_LOCKS[lock_name] = lock
        return lock


@contextlib.contextmanager
def _cache_for(root: Path) -> Iterator[diskcache.Cache]:
    """打开一个 diskcache 实例,用完即关。

    不做模块级单例:测试会频繁切换 `BAR_METADATA_CACHE_DIR` 到各自 tmp_path,
    持久单例会残留旧 root 的 DB 句柄(Windows 下锁文件阻碍 tmp_path 清理)。
    每次 get_or_fetch 开一次 Cache,SQLite 打开是 ms 级,远低于被缓存的网络
    fetch(秒级),且保证调用返回后句柄立即释放。
    """
    root.mkdir(parents=True, exist_ok=True)
    cache = diskcache.Cache(
        str(root),
        size_limit=_size_limit_bytes(),
        disk_min_file_size=_DISK_MIN_FILE_SIZE,
    )
    try:
        yield cache
    finally:
        cache.close()


def _db_read(cache: diskcache.Cache, key_digest: str, *, allow_expired: bool = False) -> tuple[Any, bool]:
    """读信封并校验 schema_version / expires_at。命中返回 (payload, True)。"""
    entry = cache.get(key_digest, default=None)
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


def _db_write(cache: diskcache.Cache, key_digest: str, key: dict[str, Any], payload: Any, ttl: timedelta) -> None:
    now = _utc_now()
    entry = {
        'schema_version': SCHEMA_VERSION,
        'key': key,
        'created_at': now.isoformat().replace('+00:00', 'Z'),
        'expires_at': (now + ttl).isoformat().replace('+00:00', 'Z'),
        'payload': payload,
    }
    cache.set(key_digest, entry, expire=int(ttl.total_seconds()))


def _acquire_advisory_lock(cache: diskcache.Cache, lock_key: str) -> bool:
    """跨进程原子互斥:用 diskcache.add 的原子 INSERT 作 marker。

    `add` 仅在 key 不存在时写入成功(SQLite 串行化写),返回 True 即拿到锁。
    退出时 delete marker;持锁进程崩溃则 marker 在 `_LOCK_EXPIRE_SECONDS` 后
    被 diskcache expire 自动清理,等价于 4341 个 lock 文件的自愈但无需文件。
    """
    return cache.add(lock_key, 'locked', expire=_LOCK_EXPIRE_SECONDS)


def _release_advisory_lock(cache: diskcache.Cache, lock_key: str) -> None:
    with contextlib.suppress(Exception):
        cache.delete(lock_key)


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
    key_digest = _key_hash(key)
    lock_key = f'lock:{key_digest}'
    lock_name = key_digest

    with _cache_for(root) as cache:
        if mode != 'refresh':
            payload, hit = _db_read(cache, key_digest)
            if hit:
                return payload
            if mode == 'cache-only':
                raise MetadataCacheMiss(f'metadata cache miss: {provider}:{endpoint}')

        thread_lock = _get_thread_lock(lock_name)
        with thread_lock:
            if _acquire_advisory_lock(cache, lock_key):
                try:
                    # 拿到锁后再双检:等锁期间别进程可能已写入。
                    if mode != 'refresh':
                        payload, hit = _db_read(cache, key_digest)
                        if hit:
                            return payload
                        if mode == 'cache-only':
                            raise MetadataCacheMiss(f'metadata cache miss: {provider}:{endpoint}')

                    payload = fetcher()
                    if payload is None:
                        return None  # None 不存(调用方每次都要重新 fetch)
                    ttl = _ttl_for_payload(payload)
                    _db_write(cache, key_digest, key, payload, ttl)
                    return payload
                finally:
                    _release_advisory_lock(cache, lock_key)
            else:
                # 他人持锁:轮询等写入,命中返回;超时则降级自行 fetch(值仍正确)。
                for _ in range(_LOCK_WAIT_ATTEMPTS):
                    time.sleep(_LOCK_WAIT_INTERVAL)
                    payload, hit = _db_read(cache, key_digest)
                    if hit:
                        return payload
                    if mode == 'cache-only':
                        # cache-only 不 fetch,继续等;超时后由下方 miss 抛出。
                        continue
                if mode == 'cache-only':
                    raise MetadataCacheMiss(f'metadata cache miss: {provider}:{endpoint}')
                # 降级:锁超时但仍无值,自行 fetch(失去 stampede 保护但结果正确)。
                payload = fetcher()
                if payload is None:
                    return None
                ttl = _ttl_for_payload(payload)
                _db_write(cache, key_digest, key, payload, ttl)
                return payload


def gc_expired() -> None:
    """清理过期条目 + 死锁 marker + 超容量 LRU 淘汰。

    由启动入口与批次收尾(task_queue)调用。off 模式跳过。失败不抛,只记日志。
    """
    if get_cache_mode() == 'off':
        return
    root = get_cache_root()
    try:
        with _cache_for(root) as cache:
            cache.expire()  # 删过期条目(含过期 lock marker)
            cache.cull()  # 超 size_limit 时按 LRU 淘汰
    except Exception:
        logger.exception('[metadata_cache] gc_expired 失败')


def _read_legacy_entry(path: Path, *, allow_expired: bool = False) -> tuple[dict[str, Any], bool] | None:
    """读旧碎 JSON 信封(仅迁移用)。返回 (envelope, True) 或 None。"""
    try:
        with path.open('r', encoding='utf-8') as file:
            entry = json.load(file)
    except Exception:
        return None
    if not isinstance(entry, dict) or entry.get('schema_version') != SCHEMA_VERSION:
        return None
    expires_at = entry.get('expires_at')
    if isinstance(expires_at, str) and not allow_expired:
        try:
            if datetime.fromisoformat(expires_at.replace('Z', '+00:00')) <= _utc_now():
                return None
        except ValueError:
            return None
    return entry, True


def _legacy_provider_dirs(root: Path) -> list[Path]:
    """识别旧碎 JSON 树:`root/<provider>/v1/...`,排除 _locks 与 diskcache 自身。"""
    if not root.exists():
        return []
    dirs: list[Path] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        if child.name == '_locks' or child.name.startswith('.'):
            continue
        if (child / 'v1').is_dir():
            dirs.append(child)
    return dirs


def migrate_legacy() -> tuple[int, int]:
    """一次性迁移旧碎 JSON 树到 diskcache。幂等可中断。

    流程:扫 `root/<provider>/v1/<endpoint>/<digest[:2]>/<digest>.json` →
    过滤过期/非 v1 → `cache.set(digest, envelope, expire=剩余秒)` → 写哨兵。
    旧树保留不删(回滚保险),哨兵写入后后续启动跳过。单文件解析失败跳过不阻塞。
    """
    root = get_cache_root()
    sentinel = root / _MIGRATED_SENTINEL
    if sentinel.exists():
        return 0, 0
    provider_dirs = _legacy_provider_dirs(root)
    if not provider_dirs:
        # 无旧树:仍写哨兵,避免每次启动空扫。
        try:
            root.mkdir(parents=True, exist_ok=True)
            sentinel.write_text('no-legacy', encoding='utf-8')
        except Exception:
            logger.exception('[metadata_cache] 写迁移哨兵失败(无旧树)')
        return 0, 0

    migrated = 0
    skipped = 0
    try:
        with _cache_for(root) as cache:
            for prov_dir in provider_dirs:
                for entry_path in prov_dir.glob('v1/*/*/*.json'):
                    result = _read_legacy_entry(entry_path)
                    if result is None:
                        skipped += 1
                        continue
                    envelope, _ok = result
                    key = envelope.get('key')
                    if not isinstance(key, dict):
                        skipped += 1
                        continue
                    digest = _key_hash(key)
                    expires_at_raw = envelope.get('expires_at')
                    try:
                        expires_at = datetime.fromisoformat(str(expires_at_raw).replace('Z', '+00:00'))
                    except (ValueError, TypeError):
                        skipped += 1
                        continue
                    remaining = (expires_at - _utc_now()).total_seconds()
                    if remaining <= 0:
                        skipped += 1
                        continue
                    cache.set(digest, envelope, expire=int(remaining))
                    migrated += 1
        sentinel.write_text(f'migrated={migrated},skipped={skipped}', encoding='utf-8')
        logger.info(f'[metadata_cache] 迁移完成:from legacy JSON tree → migrated={migrated}, skipped={skipped}')
        logger.info(
            '[metadata_cache] 旧碎 JSON 树已保留作为回滚保险;确认新缓存正常后可手动删除 '
            f'{", ".join(str(p) for p in provider_dirs)} 与 {root / "_locks"}'
        )
    except Exception:
        logger.exception('[metadata_cache] 迁移失败,下次启动会重试(已迁移条目覆盖写无副作用)')
    return migrated, skipped


def migrate_legacy_if_needed() -> None:
    """启动入口用:off 模式跳过,否则执行迁移。失败不阻断启动。"""
    if get_cache_mode() == 'off':
        return
    try:
        migrate_legacy()
    except Exception:
        logger.exception('[metadata_cache] migrate_legacy_if_needed 异常')
