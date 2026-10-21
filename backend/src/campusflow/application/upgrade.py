"""桌面应用升级编排（P011）：版本门禁、升级前备份、失败恢复与保留策略。

公开 API（sidecar CLI 直接调用，不要复制这里的步骤）：

- ``bundled_head(migrations_dir: str | None = None) -> str``
  由 ``infrastructure.db.schema`` 提供：返回安装包内置 Alembic 头。
- ``database_revision(engine) -> str | None``
  由 ``infrastructure.db.schema`` 提供：读取数据库当前 revision。
- ``evaluate_upgrade(db_revision, bundled_head, *, known_revisions=None) -> UpgradeState``
  纯函数；比较数据库与内置版本。``known_revisions`` 为内置迁移图中的全部
  revision，缺省时无法证明数据库版本是内置版本的祖先，按“更新”拒绝。
- ``inspect_schema(settings, *, head=None, revision=None, known_revisions=None) -> SchemaSnapshot``
  读取一次数据库状态，便于 CLI 展示。
- ``check_schema_or_raise(settings, ...) -> UpgradeState``
  启动门禁：``database_ahead`` 抛 ``DatabaseAheadError``；未初始化或需要升级
  抛 ``UpgradeRequiredError``；只有 ``up_to_date`` 才返回。
- ``run_upgrade(settings, *, backup=None, restore=None, migrate=None, head=None,
  revision=None, known_revisions=None, retention_count=None) -> UpgradeReport``
  升级编排；五个可注入端口便于故障演练。默认实现真实调用 Alembic 与
  pg_dump／pg_restore。
- ``upgrade_if_needed(settings, **ports) -> UpgradeReport``
  默认端口的 ``run_upgrade``，sidecar 启动流程调用它再调用门禁。

约定：``database_ahead`` 不降级数据库，直接拒绝启动；任何迁移失败都不得
保留半升级状态，必须先恢复升级前备份再抛中文错误。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, quote_plus, urlsplit, urlunsplit

from campusflow.core.config import Settings

logger = logging.getLogger(__name__)


class UpgradeError(RuntimeError):
    """升级流程错误；消息面向用户，不得包含数据库口令。"""


class DatabaseAheadError(UpgradeError):
    """数据库结构新于当前应用：不支持旧应用打开新库，也不降级。"""


class UpgradeRequiredError(UpgradeError):
    """数据库尚未初始化或落后于内置版本，应用不能直接启动。"""


class UpgradeFailedError(UpgradeError):
    """迁移失败；消息说明备份是否已恢复以及备份位置。"""


class BackupError(UpgradeError):
    """升级前备份或恢复失败；此时不得继续迁移。"""


class UpgradeState(StrEnum):
    """数据库与内置迁移头的相对状态。"""

    UNINITIALIZED = "uninitialized"
    UP_TO_DATE = "up_to_date"
    UPGRADE_REQUIRED = "upgrade_required"
    DATABASE_AHEAD = "database_ahead"


@dataclass(frozen=True)
class BackupInfo:
    """一次升级前备份；manifest 内容同时写入 backup 目录下的 manifest.json。"""

    path: Path
    manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def database_revision(self) -> str | None:
        value = self.manifest.get("database_revision")
        return str(value) if value is not None else None

    @property
    def app_schema_head(self) -> str | None:
        value = self.manifest.get("app_schema_head")
        return str(value) if value is not None else None


@dataclass(frozen=True)
class SchemaSnapshot:
    """一次只读检查的结果：版本、内置头和判定状态。"""

    database_revision: str | None
    bundled_head: str
    state: UpgradeState


@dataclass(frozen=True)
class UpgradeReport:
    """升级结果；action=none 表示无需迁移。"""

    action: Literal["none", "created_schema", "upgraded"]
    state: UpgradeState
    previous_revision: str | None
    bundled_head: str
    backup: BackupInfo | None = None
    pruned_backups: tuple[str, ...] = ()

    @property
    def upgraded(self) -> bool:
        return self.action in ("created_schema", "upgraded")


BackupFn = Callable[..., BackupInfo]
RestoreFn = Callable[[Settings, BackupInfo], None]
MigrateFn = Callable[[Settings, str], None]
HeadFn = Callable[[str | None], str]
RevisionFn = Callable[[Settings], str | None]
PruneFn = Callable[..., tuple[str, ...]]


def redact_database_url(database_url: str) -> str:
    """把连接串中的口令替换为 ***；无法解析时也不回显原文。"""
    try:
        parts = urlsplit(database_url)
        password = parts.password
    except ValueError:
        return "<无法解析的数据库地址>"
    if not password:
        return database_url
    host = parts.hostname or ""
    netloc = f"{quote(parts.username or '')}:***@{host}"
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def redact_text(text: str, database_url: str) -> str:
    """从日志与异常文本中去掉连接口令，保留其余诊断信息。"""
    try:
        password = urlsplit(database_url).password
    except ValueError:
        password = None
    if password:
        for candidate in {password, quote(password), quote_plus(password)}:
            if candidate:
                text = text.replace(candidate, "***")
    return text


def _revision_parts(value: str) -> set[str]:
    return {part for part in value.replace(" ", "").split(",") if part}


def evaluate_upgrade(
    db_revision: str | None,
    bundled_head: str,
    *,
    known_revisions: Collection[str] | None = None,
) -> UpgradeState:
    """比较数据库与内置迁移头。

    只有能证明数据库 revision 属于内置迁移图时才允许升级；未知 revision
    视为“数据库更新”，避免旧应用误改新库。``known_revisions`` 缺省时无法
    证明祖先关系，除相等外一律按 ``database_ahead`` 处理。
    """
    if not db_revision:
        return UpgradeState.UNINITIALIZED
    heads = _revision_parts(db_revision)
    if not heads:
        return UpgradeState.UNINITIALIZED
    if heads == {bundled_head}:
        return UpgradeState.UP_TO_DATE
    if known_revisions is None:
        return UpgradeState.DATABASE_AHEAD
    if heads <= set(known_revisions):
        return UpgradeState.UPGRADE_REQUIRED
    return UpgradeState.DATABASE_AHEAD


def _default_head(migrations_dir: str | None) -> str:
    from campusflow.infrastructure.db.schema import bundled_head

    return bundled_head(migrations_dir)


def _default_known(migrations_dir: str | None) -> frozenset[str]:
    from campusflow.infrastructure.db.schema import bundled_revisions

    return bundled_revisions(migrations_dir)


def _default_revision(settings: Settings) -> str | None:
    from campusflow.infrastructure.db.schema import settings_database_revision

    return settings_database_revision(settings)


def _default_migrate(settings: Settings, target_revision: str) -> None:
    from campusflow.infrastructure.db.schema import run_alembic_upgrade

    run_alembic_upgrade(settings, target_revision)


def _default_backup(settings: Settings, *, db_revision: str | None, app_head: str) -> BackupInfo:
    from campusflow.infrastructure.backup.postgres import create_pre_upgrade_backup

    return create_pre_upgrade_backup(settings, db_revision=db_revision, app_head=app_head)


def _default_restore(settings: Settings, backup: BackupInfo) -> None:
    from campusflow.infrastructure.backup.postgres import restore_pre_upgrade_backup

    restore_pre_upgrade_backup(settings, backup)


def _default_prune(settings: Settings, *, keep: int, current: Path | None) -> tuple[str, ...]:
    from campusflow.infrastructure.backup.postgres import prune_pre_upgrade_backups

    return prune_pre_upgrade_backups(settings, keep=keep, current=current)


def inspect_schema(
    settings: Settings,
    *,
    head: HeadFn | None = None,
    revision: RevisionFn | None = None,
    known_revisions: Collection[str] | None = None,
) -> SchemaSnapshot:
    """只读检查；``known_revisions`` 默认从安装包迁移图读取。"""
    head_fn = head or _default_head
    revision_fn = revision or _default_revision
    target = head_fn(settings.migrations_dir)
    known = (
        set(known_revisions)
        if known_revisions is not None
        else set(_default_known(settings.migrations_dir))
    )
    known.add(target)
    current = revision_fn(settings)
    return SchemaSnapshot(
        database_revision=current,
        bundled_head=target,
        state=evaluate_upgrade(current, target, known_revisions=known),
    )


def check_schema_or_raise(
    settings: Settings,
    *,
    head: HeadFn | None = None,
    revision: RevisionFn | None = None,
    known_revisions: Collection[str] | None = None,
) -> UpgradeState:
    """启动门禁：只允许在已初始化且版本一致时继续启动。

    数据库新于应用时抛 ``DatabaseAheadError``；未初始化或需要升级时抛
    ``UpgradeRequiredError``，提示先执行升级而不是带着旧结构启动。
    """
    snapshot = inspect_schema(
        settings, head=head, revision=revision, known_revisions=known_revisions
    )
    if snapshot.state is UpgradeState.DATABASE_AHEAD:
        raise DatabaseAheadError(
            f"数据库结构版本（{snapshot.database_revision}）高于当前应用内置版本"
            f"（{snapshot.bundled_head}）。新版数据库不支持由旧版应用打开，"
            "请安装较新的 CampusFlow 应用后重试；本应用不会降级数据库。"
        )
    if snapshot.state is UpgradeState.UNINITIALIZED:
        raise UpgradeRequiredError("数据库尚未初始化，请先执行升级迁移后再启动应用。")
    if snapshot.state is UpgradeState.UPGRADE_REQUIRED:
        raise UpgradeRequiredError(
            f"数据库结构版本（{snapshot.database_revision}）需要升级到"
            f"（{snapshot.bundled_head}）后才能启动应用。"
        )
    return snapshot.state


def run_upgrade(
    settings: Settings,
    *,
    backup: BackupFn | None = None,
    restore: RestoreFn | None = None,
    migrate: MigrateFn | None = None,
    head: HeadFn | None = None,
    revision: RevisionFn | None = None,
    known_revisions: Collection[str] | None = None,
    retention_count: int | None = None,
    prune: PruneFn | None = None,
) -> UpgradeReport:
    """备份、迁移、校验并按保留数清理旧备份。

    数据库更新于应用时直接拒绝；未初始化时直接建表（无可备份数据）；
    需要升级时先创建数据库与资料备份，迁移或校验失败立即恢复备份。
    保留清理只在升级成功后执行，恢复所用的备份永远不会被删除。
    """
    head_fn = head or _default_head
    revision_fn = revision or _default_revision
    migrate_fn = migrate or _default_migrate
    backup_fn = backup or _default_backup
    restore_fn = restore or _default_restore
    prune_fn = prune or _default_prune

    target = head_fn(settings.migrations_dir)
    snapshot = inspect_schema(
        settings,
        head=lambda _: target,
        revision=revision_fn,
        known_revisions=known_revisions,
    )
    if snapshot.state is UpgradeState.DATABASE_AHEAD:
        raise DatabaseAheadError(
            f"数据库结构版本（{snapshot.database_revision}）高于当前应用内置版本"
            f"（{target}）。新版数据库不支持由旧版应用打开，本次不执行任何迁移；"
            "请安装较新的 CampusFlow 应用。"
        )
    if snapshot.state is UpgradeState.UP_TO_DATE:
        return UpgradeReport(
            action="none",
            state=UpgradeState.UP_TO_DATE,
            previous_revision=snapshot.database_revision,
            bundled_head=target,
        )

    backup_info: BackupInfo | None = None
    if snapshot.state is UpgradeState.UPGRADE_REQUIRED:
        # 备份失败必须中止，不允许在没有可恢复副本时改库。
        backup_info = backup_fn(settings, db_revision=snapshot.database_revision, app_head=target)

    try:
        migrate_fn(settings, target)
        final_revision = revision_fn(settings)
        if final_revision != target:
            raise UpgradeError(
                f"迁移结束后数据库版本为 {final_revision or '空'}，与内置版本 {target} 不一致。"
            )
    except Exception as exc:
        if backup_info is None:
            raise UpgradeFailedError(
                "数据库初始化失败，未创建可用的数据库结构。请检查数据库日志后重试。"
            ) from exc
        try:
            restore_fn(settings, backup_info)
        except Exception as restore_exc:
            raise UpgradeFailedError(
                "数据库迁移失败，且自动恢复未成功。升级前备份保留在"
                f" {backup_info.path}，请勿删除并手动恢复后再重试。"
            ) from restore_exc
        raise UpgradeFailedError(
            f"数据库迁移失败，已自动恢复升级前的数据库与资料备份。备份保留在 {backup_info.path}。"
        ) from exc

    action: Literal["created_schema", "upgraded"] = (
        "created_schema" if backup_info is None else "upgraded"
    )
    pruned: tuple[str, ...] = ()
    keep = retention_count if retention_count is not None else settings.backup_retention_count
    try:
        pruned = prune_fn(
            settings,
            keep=keep,
            current=backup_info.path if backup_info is not None else None,
        )
    except Exception:
        # 清理失败不影响已经成功的升级，只留下更多备份供用户手动导出。
        logger.warning("backup_prune_failed: 升级已成功，但旧备份清理失败")
    return UpgradeReport(
        action=action,
        state=UpgradeState.UP_TO_DATE,
        previous_revision=snapshot.database_revision,
        bundled_head=target,
        backup=backup_info,
        pruned_backups=pruned,
    )


def upgrade_if_needed(settings: Settings, **ports: Any) -> UpgradeReport:
    """sidecar 启动入口：按需升级；关键字参数透传给 ``run_upgrade``。"""
    return run_upgrade(settings, **ports)
