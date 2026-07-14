"""/sendTask 白名单标签准入回归测试。"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import src.api.routes_tasks as routes_tasks
import src.web as web
from src.config.config_manager import CN_MAP, CONFIG_DEFAULT


class _FakeQueue:
    def __init__(self) -> None:
        self.enqueued: list[dict[str, object]] = []

    def is_path_in_queue(self, path: str) -> bool:
        return False

    def enqueue(
        self,
        *,
        path: str,
        is_anime: bool | None,
        is_movie: bool | None,
    ) -> str:
        self.enqueued.append(
            {
                "path": path,
                "is_anime": is_anime,
                "is_movie": is_movie,
            }
        )
        return "test-task-id"


def _patch_webhook_dependencies(
    monkeypatch,
    *,
    allowed_tags: str = "",
    skip_tags: str = "",
) -> _FakeQueue:
    queue = _FakeQueue()
    config = {
        "allowed_tags": allowed_tags,
        "skip_tags": skip_tags,
        "host_path_prefix": "",
        "docker_mnt": "/media",
    }

    monkeypatch.setattr(web.cm, "get_config", lambda key: config.get(key, ""))
    monkeypatch.setattr(web, "get_queue_manager", lambda: queue)
    return queue


def _send_task(path: Path, **form: str) -> tuple[int, dict[str, object]]:
    with TestClient(web.app) as client:
        response = client.post("/sendTask", data={"path": str(path), **form})
    return response.status_code, response.json()


def test_allowed_tags_empty_keeps_untagged_webhook_compatible(
    tmp_path: Path, monkeypatch
):
    """白名单留空时，无标签 webhook 保持历史上可入队的行为。"""
    queue = _patch_webhook_dependencies(monkeypatch, allowed_tags="")

    status, body = _send_task(tmp_path)

    assert status == 200
    assert body["code"] == 200
    assert len(queue.enqueued) == 1


def test_allowed_tags_accept_exact_normalized_matches(
    tmp_path: Path, monkeypatch
):
    """逗号、空格和大小写归一后，任一精确标签命中即可入队。"""
    queue = _patch_webhook_dependencies(
        monkeypatch, allowed_tags="动漫,电影,tv"
    )

    status, body = _send_task(tmp_path, tag=" Other, TV ")

    assert status == 200
    assert body["code"] == 200
    assert queue.enqueued == [
        {
            "path": str(tmp_path),
            "is_anime": None,
            "is_movie": None,
        }
    ]


def test_allowed_tags_rejects_missing_or_partial_tags_before_path_check(
    tmp_path: Path, monkeypatch
):
    """启用后无标签或近似标签都不能放行，也不会走到路径检查。"""
    queue = _patch_webhook_dependencies(monkeypatch, allowed_tags="动漫,电影,tv")
    nonexistent_path = tmp_path / "does-not-exist"

    for tag in ("", "动漫电影"):
        status, body = _send_task(nonexistent_path, tag=tag)
        assert status == 200
        assert body["code"] == 202
        assert "未命中允许标签" in str(body["data"])

    assert queue.enqueued == []


def test_skip_tag_takes_precedence_over_allowed_tag(tmp_path: Path, monkeypatch):
    """同时命中白名单与跳过标签时，跳过规则必须优先。"""
    queue = _patch_webhook_dependencies(
        monkeypatch,
        allowed_tags="动漫,电影,tv",
        skip_tags="reseed",
    )

    status, body = _send_task(tmp_path, tag="动漫,reseed")

    assert status == 200
    assert body["code"] == 202
    assert "命中跳过标签：reseed" in str(body["data"])
    assert queue.enqueued == []


def test_explicit_no_process_takes_precedence_over_whitelist(
    tmp_path: Path, monkeypatch
):
    """调用方显式 no_process 不受白名单命中与否影响。"""
    queue = _patch_webhook_dependencies(monkeypatch, allowed_tags="动漫")

    status, body = _send_task(tmp_path, tag="电影", no_process="true")

    assert status == 200
    assert body["code"] == 202
    assert "no_process 已启用" in str(body["data"])
    assert queue.enqueued == []


def test_allowed_movie_tag_keeps_existing_movie_classification(
    tmp_path: Path, monkeypatch
):
    """白名单只决定准入，不改变获准任务的既有媒体分类。"""
    queue = _patch_webhook_dependencies(monkeypatch, allowed_tags="电影")

    status, body = _send_task(tmp_path, tag="电影")

    assert status == 200
    assert body["code"] == 200
    assert queue.enqueued[0]["is_anime"] is None
    assert queue.enqueued[0]["is_movie"] is True


def test_allowed_tags_config_and_field_spec_are_exposed():
    """白名单默认关闭，且配置 API 向前端下发可渲染的字段元数据。"""
    assert CONFIG_DEFAULT["allowed_tags"] == ""
    assert CN_MAP["allowed_tags"] == "仅处理标签（白名单，逗号分隔）"

    with TestClient(web.app) as client:
        response = client.get("/api/config/field-spec")

    assert response.status_code == 200
    entry = next(
        item
        for item in response.json()["field_spec"]
        if item["key"] == "allowed_tags"
    )
    assert entry["label"] == CN_MAP["allowed_tags"]
    assert entry["control"] == "input"
    assert entry["level"] == "basic"
    assert entry["tab"] == "general"
    assert entry["group"] == "跳过标签"


def test_api_task_creation_does_not_use_webhook_allowed_tags(
    tmp_path: Path, monkeypatch
):
    """手工/API 入队没有 qBittorrent 标签，不应受 webhook 白名单限制。"""
    queue = _FakeQueue()
    monkeypatch.setattr(routes_tasks, "get_queue_manager", lambda: queue)
    monkeypatch.setattr(web.cm, "get_config", lambda key: "动漫")

    with TestClient(web.app) as client:
        response = client.post(
            "/api/tasks",
            json={"path": str(tmp_path), "is_anime": False, "is_movie": False},
        )

    assert response.status_code == 200
    assert response.json()["task_id"] == "test-task-id"
    assert len(queue.enqueued) == 1
