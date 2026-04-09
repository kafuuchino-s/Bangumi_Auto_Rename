from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import time

from src.config.config_manager import cm

from data.ai_batch_regression.run_full_regression import (
    NON_FAILURE_STATUSES,
    _should_skip_special_compilation_case,
    build_initial_record,
    build_parser,
    build_previous_records_index,
    build_regression_cases,
    build_selected_dirs,
    build_summary,
    build_timeout_record,
    collect_video_files,
    filter_cases_by_status,
    finish_record,
    pop_completed_records_in_order,
    run_case_with_isolated_context,
)


def test_build_selected_dirs_filters_by_only_dirs_and_status(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    dir_a = root / "A"
    dir_b = root / "B"
    dir_c = root / "C"
    dir_a.mkdir()
    dir_b.mkdir()
    dir_c.mkdir()

    previous_output = tmp_path / "previous_run"
    previous_output.mkdir()
    (previous_output / "results.jsonl").write_text(
        "\n".join(
            [
                '{"dir_name": "A", "status": "tmdb_not_found"}',
                '{"dir_name": "B", "status": "ok"}',
                '{"dir_name": "C", "status": "ai_empty_mapping"}',
            ]
        ),
        encoding="utf-8",
    )

    selected, previous_records = build_selected_dirs(
        root=root,
        dirs=[dir_a, dir_b, dir_c],
        start_index=1,
        only_dirs={"A", "C"},
        status_filter={"tmdb_not_found", "ai_empty_mapping"},
        resume_from=previous_output,
    )

    assert [path.name for path in selected] == ["A", "C"]
    assert previous_records["A"]["status"] == "tmdb_not_found"
    assert previous_records["C"]["status"] == "ai_empty_mapping"



def test_build_regression_cases_expands_structural_parent_dirs(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    yamato = root / "Space Battleship Yamato 2199"
    film = yamato / "Film"
    serie = yamato / "Série"
    extras = yamato / "Extras"
    film.mkdir(parents=True)
    serie.mkdir()
    extras.mkdir()
    (film / "movie.mkv").write_text("", encoding="utf-8")
    (serie / "ep01.mkv").write_text("", encoding="utf-8")
    (extras / "ncop.mkv").write_text("", encoding="utf-8")

    rename = Mock()
    rename._derive_subtask_custom_name.side_effect = (
        lambda parent, sub, existing: parent.name
    )

    cases = build_regression_cases([yamato], rename)

    assert [case["display_name"] for case in cases] == [
        "Space Battleship Yamato 2199 :: Extras",
        "Space Battleship Yamato 2199 :: Film",
        "Space Battleship Yamato 2199 :: Série",
    ]
    assert all(case["custom_name"] == "Space Battleship Yamato 2199" for case in cases)

    unsplit_cases = build_regression_cases(
        [yamato],
        rename,
        include_structural_dirs=False,
    )
    assert [case["display_name"] for case in unsplit_cases] == [
        "Space Battleship Yamato 2199"
    ]
    assert unsplit_cases[0]["custom_name"] is None



def test_build_regression_cases_splits_non_structural_series_children(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    yozakura = root / "Yozakura Quartet"
    yoza_quar = yozakura / "[Quetzal] Yoza-Quar!"
    hana = yozakura / "[Quetzal] Yozakura Quartet - Hana no Uta"
    yoza_quar.mkdir(parents=True)
    hana.mkdir()
    (yoza_quar / "01.mkv").write_text("", encoding="utf-8")
    (hana / "01.mkv").write_text("", encoding="utf-8")

    rename = Mock()
    rename._derive_subtask_custom_name.return_value = None

    cases = build_regression_cases([yozakura], rename)

    assert [case["display_name"] for case in cases] == [
        "Yozakura Quartet :: [Quetzal] Yoza-Quar!",
        "Yozakura Quartet :: [Quetzal] Yozakura Quartet - Hana no Uta",
    ]
    assert all(case["custom_name"] is None for case in cases)



def test_filter_cases_by_status_uses_expanded_case_display_name(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    yamato = root / "Space Battleship Yamato 2199"
    film = yamato / "Film"
    serie = yamato / "Série"
    film.mkdir(parents=True)
    serie.mkdir()
    (film / "movie.mkv").write_text("", encoding="utf-8")
    (serie / "ep01.mkv").write_text("", encoding="utf-8")

    rename = Mock()
    rename._derive_subtask_custom_name.side_effect = (
        lambda parent, sub, existing: parent.name
    )
    cases = build_regression_cases([yamato], rename)

    previous_records = build_previous_records_index(
        [
            {
                "dir_name": "Space Battleship Yamato 2199 :: Film",
                "status": "ai_empty_mapping",
            },
            {
                "dir_name": "Space Battleship Yamato 2199 :: Série",
                "status": "ok",
            },
        ]
    )

    filtered = filter_cases_by_status(
        cases,
        previous_records,
        {"ai_empty_mapping"},
    )

    assert [case["display_name"] for case in filtered] == [
        "Space Battleship Yamato 2199 :: Film"
    ]



def test_collect_video_files_skips_promotional_content(tmp_path):
    series_dir = tmp_path / "OVERLORD Ple Ple Pleiades"
    series_dir.mkdir()
    main_file = series_dir / "[VCB-Studio] OVERLORD Ple Ple Pleiades [Ma10p_1080p][x265_flac].mkv"
    menu_file = series_dir / "[VCB-Studio] OVERLORD Ple Ple Pleiades [Menu][Ma10p_1080p][x265].mkv"
    main_file.write_text("", encoding="utf-8")
    menu_file.write_text("", encoding="utf-8")

    collected = collect_video_files(series_dir)

    assert collected == [main_file]



def test_should_skip_special_compilation_case_for_single_low_confidence_bundle():
    video_file = Path("OVERLORD Ple Ple Pleiades.mkv")
    file_analysis = [{"path": video_file.name, "duration": 30.0}]
    anime_info = {
        "seasons": [
            {
                "season_number": 0,
                "episodes": [
                    {"episode_number": 1, "runtime": 8},
                    {"episode_number": 2, "runtime": 8},
                    {"episode_number": 3, "runtime": 8},
                    {"episode_number": 4, "runtime": 8},
                ],
            }
        ]
    }
    ai_result = SimpleNamespace(confidence="Low", file_mapping=[])

    assert _should_skip_special_compilation_case(
        [video_file],
        file_analysis,
        anime_info,
        ai_result,
        "ai_empty_mapping",
    )



def test_should_not_skip_special_compilation_case_when_episode_marker_exists():
    video_file = Path("OVERLORD Ple Ple Pleiades [SP01].mkv")
    file_analysis = [{"path": video_file.name, "duration": 30.0}]
    anime_info = {
        "seasons": [
            {
                "season_number": 0,
                "episodes": [
                    {"episode_number": 1, "runtime": 8},
                    {"episode_number": 2, "runtime": 8},
                    {"episode_number": 3, "runtime": 8},
                    {"episode_number": 4, "runtime": 8},
                ],
            }
        ]
    }
    ai_result = SimpleNamespace(confidence="Low", file_mapping=[])

    assert not _should_skip_special_compilation_case(
        [video_file],
        file_analysis,
        anime_info,
        ai_result,
        "ai_empty_mapping",
    )



def test_build_summary_treats_skipped_movie_case_as_non_failure(tmp_path):
    summary = build_summary(
        records=[
            {"dir_name": "MovieDir", "status": "skipped_movie_case", "video_file_count": 11, "unmatched_count": 11},
            {"dir_name": "BundleDir", "status": "skipped_special_compilation_case", "video_file_count": 1, "unmatched_count": 1},
            {"dir_name": "TvDir", "status": "ok", "video_file_count": 1, "sanitized_mapping_count": 1},
            {"dir_name": "BadDir", "status": "ai_empty_mapping", "validation_reason": "ai_empty_mapping", "validation_detail": "AI 未返回 file_mapping"},
        ],
        run_id="20260408_000000",
        root=tmp_path,
        output_dir=tmp_path / "out",
        provider="openai",
        model="gpt-5.4-mini",
    )

    assert "skipped_movie_case" in NON_FAILURE_STATUSES
    assert "skipped_special_compilation_case" in NON_FAILURE_STATUSES
    assert summary["ok_dirs"] == 1
    assert summary["non_failure_dirs"] == 3
    assert summary["invalid_dirs"] == 1
    assert summary["skipped_movie_dirs"] == 1
    assert len(summary["top_failures"]) == 1
    assert summary["top_failures"][0]["dir_name"] == "BadDir"



def test_build_parser_defaults_max_workers_to_one():
    parser = build_parser()

    args = parser.parse_args(["--root", "H:/Anime/Anime Series"])

    assert args.max_workers == 1
    assert args.per_case_timeout == 420



def test_pop_completed_records_in_order_flushes_contiguous_records_only():
    completed = {
        2: {"dir_name": "B"},
        1: {"dir_name": "A"},
        4: {"dir_name": "D"},
    }

    flushed, next_index = pop_completed_records_in_order(completed, 1)

    assert flushed == [(1, {"dir_name": "A"}), (2, {"dir_name": "B"})]
    assert next_index == 3
    assert completed == {4: {"dir_name": "D"}}



def test_run_case_with_isolated_context_uses_thread_local_auto_save_override():
    observed = {}

    class DummyRename:
        pass

    def fake_run_case(case, rename):
        observed["case"] = case
        observed["rename_type"] = type(rename)
        observed["ai_auto_save"] = cm.get_config("ai_auto_save")
        return {"status": "ok"}

    result = run_case_with_isolated_context(
        {"display_name": "CaseA"},
        rename_factory=DummyRename,
        run_case_func=fake_run_case,
    )

    assert result == {"status": "ok"}
    assert observed["case"] == {"display_name": "CaseA"}
    assert observed["rename_type"] is DummyRename
    assert observed["ai_auto_save"] is False



def test_build_timeout_record_keeps_existing_record_shape():
    record = build_initial_record(
        3,
        {
            "display_name": "CaseC",
            "path": Path("/tmp/CaseC"),
        },
    )

    timed_out = build_timeout_record(record, time.time(), 420)

    assert timed_out["index"] == 3
    assert timed_out["dir_name"] == "CaseC"
    assert timed_out["status"] == "timeout"
    assert timed_out["timeout_seconds"] == 420
    assert "duration_sec" in timed_out
    assert "finished_at" in timed_out
