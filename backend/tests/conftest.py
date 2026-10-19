"""集成测试配置：使用真实 PostgreSQL 测试库（campusflow_test）。

测试库由 infra/docker-compose.yml 首次初始化时创建；表结构通过 Alembic 迁移建立。
每个测试前清空所有表，保证用例之间互不影响。
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from campusflow.api import qa as qa_api
from campusflow.application import planning
from campusflow.core.config import Settings
from campusflow.main import create_app

# 排期用例以 2026-10-19 为基准日期，并假设当天仍可安排晚间时段。
# 这些用例显式使用固定时钟，避免在当天晚些时候或之后运行时因容量耗尽而失败。
FIXED_PLANNING_NOW = datetime(2026, 10, 19, 0, 30, tzinfo=UTC)


class _FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return FIXED_PLANNING_NOW.astimezone(tz) if tz else FIXED_PLANNING_NOW.replace(tzinfo=None)

    @classmethod
    def today(cls):
        return cls.now()

    @classmethod
    def utcnow(cls):
        return FIXED_PLANNING_NOW.replace(tzinfo=None)


@pytest.fixture
def fixed_planning_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(planning, "datetime", _FixedDateTime)
    monkeypatch.setattr(qa_api, "datetime", _FixedDateTime)


BACKEND_DIR = Path(__file__).resolve().parent.parent

TEST_DATABASE_URL = os.environ.get(
    "CAMPUSFLOW_TEST_DATABASE_URL",
    "postgresql+psycopg://campusflow:campusflow@127.0.0.1:5432/campusflow_test",
)

TABLES = ", ".join(
    (
        "workspaces",
        "semesters",
        "courses",
        "learner_profiles",
        "course_study_records",
        "course_aliases",
        "course_relations",
        "curriculum_courses",
        "curriculum_plans",
        "tasks",
        "task_dependencies",
        "reminder_triggers",
        "reminder_rules",
        "task_progress_changes",
        "fixed_events",
        "availability_slots",
        "materials",
        "material_versions",
        "processing_jobs",
        "processing_job_attempts",
        "source_chunks",
        "material_version_relations",
        "embedding_profiles",
        "indexed_blocks",
        "index_manifests",
        "qa_turns",
        "qa_conversations",
        "plan_versions",
        "plan_blocks",
        "plan_drafts",
    )
)


def _check_database_reachable() -> None:
    engine = create_engine(TEST_DATABASE_URL, connect_args={"connect_timeout": 3})
    try:
        with engine.connect():
            pass
    except Exception as exc:
        raise RuntimeError(
            f"无法连接测试数据库 {TEST_DATABASE_URL}。请先在 infra/ 目录执行 docker compose up -d。"
        ) from exc


def _validate_test_database_url(url: str) -> None:
    database = make_url(url).database or ""
    if not database.endswith("_test"):
        raise RuntimeError(
            f"测试数据库名必须以 _test 结尾，当前为 {database!r}。"
            "拒绝在可能是开发库或业务库的地址上执行迁移与清表。"
        )


@contextmanager
def _temporary_database_url(url: str) -> Iterator[None]:
    previous = os.environ.get("CAMPUSFLOW_DATABASE_URL")
    os.environ["CAMPUSFLOW_DATABASE_URL"] = url
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("CAMPUSFLOW_DATABASE_URL", None)
        else:
            os.environ["CAMPUSFLOW_DATABASE_URL"] = previous


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    _validate_test_database_url(TEST_DATABASE_URL)
    _check_database_reachable()
    # Alembic 的 env.py 从环境变量读取地址，指向测试库后执行迁移；会话结束恢复原值
    with _temporary_database_url(TEST_DATABASE_URL):
        config = Config(str(BACKEND_DIR / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
        command.upgrade(config, "head")

        # 测试里关闭周期恢复扫描，避免后台任务干扰用例；启动扫描仍保留
        settings = Settings(
            _env_file=None, database_url=TEST_DATABASE_URL, recovery_interval_seconds=0
        )
        with TestClient(create_app(settings)) as test_client:
            response = test_client.post(
                "/api/v1/auth/login",
                json={"username": settings.local_username, "password": settings.local_password},
            )
            assert response.status_code == 200
            yield test_client


@pytest.fixture(autouse=True)
def _clean_tables(request: pytest.FixtureRequest) -> Iterator[None]:
    # 仅对声明 client 的集成测试清库；纯领域测试不触发数据库连接
    if "client" not in request.fixturenames:
        yield
        return
    request.getfixturevalue("client")
    engine = create_engine(TEST_DATABASE_URL)
    with engine.connect() as connection:
        connection.execute(text(f"TRUNCATE TABLE {TABLES} RESTART IDENTITY CASCADE"))
        connection.commit()
    yield
