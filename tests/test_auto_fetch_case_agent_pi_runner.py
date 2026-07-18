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
    build_missing_video_cards,
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
                preferred_language="zh-CN",
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
    assert data["missing_videos"][0]["preferred_language"] == "zh-CN"
    assert len(data["keywords"]) == 1


def test_evidence_broker_extracts_preferred_language_from_task_data():
    """evidence_broker 从 task_data 的 subtitle_auto_fetch_preferred_language 抽入
    MissingVideoCard.preferred_language（Pi 简繁抉择的数据来源）。"""
    task_data = {
        "uuid": "t1",
        "name": "Foo",
        "is_movie": False,
        "season_id": 1,
        "bgm_subject_name": "Foo",
        "subtitle_auto_fetch_preferred_language": "zh-TW",
    }
    record_data = {}
    missing = [Path("/lib/Foo/Season 01/Foo - S01E01 - Pilot.mkv")]
    cards = build_missing_video_cards(
        task_data=task_data, record_data=record_data, missing_videos=missing
    )
    assert len(cards) == 1
    assert cards[0].preferred_language == "zh-TW"


def test_evidence_broker_preferred_language_defaults_empty_when_unset():
    """task_data 无 subtitle_auto_fetch_preferred_language → preferred_language 空
    （Pi 据此不做语言 tie-break）。"""
    task_data = {"uuid": "t1", "name": "Foo", "is_movie": False, "season_id": 1}
    cards = build_missing_video_cards(
        task_data=task_data, record_data={}, missing_videos=[Path("/lib/Foo/ep.mkv")]
    )
    assert cards[0].preferred_language == ""


def test_evidence_broker_fills_per_video_bgm_subject_for_multi_season():
    """多季合集：bgm_video_subject_map + bgm_subjects → 每 card 带各自 subject。
    0091 鬼灭 4 subject（S01/S02/S03/剧场版），每 video card 填对应 subject_id +
    subject_name(日文) + subject_name_cn(中文)。Pi 据此按 subject 分组多帖多包。"""
    task_data = {
        "uuid": "t1", "name": "Demon Slayer", "is_movie": False, "season_id": 1,
        "bgm_subject_name": "鬼滅の刃", "bgm_subject_name_cn": "鬼灭之刃",
        "bgm_video_subject_map": {
            "Demon Slayer - S01E01.mkv": 245665,
            "Demon Slayer - S02E01.mkv": 350764,
            "Demon Slayer - S03E01.mkv": 328195,
            "Demon Slayer Movie.mkv": 291494,
        },
        "bgm_subjects": [
            {"id": 245665, "name": "鬼滅の刃", "name_cn": "鬼灭之刃", "media_kind": "tv"},
            {"id": 350764, "name": "鬼滅の刃 無限列車編", "name_cn": "鬼灭之刃 无限列车编", "media_kind": "tv"},
            {"id": 328195, "name": "鬼滅の刃 遊郭編", "name_cn": "鬼灭之刃 游郭编", "media_kind": "tv"},
            {"id": 291494, "name": "劇場版 鬼滅の刃 無限列車編", "name_cn": "剧场版 鬼灭之刃 无限列车编", "media_kind": "movie"},
        ],
    }
    missing = [
        Path("/lib/Demon Slayer - S01E01.mkv"),
        Path("/lib/Demon Slayer - S02E01.mkv"),
        Path("/lib/Demon Slayer - S03E01.mkv"),
        Path("/lib/Demon Slayer Movie.mkv"),
    ]
    cards = build_missing_video_cards(
        task_data=task_data, record_data={}, missing_videos=missing
    )
    by_video = {c.video: c for c in cards}
    assert by_video["Demon Slayer - S01E01.mkv"].bangumi_subject_id == 245665
    assert by_video["Demon Slayer - S01E01.mkv"].subject_name == "鬼滅の刃"
    assert by_video["Demon Slayer - S02E01.mkv"].bangumi_subject_id == 350764
    assert by_video["Demon Slayer - S02E01.mkv"].subject_name == "鬼滅の刃 無限列車編"
    assert by_video["Demon Slayer - S03E01.mkv"].subject_name_cn == "鬼灭之刃 游郭编"
    assert by_video["Demon Slayer Movie.mkv"].bangumi_subject_id == 291494
    # 主体单值字段仍保留（向后兼容）
    assert cards[0].bgm_subject_name == "鬼滅の刃"


def test_evidence_broker_no_subject_map_falls_back_to_task_single():
    """旧 task 无 bgm_video_subject_map → bangumi_subject_id=0 + subject_name 空，
    Pi 回退 task 级 bgm_subject_name 单值（向后兼容，不崩）。"""
    task_data = {"uuid": "t1", "name": "Foo", "is_movie": False, "season_id": 1,
                 "bgm_subject_name": "Foo", "bgm_subject_name_cn": "Foo中文"}
    cards = build_missing_video_cards(
        task_data=task_data, record_data={}, missing_videos=[Path("/lib/Foo - S01E01.mkv")]
    )
    assert cards[0].bangumi_subject_id == 0
    assert cards[0].subject_name == ""
    assert cards[0].bgm_subject_name == "Foo"  # 旧字段仍可用


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


def test_tool_search_candidates_batches_keywords_when_over_limit(tmp_path):
    """词数超过 _SEARCH_KEYWORD_BATCH_LIMIT(4)时只搜前 4 个，剩余在 remaining_keywords 告知 Pi。"""
    from src.subtitle.auto_fetch_case_agent.pi_tools import _SEARCH_KEYWORD_BATCH_LIMIT
    provider = _FakeProvider({f"kw{i}": [_candidate(title=f"c{i}")] for i in range(6)})
    state = AutoFetchCaseToolState(workspace=_workspace(), run_dir=tmp_path, provider=provider)
    result = state.handle_tool(
        "search_candidates", {"keywords": ["kw0", "kw1", "kw2", "kw3", "kw4", "kw5"]}
    )
    assert result["ok"] is True
    assert result["status"] == "candidates_loaded"
    assert len(result["keywords"]) == _SEARCH_KEYWORD_BATCH_LIMIT  # 只搜了前 4
    assert len(result["remaining_keywords"]) == 2  # kw4, kw5 未搜
    assert "next_action_hint" in result
    # 第二次搜剩余
    result2 = state.handle_tool("search_candidates", {"keywords": result["remaining_keywords"]})
    assert result2["ok"] is True
    assert len(result2["keywords"]) == 2
    assert "remaining_keywords" not in result2


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


def test_tool_load_candidate_packages_batches_when_over_limit(tmp_path):
    """候选数超过 _LOAD_CANDIDATE_BATCH_LIMIT(3)时只加载前 3 个，剩余在 remaining 告知 Pi。"""
    from src.subtitle.auto_fetch_case_agent.pi_tools import _LOAD_CANDIDATE_BATCH_LIMIT
    cands = [_candidate(title=f"c{i}", packages=[_make_package(f"p{i}", ["batch", "simplified"])]) for i in range(5)]
    provider = _FakeProvider({"Foo": cands})
    state = AutoFetchCaseToolState(workspace=_workspace(), run_dir=tmp_path, provider=provider)
    state.handle_tool("search_candidates", {"keyword": "Foo"})
    # 5 个候选 → load 5 个 refs，应只加载前 3
    result = state.handle_tool(
        "load_candidate_packages",
        {"candidate_refs": ["CD1", "CD2", "CD3", "CD4", "CD5"]},
    )
    assert result["ok"] is True
    assert result["status"] == "packages_loaded"
    assert len(result["candidate_refs"]) == _LOAD_CANDIDATE_BATCH_LIMIT  # 只加载前 3
    assert len(result["remaining_candidate_refs"]) == 2  # CD4, CD5 未加载
    assert "next_action_hint" in result
    # 第二次加载剩余
    result2 = state.handle_tool(
        "load_candidate_packages", {"candidate_refs": result["remaining_candidate_refs"]}
    )
    assert result2["ok"] is True
    assert len(result2["candidate_refs"]) == 2
    assert "remaining_candidate_refs" not in result2


def test_tool_load_candidate_packages_assigns_pk_refs_when_search_had_no_packages(tmp_path):
    """回归 bug：search 阶段候选不带 packages（acgrip search 只返帖子标题），
    load 阶段才填充包 → 包必须被分配新 PK<idx> ref（不能是空），否则 Pi 无法
    submit_package。"""
    class _LazyLoadProvider(_FakeProvider):
        def __init__(self):
            super().__init__({"Foo": [_candidate(packages=None)]})
            self._full_pkg = _make_package("p1", ["batch", "simplified"])

        def load_thread_packages(self, candidate):
            # load 阶段才填充包（模拟 acgrip search 返标题、load 返楼包）
            candidate.thread_packages = [self._full_pkg]
            return candidate

    provider = _LazyLoadProvider()
    state = AutoFetchCaseToolState(workspace=_workspace(), run_dir=tmp_path, provider=provider)
    # search：candidate 无 packages → add_candidate 不分配 PK（workspace CD1.packages 空）
    state.handle_tool("search_candidates", {"keyword": "Foo"})
    ws_cand = state.workspace.candidate_by_ref().get("CD1")
    assert ws_cand is not None
    assert ws_cand.packages == []
    # load：填充包 → 必须分配 PK1（修复前 ref 是空）
    result = state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
    assert result["ok"] is True
    ws_cand = state.workspace.candidate_by_ref().get("CD1")
    assert len(ws_cand.packages) == 1
    pkg_ref = ws_cand.packages[0].ref
    assert pkg_ref == "PK1", f"expected PK1, got {pkg_ref!r}"
    assert "PK1" in state.provider_packages_by_ref
    # per_candidate.package_refs 也应含 PK1
    assert result["per_candidate"][0]["package_refs"] == ["PK1"]


def test_readable_candidate_package_count_null_until_loaded(tmp_path):
    """回归 bug（机制根因）：search 阶段 candidate 的 package_count /
    has_downloadable_attachment 在 readable card 里必须是 null（未探测），
    而不是 0 / false——否则 Pi 会读成"确认无包"跳过 load 直接 fail_closed。
    load 后才渲染真值，且 packages_loaded 置 True。"""
    class _LazyLoadProvider(_FakeProvider):
        def __init__(self):
            super().__init__({"Foo": [_candidate(packages=None)]})
            self._full_pkg = _make_package("p1", ["batch", "simplified"])

        def load_thread_packages(self, candidate):
            candidate.thread_packages = [self._full_pkg]
            return candidate

    provider = _LazyLoadProvider()
    state = AutoFetchCaseToolState(workspace=_workspace(), run_dir=tmp_path, provider=provider)
    state.handle_tool("search_candidates", {"keyword": "Foo"})
    # search 后：readable card package_count / has_downloadable 必须是 None（未探测）
    cards = state.workspace.readable_candidate_cards()
    assert len(cards) == 1
    assert cards[0]["packages_loaded"] is False
    assert cards[0]["package_count"] is None
    assert cards[0]["has_downloadable_attachment"] is None
    # load 后：packages_loaded=True，package_count 渲染真值
    state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
    cards = state.workspace.readable_candidate_cards()
    assert cards[0]["packages_loaded"] is True
    assert cards[0]["package_count"] == 1
    assert cards[0]["has_downloadable_attachment"] is True


def test_submit_candidate_gate_rejects_unloaded_with_load_first_hint(tmp_path):
    """回归 bug：Pi 在 search 后未 load 就 submit_candidate（packages 仍空），
    gate 必须拒绝并给"load first"hint，而不是静默接受或模糊拒绝——
    防止 Pi 跳过 load 误判无包。"""
    class _LazyLoadProvider(_FakeProvider):
        def __init__(self):
            super().__init__({"Foo": [_candidate(packages=None)]})
            self._full_pkg = _make_package("p1", ["batch", "simplified"])

        def load_thread_packages(self, candidate):
            candidate.thread_packages = [self._full_pkg]
            return candidate

    provider = _LazyLoadProvider()
    state = AutoFetchCaseToolState(workspace=_workspace(), run_dir=tmp_path, provider=provider)
    state.handle_tool("search_candidates", {"keyword": "Foo"})
    # 未 load 直接 submit → gate 拒，issue 文案含 load 引导
    result = state.handle_tool(
        "submit_candidate", {"candidate_ref": "CD1", "reason": "arc match"}
    )
    assert result["ok"] is True
    assert result["accepted"] is False
    assert result["status"] == "invalid"
    hint_blob = json.dumps(result, ensure_ascii=False)
    assert "load_candidate_packages" in hint_blob
    # load 后再 submit → accept
    state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
    result2 = state.handle_tool(
        "submit_candidate", {"candidate_ref": "CD1", "reason": "arc match"}
    )
    assert result2["accepted"] is True


def test_submit_candidate_gate_loaded_empty_candidate_uses_plain_hint(tmp_path):
    """load 后确实无包（packages_loaded=True, packages 空）的候选被 submit 时，
    hint 用普通"pick another"文案，不含 load-first 误导。"""
    class _EmptyLoadProvider(_FakeProvider):
        def __init__(self):
            super().__init__({"Foo": [_candidate(packages=None)]})

        def load_thread_packages(self, candidate):
            candidate.thread_packages = []  # load 后仍无包
            return candidate

    provider = _EmptyLoadProvider()
    state = AutoFetchCaseToolState(workspace=_workspace(), run_dir=tmp_path, provider=provider)
    state.handle_tool("search_candidates", {"keyword": "Foo"})
    state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
    ws_cand = state.workspace.candidate_by_ref().get("CD1")
    assert ws_cand.packages_loaded is True
    assert ws_cand.packages == []
    result = state.handle_tool(
        "submit_candidate", {"candidate_ref": "CD1", "reason": "arc match"}
    )
    assert result["accepted"] is False
    hint_blob = json.dumps(result, ensure_ascii=False)
    assert "pick a candidate" in hint_blob
    # loaded 但空 → 不应再给 load-first 误导
    assert "load_candidate_packages to probe" not in hint_blob


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


def test_tool_submit_package_appends_selection_then_submit_complete(tmp_path):
    """多季覆盖：submit_package 不落 final，append 到 selections；submit_complete
    落 final 含 selections list。"""
    cand = _candidate(packages=[_make_package("p1", ["batch", "simplified"])])
    provider = _FakeProvider({"Foo": [cand]})
    state = AutoFetchCaseToolState(workspace=_workspace(), run_dir=tmp_path, provider=provider)
    state.handle_tool("search_candidates", {"keyword": "Foo"})
    state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
    result = state.handle_tool("submit_package", {"package_ref": "PK1", "reason": "main batch"})
    assert result["ok"] is True
    assert result["accepted"] is True
    assert result["status"] == "package_selected"  # 不再 accepted/落 final
    assert state.final_result is None  # submit_package 不落 final
    assert len(state.selections) == 1
    assert state.selections[0].package_ref == "PK1"
    assert state.selections[0].download_url == "https://x/p1.zip"
    # submit_complete 落 final
    complete = state.handle_tool("submit_complete", {"reason": "all covered"})
    assert complete["accepted"] is True
    assert complete["status"] == "accepted"
    assert state.final_result is not None
    assert state.final_result["final_action"] == "submit_complete"
    assert state.final_result["selections_count"] == 1
    assert state.final_result["selections"][0]["package_ref"] == "PK1"


def test_tool_submit_complete_rejects_no_selections(tmp_path):
    """submit_complete 时无 selection → invalid（应 fail_closed）。"""
    state = AutoFetchCaseToolState(workspace=_workspace(), run_dir=tmp_path, provider=_FakeProvider())
    result = state.handle_tool("submit_complete", {"reason": "nothing"})
    assert result["accepted"] is False
    assert result["status"] == "invalid"
    assert state.final_result is None


def test_tool_submit_complete_confirmation_on_uncovered_multi_subject(tmp_path):
    """多 subject 任务 Pi 只选 1 包就 submit_complete → 第一次不落 final，返回
    need_confirm 提示逼 Pi 确认 uncovered subject 搜过无帖；第二次 submit_complete
    才落 final。防 Pi 偷懒选 1 包就停（0042/0062 偶发波动）。

    用现有 _multi_subject_workspace（0091 鬼灭 3 subject：245665/350764/291494）。"""
    cand = _candidate(packages=[_make_package("p1", ["batch", "simplified"])])
    provider = _FakeProvider({"鬼滅の刃": [cand]})
    state = AutoFetchCaseToolState(
        workspace=_multi_subject_workspace(), run_dir=tmp_path, provider=provider
    )
    state.handle_tool("search_candidates", {"keyword": "鬼滅の刃"})
    state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
    # 只选 1 个包（覆盖 subject 245665 = S01）
    state.handle_tool(
        "submit_package",
        {"package_ref": "PK1", "reason": "S1 batch", "bangumi_subject_id": 245665},
    )
    assert len(state.selections) == 1

    # 第一次 submit_complete：还有 2 个 subject (350764/291494) uncovered → 不落 final
    first = state.handle_tool("submit_complete", {"reason": "done"})
    assert first["accepted"] is False
    assert first["status"] == "need_confirm"
    assert state.final_result is None  # 关键：没落 final
    assert "uncovered" in (first.get("summary") or "").lower()
    assert first["uncovered_subject_ids"] == [291494, 350764]
    assert state.submit_complete_confirmations == 1

    # 第二次 submit_complete：Pi 确认搜过无帖 → 落 final（uncovered 仍合格）
    second = state.handle_tool(
        "submit_complete", {"reason": "confirmed no thread for 350764/291494"}
    )
    assert second["accepted"] is True
    assert second["status"] == "accepted"
    assert state.final_result is not None
    assert state.final_result["selections_count"] == 1


def test_tool_submit_complete_force_skips_confirmation(tmp_path):
    """force=True 跳过确认（auto 兜底用：Pi 已结束，nudge 无意义，直接落 final）。"""
    cand = _candidate(packages=[_make_package("p1", ["batch", "simplified"])])
    provider = _FakeProvider({"鬼滅の刃": [cand]})
    state = AutoFetchCaseToolState(
        workspace=_multi_subject_workspace(), run_dir=tmp_path, provider=provider
    )
    state.handle_tool("search_candidates", {"keyword": "鬼滅の刃"})
    state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
    state.handle_tool(
        "submit_package",
        {"package_ref": "PK1", "reason": "S1 batch", "bangumi_subject_id": 245665},
    )
    # force=True 直接落 final，不走确认
    result = state.tool_submit_complete(reason="auto fallback", force=True)
    assert result["accepted"] is True
    assert result["status"] == "accepted"
    assert state.final_result is not None


def test_tool_submit_complete_single_subject_no_confirmation(tmp_path):
    """单 subject 任务不受确认机制影响（total_subjects=1，直接落 final）。"""
    cand = _candidate(packages=[_make_package("p1", ["batch", "simplified"])])
    provider = _FakeProvider({"Foo": [cand]})
    state = AutoFetchCaseToolState(
        workspace=_workspace(), run_dir=tmp_path, provider=provider
    )
    state.handle_tool("search_candidates", {"keyword": "Foo"})
    state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
    state.handle_tool("submit_package", {"package_ref": "PK1", "reason": "main batch"})
    result = state.handle_tool("submit_complete", {"reason": "all covered"})
    assert result["accepted"] is True
    assert result["status"] == "accepted"
    assert state.final_result is not None


def test_tool_submit_package_link_url_pins_attachment(tmp_path):
    """link_url 指定具体附件（AI-first 附件选择）：包内多附件时 Pi 按 link
    label/filename 选具体附件，selection.download_url 用 Pi 指定的 url，而非
    固定层打分选第一个。修复大和号 2205 前篇+後篇同楼两附件只下前篇的问题。
    """
    # 构造一个楼包含前篇 + 後篇两个 attachment（同楼分两个 zip）
    pkg = SubtitleThreadPackage(
        package_id="p1", page_number=1, floor_label="楼主",
        post_author="a", post_time="t", post_text="字幕", context_text="",
        links=[
            SubtitleThreadPackageLink(
                url="https://x/zenpen_01-04.zip", kind="attachment",
                label="前篇 [01-04].zip", filename_hint="前篇 [01-04].zip",
                is_direct_download=True,
            ),
            SubtitleThreadPackageLink(
                url="https://x/kouhen_05-08.7z", kind="attachment",
                label="後篇 [05-08].7z", filename_hint="後篇 [05-08].7z",
                is_direct_download=True,
            ),
        ],
        has_direct_download=True, package_flags=["batch"],
    )
    cand = _candidate(packages=[pkg])
    provider = _FakeProvider({"Foo": [cand]})
    state = AutoFetchCaseToolState(workspace=_workspace(), run_dir=tmp_path, provider=provider)
    state.handle_tool("search_candidates", {"keyword": "Foo"})
    state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
    # Pi 指定後篇附件 url
    result = state.handle_tool(
        "submit_package",
        {"package_ref": "PK1", "reason": "後篇 05-08", "link_url": "https://x/kouhen_05-08.7z"},
    )
    assert result["ok"] is True
    assert result["accepted"] is True
    assert state.selections[0].download_url == "https://x/kouhen_05-08.7z"


def test_tool_submit_package_link_url_rejected_when_not_package_link(tmp_path):
    """link_url 必须是包内可下载 link，防 Pi 编造 url。"""
    pkg = _make_package("p1", ["batch", "simplified"])
    cand = _candidate(packages=[pkg])
    provider = _FakeProvider({"Foo": [cand]})
    state = AutoFetchCaseToolState(workspace=_workspace(), run_dir=tmp_path, provider=provider)
    state.handle_tool("search_candidates", {"keyword": "Foo"})
    state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
    result = state.handle_tool(
        "submit_package",
        {"package_ref": "PK1", "reason": "x", "link_url": "https://evil/fake.zip"},
    )
    assert result["ok"] is False
    assert result["status"] == "invalid"
    assert len(state.selections) == 0


def test_tool_submit_package_no_link_url_uses_first_attachment(tmp_path):
    """link_url 省略时回退取第一个可下载附件（兼容单附件包 + 旧调用）。"""
    pkg = _make_package("p1", ["batch", "simplified"])
    cand = _candidate(packages=[pkg])
    provider = _FakeProvider({"Foo": [cand]})
    state = AutoFetchCaseToolState(workspace=_workspace(), run_dir=tmp_path, provider=provider)
    state.handle_tool("search_candidates", {"keyword": "Foo"})
    state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
    result = state.handle_tool("submit_package", {"package_ref": "PK1", "reason": "main"})
    assert result["ok"] is True
    assert state.selections[0].download_url == "https://x/p1.zip"


def test_tool_submit_package_bangumi_subject_id_overrides_candidate(tmp_path):
    """B9：submit_package bangumi_subject_id 显式声明覆盖 candidate 覆盖态。

    修复 0002 subject 归属错乱：Pi 对同 CD 多次 submit_candidate 声明不同 subject
    时，candidate.bangumi_subject_id 被后声明覆盖，selection subject 错乱（前篇/
    後篇都被记 319390）。Pi 在 submit_package 显式传 bangumi_subject_id 修正。
    """
    pkg = _make_package("p1", ["batch", "simplified"])
    cand = _candidate(packages=[pkg])
    provider = _FakeProvider({"Foo": [cand]})
    state = AutoFetchCaseToolState(workspace=_workspace(), run_dir=tmp_path, provider=provider)
    state.handle_tool("search_candidates", {"keyword": "Foo"})
    state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
    # 先 submit_candidate 声明 319390（设 candidate.bangumi_subject_id=319390）
    state.handle_tool(
        "submit_candidate",
        {"candidate_ref": "CD1", "language": "zh-CN", "bangumi_subject_id": 319390},
    )
    # submit_package 显式声明 352905（後篇 → 後章 subject）
    result = state.handle_tool(
        "submit_package",
        {"package_ref": "PK1", "reason": "後篇 05-08", "bangumi_subject_id": 352905},
    )
    assert result["ok"] is True
    # selection subject 用 Pi 显式声明的 352905，而非 candidate 的 319390
    assert state.selections[0].bangumi_subject_id == 352905


def test_tool_submit_package_subject_falls_back_to_candidate_when_unspecified(tmp_path):
    """B9：Pi 未传 bangumi_subject_id 时回退 candidate 值（兼容）。"""
    pkg = _make_package("p1", ["batch", "simplified"])
    cand = _candidate(packages=[pkg])
    provider = _FakeProvider({"Foo": [cand]})
    state = AutoFetchCaseToolState(workspace=_workspace(), run_dir=tmp_path, provider=provider)
    state.handle_tool("search_candidates", {"keyword": "Foo"})
    state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
    state.handle_tool(
        "submit_candidate",
        {"candidate_ref": "CD1", "language": "zh-CN", "bangumi_subject_id": 319390},
    )
    result = state.handle_tool("submit_package", {"package_ref": "PK1", "reason": "main"})
    assert result["ok"] is True
    # 未传 → 用 candidate 的 319390
    assert state.selections[0].bangumi_subject_id == 319390


def test_tool_submit_package_gate_no_longer_rejects_font_only(tmp_path):
    """A2：font-gate 已删。font flag 包只要 has_downloadable_link 就放行，
    包性质由 Pi 自判（SKILL 教）。固定层不再拦 font-only。
    """
    cand = _candidate(packages=[_make_package("font", ["font"])])
    provider = _FakeProvider({"Foo": [cand]})
    state = AutoFetchCaseToolState(workspace=_workspace(), run_dir=tmp_path, provider=provider)
    state.handle_tool("search_candidates", {"keyword": "Foo"})
    state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
    result = state.handle_tool("submit_package", {"package_ref": "PK1"})
    # font_only 但可下载 → gate 放行（不再 invalid）
    assert result["accepted"] is True
    assert result["status"] == "package_selected"
    assert state.final_result is None  # submit_package 不落 final


def test_tool_inspect_package_returns_details(tmp_path):
    cand = _candidate(packages=[_make_package("p1", ["batch", "simplified"])])
    provider = _FakeProvider({"Foo": [cand]})
    state = AutoFetchCaseToolState(workspace=_workspace(), run_dir=tmp_path, provider=provider)
    state.handle_tool("search_candidates", {"keyword": "Foo"})
    state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
    result = state.handle_tool("inspect_package", {"package_ref": "PK1"})
    assert result["ok"] is True
    # is_font_or_patch_only 已删，inspect_package 不再暴露该字段
    assert "is_font_or_patch_only" not in result["package"]
    # links 完整暴露供 Pi 选附件
    assert "links" in result["package"]


def test_readable_missing_video_cards_expose_per_video_subject_grouping():
    """多季覆盖：readable missing video card 暴露 per-video subject 分组信息，
    Pi 据此按 subject 分组多帖多包。0091 鬼灭 4 subject 场景。"""
    ws = build_auto_fetch_case_workspace(
        task_uuid="t1",
        scan_scope=ScanScopeCard(scope_type="series", root="/lib/Demon Slayer", source="task_data"),
        missing_videos=[
            MissingVideoCard(
                task_uuid="t1", video="Demon Slayer - S01E01.mkv",
                target_path="/lib/Demon Slayer/Season 01/Demon Slayer - S01E01.mkv",
                source_video="[VCB] Kimetsu 01.mkv", task_title="Demon Slayer",
                season=1, is_movie=False, bgm_subject_name="鬼滅の刃",
                bgm_subject_name_cn="鬼灭之刃",
                bangumi_subject_id=245665, subject_name="鬼滅の刃", subject_name_cn="鬼灭之刃",
                preferred_language="zh-CN",
            ),
            MissingVideoCard(
                task_uuid="t1", video="Demon Slayer - S02E01.mkv",
                target_path="/lib/Demon Slayer/Season 02/Demon Slayer - S02E01.mkv",
                source_video="[VCB] Kimetsu Mugen 01.mkv", task_title="Demon Slayer",
                season=2, is_movie=False, bgm_subject_name="鬼滅の刃",
                bgm_subject_name_cn="鬼灭之刃",
                bangumi_subject_id=350764, subject_name="鬼滅の刃 無限列車編",
                subject_name_cn="鬼灭之刃 无限列车编", preferred_language="zh-CN",
            ),
            MissingVideoCard(
                task_uuid="t1", video="Demon Slayer Movie.mkv",
                target_path="/lib/Demon Slayer Movie/Demon Slayer Movie.mkv",
                source_video="[VCB] Gekijouban.mkv", task_title="Demon Slayer",
                season=None, is_movie=True, bgm_subject_name="鬼滅の刃",
                bgm_subject_name_cn="鬼灭之刃",
                bangumi_subject_id=291494, subject_name="劇場版 鬼滅の刃 無限列車編",
                subject_name_cn="剧场版 鬼灭之刃 无限列车编", preferred_language="zh-CN",
            ),
        ],
        keywords=[SearchKeywordCard(keyword="鬼滅の刃")],
    )
    cards = ws.readable_missing_video_cards()
    by_video = {c["video"]: c for c in cards}
    # S01 card 带 subject 245665
    s01 = by_video["Demon Slayer - S01E01.mkv"]
    assert s01["bangumi_subject_id"] == 245665
    assert s01["subject_name"] == "鬼滅の刃"
    # S02 card 带 subject 350764（不同于 S01）
    s02 = by_video["Demon Slayer - S02E01.mkv"]
    assert s02["bangumi_subject_id"] == 350764
    assert s02["subject_name"] == "鬼滅の刃 無限列車編"
    # movie card 带 subject 291494
    mov = by_video["Demon Slayer Movie.mkv"]
    assert mov["bangumi_subject_id"] == 291494
    # 3 个 card 分属 3 个不同 subject —— Pi 能看到 subject 分组
    subj_ids = {c["bangumi_subject_id"] for c in cards}
    assert subj_ids == {245665, 350764, 291494}


def test_readable_packages_compact_long_post_text_and_links(tmp_path):
    """readable card 压缩长 post_text/context_text + links（参考 rename _compact_text），
    避免大包 context 撑爆 Pi；inspect_package 保留全文。"""
    long_text = "楼层正文" + "详情" * 200  # 远超 200 字符
    pkg = SubtitleThreadPackage(
        package_id="p1", page_number=1, floor_label="p1-floor",
        post_author="author", post_time="2023-01-01 00:00:00",
        post_text=long_text, context_text=long_text,
        links=[
            SubtitleThreadPackageLink(
                url=f"https://x/{i}.zip", kind="attachment",
                label=f"{i}.zip", filename_hint=f"{i}.zip", is_direct_download=True,
            )
            for i in range(5)
        ],
        has_direct_download=True, package_flags=["batch", "simplified"],
    )
    cand = _candidate(packages=[pkg])
    provider = _FakeProvider({"Foo": [cand]})
    state = AutoFetchCaseToolState(workspace=_workspace(), run_dir=tmp_path, provider=provider)
    state.handle_tool("search_candidates", {"keyword": "Foo"})
    load_result = state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
    # readable packages（load 返回的 packages 字段）：post_text 截断、links 只 2 个、link_count=5
    readable_pkg = load_result["packages"][0]["packages"][0]
    assert len(readable_pkg["post_text"]) <= 203  # 200 + '...'
    assert readable_pkg["post_text"].endswith("...")
    assert readable_pkg["link_count"] == 5
    assert len(readable_pkg["links"]) == 2
    # inspect_package 保留全文 + 完整 links
    inspect_result = state.handle_tool("inspect_package", {"package_ref": "PK1"})
    full_pkg = inspect_result["package"]
    assert full_pkg["post_text"] == long_text  # 全文未截断
    assert len(full_pkg["links"]) == 5  # 完整链接


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
            backend="pi", provider=provider,
        )
    finally:
        pr_mod.run_auto_fetch_case_agent_pi = orig_run

    assert result["status"] == "accepted"
    assert result["selected_candidate_ref"] == "CD1"
    assert result["selected_package_ref"] == "PK1"
    assert "pi_run" in result["snapshot"]


# ---------------------------------------------------------------------------
# /state 端点 + 多季覆盖：暴露 selections/covered/uncovered subject 供 sidecar nudge
# ---------------------------------------------------------------------------

def _multi_subject_workspace() -> Any:
    """0091 鬼灭 3-subject 场景：S01 / S02 / Movie 分属不同 subject。"""
    return build_auto_fetch_case_workspace(
        task_uuid="t1",
        scan_scope=ScanScopeCard(scope_type="series", root="/lib/Demon Slayer", source="task_data"),
        missing_videos=[
            MissingVideoCard(
                task_uuid="t1", video="Demon Slayer - S01E01.mkv",
                target_path="/lib/Demon Slayer/Season 01/Demon Slayer - S01E01.mkv",
                source_video="[VCB] Kimetsu 01.mkv", task_title="Demon Slayer",
                season=1, is_movie=False,
                bangumi_subject_id=245665, subject_name="鬼滅の刃", subject_name_cn="鬼灭之刃",
                preferred_language="zh-CN",
            ),
            MissingVideoCard(
                task_uuid="t1", video="Demon Slayer - S02E01.mkv",
                target_path="/lib/Demon Slayer/Season 02/Demon Slayer - S02E01.mkv",
                source_video="[VCB] Kimetsu Mugen 01.mkv", task_title="Demon Slayer",
                season=2, is_movie=False,
                bangumi_subject_id=350764, subject_name="鬼滅の刃 無限列車編",
                subject_name_cn="鬼灭之刃 无限列车编", preferred_language="zh-CN",
            ),
            MissingVideoCard(
                task_uuid="t1", video="Demon Slayer Movie.mkv",
                target_path="/lib/Demon Slayer Movie/Demon Slayer Movie.mkv",
                source_video="[VCB] Gekijouban.mkv", task_title="Demon Slayer",
                season=None, is_movie=True,
                bangumi_subject_id=291494, subject_name="劇場版 鬼滅の刃 無限列車編",
                subject_name_cn="剧场版 鬼灭之刃 无限列车编", preferred_language="zh-CN",
            ),
        ],
        keywords=[SearchKeywordCard(keyword="鬼滅の刃")],
    )


def test_state_endpoint_reports_zero_coverage_initially(tmp_path):
    """/state 在没有任何 selection 时报告全部 subject 未覆盖。"""
    import urllib.request

    from src.subtitle.auto_fetch_case_agent.pi_runner import _running_tool_server

    state = AutoFetchCaseToolState(
        workspace=_multi_subject_workspace(), run_dir=tmp_path, provider=_FakeProvider()
    )
    with _running_tool_server(state, token="tok") as base:
        resp = urllib.request.urlopen(f"{base}/state").read().decode("utf-8")
        snap = json.loads(resp)
        assert snap["ok"] is True
        assert snap["selections_count"] == 0
        assert snap["final_result_present"] is False
        assert snap["total_subject_count"] == 3
        assert sorted(snap["covered_subject_ids"]) == []
        # 3 个 subject 全未覆盖
        assert sorted(snap["uncovered_subject_ids"]) == [245665, 291494, 350764]
        # per-subject video 计数（每个 subject 1 个 video；JSON 序列化后 key 为 str）
        assert snap["per_subject_video_count"] == {"245665": 1, "350764": 1, "291494": 1}
        assert snap["missing_video_count"] == 3


def test_state_endpoint_reports_partial_coverage_after_one_submit(tmp_path):
    """Pi 只 submit_package 了 S01 一个包后停：/state 报告 S02+Movie 仍未覆盖，
    sidecar nudge 据此提醒"不要只覆盖一季就停"。"""
    import urllib.request

    from src.subtitle.auto_fetch_case_agent.pi_runner import _running_tool_server

    provider = _FakeProvider({"鬼滅の刃": [_candidate(packages=[_make_package("p1", ["batch", "simplified"])])]})
    state = AutoFetchCaseToolState(
        workspace=_multi_subject_workspace(), run_dir=tmp_path, provider=provider
    )
    # 模拟 Pi：search → load → submit_candidate(S01 subject 245665) → submit_package
    state.handle_tool("search_candidates", {"keyword": "鬼滅の刃"})
    state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
    state.handle_tool("submit_candidate", {"candidate_ref": "CD1", "bangumi_subject_id": 245665})
    state.handle_tool("submit_package", {"package_ref": "PK1", "reason": "S01 batch"})
    # 此刻 Pi 卡住没继续——final 仍未落
    assert state.final_result is None

    with _running_tool_server(state, token="tok") as base:
        snap = json.loads(urllib.request.urlopen(f"{base}/state").read().decode("utf-8"))
        assert snap["selections_count"] == 1
        assert snap["final_result_present"] is False
        assert sorted(snap["covered_subject_ids"]) == [245665]
        # S02 + Movie 仍未覆盖——这就是 nudge 要提醒的
        assert sorted(snap["uncovered_subject_ids"]) == [291494, 350764]
        assert snap["total_subject_count"] == 3


def test_state_endpoint_reports_full_coverage_after_submit_complete(tmp_path):
    """所有 subject 都 submit_package 后 submit_complete：/state 全覆盖 + final 落。"""
    from src.subtitle.auto_fetch_case_agent.pi_runner import _running_tool_server
    import urllib.request

    provider = _FakeProvider({"鬼滅の刃": [_candidate(packages=[_make_package("p1", ["batch", "simplified"])])]})
    state = AutoFetchCaseToolState(
        workspace=_multi_subject_workspace(), run_dir=tmp_path, provider=provider
    )
    # 3 个 subject 各 submit 一包（复用同一帖同包，测试只验状态会计）
    for sid in (245665, 350764, 291494):
        state.handle_tool("search_candidates", {"keyword": "鬼滅の刃"})
        state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
        state.handle_tool("submit_candidate", {"candidate_ref": "CD1", "bangumi_subject_id": sid})
        state.handle_tool("submit_package", {"package_ref": "PK1", "reason": f"subject {sid}"})
    state.handle_tool("submit_complete", {"reason": "all 3 subjects covered"})

    with _running_tool_server(state, token="tok") as base:
        snap = json.loads(urllib.request.urlopen(f"{base}/state").read().decode("utf-8"))
        assert snap["selections_count"] == 3
        assert snap["final_result_present"] is True
        assert sorted(snap["covered_subject_ids"]) == [245665, 291494, 350764]
        assert snap["uncovered_subject_ids"] == []
