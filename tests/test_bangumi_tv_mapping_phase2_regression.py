import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from pathlib import Path
from unittest.mock import patch

import pytest

from data.ai_batch_regression.run_full_regression import get_anime_info
from src.ai.client import AIClient
from src.ai.models import AIAnalysisResult, EpisodeMapping
from src.rename.ai_processor import AIProcessor
from src.rename.get_info import Search
from src.rename.process import Rename


def _build_anime_info():
    return {
        "name": "魔法少女小圆",
        "original_name": "魔法少女まどか☆マギカ",
        "first_air_date": "2011-01-07",
        "seasons": [
            {
                "season_number": 1,
                "name": "Season 1",
                "episodes": [
                    {"episode_number": 1, "name": "Episode 1"},
                    {"episode_number": 2, "name": "Episode 2"},
                ],
            }
        ],
    }


def test_phase2_invalid_mapping_rejects_ambiguous_synthesized_path(tmp_path):
    (tmp_path / "Disc1").mkdir()
    (tmp_path / "Disc2").mkdir()
    file_name = "[VCB-Studio] Puella Magi Madoka Magica [01].mkv"
    actual_file_1 = tmp_path / "Disc1" / file_name
    actual_file_2 = tmp_path / "Disc2" / file_name
    actual_file_1.write_text("video", encoding="utf-8")
    actual_file_2.write_text("video", encoding="utf-8")

    ai_result = AIAnalysisResult(
        confidence="High",
        reason="模拟 To LOVE-Ru / Magia Record 类跨目录拼接路径",
        season_mapping=[],
        file_mapping=[
            EpisodeMapping(
                file_path=f"MagiRepo/{file_name}",
                tmdb_season=1,
                tmdb_episode=1,
                episode_type="regular",
                confidence="High",
            )
        ],
        unmatched_files=[],
        conflict_details=[],
        extra_notes=None,
    )

    ok, reason, detail = AIProcessor().validate_tv_result(
        ai_result,
        _build_anime_info(),
        base_path=tmp_path,
        video_files=[actual_file_1, actual_file_2],
    )

    assert ok is False
    assert reason == "ai_invalid_mapping"
    assert "路径不唯一:MagiRepo/" in detail


def test_phase2_invalid_mapping_rejects_ambiguous_synthesized_path(tmp_path):
    (tmp_path / "Disc1").mkdir()
    (tmp_path / "Disc2").mkdir()
    file_name = "[VCB-Studio] Puella Magi Madoka Magica [01].mkv"
    actual_file_1 = tmp_path / "Disc1" / file_name
    actual_file_2 = tmp_path / "Disc2" / file_name
    actual_file_1.write_text("video", encoding="utf-8")
    actual_file_2.write_text("video", encoding="utf-8")

    ai_result = AIAnalysisResult(
        confidence="High",
        reason="模拟 To LOVE-Ru / Magia Record 类跨目录拼接路径",
        season_mapping=[],
        file_mapping=[
            EpisodeMapping(
                file_path=f"MagiRepo/{file_name}",
                tmdb_season=1,
                tmdb_episode=1,
                episode_type="regular",
                confidence="High",
            )
        ],
        unmatched_files=[],
        conflict_details=[],
        extra_notes=None,
    )

    ok, reason, detail = AIProcessor().validate_tv_result(
        ai_result,
        _build_anime_info(),
        base_path=tmp_path,
        video_files=[actual_file_1, actual_file_2],
    )

    assert ok is False
    assert reason == "ai_invalid_mapping"
    assert "路径不唯一:MagiRepo/" in detail



def test_phase2_invalid_mapping_rejects_truncated_basename_with_nested_hint(tmp_path):
    (tmp_path / "Disc1").mkdir()
    file_name = "[VCB-Studio] Magia Record [01].mkv"
    actual_file = tmp_path / "Disc1" / file_name
    actual_file.write_text("video", encoding="utf-8")

    ai_result = AIAnalysisResult(
        confidence="High",
        reason="模拟 AI 截断子目录后只保留 basename",
        season_mapping=[],
        file_mapping=[
            EpisodeMapping(
                file_path=f"MagiRepo/{file_name}",
                tmdb_season=1,
                tmdb_episode=1,
                episode_type="regular",
                confidence="High",
            )
        ],
        unmatched_files=[],
        conflict_details=[],
        extra_notes=None,
    )

    ok, reason, detail = AIProcessor().validate_tv_result(
        ai_result,
        _build_anime_info(),
        base_path=tmp_path,
        video_files=[actual_file],
    )

    assert ok is False
    assert reason == "ai_invalid_mapping"
    assert detail == f"文件不存在:MagiRepo/{file_name}"



def test_phase2_empty_mapping_returns_ai_empty_mapping(tmp_path):
    video_path = tmp_path / "[VCB-Studio] Yozakura Quartet [01].mkv"
    video_path.write_text("video", encoding="utf-8")

    ai_result = AIAnalysisResult(
        confidence="Low",
        reason="复杂目录下放弃映射",
        season_mapping=[],
        file_mapping=[],
        unmatched_files=[video_path.name],
        conflict_details=[],
        extra_notes=None,
    )

    ok, reason, detail = AIProcessor().validate_tv_result(
        ai_result,
        _build_anime_info(),
        base_path=tmp_path,
        video_files=[video_path],
    )

    assert ok is False
    assert reason == "ai_empty_mapping"
    assert detail == "AI 未返回 file_mapping"


def test_phase2_regression_entry_uses_ai_first_title_lookup():
    folder_name = "[Moozzi2] Denki-gai no Honya-san BD-BOX - TV + SP"
    rename = Rename()
    path = Path(folder_name)
    target_info = {
        "id": 100,
        "name": "Denki-gai no Honya-san",
        "genres": [{"name": "Animation"}],
    }

    with patch.object(
        rename.ai_processor.ai_client,
        "extract_title_metadata",
        return_value=type(
            "TitleMetadata",
            (),
            {
                "title": "デンキ街の本屋さん",
                "fallback_title": "Denki-gai no Honya-san",
                "type": "tv",
            },
        )(),
    ), patch.object(
        rename.ai_processor.ai_client,
        "is_available",
        return_value=True,
    ), patch.object(
        rename,
        "_search_tv_with_ai_selection",
        return_value=("Denki-gai no Honya-san", target_info, "High", ""),
    ) as search_tv, patch.object(
        rename,
        "_search_movie_with_ai_selection",
        return_value=("", None, None, "tmdb_not_found"),
    ):
        title_info, name, tv_info = get_anime_info(path, rename)

    assert title_info["ai_input_name"] == folder_name
    assert title_info["lookup_status"] == "ok"
    assert search_tv.call_args_list[0].args[0] == folder_name
    assert search_tv.call_args_list[0].args[1] == "デンキ街の本屋さん"
    assert name == "Denki-gai no Honya-san"
    assert tv_info == target_info



def test_get_anime_info_preserves_child_name_without_manual_title_for_non_structural_case(
    tmp_path,
):
    rename = Rename()
    root = tmp_path / "Yozakura Quartet"
    child = root / "[Quetzal] Yoza-Quar!"
    child.mkdir(parents=True)
    (child / "01.mkv").write_text("", encoding="utf-8")

    with patch.object(
        rename.ai_processor.ai_client,
        "is_available",
        return_value=True,
    ), patch.object(
        rename,
        "check_task_type",
        return_value=("夜樱四重奏", {"id": 1}, True, False, "High"),
    ) as check_task_type:
        title_info, name, tv_info = get_anime_info(child, rename)

    assert title_info["cleaned_name"] == "Yoza-Quar"
    assert title_info["raw_title"] == "Yoza-Quar"
    assert title_info["ai_input_name"] == "[Quetzal] Yoza-Quar!"
    assert check_task_type.call_args.kwargs["prefer_manual_title"] is False
    assert name == "夜樱四重奏"
    assert tv_info == {"id": 1}



def test_phase2_aria_selected_tmdb_info_should_include_season_episode_details():
    hydrated = {
        "id": 53787,
        "name": "水星领航员",
        "genres": [{"name": "Animation"}],
        "seasons": [
            {
                "season_number": 0,
                "episode_count": 22,
                "episodes": [
                    {
                        "episode_number": 11,
                        "name": "Aria the Avvenire-1",
                        "season_number": 0,
                    },
                    {
                        "episode_number": 12,
                        "name": "Aria the Avvenire-2",
                        "season_number": 0,
                    },
                ],
            },
            {"season_number": 1, "episode_count": 13, "episodes": []},
        ],
    }
    search = Search()
    raw_info = {
        "id": 53787,
        "name": "水星领航员",
        "genres": [{"name": "Animation"}],
        "seasons": [
            {"season_number": 0, "episode_count": 22},
            {"season_number": 1, "episode_count": 13},
        ],
    }

    with patch.object(search, "get_tv_info_by_id", return_value=raw_info), patch.object(
        search,
        "fill_season_info",
        return_value=hydrated,
    ) as fill_season_info:
        result = search.fill_season_info(search.get_tv_info_by_id(53787))

    fill_season_info.assert_called_once()
    assert result["seasons"][0]["episodes"][0]["name"] == "Aria the Avvenire-1"
    assert result["seasons"][0]["episodes"][1]["name"] == "Aria the Avvenire-2"


def test_phase2_search_tv_by_query_retries_with_clean_variant(monkeypatch):
    search = Search()
    calls = []

    def _fake_search(query, year=None):
        calls.append((query, year))
        if query == "Denki-gai no Honya-san":
            return [
                {
                    "id": 100,
                    "name": "Denki-gai no Honya-san",
                    "original_name": "デンキ街の本屋さん",
                    "first_air_date": "2014-10-02",
                    "popularity": 1.0,
                }
            ]
        return None

    monkeypatch.setattr(search, "_search_tv_multi_language", _fake_search)

    results = search.search_tv_by_query(
        "Denki-gai no Honya-san BD-BOX - TV + SP",
        0,
        limit=5,
    )

    assert results is not None
    assert results[0]["id"] == 100
    assert calls[0] == ("Denki-gai no Honya-san BD-BOX - TV + SP", 0)
    assert ("Denki-gai no Honya-san", 0) in calls



def test_phase2_tmdb_ranking_prefers_madoka_base_series_over_spinoff():
    ranked = Search().rank_tv_candidates(
        source_title="Puella Magi Madoka Magica",
        query="Puella Magi Madoka Magica",
        candidates=[
            {
                "id": 1,
                "name": "Puella Magi Madoka Magica",
                "original_name": "魔法少女まどか☆マギカ",
                "first_air_date": "2011-01-07",
                "popularity": 12.0,
            },
            {
                "id": 2,
                "name": "Magia Record: Puella Magi Madoka Magica Side Story",
                "original_name": "マギアレコード 魔法少女まどか☆マギカ外伝",
                "first_air_date": "2020-01-04",
                "popularity": 55.0,
            },
        ],
        year=2011,
    )

    assert ranked[0]["id"] == 1



def test_phase2_prompt_emphasizes_partial_mapping_for_complex_directories():
    prompt = AIClient.build_common_prompt(
        {
            "name": "Yozakura Quartet",
            "first_air_date": "2013-10-06",
            "number_of_seasons": 1,
            "number_of_episodes": 13,
            "seasons": [
                {
                    "season_number": 1,
                    "name": "Season 1",
                    "episode_count": 13,
                }
            ],
        },
        [
            {"path": "Disc1/Episode 01.mkv", "duration": 24.0},
            {"path": "SPs/NCOP.mkv", "duration": 1.5},
            {"path": "Extras/Interview.mkv", "duration": 12.0},
        ],
        bangumi_context=None,
    )

    assert "顶层子目录: Disc1, SPs, Extras" in prompt
    assert "如果不同文件落在不同子目录" in prompt
    assert "[001] source_index=1 source_path=`Disc1/Episode 01.mkv`" in prompt
    assert "如果只能确定部分文件，优先输出有把握的合法映射" in prompt
    assert "如果输入列表里同时存在“根目录文件 + 子目录文件”或“同编号不同版本文件”" in prompt
    assert "不要把短标签/简称扩写回主标题" in prompt
    assert "MagiRepo [01]" in prompt
    assert "SP01][Hi10p_1080p][x264_flac]" in prompt
    assert "只能从上面 `[编号] source_index=... source_path=` 列表里原样选择" in prompt
    assert "先定位对应的 `[编号]` 行" in prompt
    assert "对 `SP01 / SP02 / OVA01` 这类 special 编号" in prompt
    assert "不要因为目录复杂就返回空的 `file_mapping`" in prompt
    assert "必须复制完整相对路径，不能截断为 basename，也不能省略中间子目录" in prompt



def test_phase2_timeout_case_hits_per_case_timeout():
    def _run_case():
        time.sleep(0.2)
        return "done"

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_run_case)
        with pytest.raises(FuturesTimeoutError):
            future.result(timeout=0.01)
