import pytest
from fastapi.testclient import TestClient

from campusflow.core.config import Settings
from campusflow.main import create_app


@pytest.mark.parametrize(
    ("username", "password"),
    [("学生", "wrong"), ("student", "密码"), ("学生", "密码🔑")],
)
def test_unicode_invalid_credentials_return_401(username: str, password: str) -> None:
    with TestClient(create_app(Settings(_env_file=None))) as client:
        response = client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )
        assert response.status_code == 401
        assert response.json() == {"detail": "用户名或密码错误"}
        assert client.get("/api/v1/auth/session").status_code == 401


def test_unicode_configured_credentials_login_and_logout() -> None:
    settings = Settings(_env_file=None, local_username="学生", local_password="密码🔑")
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/auth/login", json={"username": "学生", "password": "密码🔑"}
        )
        assert response.status_code == 200
        assert client.get("/api/v1/auth/session").json() == {"username": "学生"}
        assert client.post("/api/v1/auth/logout").status_code == 204
        assert client.get("/api/v1/auth/session").status_code == 401


def test_private_routes_require_login_and_logout_invalidates_session() -> None:
    settings = Settings(_env_file=None, local_username="tester", local_password="correct horse")
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/semesters").status_code == 401
        assert client.get("/api/v1/auth/session").status_code == 401

        rejected = client.post(
            "/api/v1/auth/login", json={"username": "tester", "password": "wrong"}
        )
        assert rejected.status_code == 401
        assert "password" not in rejected.text.lower()

        logged_in = client.post(
            "/api/v1/auth/login",
            json={"username": "tester", "password": "correct horse"},
        )
        assert logged_in.status_code == 200
        assert logged_in.json() == {"username": "tester"}
        assert client.get("/api/v1/auth/session").json() == {"username": "tester"}

        assert client.post("/api/v1/auth/logout").status_code == 204
        assert client.get("/api/v1/semesters").status_code == 401
