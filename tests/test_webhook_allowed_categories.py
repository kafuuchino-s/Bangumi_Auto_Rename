"""/sendTask qBittorrent 分类白名单准入回归测试。"""

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
    allowed_categories: str = "",
    skip_tags: str = "",
) -> _FakeQueue:
    queue = _FakeQueue()
    config = {
        "allowed_categories": allowed_categories,
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


def test_empty_category_whitelist_keeps_categoryless_webhook_compatible(
    tmp_path: Path, monkeypatch
):
    """分类白名单留空时，旧 webhook 不传 category 也可入队。"""
    queue = _patch_webhook_dependencies(monkeypatch, allowed_categories="")

    status, body = _send_task(tmp_path)

    assert status == 200
    assert body["code"] == 200
    assert len(queue.enqueued) == 1


def test_category_whitelist_accepts_exact_normalized_category(
    tmp_path: Path, monkeypatch
):
    """分类为单值，去首尾空格和忽略大小写后精确匹配。"""
    queue = _patch_webhook_dependencies(
        monkeypatch, allowed_categories="动漫,电影,tv"
    )

    status, body = _send_task(tmp_path, category=" TV ")

    assert status == 200
    assert body["code"] == 200
    assert queue.enqueued == [
        {
            "path": str(tmp_path),
            "is_anime": False,
            "is_movie": None,
        }
    ]


def test_category_whitelist_rejects_missing_partial_and_tag_only_values(
    tmp_path: Path, monkeypatch
):
    """标签不能绕过分类白名单，近似分类也不能精确命中。"""
    queue = _patch_webhook_dependencies(
        monkeypatch, allowed_categories="动漫,电影,tv"
    )
    nonexistent_path = tmp_path / "does-not-exist"

    for form in ({}, {"category": "动漫电影"}, {"tag": "动漫"}):
        status, body = _send_task(nonexistent_path, **form)
        assert status == 200
        assert body["code"] == 202
        assert "未命中允许分类" in str(body["data"])

    assert queue.enqueued == []


def test_skip_tag_takes_precedence_over_allowed_category(
    tmp_path: Path, monkeypatch
):
    """允许分类带 reseed 标签时，标签跳过规则优先。"""
    queue = _patch_webhook_dependencies(
        monkeypatch,
        allowed_categories="动漫,电影,tv",
        skip_tags="reseed",
    )

    status, body = _send_task(tmp_path, category="动漫", tag="reseed")

    assert status == 200
    assert body["code"] == 202
    assert "命中跳过标签：reseed" in str(body["data"])
    assert queue.enqueued == []


def test_explicit_no_process_takes_precedence_over_category_whitelist(
    tmp_path: Path, monkeypatch
):
    """调用方显式 no_process 不受分类是否允许影响。"""
    queue = _patch_webhook_dependencies(monkeypatch, allowed_categories="动漫")

    status, body = _send_task(tmp_path, category="电影", no_process="true")

    assert status == 200
    assert body["code"] == 202
    assert "no_process 已启用" in str(body["data"])
    assert queue.enqueued == []


def test_categories_supply_initial_media_type_hints(tmp_path: Path, monkeypatch):
    """qB 分类为获准任务提供初始根目录提示。"""
    for category, expected_anime, expected_movie in (
        ("动漫", True, None),
        ("电影", False, True),
        ("tv", False, None),
        ("其他", None, None),
    ):
        queue = _patch_webhook_dependencies(monkeypatch, allowed_categories="")

        status, body = _send_task(tmp_path, category=category)

        assert status == 200
        assert body["code"] == 200
        assert queue.enqueued[0]["is_anime"] is expected_anime
        assert queue.enqueued[0]["is_movie"] is expected_movie


def test_explicit_media_type_flags_override_category_hints(
    tmp_path: Path, monkeypatch
):
    """调用方显式传入类型时，不被 qB 分类提示覆盖。"""
    queue = _patch_webhook_dependencies(monkeypatch, allowed_categories="")

    status, body = _send_task(
        tmp_path,
        category="电影",
        is_anime="true",
        is_movie="false",
    )

    assert status == 200
    assert body["code"] == 200
    assert queue.enqueued == [
        {
            "path": str(tmp_path),
            "is_anime": True,
            "is_movie": False,
        }
    ]


def test_allowed_categories_config_and_field_spec_are_exposed():
    """分类白名单默认关闭，且 API 向前端下发可渲染字段元数据。"""
    assert CONFIG_DEFAULT["allowed_categories"] == ""
    assert "allowed_tags" not in CONFIG_DEFAULT
    assert CN_MAP["allowed_categories"] == "仅处理分类（白名单，逗号分隔）"

    with TestClient(web.app) as client:
        response = client.get("/api/config/field-spec")

    assert response.status_code == 200
    entry = next(
        item
        for item in response.json()["field_spec"]
        if item["key"] == "allowed_categories"
    )
    assert entry["label"] == CN_MAP["allowed_categories"]
    assert entry["control"] == "input"
    assert entry["level"] == "basic"
    assert entry["tab"] == "general"
    assert entry["group"] == "Webhook 过滤与分类"


def test_api_task_creation_does_not_use_webhook_category_whitelist(
    tmp_path: Path, monkeypatch
):
    """手工/API 入队没有 qB 分类，不应受 webhook 分类白名单限制。"""
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
