"""A003：依赖参与排期顺序与可安排窗口。"""

from conftest import TEST_DATABASE_URL
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text


def _task(client: TestClient, title: str, **overrides) -> dict:
    payload = {
        "title": title,
        "due_date": "2026-10-20",
        "estimated_minutes": 60,
        "remaining_minutes": 60,
    }
    payload.update(overrides)
    response = client.post("/api/v1/tasks", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _availability(client: TestClient) -> None:
    response = client.post(
        "/api/v1/availability-slots",
        json={"day_of_week": 0, "start_time": "18:00:00", "end_time": "22:00:00"},
    )
    assert response.status_code == 201, response.text


def _generate(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/plans/drafts",
        json={
            "start_date": "2026-10-19",
            "end_date": "2026-10-20",
            "assumptions": {"buffer_minutes": 0},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _depend(client: TestClient, task_id: int, depends_on: int) -> None:
    response = client.post(
        f"/api/v1/tasks/{task_id}/dependencies",
        json={"depends_on_task_id": depends_on},
    )
    assert response.status_code == 201, response.text


def test_dependent_task_is_scheduled_after_prerequisite(client: TestClient) -> None:
    prerequisite = _task(client, "先读材料", due_date="2026-10-20")
    dependent = _task(
        client,
        "再写报告",
        due_date="2026-10-19",
        due_time="21:00:00",
    )
    _depend(client, dependent["id"], prerequisite["id"])
    _availability(client)

    draft = _generate(client)
    blocks = draft["blocks"]
    assert [block["task_id"] for block in blocks] == [prerequisite["id"], dependent["id"]]

    prerequisite_block = blocks[0]
    dependent_block = blocks[1]
    assert dependent_block["starts_at"] >= prerequisite_block["ends_at"]
    assert any(item["kind"] == "dependency" for item in dependent_block["constraint_source"])
    assert "task_dependencies" in draft["coverage"]["checks"]
    assert draft["unscheduled"] == []


def test_cancelled_prerequisite_leaves_dependent_unscheduled(client: TestClient) -> None:
    prerequisite = _task(client, "已取消的前置")
    dependent = _task(client, "依赖它的任务")
    _depend(client, dependent["id"], prerequisite["id"])
    cancelled = client.patch(
        f"/api/v1/tasks/{prerequisite['id']}/progress",
        json={"progress": "cancelled", "reason": "课程调整"},
    )
    assert cancelled.status_code == 200
    _availability(client)

    draft = _generate(client)
    assert all(block["task_id"] != dependent["id"] for block in draft["blocks"])
    entry = next(item for item in draft["unscheduled"] if item["task_id"] == dependent["id"])
    assert entry["status"] == "unscheduled"
    assert entry["reason_code"] == "dependency_unmet"
    assert "已取消" in entry["message"]


def test_completed_prerequisite_does_not_delay_dependent(client: TestClient) -> None:
    prerequisite = _task(client, "已完成的前置")
    dependent = _task(client, "后续任务")
    _depend(client, dependent["id"], prerequisite["id"])
    done = client.patch(
        f"/api/v1/tasks/{prerequisite['id']}/progress",
        json={"progress": "done", "reason": "提前完成"},
    )
    assert done.status_code == 200
    _availability(client)

    draft = _generate(client)
    scheduled = {block["task_id"] for block in draft["blocks"]}
    assert scheduled == {dependent["id"]}
    assert [item for item in draft["unscheduled"] if item["status"] == "unscheduled"] == []


def test_cycle_tasks_are_not_planned(client: TestClient) -> None:
    first = _task(client, "环节点甲")
    second = _task(client, "环节点乙")

    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        workspace_id = connection.execute(text("SELECT id FROM workspaces LIMIT 1")).scalar_one()
        for task_id, depends_on in (
            (first["id"], second["id"]),
            (second["id"], first["id"]),
        ):
            connection.execute(
                text(
                    "INSERT INTO task_dependencies (workspace_id, task_id, depends_on_task_id) "
                    "VALUES (:workspace_id, :task_id, :depends_on)"
                ),
                {"workspace_id": workspace_id, "task_id": task_id, "depends_on": depends_on},
            )

    _availability(client)
    draft = _generate(client)
    assert draft["blocks"] == []
    blocked = {item["task_id"]: item for item in draft["unscheduled"]}
    assert set(blocked) == {first["id"], second["id"]}, draft["coverage"]
    assert all(item["reason_code"] == "dependency_cycle" for item in blocked.values())


def test_dependency_that_finishes_after_deadline_is_unscheduled(client: TestClient) -> None:
    prerequisite = _task(client, "前置占满时段")
    dependent = _task(client, "截止很早的后续", due_date="2026-10-19", due_time="19:00:00")
    _depend(client, dependent["id"], prerequisite["id"])
    _availability(client)

    draft = _generate(client)
    entry = next(item for item in draft["unscheduled"] if item["task_id"] == dependent["id"])
    assert entry["reason_code"] == "dependency_deadline"
    assert entry["remaining_minutes"] == 60
