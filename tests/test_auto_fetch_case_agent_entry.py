"""auto_fetch Case Agent 入口 + fail_closed 解读对齐测试（Pi fake runtime）。

验证（auto_fetch 选帖/选包统一走 Pi evidence-driven 后端，single_shot 已移除）：
- ``run_auto_fetch_case_agent`` 入口经 ``_run_pi_backend`` → ``run_auto_fetch_case_agent_pi``，
  四态（accepted/fail_closed/need_confirm/invalid）由 Pi run 结果归一。
- auto_fetch.py 薄入口：Case Agent 主路径 accepted 走下载 + processor；
  fail_closed（候选/包被拒 / Pi 无 final）映射 ``reason=pi_fail_closed``，合格 skipped。
- **fail_closed 解读对齐**：processor 落盘产 fail_closed（对外 need_confirm +
  case_agent_status 审计）→ auto_fetch 视为该包未配对成功的合格结果，透传
  ``processor_case_agent_status`` / ``failure_reason`` 审计。
- source_video 证据口径统一（record key -> MissingVideoCard.source_video）。

**不真起 Pi sidecar / 不发真实 AI**：通过 monkeypatch
``pi_runner.run_auto_fetch_case_agent_pi`` 注入 ``runtime_invoker``，直接调
``state.handle_tool`` 编排 tool_call 序列（search → submit_candidate → load →
submit_package / fail_closed），provider 用 fake。范式见
``tests/test_auto_fetch_case_agent_pi_runner.py::test_entry_pi_backend_accepted_returns_four_state``。
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


class _FakeProvider:
    """记录调用的 fake provider（Pi 工具经 handle_tool 调 search/prepare/load）。"""

    def __init__(self, candidates_by_keyword=None):
        self._by_kw = candidates_by_keyword or {}

    def search(self, keyword, limit=10):
        return list(self._by_kw.get(keyword, []))

    def prepare_candidate(self, candidate):
        return candidate

    def load_thread_packages(self, candidate):
        return candidate


def _patch_pi_runner_with_invoker(monkeypatch, tmp_path, invoker):
    """patch ``pi_runner.run_auto_fetch_case_agent_pi`` 注入 runtime_invoker，
    并把 _case_root 指到 tmp_path 避免写真 run_dir。返回 (orig_restore)。"""
    import src.subtitle.auto_fetch_case_agent.pi_runner as pr_mod

    monkeypatch.setattr(pr_mod, "_case_root", lambda: tmp_path)
    orig_run = pr_mod.run_auto_fetch_case_agent_pi

    def patched_run(*, workspace, provider, task_data, source_label="", runtime_invoker=None):
        return orig_run(
            workspace=workspace,
            provider=provider,
            task_data=task_data,
            source_label=source_label,
            runtime_invoker=invoker,
        )

    monkeypatch.setattr(pr_mod, "run_auto_fetch_case_agent_pi", patched_run)


# ---------------------------------------------------------------------------
# run_auto_fetch_case_agent 入口四态（Pi fake runtime 驱动）
# ---------------------------------------------------------------------------

def test_entry_fail_closed_when_no_candidates(tmp_path, monkeypatch):
    """Pi 搜不到候选 → fail_closed（合格）。入口层验 Pi run 归一的 fail_closed。"""
    provider = _FakeProvider({"Foo": []})

    def invoker(state):
        # Pi 搜空：search_candidates 返回 no_candidates，agent fail_closed
        state.handle_tool("search_candidates", {"keyword": "Foo"})
        state.handle_tool(
            "fail_closed",
            {"reason": "no candidates match arc", "reason_kind": "no_candidates"},
        )
        return {"ok": True, "returncode": 0, "argv": ["fake"]}

    _patch_pi_runner_with_invoker(monkeypatch, tmp_path, invoker)
    result = run_auto_fetch_case_agent(
        workspace=_build_workspace(),
        candidates=[],
        task_data={"uuid": "t1"},
        backend="pi",
        provider=provider,
    )
    assert result["status"] == "fail_closed"


def test_entry_accepted_returns_selected_refs(tmp_path, monkeypatch):
    """Pi 选帖选包 accepted → 入口返回四态 accepted + selected refs + provider 原始对象。"""
    cand = _make_candidate("Foo 字幕", packages=[_make_package("p1", ["batch", "simplified"])])
    provider = _FakeProvider({"Foo": [cand]})

    def invoker(state):
        state.handle_tool("search_candidates", {"keyword": "Foo"})
        state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
        state.handle_tool("submit_package", {"package_ref": "PK1", "reason": "main batch"})
        return {"ok": True, "returncode": 0, "argv": ["fake"]}

    _patch_pi_runner_with_invoker(monkeypatch, tmp_path, invoker)
    result = run_auto_fetch_case_agent(
        workspace=_build_workspace(),
        candidates=[],
        task_data={"uuid": "t1"},
        backend="pi",
        provider=provider,
    )
    assert result["status"] == "accepted"
    assert result["selected_candidate_ref"] == "CD1"
    assert result["selected_package_ref"] == "PK1"
    assert "pi_run" in result["snapshot"]


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


def _install_pi_invoker(monkeypatch, tmp_path, invoker):
    """把 fetcher 走的 pi_runner 也注入 invoker（process_task 经 auto_fetch 入口）。"""
    _patch_pi_runner_with_invoker(monkeypatch, tmp_path, invoker)


def test_process_case_agent_accepted_lands_and_persists_pipeline_mode(
    monkeypatch, tmp_path
):
    """Pi accepted → 下载 + processor success → status=success，
    pipeline_mode=auto_fetch_case_agent_primary，case_agent_status=accepted。"""
    fetcher = _build_fetcher(monkeypatch, tmp_path)
    cand = _make_candidate(
        "Foo 字幕", packages=[_make_package("p1", ["batch", "simplified"])]
    )
    monkeypatch.setattr(fetcher.provider, "search", lambda keyword, limit=10: [cand])
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda c: c)
    monkeypatch.setattr(fetcher.provider, "load_thread_packages", lambda c: c)

    def invoker(state):
        state.handle_tool("search_candidates", {"keyword": "Foo"})
        state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
        state.handle_tool("submit_package", {"package_ref": "PK1", "reason": "main batch"})
        return {"ok": True, "returncode": 0, "argv": ["fake"]}

    _install_pi_invoker(monkeypatch, tmp_path, invoker)

    downloaded = tmp_path / "got.zip"
    downloaded.write_text("subtitle", encoding="utf-8")
    monkeypatch.setattr(
        fetcher.provider,
        "download",
        lambda c, dd, package=None, download_url=None: SimpleNamespace(
            status="success", downloaded_path=downloaded,
            download_url="https://x/got.zip", selected_package=package,
        ),
    )
    monkeypatch.setattr(
        fetcher.processor, "process",
        lambda path, target_task_uuid=None, allowed_target_videos=None: {
            "status": "success"
        },
    )

    result = fetcher.process_task("task-1")
    assert result["status"] == "success"
    assert result["pipeline_mode"] == "auto_fetch_case_agent_primary"
    assert result["case_agent_status"] == "accepted"


def test_process_case_agent_fail_closed_skips_with_audit(monkeypatch, tmp_path):
    """Pi fail_closed（候选/包被拒或无 final）→ status=skipped，
    reason 含 pi_fail_closed，case_agent_status=fail_closed（合格，不下载）。"""
    fetcher = _build_fetcher(monkeypatch, tmp_path)
    cand = _make_candidate("Wrong Arc", packages=[_make_package("p1", ["batch"])])
    monkeypatch.setattr(fetcher.provider, "search", lambda keyword, limit=10: [cand])
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda c: c)
    monkeypatch.setattr(fetcher.provider, "load_thread_packages", lambda c: c)

    def invoker(state):
        state.handle_tool("search_candidates", {"keyword": "Foo"})
        # Pi 判定 arc 不匹配 → fail_closed
        state.handle_tool(
            "fail_closed",
            {"reason": "no candidate matches arc", "reason_kind": "insufficient_evidence"},
        )
        return {"ok": True, "returncode": 0, "argv": ["fake"]}

    _install_pi_invoker(monkeypatch, tmp_path, invoker)
    monkeypatch.setattr(
        fetcher.provider, "download",
        lambda *a, **k: pytest.fail("download should not be called on fail_closed"),
    )
    monkeypatch.setattr(
        fetcher.processor, "process",
        lambda *a, **k: pytest.fail("processor should not be called on fail_closed"),
    )

    result = fetcher.process_task("task-1")
    assert result["status"] == "skipped"
    assert result["pipeline_mode"] == "auto_fetch_case_agent_primary"
    assert result["case_agent_status"] == "fail_closed"
    # _run_pi_backend fail_closed 归一 reason_kind='pi_fail_closed'，auto_fetch 透传
    assert result["reason"] == "pi_fail_closed"


# ---------------------------------------------------------------------------
# fail_closed 解读对齐：processor fail_closed → 审计透传
# ---------------------------------------------------------------------------

def test_process_persists_processor_case_agent_status_when_processor_fail_closed(
    monkeypatch, tmp_path
):
    """Pi accepted + 下载成功，但 processor 落盘产 fail_closed（对外 need_confirm）→
    auto_fetch 视为该包未配对成功的合格可重试结果，最终 failed，
    透传 processor_case_agent_status=fail_closed + failure_reason=processor_fail_closed。

    （Pi 驱动后无关键词循环重试；本例验单次 accepted→download→processor fail_closed
    的审计透传，不再验"换词重试"。）"""
    fetcher = _build_fetcher(monkeypatch, tmp_path)
    cand = _make_candidate(
        "Foo 字幕", packages=[_make_package("p1", ["batch", "simplified"])]
    )
    monkeypatch.setattr(fetcher.provider, "search", lambda keyword, limit=10: [cand])
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda c: c)
    monkeypatch.setattr(fetcher.provider, "load_thread_packages", lambda c: c)

    def invoker(state):
        state.handle_tool("search_candidates", {"keyword": "Foo"})
        state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
        state.handle_tool("submit_package", {"package_ref": "PK1", "reason": "main batch"})
        return {"ok": True, "returncode": 0, "argv": ["fake"]}

    _install_pi_invoker(monkeypatch, tmp_path, invoker)

    downloaded = tmp_path / "got.zip"
    downloaded.write_text("s", encoding="utf-8")
    monkeypatch.setattr(
        fetcher.provider, "download",
        lambda c, dd, package=None, download_url=None: SimpleNamespace(
            status="success", downloaded_path=downloaded,
            download_url="https://x/got.zip", selected_package=package,
        ),
    )

    def fake_process(path, target_task_uuid=None, allowed_target_videos=None):
        return {
            "status": "need_confirm",
            "case_agent_status": "fail_closed",
            "error": "字幕映射合同校验未通过",
        }

    monkeypatch.setattr(fetcher.processor, "process", fake_process)

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
