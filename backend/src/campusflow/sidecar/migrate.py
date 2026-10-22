"""`migrate`：以编程方式执行 Alembic `upgrade head`，并支持只读检查。

迁移脚本位置由 `core.resources.resolve_migrations_dir()` 解析，打包或桌面
运行时都不依赖仓库相对路径；重复执行在已是最新的库上是空操作。
"""

from __future__ import annotations

from alembic import command
from sqlalchemy.exc import SQLAlchemyError

from campusflow.core.config import Settings
from campusflow.infrastructure.db.migrations import build_alembic_config
from campusflow.sidecar.dbtools import (
    connect,
    database_url_environment,
    migration_state,
)
from campusflow.sidecar.errors import SidecarError
from campusflow.sidecar.logs import EventLogger, redact_text


def _upgrade_failure(exc: SQLAlchemyError) -> SidecarError:
    """把迁移失败折算成可操作的中文提示；不改写数据库也不回显连接串。"""
    original = getattr(exc, "orig", exc)
    detail = redact_text(str(original).strip().splitlines()[0])[:200] if str(original) else ""
    # 空库首次迁移要创建 vector 扩展，普通业务角色没有该权限。
    if "vector" in detail and "permission" in detail:
        return SidecarError(
            "迁移失败：当前数据库角色无权创建 vector 扩展，"
            "请先用超级用户在该库执行 CREATE EXTENSION vector 后重试"
        )
    return SidecarError(f"迁移失败：{detail or type(exc).__name__}")


def inspect_migrations(settings: Settings) -> dict[str, object]:
    """只读查询当前迁移状态；数据库不可达时抛原始异常由调用方折算。"""
    engine = connect(settings)
    try:
        return migration_state(settings, engine)
    finally:
        engine.dispose()


def run_migrate(settings: Settings, logger: EventLogger, *, check: bool = False) -> dict:
    """升级到 head 或只检查；返回含 before／after 的迁移状态。

    `check=True` 不写库：只比较 `alembic_version` 与脚本 head，
    落后或空库都算未就绪，由 CLI 折算成非零退出码。
    """
    before = inspect_migrations(settings)
    if check:
        logger.info(
            "migrate_checked",
            current=before["current"],
            head=before["head"],
            at_head=before["at_head"],
        )
        return before

    target = before["head"]
    if not target:
        raise SidecarError("迁移目录中没有可用的 head 版本")
    # env.py 从 Settings 读取连接串，必须在其执行期间覆盖环境变量。
    try:
        with database_url_environment(settings.database_url):
            command.upgrade(build_alembic_config(settings.migrations_dir), "head")
    except SQLAlchemyError as exc:
        logger.error("migrate_failed", error=type(exc).__name__)
        raise _upgrade_failure(exc) from None
    after = inspect_migrations(settings)
    if not after["at_head"]:
        raise SidecarError("迁移执行后仍不是最新版本，请检查 alembic_version 与迁移脚本")
    logger.info(
        "migrate_completed",
        before=before["current"],
        after=after["current"],
        head=after["head"],
    )
    return {"before": before["current"], "after": after["current"], "head": after["head"]}
