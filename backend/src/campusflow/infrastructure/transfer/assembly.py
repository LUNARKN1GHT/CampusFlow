"""P010：迁移包服务的装配入口（CLI／Electron 菜单直接调用这三个函数）。

    from campusflow.core.config import Settings
    from campusflow.infrastructure.transfer import (
        export_workspace, inspect_archive, import_archive,
    )

    settings = Settings()                     # 或桌面模式显式构造
    export_workspace(settings, Path("~/Desktop/backup.campusflow"))

    info = inspect_archive(Path("backup.campusflow"), settings=settings)
    print(info.counts, info.requires_migration, info.requires_reauthorization)
    # 只有用户看到上面的内容并确认后，才把它带进 import：
    import_archive(
        settings,
        Path("backup.campusflow"),
        replace=True,                          # 目标库已有数据时必须显式声明
        confirm_token=info.confirm_token,      # 归档摘要，绑定具体文件
    )

`export_workspace` 不会追问路径以外的任何内容；`import_archive` 在拿到确认
令牌之前不做任何写入，导入失败时本机数据保持不变。

机制（详见 `application/transfer/service.py` 与 `docs/verification/DESKTOP-P010-transfer.md`）：

- 导出：统计关键表 → `pg_dump -Fc`（凭据表只排数据）→ 收集原文件与摘要 →
  原子写入 ZIP。整库导出，按工作空间裁剪会被拒绝。
- 检查：ZIP 结构/路径/重复条目 → 清单字段 → 格式版本 → 全部文件摘要 →
  `schema_head` 必须在本应用迁移范围内（更旧的 head 报告需要前向迁移）。
- 导入：确认令牌 = 归档文件摘要 → 非空目标需 `replace=True` → 安全备份 →
  临时库恢复 → 前向迁移 → 条目数校验 → 解资料并核对 → 换资料目录 →
  改库名切换；任一步失败删除临时库/工作目录并还原资料目录。

限制：导入需要 CREATEDB（桌面模式默认本机超级用户，服务器请配置
`CAMPUSFLOW_PG_ADMIN_URL`）；切换会断开目标库连接，应在服务停止时执行；
旧数据库与旧资料目录保留供人工回退，暂不自动清理；归档未加密，只写入
0600 权限的文件；跨 Windows 与跨版本的迁移未在本仓库验证。
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.engine import make_url

from campusflow.application.transfer import (
    ArchiveInfo,
    ExportResult,
    ImportResult,
    TargetDatabase,
    TransferService,
)
from campusflow.application.transfer import inspect_archive as _inspect_archive
from campusflow.application.transfer.errors import TransferError
from campusflow.core.config import Settings
from campusflow.core.resources import resolve_backup_dir
from campusflow.infrastructure.transfer.archive import CampusflowArchive
from campusflow.infrastructure.transfer.migrator import AlembicMigrator
from campusflow.infrastructure.transfer.pg import PgToolchain


def describe_target(settings: Settings, *, admin_url: str | None = None) -> TargetDatabase:
    """从配置派生目标库、应用角色与维护连接（默认连到 postgres 库）。"""
    url = make_url(settings.database_url)
    if not url.database:
        raise TransferError("数据库地址缺少库名，无法确定迁移目标。")
    resolved_admin = admin_url or settings.pg_admin_url
    if resolved_admin is None:
        resolved_admin = url.set(database="postgres").render_as_string(hide_password=False)
    return TargetDatabase(
        name=url.database,
        role=url.username or "",
        url=settings.database_url,
        admin_url=resolved_admin,
    )


def build_transfer_service(settings: Settings, *, admin_url: str | None = None) -> TransferService:
    """装配完整服务；测试可用 `admin_url` 指向具备建库权限的维护账户。"""
    target = describe_target(settings, admin_url=admin_url)
    return TransferService(
        target=target,
        storage_dir=Path(settings.storage_dir),
        backup_dir=resolve_backup_dir(settings),
        store=CampusflowArchive(),
        tools=PgToolchain(settings, admin_url=target.admin_url),
        migrator=AlembicMigrator(settings.migrations_dir),
    )


def export_workspace(
    settings: Settings,
    output_path: Path | str,
    *,
    workspace_id: int | None = None,
) -> ExportResult:
    """导出整库与全部原始资料；不传 `workspace_id`（首版不支持按空间裁剪）。"""
    return build_transfer_service(settings).export(output_path, workspace_id=workspace_id)


def inspect_archive(archive_path: Path | str, *, settings: Settings | None = None) -> ArchiveInfo:
    """只读检查归档，不需要连接业务数据库；返回的 `confirm_token` 供导入确认。"""
    migrations_dir = settings.migrations_dir if settings is not None else None
    return _inspect_archive(
        archive_path, store=CampusflowArchive(), migrator=AlembicMigrator(migrations_dir)
    )[1]


def import_archive(
    settings: Settings,
    archive_path: Path | str,
    *,
    replace: bool = False,
    confirm_token: str | None = None,
    admin_url: str | None = None,
) -> ImportResult:
    """把归档导入本机；`admin_url` 仅用于验收/测试时指定建库账户。"""
    return build_transfer_service(settings, admin_url=admin_url).import_archive(
        archive_path, replace=replace, confirm_token=confirm_token
    )


__all__ = [
    "ArchiveInfo",
    "ExportResult",
    "ImportResult",
    "build_transfer_service",
    "describe_target",
    "export_workspace",
    "import_archive",
    "inspect_archive",
]
