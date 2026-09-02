from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.routes_config as routes_config
from src.api.routes_config import router


def test_moviepilot_connection_uses_shared_client(monkeypatch) -> None:
    class _Client:
        def list_downloaders(self):
            return [{"name": "qBittorrent", "type": "qbittorrent"}]

    class _MoviePilotClient:
        @staticmethod
        def configured():
            return _Client()

    monkeypatch.setattr(routes_config, "MoviePilotClient", _MoviePilotClient)
    app = FastAPI()
    app.include_router(router, prefix="/api")

    with TestClient(app) as client:
        response = client.post("/api/config/test-moviepilot")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "success": True,
        "message": "连接成功，已启用下载器 1 个",
    }
