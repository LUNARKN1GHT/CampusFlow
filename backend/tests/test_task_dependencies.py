"""A001：任务依赖与子任务模型。

覆盖验收：包含关系（parent_task_id）与先后依赖（task_dependencies）分开保存；
子任务保留父任务和自己的来源关联。
"""

from conftest import TEST_DATABASE_URL
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text


def _create_task(client: TestClient, title: str, **extra) -> dict:
    response = client.post("/api/v1/tasks", json={"title": title, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def test_subtask_keeps_parent_without_creating_dependency(client: TestClient) -> None:
    parent = _create_task(client, "写课程论文")
    child = _create_task(client, "整理参考文献", parent_task_id=parent["id"])

    assert child["parent_task_id"] == parent["id"]

    relations = client.get(f"/api/v1/tasks/{parent['id']}/relations")
    assert relations.status_code == 200
    data = relations.json()
    assert data["parent"] is None
    assert [item["id"] for item in data["subtasks"]] == [child["id"]]
    # 包含关系不自动变成先后依赖
    assert data["prerequisites"] == []
    assert data["dependents"] == []

    engine = create_engine(TEST_DATABASE_URL)
    with engine.connect() as connection:
        count = connection.execute(text("SELECT count(*) FROM task_dependencies")).scalar_one()
    assert count == 0


def test_dependency_crud_is_separate_from_containment(client: TestClient) -> None:
    first = _create_task(client, "阅读材料")
    second = _create_task(client, "写读书报告")

    created = client.post(
        f"/api/v1/tasks/{second['id']}/dependencies",
        json={"depends_on_task_id": first["id"]},
    )
    assert created.status_code == 201, created.text
    assert created.json()["task_id"] == second["id"]
    assert created.json()["depends_on_task_id"] == first["id"]

    # 重复添加保持幂等，不会产生第二条边
    again = client.post(
        f"/api/v1/tasks/{second['id']}/dependencies",
        json={"depends_on_task_id": first["id"]},
    )
    assert again.status_code == 201
    assert again.json()["id"] == created.json()["id"]

    relations = client.get(f"/api/v1/tasks/{second['id']}/relations").json()
    assert [item["id"] for item in relations["prerequisites"]] == [first["id"]]
    assert relations["parent"] is None
    assert relations["subtasks"] == []

    dependents = client.get(f"/api/v1/tasks/{first['id']}/relations").json()
    assert [item["id"] for item in dependents["dependents"]] == [second["id"]]

    removed = client.delete(f"/api/v1/tasks/{second['id']}/dependencies/{first['id']}")
    assert removed.status_code == 204
    assert client.get(f"/api/v1/tasks/{second['id']}/relations").json()["prerequisites"] == []


def test_task_cannot_depend_on_itself(client: TestClient) -> None:
    task = _create_task(client, "独立任务")
    response = client.post(
        f"/api/v1/tasks/{task['id']}/dependencies",
        json={"depends_on_task_id": task["id"]},
    )
    assert response.status_code == 400
    assert "自己" in response.json()["detail"]


def test_parent_chain_cannot_form_cycle(client: TestClient) -> None:
    outer = _create_task(client, "总任务")
    inner = _create_task(client, "子任务", parent_task_id=outer["id"])

    response = client.patch(
        f"/api/v1/tasks/{outer['id']}/parent", json={"parent_task_id": inner["id"]}
    )
    assert response.status_code == 400
    assert "成环" in response.json()["detail"]


def test_subtask_keeps_its_own_source_association(client: TestClient) -> None:
    parent = _create_task(client, "父任务")
    child = _create_task(client, "子任务")

    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        workspace_id = connection.execute(text("SELECT id FROM workspaces LIMIT 1")).scalar_one()
        material_id = connection.execute(
            text(
                "INSERT INTO materials (workspace_id, title, source_type, status, archived) "
                "VALUES (:workspace_id, '课程通知', 'paste', 'ready', false) RETURNING id"
            ),
            {"workspace_id": workspace_id},
        ).scalar_one()
        connection.execute(
            text("UPDATE tasks SET source_material_id = :material_id WHERE id = :task_id"),
            {"material_id": material_id, "task_id": child["id"]},
        )

    updated = client.patch(
        f"/api/v1/tasks/{child['id']}/parent", json={"parent_task_id": parent["id"]}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["parent_task_id"] == parent["id"]
    assert updated.json()["source_state"] == "available"

    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT parent_task_id, source_material_id FROM tasks WHERE id = :task_id"),
            {"task_id": child["id"]},
        ).one()
    assert row.parent_task_id == parent["id"]
    assert row.source_material_id == material_id
