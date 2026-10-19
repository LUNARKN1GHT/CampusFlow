"""A013：完成、取消、暂停与重新打开对提醒的影响。"""

from datetime import UTC, datetime

from conftest import TEST_DATABASE_URL
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from campusflow.infrastructure.db.models import ReminderTrigger


def _task(client: TestClient, **overrides) -> dict:
    payload = {
        "title": "生命周期任务",
        "due_date": "2026-11-20",
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
        ReminderTrigger.read_at,
    ).where(ReminderTrigger.task_id == task_id)
    if status is not None:
        statement = statement.where(ReminderTrigger.status == status)
    with engine.connect() as connection:
        rows = connection.execute(statement).all()
    return [{"id": row[0], "status": row[1], "read_at": row[2]} for row in rows]


def _progress(client: TestClient, task_id: int, progress: str, reason: str) -> None:
    response = client.patch(
        f"/api/v1/tasks/{task_id}/progress",
        json={"progress": progress, "reason": reason},
    )
    assert response.status_code == 200, response.text


def test_done_and_cancel_stop_pending_without_touching_history(client: TestClient) -> None:
    task = _task(client)
    _rule(client, task_id=task["id"], advance_minutes=60)
    [pending] = _triggers(task["id"], "pending")

    _progress(client, task["id"], "done", "提前完成")
    assert _triggers(task["id"], "pending") == []
    assert pending["id"] in [item["id"] for item in _triggers(task["id"], "cancelled")]

    _progress(client, task["id"], "in_progress", "需要补充")
    assert [item["id"] for item in _triggers(task["id"], "pending")] == [pending["id"]]

    _progress(client, task["id"], "cancelled", "课程取消")
    assert _triggers(task["id"], "pending") == []

    _progress(client, task["id"], "in_progress", "重新加入")
    assert [item["id"] for item in _triggers(task["id"], "pending")] == [pending["id"]]


def test_reopen_does_not_resend_sent_reminder(client: TestClient) -> None:
    task = _task(client)
    _rule(client, task_id=task["id"], advance_minutes=60)
    [trigger] = _triggers(task["id"], "pending")

    engine = create_engine(TEST_DATABASE_URL)
    with engine.connect() as connection:
        connection.execute(
            ReminderTrigger.__table__.update()
            .where(ReminderTrigger.id == trigger["id"])
            .values(status="sent", sent_at=datetime.now(UTC), read_at=datetime.now(UTC))
        )
        connection.commit()

    _progress(client, task["id"], "done", "完成")
    _progress(client, task["id"], "in_progress", "重新打开")

    rows = _triggers(task["id"])
    sent = [row for row in rows if row["id"] == trigger["id"]]
    assert len(sent) == 1
    assert sent[0]["status"] == "sent"
    assert sent[0]["read_at"] is not None
    assert _triggers(task["id"], "pending") == []


def test_pause_and_resume_rule_changes_future_set(client: TestClient) -> None:
    task = _task(client)
    rule = _rule(client, advance_minutes=60)
    [first] = _triggers(task["id"], "pending")

    paused = client.patch(
        f"/api/v1/reminders/rules/{rule['id']}",
        json={"advance_minutes": 60, "paused": True},
    )
    assert paused.status_code == 200
    assert _triggers(task["id"], "pending") == []
    assert first["id"] in [item["id"] for item in _triggers(task["id"], "cancelled")]

    resumed = client.patch(
        f"/api/v1/reminders/rules/{rule['id']}",
        json={"advance_minutes": 60, "paused": False},
    )
    assert resumed.status_code == 200
    assert len(_triggers(task["id"], "pending")) == 1


def test_disabled_task_rule_falls_back_to_no_reminder(client: TestClient) -> None:
    task = _task(client)
    rule = _rule(client, task_id=task["id"], advance_minutes=60)
    [pending] = _triggers(task["id"], "pending")

    disabled = client.patch(
        f"/api/v1/reminders/rules/{rule['id']}",
        json={"advance_minutes": 60, "enabled": False},
    )
    assert disabled.status_code == 200
    assert _triggers(task["id"], "pending") == []
    assert pending["id"] in [item["id"] for item in _triggers(task["id"], "cancelled")]


def test_task_due_change_replaces_only_changed_window(client: TestClient) -> None:
    task = _task(client)
    _rule(client, task_id=task["id"], advance_minutes=60)
    [before] = _triggers(task["id"], "pending")

    new_due = "2026-11-25"
    updated = client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={"due_date": new_due, "due_time": "18:00:00"},
    )
    assert updated.status_code == 200

    rows = {item["id"]: item["status"] for item in _triggers(task["id"])}
    assert rows[before["id"]] == "cancelled"
    assert len(_triggers(task["id"], "pending")) == 1
