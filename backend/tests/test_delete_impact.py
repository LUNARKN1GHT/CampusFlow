"""D008 集成测试：资料删除影响预览与确认。

验收：预览列出将失效的版本/片段/关联个人任务；用户能选择保留个人任务
或一并删除；执行内容改变后旧确认失效。
"""

from conftest import TEST_DATABASE_URL
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

TEXT = "删除测试内容。\n\n第二段。"


def _import(client: TestClient) -> dict:
    response = client.post("/api/v1/materials/text", json={"title": "待删除", "content": TEXT})
    assert response.status_code == 201
    return response.json()


def _create_linked_task(client: TestClient, material_id: int) -> dict:
    """创建任务并直接关联到资料（模拟 R 系列将来写入的来源关系）。"""
    task = client.post("/api/v1/tasks", json={"title": "关联任务"}).json()
    engine = create_engine(TEST_DATABASE_URL)
    with engine.connect() as connection:
        connection.execute(
            text("UPDATE tasks SET source_material_id = :m WHERE id = :t"),
            {"m": material_id, "t": task["id"]},
        )
        connection.commit()
    return task


def test_preview_lists_impact_and_confirm_token(client: TestClient) -> None:
    imported = _import(client)
    material_id = imported["material"]["id"]
    task = _create_linked_task(client, material_id)

    impact = client.get(f"/api/v1/materials/{material_id}/delete-impact").json()
    assert impact["version_count"] == 1
    assert impact["chunk_count"] == 2
    assert impact["linked_task_ids"] == [task["id"]]
    assert impact["confirm_token"]


def test_delete_with_token_removes_material_and_unlinks_task(client: TestClient) -> None:
    """保留个人任务：资料删除，任务保留且来源解除（D008 验收）。"""
    imported = _import(client)
    material_id = imported["material"]["id"]
    task = _create_linked_task(client, material_id)

    token = client.get(f"/api/v1/materials/{material_id}/delete-impact").json()["confirm_token"]
    response = client.post(
        f"/api/v1/materials/{material_id}/delete",
        json={"confirm_token": token, "keep_tasks": True},
    )
    assert response.status_code == 204

    assert client.get(f"/api/v1/materials/{material_id}").status_code == 404
    assert client.get("/api/v1/materials").json() == []
    # 任务保留
    kept = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert kept["title"] == "关联任务"


def test_delete_with_keep_tasks_false_removes_task(client: TestClient) -> None:
    """一并删除：关联个人任务随资料删除。"""
    imported = _import(client)
    material_id = imported["material"]["id"]
    task = _create_linked_task(client, material_id)

    token = client.get(f"/api/v1/materials/{material_id}/delete-impact").json()["confirm_token"]
    response = client.post(
        f"/api/v1/materials/{material_id}/delete",
        json={"confirm_token": token, "keep_tasks": False},
    )
    assert response.status_code == 204
    assert client.get(f"/api/v1/tasks/{task['id']}").status_code == 404


def test_stale_token_rejected_after_content_change(client: TestClient) -> None:
    """执行内容改变后旧确认失效（D008 验收）：预览后又关联了新任务。"""
    imported = _import(client)
    material_id = imported["material"]["id"]

    old_token = client.get(f"/api/v1/materials/{material_id}/delete-impact").json()["confirm_token"]
    _create_linked_task(client, material_id)  # 内容变化：多了一条关联任务

    response = client.post(
        f"/api/v1/materials/{material_id}/delete",
        json={"confirm_token": old_token, "keep_tasks": True},
    )
    assert response.status_code == 400
    assert "重新预览" in response.json()["detail"]

    # 新预览的 token 可用
    new_token = client.get(f"/api/v1/materials/{material_id}/delete-impact").json()["confirm_token"]
    assert new_token != old_token
    ok = client.post(
        f"/api/v1/materials/{material_id}/delete",
        json={"confirm_token": new_token, "keep_tasks": True},
    )
    assert ok.status_code == 204


def test_delete_removes_original_files(client: TestClient, tmp_path) -> None:
    """删除后私有存储中的原文件被清理。"""
    from campusflow.infrastructure.storage.local import LocalFileStorage

    storage = LocalFileStorage(tmp_path)
    client.app.state.file_storage = storage

    imported = _import(client)
    material_id = imported["material"]["id"]
    assert any(path.is_file() for path in tmp_path.rglob("*"))

    token = client.get(f"/api/v1/materials/{material_id}/delete-impact").json()["confirm_token"]
    client.post(
        f"/api/v1/materials/{material_id}/delete",
        json={"confirm_token": token, "keep_tasks": True},
    )
    assert not any(path.is_file() for path in tmp_path.rglob("*"))
