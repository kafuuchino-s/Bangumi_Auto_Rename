"""list_task_rows 列表去重口径单测。

防回归：同路径的多次任务（失败→重试→成功）是不同业务事件，必须按 uuid
各自独立成行，不能按 path 合并去重——否则按 mtime 倒序遍历时旧失败记录
会覆盖新成功记录，列表只剩一条（曾出现的 bug）。

同时覆盖：
- status 归一成短词（成功/失败），供 StatusBadge 精确匹配；完整 error 不进列表行。
- failure_reason_label 人话短句。
- type==subtitle 的任务跳过。
- path 为空跳过。
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.api import serializers


class _FakeTaskFile:
    """模拟 TASK_PATH.iterdir() 返回的 Path-like（有 stem + stat().st_mtime）。"""

    def __init__(self, stem: str, mtime: int):
        self._stem = stem
        self._mtime = mtime

    @property
    def stem(self) -> str:
        return self._stem

    def stat(self):
        class _S:
            st_mtime = self._mtime

        return _S()


def _patch_list_io(monkeypatch, task_files, task_data_by_uuid, active_tasks=None):
    """patch get_task / TASK_PATH.iterdir / queue_mgr.list_active_tasks。"""
    monkeypatch.setattr(serializers, "get_task", lambda uuid: task_data_by_uuid.get(uuid, {}))

    class _FakeTaskPath:
        exists = staticmethod(lambda: True)
        iterdir = staticmethod(lambda: list(task_files))

    monkeypatch.setattr(serializers, "TASK_PATH", _FakeTaskPath())

    class _FakeQueue:
        def list_active_tasks(self):
            return active_tasks or []

        def get_queue_position(self, _path):
            return 1

        def get_path_status(self, _path):
            return None

    monkeypatch.setattr(
        serializers, "get_queue_manager", lambda: _FakeQueue()
    )


def test_same_path_multiple_tasks_all_shown(monkeypatch):
    """同路径 3 条任务（旧失败 + 两次成功）必须都显示，不按 path 合并。"""
    path = "/media/Anime/X"
    files = [
        _FakeTaskFile("uuid-newest", 300),   # 最新：成功
        _FakeTaskFile("uuid-middle", 200),   # 成功
        _FakeTaskFile("uuid-oldest", 100),   # 最旧：失败
    ]
    data = {
        "uuid-newest": {"uuid": "uuid-newest", "path": path, "name": "X", "transferred_file_count": 18},
        "uuid-middle": {"uuid": "uuid-middle", "path": path, "name": "X", "transferred_file_count": 18},
        "uuid-oldest": {
            "uuid": "uuid-oldest",
            "path": path,
            "name": "",
            "failure_reason": "local_bangumi_case_agent_primary",
            "error": "[Case Agent] ...fail_closed...",
        },
    }
    _patch_list_io(monkeypatch, files, data)

    rows = serializers.list_task_rows()
    uuids = {r["uuid"] for r in rows}
    assert uuids == {"uuid-newest", "uuid-middle", "uuid-oldest"}, (
        f"同路径多任务应各自独立成行，实际: {uuids}"
    )
    # 旧失败不被新成功覆盖
    by_uuid = {r["uuid"]: r for r in rows}
    assert by_uuid["uuid-oldest"]["status"] == "失败"
    assert by_uuid["uuid-newest"]["status"] == "成功"
    assert by_uuid["uuid-middle"]["status"] == "成功"


def test_status_normalized_to_short_word_not_full_error(monkeypatch):
    """列表 status 归一成短词（失败），完整 error 不进列表行（详情页展示）。"""
    files = [_FakeTaskFile("uuid-fail", 100)]
    data = {
        "uuid-fail": {
            "uuid": "uuid-fail",
            "path": "/media/X",
            "name": "X",
            "failure_reason": "local_bangumi_case_agent_primary",
            "error": "[Case Agent] Local->Bangumi mapping-only result written: ...fail_closed...; mapping-only phase stops before TMDB, final filename, move, and Emby",
        },
    }
    _patch_list_io(monkeypatch, files, data)

    rows = serializers.list_task_rows()
    assert len(rows) == 1
    r = rows[0]
    # 短词，供 StatusBadge 精确匹配
    assert r["status"] == "失败"
    # 完整 error 原文不得塞进 status
    assert "fail_closed" not in r["status"]
    assert "mapping-only" not in r["status"]
    # 人话短句
    assert r["failure_reason_label"] == "Case Agent 映射未通过合同校验"


def test_success_task_status_word(monkeypatch):
    """无 error/failure_reason 的任务 status='成功'。"""
    files = [_FakeTaskFile("uuid-ok", 100)]
    data = {
        "uuid-ok": {
            "uuid": "uuid-ok",
            "path": "/media/X",
            "name": "X",
            "transferred_file_count": 12,
            "error": "",
        },
    }
    _patch_list_io(monkeypatch, files, data)

    rows = serializers.list_task_rows()
    assert rows[0]["status"] == "成功"
    assert rows[0]["failure_reason_label"] == ""


def test_subtitle_type_task_skipped(monkeypatch):
    """type=='subtitle' 的任务不进列表行（字幕子任务单独展示）。"""
    files = [_FakeTaskFile("uuid-sub", 100), _FakeTaskFile("uuid-main", 200)]
    data = {
        "uuid-sub": {"uuid": "uuid-sub", "path": "/media/X", "type": "subtitle"},
        "uuid-main": {"uuid": "uuid-main", "path": "/media/X", "name": "X"},
    }
    _patch_list_io(monkeypatch, files, data)

    rows = serializers.list_task_rows()
    uuids = {r["uuid"] for r in rows}
    assert uuids == {"uuid-main"}


def test_empty_path_skipped(monkeypatch):
    """path 为空的任务跳过。"""
    files = [_FakeTaskFile("uuid-nopath", 100), _FakeTaskFile("uuid-ok", 200)]
    data = {
        "uuid-nopath": {"uuid": "uuid-nopath", "path": "", "name": "X"},
        "uuid-ok": {"uuid": "uuid-ok", "path": "/media/X", "name": "X"},
    }
    _patch_list_io(monkeypatch, files, data)

    rows = serializers.list_task_rows()
    uuids = {r["uuid"] for r in rows}
    assert uuids == {"uuid-ok"}
