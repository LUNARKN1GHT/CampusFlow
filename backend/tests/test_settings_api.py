import pytest
from fastapi.testclient import TestClient

from campusflow.main import create_app


def test_settings_defaults_update_and_validation(client: TestClient) -> None:
    current = client.get("/api/v1/settings")
    assert current.status_code == 200, current.text
    assert current.json()["timezone"] == "Asia/Shanghai"
    assert current.json()["daily_capacity_minutes"] == 240
    assert current.json()["weekly_capacity_minutes"] == 1680

    updated = client.put(
        "/api/v1/settings",
        json={
            "timezone": "Asia/Tokyo",
            "daily_capacity_minutes": 180,
            "break_minutes": 20,
            "buffer_minutes": 45,
            "weekly_capacity_minutes": 900,
        },
    )
    assert updated.status_code == 200, updated.text
    assert client.get("/api/v1/settings").json()["buffer_minutes"] == 45
    assert client.get("/api/v1/settings").json()["weekly_capacity_minutes"] == 900

    assert (
        client.put(
            "/api/v1/settings",
            json={
                "timezone": "Mars/Olympus",
                "daily_capacity_minutes": 180,
                "break_minutes": 20,
                "buffer_minutes": 45,
            },
        ).status_code
        == 400
    )


def test_availability_rejects_overlap(client: TestClient) -> None:
    first = client.post(
        "/api/v1/availability-slots",
        json={"day_of_week": 1, "start_time": "09:00:00", "end_time": "11:00:00"},
    )
    assert first.status_code == 201
    overlap = client.post(
        "/api/v1/availability-slots",
        json={"day_of_week": 1, "start_time": "10:30:00", "end_time": "12:00:00"},
    )
    assert overlap.status_code == 400
    assert "重叠" in overlap.json()["detail"]


def test_availability_overlap_is_rejected_without_double_counting(client: TestClient) -> None:
    day = 2
    payload = {"day_of_week": day, "start_time": "19:00:00", "end_time": "21:00:00"}
    assert client.post("/api/v1/availability-slots", json=payload).status_code == 201

    for start, end in (
        ("20:00:00", "22:00:00"),
        ("18:00:00", "19:30:00"),
        ("19:30:00", "20:30:00"),
    ):
        rejected = client.post(
            "/api/v1/availability-slots",
            json={"day_of_week": day, "start_time": start, "end_time": end},
        )
        assert rejected.status_code == 400
        assert "重叠" in rejected.json()["detail"]

    # 重复输入被拒绝，同一天只有一条时段，容量不会被重复累加
    slots = client.get("/api/v1/availability-slots").json()
    assert len(slots) == 1

    # 相邻但不重叠的时段可以录入，容量按两段分别统计
    adjacent = client.post(
        "/api/v1/availability-slots",
        json={"day_of_week": day, "start_time": "21:00:00", "end_time": "22:00:00"},
    )
    assert adjacent.status_code == 201
    assert len(client.get("/api/v1/availability-slots").json()) == 2


def test_availability_slot_rejects_cross_midnight_with_guidance(client: TestClient) -> None:
    response = client.post(
        "/api/v1/availability-slots",
        json={"day_of_week": 4, "start_time": "23:00:00", "end_time": "01:00:00"},
    )
    assert response.status_code == 400
    assert "跨日" in response.json()["detail"]

    # 按提示拆分为两天录入后即可保存，且不产生跨日时段
    assert (
        client.post(
            "/api/v1/availability-slots",
            json={"day_of_week": 4, "start_time": "23:00:00", "end_time": "23:59:00"},
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/v1/availability-slots",
            json={"day_of_week": 5, "start_time": "00:00:00", "end_time": "01:00:00"},
        ).status_code
        == 201
    )


@pytest.mark.parametrize(
    "field,value,status",
    [
        ("timezone", "/etc/passwd", 400),
        ("timezone", "../UTC", 400),
        ("weekly_capacity_minutes", -1, 422),
        ("weekly_capacity_minutes", 10081, 422),
        ("daily_capacity_minutes", -1, 422),
        ("break_minutes", -1, 422),
        ("buffer_minutes", -1, 422),
    ],
)
def test_settings_invalid_input_is_atomic(
    client: TestClient, field: str, value, status: int
) -> None:
    before = client.get("/api/v1/settings").json()
    payload = {key: item for key, item in before.items() if key != "workspace_id"}
    payload[field] = value
    assert client.put("/api/v1/settings", json=payload).status_code == status
    assert client.get("/api/v1/settings").json() == before


def test_settings_survive_new_app_and_legacy_clients(client: TestClient) -> None:
    payload = {
        "timezone": "America/New_York",
        "daily_capacity_minutes": 180,
        "weekly_capacity_minutes": 0,
        "break_minutes": 20,
        "buffer_minutes": 45,
    }
    assert client.put("/api/v1/settings", json=payload).status_code == 200
    del payload["weekly_capacity_minutes"]
    assert client.put("/api/v1/settings", json=payload).json()["weekly_capacity_minutes"] == 0
    settings = client.app.state.settings
    with TestClient(create_app(settings)) as restarted:
        assert (
            restarted.post(
                "/api/v1/auth/login",
                json={"username": settings.local_username, "password": settings.local_password},
            ).status_code
            == 200
        )
        actual = restarted.get("/api/v1/settings").json()
        assert actual["weekly_capacity_minutes"] == 0
        assert actual["timezone"] == "America/New_York"
