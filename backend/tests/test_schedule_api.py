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


def test_event_update_rejects_null_required_fields(client: TestClient) -> None:
    event = _create_event(client)

    for field in ("title", "starts_at", "ends_at", "recurrence"):
        response = client.patch(f"/api/v1/fixed-events/{event['id']}", json={field: None})
        assert response.status_code == 422
        assert field in response.text


def test_event_update_clears_optional_fields(client: TestClient) -> None:
    event = _create_event(client)

    updated = client.patch(f"/api/v1/fixed-events/{event['id']}", json={"location": None}).json()
    assert updated["location"] is None
    assert updated["title"] == "操作系统实验课"  # 未提交字段不变


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


def test_weekly_event_occurrences_respect_query_and_repeat_end(client: TestClient) -> None:
    event = _create_event(
        client,
        recurrence="weekly",
        repeat_until="2026-10-12",
    )
    response = client.get(
        "/api/v1/fixed-events/occurrences",
        params={"start_date": "2026-10-01", "end_date": "2026-10-31"},
    )
    assert response.status_code == 200, response.text
    occurrences = response.json()
    assert [item["starts_at"][:10] for item in occurrences] == ["2026-10-05", "2026-10-12"]
    assert {item["source_event_id"] for item in occurrences} == {event["id"]}


def test_weekly_occurrences_are_not_duplicated_or_missed_at_window_edges(
    client: TestClient,
) -> None:
    _create_event(client, recurrence="weekly", repeat_until="2026-10-26")

    full = client.get(
        "/api/v1/fixed-events/occurrences",
        params={"start_date": "2026-10-01", "end_date": "2026-10-31"},
    ).json()
    first_half = client.get(
        "/api/v1/fixed-events/occurrences",
        params={"start_date": "2026-10-01", "end_date": "2026-10-05"},
    ).json()
    second_half = client.get(
        "/api/v1/fixed-events/occurrences",
        params={"start_date": "2026-10-06", "end_date": "2026-10-31"},
    ).json()

    merged = [item["starts_at"] for item in first_half + second_half]
    assert merged == [item["starts_at"] for item in full]
    assert len(merged) == len(set(merged))

    # 窗口越过 repeat_until 时不产生额外实例（最后一次为 10-26）
    assert [item["starts_at"][:10] for item in full] == [
        "2026-10-05",
        "2026-10-12",
        "2026-10-19",
        "2026-10-26",
    ]
    beyond = client.get(
        "/api/v1/fixed-events/occurrences",
        params={"start_date": "2026-10-20", "end_date": "2026-11-30"},
    ).json()
    assert [item["starts_at"][:10] for item in beyond] == ["2026-10-26"]


def test_occurrences_clip_to_query_window_and_keep_workspace_offset(client: TestClient) -> None:
    _create_event(client, recurrence="weekly", repeat_until="2026-10-26")

    # 窗口起点在实例结束之后，该实例不进入结果
    after_first = client.get(
        "/api/v1/fixed-events/occurrences",
        params={"start_date": "2026-10-06", "end_date": "2026-10-11"},
    ).json()
    assert [item["starts_at"][:10] for item in after_first] == []

    # 窗口起点落在实例进行中，实例仍被包含
    mid_occurrence = client.get(
        "/api/v1/fixed-events/occurrences",
        params={"start_date": "2026-10-12", "end_date": "2026-10-13"},
    ).json()
    assert [item["starts_at"][:10] for item in mid_occurrence] == ["2026-10-12"]
    # 工作区时区（Asia/Shanghai）下展示本地偏移，UTC 存储未被改写
    assert mid_occurrence[0]["starts_at"].endswith("+08:00")
    assert mid_occurrence[0]["ends_at"].endswith("+08:00")


def test_cross_midnight_occurrence_appears_once_per_window(client: TestClient) -> None:
    _create_event(
        client,
        title="深夜自习",
        starts_at="2026-09-28T23:00:00",
        ends_at="2026-09-29T01:00:00",
        recurrence="weekly",
        repeat_until="2026-10-12",
    )

    spanning = client.get(
        "/api/v1/fixed-events/occurrences",
        params={"start_date": "2026-10-04", "end_date": "2026-10-06"},
    ).json()
    assert [item["starts_at"][:16] for item in spanning] == ["2026-10-05T23:00"]
    assert spanning[0]["ends_at"][:16] == "2026-10-06T01:00"


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
