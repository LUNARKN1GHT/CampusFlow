"""A004：可编辑子任务、拆分建议与父子耗时汇总。"""

from fastapi.testclient import TestClient


def _task(client: TestClient, title: str, **overrides) -> dict:
    payload = {
        "title": title,
        "estimated_minutes": 120,
        "remaining_minutes": 120,
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


def test_split_suggestion_is_editable_and_not_persisted(client: TestClient) -> None:
    parent = _task(client, "课程论文")

    response = client.post(f"/api/v1/tasks/{parent['id']}/split-suggestions", json={})
    assert response.status_code == 200, response.text
    suggestion = response.json()
    assert suggestion["suggestion"] is True
    assert suggestion["editable"] is True
    assert len(suggestion["items"]) == 3
    assert sum(item["remaining_minutes"] for item in suggestion["items"]) == 120
    assert all(item["suggested"] is True for item in suggestion["items"])

    # 建议只读，不自动创建子任务
    assert client.get(f"/api/v1/tasks/{parent['id']}/subtasks").json() == []


def test_create_edit_and_roll_up_subtasks(client: TestClient) -> None:
    parent = _task(client, "课程论文")
    created = client.post(
        f"/api/v1/tasks/{parent['id']}/subtasks",
        json={
            "items": [
                {"title": "阅读文献", "remaining_minutes": 40},
                {"title": "写初稿", "remaining_minutes": 60},
            ]
        },
    )
    assert created.status_code == 201, created.text
    children = created.json()
    assert [item["parent_task_id"] for item in children] == [parent["id"], parent["id"]]

    rollup = client.get(f"/api/v1/tasks/{parent['id']}/rollup").json()
    assert rollup["subtask_count"] == 2
    assert rollup["estimated_minutes"] == 100
    assert rollup["remaining_minutes"] == 100
    assert rollup["percent_complete"] == 0

    # 编辑子任务后汇总随之变化；父任务本身字段不被改写
    client.patch(f"/api/v1/tasks/{children[0]['id']}", json={"remaining_minutes": 10})
    client.patch(
        f"/api/v1/tasks/{children[1]['id']}/progress",
        json={"progress": "done", "reason": "已完成"},
    )
    rollup = client.get(f"/api/v1/tasks/{parent['id']}/rollup").json()
    assert rollup["remaining_minutes"] == 10
    assert rollup["percent_complete"] == 90
    parent_after = client.get(f"/api/v1/tasks/{parent['id']}").json()
    assert parent_after["estimated_minutes"] == 120
    assert parent_after["remaining_minutes"] == 120

    relations = client.get(f"/api/v1/tasks/{parent['id']}/relations").json()
    assert relations["rollup"] == rollup


def test_task_list_rollup_matches_detail_for_parent(client: TestClient) -> None:
    """#557：列表卡片与详情使用同一份父任务汇总，且不改写父任务自身字段。"""
    parent = _task(client, "课程论文", estimated_minutes=60, remaining_minutes=60)
    plain = _task(client, "普通任务", estimated_minutes=30, remaining_minutes=30)
    children = client.post(
        f"/api/v1/tasks/{parent['id']}/subtasks",
        json={
            "items": [
                {"title": "整理需求", "remaining_minutes": 20},
                {"title": "绘制流程图", "remaining_minutes": 40},
            ]
        },
    ).json()

    listing = {item["id"]: item for item in client.get("/api/v1/tasks").json()}
    assert listing[plain["id"]]["subtask_rollup"] is None
    assert listing[parent["id"]]["remaining_minutes"] == 60
    assert listing[parent["id"]]["subtask_rollup"] == client.get(
        f"/api/v1/tasks/{parent['id']}/rollup"
    ).json()

    client.patch(
        f"/api/v1/tasks/{children[0]['id']}/progress",
        json={"progress": "done", "reason": "已完成"},
    )
    parent_item = next(
        item for item in client.get("/api/v1/tasks").json() if item["id"] == parent["id"]
    )
    assert parent_item["subtask_rollup"]["remaining_minutes"] == 40
    assert parent_item["subtask_rollup"]["percent_complete"] == 33
    assert parent_item["subtask_rollup"] == client.get(
        f"/api/v1/tasks/{parent['id']}/rollup"
    ).json()

    # 全部子任务完成后卡片汇总为剩余 0 / 完成度 100%，与详情一致
    client.patch(
        f"/api/v1/tasks/{children[1]['id']}/progress",
        json={"progress": "done", "reason": "已完成"},
    )
    parent_item = next(
        item for item in client.get("/api/v1/tasks").json() if item["id"] == parent["id"]
    )
    assert parent_item["subtask_rollup"]["remaining_minutes"] == 0
    assert parent_item["subtask_rollup"]["percent_complete"] == 100
    assert parent_item["subtask_rollup"] == client.get(
        f"/api/v1/tasks/{parent['id']}/rollup"
    ).json()


def test_parent_minutes_are_not_double_counted_in_schedule(
    client: TestClient, fixed_planning_clock: None
) -> None:
    parent = _task(client, "大作业", estimated_minutes=120, remaining_minutes=120)
    client.post(
        f"/api/v1/tasks/{parent['id']}/subtasks",
        json={
            "items": [
                {"title": "资料整理", "remaining_minutes": 30},
                {"title": "实现", "remaining_minutes": 30},
            ]
        },
    )
    _availability(client)

    response = client.post(
        "/api/v1/plans/drafts",
        json={
            "start_date": "2026-10-19",
            "end_date": "2026-10-20",
            "assumptions": {"buffer_minutes": 0},
        },
    )
    assert response.status_code == 201, response.text
    draft = response.json()
    assert sum(block["minutes"] for block in draft["blocks"]) == 60
    assert all(block["task_id"] != parent["id"] for block in draft["blocks"])
    excluded = next(item for item in draft["unscheduled"] if item["task_id"] == parent["id"])
    assert excluded["status"] == "excluded"
    assert excluded["reason_code"] == "container_with_subtasks"
    assert draft["coverage"]["tasks_scheduled"] == 2
    assert draft["coverage"]["tasks_excluded"] == 1


def test_only_one_subtask_level_is_allowed(client: TestClient) -> None:
    parent = _task(client, "顶层任务")
    child = _task(client, "子任务", parent_task_id=parent["id"])

    response = client.post(
        f"/api/v1/tasks/{child['id']}/subtasks",
        json={"items": [{"title": "孙任务"}]},
    )
    assert response.status_code == 400
    assert "一层" in response.json()["detail"]
