from __future__ import annotations

import json
import os

from src.config import config_manager


def test_write_config_retries_atomic_replace_permission_error(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    monkeypatch.setattr(config_manager, "CONFIG_PATH", config_path)
    monkeypatch.setattr(config_manager.time, "sleep", lambda _seconds: None)

    calls = {"count": 0}
    real_replace = os.replace

    def flaky_replace(src, dst):
        calls["count"] += 1
        if calls["count"] == 1:
            raise PermissionError("temporary Windows file lock")
        return real_replace(src, dst)

    monkeypatch.setattr(config_manager.os, "replace", flaky_replace)

    manager = config_manager.ConfigManager()
    manager.config["ai_model"] = "retry-test-model"
    manager.write_config()

    assert calls["count"] >= 2
    assert config_path.exists()
    assert "retry-test-model" in config_path.read_text(encoding="UTF-8")


def _load_config_manager_from_data(tmp_path, monkeypatch, data):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(data, ensure_ascii=False), encoding="UTF-8")
    monkeypatch.setattr(config_manager, "CONFIG_PATH", config_path)
    return config_manager.ConfigManager(), config_path


def test_migrate_legacy_allowed_tags_to_categories(tmp_path, monkeypatch):
    """上一版的白名单值升级后保留，并清理已废弃配置键。"""
    manager, config_path = _load_config_manager_from_data(
        tmp_path,
        monkeypatch,
        {"allowed_tags": "动漫,电影,tv"},
    )

    assert manager.config["allowed_categories"] == "动漫,电影,tv"
    assert "allowed_tags" not in manager.config
    persisted = json.loads(config_path.read_text(encoding="UTF-8"))
    assert persisted["allowed_categories"] == "动漫,电影,tv"
    assert "allowed_tags" not in persisted


def test_new_category_key_wins_over_legacy_tag_value(tmp_path, monkeypatch):
    """新旧键并存时，已显式保存的新分类配置必须优先。"""
    manager, _ = _load_config_manager_from_data(
        tmp_path,
        monkeypatch,
        {
            "allowed_tags": "旧值",
            "allowed_categories": "新值",
        },
    )

    assert manager.config["allowed_categories"] == "新值"
    assert "allowed_tags" not in manager.config


def test_explicit_empty_category_key_does_not_restore_legacy_value(
    tmp_path, monkeypatch
):
    """用户显式关闭新白名单后，旧字段不能在后续启动时复活。"""
    manager, config_path = _load_config_manager_from_data(
        tmp_path,
        monkeypatch,
        {
            "allowed_tags": "动漫,电影,tv",
            "allowed_categories": "",
        },
    )

    assert manager.config["allowed_categories"] == ""
    manager.update_config()
    persisted = json.loads(config_path.read_text(encoding="UTF-8"))
    assert persisted["allowed_categories"] == ""
    assert "allowed_tags" not in persisted
