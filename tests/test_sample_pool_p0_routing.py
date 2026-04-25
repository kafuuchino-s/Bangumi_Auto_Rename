from pathlib import Path
from types import SimpleNamespace

from src.rename.cleaner import build_tv_search_queries, is_promotional_content
from src.rename.get_info import Search
from src.rename.process import Rename
from tools import generate_sample_pool_main_flow_manifest as main_flow_manifest
from tools import run_sample_pool_main_flow_preview as main_flow_preview


def test_select_ranked_tv_candidate_rejects_low_score_single_candidate():
    search = Search.__new__(Search)
    low_candidate = {"id": 1, "name": "Wrong Show", "_match_score": 65}
    near_threshold_without_query = {"id": 3, "name": "Near Show", "_match_score": 67}
    near_threshold_tmdb_query = {
        "id": 4,
        "name": "Hyperdimension Neptunia",
        "_match_score": 67,
        "_matched_query": "Choujigen Game Neptune The Animation",
    }
    high_candidate = {"id": 2, "name": "Right Show", "_match_score": 72}

    selected, confidence = search._select_ranked_tv_candidate([low_candidate])
    assert selected is None
    assert confidence == "Low"

    selected, confidence = search._select_ranked_tv_candidate([near_threshold_without_query])
    assert selected is None
    assert confidence == "Low"

    selected, confidence = search._select_ranked_tv_candidate([near_threshold_tmdb_query])
    assert selected == near_threshold_tmdb_query
    assert confidence == "High"

    selected, confidence = search._select_ranked_tv_candidate([high_candidate])
    assert selected == high_candidate
    assert confidence == "High"


def test_tv_search_queries_include_disambiguated_title_fallbacks():
    aria_queries = build_tv_search_queries("ARIA The AVVENIRE Capitolo Version")
    saiki_queries = build_tv_search_queries("The Disastrous Life of Saiki K. S00 2018 1080p NF WEB-DL")
    wake_up_girls_queries = build_tv_search_queries("Wake up, Girls! ZOO - TV + SP")

    assert "ARIA The AVVENIRE" in aria_queries
    assert "The Disastrous Life of Saiki K" in saiki_queries
    assert "Wake up, Girls! ZOO" in wake_up_girls_queries


def test_tv_search_queries_include_safe_official_aliases():
    assert "The Devil Is a Part-Timer!" in build_tv_search_queries("Hataraku Maou-sama!")
    assert "Inari Kon Kon" in build_tv_search_queries("Inari, Konkon, Koi Iroha")
    assert "Hyperdimension Neptunia" in build_tv_search_queries("Choujigen Game Neptune The Animation")


def test_promotional_content_detects_common_bd_extras():
    assert is_promotional_content("[Vol.01][Menu][BDRIP][1080P].mkv")
    assert is_promotional_content("[Uchuu Yamato][Logo][BDRIP].mkv")
    assert is_promotional_content("ノクレジットOP.mkv")
    assert is_promotional_content("ノンクレジットED.mkv")
    assert is_promotional_content("劇場マナーCM 01.mkv")
    assert is_promotional_content("Chapter 01 Take Off Trailer Collection.mkv")
    assert not is_promotional_content("Gatchaman Crowds insight 01.mkv")
    assert not is_promotional_content("[Moozzi2] Wake up, Girls! ZOO - 01 (BD 1920x1080 x.264 Flac).mkv")


def test_supplemental_video_file_detects_event_and_summary_extras(tmp_path):
    rename = Rename.__new__(Rename)
    base = tmp_path / "Yamato"
    base.mkdir()
    supplemental_names = [
        "2205 Take Off Yamato Talk.mkv",
        "Chapter 01 Completion Announcement Theater Greeting.mkv",
        "Chapter 01 Memorial Theater Greeting After Movie Showing.mkv",
        "Story Summary Before Main Movie Showing.mkv",
    ]

    for name in supplemental_names:
        path = base / name
        path.touch()
        assert rename._is_supplemental_video_file(path, base)

    main = base / "Yamato 2205 - 01.mkv"
    main.touch()
    assert not rename._is_supplemental_video_file(main, base)


def test_select_ranked_movie_candidate_rejects_low_score_single_candidate():
    rename = Rename.__new__(Rename)
    low_candidate = {"id": 10, "title": "Wrong Movie", "_match_score": 69}
    high_candidate = {"id": 11, "title": "Right Movie", "_match_score": 70}

    selected, confidence = rename._select_ranked_movie_candidate([low_candidate])
    assert selected is None
    assert confidence == "Low"

    selected, confidence = rename._select_ranked_movie_candidate([high_candidate])
    assert selected == high_candidate
    assert confidence == "High"


def test_search_tv_with_single_low_score_candidate_fails_closed(monkeypatch):
    rename = Rename.__new__(Rename)
    search = Search.__new__(Search)
    rename.search = search
    monkeypatch.setattr(search, "search_tv_by_query", lambda *args, **kwargs: [{"id": 1, "name": "Wrong Show", "_match_score": 10}])
    monkeypatch.setattr(search, "rank_tv_candidates", lambda *args, **kwargs: [{"id": 1, "name": "Wrong Show", "_match_score": 10}])

    selected_name, info, confidence, reason = rename._search_tv_with_ai_selection(
        "Expected Specific TV Title",
        "Expected Specific TV Title",
        0,
        SimpleNamespace(is_available=lambda: True),
    )

    assert selected_name == ""
    assert info is None
    assert confidence == "Low"
    assert reason == "ai_low_confidence"


def test_search_movie_with_single_low_score_candidate_fails_closed(monkeypatch):
    rename = Rename.__new__(Rename)
    search = Search.__new__(Search)
    rename.search = search
    monkeypatch.setattr(search, "search_movies_by_title", lambda *args, **kwargs: [{"id": 1, "title": "Wrong Movie", "_match_score": 10}])

    selected_name, info, confidence, reason = rename._search_movie_with_ai_selection(
        "Expected Specific Movie Title",
        "Expected Specific Movie Title",
        0,
        SimpleNamespace(is_available=lambda: True),
    )

    assert selected_name == ""
    assert info is None
    assert confidence == "Low"
    assert reason == "ai_low_confidence"


def test_main_flow_preview_observation_provenance_uses_runtime_entrypoint():
    entry = main_flow_preview.RenameSample(
        sample_id="sample_demo",
        sample_json="tests/sample_pool/raw/tv/sample_demo.json",
        check=False,
        anchor=True,
        tags=["tv_strict_mapping"],
        protects=["mixed_route"],
    )
    observation = main_flow_preview.build_observation(
        entry=entry,
        execution_result={
            "status": "executed",
            "infra_failure": False,
            "payload": {"final_type": "tv", "routes": [], "mapping": []},
            "artifacts": {"sample_root": "tmp/sample_demo"},
        },
        manifest_version="test",
    )

    assert observation["runner_kind"] == "rename_lane_main_flow"
    assert observation["lane_contract"]["authoritative_for_sample_pool"] is True
    assert observation["main_flow_verified"] is True
    assert observation["uses_runtime_rename_process"] is True
    assert observation["uses_shadow_candidate_logic"] is False
    assert observation["main_flow_observer"] is True
    assert "Rename.process" in observation["runtime_entrypoint"]
    assert observation["observation_is_truth"] is False


def test_main_flow_manifest_marks_all_entries_as_non_blocking_anchors(tmp_path):
    raw_root = tmp_path / "raw"
    (raw_root / "tv").mkdir(parents=True)
    (raw_root / "movie").mkdir(parents=True)
    (raw_root / "tv" / "sample_tv.json").write_text("{}", encoding="utf-8")
    (raw_root / "movie" / "sample_movie.json").write_text("{}", encoding="utf-8")

    manifest = main_flow_manifest.build_manifest(raw_root)

    assert manifest["manifest_version"] == "2026-04-24-main-flow-full"
    assert [entry["sample_id"] for entry in manifest["entries"]] == ["sample_movie", "sample_tv"]
    assert all(entry["check"] is False for entry in manifest["entries"])
    assert all(entry["anchor"] is True for entry in manifest["entries"])
    assert manifest["entries"][0]["tags"] == ["raw_movie", "main_flow_preview", "movie_resolution"]
    assert manifest["entries"][1]["tags"] == ["raw_tv", "main_flow_preview", "tv_strict_mapping"]


def test_dual_route_decision_prefers_tv_when_movie_subset_empty(monkeypatch, tmp_path):
    source = tmp_path / "Movie Named TV Bundle"
    source.mkdir()
    for episode in range(1, 4):
        (source / f"Sample [{episode:02d}].mkv").touch()
    (source / "Sample [SP01][NCOP].mkv").touch()

    rename = Rename.__new__(Rename)
    monkeypatch.setattr(rename, "_is_confidence_acceptable", lambda confidence: confidence == "High")
    task_plan = {
        "is_movie": True,
        "ai_type": "movie",
        "tv_candidate": {"available": True, "confidence": "High"},
        "movie_candidate": {"available": True, "confidence": "High"},
        "mixed_parent_plan": {
            "total_video_count": 4,
            "tv_claimed_file_count": 3,
            "movie_claimed_file_count": 0,
        },
    }

    assert rename._evaluate_dual_route_decision(source, task_plan) is False


def test_dual_route_override_does_not_bypass_overlap_fail_closed():
    mixed_parent_plan = {
        "overlap_relative_paths": ["Feature [01].mkv"],
        "tv_claimed_file_count": 3,
        "movie_claimed_file_count": 1,
    }

    assert not Rename._dual_route_override_allows_single_route_fallback(False, mixed_parent_plan)
