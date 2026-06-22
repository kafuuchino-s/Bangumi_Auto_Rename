"""auto_fetch 映射模式单测（L2：搜帖→选包→下载临时→processor mapping_only 不落媒体库）。

auto_fetch 选帖/选包统一走 Pi evidence-driven 后端（single_shot 已移除）。
本套件验证 process_task_mapping：

- missing_videos_override 路径：虚拟目标路径（视频文件不必 exists）也能跑通选帖。
- 下载到 auto_fetch_mapping 临时目录（非生产 auto_fetch 目录）。
- 调 processor.process_mapping（mapping_only=True），不是 processor.process。
- 产物写 .subtitle_fetch_mapping.json（非生产 .subtitle_fetch.json）。
- 搜不到候选 → Pi fail_closed → failed（合格不落盘）。

验证 processor.process_mapping / _land_compiled_plan mapping_only 分叉：
- accepted 返回 mapping_only=True + mappings，不调 trans_file 落媒体库。

**不真起 Pi sidecar / 不发真实 AI**：通过 monkeypatch
``pi_runner.run_auto_fetch_case_agent_pi`` 注入 ``runtime_invoker``，直接调
``state.handle_tool`` 编排 tool_call 序列，provider 用 fake。范式见
``tests/test_auto_fetch_case_agent_pi_runner.py::test_entry_pi_backend_accepted_returns_four_state``。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.subtitle.auto_fetch import SubtitleAutoFetcher
from src.subtitle.processor import SubtitleProcessor
from src.subtitle.providers import SubtitleCandidate, SubtitleThreadPackage


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


def make_package(package_id, flags, *, has_direct_download=True):
    return SubtitleThreadPackage(
        package_id=package_id,
        page_number=1,
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


def build_mapping_fetcher(monkeypatch, tmp_path):
    """构造映射模式 fetcher：task/record 指向虚拟路径（不创建视频文件）。"""
    fetcher = SubtitleAutoFetcher()
    task_uuid = "map-task-1"
    # 合成的 TMDB 目标路径（视频文件不创建——映射模式不要求 exists）
    target_movie = tmp_path / "Movie" / "Omoide no Mani (2014)" / "Omoide no Mani (2014).mkv"
    monkeypatch.setattr(
        "src.subtitle.auto_fetch.get_task",
        lambda uuid: {
            "uuid": uuid,
            "name": "Omoide no Mani",
            "tmdb_name": "Omoide no Mani",
            "path": str(tmp_path / "Omoide.no.Mani.2014.BluRay.1080p.FLAC.x265-MGRT.mkv"),
            "season_id": 1,
            "is_movie": True,
            "target_root": str(target_movie.parent),
        },
    )
    monkeypatch.setattr(
        "src.subtitle.auto_fetch.get_record",
        lambda uuid: {
            str(tmp_path / "Omoide.no.Mani.2014.BluRay.1080p.FLAC.x265-MGRT.mkv"): str(
                target_movie
            )
        },
    )
    return fetcher, task_uuid, target_movie


def test_process_task_mapping_uses_override_and_temp_download(monkeypatch, tmp_path):
    """mapping 模式：missing_videos_override 跳过采集，Pi accepted → 下载到
    auto_fetch_mapping 临时目录，调 processor.process_mapping，产物写
    .subtitle_fetch_mapping.json。"""
    fetcher, task_uuid, target_movie = build_mapping_fetcher(monkeypatch, tmp_path)

    candidate = SubtitleCandidate(
        title="thread-1",
        detail_url="https://bbs.acgrip.com/thread-1",
        source="acgrip",
    )
    candidate.thread_packages = [make_package("batch", ["batch", "simplified"])]
    candidate.pages_scanned = 1

    monkeypatch.setattr(fetcher.provider, "search", lambda keyword, limit=10: [candidate])
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda c: c)
    monkeypatch.setattr(fetcher.provider, "load_thread_packages", lambda c: c)

    def invoker(state):
        state.handle_tool("search_candidates", {"keyword": "Omoide no Mani"})
        state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
        state.handle_tool("submit_package", {"package_ref": "PK1", "reason": "main batch"})
        return {"ok": True, "returncode": 0, "argv": ["fake"]}

    _patch_pi_runner_with_invoker(monkeypatch, tmp_path, invoker)

    download_calls: dict[str, Any] = {}

    def fake_download(cand, destination_dir, package=None, download_url=None):
        download_calls["dest"] = Path(destination_dir)
        downloaded = tmp_path / "map_picked.zip"
        downloaded.write_text("subtitle", encoding="utf-8")
        return SimpleNamespace(
            status="success",
            downloaded_path=downloaded,
            download_url="https://example.com/map_picked.zip",
            selected_package=package,
        )

    monkeypatch.setattr(fetcher.provider, "download", fake_download)

    processor_calls: dict[str, Any] = {}

    def fake_process_mapping(archive_path, target_task_uuid=None):
        processor_calls["mapping_only"] = True
        processor_calls["archive"] = Path(archive_path)
        return {
            "status": "success",
            "mapping_only": True,
            "mappings": [
                {
                    "subtitle": "sub.ass",
                    "video": target_movie.name,
                    "target": "Omoide no Mani (2014).zh-CN.default.ass",
                    "task_uuid": target_task_uuid,
                    "task_title": "Omoide no Mani",
                    "language": "zh-CN",
                    "sync_status": "disabled",
                }
            ],
            "unmatched": [],
        }

    monkeypatch.setattr(fetcher.processor, "process_mapping", fake_process_mapping)
    # 确保 process（生产落盘）绝不被调用
    monkeypatch.setattr(
        fetcher.processor, "process",
        lambda *a, **k: pytest.fail("processor.process 不应在 mapping 模式被调用"),
    )

    # missing_videos_override：虚拟目标路径，视频文件不存在
    result = fetcher.process_task_mapping(
        task_uuid, missing_videos_override=[target_movie]
    )

    assert result["status"] == "success"
    assert result["pipeline_mode"] == "auto_fetch_case_agent_primary"
    # 下载到 auto_fetch_mapping 临时目录
    assert "auto_fetch_mapping" in str(download_calls["dest"])
    # 调的是 process_mapping，不是 process
    assert processor_calls.get("mapping_only") is True
    # 多 selection 合并格式：单 selection 走兼容路径，selections[0].processor_result
    # 透传 mapping_only + mappings；顶层 mappings 为合并汇总
    assert result["selections_count"] == 1
    assert result["selections"][0]["processor_result"]["mapping_only"] is True
    assert result["selections"][0]["processor_result"]["mappings"][0]["video"] == target_movie.name
    # 顶层合并 mappings（matched_count 取 processor matched_count，fake 未设→0）
    assert result["mappings"][0]["video"] == target_movie.name
    assert len(result["mappings"]) == 1


def test_process_task_mapping_skip_when_override_empty(monkeypatch, tmp_path):
    """missing_videos_override 为空列表 → skip(subtitle_already_exists)，不进选帖循环。"""
    fetcher, task_uuid, _ = build_mapping_fetcher(monkeypatch, tmp_path)
    monkeypatch.setattr(
        fetcher.provider, "search",
        lambda *a, **k: pytest.fail("search 不应在无 missing_videos 时被调用"),
    )
    result = fetcher.process_task_mapping(task_uuid, missing_videos_override=[])
    assert result["status"] == "skipped"
    assert result["reason"] == "subtitle_already_exists"


def test_process_task_mapping_no_candidates_fail_closed(monkeypatch, tmp_path):
    """Pi 搜不到候选 → fail_closed → failed（合格不落盘），reason=pi_fail_closed。"""
    fetcher, task_uuid, target_movie = build_mapping_fetcher(monkeypatch, tmp_path)
    monkeypatch.setattr(fetcher.provider, "search", lambda keyword, limit=10: [])
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda c: c)
    monkeypatch.setattr(fetcher.provider, "load_thread_packages", lambda c: c)

    def invoker(state):
        state.handle_tool("search_candidates", {"keyword": "Omoide no Mani"})
        state.handle_tool(
            "fail_closed",
            {"reason": "no candidates", "reason_kind": "no_candidates"},
        )
        return {"ok": True, "returncode": 0, "argv": ["fake"]}

    _patch_pi_runner_with_invoker(monkeypatch, tmp_path, invoker)
    monkeypatch.setattr(
        fetcher.provider, "download",
        lambda *a, **k: pytest.fail("download should not be called on fail_closed"),
    )

    result = fetcher.process_task_mapping(
        task_uuid, missing_videos_override=[target_movie]
    )
    # _execute_fetch: fail_closed/need_confirm -> skipped（合格，不落盘）
    assert result["status"] == "skipped"
    assert result["case_agent_status"] == "fail_closed"
    # _run_pi_backend fail_closed 归一 reason_kind='pi_fail_closed'
    assert result["reason"] == "pi_fail_closed"


# ---------------------------------------------------------------------------
# processor.process_mapping / _land_compiled_plan mapping_only 分叉
# ---------------------------------------------------------------------------


def test_processor_process_mapping_returns_mapping_not_land(monkeypatch, tmp_path):
    """processor.process_mapping（mapping_only=True）：accepted 返回 mapping_only=True +
    mappings，不调 trans_file 落媒体库。

    用真实 SubtitleProcessor 但 mock 掉 Case Agent entry 返回 accepted compiled_plan，
    验证 _land_compiled_plan 在 mapping_only 分叉跳过 trans_file。
    """
    processor = SubtitleProcessor()

    # 构造一个虚拟字幕包（zip 含一个 .ass）
    archive = tmp_path / "sub.zip"
    _make_zip_with_ass(archive, "Omoide no Mani (2014).zh-CN.ass")

    # mock Case Agent entry 直接返回 accepted + compiled_plan
    # compiled_plan 需 .mappings（list，每个含 subtitle_archive_path/task_uuid/video/
    # emby_lang/is_simplified）+ .unmatched_refs + .model_dump（_land_compiled_plan 遍历用）
    fake_compiled_mapping = SimpleNamespace(
        subtitle_archive_path="Omoide no Mani (2014).zh-CN.ass",
        task_uuid="map-task-1",
        video="Omoide no Mani (2014).mkv",
        emby_lang="zh-CN",
        is_simplified=True,
    )
    fake_compiled = SimpleNamespace(
        mappings=[fake_compiled_mapping],
        unmatched_refs=[],
        model_dump=lambda mode="json": {
            "mappings": [
                {
                    "subtitle_archive_path": "Omoide no Mani (2014).zh-CN.ass",
                    "task_uuid": "map-task-1",
                    "video": "Omoide no Mani (2014).mkv",
                    "emby_lang": "zh-CN",
                    "is_simplified": True,
                }
            ],
            "unmatched_refs": [],
            "summary": "fake plan",
        },
    )

    def fake_entry(*, subtitle_files, processed_tasks, ai_client, source_path,
                   language_resolver, archive_name, archive_structure):
        return {
            "status": "accepted",
            "snapshot": {"draft": {"confidence": "High"}},
            "compiled_plan": fake_compiled,
        }

    monkeypatch.setattr(
        "src.subtitle.case_agent.run_subtitle_case_agent_mapping", fake_entry
    )

    # mock processed_tasks 加载（绕开真实 record 文件）
    target_movie = tmp_path / "Movie" / "Omoide no Mani (2014)" / "Omoide no Mani (2014).mkv"
    fake_processed_task = {
        "uuid": "map-task-1",
        "title": "Omoide no Mani",
        "year": 2014,
        "season": None,
        "target_dir": str(target_movie.parent),
        "target_root": str(target_movie.parent.parent),
        "videos": [target_movie.name],
        "video_targets": {target_movie.name: str(target_movie)},
        "source_videos": {target_movie.name: "Omoide.no.Mani.2014.BluRay.1080p.FLAC.x265-MGRT.mkv"},
        "is_movie": True,
    }
    monkeypatch.setattr(
        processor, "_resolve_processed_tasks",
        lambda *, target_task_uuid: ([fake_processed_task], ""),
    )

    # 拦截 trans_file：mapping_only 不应调用它
    trans_called = {"yes": False}

    class FakeTrans:
        def __init__(self, *a, **k):
            pass

        def trans_file(self):
            trans_called["yes"] = True
            return None

    monkeypatch.setattr("src.subtitle.processor.Trans", FakeTrans)

    result = processor.process_mapping(archive, target_task_uuid="map-task-1")

    assert result["status"] == "success"
    assert result["mapping_only"] is True
    assert result["mappings"][0]["video"] == target_movie.name
    assert result["mappings"][0]["language"] == "zh-CN"
    # 关键：不落盘
    assert trans_called["yes"] is False
    # 媒体库目标文件不存在（未落盘）
    assert not (target_movie.parent / "Omoide no Mani (2014).zh-CN.default.ass").exists()


def _make_zip_with_ass(zip_path: Path, ass_name: str) -> None:
    """构造一个含单个 .ass 字幕的 zip 包（processor.extractor 能解压）。"""
    import zipfile

    ass_content = "[Script Info]\nTitle: test\n"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(ass_name, ass_content)
