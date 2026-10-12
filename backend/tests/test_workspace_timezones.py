"""W008：工作空间时区、跨日、偏移与 DST 的回归测试。"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient


def _timezone(client: TestClient, value: str) -> None:
    settings = client.get("/api/v1/settings").json()
    settings.pop("workspace_id")
    settings["timezone"] = value
    response = client.put("/api/v1/settings", json=settings)
    assert response.status_code == 200, response.text


def _event(client: TestClient, **values) -> dict:
    response = client.post(
        "/api/v1/fixed-events",
        json={"title": "时区验证", **values},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_workspace_naive_create_patch_and_offset_cross_day(client: TestClient) -> None:
    _timezone(client, "America/New_York")
    event = _event(client, starts_at="2026-10-08T23:30:00", ends_at="2026-10-09T00:30:00")
    assert datetime.fromisoformat(event["starts_at"]) == datetime(2026, 10, 9, 3, 30, tzinfo=UTC)
    patched = client.patch(
        f"/api/v1/fixed-events/{event['id']}", json={"ends_at": "2026-10-09T01:30:00"}
    ).json()
    assert datetime.fromisoformat(patched["ends_at"]) == datetime(2026, 10, 9, 5, 30, tzinfo=UTC)
    explicit = _event(
        client, starts_at="2026-10-09T00:30:00+09:00", ends_at="2026-10-09T01:30:00+09:00"
    )
    assert datetime.fromisoformat(explicit["starts_at"]) == datetime(
        2026, 10, 8, 15, 30, tzinfo=UTC
    )
    _timezone(client, "Asia/Tokyo")
    stored = client.get("/api/v1/fixed-events").json()
    assert [item["id"] for item in stored] == [explicit["id"], patched["id"]]
    for actual, original in zip(stored, [explicit, patched], strict=True):
        for field in ("starts_at", "ends_at"):
            assert datetime.fromisoformat(actual[field]) == datetime.fromisoformat(original[field])


def test_occurrences_use_workspace_day_and_repeat_cutoff(client: TestClient) -> None:
    _timezone(client, "America/New_York")
    _event(
        client,
        starts_at="2026-10-08T23:30:00",
        ends_at="2026-10-09T00:30:00",
        recurrence="weekly",
        repeat_until="2026-10-15",
    )
    response = client.get(
        "/api/v1/fixed-events/occurrences",
        params={"start_date": "2026-10-08", "end_date": "2026-10-08"},
    )
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["starts_at"] == "2026-10-08T23:30:00-04:00"
    later = client.get(
        "/api/v1/fixed-events/occurrences",
        params={"start_date": "2026-10-15", "end_date": "2026-10-31"},
    ).json()
    assert [item["starts_at"][:10] for item in later] == ["2026-10-15"]


@pytest.mark.parametrize("start", ["2026-03-08T02:15:00", "2026-11-01T01:15:00"])
def test_dst_naive_gaps_and_ambiguity_rejected(client: TestClient, start: str) -> None:
    _timezone(client, "America/New_York")
    response = client.post(
        "/api/v1/fixed-events",
        json={"title": "不确定时刻", "starts_at": start, "ends_at": start[:11] + "03:30:00"},
    )
    assert response.status_code == 400
    assert "夏令时" in response.json()["detail"]
    assert client.get("/api/v1/fixed-events").json() == []


def test_dst_explicit_fold_preserves_instant_and_positive_duration(client: TestClient) -> None:
    _timezone(client, "America/New_York")
    event = _event(
        client, starts_at="2026-11-01T01:30:00-04:00", ends_at="2026-11-01T01:15:00-05:00"
    )
    assert datetime.fromisoformat(event["starts_at"]) == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert datetime.fromisoformat(event["ends_at"]) == datetime(2026, 11, 1, 6, 15, tzinfo=UTC)


def test_weekly_dst_keeps_wall_clock_and_elapsed_duration(client: TestClient) -> None:
    _timezone(client, "America/New_York")
    _event(
        client,
        starts_at="2026-10-25T09:00:00",
        ends_at="2026-10-25T10:00:00",
        recurrence="weekly",
        repeat_until="2026-11-08",
    )
    response = client.get(
        "/api/v1/fixed-events/occurrences",
        params={"start_date": "2026-10-25", "end_date": "2026-11-08"},
    )
    assert response.status_code == 200, response.text
    values = response.json()
    assert [item["starts_at"] for item in values] == [
        "2026-10-25T09:00:00-04:00",
        "2026-11-01T09:00:00-05:00",
        "2026-11-08T09:00:00-05:00",
    ]
    for item in values:
        assert (
            datetime.fromisoformat(item["ends_at"]) - datetime.fromisoformat(item["starts_at"])
        ).total_seconds() == 3600
