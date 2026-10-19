"""A014：计划与通知变化后重算提醒。"""

from conftest import TEST_DATABASE_URL
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from campusflow.infrastructure.db.models import ReminderTrigger


def _task(client: TestClient, **overrides) -> dict:
    payload = {
        "title": "重算任务",
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
    statement = select(ReminderTrigger.id, ReminderTrigger.status).where(
        ReminderTrigger.task_id == task_id
    )
    if status is not None:
        statement = statement.where(ReminderTrigger.status == status)
    with engine.connect() as connection:
        return [{"id": row[0], "status": row[1]} for row in connection.execute(statement).all()]


def _draft(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/plans/drafts",
        json={
            "start_date": "2026-11-16",
            "end_date": "2026-11-22",
            "assumptions": {"buffer_minutes": 0},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_unaccepted_draft_does_not_change_reminders(client: TestClient) -> None:
    task = _task(client)
    _rule(client, task_id=task["id"], advance_minutes=60)
    before = _triggers(task["id"])

    draft = _draft(client)
    after_draft = _triggers(task["id"])
    assert after_draft == before

    accepted = client.post(
        f"/api/v1/plans/drafts/{draft['id']}/accept",
        json={"expected_version": draft["version"]},
    )
    assert accepted.status_code == 200, accepted.text
    assert _triggers(task["id"]) == before


def test_accepted_due_change_replaces_without_two_pending_sets(client: TestClient) -> None:
    task = _task(client)
    _rule(client, task_id=task["id"], advance_minutes=60)
    [before] = _triggers(task["id"], "pending")

    updated = client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={"due_date": "2026-11-24", "due_time": "12:00:00"},
    )
    assert updated.status_code == 200, updated.text

    pending = _triggers(task["id"], "pending")
    assert len(pending) == 1
    assert pending[0]["id"] != before["id"]
    assert before["id"] in [item["id"] for item in _triggers(task["id"], "cancelled")]


def test_reminder_does_not_touch_task_or_plan(client: TestClient) -> None:
    task = _task(client)
    _rule(client, task_id=task["id"], advance_minutes=60)
    draft = _draft(client)
    accepted = client.post(
        f"/api/v1/plans/drafts/{draft['id']}/accept",
        json={"expected_version": draft["version"]},
    )
    assert accepted.status_code == 200

    current = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert current["due_date"] == "2026-11-20"
    assert current["due_time"] == "18:00:00"
    plan = client.get("/api/v1/plans/current").json()
    assert plan["status"] == "accepted"
