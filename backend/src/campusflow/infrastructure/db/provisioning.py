"""首启数据库准备（P007）：建库、启用 pgvector、读取迁移头。

只用 psycopg 连接，不经 shell，口令作为连接参数传入，不出现在命令行或异常文本里。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import psycopg
from psycopg import sql

_ADMIN_DATABASE = "postgres"
_CONNECT_TIMEOUT_SECONDS = 10


class ProvisioningError(RuntimeError):
    """建库、扩展或迁移头读取失败；消息不含口令。"""


@dataclass(frozen=True)
class DatabaseTarget:
    """连接目标；口令不进入 repr，避免随日志或异常泄露。"""

    host: str
    port: int
    database: str
    username: str
    password: str = field(repr=False)

    def with_database(self, database: str) -> DatabaseTarget:
        return DatabaseTarget(
            host=self.host,
            port=self.port,
            database=database,
            username=self.username,
            password=self.password,
        )

    def connect(self, database: str | None = None, *, autocommit: bool = False):
        try:
            return psycopg.connect(
                host=self.host,
                port=self.port,
                dbname=database or self.database,
                user=self.username,
                password=self.password,
                connect_timeout=_CONNECT_TIMEOUT_SECONDS,
                autocommit=autocommit,
            )
        except psycopg.Error as exc:
            raise ProvisioningError(
                f"无法连接 PostgreSQL（{self.host}:{self.port}）：{exc.__class__.__name__}"
            ) from exc


def database_exists(target: DatabaseTarget, database: str) -> bool:
    try:
        with target.connect(_ADMIN_DATABASE, autocommit=True) as connection:
            row = connection.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (database,)
            ).fetchone()
            return row is not None
    except psycopg.Error as exc:
        raise ProvisioningError("查询数据库列表失败") from exc


def create_database(target: DatabaseTarget, database: str, owner: str | None = None) -> bool:
    """建库；已存在返回 False（不覆盖已有数据）。"""
    if database_exists(target, database):
        return False
    try:
        with target.connect(_ADMIN_DATABASE, autocommit=True) as connection:
            statement = sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database))
            if owner is not None:
                statement = statement + sql.SQL(" OWNER {}").format(sql.Identifier(owner))
            connection.execute(statement)
    except psycopg.Error as exc:
        raise ProvisioningError(f"创建数据库 {database} 失败") from exc
    return True


def ensure_vector_extension(target: DatabaseTarget) -> str:
    """启用 pgvector 并返回实际版本；缺少扩展文件时给出可操作的说明。"""
    try:
        with target.connect(autocommit=True) as connection:
            connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
            row = connection.execute(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            ).fetchone()
    except psycopg.errors.UndefinedFile as exc:
        raise ProvisioningError(
            "PostgreSQL 未提供 pgvector 扩展（vector），请使用带 pgvector 的发行包"
        ) from exc
    except psycopg.Error as exc:
        raise ProvisioningError("启用 pgvector 扩展失败") from exc
    if row is None:
        raise ProvisioningError("启用 pgvector 后仍找不到扩展记录")
    return str(row[0])


def ping(target: DatabaseTarget) -> None:
    """用一次简单查询确认连接与口令可用。"""
    try:
        with target.connect() as connection:
            connection.execute("SELECT 1").fetchone()
    except psycopg.Error as exc:
        raise ProvisioningError("数据库连接检查失败") from exc


def read_schema_head(target: DatabaseTarget) -> str | None:
    """读取 alembic_version 的迁移头；未迁移时返回 None。"""
    try:
        with target.connect() as connection:
            row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    except psycopg.errors.UndefinedTable:
        return None
    except psycopg.Error as exc:
        raise ProvisioningError("读取数据库迁移头失败") from exc
    return str(row[0]) if row else None
