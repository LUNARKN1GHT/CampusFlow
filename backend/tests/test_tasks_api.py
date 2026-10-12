"""任务 API 集成测试（真实 PostgreSQL 测试库）。

重点覆盖验收要求：只有日期的 DDL 保持日期精度，不伪装成午夜/23:59。
"""

from fastapi.testclient import TestClient


def _create_task(
    client: TestClient,
    title: str = "写实验报告",
    *,
    due_date: str | None = "2026-10-15",
    extra: dict | None = None,
) -> dict:
    payload: dict = {"title": title, "due_date": due_date}
    if extra:
        payload.update(extra)
    response = client.post("/api/v1/tasks", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_date_only_ddl_keeps_date_precision(client: TestClient) -> None:
    response = client.post(
        "/api/v1/tasks", json={"title": "只写日期的作业", "due_date": "2026-10-15"}
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["due_date"] == "2026-10-15"
    assert data["due_time"] is None
    # 验收硬指标：响应中不得出现把日期伪装成午夜或 23:59 的时刻
    assert "00:00:00" not in response.text
    assert "23:59:00" not in response.text


def test_due_time_without_due_date_rejected(client: TestClient) -> None:
    response = client.post("/api/v1/tasks", json={"title": "只有时刻", "due_time": "23:59:00"})
    assert response.status_code == 400
    assert "日期" in response.json()["detail"]


def test_task_create_list_progress(client: TestClient) -> None:
    task = _create_task(client)
    assert task["progress"] == "not_started"
    assert task["priority"] == "medium"

    listed = client.get("/api/v1/tasks").json()
    assert [t["id"] for t in listed] == [task["id"]]

    done = client.patch(
        f"/api/v1/tasks/{task['id']}/progress",
        json={"progress": "done", "reason": "已经提交"},
    ).json()
    assert done["progress"] == "done"

    remaining = client.get("/api/v1/tasks", params={"progress": "not_started"}).json()
    assert remaining == []
    done_list = client.get("/api/v1/tasks", params={"progress": "done"}).json()
    assert [t["id"] for t in done_list] == [task["id"]]

    reopened = client.patch(
        f"/api/v1/tasks/{task['id']}/progress",
        json={"progress": "in_progress", "reason": "教师要求补交附件"},
    ).json()
    assert reopened["progress"] == "in_progress"

    history = client.get(f"/api/v1/tasks/{task['id']}/progress-history").json()
    assert [item["to_progress"] for item in history] == ["in_progress", "done"]
    assert history[0]["reason"] == "教师要求补交附件"


def test_task_update_merges_fields(client: TestClient) -> None:
    task = _create_task(client, extra={"description": "要交到教学网", "course_id": None})
    updated = client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={"title": "改过的标题", "description": None, "priority": "high"},
    ).json()
    assert updated["title"] == "改过的标题"
    assert updated["description"] is None
    assert updated["priority"] == "high"
    assert updated["due_date"] == "2026-10-15"  # 未提交字段不变


def test_task_update_rejects_null_required_fields(client: TestClient) -> None:
    task = _create_task(client)

    for field in ("title", "priority"):
        response = client.patch(f"/api/v1/tasks/{task['id']}", json={field: None})
        assert response.status_code == 422
        assert field in response.text


def test_task_progress_missing_task(client: TestClient) -> None:
    response = client.patch("/api/v1/tasks/999/progress", json={"progress": "done"})
    assert response.status_code == 404


def test_task_requires_course_exists(client: TestClient) -> None:
    response = client.post("/api/v1/tasks", json={"title": "挂到幽灵课", "course_id": 999})
    assert response.status_code == 404


def test_personal_target_independent_from_formal_due(client: TestClient) -> None:
    task = _create_task(
        client,
        extra={"due_date": "2026-10-20", "target_date": "2026-10-18", "due_note": "教学网通知"},
    )
    assert task["due_date"] == "2026-10-20"
    assert task["target_date"] == "2026-10-18"
    assert task["due_source"] == "user"
    assert task["due_note"] == "教学网通知"

    # 修改个人目标不改变正式 DDL
    updated = client.patch(f"/api/v1/tasks/{task['id']}", json={"target_date": "2026-10-19"}).json()
    assert updated["target_date"] == "2026-10-19"
    assert updated["due_date"] == "2026-10-20"
    assert updated["due_time"] is None

    # 修改正式截止时未提交说明则保留既有说明与来源
    moved = client.patch(f"/api/v1/tasks/{task['id']}", json={"due_date": "2026-10-22"}).json()
    assert moved["due_date"] == "2026-10-22"
    assert moved["due_note"] == "教学网通知"
    assert moved["due_source"] == "user"

    # 明确更新来源类型与说明
    sourced = client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={"due_source": "document", "due_note": "大纲第 3 页"},
    ).json()
    assert sourced["due_source"] == "document"
    assert sourced["due_note"] == "大纲第 3 页"


def test_due_source_rejects_null(client: TestClient) -> None:
    task = _create_task(client)
    response = client.patch(f"/api/v1/tasks/{task['id']}", json={"due_source": None})
    assert response.status_code == 422


def test_task_remaining_minutes_defaults_and_rejects_negative(client: TestClient) -> None:
    task = _create_task(client, extra={"estimated_minutes": 90})
    assert task["estimated_minutes"] == 90
    assert task["remaining_minutes"] == 90

    updated = client.patch(f"/api/v1/tasks/{task['id']}", json={"remaining_minutes": 35}).json()
    assert updated["estimated_minutes"] == 90
    assert updated["remaining_minutes"] == 35

    rejected = client.patch(f"/api/v1/tasks/{task['id']}", json={"remaining_minutes": -1})
    assert rejected.status_code == 422
