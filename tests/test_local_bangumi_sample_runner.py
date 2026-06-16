from __future__ import annotations

import json
from pathlib import Path

from src.rename.local_fact_surface import local_fact_surface_to_dict
from tools import run_local_bangumi_mapping_sample_pool as runner


def test_convergence_manifest_resolves_existing_samples_in_order():
    manifest = Path("tests/sample_pool/suites/local_bangumi_case_agent_convergence.json")

    samples = runner._load_sample_list(manifest, Path("tests/sample_pool/raw"))

    assert len(samples) == 35
    assert samples[0].as_posix().endswith(
        "movie/sample_0001_92_impatient_eilas_miyafuji_strike_witches_501_butai_hasshin_shimasu_movie_1080p_05fe5870_mkv.json"
    )
    assert samples[-1].as_posix().endswith(
        "tv/sample_0122_the_disastrous_life_of_saiki_k_s00_2018_1080p_nf_web_dl_x264_ddp_2_0_animef_adweb.json"
    )


def test_sample_list_filtering_preserves_manifest_order(tmp_path: Path):
    raw_root = tmp_path / "raw"
    raw_root.mkdir()
    first = raw_root / "sample_alpha.json"
    second = raw_root / "sample_beta.json"
    first.write_text("{}", encoding="utf-8")
    second.write_text("{}", encoding="utf-8")
    manifest = tmp_path / "suite.json"
    manifest.write_text(
        json.dumps(
            {
                "samples": [
                    {"sample_path": "sample_beta.json"},
                    {"sample_path": "sample_alpha.json"},
                ]
            }
        ),
        encoding="utf-8",
    )

    samples = runner._load_sample_list(manifest, raw_root)
    filtered = runner._filter_samples(samples, ["sample"], limit=1)

    assert [sample.name for sample in samples] == ["sample_beta.json", "sample_alpha.json"]
    assert [sample.name for sample in filtered] == ["sample_beta.json"]


def test_case_agent_ai_call_stats_counts_stages_and_retries():
    stats = runner._case_agent_ai_call_stats(
        {
            "case_judge_request_audits": [
                {
                    "note": "pi_case_agent_session_summary",
                    "provider_retry_count": 2,
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 10,
                        "total_tokens": 110,
                        "input_tokens_details": {
                            "cached_tokens": 40,
                        },
                    },
                },
                {"call_name": "LocalPackageAnalysis", "provider_retry_count": 9},
                {"note": "not an ai call"},
            ]
        }
    )

    assert stats["ai_call_count"] == 1
    assert stats["ai_attempt_count_estimate"] == 3
    assert stats["ai_provider_retry_count"] == 2
    assert stats["ai_call_counts_by_stage"] == {
        "pi_case_agent": 1,
    }
    assert stats["ai_attempt_counts_by_stage"]["pi_case_agent"] == 3
    assert stats["ai_provider_retry_counts_by_stage"]["pi_case_agent"] == 2
    assert stats["pi_usage_total_tokens"] == 110
    assert stats["pi_usage_input_tokens"] == 100
    assert stats["pi_usage_output_tokens"] == 10
    assert stats["pi_provider_cached_input_tokens"] == 40
    assert stats["pi_provider_cached_input_ratio"] == 0.4
    assert stats["pi_max_turn_input_tokens"] == 100


def test_sample_row_includes_case_agent_ai_call_stats(tmp_path: Path):
    row = runner._sample_row(
        tmp_path / "sample.json",
        {
            "ok": True,
            "snapshot": {
                "status": "fail_closed",
                "summary": "no_new_evidence",
                "case_agent_mode": "pi_case_agent",
                "pi_run_dir": "data/pi_case_agent/runs/CASE",
                "pi_tool_call_counts": {"get_case_context": 1, "fail_closed": 1},
                "pi_tool_sequence": ["get_case_context", "fail_closed"],
                "pi_runtime_result": {"runner_result": {"turn_count": 2}},
                "tool_rejection_count": 1,
                "compact_count": 0,
                "case_judge_request_audits": [
                    {"note": "pi_case_agent_session_summary", "provider_retry_count": 1},
                ],
            },
        },
        elapsed_ms=123,
    )

    assert row["ai_call_count"] == 1
    assert row["ai_attempt_count_estimate"] == 2
    assert row["ai_call_counts_by_stage"] == {"pi_case_agent": 1}
    assert row["pi_turn_count"] == 2
    assert row["pi_tool_call_counts"] == {"get_case_context": 1, "fail_closed": 1}
    assert row["tool_rejection_count"] == 1


def test_pi_runtime_blank_budget_exhausted_counts_as_provider_no_response():
    assert runner._is_provider_no_response_result(
        {
            "ok": True,
            "snapshot": {
                "status": "fail_closed",
                "summary": "budget_exhausted",
                "case_agent_error_kind": "pi_runtime_failed",
                "pi_tool_sequence": ["fail_closed"],
                "pi_tool_call_counts": {"fail_closed": 1},
                "pi_runtime_result": {
                    "runner_result": {
                        "final_result_present": False,
                        "turn_count": 12,
                    }
                },
            },
        }
    )


def test_pi_runtime_no_final_after_useful_tools_counts_as_provider_no_response():
    assert runner._is_provider_no_response_result(
        {
            "ok": True,
            "snapshot": {
                "status": "fail_closed",
                "summary": "budget_exhausted",
                "case_agent_error_kind": "pi_runtime_failed",
                "pi_tool_sequence": [
                    "get_recipe_params_draft",
                    "search_bangumi_subjects",
                    "validate_recipe_params_draft",
                ],
                "pi_tool_call_counts": {
                    "get_recipe_params_draft": 2,
                    "search_bangumi_subjects": 1,
                    "validate_recipe_params_draft": 1,
                },
                "pi_runtime_result": {
                    "runner_result": {
                        "final_result_present": False,
                    }
                },
            },
        }
    )


def test_pi_runtime_direct_no_final_error_counts_as_provider_no_response():
    assert runner._is_provider_no_response_result(
        {
            "ok": False,
            "snapshot": {
                "status": "error",
                "summary": "Pi runtime ended without a final submit_organize_recipe_params/fail_closed result.",
                "case_agent_error_kind": "pi_runtime_failed",
                "errors": [
                    "error_kind=pi_runtime_failed",
                    "error_kind=pi_no_final_result",
                ],
                "pi_runtime_result": {
                    "runner_result": {
                        "final_result_present": False,
                    }
                },
            },
        }
    )


def test_semantic_budget_fail_closed_final_result_is_not_provider_no_response():
    assert not runner._is_provider_no_response_result(
        {
            "ok": True,
            "snapshot": {
                "status": "fail_closed",
                "summary": "budget_exhausted",
                "case_agent_error_kind": "pi_runtime_failed",
                "pi_tool_sequence": ["search_bangumi_subjects", "fail_closed"],
                "pi_tool_call_counts": {"search_bangumi_subjects": 1, "fail_closed": 1},
                "pi_runtime_result": {
                    "runner_result": {
                        "final_result_present": True,
                    }
                },
            },
        }
    )


def test_allowed_fail_closed_is_not_strict_failure_retry():
    assert runner._sample_strict_failure_retry_reason(
        {
            "ok": True,
            "snapshot": {
                "status": "fail_closed",
                "summary": "semantic_ambiguity",
                "final_verifier_passed": True,
            },
        }
    ) == ""


def test_unaccepted_fail_closed_counts_as_strict_failure_retry():
    assert runner._sample_strict_failure_retry_reason(
        {
            "ok": True,
            "snapshot": {
                "status": "fail_closed",
                "summary": "No supportable target row surfaced from this attempt.",
                "final_verifier_passed": True,
            },
        }
    ) == "strict_fail_closed"


def test_runtime_no_final_budget_exhausted_is_retried(tmp_path: Path, monkeypatch):
    sample = tmp_path / "sample_retry.json"
    sample.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    class FakeEvidence:
        root_name = "Retry Sample"
        files = [object()]
        main_video_count = 1
        supplemental_candidate_count = 0

    monkeypatch.setattr(runner, "local_evidence_from_raw_sample", lambda _sample: FakeEvidence())
    monkeypatch.setattr(runner, "AIClient", lambda: object())
    monkeypatch.setattr(runner, "BangumiClient", lambda: object())
    attempts = []

    def fake_run_local_bangumi_case_agent_mapping(**_kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            return {
                "ok": True,
                "status": "fail_closed",
                "summary": "budget_exhausted",
                "snapshot": {
                    "status": "fail_closed",
                    "summary": "budget_exhausted",
                    "case_agent_error_kind": "pi_runtime_failed",
                    "pi_tool_sequence": ["validate_recipe_params_draft"],
                    "pi_tool_call_counts": {"validate_recipe_params_draft": 1},
                    "pi_runtime_result": {
                        "runner_result": {
                            "final_result_present": False,
                        }
                    },
                },
            }
        return {
            "ok": True,
            "status": "accepted",
            "summary": "accepted",
            "snapshot": {
                "status": "accepted",
                "summary": "accepted",
                "final_verifier_passed": True,
                "pi_runtime_result": {
                    "runner_result": {
                        "final_result_present": True,
                    }
                },
            },
        }

    monkeypatch.setattr(
        runner,
        "run_local_bangumi_case_agent_mapping",
        fake_run_local_bangumi_case_agent_mapping,
    )

    row = runner._run_mapping_sample_uncapped(sample, output_dir)

    assert len(attempts) == 2
    assert row["status"] == "accepted"
    assert row["sample_runner_retry_count"] == 1
    assert row["sample_runner_retry_reasons"] == ["pi_runtime_no_final_result"]
    written = json.loads((output_dir / "sample_retry.json").read_text(encoding="utf-8"))
    assert written["sample_runner"]["sample_runner_retry_count"] == 1


def test_strict_fail_closed_is_retried(tmp_path: Path, monkeypatch):
    sample = tmp_path / "sample_retry_fail_closed.json"
    sample.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    class FakeEvidence:
        root_name = "Retry Fail Closed Sample"
        files = [object()]
        main_video_count = 1
        supplemental_candidate_count = 0

    monkeypatch.setattr(runner, "local_evidence_from_raw_sample", lambda _sample: FakeEvidence())
    monkeypatch.setattr(runner, "AIClient", lambda: object())
    monkeypatch.setattr(runner, "BangumiClient", lambda: object())
    attempts = []

    def fake_run_local_bangumi_case_agent_mapping(**_kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            return {
                "ok": True,
                "status": "fail_closed",
                "summary": "No supportable target row surfaced from this attempt.",
                "snapshot": {
                    "status": "fail_closed",
                    "summary": "No supportable target row surfaced from this attempt.",
                    "final_verifier_passed": True,
                    "pi_runtime_result": {
                        "runner_result": {
                            "final_result_present": True,
                        }
                    },
                },
            }
        return {
            "ok": True,
            "status": "accepted",
            "summary": "accepted",
            "snapshot": {
                "status": "accepted",
                "summary": "accepted",
                "final_verifier_passed": True,
                "accepted_accounting_ready": True,
                "main_file_count": 1,
                "accounted_for_count": 1,
                "mapped_file_count": 1,
                "resolved_unmapped_file_count": 0,
                "manual_review_file_count": 0,
                "unresolved_count": 0,
            },
        }

    monkeypatch.setattr(
        runner,
        "run_local_bangumi_case_agent_mapping",
        fake_run_local_bangumi_case_agent_mapping,
    )

    row = runner._run_mapping_sample_uncapped(sample, output_dir)

    assert len(attempts) == 2
    assert row["status"] == "accepted"
    assert row["sample_runner_retry_count"] == 1
    assert row["sample_runner_retry_reasons"] == ["strict_fail_closed"]


def test_strict_row_ok_accepts_agent_fail_closed_submit_summary():
    assert runner._strict_row_ok(
        {
            "ok": True,
            "status": "fail_closed",
            "summary": "agent_fail_closed_from_submit",
            "final_verifier_passed": True,
        }
    )


def test_accepted_contract_ok_counts_manual_review_as_accounted():
    assert runner._accepted_contract_ok(
        {
            "status": "accepted",
            "main_file_count": 3,
            "accounted_for_count": 3,
            "mapped_file_count": 1,
            "excluded_file_count": 1,
            "manual_review_file_count": 1,
            "unresolved_count": 0,
            "open_file_count": 0,
            "needs_more_evidence_file_count": 0,
            "unaligned_file_count": 0,
            "accepted_accounting_ready": True,
            "final_verifier_passed": True,
        }
    )


def test_strict_row_ok_accepts_recovery_failure_semantics():
    for summary in ("retrieval_exhausted", "agent_recovery_failed", "semantic_ambiguity", "provider_failure"):
        assert runner._strict_row_ok(
            {
                "ok": True,
                "status": "fail_closed",
                "summary": summary,
                "final_verifier_passed": True,
            }
        )


def test_run_mapping_sample_timeout_writes_timeout_result(tmp_path: Path, monkeypatch):
    sample = tmp_path / "sample_timeout.json"
    sample.write_text(
        json.dumps(
            {
                "root_name": "Timeout Sample",
                "files": [{"path": "Timeout Sample 01.mkv"}],
            }
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    runner._progress_path_for_sample(sample, output_dir).write_text(
        json.dumps(
            {
                "kind": "local_bangumi_pi_case_agent_progress",
                "case_id": "CASE_TIMEOUT",
                "phase": "tool_output",
                "session": {
                    "pi_turn_count": 3,
                    "pi_tool_sequence": ["get_case_context", "fail_closed"],
                    "tool_rejection_count": 1,
                },
            }
        ),
        encoding="utf-8",
    )

    class FakeProcess:
        exitcode = None

        def __init__(self, *args, **kwargs):
            self.terminated = False

        def start(self):
            return None

        def join(self, timeout=None):
            return None

        def is_alive(self):
            return not self.terminated

        def terminate(self):
            self.terminated = True
            self.exitcode = -15

    monkeypatch.setattr(runner.mp, "Process", FakeProcess)

    row = runner._run_mapping_sample(sample, output_dir, max_rounds=1, sample_timeout_seconds=1)

    assert row["status"] == "error"
    assert row["sample_timed_out"] is True
    assert row["sample_timeout_seconds"] == 1
    assert row["summary"] == "sample_timeout_1s"
    assert row["partial_progress_phase"] == "tool_output"
    assert row["partial_pi_turn_count"] == 3
    assert row["partial_pi_tool_sequence"] == ["get_case_context", "fail_closed"]
    written = json.loads((output_dir / "sample_timeout.json").read_text(encoding="utf-8"))
    assert written["sample_runner"]["sample_timed_out"] is True
    assert written["case_agent_progress"]["case_id"] == "CASE_TIMEOUT"


def test_runner_progress_is_used_when_pi_progress_not_started(tmp_path: Path, monkeypatch):
    sample = tmp_path / "sample_progress.json"
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    progress_path = runner._progress_path_for_sample(sample, output_dir)
    monkeypatch.setenv(runner.CASE_AGENT_PROGRESS_ENV_VAR, progress_path.as_posix())

    runner._write_runner_progress(
        sample,
        output_dir,
        phase="case_agent_mapping_started",
        extra={"attempt": 1, "max_rounds": 12},
    )
    row = runner._row_with_partial_progress(
        {
            "sample": sample.as_posix(),
            "status": "error",
            "ok": False,
            "summary": "sample_timeout_300s",
        },
        progress_path,
    )

    payload = json.loads(progress_path.read_text(encoding="utf-8"))
    assert payload["kind"] == "local_bangumi_sample_runner_progress"
    assert payload["phase"] == "case_agent_mapping_started"
    assert row["partial_progress_phase"] == "case_agent_mapping_started"
    assert row["partial_progress_path"] == progress_path.as_posix()


def test_sample_timeout_passes_shorter_pi_timeout_to_child(tmp_path: Path, monkeypatch):
    sample = tmp_path / "sample_progress.json"
    sample.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    captured = {}

    class FakeProcess:
        exitcode = 0

        def __init__(self, *args, **kwargs):
            captured["args"] = kwargs.get("args", ())
            self.terminated = False

        def start(self):
            return None

        def join(self, timeout=None):
            return None

        def is_alive(self):
            return False

        def terminate(self):
            self.terminated = True

    class FakeQueue:
        def __init__(self, maxsize=1):
            pass

        def get(self, timeout=None):
            return {"ok": True, "row": {"sample": sample.as_posix(), "status": "accepted", "ok": True}}

    monkeypatch.setattr(runner.mp, "Process", FakeProcess)
    monkeypatch.setattr(runner.mp, "Queue", FakeQueue)

    row = runner._run_mapping_sample(sample, output_dir, max_rounds=None, sample_timeout_seconds=120)

    assert row["status"] == "accepted"
    assert len(captured["args"]) == 6
    assert captured["args"][-2] == 105
    assert captured["args"][-1] > captured["args"][-2]


def test_raw_sample_builds_local_fact_surface_shape(tmp_path: Path):
    sample = tmp_path / "sample_fact.json"
    sample.write_text(
        json.dumps(
            {
                "root_name": "Fact Sample",
                "files": [
                    {"path": "Fact Sample/Fact Sample - 01.mkv", "size": 123},
                    {"path": "Fact Sample/Fact Sample - 01.chs.ass", "size": 45},
                ],
            }
        ),
        encoding="utf-8",
    )

    evidence = runner.local_evidence_from_raw_sample(sample)

    assert evidence.fact_surface is not None
    assert len(evidence.fact_surface.files) == 2
    video_fact = next(fact for fact in evidence.fact_surface.files if fact.relative_path.endswith(".mkv"))
    assert video_fact.container_facts.probe_status == "not_attempted"
    assert video_fact.subtitle_facts.external_subtitle_refs
    assert video_fact.subtitle_facts.external_subtitle_refs[0]["language_markers"] == ["chs"]
    assert video_fact.missing_facts


def test_raw_sample_file_container_facts_overlay_runtime_surface(tmp_path: Path):
    sample = tmp_path / "sample_fact_with_duration.json"
    sample.write_text(
        json.dumps(
            {
                "root_name": "Fact Sample",
                "files": [
                    {
                        "path": "Fact Sample/Fact Sample - 01.mkv",
                        "size": 123,
                        "container_facts": {
                            "probe_status": "available",
                            "duration_seconds": 1420.005,
                            "container_format": "matroska,webm",
                            "video_stream_count": 1,
                            "audio_stream_count": 1,
                            "subtitle_stream_count": 0,
                            "resolution": "1920x1080",
                            "probe_error_class": "",
                        },
                    },
                    {"path": "Fact Sample/Fact Sample - 01.chs.ass", "size": 45},
                ],
            }
        ),
        encoding="utf-8",
    )

    evidence = runner.local_evidence_from_raw_sample(sample)
    surface = local_fact_surface_to_dict(evidence.fact_surface)
    video_fact = next(item for item in surface["files"] if item["relative_path"].endswith(".mkv"))

    assert video_fact["container_facts"]["probe_status"] == "available"
    assert video_fact["container_facts"]["duration_seconds"] == 1420.005
    assert "container_facts" not in {
        item.get("fact_class") for item in video_fact["missing_facts"]
    }


def test_dry_build_row_reports_local_fact_counts(tmp_path: Path):
    sample = tmp_path / "sample_fact_dry.json"
    sample.write_text(
        json.dumps(
            {
                "root_name": "Dry Fact Sample",
                "files": [{"path": "Dry Fact Sample/Dry Fact Sample - 01.mkv", "size": 123}],
            }
        ),
        encoding="utf-8",
    )

    row = runner._dry_build_row(sample)

    assert row["status"] == "dry_build"
    assert row["local_fact_file_count"] == 1
    assert row["local_fact_probe_status_counts"]["not_attempted"] == 1
    assert row["local_fact_missing_fact_summary"]["by_class"]["container_facts"] == 1


def test_run_in_parallel_runs_single_sample_serially(monkeypatch):
    class FailingExecutor:
        def __init__(self, *args, **kwargs):
            raise AssertionError("single sample should not enter ThreadPoolExecutor")

    monkeypatch.setattr(runner, "ThreadPoolExecutor", FailingExecutor)
    calls: list[str] = []

    rows = runner._run_in_parallel(
        [Path("sample_one.json")],
        lambda sample, prefix: calls.append(sample.name) or {"sample": sample.name, "prefix": prefix},
        "ok",
    )

    assert calls == ["sample_one.json"]
    assert rows == [{"sample": "sample_one.json", "prefix": "ok"}]


def test_sample_worker_default_is_ten():
    assert runner.SAMPLE_WORKER_COUNT == 10
