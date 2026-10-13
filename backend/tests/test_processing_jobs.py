"""J001 集成测试：处理作业状态与尝试记录模型。

验收：完成与部分成功可区分；失败信息不包含密钥或原始隐私全文。
"""

import pytest
from conftest import TEST_DATABASE_URL
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from campusflow.domain.jobs import sanitize_error_reason
from campusflow.domain.states import JobStatus
from campusflow.infrastructure.repositories import (
    SqlAlchemyJobRepository,
    SqlAlchemyMaterialRepository,
)


@pytest.fixture
def ids(client: TestClient) -> dict:
    workspace_id = client.get("/api/v1/settings").json()["workspace_id"]
    response = client.post(
        "/api/v1/materials/text", json={"title": "作业测试", "content": "内容一\n\n内容二"}
    )
    assert response.status_code == 201
    material = response.json()["material"]
    engine = create_engine(TEST_DATABASE_URL)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        version_id = SqlAlchemyMaterialRepository(session).list_versions(material["id"])[0].id
    return {"workspace_id": workspace_id, "material_id": material["id"], "version_id": version_id}


def test_done_and_partial_are_distinct(ids: dict) -> None:
    """完成与部分成功是不同状态（J001 验收）。"""
    engine = create_engine(TEST_DATABASE_URL)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        repo = SqlAlchemyJobRepository(session)
        job = repo.create(ids["workspace_id"], ids["material_id"], ids["version_id"], "parse")
        assert job.status == "pending"
        assert job.attempts == 0

        repo.record_attempt(job.id, status="running", scope=None, error_reason=None)
        done = repo.record_attempt(
            job.id, status="done", scope="第 1-3 页全部完成", error_reason=None
        )
        assert done.status == JobStatus.DONE
        assert done.attempts == 2

        partial_job = repo.create(
            ids["workspace_id"], ids["material_id"], ids["version_id"], "parse"
        )
        partial = repo.record_attempt(
            partial_job.id,
            status="partial",
            scope="第 1、2 页完成，第 3 页失败",
            error_reason="第 3 页为扫描页",
        )
        assert partial.status == JobStatus.PARTIAL
        assert partial.status != JobStatus.DONE
        assert partial.scope and "第 3 页失败" in partial.scope
        session.commit()


def test_failure_reason_sanitized(ids: dict) -> None:
    """失败信息中的密钥与隐私内容被隐藏（J001 验收）。"""
    engine = create_engine(TEST_DATABASE_URL)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        repo = SqlAlchemyJobRepository(session)
        job = repo.create(ids["workspace_id"], ids["material_id"], ids["version_id"], "extract")
        failed = repo.record_attempt(
            job.id,
            status="failed",
            scope=None,
            error_reason="调用失败：Authorization Bearer sk-abc123def456 无效，请联系 13812345678",
        )
        assert failed.status == JobStatus.FAILED
        assert "sk-abc123def456" not in (failed.error_reason or "")
        assert "13812345678" not in (failed.error_reason or "")
        assert "Bearer" not in (failed.error_reason or "")
        assert "[已隐藏]" in failed.error_reason
        session.commit()


def test_sanitize_error_reason_rules() -> None:
    assert sanitize_error_reason(None) is None
    assert "token=xyz" not in (sanitize_error_reason("配置 token=xyz123456") or "")
    long_text = "错" * 500
    assert len(sanitize_error_reason(long_text)) <= 301
    assert sanitize_error_reason("普通错误") == "普通错误"
