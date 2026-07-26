"""Contract regression tests for the Vite-era API surface."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.web import SPAStaticFiles, app


def test_success_and_error_envelopes() -> None:
    client = TestClient(app)
    tasks = client.get("/api/tasks")
    assert tasks.status_code == 200
    assert set(tasks.json()) == {"data"}
    assert isinstance(tasks.json()["data"]["tasks"], list)
    assert all("failure_reason_label" not in row for row in tasks.json()["data"]["tasks"])

    missing = client.get("/api/files/browse", params={"path": "Z:/does-not-exist"})
    assert missing.status_code == 404
    assert set(missing.json()) == {"error"}
    assert missing.json()["error"]["code"] == "path_not_found"
    assert missing.json()["error"]["params"]["path"] == "Z:/does-not-exist"


def test_validation_errors_use_the_same_error_envelope() -> None:
    client = TestClient(app)
    response = client.get(
        "/api/files/browse",
        params={"path": ".", "page": "not-a-number"},
    )
    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == "validation_error"
    assert isinstance(body["error"]["params"]["fields"], list)


def test_bad_file_path_uses_a_stable_error_code(tmp_path: Path) -> None:
    file_path = tmp_path / "not-a-directory.txt"
    file_path.write_text("fixture", encoding="utf-8")
    response = TestClient(app).get(
        "/api/files/browse", params={"path": str(file_path)}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_spa_fallback_does_not_hide_assets_or_api(tmp_path: Path) -> None:
    frontend_out = tmp_path / "out"
    frontend_out.mkdir()
    (frontend_out / "index.html").write_text(
        '<html><body><div id="root"></div></body></html>', encoding="utf-8"
    )

    spa_app = FastAPI()

    @spa_app.get("/api/known")
    def _known_api() -> dict[str, bool]:
        return {"ok": True}

    spa_app.mount(
        "/", SPAStaticFiles(directory=str(frontend_out)), name="frontend-test"
    )
    client = TestClient(spa_app)
    html = client.get("/settings/general", headers={"accept": "text/html"})
    assert html.status_code == 200
    assert '<div id="root">' in html.text
    assert client.get("/assets/missing.js").status_code == 404
    assert client.get("/api/known").json() == {"ok": True}
    assert client.get("/api/unknown").status_code == 404

    production_client = TestClient(app)
    unknown_api = production_client.get("/api/unknown")
    assert unknown_api.status_code == 404
    assert unknown_api.json()["error"]["code"] == "path_not_found"
    assert production_client.get("/health").json() == {"status": "ok"}


def test_config_v2_migration_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    import src.config.config_manager as module

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"mode": "链接", "overwrite_existing": False}), encoding="utf-8"
    )
    monkeypatch.setattr(module, "CONFIG_PATH", config_path)
    manager = module.ConfigManager()
    assert manager.get_config("mode") == "link"
    assert manager.get_config("overwrite_existing") == "skip"
    backup = tmp_path / "config.pre-i18n-v2.json"
    assert backup.exists()
    first_backup = backup.read_text(encoding="utf-8")
    manager.update_config()
    assert backup.read_text(encoding="utf-8") == first_backup
    assert json.loads(config_path.read_text(encoding="utf-8"))["config_schema_version"] == 2


def test_field_spec_and_config_enums_are_stable_ids() -> None:
    client = TestClient(app)
    spec = client.get("/api/config/field-spec").json()["data"]["field_spec"]
    mode = next(item for item in spec if item["key"] == "mode")
    overwrite = next(item for item in spec if item["key"] == "overwrite_existing")
    assert mode["options"] == ["link", "copy", "move"]
    assert overwrite["options"] == ["overwrite", "skip"]
    assert all("label" not in item and "help" not in item for item in spec)


def test_unknown_status_is_closed_to_the_stable_status_set() -> None:
    from src.api.contract import canonical_status

    assert canonical_status("legacy presentation status") == "pending"
    assert canonical_status("anything", has_error=True) == "failed"
