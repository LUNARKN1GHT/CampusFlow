"""sidecar 的数据库小型只读辅助：迁移头、空库判断与 libpq 连接串。

这里的函数只读或只做连接层转换，不承载业务规则；写入仍由 Alembic 迁移、
pg_dump／pg_restore 完成。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Engine, make_url

from campusflow.core.config import Settings
from campusflow.infrastructure.db.engine import create_db_engine
from campusflow.infrastructure.db.migrations import build_alembic_config


def script_heads(settings: Settings) -> list[str]:
    """代码期望的迁移头；读不到迁移目录时抛 FileNotFoundError。"""
    config = build_alembic_config(settings.migrations_dir)
    return sorted(ScriptDirectory.from_config(config).get_heads())


def database_heads(engine: Engine) -> list[str]:
    """数据库已应用的迁移头；空库或未迁移时返回空列表。"""
    with engine.connect() as connection:
        return sorted(MigrationContext.configure(connection).get_current_heads())


def migration_state(settings: Settings, engine: Engine) -> dict[str, object]:
    """合并数据库与代码两侧的迁移头，供 migrate／doctor 共用。"""
    current = database_heads(engine)
    head = script_heads(settings)
    return {"current": current, "head": head, "at_head": current == head}


def database_name(database_url: str) -> str:
    return make_url(database_url).database or ""


def libpq_url(database_url: str) -> str:
    """转成 libpq 可用的 `postgresql://` 地址；SQLAlchemy 的驱动后缀要摘掉。"""
    return make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)


def redacted_database_url(database_url: str) -> str:
    """写进 manifest／诊断输出的地址：保留用户名与库名，口令一律 `***`。"""
    return make_url(database_url).render_as_string(hide_password=True)


def public_table_count(engine: Engine) -> int:
    """`public` 下的表数量；用于判断目标库是否已经有结构或数据。"""
    with engine.connect() as connection:
        return int(
            connection.execute(
                text("SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname = 'public'")
            ).scalar_one()
        )


def database_has_content(engine: Engine) -> bool:
    """存在任何 `public` 表即视为非空：迁移过的库一定至少有 alembic_version。"""
    return public_table_count(engine) > 0


@contextmanager
def database_url_environment(database_url: str) -> Iterator[None]:
    """临时把连接串写入环境变量，供 Alembic 的 env.py 读取。

    env.py 从 Settings 取值而不是 ini，因此必须在调用期间覆盖环境变量；
    结束后恢复原值（包括原本不存在的情况）。
    """
    variable = "CAMPUSFLOW_DATABASE_URL"
    previous = os.environ.get(variable)
    os.environ[variable] = database_url
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(variable, None)
        else:
            os.environ[variable] = previous


def connect(settings: Settings) -> Engine:
    """按应用配置建引擎；诊断与备份只在需要时短暂持有。"""
    return create_db_engine(settings)
