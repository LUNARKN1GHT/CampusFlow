"""D007 集成测试：资料归档与恢复。

验收：归档保留原文和历史、默认检索排除；归档不删除正式任务（版本/片段不动）；
恢复后的引用状态与资料有效范围一致。
"""

from conftest import TEST_DATABASE_URL
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from campusflow.infrastructure.repositories import SqlAlchemyMaterialRepository

TEXT = "归档测试内容。\n\n第二段保留。"


def _import(client: TestClient, title: str = "待归档") -> dict:
    response = client.post("/api/v1/materials/text", json={"title": title, "content": TEXT})
    assert response.status_code == 201
    return response.json()


def _chunk_count(material_id: int) -> int:
    engine = create_engine(TEST_DATABASE_URL)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        repo = SqlAlchemyMaterialRepository(session)
        versions = repo.list_versions(material_id)
        return sum(len(repo.list_chunks(v.id)) for v in versions)


def test_archive_hides_from_default_list_but_keeps_history(client: TestClient) -> None:
    imported = _import(client)
    material_id = imported["material"]["id"]
    chunks_before = _chunk_count(material_id)

    archived = client.post(f"/api/v1/materials/{material_id}/archive").json()
    assert archived["archived"] is True

    # 默认列表排除
    default_list = client.get("/api/v1/materials").json()
    assert default_list == []
    # include_archived 可见，历史元数据保留
    with_archived = client.get("/api/v1/materials", params={"include_archived": True}).json()
    assert [m["id"] for m in with_archived] == [material_id]
    assert with_archived[0]["title"] == "待归档"
    # 原文片段（历史）不动——归档不删除任务与片段
    assert _chunk_count(material_id) == chunks_before == 2


def test_restore_brings_material_back_with_references(client: TestClient) -> None:
    """恢复后引用状态与有效范围一致：重新出现在默认列表，片段完整。"""
    imported = _import(client)
    material_id = imported["material"]["id"]
    client.post(f"/api/v1/materials/{material_id}/archive")

    restored = client.post(f"/api/v1/materials/{material_id}/restore").json()
    assert restored["archived"] is False

    default_list = client.get("/api/v1/materials").json()
    assert [m["id"] for m in default_list] == [material_id]
    assert _chunk_count(material_id) == 2


def test_archive_unknown_material(client: TestClient) -> None:
    response = client.post("/api/v1/materials/999/archive")
    assert response.status_code == 404
    assert response.json() == {"detail": "资料不存在"}
