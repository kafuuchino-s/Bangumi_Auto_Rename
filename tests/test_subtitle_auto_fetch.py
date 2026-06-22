"""auto_fetch 薄入口 process_task 测试（Pi fake runtime 驱动）。

auto_fetch 选帖/选包统一走 Pi evidence-driven 后端（single_shot 已移除），
Python 主进程不预爬、不 AI 扩词、无换词重试、无 legacy 规则兜底。本套件覆盖：

- accepted 主路径：Pi 选帖选包 → 下载 → processor success。
- fail_closed 不下载：Pi 判定无匹配 → skipped，不触发 download/processor。
- processor fail_closed 审计透传：Pi accepted + 下载成功但 processor 落盘产
  fail_closed → 最终 failed，透传 processor_case_agent_status / failure_reason。
- 搜索词构造：``_build_search_keywords`` 主词取 BGM subject 名，回退源目录标题
  变体（方向 A，确定性变体规范化，不 AI 扩词）。

**不真起 Pi sidecar / 不发真实 AI**：通过 monkeypatch
``pi_runner.run_auto_fetch_case_agent_pi`` 注入 ``runtime_invoker``，直接调
``state.handle_tool`` 编排 tool_call 序列，provider 用 fake。范式见
``tests/test_auto_fetch_case_agent_pi_runner.py::test_entry_pi_backend_accepted_returns_four_state``。
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.subtitle.auto_fetch import SubtitleAutoFetcher
from src.subtitle.providers import SubtitleCandidate, SubtitleThreadPackage


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

def make_package(package_id, flags, *, has_direct_download=True, page_number=1):
    return SubtitleThreadPackage(
        package_id=package_id,
        page_number=page_number,
        floor_label=f"{package_id}-floor",
        post_author="author",
        post_time="2023-03-13 06:43:45",
        post_text="package text",
        context_text="package context",
        links=[
            SimpleNamespace(
                url=f"https://example.com/{package_id}.zip",
                kind="attachment",
                label=f"{package_id}.zip",
                filename_hint=f"{package_id}.zip",
                is_direct_download=has_direct_download,
            )
        ],
        has_direct_download=has_direct_download,
        package_flags=flags,
    )


def build_fetcher(monkeypatch, tmp_path):
    fetcher = SubtitleAutoFetcher()
    task_uuid = "task-1"
    monkeypatch.setattr(
        "src.subtitle.auto_fetch.get_task",
        lambda uuid: {
            "uuid": uuid,
            "name": "Seitokai no Ichizon",
            "tmdb_name": "Seitokai no Ichizon",
            "season_id": 1,
            "is_movie": False,
            "target_root": str(tmp_path / "Series"),
        },
    )
    monkeypatch.setattr(
        "src.subtitle.auto_fetch.get_record",
        lambda uuid: {"a": str(tmp_path / "Series" / "Season 1" / "ep1.mkv")},
    )
    season_dir = tmp_path / "Series" / "Season 1"
    season_dir.mkdir(parents=True, exist_ok=True)
    (season_dir / "ep1.mkv").write_text("video", encoding="utf-8")
    return fetcher, task_uuid


def build_season0_fetcher(monkeypatch, tmp_path):
    fetcher = SubtitleAutoFetcher()
    task_uuid = "task-season0"
    monkeypatch.setattr(
        "src.subtitle.auto_fetch.get_task",
        lambda uuid: {
            "uuid": uuid,
            "name": "鬼灭之刃",
            "tmdb_name": "鬼灭之刃",
            "path": str(
                tmp_path
                / "[BeanSub&FZSD&VCB-Studio] Gekijouban Kimetsu no Yaiba Mugen Ressha Hen [Ma10p_1080p]"
            ),
            "season_id": 0,
            "is_movie": False,
            "target_root": str(tmp_path / "SeriesRoot"),
        },
    )
    monkeypatch.setattr(
        "src.subtitle.auto_fetch.get_record",
        lambda uuid: {
            str(
                tmp_path
                / "Source"
                / "[BeanSub&FZSD&VCB-Studio] Gekijouban Kimetsu no Yaiba Mugen Ressha Hen [Ma10p_1080p][x265_flac].mkv"
            ): str(
                tmp_path
                / "SeriesRoot"
                / "Season 00"
                / "鬼灭之刃 - S00E14 - 1080p x265 FLAC - BeanSub&FZSD&VCB-Studio.mkv"
            )
        },
    )
    season_dir = tmp_path / "SeriesRoot" / "Season 00"
    season_dir.mkdir(parents=True, exist_ok=True)
    (
        season_dir
        / "鬼灭之刃 - S00E14 - 1080p x265 FLAC - BeanSub&FZSD&VCB-Studio.mkv"
    ).write_text("video", encoding="utf-8")
    return fetcher, task_uuid


def _patch_pi_runner_with_invoker(monkeypatch, tmp_path, invoker):
    """patch ``pi_runner.run_auto_fetch_case_agent_pi`` 注入 runtime_invoker，
    _case_root 指到 tmp_path 避免写真 run_dir。"""
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
# accepted 主路径
# ---------------------------------------------------------------------------

def test_process_accepted_lands_via_pi_selected_package(monkeypatch, tmp_path):
    """Pi 选帖选包 accepted → 下载选中的 batch 包 → processor success。"""
    fetcher, task_uuid = build_fetcher(monkeypatch, tmp_path)
    candidate = SubtitleCandidate(
        title="thread-1",
        detail_url="https://bbs.acgrip.com/thread-1",
        source="acgrip",
    )
    candidate.thread_packages = [
        make_package("patch", ["patch"]),
        make_package("batch", ["batch", "simplified"]),
    ]
    monkeypatch.setattr(fetcher.provider, "search", lambda keyword, limit=10: [candidate])
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda c: c)
    monkeypatch.setattr(fetcher.provider, "load_thread_packages", lambda c: c)

    def invoker(state):
        state.handle_tool("search_candidates", {"keyword": "Seitokai no Ichizon"})
        state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
        # 选 batch 包（PK2），非 patch（PK1）
        state.handle_tool("submit_package", {"package_ref": "PK2", "reason": "main batch"})
        return {"ok": True, "returncode": 0, "argv": ["fake"]}

    _patch_pi_runner_with_invoker(monkeypatch, tmp_path, invoker)

    downloaded = tmp_path / "picked.zip"
    downloaded.write_text("subtitle", encoding="utf-8")
    download_calls = {}

    def fake_download(candidate, destination_dir, package=None, download_url=None):
        download_calls["package"] = package
        return SimpleNamespace(
            status="success",
            downloaded_path=downloaded,
            download_url="https://example.com/picked.zip",
            selected_package=package,
        )

    monkeypatch.setattr(fetcher.provider, "download", fake_download)
    monkeypatch.setattr(
        fetcher.processor, "process",
        lambda path, target_task_uuid=None: {"status": "success"},
    )

    result = fetcher.process_task(task_uuid)

    assert result["status"] == "success"
    assert result["pipeline_mode"] == "auto_fetch_case_agent_primary"
    assert result["case_agent_status"] == "accepted"
    # B9 多 selection 结构：result 用 selections 列表，每项含 selected_package
    assert result["selections_count"] >= 1
    assert result["selections"][0]["selected_package"]["package_id"] == "batch"
    assert download_calls["package"].package_id == "batch"


# ---------------------------------------------------------------------------
# fail_closed 不下载
# ---------------------------------------------------------------------------

def test_process_fail_closed_skips_without_download(monkeypatch, tmp_path):
    """Pi 判定无匹配候选 → fail_closed → skipped，不触发 download/processor。"""
    fetcher, task_uuid = build_fetcher(monkeypatch, tmp_path)
    candidate = SubtitleCandidate(
        title="wrong arc",
        detail_url="https://bbs.acgrip.com/thread-wrong",
        source="acgrip",
    )
    candidate.thread_packages = [make_package("batch", ["batch", "simplified"])]
    monkeypatch.setattr(fetcher.provider, "search", lambda keyword, limit=10: [candidate])
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda c: c)
    monkeypatch.setattr(fetcher.provider, "load_thread_packages", lambda c: c)

    def invoker(state):
        state.handle_tool("search_candidates", {"keyword": "Seitokai no Ichizon"})
        state.handle_tool(
            "fail_closed",
            {"reason": "no candidate matches arc", "reason_kind": "insufficient_evidence"},
        )
        return {"ok": True, "returncode": 0, "argv": ["fake"]}

    _patch_pi_runner_with_invoker(monkeypatch, tmp_path, invoker)
    monkeypatch.setattr(
        fetcher.provider, "download",
        lambda *a, **k: pytest.fail("download should not be called on fail_closed"),
    )
    monkeypatch.setattr(
        fetcher.processor, "process",
        lambda *a, **k: pytest.fail("processor should not be called on fail_closed"),
    )

    result = fetcher.process_task(task_uuid)

    assert result["status"] == "skipped"
    assert result["case_agent_status"] == "fail_closed"
    assert result["reason"] == "pi_fail_closed"


# ---------------------------------------------------------------------------
# processor fail_closed 审计透传
# ---------------------------------------------------------------------------

def test_process_persists_processor_case_agent_status_when_processor_fail_closed(
    monkeypatch, tmp_path
):
    """Pi accepted + 下载成功，但 processor 落盘产 fail_closed → 最终 failed，
    透传 processor_case_agent_status=fail_closed + failure_reason=processor_fail_closed。"""
    fetcher, task_uuid = build_fetcher(monkeypatch, tmp_path)
    candidate = SubtitleCandidate(
        title="thread-1",
        detail_url="https://bbs.acgrip.com/thread-1",
        source="acgrip",
    )
    candidate.thread_packages = [make_package("batch", ["batch", "simplified"])]
    monkeypatch.setattr(fetcher.provider, "search", lambda keyword, limit=10: [candidate])
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda c: c)
    monkeypatch.setattr(fetcher.provider, "load_thread_packages", lambda c: c)

    def invoker(state):
        state.handle_tool("search_candidates", {"keyword": "Seitokai no Ichizon"})
        state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
        state.handle_tool("submit_package", {"package_ref": "PK1", "reason": "main batch"})
        return {"ok": True, "returncode": 0, "argv": ["fake"]}

    _patch_pi_runner_with_invoker(monkeypatch, tmp_path, invoker)

    downloaded = tmp_path / "got.zip"
    downloaded.write_text("s", encoding="utf-8")
    monkeypatch.setattr(
        fetcher.provider, "download",
        lambda c, dd, package=None, download_url=None: SimpleNamespace(
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

    result = fetcher.process_task(task_uuid)

    assert result["status"] == "failed"
    assert result.get("processor_case_agent_status") == "fail_closed"
    assert result.get("failure_reason") == "processor_fail_closed"


# ---------------------------------------------------------------------------
# 搜索词构造：方向 A（BGM subject 名优先 + 源目录标题回退，不 AI 扩词）
# ---------------------------------------------------------------------------

def test_build_search_keywords_prefers_bgm_subject_name(monkeypatch, tmp_path):
    """task_data 含 bgm_subject_name_cn / bgm_subject_name → 主词取 BGM 名变体。"""
    fetcher, _ = build_fetcher(monkeypatch, tmp_path)
    task_data = {
        "uuid": "t1",
        "name": "Seitokai no Ichizon",
        "bgm_subject_name": "生徒会の一存",
        "bgm_subject_name_cn": "碧阳学园学生会议事录",
        "season_id": 1,
        "is_movie": False,
    }
    missing = [tmp_path / "Series" / "Season 1" / "ep1.mkv"]
    keywords = fetcher._build_search_keywords(task_data, missing)
    # name_cn 优先；至少含 name_cn 原始变体
    assert any("碧阳学园学生会议事录" in k for k in keywords)
    # 也含 name 变体
    assert any("生徒会の一存" in k for k in keywords)


def test_build_search_keywords_falls_back_to_source_path_title(monkeypatch, tmp_path):
    """无 BGM 名时回退源目录标题（path）变体；确定性规范化，不 AI 扩词。"""
    fetcher, _ = build_season0_fetcher(monkeypatch, tmp_path)
    task_data = {
        "uuid": "t-season0",
        "name": "鬼灭之刃",
        "path": str(
            tmp_path
            / "[BeanSub&FZSD&VCB-Studio] Gekijouban Kimetsu no Yaiba Mugen Ressha Hen [Ma10p_1080p]"
        ),
        "season_id": 0,
        "is_movie": False,
    }
    missing = [tmp_path / "SeriesRoot" / "Season 00" / "ep.mkv"]
    keywords = fetcher._build_search_keywords(task_data, missing)
    # 源目录标题变体应包含英文主名（ascii_only 变体）
    assert any("Gekijouban Kimetsu no Yaiba Mugen Ressha Hen" in k for k in keywords)
    # name 变体也在
    assert any("鬼灭之刃" in k for k in keywords)


def test_build_search_keywords_empty_when_no_signal(monkeypatch, tmp_path):
    """无 BGM 名 / 无 path → 兜底用缺失视频文件 stem（ep1）生成确定性变体。"""
    fetcher, _ = build_fetcher(monkeypatch, tmp_path)
    task_data = {"uuid": "t1", "season_id": 1, "is_movie": False}
    missing = [tmp_path / "Series" / "Season 1" / "ep1.mkv"]
    keywords = fetcher._build_search_keywords(task_data, missing)
    # 无 name/path/BGM 名，兜底 missing video 文件 stem "ep1" + 其 digit_spaced 变体 "ep 1"
    assert "ep1" in keywords
    assert all("鬼灭" not in k and "Seitokai" not in k for k in keywords)
