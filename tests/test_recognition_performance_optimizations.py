from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from src.ai.client import AIClient
from src.bangumi.client import BangumiClient
from src.rename.get_info import Search
from src.rename.process import Rename


class FakeTVSeason:
    calls = []
    payloads = {}

    def __init__(self, tv_id, season_number):
        self.tv_id = tv_id
        self.season_number = season_number

    def info(self, language="zh-CN"):
        FakeTVSeason.calls.append((self.tv_id, self.season_number, language))
        return FakeTVSeason.payloads[(self.tv_id, self.season_number)]


class FakeTV:
    calls = []
    payloads = {}

    def __init__(self, tv_id):
        self.tv_id = tv_id

    def info(self, language="zh-CN"):
        FakeTV.calls.append((self.tv_id, language))
        payload = FakeTV.payloads[self.tv_id]
        self.__dict__.update(payload)
        return payload


class FakeMovie:
    calls = []
    payloads = {}

    def __init__(self, movie_id):
        self.movie_id = movie_id

    def info(self, language="zh-CN"):
        FakeMovie.calls.append((self.movie_id, language))
        payload = FakeMovie.payloads[self.movie_id]
        self.__dict__.update(payload)
        return payload


class StubVideoAnalyzer:
    def __init__(self, file_analysis):
        self.file_analysis = list(file_analysis)
        self.calls = []

    def analyze_video_files(self, path, current_video_files):
        self.calls.append((path, list(current_video_files)))
        return list(self.file_analysis)


class StubAIProcessor:
    def __init__(self):
        self.video_analyzer = StubVideoAnalyzer(
            [{"path": "Disc1/Episode 01.mkv", "duration": 24.0}]
        )
        self.analyze_calls = []

    def _collect_video_files(self, path):
        return [path / "Disc1" / "Episode 01.mkv"]

    def _collect_all_local_files(self, path):
        return [path / "Disc1" / "Episode 01.mkv"]

    def analyze_anime_files(
        self,
        path,
        anime_info,
        video_files=None,
        file_analysis=None,
    ):
        self.analyze_calls.append(
            {
                "path": path,
                "anime_info": anime_info,
                "video_files": video_files,
                "file_analysis": file_analysis,
            }
        )
        return SimpleNamespace(confidence="High")

    def validate_tv_result(self, *args, **kwargs):
        return True, None, ""

    def apply_ai_mapping(self, *args, **kwargs):
        source = kwargs["all_local_files"][0]
        return {source: Path("target") / source.name}


class DummyTrans:
    def __init__(self, mapping, _uuid):
        self.mapping = mapping

    def trans_file(self):
        return None


def _stub_uuid4():
    return uuid4()


def test_search_get_season_info_uses_process_cache(monkeypatch):
    Search._season_info_cache.clear()
    FakeTVSeason.calls = []
    FakeTVSeason.payloads = {
        (100, 1): {
            "id": 1,
            "season_number": 1,
            "name": "Season 1",
            "air_date": "2024-01-01",
            "episode_count": 2,
            "episodes": [
                {
                    "episode_number": 1,
                    "episode_type": "regular",
                    "name": "Episode 1",
                    "air_date": "2024-01-01",
                    "runtime": 24,
                    "overview": "",
                }
            ],
        }
    }
    monkeypatch.setattr("src.rename.get_info.tmdb.TV_Seasons", FakeTVSeason)

    first = Search().get_season_info(100, 1)
    second = Search().get_season_info(100, 1)

    assert first == second
    assert FakeTVSeason.calls == [(100, 1, "zh-CN")]


def test_search_fill_season_info_skips_already_hydrated_tv_info(monkeypatch):
    Search._hydrated_tv_cache.clear()
    search = Search()
    hydrated = {
        "id": 200,
        "name": "Hydrated Show",
        "seasons": [
            {
                "season_number": 1,
                "episode_count": 1,
                "episodes": [
                    {
                        "episode_number": 1,
                        "episode_type": "regular",
                        "name": "Episode 1",
                    }
                ],
                "_episodes_loaded": True,
            }
        ],
    }

    def _unexpected(*args, **kwargs):
        raise AssertionError("should not fetch season details for hydrated tv info")

    monkeypatch.setattr(search, "get_season_info", _unexpected)

    result = search.fill_season_info(hydrated)

    assert result["seasons"][0]["episodes"][0]["name"] == "Episode 1"


def test_search_get_tv_info_by_id_uses_shared_cache(monkeypatch):
    Search._tv_info_cache.clear()
    FakeTV.calls = []
    FakeTV.payloads = {
        321: {
            "id": 321,
            "name": "Cached TV",
            "seasons": [{"season_number": 1, "episode_count": 12}],
        }
    }
    monkeypatch.setattr("src.rename.get_info.tmdb.TV", FakeTV)

    first = Search().get_tv_info_by_id(321)
    second = Search().get_tv_info_by_id(321)

    assert first == second
    assert FakeTV.calls == [(321, "zh-CN")]


def test_search_get_movie_info_by_id_uses_shared_cache(monkeypatch):
    Search._movie_info_cache.clear()
    FakeMovie.calls = []
    FakeMovie.payloads = {
        654: {
            "id": 654,
            "title": "Cached Movie",
            "release_date": "2024-01-01",
        }
    }
    monkeypatch.setattr("src.rename.get_info.tmdb.Movies", FakeMovie)

    first = Search().get_movie_info_by_id(654)
    second = Search().get_movie_info_by_id(654)

    assert first == second
    assert FakeMovie.calls == [(654, "zh-CN")]


def test_bangumi_client_search_subjects_uses_cache(monkeypatch):
    BangumiClient._search_cache.clear()
    client = BangumiClient()
    calls = []

    def fake_request_json(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {
            "data": [
                {
                    "id": 1,
                    "type": 2,
                    "name": "Neptune",
                    "name_cn": "海王星",
                    "date": "2013-07-12",
                    "platform": "TV",
                    "summary": "",
                    "total_episodes": 13,
                    "eps": 13,
                    "rating": {"score": 6.8, "total": 10},
                    "rank": 1,
                    "tags": [],
                    "meta_tags": [],
                }
            ]
        }

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    first = client.search_subjects("Neptune", 2013)
    second = client.search_subjects("Neptune", 2013)

    assert len(first) == 1
    assert second[0].id == 1
    assert len(calls) == 1


def test_bangumi_client_get_episodes_uses_cache(monkeypatch):
    BangumiClient._episodes_cache.clear()
    client = BangumiClient()
    calls = []

    def fake_request_json(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {
            "data": [
                {
                    "id": 10,
                    "subject_id": 47957,
                    "type": 0,
                    "sort": 1,
                    "ep": 1,
                    "name": "ep1",
                    "name_cn": "第1集",
                    "airdate": "2013-07-12",
                    "duration": "00:23:40",
                    "desc": "",
                }
            ],
            "total": 1,
        }

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    first = client.get_episodes(47957)
    second = client.get_episodes(47957)

    assert len(first) == 1
    assert second[0].subject_id == 47957
    assert len(calls) == 1


def test_build_common_prompt_compacts_tmdb_details_for_non_selected_seasons():
    anime_info = {
        "name": "测试动画",
        "first_air_date": "2013-07-12",
        "number_of_seasons": 3,
        "number_of_episodes": 39,
        "seasons": [
            {
                "season_number": 0,
                "name": "Specials",
                "episode_count": 2,
                "episodes": [
                    {
                        "episode_number": 1,
                        "name": "SP1",
                        "air_date": "2014-01-01",
                        "runtime": 24,
                    }
                ],
            },
            {
                "season_number": 1,
                "name": "Season 1",
                "episode_count": 13,
                "episodes": [
                    {
                        "episode_number": 1,
                        "name": "Episode 1",
                        "air_date": "2013-07-12",
                        "runtime": 24,
                    }
                ],
                "_episodes_loaded": True,
            },
            {
                "season_number": 2,
                "name": "Season 2",
                "episode_count": 13,
                "episodes": [
                    {
                        "episode_number": 1,
                        "name": "Episode 14",
                        "air_date": "2014-07-12",
                        "runtime": 24,
                    }
                ],
                "_episodes_loaded": True,
            },
        ],
    }

    prompt = AIClient.build_common_prompt(
        anime_info,
        [{"path": "Disc1/Episode 01.mkv", "duration": 24.0}],
        bangumi_context=None,
    )

    assert "TMDB 季度摘要：" in prompt
    assert "TMDB 候选季度详细集目：" in prompt
    assert "- Season 2: Season 2 (共 13 集)" in prompt
    assert "【Season 1】Season 1 (共 1 集)" in prompt
    assert "【Season 2】Season 2 (共 1 集)" not in prompt
    assert "提示: 以上只展开高相关季度；最终仍只能映射到全部 TMDB 真实存在的 SxxExx。" in prompt


def test_rename_tv_flow_reuses_precomputed_file_analysis(monkeypatch, tmp_path):
    rename = Rename()
    rename.ai_processor = StubAIProcessor()
    monkeypatch.setattr(rename, "_is_confidence_acceptable", lambda confidence: True)
    monkeypatch.setattr(rename, "_write_task_data", lambda payload: None)
    monkeypatch.setattr(rename, "_resolve_task_poster_path", lambda **kwargs: None)
    monkeypatch.setattr(rename, "_detect_season_id_from_mapping", lambda mapping: 1)
    monkeypatch.setattr("src.rename.process.uuid.uuid4", _stub_uuid4)
    monkeypatch.setattr("src.rename.process.Trans", DummyTrans)

    source_dir = tmp_path / "Series"
    disc_dir = source_dir / "Disc1"
    disc_dir.mkdir(parents=True)
    source_file = disc_dir / "Episode 01.mkv"
    source_file.write_text("video", encoding="utf-8")

    info = {
        "id": 123,
        "name": "Series",
        "first_air_date": "2024-01-01",
        "seasons": [
            {
                "season_number": 1,
                "episode_count": 1,
                "episodes": [
                    {
                        "episode_number": 1,
                        "name": "Episode 1",
                        "season_number": 1,
                    }
                ],
                "_episodes_loaded": True,
            }
        ],
        "poster_path": None,
        "genres": [{"id": 16, "name": "Animation"}],
    }

    monkeypatch.setattr(
        Rename,
        "check_task_type",
        lambda self, *args, **kwargs: ("Series", info, True, False, "High"),
    )

    result = rename._process(source_dir, _is_sub_task=True)

    assert result is True
    assert len(rename.ai_processor.video_analyzer.calls) == 1
    assert len(rename.ai_processor.analyze_calls) == 1
    assert rename.ai_processor.analyze_calls[0]["file_analysis"] == [
        {"path": "Disc1/Episode 01.mkv", "duration": 24.0}
    ]
