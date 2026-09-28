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

    done = client.patch(f"/api/v1/tasks/{task['id']}/progress", json={"progress": "done"}).json()
    assert done["progress"] == "done"

    remaining = client.get("/api/v1/tasks", params={"progress": "not_started"}).json()
    assert remaining == []
    done_list = client.get("/api/v1/tasks", params={"progress": "done"}).json()
    assert [t["id"] for t in done_list] == [task["id"]]


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


def test_task_progress_missing_task(client: TestClient) -> None:
    response = client.patch("/api/v1/tasks/999/progress", json={"progress": "done"})
    assert response.status_code == 404


def test_task_requires_course_exists(client: TestClient) -> None:
    response = client.post("/api/v1/tasks", json={"title": "挂到幽灵课", "course_id": 999})
    assert response.status_code == 404
