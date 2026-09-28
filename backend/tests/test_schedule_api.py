"""固定日程与可用时间 API 集成测试（真实 PostgreSQL 测试库）。"""

from datetime import UTC, datetime

from fastapi.testclient import TestClient


def _create_event(client: TestClient, **overrides: object) -> dict:
    payload: dict = {
        "title": "操作系统实验课",
        "starts_at": "2026-09-28T14:00:00",
        "ends_at": "2026-09-28T16:00:00",
        "location": "实验楼 302",
    }
    payload.update(overrides)
    response = client.post("/api/v1/fixed-events", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_event_create_list_update_delete(client: TestClient) -> None:
    event = _create_event(client)
    listed = client.get("/api/v1/fixed-events").json()
    assert [e["id"] for e in listed] == [event["id"]]

    updated = client.patch(
        f"/api/v1/fixed-events/{event['id']}", json={"location": "实验楼 405"}
    ).json()
    assert updated["location"] == "实验楼 405"
    assert updated["title"] == "操作系统实验课"  # 未提交字段不变

    assert client.delete(f"/api/v1/fixed-events/{event['id']}").status_code == 204
    assert client.get("/api/v1/fixed-events").json() == []
    assert client.delete(f"/api/v1/fixed-events/{event['id']}").status_code == 404


def test_naive_datetime_is_stored_as_local_time(client: TestClient) -> None:
    """前端不带时区的 "14:00" 按本地时区（Asia/Shanghai）理解，即 06:00 UTC。"""
    event = _create_event(client)
    stored = datetime.fromisoformat(event["starts_at"])
    assert stored == datetime(2026, 9, 28, 6, 0, tzinfo=UTC)


def test_event_rejects_invalid_ranges(client: TestClient) -> None:
    response = client.post(
        "/api/v1/fixed-events",
        json={
            "title": "时间倒流",
            "starts_at": "2026-09-28T16:00:00",
            "ends_at": "2026-09-28T14:00:00",
        },
    )
    assert response.status_code == 400

    weekly_without_end = client.post(
        "/api/v1/fixed-events",
        json={
            "title": "每周实验",
            "starts_at": "2026-09-28T14:00:00",
            "ends_at": "2026-09-28T16:00:00",
            "recurrence": "weekly",
        },
    )
    assert weekly_without_end.status_code == 400


def test_availability_slot_create_list_delete(client: TestClient) -> None:
    response = client.post(
        "/api/v1/availability-slots",
        json={"day_of_week": 3, "start_time": "19:00:00", "end_time": "21:00:00"},
    )
    assert response.status_code == 201, response.text
    slot = response.json()
    assert slot["day_of_week"] == 3

    listed = client.get("/api/v1/availability-slots").json()
    assert [s["id"] for s in listed] == [slot["id"]]

    assert client.delete(f"/api/v1/availability-slots/{slot['id']}").status_code == 204
    assert client.get("/api/v1/availability-slots").json() == []


def test_availability_slot_validation(client: TestClient) -> None:
    bad_dow = client.post(
        "/api/v1/availability-slots",
        json={"day_of_week": 9, "start_time": "19:00:00", "end_time": "21:00:00"},
    )
    assert bad_dow.status_code == 422

    bad_range = client.post(
        "/api/v1/availability-slots",
        json={"day_of_week": 3, "start_time": "21:00:00", "end_time": "19:00:00"},
    )
    assert bad_range.status_code == 400
