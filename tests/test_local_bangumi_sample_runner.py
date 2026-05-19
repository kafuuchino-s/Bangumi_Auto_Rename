from __future__ import annotations

import json
from pathlib import Path

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
                {"call_name": "call_local_structure_agent", "provider_retry_count": 0},
                {"call_name": "call_case_briefing_agent", "provider_retry_count": 1},
                {
                    "note": "orchestrator_agent_called",
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
                {"call_name": "call_mapping_draft_editor", "provider_retry_count": 2},
                {"call_name": "LocalPackageAnalysis", "provider_retry_count": 9},
                {"note": "not an ai call"},
            ]
        }
    )

    assert stats["ai_call_count"] == 4
    assert stats["ai_attempt_count_estimate"] == 9
    assert stats["ai_provider_retry_count"] == 5
    assert stats["ai_call_counts_by_stage"] == {
        "local_structure": 1,
        "case_briefing": 1,
        "orchestrator_agent": 1,
        "mapping_draft_editor": 1,
    }
    assert stats["ai_attempt_counts_by_stage"]["case_briefing"] == 2
    assert stats["ai_attempt_counts_by_stage"]["orchestrator_agent"] == 3
    assert stats["ai_attempt_counts_by_stage"]["mapping_draft_editor"] == 3
    assert stats["ai_provider_retry_counts_by_stage"]["orchestrator_agent"] == 2
    assert stats["orchestrator_usage_total_tokens"] == 110
    assert stats["orchestrator_usage_input_tokens"] == 100
    assert stats["orchestrator_usage_output_tokens"] == 10
    assert stats["orchestrator_provider_cached_input_tokens"] == 40
    assert stats["orchestrator_provider_cached_input_ratio"] == 0.4
    assert stats["orchestrator_max_turn_input_tokens"] == 100


def test_sample_row_includes_case_agent_ai_call_stats(tmp_path: Path):
    row = runner._sample_row(
        tmp_path / "sample.json",
        {
            "ok": True,
            "snapshot": {
                "status": "fail_closed",
                "summary": "no_new_evidence",
                "orchestrator_turn_count": 2,
                "orchestrator_tool_call_counts": {"compose_queries": 1, "execute_evidence": 1},
                "orchestrator_tool_sequence": ["compose_queries", "execute_evidence"],
                "tool_rejection_count": 1,
                "compact_count": 0,
                "case_judge_request_audits": [
                    {"call_name": "call_case_planner", "provider_retry_count": 0},
                    {"call_name": "call_case_judge", "provider_retry_count": 1},
                ],
            },
        },
        elapsed_ms=123,
    )

    assert row["ai_call_count"] == 2
    assert row["ai_attempt_count_estimate"] == 3
    assert row["ai_call_counts_by_stage"] == {"case_planner": 1, "case_judge": 1}
    assert row["orchestrator_turn_count"] == 2
    assert row["orchestrator_tool_call_counts"] == {"compose_queries": 1, "execute_evidence": 1}
    assert row["tool_rejection_count"] == 1


def test_strict_row_ok_accepts_agent_fail_closed_submit_summary():
    assert runner._strict_row_ok(
        {
            "ok": True,
            "status": "fail_closed",
            "summary": "agent_fail_closed_from_submit",
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
                "kind": "local_bangumi_orchestrator_progress",
                "case_id": "CASE_TIMEOUT",
                "phase": "tool_output",
                "session": {
                    "orchestrator_turn_count": 3,
                    "orchestrator_tool_sequence": ["propose_case_understanding", "execute_evidence"],
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
    assert row["partial_orchestrator_turn_count"] == 3
    assert row["partial_orchestrator_tool_sequence"] == ["propose_case_understanding", "execute_evidence"]
    written = json.loads((output_dir / "sample_timeout.json").read_text(encoding="utf-8"))
    assert written["sample_runner"]["sample_timed_out"] is True
    assert written["case_agent_progress"]["case_id"] == "CASE_TIMEOUT"


def test_runner_progress_is_used_when_orchestrator_progress_not_started(tmp_path: Path, monkeypatch):
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
