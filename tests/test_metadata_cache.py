from __future__ import annotations

import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

from src.utils.metadata_cache import MetadataCacheMiss, get_or_fetch


def _configure_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str = 'read-write') -> None:
    monkeypatch.setenv('BAR_METADATA_CACHE_DIR', str(tmp_path / 'metadata-cache'))
    monkeypatch.setenv('BAR_METADATA_CACHE_MODE', mode)


def test_metadata_cache_roundtrip_and_cache_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure_cache(monkeypatch, tmp_path)
    calls = {'count': 0}

    def fetch():
        calls['count'] += 1
        return {'ok': True}

    first = get_or_fetch(provider='tmdb', endpoint='search/tv', params={'query': 'ARIA'}, fetcher=fetch)
    second = get_or_fetch(provider='tmdb', endpoint='search/tv', params={'query': 'ARIA'}, fetcher=fetch)

    assert first == {'ok': True}
    assert second == {'ok': True}
    assert calls['count'] == 1

    monkeypatch.setenv('BAR_METADATA_CACHE_MODE', 'cache-only')
    cached = get_or_fetch(
        provider='tmdb',
        endpoint='search/tv',
        params={'query': 'ARIA'},
        fetcher=lambda: (_ for _ in ()).throw(AssertionError('network called')),
    )
    assert cached == {'ok': True}


def test_metadata_cache_cache_only_miss_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure_cache(monkeypatch, tmp_path, mode='cache-only')

    with pytest.raises(MetadataCacheMiss):
        get_or_fetch(provider='tmdb', endpoint='search/tv', params={'query': 'missing'}, fetcher=lambda: {'no': True})


def test_metadata_cache_does_not_store_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure_cache(monkeypatch, tmp_path)
    calls = {'count': 0}

    def fetch():
        calls['count'] += 1
        return None

    assert get_or_fetch(provider='tmdb', endpoint='search/tv', params={'query': 'timeout'}, fetcher=fetch) is None
    assert get_or_fetch(provider='tmdb', endpoint='search/tv', params={'query': 'timeout'}, fetcher=fetch) is None
    assert calls['count'] == 2


def _process_cache_fetch(cache_dir: str, counter_file: str) -> dict:
    os.environ['BAR_METADATA_CACHE_DIR'] = cache_dir
    os.environ['BAR_METADATA_CACHE_MODE'] = 'read-write'

    def fetch():
        time.sleep(0.1)
        path = Path(counter_file)
        count = int(path.read_text(encoding='utf-8')) if path.exists() else 0
        path.write_text(str(count + 1), encoding='utf-8')
        return {'value': 42}

    return get_or_fetch(provider='tmdb', endpoint='movie/details', params={'movie_id': 42}, fetcher=fetch)


def test_metadata_cache_multiprocess_same_key_fetches_once(tmp_path: Path):
    cache_dir = str(tmp_path / 'metadata-cache')
    counter_file = str(tmp_path / 'counter.txt')

    with ProcessPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(_process_cache_fetch, [cache_dir] * 4, [counter_file] * 4))

    assert results == [{'value': 42}] * 4
    assert json.loads(json.dumps({'count': int(Path(counter_file).read_text(encoding='utf-8'))}))['count'] == 1


def test_tmdb_search_uses_disk_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from src.rename.get_info import Search

    _configure_cache(monkeypatch, tmp_path)
    Search._tv_search_cache.clear()
    calls = {'count': 0}

    class FakeSearch:
        def __init__(self):
            self.results = []

        def tv(self, **kwargs):  # noqa: ANN001
            calls['count'] += 1
            self.results = [{'id': 1, 'name': 'Cached Show'}]

    monkeypatch.setattr('src.rename.get_info.tmdb.Search', FakeSearch)
    search = Search()
    assert search._tmdb_search_tv(query='Cached Show', language='en-US', year=2024) == [
        {'id': 1, 'name': 'Cached Show'}
    ]

    monkeypatch.setenv('BAR_METADATA_CACHE_MODE', 'cache-only')

    class FailingSearch:
        def tv(self, **kwargs):  # noqa: ANN001
            raise AssertionError('network called')

    monkeypatch.setattr('src.rename.get_info.tmdb.Search', FailingSearch)
    assert search._tmdb_search_tv(query='Cached Show', language='en-US', year=2024) == [
        {'id': 1, 'name': 'Cached Show'}
    ]
    assert calls['count'] == 1


def test_bangumi_request_uses_disk_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from src.bangumi.client import BangumiClient

    _configure_cache(monkeypatch, tmp_path)
    calls = {'count': 0}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {'data': [{'id': 1, 'type': 2, 'name': 'Cached', 'name_cn': '缓存'}]}

    def fake_request(**kwargs):  # noqa: ANN001
        calls['count'] += 1
        return FakeResponse()

    client = BangumiClient()
    monkeypatch.setattr(client.session, 'request', fake_request)
    first = client._request_json('post', '/v0/search/subjects', json={'keyword': 'Cached'})
    assert first['data'][0]['id'] == 1

    monkeypatch.setenv('BAR_METADATA_CACHE_MODE', 'cache-only')
    client2 = BangumiClient()
    monkeypatch.setattr(
        client2.session,
        'request',
        lambda **kwargs: (_ for _ in ()).throw(AssertionError('network called')),
    )
    second = client2._request_json('post', '/v0/search/subjects', json={'keyword': 'Cached'})
    assert second == first
    assert calls['count'] == 1
