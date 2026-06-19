"""auto_fetch Case Agent 入口 + fail_closed 解读对齐测试（Phase 2）。

验证：
- ``run_auto_fetch_case_agent`` single_shot 后端四态（accepted/fail_closed/
  need_confirm/invalid）
- auto_fetch.py 薄入口分发：Case Agent 主路径 accepted 走下载+processor；
  fail_closed（候选/包被拒）映射旧 reason；AI 无选择时回退 legacy 规则兜底
- **fail_closed 解读对齐**：processor 落盘产 fail_closed（对外 need_confirm +
  case_agent_status 审计）→ auto_fetch 视为可重试合格结果，透传 case_agent_status
- source_video 证据口径统一（record key -> MissingVideoCard.source_video）
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.subtitle.auto_fetch import SubtitleAutoFetcher
from src.subtitle.auto_fetch_case_agent import (
    AutoFetchCaseWorkspace,
    MissingVideoCard,
    ScanScopeCard,
    SearchKeywordCard,
    build_auto_fetch_case_workspace,
    run_auto_fetch_case_agent,
)
from src.subtitle.providers.base import (
    SubtitleCandidate,
    SubtitleThreadPackage,
    SubtitleThreadPackageLink,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _ai_choice(*, should_use=True, selected_index=0, reason="ok", language="简体中文"):
    return SimpleNamespace(
        selected_index=selected_index,
        should_use=should_use,
        confidence="High",
        language_assessment=language,
        reason=reason,
        warnings=[],
        model_dump=lambda: {
            "selected_index": selected_index,
            "should_use": should_use,
            "confidence": "High",
            "language_assessment": language,
            "reason": reason,
            "warnings": [],
        },
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


def _make_candidate(title, *, packages=None, attachment_urls=None):
    c = SubtitleCandidate(
        title=title,
        detail_url=f"https://bbs.acgrip.com/{title}",
        source="acgrip",
        attachment_urls=attachment_urls or [],
    )
    if packages is not None:
        c.thread_packages = packages
    return c


def _build_workspace():
    return build_auto_fetch_case_workspace(
        task_uuid="t1",
        scan_scope=ScanScopeCard(scope_type="task", root="", source=""),
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


class _FakeAI:
    def __init__(self, candidate_choice, package_choice, available=True):
        self._cand = candidate_choice
        self._pkg = package_choice
        self._available = available

    def is_available(self):
        return self._available

    def choose_subtitle_candidate(self, task_data, ranked_candidates):
        return self._cand

    def choose_subtitle_thread_package(self, task_data, candidate_data, package_summaries):
        return self._pkg


# ---------------------------------------------------------------------------
# run_auto_fetch_case_agent 四态
# ---------------------------------------------------------------------------

def test_entry_accepted_returns_selected_candidate_and_package():
    ws = _build_workspace()
    cand = _make_candidate("Foo 字幕", packages=[_make_package("p1", ["batch", "simplified"])])
    ai = _FakeAI(_ai_choice(should_use=True), _ai_choice(should_use=True))
    result = run_auto_fetch_case_agent(
        workspace=ws, candidates=[cand], task_data={"uuid": "t1"},
        ai_client=ai, candidate_summaries=[{"index": 0, "title": "Foo 字幕"}],
    )
    assert result["status"] == "accepted"
    assert result["selected_candidate_ref"] == "CD1"
    assert result["selected_package_ref"] == "PK1"
    assert result["selected_candidate"]["title"] == "Foo 字幕"


def test_entry_fail_closed_when_candidate_ai_rejects():
    ws = _build_workspace()
    cand = _make_candidate("Wrong Arc", packages=[_make_package("p1", ["batch"])])
    ai = _FakeAI(_ai_choice(should_use=False, reason="wrong arc"), None)
    result = run_auto_fetch_case_agent(
        workspace=ws, candidates=[cand], task_data={"uuid": "t1"},
        ai_client=ai, candidate_summaries=[{"index": 0, "title": "Wrong Arc"}],
    )
    assert result["status"] == "fail_closed"
    assert result["reason_kind"] == "candidate_ai_rejected"
    assert result["ai_rerank_result"]["should_use"] is False


def test_entry_fail_closed_when_package_ai_rejects_carries_candidate_ref():
    ws = _build_workspace()
    cand = _make_candidate("Foo 字幕", packages=[_make_package("p1", ["special"])])
    ai = _FakeAI(_ai_choice(should_use=True), _ai_choice(should_use=False, reason="special-only"))
    result = run_auto_fetch_case_agent(
        workspace=ws, candidates=[cand], task_data={"uuid": "t1"},
        ai_client=ai, candidate_summaries=[{"index": 0, "title": "Foo 字幕"}],
    )
    assert result["status"] == "fail_closed"
    assert result["reason_kind"] == "package_ai_rejected"
    # 候选已被接受，ref 透传供调用方恢复 selected_candidate
    assert result["selected_candidate_ref"] == "CD1"
    assert result["package_ai_result"]["should_use"] is False


def test_entry_invalid_when_ai_unavailable():
    ws = _build_workspace()
    cand = _make_candidate("Foo 字幕", packages=[_make_package("p1", ["batch"])])
    ai = _FakeAI(None, None, available=False)
    result = run_auto_fetch_case_agent(
        workspace=ws, candidates=[cand], task_data={"uuid": "t1"},
        ai_client=ai, candidate_summaries=[{"index": 0, "title": "Foo 字幕"}],
    )
    assert result["status"] == "invalid"


def test_entry_fail_closed_when_no_candidates():
    ws = _build_workspace()
    ai = _FakeAI(None, None)
    result = run_auto_fetch_case_agent(
        workspace=ws, candidates=[], task_data={"uuid": "t1"},
        ai_client=ai, candidate_summaries=[],
    )
    assert result["status"] == "fail_closed"
    assert result["reason_kind"] == "no_candidates"


# ---------------------------------------------------------------------------
# auto_fetch.py 薄入口分发 + case_agent_status 审计透传
# ---------------------------------------------------------------------------

def _build_fetcher(monkeypatch, tmp_path):
    fetcher = SubtitleAutoFetcher()
    monkeypatch.setattr(
        "src.subtitle.auto_fetch.get_task",
        lambda uuid: {
            "uuid": uuid,
            "name": "Foo",
            "tmdb_name": "Foo",
            "season_id": 1,
            "is_movie": False,
            "target_root": str(tmp_path / "Series"),
        },
    )
    monkeypatch.setattr(
        "src.subtitle.auto_fetch.get_record",
        lambda uuid: {
            "/dl/[SubGroup] Foo 01.mkv": str(
                tmp_path / "Series" / "Season 1" / "Foo - S01E01 - Pilot.mkv"
            )
        },
    )
    season_dir = tmp_path / "Series" / "Season 1"
    season_dir.mkdir(parents=True, exist_ok=True)
    (season_dir / "Foo - S01E01 - Pilot.mkv").write_text("video", encoding="utf-8")
    return fetcher


def _force_case_agent_enabled(monkeypatch, enabled=True):
    """强制 case_agent 主路径启用（config 默认即 True，显式钉死避免环境漂移）。"""
    import src.subtitle.auto_fetch as af_mod

    orig_cm_get = af_mod.cm_get

    def patched(key, default=None):
        if key == "subtitle_auto_fetch_case_agent_primary_enabled":
            return enabled
        if key == "subtitle_auto_fetch_case_agent_backend":
            return "single_shot"
        return orig_cm_get(key, default)

    monkeypatch.setattr(af_mod, "cm_get", patched)


def test_process_case_agent_accepted_lands_and_persists_pipeline_mode(
    monkeypatch, tmp_path
):
    fetcher = _build_fetcher(monkeypatch, tmp_path)
    _force_case_agent_enabled(monkeypatch, enabled=True)
    cand = _make_candidate(
        "Foo 字幕", packages=[_make_package("p1", ["batch", "simplified"])]
    )
    monkeypatch.setattr(fetcher.provider, "search", lambda keyword, limit=10: [cand])
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda c: c)
    monkeypatch.setattr(fetcher.provider, "load_thread_packages", lambda c: c)
    monkeypatch.setattr(
        fetcher.ai_client,
        "choose_subtitle_candidate",
        lambda td, rc: _ai_choice(should_use=True, reason="ok"),
    )
    monkeypatch.setattr(
        fetcher.ai_client,
        "choose_subtitle_thread_package",
        lambda td, cd, ps: _ai_choice(should_use=True, reason="pick"),
    )
    downloaded = tmp_path / "got.zip"
    downloaded.write_text("subtitle", encoding="utf-8")
    monkeypatch.setattr(
        fetcher.provider,
        "download",
        lambda c, dd, package=None: SimpleNamespace(
            status="success", downloaded_path=downloaded,
            download_url="https://x/got.zip", selected_package=package,
        ),
    )
    monkeypatch.setattr(
        fetcher.processor, "process",
        lambda path, target_task_uuid=None: {"status": "success"},
    )

    result = fetcher.process_task("task-1")
    assert result["status"] == "success"
    assert result["pipeline_mode"] == "auto_fetch_case_agent_primary"
    assert result["case_agent_status"] == "accepted"


def test_process_case_agent_candidate_rejected_skips_with_audit(
    monkeypatch, tmp_path
):
    fetcher = _build_fetcher(monkeypatch, tmp_path)
    _force_case_agent_enabled(monkeypatch, enabled=True)
    cand = _make_candidate("Wrong Arc", packages=[_make_package("p1", ["batch"])])
    monkeypatch.setattr(fetcher.provider, "search", lambda keyword, limit=10: [cand])
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda c: c)
    monkeypatch.setattr(fetcher.provider, "load_thread_packages", lambda c: c)
    monkeypatch.setattr(
        fetcher.ai_client,
        "choose_subtitle_candidate",
        lambda td, rc: _ai_choice(should_use=False, reason="wrong arc"),
    )
    monkeypatch.setattr(
        fetcher.ai_client,
        "choose_subtitle_thread_package",
        lambda *a, **k: _ai_choice(should_use=True),
    )
    monkeypatch.setattr(
        fetcher.provider, "download",
        lambda *a, **k: pytest.fail("download should not be called"),
    )
    monkeypatch.setattr(
        fetcher.processor, "process",
        lambda *a, **k: pytest.fail("processor should not be called"),
    )

    result = fetcher.process_task("task-1")
    assert result["status"] == "skipped"
    assert result["reason"] == "candidate_ai_rejected"
    assert result["pipeline_mode"] == "auto_fetch_case_agent_primary"
    assert result["case_agent_status"] == "fail_closed"


def test_process_case_agent_falls_back_legacy_when_ai_no_choice(
    monkeypatch, tmp_path
):
    """AI 无选择（返回 None，非显式拒绝）→ single_shot 兼容兜底回退旧规则选帖/选包。"""
    fetcher = _build_fetcher(monkeypatch, tmp_path)
    _force_case_agent_enabled(monkeypatch, enabled=True)
    # 两个包：font-only 与 batch；旧 _pick_best_package_by_rules 应选 batch
    cand = _make_candidate(
        "Foo 字幕",
        packages=[
            _make_package("font", ["font"]),
            _make_package("batch", ["batch", "simplified"]),
        ],
    )
    monkeypatch.setattr(fetcher.provider, "search", lambda keyword, limit=10: [cand])
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda c: c)
    monkeypatch.setattr(fetcher.provider, "load_thread_packages", lambda c: c)
    # AI 候选返回 None（不可用/无选择）→ 回退旧链路 candidates[0]
    monkeypatch.setattr(fetcher.ai_client, "choose_subtitle_candidate", lambda *a, **k: None)
    monkeypatch.setattr(fetcher.ai_client, "choose_subtitle_thread_package", lambda *a, **k: None)
    downloaded = tmp_path / "got.zip"
    downloaded.write_text("subtitle", encoding="utf-8")
    selected_pkg = {}

    def fake_download(c, dd, package=None):
        selected_pkg["id"] = package.package_id if package else None
        return SimpleNamespace(
            status="success", downloaded_path=downloaded,
            download_url="https://x/got.zip", selected_package=package,
        )

    monkeypatch.setattr(fetcher.provider, "download", fake_download)
    monkeypatch.setattr(
        fetcher.processor, "process",
        lambda path, target_task_uuid=None: {"status": "success"},
    )

    result = fetcher.process_task("task-1")
    assert result["status"] == "success"
    # 旧规则兜底选 batch 包（font-only 被规则排除）
    assert selected_pkg["id"] == "batch"
    assert result["pipeline_mode"] == "auto_fetch_legacy_compat"


# ---------------------------------------------------------------------------
# fail_closed 解读对齐：processor fail_closed → 可重试合格结果 + 审计透传
# ---------------------------------------------------------------------------

def test_process_retries_when_processor_returns_fail_closed_with_audit(
    monkeypatch, tmp_path
):
    """processor 落盘产 fail_closed（对外 need_confirm + case_agent_status）→
    auto_fetch 视为该包未配对成功的合格可重试结果，换关键词重试，
    透传 processor_case_agent_status / failure_reason 审计。"""
    fetcher = _build_fetcher(monkeypatch, tmp_path)
    _force_case_agent_enabled(monkeypatch, enabled=True)

    # 两个关键词：第一个关键词的包 processor fail_closed；第二个成功
    cand_wrong = _make_candidate(
        "Foo 字幕 v1", packages=[_make_package("p1", ["batch", "simplified"])]
    )
    cand_correct = _make_candidate(
        "Foo 字幕 v2", packages=[_make_package("p2", ["batch", "simplified"])]
    )

    def fake_search(keyword, limit=10):
        if keyword == "Foo":
            return [cand_wrong]
        if keyword == "Foo v2":
            return [cand_correct]
        return []

    monkeypatch.setattr(fetcher.provider, "search", fake_search)
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda c: c)
    monkeypatch.setattr(fetcher.provider, "load_thread_packages", lambda c: c)
    monkeypatch.setattr(
        fetcher.ai_client, "choose_subtitle_candidate",
        lambda td, rc: _ai_choice(should_use=True, reason="ok"),
    )
    monkeypatch.setattr(
        fetcher.ai_client, "choose_subtitle_thread_package",
        lambda td, cd, ps: _ai_choice(should_use=True, reason="pick"),
    )

    downloaded_wrong = tmp_path / "wrong.zip"
    downloaded_wrong.write_text("s", encoding="utf-8")
    downloaded_correct = tmp_path / "correct.zip"
    downloaded_correct.write_text("s", encoding="utf-8")
    processor_calls = []

    def fake_download(c, dd, package=None):
        path = downloaded_wrong if "v1" in c.title else downloaded_correct
        return SimpleNamespace(
            status="success", downloaded_path=path,
            download_url=f"https://x/{path.name}", selected_package=package,
        )

    def fake_process(path, target_task_uuid=None):
        processor_calls.append(path.name)
        if path == downloaded_wrong:
            # processor Case Agent fail_closed：对外 need_confirm + 审计
            return {
                "status": "need_confirm",
                "case_agent_status": "fail_closed",
                "error": "字幕映射合同校验未通过",
            }
        return {"status": "success"}

    monkeypatch.setattr(fetcher.provider, "download", fake_download)
    monkeypatch.setattr(fetcher.processor, "process", fake_process)

    # 让第二个关键词 "Foo v2" 进入 pending（AI 扩词或确定性词）
    monkeypatch.setattr(
        fetcher.ai_client,
        "generate_subtitle_search_queries",
        lambda td: ["Foo v2"],
    )

    result = fetcher.process_task("task-1")
    assert result["status"] == "success"
    # 第一个包因 processor fail_closed 被重试，第二个包成功
    assert processor_calls == ["wrong.zip", "correct.zip"]
    assert result["pipeline_mode"] == "auto_fetch_case_agent_primary"


def test_process_fail_closed_persists_processor_case_agent_status_when_all_fail(
    monkeypatch, tmp_path
):
    """所有关键词都 processor fail_closed → 最终 failed，但 last_result 透传
    processor_case_agent_status=fail_closed + failure_reason=processor_fail_closed。"""
    fetcher = _build_fetcher(monkeypatch, tmp_path)
    _force_case_agent_enabled(monkeypatch, enabled=True)
    cand = _make_candidate(
        "Foo 字幕", packages=[_make_package("p1", ["batch", "simplified"])]
    )
    monkeypatch.setattr(fetcher.provider, "search", lambda keyword, limit=10: [cand])
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda c: c)
    monkeypatch.setattr(fetcher.provider, "load_thread_packages", lambda c: c)
    monkeypatch.setattr(
        fetcher.ai_client, "choose_subtitle_candidate",
        lambda td, rc: _ai_choice(should_use=True, reason="ok"),
    )
    monkeypatch.setattr(
        fetcher.ai_client, "choose_subtitle_thread_package",
        lambda td, cd, ps: _ai_choice(should_use=True, reason="pick"),
    )
    downloaded = tmp_path / "got.zip"
    downloaded.write_text("s", encoding="utf-8")
    monkeypatch.setattr(
        fetcher.provider, "download",
        lambda c, dd, package=None: SimpleNamespace(
            status="success", downloaded_path=downloaded,
            download_url="https://x/got.zip", selected_package=package,
        ),
    )

    def fake_process(path, target_task_uuid=None):
        return {
            "status": "need_confirm",
            "case_agent_status": "fail_closed",
            "error": "字幕映射合同校验未通过",
        }

    monkeypatch.setattr(fetcher.processor, "process", fake_process)
    # 无 AI 扩词，单关键词耗尽即终止
    monkeypatch.setattr(
        fetcher.ai_client, "generate_subtitle_search_queries", lambda td: []
    )

    result = fetcher.process_task("task-1")
    assert result["status"] == "failed"
    assert result.get("processor_case_agent_status") == "fail_closed"
    assert result.get("failure_reason") == "processor_fail_closed"
    assert result["pipeline_mode"] == "auto_fetch_case_agent_primary"


# ---------------------------------------------------------------------------
# source_video 证据口径统一
# ---------------------------------------------------------------------------

def test_missing_video_card_source_video_aligned_with_subtitle_import(monkeypatch, tmp_path):
    """record key = local 源路径；MissingVideoCard.source_video 取 key basename，
    与字幕导入 SubtitleTargetVideoCard.source_video 同口径。"""
    fetcher = _build_fetcher(monkeypatch, tmp_path)
    # _build_case_agent_workspace 内部走 evidence_broker.build_missing_video_cards
    task_data = {
        "uuid": "t1", "name": "Foo", "is_movie": False, "season_id": 1,
        "target_root": str(tmp_path / "Series"),
    }
    record_data = {
        "/dl/[SubGroup] Foo 01.mkv": str(
            tmp_path / "Series" / "Season 1" / "Foo - S01E01 - Pilot.mkv"
        )
    }
    scan_scope = {"type": "series", "root": str(tmp_path / "Series"), "source": "task_data"}
    missing = [tmp_path / "Series" / "Season 1" / "Foo - S01E01 - Pilot.mkv"]
    ws = fetcher._build_case_agent_workspace(
        task_uuid="t1", task_data=task_data, record_data=record_data,
        scan_scope=scan_scope, missing_videos=missing, keywords=["Foo"],
    )
    assert ws is not None
    assert ws.missing_videos[0].source_video == "[SubGroup] Foo 01.mkv"
    assert ws.missing_videos[0].video == "Foo - S01E01 - Pilot.mkv"
