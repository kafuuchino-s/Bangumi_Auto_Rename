"""auto_fetch Case Agent Pi runner / pi_tools 单测（Phase 3）。

不真起 node sidecar：用 ``runtime_invoker`` 注入模拟 Pi 驱动（直接调
``state.handle_tool`` 模拟 agent 的 search → submit_candidate → load →
submit_package 序列），或用 fake runtime env。覆盖三态：
accepted / fail_closed / timeout→auto fail_closed。

对齐 tests/test_subtitle_case_agent_pi_runner 风格：构造 workspace + fake
provider，断言 AutoFetchCaseAgentRunResult.status / tool_trace / selected refs。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.subtitle.auto_fetch_case_agent import (
    AutoFetchCaseAgentRunResult,
    AutoFetchCaseToolState,
    MissingVideoCard,
    ScanScopeCard,
    SearchKeywordCard,
    build_auto_fetch_case_workspace,
    run_auto_fetch_case_agent_pi,
)
from src.subtitle.providers.base import (
    SubtitleCandidate,
    SubtitleThreadPackage,
    SubtitleThreadPackageLink,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _workspace() -> Any:
    return build_auto_fetch_case_workspace(
        task_uuid="t1",
        scan_scope=ScanScopeCard(scope_type="series", root="/lib/Foo", source="task_data"),
        missing_videos=[
            MissingVideoCard(
                task_uuid="t1",
                video="Foo - S01E01 - Pilot.mkv",
                target_path="/lib/Foo/Season 01/Foo - S01E01 - Pilot.mkv",
                source_video="[SubGroup] Foo 01.mkv",
                task_title="Foo",
                season=1,
                is_movie=False,
            )
        ],
        keywords=[SearchKeywordCard(keyword="Foo")],
    )


def _make_package(package_id, flags, *, has_direct=True):
    return SubtitleThreadPackage(
        package_id=package_id,
        page_number=1,
        floor_label=f"{package_id}-floor",
        post_author="author",
        post_time="2023-01-01 00:00:00",
        post_text="text",
        context_text="ctx",
        links=[
            SubtitleThreadPackageLink(
                url=f"https://x/{package_id}.zip",
                kind="attachment",
                label=f"{package_id}.zip",
                filename_hint=f"{package_id}.zip",
                is_direct_download=has_direct,
            )
        ],
        has_direct_download=has_direct,
        package_flags=flags,
    )


def _candidate(title="Foo 字幕", *, packages=None):
    c = SubtitleCandidate(
        title=title, detail_url=f"https://bbs.acgrip.com/{title}", source="acgrip"
    )
    if packages is not None:
        c.thread_packages = packages
    return c


class _FakeProvider:
    """记录调用的 fake provider。"""

    def __init__(self, candidates_by_keyword: dict[str, list] | None = None):
        self._by_kw = candidates_by_keyword or {}
        self.search_calls: list[str] = []
        self.prepare_calls: list[str] = []
        self.load_calls: list[str] = []

    def search(self, keyword, limit=10):
        self.search_calls.append(keyword)
        return list(self._by_kw.get(keyword, []))

    def prepare_candidate(self, candidate):
        self.prepare_calls.append(candidate.title)
        return candidate

    def load_thread_packages(self, candidate):
        self.load_calls.append(candidate.title)
        return candidate


# ---------------------------------------------------------------------------
# pi_tools：6 工具
# ---------------------------------------------------------------------------

def test_tool_get_context_exposes_missing_videos_and_keywords(tmp_path):
    state = AutoFetchCaseToolState(
        workspace=_workspace(), run_dir=tmp_path, provider=_FakeProvider()
    )
    result = state.handle_tool("get_auto_fetch_context", {"detail": True})
    assert result["ok"] is True
    data = result["data"]
    assert data["scan_scope"]["scope_type"] == "series"
    assert len(data["missing_videos"]) == 1
    assert data["missing_videos"][0]["source_video"] == "[SubGroup] Foo 01.mkv"
    assert len(data["keywords"]) == 1


def test_tool_search_candidates_injects_cd_refs(tmp_path):
    provider = _FakeProvider({"Foo": [_candidate(packages=[_make_package("p1", ["batch", "simplified"])])]})
    state = AutoFetchCaseToolState(workspace=_workspace(), run_dir=tmp_path, provider=provider)
    result = state.handle_tool("search_candidates", {"keyword": "Foo"})
    assert result["ok"] is True
    assert result["candidate_count"] == 1
    assert result["new_candidate_refs"] == ["CD1"]
    assert state.workspace.candidate_refs == ["CD1"]
    assert "CD1" in state.provider_candidates_by_ref


def test_tool_search_candidates_empty_returns_no_candidates(tmp_path):
    provider = _FakeProvider({"Foo": []})
    state = AutoFetchCaseToolState(workspace=_workspace(), run_dir=tmp_path, provider=provider)
    result = state.handle_tool("search_candidates", {"keyword": "Foo"})
    assert result["ok"] is True
    assert result["status"] == "no_candidates"
    assert result["candidate_count"] == 0


def test_tool_load_candidate_packages_backfills_pk_refs(tmp_path):
    cand = _candidate(packages=[_make_package("p1", ["batch", "simplified"])])
    provider = _FakeProvider({"Foo": [cand]})
    state = AutoFetchCaseToolState(workspace=_workspace(), run_dir=tmp_path, provider=provider)
    state.handle_tool("search_candidates", {"keyword": "Foo"})
    result = state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
    assert result["ok"] is True
    assert result["status"] == "packages_loaded"
    ws_cand = state.workspace.candidate_by_ref().get("CD1")
    assert ws_cand is not None
    assert len(ws_cand.packages) == 1
    assert ws_cand.packages[0].ref == "PK1"
    assert "PK1" in state.provider_packages_by_ref


def test_tool_submit_candidate_gate_accepts_downloadable(tmp_path):
    cand = _candidate(packages=[_make_package("p1", ["batch", "simplified"])])
    provider = _FakeProvider({"Foo": [cand]})
    state = AutoFetchCaseToolState(workspace=_workspace(), run_dir=tmp_path, provider=provider)
    state.handle_tool("search_candidates", {"keyword": "Foo"})
    result = state.handle_tool(
        "submit_candidate", {"candidate_ref": "CD1", "language": "chs", "reason": "arc match"}
    )
    assert result["ok"] is True
    assert result["accepted"] is True
    assert result["status"] == "candidate_accepted"
    assert result["next_action"] == "submit_package"
    # 候选阶段不落 final_result
    assert state.final_result is None


def test_tool_submit_candidate_gate_rejects_unknown_ref(tmp_path):
    state = AutoFetchCaseToolState(
        workspace=_workspace(), run_dir=tmp_path, provider=_FakeProvider()
    )
    result = state.handle_tool("submit_candidate", {"candidate_ref": "CD99"})
    assert result["ok"] is True
    assert result["accepted"] is False
    assert result["status"] == "invalid"


def test_tool_submit_package_gate_accepts_and_sets_final(tmp_path):
    cand = _candidate(packages=[_make_package("p1", ["batch", "simplified"])])
    provider = _FakeProvider({"Foo": [cand]})
    state = AutoFetchCaseToolState(workspace=_workspace(), run_dir=tmp_path, provider=provider)
    state.handle_tool("search_candidates", {"keyword": "Foo"})
    state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
    result = state.handle_tool("submit_package", {"package_ref": "PK1", "reason": "main batch"})
    assert result["ok"] is True
    assert result["accepted"] is True
    assert result["status"] == "accepted"
    assert state.final_result is not None
    assert state.final_result["selected_candidate_ref"] == "CD1"
    assert state.final_result["selected_package_ref"] == "PK1"
    assert state.final_result["download_url"] == "https://x/p1.zip"


def test_tool_submit_package_gate_rejects_font_only(tmp_path):
    cand = _candidate(packages=[_make_package("font", ["font"])])
    provider = _FakeProvider({"Foo": [cand]})
    state = AutoFetchCaseToolState(workspace=_workspace(), run_dir=tmp_path, provider=provider)
    state.handle_tool("search_candidates", {"keyword": "Foo"})
    state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
    result = state.handle_tool("submit_package", {"package_ref": "PK1"})
    assert result["accepted"] is False
    assert result["status"] == "invalid"
    assert state.final_result is None


def test_tool_inspect_package_returns_details(tmp_path):
    cand = _candidate(packages=[_make_package("p1", ["batch", "simplified"])])
    provider = _FakeProvider({"Foo": [cand]})
    state = AutoFetchCaseToolState(workspace=_workspace(), run_dir=tmp_path, provider=provider)
    state.handle_tool("search_candidates", {"keyword": "Foo"})
    state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
    result = state.handle_tool("inspect_package", {"package_ref": "PK1"})
    assert result["ok"] is True
    assert result["package"]["package_flags"] == ["batch", "simplified"]
    assert result["package"]["is_font_or_patch_only"] is False


def test_tool_fail_closed_sets_final(tmp_path):
    state = AutoFetchCaseToolState(
        workspace=_workspace(), run_dir=tmp_path, provider=_FakeProvider()
    )
    result = state.handle_tool("fail_closed", {"reason": "no candidate matches arc", "reason_kind": "insufficient_evidence"})
    assert result["accepted"] is True
    assert result["status"] == "fail_closed"
    assert state.final_result["status"] == "fail_closed"


def test_tool_need_confirm_sets_final(tmp_path):
    state = AutoFetchCaseToolState(
        workspace=_workspace(), run_dir=tmp_path, provider=_FakeProvider()
    )
    result = state.handle_tool("need_confirm", {"reason": "ambiguous between two candidates"})
    assert result["accepted"] is True
    assert result["status"] == "need_confirm"
    assert state.final_result["status"] == "need_confirm"


def test_unknown_tool_returns_error(tmp_path):
    state = AutoFetchCaseToolState(
        workspace=_workspace(), run_dir=tmp_path, provider=_FakeProvider()
    )
    result = state.handle_tool("bogus_tool", {})
    assert result["ok"] is False
    assert "unknown tool" in result["error"]


# ---------------------------------------------------------------------------
# pi_runner：runtime_invoker 三态
# ---------------------------------------------------------------------------

def test_pi_runner_accepted_via_runtime_invoker(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.subtitle.auto_fetch_case_agent.pi_runner._case_root",
        lambda: tmp_path,
    )
    cand = _candidate(packages=[_make_package("p1", ["batch", "simplified"])])
    provider = _FakeProvider({"Foo": [cand]})

    def invoker(state: AutoFetchCaseToolState):
        state.handle_tool("search_candidates", {"keyword": "Foo"})
        state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
        state.handle_tool("submit_package", {"package_ref": "PK1", "reason": "main batch"})
        return {"ok": True, "returncode": 0, "argv": ["fake"]}

    result = run_auto_fetch_case_agent_pi(
        workspace=_workspace(), provider=provider, task_data={"uuid": "t1"},
        runtime_invoker=invoker,
    )
    assert isinstance(result, AutoFetchCaseAgentRunResult)
    assert result.status == "accepted"
    assert result.ok is True
    assert result.selected_candidate_ref == "CD1"
    assert result.selected_package_ref == "PK1"
    assert "submit_package" in result.tool_sequence


def test_pi_runner_fail_closed_via_runtime_invoker(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.subtitle.auto_fetch_case_agent.pi_runner._case_root",
        lambda: tmp_path,
    )
    provider = _FakeProvider({"Foo": []})

    def invoker(state: AutoFetchCaseToolState):
        state.handle_tool("search_candidates", {"keyword": "Foo"})
        state.handle_tool("fail_closed", {"reason": "no candidate matches arc"})
        return {"ok": True, "returncode": 0, "argv": ["fake"]}

    result = run_auto_fetch_case_agent_pi(
        workspace=_workspace(), provider=provider, task_data={"uuid": "t1"},
        runtime_invoker=invoker,
    )
    assert result.status == "fail_closed"
    assert result.final_action == "fail_closed"


def test_pi_runner_auto_fail_closed_when_no_final(tmp_path, monkeypatch):
    """runtime 结束无 final_result → 兜底 auto_fail_closed(budget_exhausted)。"""
    monkeypatch.setattr(
        "src.subtitle.auto_fetch_case_agent.pi_runner._case_root",
        lambda: tmp_path,
    )
    provider = _FakeProvider()

    def invoker(state: AutoFetchCaseToolState):
        # 只搜索不 submit → 无 final
        state.handle_tool("search_candidates", {"keyword": "Foo"})
        return {"ok": True, "returncode": 0, "argv": ["fake"]}

    result = run_auto_fetch_case_agent_pi(
        workspace=_workspace(), provider=provider, task_data={"uuid": "t1"},
        runtime_invoker=invoker,
    )
    assert result.status == "fail_closed"
    assert result.final_action == "fail_closed"


def test_pi_runner_fake_env_drives_tool_calls(tmp_path, monkeypatch):
    """BAR_PI_AUTO_FETCH_CASE_AGENT_FAKE_RESULT_JSON 驱动 tool_calls 序列。"""
    monkeypatch.setattr(
        "src.subtitle.auto_fetch_case_agent.pi_runner._case_root",
        lambda: tmp_path,
    )
    cand = _candidate(packages=[_make_package("p1", ["batch", "simplified"])])
    provider = _FakeProvider({"Foo": [cand]})
    fake_payload = {
        "tool_calls": [
            {"tool": "search_candidates", "arguments": {"keyword": "Foo"}},
            {"tool": "load_candidate_packages", "arguments": {"candidate_ref": "CD1"}},
            {"tool": "submit_package", "arguments": {"package_ref": "PK1", "reason": "main"}},
        ]
    }
    monkeypatch.setenv(
        "BAR_PI_AUTO_FETCH_CASE_AGENT_FAKE_RESULT_JSON", json.dumps(fake_payload)
    )
    result = run_auto_fetch_case_agent_pi(
        workspace=_workspace(), provider=provider, task_data={"uuid": "t1"},
    )
    assert result.status == "accepted"
    assert result.selected_package_ref == "PK1"
    monkeypatch.delenv("BAR_PI_AUTO_FETCH_CASE_AGENT_FAKE_RESULT_JSON", raising=False)


# ---------------------------------------------------------------------------
# entry pi 后端归一
# ---------------------------------------------------------------------------

def test_entry_pi_backend_accepted_returns_four_state(tmp_path, monkeypatch):
    from src.subtitle.auto_fetch_case_agent import run_auto_fetch_case_agent

    monkeypatch.setattr(
        "src.subtitle.auto_fetch_case_agent.pi_runner._case_root",
        lambda: tmp_path,
    )
    cand = _candidate(packages=[_make_package("p1", ["batch", "simplified"])])
    provider = _FakeProvider({"Foo": [cand]})

    def invoker(state):
        state.handle_tool("search_candidates", {"keyword": "Foo"})
        state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
        state.handle_tool("submit_package", {"package_ref": "PK1", "reason": "main"})
        return {"ok": True, "returncode": 0, "argv": ["fake"]}

    # 让 pi_runner 用 invoker：通过 monkeypatch run_auto_fetch_case_agent_pi
    import src.subtitle.auto_fetch_case_agent.local_auto_fetch_entry as entry_mod

    orig_pi = entry_mod.run_auto_fetch_case_agent_pi if hasattr(entry_mod, "run_auto_fetch_case_agent_pi") else None
    # local_auto_fetch_entry 延迟 import pi_runner，直接 patch pi_runner 模块
    import src.subtitle.auto_fetch_case_agent.pi_runner as pr_mod
    orig_run = pr_mod.run_auto_fetch_case_agent_pi

    def patched_run(*, workspace, provider, task_data, source_label="", runtime_invoker=None):
        return orig_run(
            workspace=workspace, provider=provider, task_data=task_data,
            source_label=source_label, runtime_invoker=invoker,
        )

    pr_mod.run_auto_fetch_case_agent_pi = patched_run
    try:
        result = run_auto_fetch_case_agent(
            workspace=_workspace(), candidates=[cand], task_data={"uuid": "t1"},
            ai_client=None, candidate_summaries=[{"index": 0, "title": "Foo 字幕"}],
            backend="pi", provider=provider,
        )
    finally:
        pr_mod.run_auto_fetch_case_agent_pi = orig_run

    assert result["status"] == "accepted"
    assert result["selected_candidate_ref"] == "CD1"
    assert result["selected_package_ref"] == "PK1"
    assert "pi_run" in result["snapshot"]
