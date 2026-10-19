"""A009：依赖排期与局部重排的端到端不变量验收。"""

from datetime import datetime

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


def _availability(client: TestClient, day_of_week: int = 0) -> None:
    response = client.post(
        "/api/v1/availability-slots",
        json={"day_of_week": day_of_week, "start_time": "18:00:00", "end_time": "22:00:00"},
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


def _accept(client: TestClient, draft: dict) -> dict:
    response = client.post(
        f"/api/v1/plans/drafts/{draft['id']}/accept",
        json={"expected_version": draft["version"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _task_ids_in_draft(draft: dict) -> list[int]:
    ids = [block["task_id"] for block in draft["blocks"] if block["task_id"] is not None]
    ids.extend(item["task_id"] for item in draft["unscheduled"])
    return ids


def _assert_every_task_once(draft: dict, task_ids: set[int]) -> None:
    seen = _task_ids_in_draft(draft)
    assert set(seen) == task_ids
    # 同一任务可以分多段，但不能既排入又出现在未排列表
    scheduled = {block["task_id"] for block in draft["blocks"]}
    unscheduled = {item["task_id"] for item in draft["unscheduled"]}
    assert scheduled.isdisjoint(unscheduled)


def test_cycle_blocks_are_not_planned_and_nothing_is_silently_dropped(client: TestClient) -> None:
    first = _task(client, "环节点甲")
    second = _task(client, "环节点乙")
    third = _task(client, "环节点丙")
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        workspace_id = connection.execute(text("SELECT id FROM workspaces LIMIT 1")).scalar_one()
        for task_id, depends_on in (
            (first["id"], second["id"]),
            (second["id"], third["id"]),
            (third["id"], first["id"]),
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
    assert sum(block["minutes"] for block in draft["blocks"]) == 0
    _assert_every_task_once(draft, {first["id"], second["id"], third["id"]})
    assert all(item["reason_code"] == "dependency_cycle" for item in draft["unscheduled"])


def test_locked_block_conflict_is_visible_and_not_moved(client: TestClient) -> None:
    task = _task(client, "锁定任务")
    _availability(client)
    accepted = _accept(client, _generate(client))
    block = accepted["blocks"][0]
    locked = client.post(
        f"/api/v1/plans/blocks/{block['id']}/lock",
        json={"locked": True, "expected_version": accepted["version"]},
    )
    assert locked.status_code == 200, locked.text

    event = client.post(
        "/api/v1/fixed-events",
        json={
            "title": "临时实验",
            "starts_at": "2026-10-19T18:30:00",
            "ends_at": "2026-10-19T19:30:00",
        },
    )
    assert event.status_code == 201, event.text

    response = client.post("/api/v1/plans/reschedule", json={})
    assert response.status_code == 201, response.text
    draft = response.json()
    assert len(draft["blocks"]) == 1
    assert draft["blocks"][0]["starts_at"] == block["starts_at"]
    assert draft["blocks"][0]["ends_at"] == block["ends_at"]
    assert draft["blocks"][0]["locked"] is True
    assert sum(item["minutes"] for item in draft["blocks"] if item["task_id"] == task["id"]) == 60

    overlaps = [item for item in draft["conflicts"] if item["type"] == "time_overlap"]
    assert overlaps
    participants = {(item["kind"], item["id"]) for item in overlaps[0]["participants"]}
    assert ("fixed_event", event.json()["id"]) in participants
    assert ("plan_block", task["id"]) in participants


def test_duration_increase_replans_once_without_duplicate_minutes(client: TestClient) -> None:
    task = _task(client, "耗时增加")
    _availability(client)
    accepted = _accept(client, _generate(client))
    client.patch(f"/api/v1/tasks/{task['id']}", json={"remaining_minutes": 120})

    summary = client.get("/api/v1/plans/adjustments").json()
    assert summary["has_changes"] is True
    assert task["id"] in summary["affected_task_ids"]
    assert client.get("/api/v1/plans/current").json()["version"] == accepted["version"]

    created = client.post("/api/v1/plans/adjustments/draft", json={})
    assert created.status_code == 201, created.text
    draft = created.json()
    assert sum(block["minutes"] for block in draft["blocks"]) == 120
    assert all(block["task_id"] == task["id"] for block in draft["blocks"])
    assert draft["unscheduled"] == []

    new_current = _accept(client, draft)
    assert sum(block["minutes"] for block in new_current["blocks"]) == 120
    unchanged = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert unchanged["due_date"] == "2026-10-20"
    assert unchanged["remaining_minutes"] == 120


def test_accepted_notification_change_does_not_rewrite_ddl(client: TestClient) -> None:
    task = _task(client, "通知变更")
    _availability(client)
    accepted = _accept(client, _generate(client))

    # 模拟通知接受后更新任务字段：新的截止时间由用户/通知确认写入
    updated = client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={"due_date": "2026-10-19", "due_time": "20:00:00"},
    )
    assert updated.status_code == 200, updated.text

    summary = client.get("/api/v1/plans/adjustments").json()
    assert summary["has_changes"] is True
    assert any(
        item["code"] in {"task_due_date_changed", "task_due_time_changed"}
        for item in summary["suggestions"]
    )
    assert client.get("/api/v1/plans/current").json()["version"] == accepted["version"]

    draft = client.post("/api/v1/plans/adjustments/draft", json={}).json()
    assert sum(block["minutes"] for block in draft["blocks"]) == 60
    assert all(
        datetime.fromisoformat(block["ends_at"])
        <= datetime.fromisoformat("2026-10-19T20:00:00+08:00")
        for block in draft["blocks"]
    )
    _accept(client, draft)

    after = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert after["due_date"] == "2026-10-19"
    assert after["due_time"] == "20:00:00"


def test_completed_prerequisite_is_not_double_counted(client: TestClient) -> None:
    prerequisite = _task(client, "已完成前置")
    dependent = _task(client, "后续任务")
    client.post(
        f"/api/v1/tasks/{dependent['id']}/dependencies",
        json={"depends_on_task_id": prerequisite["id"]},
    )
    client.patch(
        f"/api/v1/tasks/{prerequisite['id']}/progress",
        json={"progress": "done", "reason": "验收已完成"},
    )
    _availability(client)

    draft = _generate(client)
    assert sum(block["minutes"] for block in draft["blocks"]) == 60
    assert {block["task_id"] for block in draft["blocks"]} == {dependent["id"]}
    _assert_every_task_once(draft, {prerequisite["id"], dependent["id"]})
    excluded = next(item for item in draft["unscheduled"] if item["task_id"] == prerequisite["id"])
    assert excluded["status"] == "excluded"

    after = client.get(f"/api/v1/tasks/{prerequisite['id']}").json()
    assert after["progress"] == "done"
    assert after["remaining_minutes"] == 0
