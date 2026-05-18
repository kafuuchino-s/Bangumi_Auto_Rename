from __future__ import annotations

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
