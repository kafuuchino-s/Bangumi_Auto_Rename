"""auto_fetch 空 record 处理单测。

验证：当 record 文件内容为空（{}，如全跳过任务）但 task_data.target_root
指向已落地视频目录时，auto_fetch 仍应扫描实际目录、给缺字幕的视频抓字幕——
这正是 auto_fetch 的目的。空 record 不应被伪装成 record_not_found。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.subtitle.auto_fetch import SubtitleAutoFetcher


def _make_series_root(tmp_path: Path, video_stems):
    """构造 series root 下 Season 01 目录，每个 stem 一个无字幕视频。"""
    root = tmp_path / "library" / "Show (2024)"
    season = root / "Season 01"
    season.mkdir(parents=True, exist_ok=True)
    for stem in video_stems:
        (season / f"{stem}.mkv").write_bytes(b"fake")
    return root


def _patch_get_task_record(monkeypatch, task_uuid, task_data, record_data, tmp_path):
    """mock get_task/get_record，并把 write_task + TASK_PATH 指向 tmp_path，
    避免 _persist_status 写污染生产 data/task 目录。"""
    monkeypatch.setattr(
        "src.subtitle.auto_fetch.get_task", lambda uuid: task_data
    )
    monkeypatch.setattr(
        "src.subtitle.auto_fetch.get_record", lambda uuid: record_data
    )
    monkeypatch.setattr("src.subtitle.auto_fetch.write_task", lambda *a, **k: None)
    isolated = tmp_path / "task_isolated"
    isolated.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("src.utils.utils.TASK_PATH", isolated)
    monkeypatch.setattr("src.subtitle.auto_fetch.TASK_PATH", isolated)


def test_empty_record_with_target_root_still_scans_library(monkeypatch, tmp_path):
    """空 record + 有 target_root 且目录有无字幕视频 → 继续走抓取流程，
    不被 record_not_found / no_landed_videos 闸门挡住。"""
    task_uuid = "empty-record-series"
    root = _make_series_root(tmp_path, ["S01E01", "S01E02"])
    task_data = {
        "task_uuid": task_uuid,
        "is_movie": False,
        "target_root": str(root),
    }
    _patch_get_task_record(monkeypatch, task_uuid, task_data, {}, tmp_path)

    # 短路 Pi：让 case agent 直接 fail_closed，验证前面 scope+collect 真的走到了
    captured = {}

    def fake_entry(*, workspace, candidates, task_data, backend, provider):
        missing = list(workspace.missing_videos)
        captured["missing"] = missing
        captured["scope_type"] = (
            workspace.scan_scope.scope_type if workspace.scan_scope else None
        )
        return {
            "status": "fail_closed",
            "reason_kind": "pi_fail_closed",
            "snapshot": {},
        }

    monkeypatch.setattr(
        "src.subtitle.auto_fetch.run_auto_fetch_case_agent", fake_entry
    )
    fetcher = SubtitleAutoFetcher()
    result = fetcher.process_task(task_uuid)

    # fail_closed 是合格业务结果，状态落到 skipped/no_usable_candidate
    assert result["status"] in ("skipped", "failed")
    assert result.get("reason") in ("no_usable_candidate", "pi_fail_closed")
    # 关键：2 个无字幕视频被扫到，证明 scope+collect 走通了，没被空 record 挡住
    assert len(captured.get("missing") or []) == 2


def test_empty_record_no_target_root_returns_no_landed_videos(monkeypatch, tmp_path):
    """空 record + 无 target_root（task scope，依赖 record 遍历）→ no_landed_videos。"""
    task_uuid = "empty-record-task-scope"
    task_data = {"task_uuid": task_uuid, "is_movie": False}
    _patch_get_task_record(monkeypatch, task_uuid, task_data, {}, tmp_path)

    fetcher = SubtitleAutoFetcher()
    result = fetcher.process_task(task_uuid)

    assert result["status"] == "skipped"
    assert result["reason"] == "no_landed_videos"


def test_missing_record_still_returns_record_not_found(monkeypatch, tmp_path):
    """record 文件不存在时仍保持 record_not_found 语义。"""
    task_uuid = "missing-record-task"
    _patch_get_task_record(
        monkeypatch, task_uuid,
        {"task_uuid": task_uuid, "is_movie": False},
        None,
        tmp_path,
    )

    fetcher = SubtitleAutoFetcher()
    result = fetcher.process_task(task_uuid)

    assert result["status"] == "skipped"
    assert result["reason"] == "record_not_found"
