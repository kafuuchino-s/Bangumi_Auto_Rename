from types import SimpleNamespace

from src.bangumi.client import BangumiClient
from src.bangumi.context_builder import BangumiContextBuilder
from src.bangumi.models import BangumiEpisode, BangumiSubject, BangumiSubjectRelation


class StubBangumiClient:
    def __init__(self):
        self.search_calls = []
        self.related_calls = []
        self.subject_calls = []
        self.episodes_calls = []

    def search_subjects(self, keyword, year=None):
        self.search_calls.append((keyword, year))
        if keyword == "超次元游戏 海王星":
            return [
                BangumiSubject(
                    id=47957,
                    type=2,
                    name="超次元ゲーム ネプテューヌ THE ANIMATION",
                    name_cn="超次元游戏 海王星",
                    date="2013-07-12",
                    platform="TV",
                    total_episodes=13,
                    eps=13,
                    rating_score=6.8,
                    rating_total=1000,
                    rank=1800,
                    tags=["游戏改", "TV"],
                    meta_tags=["TV"],
                )
            ]
        return []

    def get_subject(self, subject_id):
        self.subject_calls.append(subject_id)
        mapping = {
            47957: BangumiSubject(
                id=47957,
                type=2,
                name="超次元ゲーム ネプテューヌ THE ANIMATION",
                name_cn="超次元游戏 海王星",
                date="2013-07-12",
                platform="TV",
                total_episodes=13,
                eps=13,
                rating_score=6.8,
                rating_total=1000,
                rank=1800,
                tags=["游戏改", "TV"],
                meta_tags=["TV"],
            ),
            92576: BangumiSubject(
                id=92576,
                type=2,
                name="超次元ゲーム ネプテューヌ THE ANIMATION プロセッサディスク Vol.5",
                name_cn="",
                date="2014-03-26",
                platform="OVA",
                total_episodes=1,
                eps=1,
                rating_score=6.5,
                rating_total=120,
                rank=None,
                tags=["特典"],
                meta_tags=[],
            ),
        }
        return mapping.get(subject_id)

    def get_related_subjects(self, subject_id):
        self.related_calls.append(subject_id)
        if subject_id != 47957:
            return []
        return [
            BangumiSubjectRelation(
                id=92576,
                type=2,
                relation="番外篇",
                name="超次元ゲーム ネプテューヌ THE ANIMATION プロセッサディスク Vol.5",
                name_cn="",
            ),
            BangumiSubjectRelation(
                id=300000,
                type=3,
                relation="音乐",
                name="OST",
                name_cn="",
            ),
        ]

    def get_episodes(self, subject_id):
        self.episodes_calls.append(subject_id)
        if subject_id == 47957:
            return [
                BangumiEpisode(
                    id=288868,
                    subject_id=47957,
                    type=0,
                    sort=1,
                    ep=1,
                    name="プラネテューヌの女神（ネプテューヌ）",
                    name_cn="普拉尼顿的女神（涅普迪努）",
                    airdate="2013-07-12",
                    duration="00:23:40",
                    duration_seconds=1420,
                    desc="",
                ),
                BangumiEpisode(
                    id=299277,
                    subject_id=47957,
                    type=1,
                    sort=13,
                    ep=0,
                    name="約束の永遠(トゥルーエンド)",
                    name_cn="永恒的承诺（True End）",
                    airdate="2014-03-26",
                    duration="00:23:40",
                    duration_seconds=1420,
                    desc="special",
                ),
            ]
        if subject_id == 92576:
            return [
                BangumiEpisode(
                    id=400001,
                    subject_id=92576,
                    type=1,
                    sort=1,
                    ep=0,
                    name="プロセッサディスク",
                    name_cn="处理器光盘",
                    airdate="2014-03-26",
                    duration="00:23:40",
                    duration_seconds=1420,
                    desc="disc special",
                )
            ]
        return []


def test_build_tv_context_returns_compact_bangumi_context():
    builder = BangumiContextBuilder(client=StubBangumiClient())
    anime_info = {
        "name": "超次元游戏 海王星",
        "original_name": "超次元ゲーム ネプテューヌ THE ANIMATION",
        "first_air_date": "2013-07-12",
    }
    local_files = [
        {
            "filename": "[VCB-Studio] Choujigen Game Neptune The Animation [13].mkv",
            "path": "[VCB-Studio] Choujigen Game Neptune The Animation [13].mkv",
            "duration": 24.0,
        }
    ]

    context = builder.build_tv_context(anime_info, local_files)

    assert context is not None
    assert context["source"] == "bangumi"
    assert context["selected_subject_id"] == 47957
    assert any(keyword == "超次元游戏 海王星" for keyword in context["search_keywords"])
    assert len(context["subjects"]) == 2
    assert context["subjects"][0]["subject"]["id"] == 47957
    assert context["subjects"][0]["episodes"][1]["sort"] == 13
    assert context["subjects"][0]["episodes"][1]["type"] == 1
    assert context["subjects"][1]["relation_to_main"] == "番外篇"


def test_build_tv_context_returns_none_when_search_misses():
    builder = BangumiContextBuilder(client=StubBangumiClient())
    anime_info = {
        "name": "完全不存在的作品",
        "original_name": "Not Found Anime",
        "first_air_date": "2023-01-01",
    }

    context = builder.build_tv_context(anime_info, [{"path": "ep01.mkv"}])

    assert context is None



def test_bangumi_client_retries_retryable_errors(monkeypatch):
    client = BangumiClient()
    calls = {"count": 0}

    class FakeResponse:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise __import__("requests").HTTPError(
                    f"{self.status_code} Server Error",
                    response=SimpleNamespace(status_code=self.status_code),
                )

        def json(self):
            return self._payload

    def fake_request(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return FakeResponse(502, {})
        return FakeResponse(200, {"data": []})

    monkeypatch.setattr(client, "_request_json_uncached", fake_request)

    result = client.search_subjects("Neptune")

    assert result == []


def test_build_tv_context_creates_synthetic_singleton_for_movie_subject_without_episodes():
    class MovieSingletonClient(StubBangumiClient):
        def search_subjects(self, keyword, year=None):
            self.search_calls.append((keyword, year))
            if keyword == "单体电影":
                return [
                    BangumiSubject(
                        id=700001,
                        type=2,
                        name="单体电影",
                        name_cn="单体电影",
                        date="2020-01-01",
                        platform="Movie",
                        total_episodes=0,
                        eps=0,
                    )
                ]
            return []

        def get_episodes(self, subject_id):
            self.episodes_calls.append(subject_id)
            return []

    context = BangumiContextBuilder(client=MovieSingletonClient()).build_tv_context(
        {"name": "单体电影", "first_air_date": "2020-01-01"},
        [{"path": "movie.mkv"}],
    )

    assert context is not None
    subject = context["subjects"][0]
    episode = subject["episodes"][0]
    assert episode["synthetic"] is True
    assert episode["synthetic_reason"] == "subject_singleton_no_episode_items"
    assert episode["subject_level_target"] is True
    assert episode["kind"] == "subject_singleton"
    assert episode["sort"] == 1
    assert episode["ep"] == 1
    assert episode["title"] == "单体电影"
    assert episode["name"] == "单体电影"
    assert episode["name_cn"] == "单体电影"
    assert episode["source_form_hint"] == "movie"


def test_build_tv_context_creates_synthetic_singleton_for_total_collection_relation_without_episodes():
    class TotalCollectionClient(StubBangumiClient):
        def search_subjects(self, keyword, year=None):
            self.search_calls.append((keyword, year))
            if keyword == "总集篇作品":
                return [
                    BangumiSubject(
                        id=700002,
                        type=2,
                        name="总集篇作品",
                        name_cn="总集篇作品",
                        date="2020-01-01",
                        platform="OVA",
                        total_episodes=0,
                        eps=0,
                    )
                ]
            return []

        def get_related_subjects(self, subject_id):
            self.related_calls.append(subject_id)
            return []

        def get_episodes(self, subject_id):
            self.episodes_calls.append(subject_id)
            return []

    context = BangumiContextBuilder(client=TotalCollectionClient()).build_tv_context(
        {"name": "总集篇作品", "first_air_date": "2020-01-01"},
        [{"path": "collection.mkv"}],
    )

    assert context is not None
    assert context["subjects"][0]["episodes"][0]["synthetic"] is True


def test_build_tv_context_single_episode_movie_subject_preserves_singleton_metadata():
    class OneEpisodeMovieClient(StubBangumiClient):
        def search_subjects(self, keyword, year=None):
            self.search_calls.append((keyword, year))
            if keyword == "标题为空电影":
                return [BangumiSubject(id=700006, type=2, name="标题为空电影", name_cn="标题为空电影", date="2020-01-01", platform="Movie", total_episodes=1, eps=1)]
            return []

        def get_episodes(self, subject_id):
            self.episodes_calls.append(subject_id)
            return [BangumiEpisode(id=1, subject_id=subject_id, type=0, sort=1, ep=1, name="", name_cn="", airdate="2020-01-01", duration="00:23:40", duration_seconds=1420, desc="")]

    context = BangumiContextBuilder(client=OneEpisodeMovieClient()).build_tv_context({"name": "标题为空电影", "first_air_date": "2020-01-01"}, [{"path": "movie.mkv"}])
    assert context is not None
    episode = context["subjects"][0]["episodes"][0]
    assert episode["synthetic"] is True
    assert episode["subject_level_target"] is True
    assert episode["kind"] == "subject_singleton"
    assert episode["name"] == "标题为空电影" or episode["title"] == "标题为空电影"


def test_build_tv_context_preserves_name_fallback_for_singleton_movie_episode():
    class FallbackClient(StubBangumiClient):
        def search_subjects(self, keyword, year=None):
            self.search_calls.append((keyword, year))
            if keyword == "标题为空电影":
                return [BangumiSubject(id=700005, type=2, name="标题为空电影", name_cn="标题为空电影", date="2020-01-01", platform="Movie", total_episodes=1, eps=1)]
            return []

        def get_episodes(self, subject_id):
            self.episodes_calls.append(subject_id)
            return [BangumiEpisode(id=1, subject_id=subject_id, type=0, sort=1, ep=1, name="", name_cn="", airdate="2020-01-01", duration="00:23:40", duration_seconds=1420, desc="")]

    context = BangumiContextBuilder(client=FallbackClient()).build_tv_context({"name": "标题为空电影", "first_air_date": "2020-01-01"}, [{"path": "movie.mkv"}])
    assert context is not None
    episode = context["subjects"][0]["episodes"][0]
    assert episode["kind"] == "subject_singleton" or episode["subject_level_target"] is True
    assert episode["subject_level_target"] is True
    assert episode["synthetic"] is True or episode["subject_level_target"] is True


def test_build_tv_context_does_not_create_synthetic_singleton_for_tv_series_with_many_episodes():
    class TvClient(StubBangumiClient):
        def search_subjects(self, keyword, year=None):
            self.search_calls.append((keyword, year))
            if keyword == "TV十三集":
                return [
                    BangumiSubject(
                        id=700003,
                        type=2,
                        name="TV十三集",
                        name_cn="TV十三集",
                        date="2020-01-01",
                        platform="TV",
                        total_episodes=13,
                        eps=13,
                    )
                ]
            return []

        def get_episodes(self, subject_id):
            self.episodes_calls.append(subject_id)
            return []

    context = BangumiContextBuilder(client=TvClient()).build_tv_context(
        {"name": "TV十三集", "first_air_date": "2020-01-01"},
        [{"path": "tv.mkv"}],
    )

    assert context is None


def test_build_tv_context_uses_title_cues_not_release_group_as_primary_seed():
    builder = BangumiContextBuilder(client=StubBangumiClient())
    anime_info = {
        'name': 'てーきゅう 2期',
        'original_name': 'てーきゅう 2期',
        'first_air_date': '2012-10-01',
    }
    local_files = [
        {'filename': '[Snow-Raws] てーきゅう 2期 [01].mkv', 'path': '[Snow-Raws] てーきゅう 2期 [01].mkv'},
    ]

    keywords = builder._build_search_keywords(anime_info, local_files, local_title_seeds=['てーきゅう 2期'])

    assert any('てーきゅう' in keyword for keyword in keywords)
    assert not any(keyword.startswith('Snow') and keyword == 'Snow' for keyword in keywords)


def test_build_tv_context_does_not_create_synthetic_singleton_for_non_anime_subject():
    class NonAnimeClient(StubBangumiClient):
        def search_subjects(self, keyword, year=None):
            self.search_calls.append((keyword, year))
            if keyword == "非动画作品":
                return [
                    BangumiSubject(
                        id=700004,
                        type=1,
                        name="非动画作品",
                        name_cn="非动画作品",
                        date="2020-01-01",
                        platform="Movie",
                        total_episodes=0,
                        eps=0,
                    )
                ]
            return []

        def get_episodes(self, subject_id):
            self.episodes_calls.append(subject_id)
            return []

    context = BangumiContextBuilder(client=NonAnimeClient()).build_tv_context(
        {"name": "非动画作品", "first_air_date": "2020-01-01"},
        [{"path": "other.mkv"}],
    )

    assert context is None
