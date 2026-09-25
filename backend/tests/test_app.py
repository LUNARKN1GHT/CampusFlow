from fastapi.testclient import TestClient

from campusflow.core.config import Settings
from campusflow.main import create_app


def test_health_contract_and_openapi() -> None:
    with TestClient(create_app(Settings(_env_file=None))) as client:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "service": "campusflow-api"}
        spec = client.get("/openapi.json").json()
        assert "/api/v1/health" in spec["paths"]


def test_settings_are_injected_and_unknown_routes_do_not_claim_success(monkeypatch) -> None:
    monkeypatch.setenv("CAMPUSFLOW_APP_NAME", "CampusFlow Test")
    with TestClient(create_app(Settings(_env_file=None))) as client:
        assert client.get("/openapi.json").json()["info"]["title"] == "CampusFlow Test"
        assert client.get("/api/v1/materials").status_code == 404
