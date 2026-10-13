"""D018 集成测试：授权原文读取与定位接口。

验收：空间权限贯穿原文件和预览；删除或失效引用返回明确状态。
"""

from conftest import TEST_DATABASE_URL
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from campusflow.infrastructure.db.models import Material, MaterialVersion, Workspace

TEXT = "原文读取测试。\n\n第二段内容。"


def _import(client: TestClient) -> dict:
    response = client.post("/api/v1/materials/text", json={"title": "原文测试", "content": TEXT})
    assert response.status_code == 201
    return response.json()


def test_versions_and_chunks_listing(client: TestClient) -> None:
    imported = _import(client)
    material_id = imported["material"]["id"]

    versions = client.get(f"/api/v1/materials/{material_id}/versions").json()
    assert len(versions) == 1
    assert versions[0]["version_no"] == 1

    chunks = client.get(f"/api/v1/materials/{material_id}/versions/1/chunks").json()
    assert [c["paragraph"] for c in chunks] == [1, 2]
    assert chunks[0]["text"] == "原文读取测试。"

    missing_version = client.get(f"/api/v1/materials/{material_id}/versions/99/chunks")
    assert missing_version.status_code == 404


def test_read_original_file_with_authorization(client: TestClient) -> None:
    """已登录空间内可读取原文，内容一致。"""
    imported = _import(client)
    material_id = imported["material"]["id"]

    response = client.get(f"/api/v1/materials/{material_id}/versions/1/file")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.content.decode("utf-8") == TEXT


def test_read_requires_workspace_ownership(client: TestClient) -> None:
    """其他空间的资料：版本/片段/原文全部 404（空间权限贯穿）。"""
    engine = create_engine(TEST_DATABASE_URL)
    with Session(engine) as session:
        other = Workspace(name="另一个空间")
        session.add(other)
        session.flush()
        material = Material(workspace_id=other.id, title="他人资料", source_type="text")
        session.add(material)
        session.commit()
        other_id = material.id

    assert client.get(f"/api/v1/materials/{other_id}/versions").status_code == 404
    assert client.get(f"/api/v1/materials/{other_id}/versions/1/chunks").status_code == 404
    assert client.get(f"/api/v1/materials/{other_id}/versions/1/file").status_code == 404


def test_deleted_or_unavailable_reference_returns_clear_status(client: TestClient) -> None:
    """资料删除后读取返回 404；文件缺失时返回"来源已不可用"（D018 验收）。"""
    # 已删除资料
    assert client.get("/api/v1/materials/999/versions/1/file").status_code == 404

    # 版本无存储键（模拟失效引用）
    imported = _import(client)
    material_id = imported["material"]["id"]
    engine = create_engine(TEST_DATABASE_URL)
    with Session(engine) as session:
        row = session.scalars(
            __import__("sqlalchemy")
            .select(MaterialVersion)
            .where(MaterialVersion.material_id == material_id)
        ).one()
        row.storage_key = None
        session.commit()

    response = client.get(f"/api/v1/materials/{material_id}/versions/1/file")
    assert response.status_code == 404
    assert response.json()["detail"] == "来源已不可用"
