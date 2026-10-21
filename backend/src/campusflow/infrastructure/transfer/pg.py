"""P010：PostgreSQL 客户端工具与建库/切换实现。

`pg_dump`／`pg_restore` 通过子进程调用，口令只走 `PGPASSWORD` 环境变量，
不写进命令行参数（避免出现在进程列表里）；子进程输出强制英文，便于识别
唯一允许忽略的告警（临时库预建扩展后的 `must be owner of extension`）。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, OperationalError, SQLAlchemyError
from sqlalchemy.pool import NullPool

from campusflow.application.transfer.errors import (
    DatabaseToolError,
    DatabaseToolMissingError,
)
from campusflow.core.config import Settings
from campusflow.core.resources import pg_binary_dir

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# 临时库预先创建扩展后，pg_restore 执行 COMMENT ON EXTENSION 会因所有者不同报错；
# 数据完整性由恢复后的条目数校验兜底，因此只有这一条允许忽略。
_ALLOWED_RESTORE_ERRORS = ("must be owner of extension",)
_CONNECT_TIMEOUT_SECONDS = 10
_MAX_DETAIL_LINES = 8


def quote_identifier(name: str) -> str:
    """数据库/角色名来自连接地址，仍按标识符规则校验后加引号。"""
    if not _IDENTIFIER.match(name):
        raise DatabaseToolError(f"数据库标识符不合法：{name!r}")
    return f'"{name}"'


class PgToolchain:
    """`DatabaseTools` 端口实现：pg_dump/pg_restore 与数据库管理语句。"""

    def __init__(self, settings: Settings, *, admin_url: str) -> None:
        self._bin_dir = pg_binary_dir(settings.pg_bin)
        self._admin_url = admin_url
        self._app_url = settings.database_url

    # ------------------------------------------------------------------ 连接

    @staticmethod
    def _driver_url(url: str) -> str:
        return make_url(url).set(drivername="postgresql+psycopg").render_as_string(
            hide_password=False
        )

    def connection_url(self, database: str) -> str:
        return (
            make_url(self._app_url)
            .set(database=database)
            .render_as_string(hide_password=False)
        )

    def _engine(self, url: str):
        return create_engine(
            self._driver_url(url),
            poolclass=NullPool,
            connect_args={"connect_timeout": _CONNECT_TIMEOUT_SECONDS},
        )

    def _admin_engine(self):
        return create_engine(
            self._driver_url(self._admin_url),
            poolclass=NullPool,
            isolation_level="AUTOCOMMIT",
            connect_args={"connect_timeout": _CONNECT_TIMEOUT_SECONDS},
        )

    # ------------------------------------------------------------------ 查询

    def table_names(self, url: str, names: Sequence[str]) -> frozenset[str]:
        engine = self._engine(url)
        try:
            with engine.connect() as connection:
                return frozenset(
                    name
                    for name in names
                    if connection.execute(
                        text("SELECT to_regclass(:name)"), {"name": f"public.{name}"}
                    ).scalar()
                )
        except SQLAlchemyError as error:
            raise DatabaseToolError(f"读取数据库表清单失败：{_detail(error)}") from error
        finally:
            engine.dispose()

    def count_rows(self, url: str, tables: Mapping[str, str]) -> dict[str, int]:
        engine = self._engine(url)
        counts: dict[str, int] = {}
        try:
            with engine.connect() as connection:
                for key, table in tables.items():
                    exists = connection.execute(
                        text("SELECT to_regclass(:name)"), {"name": f"public.{table}"}
                    ).scalar()
                    if not exists:
                        counts[key] = 0
                        continue
                    counts[key] = int(
                        connection.execute(text(f"SELECT count(*) FROM {quote_identifier(table)}"))
                        .scalar_one()
                    )
        except SQLAlchemyError as error:
            raise DatabaseToolError(f"读取条目数失败：{_detail(error)}") from error
        finally:
            engine.dispose()
        return counts

    def stored_material_keys(self, url: str) -> list[str]:
        engine = self._engine(url)
        try:
            with engine.connect() as connection:
                exists = connection.execute(
                    text("SELECT to_regclass('public.material_versions')")
                ).scalar()
                if not exists:
                    return []
                rows = connection.execute(
                    text("SELECT storage_key FROM material_versions WHERE storage_key IS NOT NULL")
                )
                return sorted(row[0] for row in rows)
        except SQLAlchemyError as error:
            raise DatabaseToolError(f"读取资料存储键失败：{_detail(error)}") from error
        finally:
            engine.dispose()

    def current_revision(self, url: str) -> str | None:
        engine = self._engine(url)
        try:
            with engine.connect() as connection:
                exists = connection.execute(
                    text("SELECT to_regclass('public.alembic_version')")
                ).scalar()
                if not exists:
                    return None
                return connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one_or_none()
        except SQLAlchemyError as error:
            raise DatabaseToolError(f"读取数据库版本失败：{_detail(error)}") from error
        finally:
            engine.dispose()

    def installed_extensions(self, url: str) -> list[str]:
        engine = self._engine(url)
        try:
            with engine.connect() as connection:
                rows = connection.execute(
                    text(
                        "SELECT extname FROM pg_extension "
                        "WHERE extname <> 'plpgsql' ORDER BY extname"
                    )
                )
                return [row[0] for row in rows]
        except SQLAlchemyError as error:
            raise DatabaseToolError(f"读取扩展清单失败：{_detail(error)}") from error
        finally:
            engine.dispose()

    # ------------------------------------------------------------------ 备份

    def dump_database(
        self, url: str, destination: Path, *, exclude_table_data: Sequence[str]
    ) -> None:
        parsed = make_url(url)
        command = [
            self._binary("pg_dump"),
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            *_connect_flags(parsed),
            "--file",
            str(destination),
        ]
        for table in exclude_table_data:
            command.append(f"--exclude-table-data=public.{table}")
        command.append(parsed.database or "campusflow")
        self._run(command, url, "pg_dump 导出数据库失败")

    def restore_database(self, url: str, dump: Path) -> None:
        parsed = make_url(url)
        command = [
            self._binary("pg_restore"),
            "--no-owner",
            "--no-privileges",
            *_connect_flags(parsed),
            "--dbname",
            parsed.database or "campusflow",
            str(dump),
        ]
        completed = self._run(command, url, "pg_restore 恢复数据库失败", check=False)
        errors = [
            line
            for line in completed.stderr.splitlines()
            if line.startswith("pg_restore: error:")
        ]
        unexpected = [
            line
            for line in errors
            if not any(allowed in line for allowed in _ALLOWED_RESTORE_ERRORS)
        ]
        if unexpected:
            detail = "\n".join(unexpected[:_MAX_DETAIL_LINES])
            raise DatabaseToolError(
                "pg_restore 恢复数据库失败（已放弃切换，本机数据未改变）：\n"
                f"{detail or _tail(completed.stderr)}"
            )
        if completed.returncode != 0 and not errors:
            raise DatabaseToolError(
                f"pg_restore 恢复数据库失败（返回码 {completed.returncode}）："
                f"{_tail(completed.stderr)}"
            )

    # ------------------------------------------------------------------ 建库/切换

    def database_exists(self, name: str) -> bool:
        with self._admin_engine().connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
            ).scalar()
        return bool(exists)

    def create_database(self, name: str, owner: str) -> None:
        statement = (
            f"CREATE DATABASE {quote_identifier(name)} "
            f"OWNER {quote_identifier(owner)} TEMPLATE template0 ENCODING 'UTF8'"
        )
        self._execute_admin(
            statement,
            f"创建数据库 {name} 失败（导入需要 CREATEDB 权限；"
            "桌面模式默认使用本机超级用户连接，服务器部署请配置 CAMPUSFLOW_PG_ADMIN_URL）",
        )

    def drop_database(self, name: str) -> None:
        self._terminate(name)
        self._execute_admin(
            f"DROP DATABASE IF EXISTS {quote_identifier(name)}", f"删除数据库 {name} 失败"
        )

    def provision_extensions(self, database: str, extensions: Sequence[str]) -> None:
        if not extensions:
            return
        url = (
            make_url(self._admin_url)
            .set(database=database)
            .render_as_string(hide_password=False)
        )
        engine = create_engine(
            self._driver_url(url),
            poolclass=NullPool,
            isolation_level="AUTOCOMMIT",
            connect_args={"connect_timeout": _CONNECT_TIMEOUT_SECONDS},
        )
        try:
            with engine.connect() as connection:
                for extension in extensions:
                    try:
                        connection.execute(
                            text(f"CREATE EXTENSION IF NOT EXISTS {quote_identifier(extension)}")
                        )
                    except SQLAlchemyError as error:
                        raise DatabaseToolError(
                            f"临时库创建扩展 {extension} 失败：{_detail(error)}。"
                            "请确认本机 PostgreSQL 已安装该扩展（桌面包应自带 pgvector）。"
                        ) from error
        finally:
            engine.dispose()

    def switch_database(self, *, target: str, replacement: str, previous: str) -> None:
        engine = self._admin_engine()
        try:
            with engine.connect() as connection:
                if connection.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": previous}
                ).scalar():
                    raise DatabaseToolError(f"备份数据库名 {previous} 已存在，请稍后重试。")
                self._terminate(target)
                connection.execute(
                    text(
                        f"ALTER DATABASE {quote_identifier(target)} "
                        f"RENAME TO {quote_identifier(previous)}"
                    )
                )
                try:
                    connection.execute(
                        text(
                            f"ALTER DATABASE {quote_identifier(replacement)} "
                            f"RENAME TO {quote_identifier(target)}"
                        )
                    )
                except SQLAlchemyError as error:
                    connection.execute(
                        text(
                            f"ALTER DATABASE {quote_identifier(previous)} "
                            f"RENAME TO {quote_identifier(target)}"
                        )
                    )
                    raise DatabaseToolError(
                        f"切换数据库失败，已还原原数据库：{_detail(error)}"
                    ) from error
        finally:
            engine.dispose()

    # ------------------------------------------------------------------ 内部

    def _terminate(self, name: str) -> None:
        engine = self._admin_engine()
        try:
            with engine.connect() as connection:
                connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :name AND pid <> pg_backend_pid()"
                    ),
                    {"name": name},
                )
        except SQLAlchemyError as error:
            raise DatabaseToolError(f"断开 {name} 的连接失败：{_detail(error)}") from error
        finally:
            engine.dispose()

    def _execute_admin(self, statement: str, message: str) -> None:
        engine = self._admin_engine()
        try:
            with engine.connect() as connection:
                connection.execute(text(statement))
        except SQLAlchemyError as error:
            raise DatabaseToolError(f"{message}：{_detail(error)}") from error
        finally:
            engine.dispose()

    def _binary(self, name: str) -> str:
        executable = name + (".exe" if os.name == "nt" else "")
        if self._bin_dir is not None:
            candidate = self._bin_dir / executable
            if candidate.is_file():
                return str(candidate)
            raise DatabaseToolMissingError(
                f"CAMPUSFLOW_PG_BIN 指向 {self._bin_dir}，但其中没有 {executable}。"
                "请指向包含 pg_dump、pg_restore 的 PostgreSQL 客户端目录。"
            )
        found = shutil.which(executable)
        if found is None:
            raise DatabaseToolMissingError(
                f"找不到 {executable}。请安装 PostgreSQL 客户端，"
                "或通过 CAMPUSFLOW_PG_BIN 指向包含 pg_dump、pg_restore 的目录。"
            )
        return found

    def _run(
        self,
        command: list[str],
        url: str,
        message: str,
        *,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        password = make_url(url).password
        if password:
            env["PGPASSWORD"] = password
        # 强制英文输出：用于识别唯一允许忽略的 pg_restore 告警。
        env["LC_ALL"] = "C"
        env["LANG"] = "C"
        env["PGCONNECT_TIMEOUT"] = str(_CONNECT_TIMEOUT_SECONDS)
        completed = subprocess.run(  # noqa: S603 - 参数列表固定，不经过 shell
            command,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        if check and completed.returncode != 0:
            raise DatabaseToolError(f"{message}：{_tail(completed.stderr)}")
        return completed


def _connect_flags(parsed) -> list[str]:
    flags = ["--host", parsed.host or "127.0.0.1", "--port", str(parsed.port or 5432)]
    if parsed.username:
        flags.extend(["--username", parsed.username])
    return flags


def _tail(output: str) -> str:
    lines = [line for line in output.strip().splitlines() if line.strip()]
    return "\n".join(lines[-_MAX_DETAIL_LINES:]) or "（无输出）"


def _detail(error: Exception) -> str:
    if isinstance(error, (DBAPIError, OperationalError)) and getattr(error, "orig", None):
        return str(error.orig).strip().splitlines()[0]
    return str(error).strip().splitlines()[0]


__all__ = ["PgToolchain", "quote_identifier"]
