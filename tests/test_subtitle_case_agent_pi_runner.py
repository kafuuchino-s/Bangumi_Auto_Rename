"""字幕 Case Agent Pi runner / pi_tools 单测（Phase 3）。

不真起 node sidecar：用 ``runtime_invoker`` 注入模拟 Pi 驱动（直接调
``state.handle_tool`` 模拟 agent 的 get_context → validate → submit 序列），
或用 fake runtime env。覆盖三态：accepted / fail_closed / timeout→auto fail_closed。

对齐 tests/test_case_agent_* pi_runner 风格：构造 workspace + language_resolver，
断言 SubtitleCaseAgentRunResult.status / compiled_plan / tool_trace。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.subtitle.case_agent.evidence_broker import build_target_video_cards
from src.subtitle.case_agent.models import SubtitleFileCard
from src.subtitle.case_agent.pi_runner import (
    SubtitleCaseAgentRunResult,
    run_subtitle_case_agent_pi,
)
from src.subtitle.case_agent.pi_tools import SubtitleCaseToolState
from src.subtitle.case_agent.workspace import build_subtitle_case_workspace


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _subs(tmp_path) -> list[SubtitleFileCard]:
    return [
        SubtitleFileCard(ref="", archive_path="S1/01.ass", filename="01.chs.ass", language_hint="chs"),
        SubtitleFileCard(ref="", archive_path="S1/02.ass", filename="02.chs.ass", language_hint="chs"),
    ]


def _tasks(tmp_path) -> list[dict[str, object]]:
    target_dir = tmp_path / "lib" / "Foo (2020)" / "Season 01"
    target_dir.mkdir(parents=True, exist_ok=True)
    v1 = "Foo - S01E01 - A.mkv"
    v2 = "Foo - S01E02 - B.mkv"
    (target_dir / v1).write_text("v", encoding="utf-8")
    (target_dir / v2).write_text("v", encoding="utf-8")
    return [
        {
            "uuid": "t1",
            "title": "Foo",
            "season": 1,
            "is_movie": False,
            "videos": [v1, v2],
            "target_dir": str(target_dir),
            "video_targets": {v1: str(target_dir / v1), v2: str(target_dir / v2)},
        }
    ]


def _workspace(tmp_path) -> Any:
    return build_subtitle_case_workspace(
        archive_name="foo.zip",
        subtitle_files=_subs(tmp_path),
        target_videos=build_target_video_cards(_tasks(tmp_path)),
    )


def _lang_resolver(lang: str) -> tuple[str, bool]:
    table = {"chs": ("zh-CN", True), "cht": ("zh-TW", False), "jpn": ("ja", False)}
    return table.get((lang or "").lower().strip(), ("zh-CN", True))


def _good_draft() -> dict[str, Any]:
    return {
        "summary": "all mapped",
        "confidence": "High",
        "rows": [
            {"row_ref": "R1", "subtitle_ref": "SF1", "disposition": "map_to_video", "target_ref": "TV1", "language": "chs", "reason": "ep1"},
            {"row_ref": "R2", "subtitle_ref": "SF2", "disposition": "map_to_video", "target_ref": "TV2", "language": "chs", "reason": "ep2"},
        ],
    }


def _bad_draft_coverage() -> dict[str, Any]:
    # 只映射 SF1，漏 SF2，不 unmatched -> coverage error
    return {
        "summary": "partial",
        "confidence": "Medium",
        "rows": [
            {"row_ref": "R1", "subtitle_ref": "SF1", "disposition": "map_to_video", "target_ref": "TV1", "language": "chs", "reason": "ep1"},
        ],
    }


# ---------------------------------------------------------------------------
# pi_tools: SubtitleCaseToolState
# ---------------------------------------------------------------------------

def test_tool_state_case_input_shape(tmp_path):
    ws = _workspace(tmp_path)
    state = SubtitleCaseToolState(
        workspace=ws,
        run_dir=tmp_path / "run",
        language_resolver=_lang_resolver,
        archive_name="foo.zip",
        sample_id="case-1",
    )
    case_input = state.case_input()
    assert case_input["case_agent_mode"] == "subtitle_case_agent"
    assert case_input["sample_id"] == "case-1"
    assert case_input["case_goal"]["objective"]
    assert len(case_input["case_goal"]["done_when"]) == 4
    assert case_input["context"]["subtitle_files"]
    assert case_input["context"]["target_videos"]
    assert case_input["context"]["subtitle_contract"]["final_tools"] == [
        "validate_subtitle_mapping",
        "submit_subtitle_mapping",
        "fail_closed",
    ]


def test_tool_get_context_returns_cards(tmp_path):
    ws = _workspace(tmp_path)
    state = SubtitleCaseToolState(workspace=ws, run_dir=tmp_path / "run", language_resolver=_lang_resolver)
    result = state.handle_tool("get_subtitle_mapping_context", {})
    assert result["ok"] is True
    data = result["data"]
    assert len(data["subtitle_files"]) == 2
    assert len(data["target_videos"]) == 2
    assert data["subtitle_files"][0]["ref"] == "SF1"


def test_tool_validate_accepts_good_draft(tmp_path):
    ws = _workspace(tmp_path)
    state = SubtitleCaseToolState(workspace=ws, run_dir=tmp_path / "run", language_resolver=_lang_resolver)
    result = state.handle_tool("validate_subtitle_mapping", {"mapping_draft": _good_draft()})
    assert result["ok"] is True
    assert result["accepted"] is True
    assert result["status"] == "accepted"
    assert state.verifier_result is not None
    assert state.verifier_result.passed is True


def test_tool_validate_rejects_coverage_error(tmp_path):
    ws = _workspace(tmp_path)
    state = SubtitleCaseToolState(workspace=ws, run_dir=tmp_path / "run", language_resolver=_lang_resolver)
    result = state.handle_tool("validate_subtitle_mapping", {"mapping_draft": _bad_draft_coverage()})
    assert result["ok"] is True
    assert result["accepted"] is False
    assert result["status"] == "invalid"
    assert any(h for h in result["repair_hints"])  # 给了修复提示
    assert state.verifier_result is not None
    assert state.verifier_result.passed is False


def test_tool_submit_accepted_sets_final_result(tmp_path):
    ws = _workspace(tmp_path)
    state = SubtitleCaseToolState(workspace=ws, run_dir=tmp_path / "run", language_resolver=_lang_resolver)
    result = state.handle_tool("submit_subtitle_mapping", {"mapping_draft": _good_draft()})
    assert result["accepted"] is True
    assert result["status"] == "accepted"
    assert state.final_result is not None
    assert state.final_result["status"] == "accepted"
    assert state.compiled_plan is not None
    assert len(state.compiled_plan.mappings) == 2
    assert (Path(tmp_path / "run" / "final_result.json")).exists()


def test_tool_submit_rejected_increments_rejection(tmp_path):
    ws = _workspace(tmp_path)
    state = SubtitleCaseToolState(workspace=ws, run_dir=tmp_path / "run", language_resolver=_lang_resolver)
    result = state.handle_tool("submit_subtitle_mapping", {"mapping_draft": _bad_draft_coverage()})
    assert result["accepted"] is False
    assert state.submit_rejection_count == 1
    assert state.final_result is None
    assert state.last_invalid_submission is not None


def test_tool_fail_closed_sets_final(tmp_path):
    ws = _workspace(tmp_path)
    state = SubtitleCaseToolState(workspace=ws, run_dir=tmp_path / "run", language_resolver=_lang_resolver)
    result = state.handle_tool("fail_closed", {"reason": "no target", "reason_kind": "insufficient_evidence"})
    assert result["accepted"] is True
    assert result["status"] == "fail_closed"
    assert state.final_result["status"] == "fail_closed"
    assert state.final_result["reason_kind"] == "insufficient_evidence"


def test_tool_unknown_tool_returns_error(tmp_path):
    ws = _workspace(tmp_path)
    state = SubtitleCaseToolState(workspace=ws, run_dir=tmp_path / "run", language_resolver=_lang_resolver)
    result = state.handle_tool("does_not_exist", {})
    assert result["ok"] is False
    assert "unknown tool" in result["error"]


def test_auto_finalize_after_validated_draft(tmp_path):
    ws = _workspace(tmp_path)
    state = SubtitleCaseToolState(workspace=ws, run_dir=tmp_path / "run", language_resolver=_lang_resolver)
    state.handle_tool("validate_subtitle_mapping", {"mapping_draft": _good_draft()})
    # validate 已通过但未 submit -> auto_finalize 应自动 submit
    result = state.auto_finalize_accepted_validation()
    assert result["accepted"] is True
    assert state.final_result is not None
    assert state.final_result.get("auto_finalized_from_validated_draft") is True


def test_auto_fail_closed_no_final_result(tmp_path):
    ws = _workspace(tmp_path)
    state = SubtitleCaseToolState(workspace=ws, run_dir=tmp_path / "run", language_resolver=_lang_resolver)
    result = state.auto_fail_closed_no_final_result("budget_exhausted")
    assert result["status"] == "fail_closed"
    assert state.final_result["reason_kind"] == "provider_failure"


def test_tool_summary_counts(tmp_path):
    ws = _workspace(tmp_path)
    state = SubtitleCaseToolState(workspace=ws, run_dir=tmp_path / "run", language_resolver=_lang_resolver)
    state.handle_tool("get_subtitle_mapping_context", {})
    state.handle_tool("validate_subtitle_mapping", {"mapping_draft": _good_draft()})
    summary = state.tool_summary()
    assert summary["tool_trace_count"] == 2
    assert summary["tool_call_counts"]["get_subtitle_mapping_context"] == 1
    assert summary["tool_call_counts"]["validate_subtitle_mapping"] == 1


# ---------------------------------------------------------------------------
# pi_runner: run_subtitle_case_agent_pi（runtime_invoker 模拟，不起 node）
# ---------------------------------------------------------------------------

def _invoker_submit_good(state: SubtitleCaseToolState) -> dict[str, Any]:
    """模拟 Pi agent：get_context → validate → submit 合法 draft。"""
    state.handle_tool("get_subtitle_mapping_context", {})
    state.handle_tool("validate_subtitle_mapping", {"mapping_draft": _good_draft()})
    state.handle_tool("submit_subtitle_mapping", {"mapping_draft": _good_draft(), "summary": "pi accepted"})
    return {"ok": True, "returncode": 0, "argv": ["fake-pi"], "fake": True}


def _invoker_fail_closed(state: SubtitleCaseToolState) -> dict[str, Any]:
    """模拟 Pi agent：直接 fail_closed。"""
    state.handle_tool("get_subtitle_mapping_context", {})
    state.handle_tool("fail_closed", {"reason": "global ambiguity", "reason_kind": "contradiction"})
    return {"ok": True, "returncode": 0, "argv": ["fake-pi"], "fake": True}


def _invoker_timeout_no_final(state: SubtitleCaseToolState) -> dict[str, Any]:
    """模拟 Pi 超时：只调了 get_context，没产出 final_result。"""
    state.handle_tool("get_subtitle_mapping_context", {})
    return {"ok": False, "returncode": None, "argv": ["fake-pi"], "error": "timeout", "timeout_seconds": 300}


def test_pi_runner_accepted(monkeypatch, tmp_path):
    monkeypatch.setattr("src.subtitle.case_agent.pi_runner._case_root", lambda: tmp_path / "case_root")
    ws = _workspace(tmp_path)
    result = run_subtitle_case_agent_pi(
        workspace=ws,
        language_resolver=_lang_resolver,
        source_path=tmp_path / "foo.zip",
        archive_name="foo.zip",
        runtime_invoker=_invoker_submit_good,
    )
    assert isinstance(result, SubtitleCaseAgentRunResult)
    assert result.status == "accepted"
    assert result.ok is True
    assert result.compiled_plan is not None
    assert len(result.compiled_plan.mappings) == 2
    assert result.final_action == "submit_subtitle_mapping"
    assert "submit_subtitle_mapping" in result.tool_sequence
    assert (result.run_dir / "final_result.json").exists()
    assert (result.run_dir / "run_result_summary.json").exists()


def test_pi_runner_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setattr("src.subtitle.case_agent.pi_runner._case_root", lambda: tmp_path / "case_root")
    ws = _workspace(tmp_path)
    result = run_subtitle_case_agent_pi(
        workspace=ws,
        language_resolver=_lang_resolver,
        source_path=tmp_path / "foo.zip",
        archive_name="foo.zip",
        runtime_invoker=_invoker_fail_closed,
    )
    assert result.status == "fail_closed"
    assert result.ok is True  # 合格业务结果
    assert result.compiled_plan is None
    assert result.final_action == "fail_closed"


def test_pi_runner_timeout_auto_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setattr("src.subtitle.case_agent.pi_runner._case_root", lambda: tmp_path / "case_root")
    ws = _workspace(tmp_path)
    result = run_subtitle_case_agent_pi(
        workspace=ws,
        language_resolver=_lang_resolver,
        source_path=tmp_path / "foo.zip",
        archive_name="foo.zip",
        runtime_invoker=_invoker_timeout_no_final,
    )
    # 超时 -> auto_fail_closed（provider_failure）
    assert result.status == "fail_closed"
    assert result.ok is True
    # final_result 由 auto_fail_closed 写入，reason_kind=provider_failure
    assert "timeout" in result.summary or "fail_closed" in result.summary


def test_pi_runner_validated_but_not_submitted_auto_finalizes(monkeypatch, tmp_path):
    """Pi agent validate 通过但未 submit 就结束 -> runner auto_finalize 兜底 submit。"""
    monkeypatch.setattr("src.subtitle.case_agent.pi_runner._case_root", lambda: tmp_path / "case_root")

    def invoker(state: SubtitleCaseToolState) -> dict[str, Any]:
        state.handle_tool("get_subtitle_mapping_context", {})
        state.handle_tool("validate_subtitle_mapping", {"mapping_draft": _good_draft()})
        # 故意不 submit
        return {"ok": True, "returncode": 0, "argv": ["fake-pi"], "fake": True}

    ws = _workspace(tmp_path)
    result = run_subtitle_case_agent_pi(
        workspace=ws,
        language_resolver=_lang_resolver,
        source_path=tmp_path / "foo.zip",
        archive_name="foo.zip",
        runtime_invoker=invoker,
    )
    assert result.status == "accepted"
    assert result.compiled_plan is not None


def test_pi_runner_entry_with_fake_env(monkeypatch, tmp_path):
    """entry backend=pi + fake runtime env（tool_calls 模拟 agent 序列）-> accepted。"""
    import json
    import os

    from src.subtitle.case_agent.local_subtitle_entry import run_subtitle_case_agent_mapping
    from src.subtitle.case_agent.pi_runner import _FAKE_RUNTIME_ENV

    monkeypatch.setattr("src.subtitle.case_agent.pi_runner._case_root", lambda: tmp_path / "case_root")
    fake_payload = {
        "tool_calls": [
            {"tool": "get_subtitle_mapping_context", "arguments": {}},
            {"tool": "submit_subtitle_mapping", "arguments": {"mapping_draft": _good_draft(), "summary": "fake env accepted"}},
        ]
    }
    monkeypatch.setenv(_FAKE_RUNTIME_ENV, json.dumps(fake_payload))

    tasks = _tasks(tmp_path)
    res = run_subtitle_case_agent_mapping(
        subtitle_files=_subs(tmp_path),
        processed_tasks=tasks,
        source_path=tmp_path / "foo.zip",
        language_resolver=_lang_resolver,
        archive_name="foo.zip",
        backend="pi",
    )
    assert res["status"] == "accepted"
    assert res["compiled_plan"] is not None
    assert len(res["compiled_plan"].mappings) == 2
    assert res["snapshot"]["case_agent_mode"] == "pi_subtitle_case_agent"
    assert res["snapshot"]["pi_run"]["status"] == "accepted"
