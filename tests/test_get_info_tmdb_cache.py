from src.rename.get_info import Search


def setup_function(function):
    Search._multi_search_cache.clear()


class DummySearch(Search):
    def __init__(self):
        super().__init__()
        self.multi_calls = []

    def _get_search_languages(self, query: str):
        return ['en-US', 'ja-JP', 'zh-CN']

    def _tmdb_search_multi(self, *, query: str, language: str):
        self.multi_calls.append((query, language))
        if language == 'en-US':
            return [
                {'id': 1, 'media_type': 'tv', 'name': 'Alpha TV', 'popularity': 10},
                {'id': 2, 'media_type': 'movie', 'title': 'Alpha Movie', 'popularity': 8},
            ]
        if language == 'ja-JP':
            return [
                {'id': 3, 'media_type': 'tv', 'name': 'Beta TV', 'popularity': 7},
            ]
        return [
            {'id': 4, 'media_type': 'movie', 'title': 'Gamma Movie', 'popularity': 6},
        ]


class EmptyPrimarySearch(DummySearch):
    def _tmdb_search_multi(self, *, query: str, language: str):
        self.multi_calls.append((query, language))
        if language == 'en-US':
            return []
        return [
            {'id': 3, 'media_type': 'tv', 'name': 'Beta TV', 'popularity': 7},
        ]


def test_search_multi_by_query_uses_primary_only_when_strong():
    search = DummySearch()

    result = search.search_multi_by_query('Sample Query')

    assert [language for _, language in search.multi_calls] == ['en-US']
    assert [item['id'] for item in result] == [1, 2]
    assert all(item['_matched_query'] == 'Sample Query' for item in result)


def test_search_multi_by_query_fallbacks_when_primary_empty():
    search = EmptyPrimarySearch()

    result = search.search_multi_by_query('Sample Query')

    assert [language for _, language in search.multi_calls] == ['en-US', 'ja-JP', 'zh-CN']
    assert [item['id'] for item in result] == [3]


def test_search_multi_by_query_keeps_original_query_text():
    search = DummySearch()

    result = search.search_multi_by_query('  Sample   Query  ')

    assert search.multi_calls[0][0] == '  Sample   Query  '
    assert all(item['_matched_query'] == '  Sample   Query  ' for item in result)
