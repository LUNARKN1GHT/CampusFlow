"""D001 集成测试：资料、版本与来源元数据模型。

验收：发布时间与导入时间分开；未知来源字段保留为空；版本关联稳定。
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from conftest import TEST_DATABASE_URL
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from campusflow.domain.materials import next_version_no
from campusflow.domain.states import MaterialSourceType, MaterialStatus
from campusflow.infrastructure.repositories import (
    SqlAlchemyMaterialRepository,
)

TZ = ZoneInfo("Asia/Shanghai")


@pytest.fixture
def workspace_id(client: TestClient) -> int:
    return client.get("/api/v1/settings").json()["workspace_id"]


def _make_material_repo():
    engine = create_engine(TEST_DATABASE_URL)
    factory = sessionmaker(bind=engine)
    session = factory()
    return session, SqlAlchemyMaterialRepository(session)


def test_material_keeps_unknown_source_fields_null(workspace_id: int) -> None:
    """未知发布方/发布时间/来源链接保留为 NULL，不伪造（D001 验收）。"""
    session, repo = _make_material_repo()
    with session:
        material = repo.create(
            workspace_id,
            semester_id=None,
            course_id=None,
            class_name=None,
            title="群聊通知截图",
            publisher=None,
            published_at=None,
            source_type=MaterialSourceType.IMAGE,
            source_url=None,
        )
        session.commit()

        assert material.publisher is None
        assert material.published_at is None
        assert material.source_url is None
        assert material.semester_id is None
        assert material.course_id is None
        assert material.status == MaterialStatus.PENDING


def test_published_at_and_imported_at_are_separate(workspace_id: int) -> None:
    """发布时间与导入时间分开保存（D001 验收）。"""
    published = datetime(2026, 9, 20, 12, 0, tzinfo=TZ)
    session, repo = _make_material_repo()
    with session:
        material = repo.create(
            workspace_id,
            semester_id=None,
            course_id=None,
            class_name=None,
            title="教务通知",
            publisher="教务处",
            published_at=published,
            source_type=MaterialSourceType.PDF,
            source_url=None,
        )
        session.commit()

        assert material.published_at == published
        assert material.imported_at != published  # 导入时间由系统生成
        assert material.imported_at > published


def test_versions_have_stable_association(workspace_id: int) -> None:
    """版本按 (material_id, version_no) 稳定关联且递增（D001 验收）。"""
    session, repo = _make_material_repo()
    with session:
        material = repo.create(
            workspace_id,
            semester_id=None,
            course_id=None,
            class_name=None,
            title="课程大纲",
            publisher=None,
            published_at=None,
            source_type=MaterialSourceType.TEXT,
            source_url=None,
        )
        v1 = repo.create_version(material.id, next_version_no([]), "首次导入")
        existing = repo.list_version_numbers(material.id)
        v2 = repo.create_version(material.id, next_version_no(existing), "教师更新版")
        session.commit()

        versions = repo.list_versions(material.id)
        assert [v.version_no for v in versions] == [1, 2]
        assert all(v.material_id == material.id for v in versions)
        assert versions[0].id == v1.id
        assert versions[1].id == v2.id


def test_next_version_no_rule() -> None:
    assert next_version_no([]) == 1
    assert next_version_no([1]) == 2
    assert next_version_no([1, 2, 5]) == 6
