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
                {"call_name": "call_mapping_draft_editor", "provider_retry_count": 2},
                {"call_name": "LocalPackageAnalysis", "provider_retry_count": 9},
                {"note": "not an ai call"},
            ]
        }
    )

    assert stats["ai_call_count"] == 3
    assert stats["ai_attempt_count_estimate"] == 6
    assert stats["ai_provider_retry_count"] == 3
    assert stats["ai_call_counts_by_stage"] == {
        "local_structure": 1,
        "case_briefing": 1,
        "mapping_draft_editor": 1,
    }
    assert stats["ai_attempt_counts_by_stage"]["case_briefing"] == 2
    assert stats["ai_attempt_counts_by_stage"]["mapping_draft_editor"] == 3


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
