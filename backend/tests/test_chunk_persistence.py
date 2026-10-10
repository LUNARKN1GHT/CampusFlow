"""D017 集成测试：持久化来源片段与定位坐标。

验收：重试不产生重复片段；版本变化后旧引用不误指向新文本；
页码、段落、区域坐标与置信度结构化保存。
"""

import pytest
from conftest import TEST_DATABASE_URL
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from campusflow.application.materials import fragments_to_chunks
from campusflow.application.ports.parsers import ParsedFragment, ParseOutcome, ParserLocator
from campusflow.infrastructure.repositories import SqlAlchemyMaterialRepository


@pytest.fixture
def workspace_id(client: TestClient) -> int:
    return client.get("/api/v1/settings").json()["workspace_id"]


def _repo():
    engine = create_engine(TEST_DATABASE_URL)
    factory = sessionmaker(bind=engine)
    session = factory()
    return session, SqlAlchemyMaterialRepository(session)


def _make_material(repo, workspace_id: int) -> int:
    material = repo.create(
        workspace_id,
        semester_id=None,
        course_id=None,
        class_name=None,
        title="坐标测试",
        publisher=None,
        published_at=None,
        source_type="text",
        source_url=None,
    )
    return material.id


def test_retry_does_not_create_duplicate_chunks(workspace_id: int) -> None:
    """同一批片段重复写入两次：不产生重复，返回同一批行（D017 验收）。"""
    session, repo = _repo()
    with session:
        material_id = _make_material(repo, workspace_id)
        version = repo.create_version(material_id, 1, "v1", None)
        session.commit()

        chunks = fragments_to_chunks(
            ParseOutcome(
                fragments=[
                    ParsedFragment(
                        seq=0, locator=ParserLocator(kind="paragraph", paragraph=1), text="第一段"
                    ),
                    ParsedFragment(
                        seq=1, locator=ParserLocator(kind="paragraph", paragraph=2), text="第二段"
                    ),
                ],
                failures=[],
            ),
            version.id,
        )
        first = repo.add_chunks(chunks)
        session.commit()
        second = repo.add_chunks(chunks)  # 模拟重试
        session.commit()

        assert [c.id for c in first] == [c.id for c in second]
        assert len(repo.list_chunks(version.id)) == 2

        # 数据库中确实只有两行
        engine = create_engine(TEST_DATABASE_URL)
        with engine.connect() as connection:
            count = connection.execute(
                text("SELECT count(*) FROM source_chunks WHERE version_id = :v"),
                {"v": version.id},
            ).scalar_one()
        assert count == 2


def test_old_version_references_stay_on_old_text(workspace_id: int) -> None:
    """版本更新后，旧版本的片段仍指向旧文本，不误指向新内容（D017 验收）。"""
    session, repo = _repo()
    with session:
        material_id = _make_material(repo, workspace_id)
        v1 = repo.create_version(material_id, 1, "旧版本", None)
        v1_chunks = repo.add_chunks(
            fragments_to_chunks(
                ParseOutcome(
                    fragments=[
                        ParsedFragment(
                            seq=0,
                            locator=ParserLocator(kind="paragraph", paragraph=1),
                            text="截止时间是 10 月 15 日",
                        )
                    ],
                    failures=[],
                ),
                v1.id,
            )
        )
        v2 = repo.create_version(material_id, 2, "教师更新版", None)
        repo.add_chunks(
            fragments_to_chunks(
                ParseOutcome(
                    fragments=[
                        ParsedFragment(
                            seq=0,
                            locator=ParserLocator(kind="paragraph", paragraph=1),
                            text="截止时间改为 10 月 22 日",
                        )
                    ],
                    failures=[],
                ),
                v2.id,
            )
        )
        session.commit()

        old_text = repo.list_chunks(v1.id)[0].text
        new_text = repo.list_chunks(v2.id)[0].text
        assert old_text == "截止时间是 10 月 15 日"
        assert new_text == "截止时间改为 10 月 22 日"
        assert v1_chunks[0].version_id == v1.id  # 旧引用绑定旧版本


def test_coordinates_and_confidence_persisted(workspace_id: int) -> None:
    """页码、段落、区域 bbox、置信度结构化保存（D017 验收）。"""
    session, repo = _repo()
    with session:
        material_id = _make_material(repo, workspace_id)
        version = repo.create_version(material_id, 1, None, None)
        chunks = fragments_to_chunks(
            ParseOutcome(
                fragments=[
                    ParsedFragment(
                        seq=0, locator=ParserLocator(kind="page", page=2), text="第 2 页"
                    ),
                    ParsedFragment(
                        seq=1,
                        locator=ParserLocator(kind="region", bbox=(0.1, 0.2, 0.9, 0.4)),
                        text="区域文字",
                        confidence=0.85,
                    ),
                ],
                failures=[],
            ),
            version.id,
        )
        repo.add_chunks(chunks)
        session.commit()

        saved = repo.list_chunks(version.id)
        assert saved[0].page == 2 and saved[0].paragraph is None and saved[0].bbox is None
        assert saved[1].bbox == (0.1, 0.2, 0.9, 0.4)
        assert saved[1].confidence == 0.85
        assert saved[1].locator_type == "region"


def test_text_import_response_exposes_coordinates(client: TestClient) -> None:
    """导入响应中的片段带结构化定位字段。"""
    response = client.post(
        "/api/v1/materials/text", json={"title": "坐标检查", "content": "第一段\n\n第二段"}
    )
    assert response.status_code == 201
    chunks = response.json()["chunks"]
    assert chunks[0]["paragraph"] == 1
    assert chunks[1]["paragraph"] == 2
    assert chunks[0]["page"] is None
    assert chunks[0]["bbox"] is None
    assert chunks[0]["confidence"] is None
