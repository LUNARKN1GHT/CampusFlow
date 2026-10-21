"""P010：Alembic 版本查询与前向迁移实现。

`migrations/env.py` 从 `Settings()` 读取地址，因此迁移到临时库时必须临时
覆盖 `CAMPUSFLOW_DATABASE_URL`；与测试夹具使用同一机制，退出时立即恢复。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from campusflow.application.transfer.errors import DatabaseToolError
from campusflow.infrastructure.db.migrations import build_alembic_config

_ENV_KEY = "CAMPUSFLOW_DATABASE_URL"


class AlembicMigrator:
    """`SchemaMigrator` 端口实现；只前向迁移，不提供降级。"""

    def __init__(self, migrations_dir: str | None = None) -> None:
        self._migrations_dir = migrations_dir

    def _config(self) -> Config:
        return build_alembic_config(self._migrations_dir)

    def _scripts(self) -> ScriptDirectory:
        return ScriptDirectory.from_config(self._config())

    def head_revision(self) -> str:
        head = self._scripts().get_current_head()
        if head is None:
            raise DatabaseToolError("迁移目录没有可用的 head 版本，无法判断归档兼容性。")
        return head

    def known_revisions(self) -> frozenset[str]:
        scripts = self._scripts()
        return frozenset(script.revision for script in scripts.walk_revisions())

    def upgrade_to_head(self, database_url: str) -> None:
        try:
            with _temporary_database_url(database_url):
                command.upgrade(self._config(), "head")
        except DatabaseToolError:
            raise
        except Exception as error:
            detail = str(error).strip().splitlines()
            raise DatabaseToolError(
                "恢复后的数据库前向迁移失败（已放弃切换，本机原有数据未改变）："
                f"{detail[-1] if detail else error}"
            ) from error


@contextmanager
def _temporary_database_url(url: str) -> Iterator[None]:
    previous = os.environ.get(_ENV_KEY)
    os.environ[_ENV_KEY] = url
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(_ENV_KEY, None)
        else:
            os.environ[_ENV_KEY] = previous


__all__ = ["AlembicMigrator"]
