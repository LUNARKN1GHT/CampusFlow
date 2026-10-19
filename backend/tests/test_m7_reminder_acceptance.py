"""A016：提醒漏发与重复边界的端到端验收。"""

from datetime import datetime
from zoneinfo import ZoneInfo

from conftest import TEST_DATABASE_URL
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from campusflow.infrastructure.db.models import ReminderTrigger

SHANGHAI = ZoneInfo("Asia/Shanghai")
UTC = ZoneInfo("UTC")


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _task(client: TestClient, **overrides) -> dict:
    payload = {
        "title": "验收任务",
        "due_date": "2026-12-01",
        "due_time": "18:00:00",
        "estimated_minutes": 60,
        "remaining_minutes": 60,
    }
    payload.update(overrides)
    response = client.post("/api/v1/tasks", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _rule(client: TestClient, **payload) -> dict:
    response = client.post("/api/v1/reminders/rules", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _triggers(task_id: int, status: str | None = None) -> list[dict]:
    engine = create_engine(TEST_DATABASE_URL)
    statement = select(
        ReminderTrigger.id,
        ReminderTrigger.status,
        ReminderTrigger.scheduled_for,
        ReminderTrigger.message,
    ).where(ReminderTrigger.task_id == task_id)
    if status is not None:
        statement = statement.where(ReminderTrigger.status == status)
    with engine.connect() as connection:
        return [
            {"id": row[0], "status": row[1], "scheduled_for": row[2], "message": row[3]}
            for row in connection.execute(statement).all()
        ]


def _set_timezone(client: TestClient, timezone: str) -> None:
    current = client.get("/api/v1/settings").json()
    response = client.put(
        "/api/v1/settings",
        json={
            "timezone": timezone,
            "daily_capacity_minutes": current["daily_capacity_minutes"],
            "break_minutes": current["break_minutes"],
            "buffer_minutes": current["buffer_minutes"],
            "weekly_capacity_minutes": current["weekly_capacity_minutes"],
        },
    )
    assert response.status_code == 200, response.text


def test_timezone_change_reschedules_without_rewriting_ddl(client: TestClient) -> None:
    task = _task(client)
    _rule(client, task_id=task["id"], advance_minutes=60)
    [before] = _triggers(task["id"], "pending")
    assert _instant(before["scheduled_for"].isoformat()) == datetime(
        2026, 12, 1, 17, 0, tzinfo=SHANGHAI
    )

    _set_timezone(client, "UTC")

    pending = _triggers(task["id"], "pending")
    assert len(pending) == 1
    assert _instant(pending[0]["scheduled_for"].isoformat()) == datetime(
        2026, 12, 1, 17, 0, tzinfo=UTC
    )
    assert pending[0]["id"] != before["id"]
    assert before["id"] in [item["id"] for item in _triggers(task["id"], "cancelled")]

    stored = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert stored["due_date"] == "2026-12-01"
    assert stored["due_time"] == "18:00:00"


def test_date_only_ddl_stays_date_only_after_timezone_change(client: TestClient) -> None:
    task = _task(client, due_time=None)
    _rule(client, task_id=task["id"], advance_minutes=60)
    [before] = _triggers(task["id"], "pending")

    _set_timezone(client, "UTC")
    [after] = _triggers(task["id"], "pending")
    assert "2026-12-01" in after["message"]
    assert ":" not in after["message"]
    assert before["id"] != after["id"]
    stored = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert stored["due_time"] is None


def test_repeated_sync_keeps_one_window(client: TestClient) -> None:
    task = _task(client)
    _rule(client, task_id=task["id"], advance_minutes=60, repeat_minutes=30)
    first_ids = [item["id"] for item in _triggers(task["id"], "pending")]
    for _ in range(3):
        response = client.get("/api/v1/reminders")
        assert response.status_code == 200
    assert [item["id"] for item in _triggers(task["id"], "pending")] == first_ids
    assert len(first_ids) == 3


def test_done_reopen_pause_resume_are_recorded(client: TestClient) -> None:
    task = _task(client)
    rule = _rule(client, task_id=task["id"], advance_minutes=60)
    [pending] = _triggers(task["id"], "pending")

    for progress, reason in (("done", "提前完成"), ("in_progress", "重新打开")):
        response = client.patch(
            f"/api/v1/tasks/{task['id']}/progress",
            json={"progress": progress, "reason": reason},
        )
        assert response.status_code == 200, response.text

    history = client.get(f"/api/v1/tasks/{task['id']}/progress-history").json()
    assert [item["reason"] for item in history] == ["重新打开", "提前完成"]
    assert _triggers(task["id"], "pending")[0]["id"] == pending["id"]

    paused = client.patch(
        f"/api/v1/reminders/rules/{rule['id']}",
        json={"advance_minutes": 60, "paused": True},
    )
    assert paused.status_code == 200
    assert paused.json()["version"] == 2
    assert _triggers(task["id"], "pending") == []

    resumed = client.patch(
        f"/api/v1/reminders/rules/{rule['id']}",
        json={"advance_minutes": 60, "paused": False},
    )
    assert resumed.status_code == 200
    assert resumed.json()["version"] == 3
    assert len(_triggers(task["id"], "pending")) == 1


def test_notification_change_leaves_single_future_set(client: TestClient) -> None:
    task = _task(client)
    _rule(client, task_id=task["id"], advance_minutes=60)
    [before] = _triggers(task["id"], "pending")

    updated = client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={"due_date": "2026-12-05", "due_time": "09:30:00"},
    )
    assert updated.status_code == 200

    pending = _triggers(task["id"], "pending")
    assert len(pending) == 1
    assert pending[0]["id"] != before["id"]
    assert "2026-12-05 09:30" in pending[0]["message"]
    assert before["id"] in [item["id"] for item in _triggers(task["id"], "cancelled")]
