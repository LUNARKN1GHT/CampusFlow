"""Alembic 版本检查与升级执行（P011）。

数据库地址显式来自 Settings，不使用 Alembic 命令行默认值；升级在独立进程
环境中执行，结束后释放连接，避免与备份、校验争用同一连接。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from alembic import command
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Engine

from campusflow.application.upgrade import UpgradeError
from campusflow.core.config import Settings
from campusflow.infrastructure.db.engine import create_db_engine
from campusflow.infrastructure.db.migrations import build_alembic_config


def bundled_head(migrations_dir: str | None = None) -> str:
    """返回安装包内置的唯一迁移头；多个头说明打包不完整，直接拒绝升级。"""
    heads = ScriptDirectory.from_config(build_alembic_config(migrations_dir)).get_heads()
    if len(heads) != 1:
        raise UpgradeError(f"安装包内置迁移头应为 1 个，实际为 {len(heads)} 个：{', '.join(heads)}")
    return heads[0]


def bundled_revisions(migrations_dir: str | None = None) -> frozenset[str]:
    """返回内置迁移图里的全部 revision；用于区分“旧库”与“更新版本的库”。"""
    script = ScriptDirectory.from_config(build_alembic_config(migrations_dir))
    return frozenset(revision.revision for revision in script.walk_revisions())


def database_revision(engine: Engine) -> str | None:
    """读取当前数据库的 Alembic revision；没有版本表或没有记录时返回 None。

    多个当前头（未执行合并迁移）按逗号连接，调用方需要按集合判断。
    """
    with engine.connect() as connection:
        heads = MigrationContext.configure(connection).get_current_heads()
    if not heads:
        return None
    return ",".join(sorted(heads))


def settings_database_revision(settings: Settings) -> str | None:
    """按 Settings 连接一次并释放；备份或迁移前后检查都用这个入口。"""
    engine = create_db_engine(settings)
    try:
        return database_revision(engine)
    finally:
        engine.dispose()


@contextmanager
def _database_url_environment(database_url: str) -> Iterator[None]:
    """Alembic env.py 从环境变量读取地址；只在升级期间临时覆盖并恢复。"""
    previous = os.environ.get("CAMPUSFLOW_DATABASE_URL")
    os.environ["CAMPUSFLOW_DATABASE_URL"] = database_url
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("CAMPUSFLOW_DATABASE_URL", None)
        else:
            os.environ["CAMPUSFLOW_DATABASE_URL"] = previous


def run_alembic_upgrade(settings: Settings, target_revision: str) -> None:
    """把数据库升级到指定 revision；失败时异常交由调用方触发备份恢复。"""
    config = build_alembic_config(settings.migrations_dir)
    with _database_url_environment(settings.database_url):
        command.upgrade(config, target_revision)
