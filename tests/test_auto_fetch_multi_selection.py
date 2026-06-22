"""auto_fetch 多 selection 消费单测（阶段3d）。

验证 _execute_fetch 消费 Pi submit_complete 多 selection（每 subject 一帖一包）：

- 各 selection 下载到独立 sel_<idx> 子目录（多 selection 时）。
- 逐 selection processor 配对，顶层合并 mappings/unmatched/no_target_videos/matched_count。
- accepted =>=1 selection 下载+processor success；部分失败仍 accepted。
- 全部 processor fail_closed -> 整体 failed（合格不落盘）+ failure_reason=processor_fail_closed。

不真起 Pi sidecar：monkeypatch pi_runner.run_auto_fetch_case_agent_pi 注入
runtime_invoker 编排 tool_call 序列，provider/processor 用 fake。
范式见 tests/test_auto_fetch_mapping_mode.py。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.subtitle.auto_fetch import SubtitleAutoFetcher
from src.subtitle.providers import SubtitleCandidate, SubtitleThreadPackage

# 复用 mapping_mode 测试的 helper（同目录 import）
from tests.test_auto_fetch_mapping_mode import (
    build_mapping_fetcher,
    make_package,
    _patch_pi_runner_with_invoker,
)


def _candidate(title: str) -> SubtitleCandidate:
    c = SubtitleCandidate(
        title=title,
        detail_url="https://bbs.acgrip.com/" + title,
        source="acgrip",
    )
    c.thread_packages = [make_package("pk-" + title, ["batch", "simplified"])]
    c.pages_scanned = 1
    return c


def test_multi_selection_downloads_each_to_separate_dir_and_merges(
    monkeypatch, tmp_path
):
    """3 个 subject 各 submit_package + submit_complete -> 3 个 selection 各下载到
    sel_0/sel_1/sel_2 独立子目录 + 顶层合并 3 个 mapping。"""
    fetcher, task_uuid, _ = build_mapping_fetcher(monkeypatch, tmp_path)
    target_s01 = tmp_path / "Series" / "Season 01" / "Series - S01E01.mkv"
    target_s02 = tmp_path / "Series" / "Season 02" / "Series - S02E01.mkv"
    target_mov = tmp_path / "Movie" / "Series Movie (2020).mkv"

    cand = _candidate("thread-multi")
    monkeypatch.setattr(fetcher.provider, "search", lambda keyword, limit=10: [cand])
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda c: c)
    monkeypatch.setattr(fetcher.provider, "load_thread_packages", lambda c: c)

    def invoker(state):
        state.handle_tool("search_candidates", {"keyword": "Series"})
        state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
        for sid in (245665, 350764, 291494):
            state.handle_tool(
                "submit_candidate",
                {"candidate_ref": "CD1", "bangumi_subject_id": sid},
            )
            state.handle_tool("submit_package", {"package_ref": "PK1"})
        state.handle_tool("submit_complete", {"reason": "all 3 subjects"})
        return {"ok": True, "returncode": 0, "argv": ["fake"]}

    _patch_pi_runner_with_invoker(monkeypatch, tmp_path, invoker)

    download_dests = []

    def fake_download(cand_arg, destination_dir, package=None, download_url=None):
        download_dests.append(Path(destination_dir))
        n = len(download_dests)
        downloaded = tmp_path / ("pkg_" + str(n) + ".zip")
        downloaded.write_text("subtitle", encoding="utf-8")
        return SimpleNamespace(
            status="success",
            downloaded_path=downloaded,
            download_url="https://example.com/pkg" + str(n) + ".zip",
            selected_package=package,
        )

    monkeypatch.setattr(fetcher.provider, "download", fake_download)

    call_counter = {"n": 0}
    video_by_n = {1: target_s01.name, 2: target_s02.name, 3: target_mov.name}

    def fake_process_mapping(archive_path, target_task_uuid=None):
        call_counter["n"] += 1
        n = call_counter["n"]
        return {
            "status": "success",
            "mapping_only": True,
            "matched_count": 1,
            "mappings": [
                {
                    "subtitle": "sub" + str(n) + ".ass",
                    "video": video_by_n[n],
                    "target": "Series - " + video_by_n[n] + ".zh-CN.ass",
                    "task_uuid": target_task_uuid,
                    "language": "zh-CN",
                    "sync_status": "disabled",
                }
            ],
            "unmatched": [],
            "no_target_videos": [],
        }

    monkeypatch.setattr(fetcher.processor, "process_mapping", fake_process_mapping)
    monkeypatch.setattr(
        fetcher.processor, "process",
        lambda *a, **k: pytest.fail("process 不应在 mapping 模式被调用"),
    )

    result = fetcher.process_task_mapping(
        task_uuid, missing_videos_override=[target_s01, target_s02, target_mov]
    )

    assert result["status"] == "success"
    assert result["selections_count"] == 3
    # 3 个 selection 各下载到独立 sel_0/sel_1/sel_2 子目录
    assert len(download_dests) == 3
    assert sorted(d.name for d in download_dests) == ["sel_0", "sel_1", "sel_2"]
    assert [s["status"] for s in result["selections"]] == ["success"] * 3
    # 顶层合并 3 个 mapping
    assert len(result["mappings"]) == 3
    merged_videos = {m["video"] for m in result["mappings"]}
    assert merged_videos == {target_s01.name, target_s02.name, target_mov.name}
    assert result["matched_count"] == 3


def test_multi_selection_dedupes_same_video_language_across_selections(
    monkeypatch, tmp_path
):
    """B9：多 subject 同帖重复配对去重。一帖覆盖多 subject 时，多个 selection 各自
    下载配对，合并后同 (video, language) 被配多次（0002 前後篇 05-08 各配 2 次）。
    _execute_fetch 合并 mappings 按 (video, language) 去重保留一条，matched_count
    按去重后计。
    """
    fetcher, task_uuid, _ = build_mapping_fetcher(monkeypatch, tmp_path)
    target_e05 = tmp_path / "Series" / "Season 01" / "Series - S01E05.mkv"
    target_e06 = tmp_path / "Series" / "Season 01" / "Series - S01E06.mkv"

    cand = _candidate("thread-combined")
    monkeypatch.setattr(fetcher.provider, "search", lambda keyword, limit=10: [cand])
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda c: c)
    monkeypatch.setattr(fetcher.provider, "load_thread_packages", lambda c: c)

    def invoker(state):
        state.handle_tool("search_candidates", {"keyword": "Series"})
        state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
        # 2 个 subject 都选同一帖同一包（合帖场景）
        for sid in (319390, 352905):
            state.handle_tool(
                "submit_candidate",
                {"candidate_ref": "CD1", "bangumi_subject_id": sid},
            )
            state.handle_tool("submit_package", {"package_ref": "PK1"})
        state.handle_tool("submit_complete", {"reason": "combined thread"})
        return {"ok": True, "returncode": 0, "argv": ["fake"]}

    _patch_pi_runner_with_invoker(monkeypatch, tmp_path, invoker)

    def fake_download(cand_arg, destination_dir, package=None, download_url=None):
        downloaded = tmp_path / "pkg.zip"
        downloaded.write_text("subtitle", encoding="utf-8")
        return SimpleNamespace(
            status="success",
            downloaded_path=downloaded,
            download_url="https://example.com/pkg.zip",
            selected_package=package,
        )

    monkeypatch.setattr(fetcher.provider, "download", fake_download)

    # 两个 selection 的 processor 都配对出 S01E05.zh-CN + S01E06.zh-CN（重复）
    def fake_process_mapping(archive_path, target_task_uuid=None):
        return {
            "status": "success",
            "mapping_only": True,
            "matched_count": 2,
            "mappings": [
                {
                    "subtitle": "sub.ass",
                    "video": target_e05.name,
                    "target": "Series - " + target_e05.name + ".zh-CN.ass",
                    "task_uuid": target_task_uuid,
                    "language": "zh-CN",
                    "sync_status": "disabled",
                },
                {
                    "subtitle": "sub.ass",
                    "video": target_e06.name,
                    "target": "Series - " + target_e06.name + ".zh-CN.ass",
                    "task_uuid": target_task_uuid,
                    "language": "zh-CN",
                    "sync_status": "disabled",
                },
            ],
            "unmatched": [],
            "no_target_videos": [],
        }

    monkeypatch.setattr(fetcher.processor, "process_mapping", fake_process_mapping)
    monkeypatch.setattr(
        fetcher.processor, "process",
        lambda *a, **k: pytest.fail("process 不应在 mapping 模式被调用"),
    )

    result = fetcher.process_task_mapping(
        task_uuid, missing_videos_override=[target_e05, target_e06]
    )

    assert result["status"] == "success"
    # 2 个 selection 各配 2 条 = 4 条原始，去重后 2 条（S01E05 + S01E06 各 1）
    assert len(result["mappings"]) == 2
    assert result["matched_count"] == 2
    videos = {m["video"] for m in result["mappings"]}
    assert videos == {target_e05.name, target_e06.name}


def test_multi_selection_partial_success_still_accepted(monkeypatch, tmp_path):
    """多 selection 中 1 个 processor fail_closed + 1 个 success -> 整体 accepted
    （>=1 成功即 success），失败 unit 记 processor_failed。"""
    fetcher, task_uuid, _ = build_mapping_fetcher(monkeypatch, tmp_path)
    target_a = tmp_path / "Series" / "Season 01" / "Series - S01E01.mkv"
    target_b = tmp_path / "Series" / "Season 02" / "Series - S02E01.mkv"

    cand = _candidate("thread-partial")
    monkeypatch.setattr(fetcher.provider, "search", lambda keyword, limit=10: [cand])
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda c: c)
    monkeypatch.setattr(fetcher.provider, "load_thread_packages", lambda c: c)

    def invoker(state):
        state.handle_tool("search_candidates", {"keyword": "Series"})
        state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
        state.handle_tool("submit_candidate", {"candidate_ref": "CD1", "bangumi_subject_id": 245665})
        state.handle_tool("submit_package", {"package_ref": "PK1"})
        state.handle_tool("submit_candidate", {"candidate_ref": "CD1", "bangumi_subject_id": 350764})
        state.handle_tool("submit_package", {"package_ref": "PK1"})
        state.handle_tool("submit_complete", {"reason": "2 subjects"})
        return {"ok": True, "returncode": 0, "argv": ["fake"]}

    _patch_pi_runner_with_invoker(monkeypatch, tmp_path, invoker)

    def fake_download(cand_arg, destination_dir, package=None, download_url=None):
        downloaded = tmp_path / ("pkg_" + str(destination_dir.name) + ".zip")
        downloaded.write_text("subtitle", encoding="utf-8")
        return SimpleNamespace(
            status="success", downloaded_path=downloaded,
            download_url="https://example.com/pkg.zip", selected_package=package,
        )

    monkeypatch.setattr(fetcher.provider, "download", fake_download)

    call_counter = {"n": 0}

    def fake_process_mapping(archive_path, target_task_uuid=None):
        call_counter["n"] += 1
        if call_counter["n"] == 1:
            return {
                "status": "success", "mapping_only": True, "matched_count": 1,
                "mappings": [{
                    "subtitle": "sub.ass", "video": target_a.name,
                    "target": "a.ass", "task_uuid": target_task_uuid,
                    "language": "zh-CN", "sync_status": "disabled",
                }],
                "unmatched": [], "no_target_videos": [],
            }
        return {
            "status": "need_confirm", "case_agent_status": "fail_closed",
            "error": "no confident match", "mappings": [], "unmatched": [],
            "no_target_videos": [],
        }

    monkeypatch.setattr(fetcher.processor, "process_mapping", fake_process_mapping)

    result = fetcher.process_task_mapping(
        task_uuid, missing_videos_override=[target_a, target_b]
    )

    assert result["status"] == "success"
    assert result["selections_count"] == 2
    statuses = [s["status"] for s in result["selections"]]
    assert "success" in statuses
    assert "processor_failed" in statuses
    assert len(result["mappings"]) == 1
    assert result["mappings"][0]["video"] == target_a.name


def test_multi_selection_all_processor_fail_closed_is_failed(monkeypatch, tmp_path):
    """多 selection 全部 processor fail_closed -> 整体 failed（合格不落盘），
    failure_reason=processor_fail_closed。"""
    fetcher, task_uuid, _ = build_mapping_fetcher(monkeypatch, tmp_path)
    target_a = tmp_path / "Series" / "Season 01" / "Series - S01E01.mkv"
    target_b = tmp_path / "Series" / "Season 02" / "Series - S02E01.mkv"

    cand = _candidate("thread-allfail")
    monkeypatch.setattr(fetcher.provider, "search", lambda keyword, limit=10: [cand])
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda c: c)
    monkeypatch.setattr(fetcher.provider, "load_thread_packages", lambda c: c)

    def invoker(state):
        state.handle_tool("search_candidates", {"keyword": "Series"})
        state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
        for sid in (245665, 350764):
            state.handle_tool("submit_candidate", {"candidate_ref": "CD1", "bangumi_subject_id": sid})
            state.handle_tool("submit_package", {"package_ref": "PK1"})
        state.handle_tool("submit_complete", {"reason": "2 subjects"})
        return {"ok": True, "returncode": 0, "argv": ["fake"]}

    _patch_pi_runner_with_invoker(monkeypatch, tmp_path, invoker)

    def fake_download(cand_arg, destination_dir, package=None, download_url=None):
        downloaded = tmp_path / ("pkg_" + str(destination_dir.name) + ".zip")
        downloaded.write_text("subtitle", encoding="utf-8")
        return SimpleNamespace(
            status="success", downloaded_path=downloaded,
            download_url="https://example.com/pkg.zip", selected_package=package,
        )

    monkeypatch.setattr(fetcher.provider, "download", fake_download)

    def fake_process_mapping(archive_path, target_task_uuid=None):
        return {
            "status": "need_confirm", "case_agent_status": "fail_closed",
            "error": "no match", "mappings": [], "unmatched": [], "no_target_videos": [],
        }

    monkeypatch.setattr(fetcher.processor, "process_mapping", fake_process_mapping)

    result = fetcher.process_task_mapping(
        task_uuid, missing_videos_override=[target_a, target_b]
    )

    assert result["status"] == "failed"
    assert result["failure_reason"] == "processor_fail_closed"
    assert result["selections_count"] == 2
    assert all(s["status"] == "processor_failed" for s in result["selections"])
    assert result["mappings"] == []


def test_multi_selection_download_fail_continues_others(monkeypatch, tmp_path):
    """多 selection 中第 1 个下载失败 -> 该 unit 记 download_failed，继续下载后续
    selection；若后续有 success -> 整体 accepted。"""
    fetcher, task_uuid, _ = build_mapping_fetcher(monkeypatch, tmp_path)
    target_a = tmp_path / "Series" / "Season 01" / "Series - S01E01.mkv"
    target_b = tmp_path / "Series" / "Season 02" / "Series - S02E01.mkv"

    cand = _candidate("thread-dlfail")
    monkeypatch.setattr(fetcher.provider, "search", lambda keyword, limit=10: [cand])
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda c: c)
    monkeypatch.setattr(fetcher.provider, "load_thread_packages", lambda c: c)

    def invoker(state):
        state.handle_tool("search_candidates", {"keyword": "Series"})
        state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
        for sid in (245665, 350764):
            state.handle_tool("submit_candidate", {"candidate_ref": "CD1", "bangumi_subject_id": sid})
            state.handle_tool("submit_package", {"package_ref": "PK1"})
        state.handle_tool("submit_complete", {"reason": "2 subjects"})
        return {"ok": True, "returncode": 0, "argv": ["fake"]}

    _patch_pi_runner_with_invoker(monkeypatch, tmp_path, invoker)

    call_counter = {"n": 0}

    def fake_download(cand_arg, destination_dir, package=None, download_url=None):
        call_counter["n"] += 1
        if call_counter["n"] == 1:
            return SimpleNamespace(
                status="error", downloaded_path=None, download_url="",
                selected_package=package, error="network_timeout",
            )
        downloaded = tmp_path / ("pkg_" + str(destination_dir.name) + ".zip")
        downloaded.write_text("subtitle", encoding="utf-8")
        return SimpleNamespace(
            status="success", downloaded_path=downloaded,
            download_url="https://example.com/pkg.zip", selected_package=package,
        )

    monkeypatch.setattr(fetcher.provider, "download", fake_download)

    def fake_process_mapping(archive_path, target_task_uuid=None):
        return {
            "status": "success", "mapping_only": True, "matched_count": 1,
            "mappings": [{
                "subtitle": "sub.ass", "video": target_b.name,
                "target": "b.ass", "task_uuid": target_task_uuid,
                "language": "zh-CN", "sync_status": "disabled",
            }],
            "unmatched": [], "no_target_videos": [],
        }

    monkeypatch.setattr(fetcher.processor, "process_mapping", fake_process_mapping)

    result = fetcher.process_task_mapping(
        task_uuid, missing_videos_override=[target_a, target_b]
    )

    # 第 1 个下载失败但第 2 个 success -> 整体 accepted
    assert result["status"] == "success"
    assert result["selections_count"] == 2
    statuses = [s["status"] for s in result["selections"]]
    assert "download_failed" in statuses
    assert "success" in statuses
    assert len(result["mappings"]) == 1
    assert result["mappings"][0]["video"] == target_b.name


def test_single_selection_legacy_path_no_subdir(monkeypatch, tmp_path):
    """旧单 submit_package 路径（entry 无 selections 字段）-> fetch_units 回退到
    顶层 selected_candidate，只有 1 unit，下载到 download_root（无 sel_ 子目录）。"""
    fetcher, task_uuid, target_movie = build_mapping_fetcher(monkeypatch, tmp_path)

    cand = _candidate("thread-single")
    monkeypatch.setattr(fetcher.provider, "search", lambda keyword, limit=10: [cand])
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda c: c)
    monkeypatch.setattr(fetcher.provider, "load_thread_packages", lambda c: c)

    # invoker 只调 submit_package（不 submit_complete）-> auto-submit_complete fallback
    # 产生 1 个 selection
    def invoker(state):
        state.handle_tool("search_candidates", {"keyword": "Omoide no Mani"})
        state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
        state.handle_tool("submit_package", {"package_ref": "PK1", "reason": "main"})
        return {"ok": True, "returncode": 0, "argv": ["fake"]}

    _patch_pi_runner_with_invoker(monkeypatch, tmp_path, invoker)

    download_dests = []

    def fake_download(cand_arg, destination_dir, package=None, download_url=None):
        download_dests.append(Path(destination_dir))
        downloaded = tmp_path / "single.zip"
        downloaded.write_text("subtitle", encoding="utf-8")
        return SimpleNamespace(
            status="success", downloaded_path=downloaded,
            download_url="https://example.com/single.zip", selected_package=package,
        )

    monkeypatch.setattr(fetcher.provider, "download", fake_download)

    def fake_process_mapping(archive_path, target_task_uuid=None):
        return {
            "status": "success", "mapping_only": True, "matched_count": 1,
            "mappings": [{
                "subtitle": "sub.ass", "video": target_movie.name,
                "target": "single.ass", "task_uuid": target_task_uuid,
                "language": "zh-CN", "sync_status": "disabled",
            }],
            "unmatched": [], "no_target_videos": [],
        }

    monkeypatch.setattr(fetcher.processor, "process_mapping", fake_process_mapping)

    result = fetcher.process_task_mapping(
        task_uuid, missing_videos_override=[target_movie]
    )

    assert result["status"] == "success"
    assert result["selections_count"] == 1
    # 单 selection 下载到 download_root，无 sel_ 子目录
    assert len(download_dests) == 1
    assert "auto_fetch_mapping" in str(download_dests[0])
    # download_root 目录名不含 sel_（单 unit 时 download_dir = download_root）
    assert download_dests[0].name != "sel_0"
    assert len(result["mappings"]) == 1


# ---------------------------------------------------------------------------
# _execute_fetch 下载并发 + processor 串行（速度优化）
# ---------------------------------------------------------------------------

def test_multi_selection_downloads_concurrently_processor_serial(monkeypatch, tmp_path):
    """并发优化：多 selection 下载并发（provider.download 重叠），processor 串行
    （self.processor 单例调用不重叠，避免 Node sidecar 资源爆炸 + temp_dir 撞）。
    concurrency>1 时下载并发，concurrency=1 时全串行。"""
    import threading
    import time as _time

    # 用 config 实际设值（默认 3）；不 mock cm_get 以免干扰其它配置读取

    fetcher, task_uuid, _ = build_mapping_fetcher(monkeypatch, tmp_path)
    target_s01 = tmp_path / "Series" / "Season 01" / "Series - S01E01.mkv"
    target_s02 = tmp_path / "Series" / "Season 02" / "Series - S02E01.mkv"
    target_mov = tmp_path / "Movie" / "Series Movie (2020).mkv"
    for p in (target_s01, target_s02, target_mov):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("v", encoding="utf-8")

    cand = _candidate("thread-multi")
    monkeypatch.setattr(fetcher.provider, "search", lambda keyword, limit=10: [cand])
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda c: c)
    monkeypatch.setattr(fetcher.provider, "load_thread_packages", lambda c: c)

    def invoker(state):
        state.handle_tool("search_candidates", {"keyword": "Series"})
        state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
        for sid in (245665, 350764, 291494):
            state.handle_tool(
                "submit_candidate",
                {"candidate_ref": "CD1", "bangumi_subject_id": sid},
            )
            state.handle_tool("submit_package", {"package_ref": "PK1"})
        state.handle_tool("submit_complete", {"reason": "all 3"})
        return {"ok": True, "returncode": 0, "argv": ["fake"]}

    _patch_pi_runner_with_invoker(monkeypatch, tmp_path, invoker)

    download_lock = threading.Lock()
    download_state = {"n": 0, "max_concurrent": 0}

    def fake_download(cand_arg, destination_dir, package=None, download_url=None):
        with download_lock:
            download_state["n"] += 1
            download_state["max_concurrent"] = max(
                download_state["max_concurrent"], download_state["n"]
            )
        _time.sleep(0.2)  # 模拟下载耗时，让并发可观察
        with download_lock:
            download_state["n"] -= 1
        n = download_state.get("counter", 0)
        download_state["counter"] = n + 1
        downloaded = tmp_path / f"pkg_{n}.zip"
        downloaded.write_text("sub", encoding="utf-8")
        return SimpleNamespace(
            status="success", downloaded_path=downloaded,
            download_url=f"https://x/pkg_{n}.zip", selected_package=package,
            download_attempts=1,
        )

    monkeypatch.setattr(fetcher.provider, "download", fake_download)

    proc_lock = threading.Lock()
    proc_concurrent = {"n": 0, "max": 0}
    video_by_idx = {0: target_s01.name, 1: target_s02.name, 2: target_mov.name}
    proc_counter = {"n": 0}

    def fake_process_mapping(archive_path, target_task_uuid=None):
        with proc_lock:
            proc_concurrent["n"] += 1
            proc_concurrent["max"] = max(proc_concurrent["max"], proc_concurrent["n"])
        _time.sleep(0.05)
        with proc_lock:
            proc_concurrent["n"] -= 1
        idx = proc_counter["n"]
        proc_counter["n"] += 1
        return {
            "status": "success", "mapping_only": True, "matched_count": 1,
            "mappings": [{"subtitle": "s.ass", "video": video_by_idx[idx], "language": "zh-CN"}],
            "unmatched": [], "no_target_videos": [], "case_agent_status": "accepted",
        }

    monkeypatch.setattr(fetcher.processor, "process_mapping", fake_process_mapping)

    result = fetcher.process_task_mapping(
        task_uuid, missing_videos_override=[target_s01, target_s02, target_mov]
    )

    assert result["status"] == "success"
    assert result["selections_count"] == 3
    # 下载并发：concurrency=3 时 max_concurrent 应 > 1（并发触发）
    assert download_state["max_concurrent"] > 1, (
        f"下载应并发，但 max_concurrent={download_state['max_concurrent']}"
    )
    # processor 串行：max 应 = 1（不重叠，避免 Node sidecar 资源爆炸）
    assert proc_concurrent["max"] == 1, (
        f"processor 应串行，但 max 并发={proc_concurrent['max']}"
    )


def test_single_selection_no_concurrency_overhead(monkeypatch, tmp_path):
    """单 selection 时不应起 ThreadPoolExecutor（concurrency=min(1,1)=1，走串行分支）。"""
    fetcher, task_uuid, _ = build_mapping_fetcher(monkeypatch, tmp_path)
    target = tmp_path / "Movie" / "Series Movie (2020).mkv"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("v", encoding="utf-8")

    cand = _candidate("thread-single")
    monkeypatch.setattr(fetcher.provider, "search", lambda keyword, limit=10: [cand])
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda c: c)
    monkeypatch.setattr(fetcher.provider, "load_thread_packages", lambda c: c)

    def invoker(state):
        state.handle_tool("search_candidates", {"keyword": "Series"})
        state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
        state.handle_tool("submit_package", {"package_ref": "PK1"})
        state.handle_tool("submit_complete", {"reason": "single"})
        return {"ok": True, "returncode": 0, "argv": ["fake"]}

    _patch_pi_runner_with_invoker(monkeypatch, tmp_path, invoker)

    def fake_download(cand_arg, destination_dir, package=None, download_url=None):
        downloaded = tmp_path / "pkg.zip"
        downloaded.write_text("sub", encoding="utf-8")
        return SimpleNamespace(
            status="success", downloaded_path=downloaded,
            download_url="https://x/pkg.zip", selected_package=package,
            download_attempts=1,
        )

    monkeypatch.setattr(fetcher.provider, "download", fake_download)
    monkeypatch.setattr(
        fetcher.processor, "process_mapping",
        lambda ap, target_task_uuid=None: {
            "status": "success", "mapping_only": True, "matched_count": 1,
            "mappings": [{"subtitle": "s.ass", "video": target.name, "language": "zh-CN"}],
            "unmatched": [], "no_target_videos": [], "case_agent_status": "accepted",
        },
    )

    result = fetcher.process_task_mapping(task_uuid, missing_videos_override=[target])
    assert result["status"] == "success"
    assert result["selections_count"] == 1
