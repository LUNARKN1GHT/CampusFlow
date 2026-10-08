"""W005 集成测试：默认空间通过应用用例解析，并发初始化安全。"""

from concurrent.futures import ThreadPoolExecutor

from conftest import TEST_DATABASE_URL
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from campusflow.infrastructure.db.models import Workspace
from campusflow.infrastructure.repositories import SqlAlchemyWorkspaceRepository


def _count_default_workspaces() -> int:
    engine = create_engine(TEST_DATABASE_URL)
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT count(*) FROM workspaces WHERE is_default")
        ).scalar_one()


def test_repeated_requests_create_single_default_workspace(client: TestClient) -> None:
    for _ in range(3):
        response = client.get("/api/v1/settings")
        assert response.status_code == 200
    assert _count_default_workspaces() == 1


def test_api_layer_does_not_query_workspace_model() -> None:
    """API 依赖不得直接查询 Workspace ORM 模型（W005 验收）。"""
    from pathlib import Path

    deps_source = (
        Path(__file__).resolve().parent.parent / "src" / "campusflow" / "api" / "deps.py"
    ).read_text(encoding="utf-8")
    assert "infrastructure.db.models" not in deps_source
    assert "select(" not in deps_source


def test_concurrent_default_workspace_creation_is_safe() -> None:
    """多个线程同时初始化默认空间，最终最多创建一行（唯一约束兜底）。"""
    engine = create_engine(TEST_DATABASE_URL)
    factory = sessionmaker(bind=engine)

    def init_default() -> int:
        with factory() as session:
            repo = SqlAlchemyWorkspaceRepository(session)
            workspace = repo.get_or_create_default("默认工作空间", "Asia/Shanghai")
            session.commit()
            return workspace.id

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: init_default(), range(16)))

    assert len(set(ids)) == 1
    assert _count_default_workspaces() == 1


def test_default_workspace_row_matches_model(client: TestClient) -> None:
    """默认空间行结构与 ORM 模型一致（含 is_default 列）。"""
    client.get("/api/v1/settings")
    engine = create_engine(TEST_DATABASE_URL)
    with engine.connect() as connection:
        row = connection.execute(select(Workspace.id, Workspace.name, Workspace.is_default)).one()
    assert row.is_default is True
    assert row.name == "默认工作空间"
