"""metadata_cache SQLite(diskcache)后端的新增测试。

旧的契约测试在 test_metadata_cache.py(不改动),这里覆盖:
- 旧碎 JSON 树 → diskcache 迁移(过期不迁、旧树保留、哨兵写入)
- gc_expired 删过期条目 + 死锁 marker
- size cap 超限 LRU 淘汰
- schema_version 不匹配当 miss
- DB 后端 stampede 保护(复用多进程模式)
"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import diskcache
import pytest

from src.utils import metadata_cache as mc
from src.utils.metadata_cache import MetadataCacheMiss, get_or_fetch


def _configure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str = 'read-write') -> Path:
    root = tmp_path / 'metadata-cache'
    monkeypatch.setenv('BAR_METADATA_CACHE_DIR', str(root))
    monkeypatch.setenv('BAR_METADATA_CACHE_MODE', mode)
    return root


def _write_legacy_tree(root: Path, entries: list[tuple[str, str, dict]]) -> None:
    """写旧碎 JSON 树:root/<provider>/v1/<endpoint>/<digest[:2]>/<digest>.json。

    entries: [(provider, endpoint, envelope), ...]，envelope 由调用方构造(含 key/expires_at)。
    digest 用 mc._key_hash(envelope['key']) 算，与旧实现 _entry_path 一致。
    """
    for provider, endpoint, envelope in entries:
        digest = mc._key_hash(envelope['key'])
        path = root / provider / 'v1' / endpoint / digest[:2] / f'{digest}.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding='utf-8')


def _envelope(payload, *, ttl_seconds=3600, schema_version=1, query='X') -> dict:
    now = datetime.now(timezone.utc)
    return {
        'schema_version': schema_version,
        'key': mc.build_key(provider='tmdb', endpoint='search/tv', params={'query': query}),
        'created_at': now.isoformat().replace('+00:00', 'Z'),
        'expires_at': (now + timedelta(seconds=ttl_seconds)).isoformat().replace('+00:00', 'Z'),
        'payload': payload,
    }


def test_migrate_legacy_moves_entries_and_keeps_tree(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    root = _configure(monkeypatch, tmp_path)
    fresh = _envelope({'ok': 1}, ttl_seconds=3600, query='X')
    expired = _envelope({'ok': 2}, ttl_seconds=-60, query='Y')  # 不同 key + 已过期
    _write_legacy_tree(root, [('tmdb', 'search_tv', fresh), ('tmdb', 'search_tv', expired)])
    # 清掉缓存实例，确保从旧树迁移
    migrated, skipped = mc.migrate_legacy()
    assert migrated == 1
    assert skipped == 1
    # 旧树保留(回滚保险)
    assert (root / 'tmdb' / 'v1').is_dir()
    # 哨兵写入
    assert (root / '.migrated_v1').exists()
    # 迁移后 cache-only 能命中 fresh，证明已落入 DB
    monkeypatch.setenv('BAR_METADATA_CACHE_MODE', 'cache-only')
    got = get_or_fetch(
        provider='tmdb', endpoint='search/tv', params={'query': 'X'},
        fetcher=lambda: (_ for _ in ()).throw(AssertionError('network called')),
    )
    assert got == {'ok': 1}
    # 再调 migrate 幂等:哨兵存在 → (0, 0)
    assert mc.migrate_legacy() == (0, 0)


def test_migrate_legacy_no_tree_writes_sentinel(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    root = _configure(monkeypatch, tmp_path)
    migrated, skipped = mc.migrate_legacy()
    assert migrated == 0 and skipped == 0
    assert (root / '.migrated_v1').exists()


def test_gc_expired_removes_expired_and_dead_locks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    root = _configure(monkeypatch, tmp_path)
    # 写一个有效条目 + 一个过期条目 + 一个死锁 marker
    get_or_fetch(provider='tmdb', endpoint='movie/details', params={'id': 1}, fetcher=lambda: {'alive': True})
    # 手写一个过期条目进 DB
    digest = mc._key_hash(mc.build_key(provider='tmdb', endpoint='movie/details', params={'id': 2}))
    with diskcache.Cache(str(root), size_limit=mc._size_limit_bytes(), disk_min_file_size=mc._DISK_MIN_FILE_SIZE) as c:
        c.set(digest, {'schema_version': 1, 'expires_at': '2000-01-01T00:00:00Z', 'payload': {'dead': True}}, expire=1)
        c.add('lock:deadmarker', 'locked', expire=1)
    time.sleep(1.2)
    mc.gc_expired()
    # 有效条目仍在
    monkeypatch.setenv('BAR_METADATA_CACHE_MODE', 'cache-only')
    assert get_or_fetch(
        provider='tmdb', endpoint='movie/details', params={'id': 1},
        fetcher=lambda: (_ for _ in ()).throw(AssertionError('network called')),
    ) == {'alive': True}
    # 过期条目 miss
    with pytest.raises(MetadataCacheMiss):
        get_or_fetch(
            provider='tmdb', endpoint='movie/details', params={'id': 2},
            fetcher=lambda: {'dead': True},
        )


def test_schema_version_mismatch_treated_as_miss(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    root = _configure(monkeypatch, tmp_path)
    digest = mc._key_hash(mc.build_key(provider='tmdb', endpoint='search/tv', params={'query': 'S'}))
    with diskcache.Cache(str(root), size_limit=mc._size_limit_bytes(), disk_min_file_size=mc._DISK_MIN_FILE_SIZE) as c:
        c.set(
            digest,
            {'schema_version': 999, 'expires_at': '9999-01-01T00:00:00Z', 'payload': {'bad': True}},
            expire=999999,
        )
    # schema 不匹配当 miss → 触发 fetcher 重新取
    calls = {'n': 0}

    def fetch():
        calls['n'] += 1
        return {'fresh': True}

    got = get_or_fetch(provider='tmdb', endpoint='search/tv', params={'query': 'S'}, fetcher=fetch)
    assert got == {'fresh': True}
    assert calls['n'] == 1


def test_size_cap_culls_lru(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # 用极小 size cap(1MB) + 写入多个大 payload 触发淘汰
    root = _configure(monkeypatch, tmp_path)
    monkeypatch.setenv('BAR_METADATA_CACHE_MAX_SIZE_MB', '1')

    big = {'data': 'x' * (200 * 1024)}  # ~200KB/条
    for i in range(20):  # 共 ~4MB，远超 1MB
        get_or_fetch(provider='tmdb', endpoint='movie/details', params={'id': i}, fetcher=lambda i=i: dict(big, id=i))
    mc.gc_expired()  # 触发 cull
    # DB 体积应被压到 size_limit 量级(而非无界增长到 4MB)
    with diskcache.Cache(str(root), size_limit=mc._size_limit_bytes(), disk_min_file_size=mc._DISK_MIN_FILE_SIZE) as c:
        total = c.volume()
    assert total <= 2 * 1024 * 1024, f'cull 后体积 {total} 未收敛到 size_limit 量级'


def _proc_stampede(cache_dir: str, counter_file: str) -> dict:
    os.environ['BAR_METADATA_CACHE_DIR'] = cache_dir
    os.environ['BAR_METADATA_CACHE_MODE'] = 'read-write'

    def fetch():
        time.sleep(0.1)
        path = Path(counter_file)
        count = int(path.read_text(encoding='utf-8')) if path.exists() else 0
        path.write_text(str(count + 1), encoding='utf-8')
        return {'value': 42}

    return get_or_fetch(provider='tmdb', endpoint='movie/details', params={'movie_id': 42}, fetcher=fetch)


def test_sqlite_backend_multiprocess_same_key_fetches_once(tmp_path: Path):
    cache_dir = str(tmp_path / 'metadata-cache')
    counter_file = str(tmp_path / 'counter.txt')
    with ProcessPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(_proc_stampede, [cache_dir] * 4, [counter_file] * 4))
    assert results == [{'value': 42}] * 4
    assert int(Path(counter_file).read_text(encoding='utf-8')) == 1


def test_config_takes_effect_when_env_unset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """env 未设时，cm.get_config 的值生效（前端接入核心契约）。"""
    _configure(monkeypatch, tmp_path)
    # 清掉所有 cache env，确保走 config 路径
    for env_key in (
        'BAR_METADATA_CACHE_MODE',
        'BAR_METADATA_CACHE_MAX_SIZE_MB',
        'BAR_METADATA_CACHE_TTL_DAYS',
        'BAR_METADATA_CACHE_NEGATIVE_TTL_HOURS',
    ):
        monkeypatch.delenv(env_key, raising=False)

    from src.config.config_manager import cm

    # max_size_mb: config=100 → 100MB
    monkeypatch.setitem(cm.config, 'metadata_cache_max_size_mb', 100)
    assert mc._size_limit_bytes() == 100 * 1024 * 1024

    # mode: config=off → off
    monkeypatch.setitem(cm.config, 'metadata_cache_mode', 'off')
    assert mc.get_cache_mode() == 'off'

    # ttl_days: config=7 → 7 天
    monkeypatch.setitem(cm.config, 'metadata_cache_ttl_days', 7)
    assert mc._ttl_for_payload({'x': 1}) == timedelta(days=7)

    # negative ttl: config=2 → 2 小时
    monkeypatch.setitem(cm.config, 'metadata_cache_negative_ttl_hours', 2)
    assert mc._ttl_for_payload([]) == timedelta(hours=2)


def test_env_overrides_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """env 优先于 config（测试/override 语义）。"""
    _configure(monkeypatch, tmp_path)
    from src.config.config_manager import cm

    monkeypatch.setitem(cm.config, 'metadata_cache_max_size_mb', 100)
    monkeypatch.setenv('BAR_METADATA_CACHE_MAX_SIZE_MB', '200')
    assert mc._size_limit_bytes() == 200 * 1024 * 1024

    monkeypatch.setitem(cm.config, 'metadata_cache_mode', 'off')
    monkeypatch.setenv('BAR_METADATA_CACHE_MODE', 'cache-only')
    assert mc.get_cache_mode() == 'cache-only'
