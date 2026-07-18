"""API 层端点 happy path 测试。

用独立 FastAPI 实例挂载 api_router，避免 NiceGUI 启动副作用。
只测只读端点 + 基本结构，不依赖真实业务数据（空数据应优雅返回）。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 确保项目根在 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import api_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(api_router, prefix="/api")
    return TestClient(app)


def test_get_tasks_returns_list():
    """GET /api/tasks 返回 tasks 列表（空数据也应返回空列表）。"""
    with _client() as c:
        r = c.get("/api/tasks")
        assert r.status_code == 200
        data = r.json()
        assert set(data) == {"data"}
        assert "tasks" in data["data"]
        assert isinstance(data["data"]["tasks"], list)


def test_get_task_detail_not_found():
    """GET /api/tasks/{uuid} 不存在时 404。"""
    with _client() as c:
        r = c.get("/api/tasks/nonexistent-uuid-xxx")
        assert r.status_code == 404


def test_get_config_masks_secrets():
    """GET /api/config 返回脱敏配置。"""
    with _client() as c:
        r = c.get("/api/config")
        assert r.status_code == 200
        config = r.json()["data"]["config"]
        # 密钥类字段若非空应为星号
        for key in ("ai_api_key", "emby_api_key", "telegram_bot_token", "api_key"):
            v = config.get(key)
            if isinstance(v, str) and v:
                assert set(v) == {"*"}, f"{key} 未脱敏: {v}"


def test_get_field_spec():
    """GET /api/config/field-spec 返回字段元数据列表。"""
    with _client() as c:
        r = c.get("/api/config/field-spec")
        assert r.status_code == 200
        spec = r.json()["data"]["field_spec"]
        assert isinstance(spec, list)
        assert len(spec) > 0
        # 每个 entry 必备字段
        for entry in spec:
            assert "key" in entry
            assert "control" in entry
            assert "level" in entry


def test_get_dashboard_stats():
    """GET /api/dashboard 返回统计结构。"""
    with _client() as c:
        r = c.get("/api/dashboard")
        assert r.status_code == 200
        stats = r.json()["data"]
        for key in ("running", "pending", "today_success", "today_failed",
                    "today_total", "success_rate"):
            assert key in stats


def test_get_log_tail():
    """GET /api/logs/tail 返回日志行列表。"""
    with _client() as c:
        r = c.get("/api/logs/tail?n=50")
        assert r.status_code == 200
        data = r.json()["data"]
        assert "lines" in data
        assert isinstance(data["lines"], list)


def test_get_subtitle_tasks():
    """GET /api/subtitle/tasks 返回字幕任务列表。"""
    with _client() as c:
        r = c.get("/api/subtitle/tasks")
        assert r.status_code == 200
        assert isinstance(r.json()["data"]["tasks"], list)


def test_browse_files_invalid_path():
    """GET /api/files/browse 不存在路径 404。"""
    with _client() as c:
        r = c.get("/api/files/browse", params={"path": "Z:/no/such/path/xyz"})
        assert r.status_code == 404


def test_browse_files_drives():
    """GET /api/files/drives 返回盘符列表。"""
    with _client() as c:
        r = c.get("/api/files/drives")
        assert r.status_code == 200
        data = r.json()["data"]
        assert "drives" in data
        assert "system" in data


def test_delete_task_not_found():
    """DELETE /api/tasks/{uuid} 不存在时 404。"""
    with _client() as c:
        r = c.delete("/api/tasks/nonexistent-uuid-xxx")
        assert r.status_code == 404
