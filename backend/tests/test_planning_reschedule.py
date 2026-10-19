"""A005：保留锁定时间块与范围外安排的局部重排。"""

from fastapi.testclient import TestClient


def _task(client: TestClient, title: str) -> dict:
    response = client.post(
        "/api/v1/tasks",
        json={"title": title, "estimated_minutes": 60, "remaining_minutes": 60},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _availability(client: TestClient) -> None:
    response = client.post(
        "/api/v1/availability-slots",
        json={"day_of_week": 0, "start_time": "18:00:00", "end_time": "22:00:00"},
    )
    assert response.status_code == 201, response.text


def _accepted_plan(client: TestClient) -> dict:
    _availability(client)
    created = client.post(
        "/api/v1/plans/drafts",
        json={
            "start_date": "2026-10-19",
            "end_date": "2026-10-20",
            "assumptions": {"buffer_minutes": 0},
        },
    )
    assert created.status_code == 201, created.text
    draft = created.json()
    accepted = client.post(
        f"/api/v1/plans/drafts/{draft['id']}/accept",
        json={"expected_version": draft["version"]},
    )
    assert accepted.status_code == 200, accepted.text
    return accepted.json()


def test_reschedule_preserves_locked_block(client: TestClient, fixed_planning_clock: None) -> None:
    first = _task(client, "锁定任务")
    _task(client, "可移动任务")
    plan = _accepted_plan(client)
    locked_block = plan["blocks"][0]

    locked = client.post(
        f"/api/v1/plans/blocks/{locked_block['id']}/lock",
        json={"locked": True, "expected_version": plan["version"]},
    )
    assert locked.status_code == 200, locked.text

    response = client.post(
        "/api/v1/plans/reschedule",
        json={"task_ids": [first["id"]]},
    )
    assert response.status_code == 201, response.text
    draft = response.json()
    assert draft["status"] == "draft"

    kept = [block for block in draft["blocks"] if block["task_id"] == locked_block["task_id"]]
    assert len(kept) == 1
    assert kept[0]["starts_at"] == locked_block["starts_at"]
    assert kept[0]["ends_at"] == locked_block["ends_at"]
    assert kept[0]["locked"] is True
    assert sum(block["minutes"] for block in draft["blocks"]) == 120

    accepted = client.post(
        f"/api/v1/plans/drafts/{draft['id']}/accept",
        json={"expected_version": draft["version"]},
    )
    assert accepted.status_code == 200, accepted.text


def test_reschedule_outside_scope_keeps_other_task_blocks(
    client: TestClient, fixed_planning_clock: None
) -> None:
    scoped = _task(client, "重排任务")
    _task(client, "范围外任务")
    plan = _accepted_plan(client)
    outside_block = plan["blocks"][-1]
    outside_task_id = outside_block["task_id"]

    response = client.post(
        "/api/v1/plans/reschedule",
        json={"task_ids": [scoped["id"]]},
    )
    assert response.status_code == 201, response.text
    draft = response.json()

    kept = [block for block in draft["blocks"] if block["task_id"] == outside_task_id]
    assert len(kept) == 1
    assert kept[0]["starts_at"] == outside_block["starts_at"]
    assert kept[0]["ends_at"] == outside_block["ends_at"]
    assert kept[0]["locked"] is False

    accepted = client.post(
        f"/api/v1/plans/drafts/{draft['id']}/accept",
        json={"expected_version": draft["version"]},
    )
    assert accepted.status_code == 200, accepted.text


def test_fully_locked_task_is_not_scheduled_twice(
    client: TestClient, fixed_planning_clock: None
) -> None:
    _task(client, "全部锁定")
    plan = _accepted_plan(client)
    block = plan["blocks"][0]
    locked = client.post(
        f"/api/v1/plans/blocks/{block['id']}/lock",
        json={"locked": True, "expected_version": plan["version"]},
    )
    assert locked.status_code == 200, locked.text

    response = client.post("/api/v1/plans/reschedule", json={})
    assert response.status_code == 201, response.text
    draft = response.json()
    assert len(draft["blocks"]) == 1
    assert draft["blocks"][0]["minutes"] == 60
    assert draft["blocks"][0]["locked"] is True
    assert draft["coverage"]["tasks_scheduled"] == 1
    assert draft["unscheduled"] == []


def test_reschedule_requires_accepted_plan(client: TestClient) -> None:
    _availability(client)
    created = client.post(
        "/api/v1/plans/drafts",
        json={"start_date": "2026-10-19", "end_date": "2026-10-20"},
    )
    assert created.status_code == 201

    response = client.post("/api/v1/plans/reschedule", json={})
    assert response.status_code == 404
