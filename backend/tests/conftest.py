"""集成测试配置：使用真实 PostgreSQL 测试库（campusflow_test）。

测试库由 infra/docker-compose.yml 首次初始化时创建；表结构通过 Alembic 迁移建立。
每个测试前清空所有表，保证用例之间互不影响。
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from campusflow.core.config import Settings
from campusflow.main import create_app

BACKEND_DIR = Path(__file__).resolve().parent.parent

TEST_DATABASE_URL = os.environ.get(
    "CAMPUSFLOW_TEST_DATABASE_URL",
    "postgresql+psycopg://campusflow:campusflow@127.0.0.1:5432/campusflow_test",
)

TABLES = "workspaces, semesters, courses, tasks, fixed_events, availability_slots"


def _check_database_reachable() -> None:
    engine = create_engine(TEST_DATABASE_URL, connect_args={"connect_timeout": 3})
    try:
        with engine.connect():
            pass
    except Exception as exc:
        raise RuntimeError(
            f"无法连接测试数据库 {TEST_DATABASE_URL}。请先在 infra/ 目录执行 docker compose up -d。"
        ) from exc


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    _check_database_reachable()
    # Alembic 的 env.py 从环境变量读取地址，指向测试库后执行迁移
    os.environ["CAMPUSFLOW_DATABASE_URL"] = TEST_DATABASE_URL
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(config, "head")

    settings = Settings(_env_file=None, database_url=TEST_DATABASE_URL)
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _clean_tables(client: TestClient) -> None:
    engine = create_engine(TEST_DATABASE_URL)
    with engine.connect() as connection:
        connection.execute(text(f"TRUNCATE TABLE {TABLES} RESTART IDENTITY CASCADE"))
        connection.commit()
    yield
