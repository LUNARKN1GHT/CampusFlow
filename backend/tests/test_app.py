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
        assert "/api/v1/ready" in spec["paths"]


def test_readiness_fails_without_database_while_health_stays_up() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://campusflow:campusflow@127.0.0.1:1/campusflow",
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/health").status_code == 200
        response = client.get("/api/v1/ready")
        assert response.status_code == 503
        assert "campusflow" not in response.text  # 不泄露连接口令


def test_readiness_succeeds_with_database(client: TestClient) -> None:
    response = client.get("/api/v1/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "campusflow-api",
        "database": "ok",
    }


def test_settings_are_injected_and_unknown_routes_do_not_claim_success(monkeypatch) -> None:
    monkeypatch.setenv("CAMPUSFLOW_APP_NAME", "CampusFlow Test")
    with TestClient(create_app(Settings(_env_file=None))) as client:
        assert client.get("/openapi.json").json()["info"]["title"] == "CampusFlow Test"
        assert client.get("/api/v1/materials").status_code == 404
