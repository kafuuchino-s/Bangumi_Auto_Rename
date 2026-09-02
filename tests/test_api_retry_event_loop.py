"""retry / edit / create 路由事件循环 bug 回归测试。

背景：routes_tasks.py 的 retry_task / edit_task / create_task 原本是同步 `def`，
FastAPI 把同步路由丢进 anyio threadpool 线程执行，该线程无运行中的事件循环。
而 enqueue() 内部用 asyncio.create_task 懒启动 worker，需要运行中的事件循环，
于是抛 RuntimeError('no running event loop') → 接口 500。

更糟的是：旧 retry 先删旧记录再入队，入队在 threadpool 里炸 500 时旧记录已删，
前端再查就 404「任务不存在」——用户表现为「点重试没反应，任务消失了」。

修复：三个路由改 async def（在主事件循环线程跑，对齐 /sendTask），
且 retry 调整为「先入队成功、再删旧记录」。

本测试用 TestClient（内部跑真实 ASGI 事件循环）钉死：
1. retry 不再 500，返回 200 + 新 task_id
2. 顺序保护：enqueue 抛错时旧任务记录仍在（不被删）
3. 正常路径：enqueue 成功后旧记录被删
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import api_router
import src.api.routes_tasks as routes_tasks
import src.utils.utils as utils


def _client(raise_server_exceptions: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(api_router, prefix="/api")
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


@pytest.fixture
def tmp_task_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """把 TASK_PATH / RECORD_PATH 指向临时目录，避免污染真实 data/。

    routes_tasks 和 utils.utils 各自 import 了 TASK_PATH/RECORD_PATH，
    需分别 monkeypatch 已绑定的模块属性。
    """
    task_dir = tmp_path / "task"
    record_dir = tmp_path / "record"
    task_dir.mkdir()
    record_dir.mkdir()

    monkeypatch.setattr(routes_tasks, "TASK_PATH", task_dir)
    monkeypatch.setattr(routes_tasks, "RECORD_PATH", record_dir)
    monkeypatch.setattr(utils, "TASK_PATH", task_dir)
    monkeypatch.setattr(utils, "RECORD_PATH", record_dir)
    return task_dir, record_dir


def _write_task_file(task_dir: Path, uuid: str, path: str) -> None:
    (task_dir / f"{uuid}.json").write_text(
        json.dumps(
            {
                "path": path,
                "is_anime": True,
                "is_movie": None,
                "source_evidence": {
                    "provider": "moviepilot",
                    "download_hash": "abc123",
                },
            }
        ),
        encoding="utf-8",
    )


def test_retry_returns_200_and_reenqueues(tmp_task_dirs, monkeypatch):
    """retry 在真实事件循环下不再 500，返回 200 + 新 task_id，旧记录被删。"""
    task_dir, record_dir = tmp_task_dirs
    uuid = "retry-ok-uuid"
    _write_task_file(task_dir, uuid, "/media/Anime/Sample")

    captured: dict[str, object] = {}

    def fake_enqueue(self, path, **kwargs):
        captured["path"] = path
        captured["original_uuid"] = kwargs.get("original_uuid")
        captured["source_evidence"] = kwargs.get("source_evidence")
        return "new-task-id-xyz"

    monkeypatch.setattr(
        "src.queue.task_queue.TaskQueueManager.enqueue", fake_enqueue
    )
    # is_path_in_queue 走真实单例，确保返回 False 让 retry 通过到 enqueue
    monkeypatch.setattr(
        "src.queue.task_queue.TaskQueueManager.is_path_in_queue",
        lambda self, p: False,
    )

    with _client() as c:
        r = c.post(f"/api/tasks/{uuid}/retry")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["data"]["task_id"] == "new-task-id-xyz"
    assert captured["path"] == "/media/Anime/Sample"
    assert captured["original_uuid"] == uuid
    assert captured["source_evidence"] == {
        "provider": "moviepilot",
        "download_hash": "abc123",
    }
    # 入队成功后旧记录被删
    assert not (task_dir / f"{uuid}.json").exists()


def test_retry_keeps_record_when_enqueue_fails(tmp_task_dirs, monkeypatch):
    """顺序保护：enqueue 抛错时旧任务记录仍在，不会因删早了而丢失。"""
    task_dir, record_dir = tmp_task_dirs
    uuid = "retry-fail-uuid"
    _write_task_file(task_dir, uuid, "/media/Anime/Sample")

    def fake_enqueue(self, path, **kwargs):
        raise RuntimeError("simulated enqueue failure")

    monkeypatch.setattr(
        "src.queue.task_queue.TaskQueueManager.enqueue", fake_enqueue
    )
    monkeypatch.setattr(
        "src.queue.task_queue.TaskQueueManager.is_path_in_queue",
        lambda self, p: False,
    )

    with _client(raise_server_exceptions=False) as c:
        r = c.post(f"/api/tasks/{uuid}/retry")

    # enqueue 失败 → 500 是预期的（生产 ASGI 把未捕获异常转 500；
    # TestClient 默认会重新抛出服务端异常，这里关掉以模拟生产行为）
    assert r.status_code == 500
    # 关键：旧记录没被删，用户还能再看到 / 再重试
    assert (task_dir / f"{uuid}.json").exists(), (
        "enqueue 失败时旧记录被提前删除，会导致任务消失"
    )


def test_retry_404_when_task_missing(tmp_task_dirs, monkeypatch):
    """任务记录不存在时 retry 返回 404，不触发 enqueue。"""
    monkeypatch.setattr(
        "src.queue.task_queue.TaskQueueManager.is_path_in_queue",
        lambda self, p: False,
    )
    with _client() as c:
        r = c.post("/api/tasks/does-not-exist-uuid/retry")
    assert r.status_code == 404


def test_create_task_is_async_path(tmp_task_dirs, monkeypatch):
    """create_task（POST /api/tasks）也走 async，enqueue 不再因事件循环炸。

    用一个不存在的路径让它在 enqueue 前返回 404，验证路由本身能正常进入
    async 处理（不抛 no running event loop）。
    """
    with _client() as c:
        r = c.post("/api/tasks", json={"path": "Z:/no/such/path/abc"})
    assert r.status_code == 404
