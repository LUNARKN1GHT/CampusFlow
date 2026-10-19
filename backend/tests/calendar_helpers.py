"""M9 日历测试共享辅助：本地模拟供应商与 HTTP 调用封装。"""

from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient


def _start(client: TestClient) -> dict:
    response = client.get("/api/v1/calendar/oauth/start")
    assert response.status_code == 200, response.text
    return response.json()


def _state_from(url: str) -> str:
    return parse_qs(urlparse(url).query)["state"][0]


def _connect(client: TestClient, code: str = "student@example.com") -> dict:
    start = _start(client)
    response = client.get(
        "/api/v1/calendar/oauth/callback",
        params={"state": _state_from(start["authorization_url"]), "code": code},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _task(client: TestClient, title: str = "写课程论文", **overrides) -> dict:
    payload = {
        "title": title,
        "due_date": "2026-11-02",
        "due_time": "10:00:00",
        "estimated_minutes": 60,
        "remaining_minutes": 60,
    }
    payload.update(overrides)
    response = client.post("/api/v1/tasks", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _draft(client: TestClient, connection: dict, task_ids: list[int]) -> dict:
    response = client.post(
        "/api/v1/calendar/drafts",
        json={
            "connection_id": connection["id"],
            "source": {"kind": "tasks", "task_ids": task_ids},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _confirm(client: TestClient, draft: dict, item_ids: list[int] | None = None) -> dict:
    chosen = item_ids if item_ids is not None else [item["id"] for item in draft["items"]]
    response = client.post(
        f"/api/v1/calendar/drafts/{draft['id']}/confirm",
        json={
            "content_fingerprint": draft["content_fingerprint"],
            "item_ids": chosen,
            "note": "确认写入外部日历",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _execute(client: TestClient, draft: dict) -> dict:
    response = client.post(
        f"/api/v1/calendar/drafts/{draft['id']}/execute",
        json={"confirmation_fingerprint": draft["confirmed_fingerprint"]},
    )
    return response


def _retry(client: TestClient, draft: dict) -> dict:
    response = client.post(
        f"/api/v1/calendar/drafts/{draft['id']}/retry",
        json={"confirmation_fingerprint": draft["confirmed_fingerprint"]},
    )
    return response


def _provider(client: TestClient):
    return client.app.state.calendar_provider


def _differences(client: TestClient, connection_id: int) -> dict:
    response = client.get("/api/v1/calendar/differences", params={"connection_id": connection_id})
    assert response.status_code == 200, response.text
    return response.json()


def _difference_draft(client: TestClient, connection_id: int) -> dict:
    response = client.post(
        "/api/v1/calendar/differences/draft", json={"connection_id": connection_id}
    )
    assert response.status_code == 201, response.text
    return response.json()
