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


def test_task_filters_by_due_range_and_keeps_undated_last(client: TestClient) -> None:
    undated = _create_task(client, "没有日期的任务", due_date=None)
    early = _create_task(client, "早的任务", due_date="2026-10-10")
    middle = _create_task(client, "中间的任务", due_date="2026-10-15")
    late = _create_task(client, "晚的任务", due_date="2026-10-20")

    within = client.get(
        "/api/v1/tasks", params={"due_from": "2026-10-12", "due_to": "2026-10-18"}
    ).json()
    assert [t["id"] for t in within] == [middle["id"]]

    open_ended = client.get("/api/v1/tasks", params={"due_from": "2026-10-15"}).json()
    assert [t["id"] for t in open_ended] == [middle["id"], late["id"]]

    # 日期缺失项不参与区间筛选，但在未筛选时排在最后，位置明确
    unfiltered = client.get("/api/v1/tasks").json()
    assert [t["id"] for t in unfiltered] == [
        early["id"],
        middle["id"],
        late["id"],
        undated["id"],
    ]

    reversed_range = client.get(
        "/api/v1/tasks", params={"due_from": "2026-10-18", "due_to": "2026-10-12"}
    )
    assert reversed_range.status_code == 400


def test_semester_filter_separates_history_and_course_less_tasks(client: TestClient) -> None:
    def create_semester(name: str, start: str, end: str) -> int:
        return client.post(
            "/api/v1/semesters", json={"name": name, "start_date": start, "end_date": end}
        ).json()["id"]

    current = create_semester("2026 秋", "2026-09-01", "2027-01-31")
    history = create_semester("2025 秋", "2025-09-01", "2026-01-31")
    current_course = client.post(
        "/api/v1/courses", json={"semester_id": current, "name": "数据结构"}
    ).json()["id"]
    history_course = client.post(
        "/api/v1/courses", json={"semester_id": history, "name": "程序设计"}
    ).json()["id"]

    current_task = _create_task(client, "本学期作业", extra={"course_id": current_course})
    history_task = _create_task(client, "上学期作业", extra={"course_id": history_course})
    course_less = _create_task(client, "没有课程的任务")

    filtered = client.get("/api/v1/tasks", params={"semester_id": current}).json()
    assert [t["id"] for t in filtered] == [current_task["id"]]
    assert history_task["id"] not in [t["id"] for t in filtered]

    # 未关联课程的任务不属于任何学期：筛选学期时不混入，未筛选时仍可见
    assert course_less["id"] not in [t["id"] for t in filtered]
    all_ids = [t["id"] for t in client.get("/api/v1/tasks").json()]
    assert {current_task["id"], history_task["id"], course_less["id"]} <= set(all_ids)

    combined = client.get(
        "/api/v1/tasks",
        params={"semester_id": current, "due_from": "2026-10-01", "due_to": "2026-10-31"},
    ).json()
    assert [t["id"] for t in combined] == [current_task["id"]]


def test_task_remaining_minutes_defaults_and_rejects_negative(client: TestClient) -> None:
    task = _create_task(client, extra={"estimated_minutes": 90})
    assert task["estimated_minutes"] == 90
    assert task["remaining_minutes"] == 90

    updated = client.patch(f"/api/v1/tasks/{task['id']}", json={"remaining_minutes": 35}).json()
    assert updated["estimated_minutes"] == 90
    assert updated["remaining_minutes"] == 35

    rejected = client.patch(f"/api/v1/tasks/{task['id']}", json={"remaining_minutes": -1})
    assert rejected.status_code == 422
