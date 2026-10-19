"""A006：变化检测只提出调整建议，不自动覆盖当前计划。"""

from fastapi.testclient import TestClient


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


def test_unchanged_inputs_have_no_adjustment_suggestion(client: TestClient) -> None:
    _task(client, "稳定任务")
    plan = _accepted_plan(client)

    summary = client.get("/api/v1/plans/adjustments").json()
    assert summary["has_plan"] is True
    assert summary["has_changes"] is False
    assert summary["auto_applied"] is False
    assert summary["requires_confirmation"] is True
    assert summary["suggestions"] == []
    assert summary["affected_task_ids"] == []
    assert summary["current_plan_id"] == plan["id"]
    assert summary["current_plan_version"] == plan["version"]

    response = client.post("/api/v1/plans/adjustments/draft", json={})
    assert response.status_code == 409


def test_progress_change_only_suggests_and_never_overwrites_plan(
    client: TestClient, fixed_planning_clock: None
) -> None:
    task = _task(client, "剩余耗时变化")
    plan = _accepted_plan(client)
    draft_count = len(client.get("/api/v1/plans/drafts").json())

    updated = client.patch(f"/api/v1/tasks/{task['id']}", json={"remaining_minutes": 30})
    assert updated.status_code == 200

    summary = client.get("/api/v1/plans/adjustments").json()
    assert summary["has_changes"] is True
    assert summary["auto_applied"] is False
    assert task["id"] in summary["affected_task_ids"]
    assert summary["suggested_task_ids"] == [task["id"]]
    assert any(item["code"] == "task_remaining_minutes_changed" for item in summary["suggestions"])
    assert "尚未修改" in summary["message"]

    # 只提出建议：当前计划与草案数量都不变
    current = client.get("/api/v1/plans/current").json()
    assert current["id"] == plan["id"]
    assert current["version"] == plan["version"]
    assert len(client.get("/api/v1/plans/drafts").json()) == draft_count

    created = client.post("/api/v1/plans/adjustments/draft", json={})
    assert created.status_code == 201, created.text
    adjustment = created.json()
    assert adjustment["status"] == "draft"
    assert [block["task_id"] for block in adjustment["blocks"]] == [task["id"]]

    # 新草案接受前，当前计划仍是旧版本
    still_current = client.get("/api/v1/plans/current").json()
    assert still_current["id"] == plan["id"]
    assert still_current["version"] == plan["version"]

    accepted = client.post(
        f"/api/v1/plans/drafts/{adjustment['id']}/accept",
        json={"expected_version": adjustment["version"]},
    )
    assert accepted.status_code == 200, accepted.text
    assert client.get("/api/v1/plans/current").json()["id"] == adjustment["id"]

    # 局部接受不修改原始 DDL
    task_after = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert task_after["due_date"] == "2026-10-20"
    assert task_after["remaining_minutes"] == 30


def test_availability_change_marks_all_tasks_affected(client: TestClient) -> None:
    _task(client, "任务一")
    _task(client, "任务二")
    plan = _accepted_plan(client)

    added = client.post(
        "/api/v1/availability-slots",
        json={"day_of_week": 1, "start_time": "18:00:00", "end_time": "20:00:00"},
    )
    assert added.status_code == 201

    summary = client.get("/api/v1/plans/adjustments").json()
    assert summary["has_changes"] is True
    assert summary["affected_all_tasks"] is True
    assert summary["suggested_task_ids"] == []
    assert any(item["code"] == "availability_changed" for item in summary["suggestions"])
    assert summary["current_plan_id"] == plan["id"]


def test_new_task_is_suggested_for_planning(client: TestClient) -> None:
    _task(client, "原任务")
    _accepted_plan(client)
    added = _task(client, "新增任务")

    summary = client.get("/api/v1/plans/adjustments").json()
    assert summary["has_changes"] is True
    assert added["id"] in summary["affected_task_ids"]
    assert any(
        item["code"] == "task_added" and item["task_id"] == added["id"]
        for item in summary["suggestions"]
    )
