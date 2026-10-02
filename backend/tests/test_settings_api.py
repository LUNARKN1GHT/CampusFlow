from fastapi.testclient import TestClient


def test_settings_defaults_update_and_validation(client: TestClient) -> None:
    current = client.get("/api/v1/settings")
    assert current.status_code == 200, current.text
    assert current.json()["timezone"] == "Asia/Shanghai"
    assert current.json()["daily_capacity_minutes"] == 240

    updated = client.put(
        "/api/v1/settings",
        json={
            "timezone": "Asia/Tokyo",
            "daily_capacity_minutes": 180,
            "break_minutes": 20,
            "buffer_minutes": 45,
        },
    )
    assert updated.status_code == 200, updated.text
    assert client.get("/api/v1/settings").json()["buffer_minutes"] == 45

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
