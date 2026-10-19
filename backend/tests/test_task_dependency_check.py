"""A002：依赖成环与取消/缺失前置的阻塞检查。"""

from conftest import TEST_DATABASE_URL
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text


def _create_task(client: TestClient, title: str, **extra) -> dict:
    response = client.post("/api/v1/tasks", json={"title": title, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def _depend(client: TestClient, task_id: int, depends_on: int):
    return client.post(
        f"/api/v1/tasks/{task_id}/dependencies",
        json={"depends_on_task_id": depends_on},
    )


def test_new_dependency_cycle_is_rejected_with_path(client: TestClient) -> None:
    first = _create_task(client, "准备实验")
    second = _create_task(client, "写报告")
    third = _create_task(client, "提交作业")

    assert _depend(client, first["id"], second["id"]).status_code == 201
    assert _depend(client, second["id"], third["id"]).status_code == 201
    response = _depend(client, third["id"], first["id"])

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "成环" in detail
    assert "准备实验" in detail and "写报告" in detail and "提交作业" in detail

    # 被拒绝的边没有落库
    relations = client.get(f"/api/v1/tasks/{third['id']}/relations").json()
    assert relations["prerequisites"] == []


def test_check_reports_cycle_and_blocks_tasks(client: TestClient) -> None:
    first = _create_task(client, "任务甲")
    second = _create_task(client, "任务乙")

    # 模拟历史数据中已经成环（接口已阻止新增）
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
                {
                    "workspace_id": workspace_id,
                    "task_id": task_id,
                    "depends_on": depends_on,
                },
            )

    data = client.get("/api/v1/tasks/dependencies/check").json()
    assert data["valid"] is False
    assert len(data["cycles"]) == 1
    assert data["cycles"][0]["task_ids"] == [first["id"], second["id"]]
    blocked_ids = {item["task_id"] for item in data["blocked_tasks"]}
    assert blocked_ids == {first["id"], second["id"]}
    assert all(
        any(reason["code"] == "dependency_cycle" for reason in item["reasons"])
        for item in data["blocked_tasks"]
    )


def test_cancelled_prerequisite_blocks_dependent(client: TestClient) -> None:
    prerequisite = _create_task(client, "先读文献")
    dependent = _create_task(client, "再写综述")
    assert _depend(client, dependent["id"], prerequisite["id"]).status_code == 201

    cancelled = client.patch(
        f"/api/v1/tasks/{prerequisite['id']}/progress",
        json={"progress": "cancelled", "reason": "课程取消"},
    )
    assert cancelled.status_code == 200

    data = client.get("/api/v1/tasks/dependencies/check").json()
    assert data["valid"] is False
    assert data["cycles"] == []
    assert [
        (item["task_id"], item["depends_on_task_id"]) for item in data["cancelled_prerequisites"]
    ] == [(dependent["id"], prerequisite["id"])]
    blocked = {item["task_id"]: item for item in data["blocked_tasks"]}
    assert dependent["id"] in blocked
    assert blocked[dependent["id"]]["reasons"][0]["code"] == "prerequisite_cancelled"
    assert "已取消" in blocked[dependent["id"]]["reasons"][0]["message"]


def test_prerequisite_outside_selected_semester_is_missing(client: TestClient) -> None:
    semester_a = client.post(
        "/api/v1/semesters",
        json={"name": "2026 秋", "start_date": "2026-09-01", "end_date": "2027-01-31"},
    ).json()
    semester_b = client.post(
        "/api/v1/semesters",
        json={"name": "2027 春", "start_date": "2027-02-01", "end_date": "2027-07-31"},
    ).json()
    course_a = client.post(
        "/api/v1/courses", json={"semester_id": semester_a["id"], "name": "课程 A"}
    ).json()
    course_b = client.post(
        "/api/v1/courses", json={"semester_id": semester_b["id"], "name": "课程 B"}
    ).json()

    earlier = _create_task(client, "春季前置", course_id=course_b["id"])
    later = _create_task(client, "秋季任务", course_id=course_a["id"])
    assert _depend(client, later["id"], earlier["id"]).status_code == 201

    data = client.get(
        "/api/v1/tasks/dependencies/check", params={"semester_id": semester_a["id"]}
    ).json()
    assert data["valid"] is False
    assert data["checked_tasks"] == 1
    assert [
        (item["task_id"], item["depends_on_task_id"]) for item in data["missing_prerequisites"]
    ] == [(later["id"], earlier["id"])]
    assert data["missing_prerequisites"][0]["code"] == "prerequisite_out_of_scope"
    blocked = {item["task_id"]: item for item in data["blocked_tasks"]}
    assert blocked[later["id"]]["reasons"][0]["code"] == "prerequisite_out_of_scope"

    # 不选学期时两个任务都在范围内，依赖可满足
    whole = client.get("/api/v1/tasks/dependencies/check").json()
    assert whole["missing_prerequisites"] == []
