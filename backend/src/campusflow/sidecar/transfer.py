"""`export`／`import`：P010 `.campusflow` 迁移包的 CLI 入口。

编排全部在 `application.transfer` 与 `infrastructure.transfer`；这里只做参数
映射、JSON 序列化与退出码折算，不复制校验、备份与切换步骤。约定：

- `export` 始终输出一个 JSON 对象（路径、大小、SHA-256、确认令牌与清单摘要）。
- `import --inspect` 只读，输出 `ArchiveInfo`（含 `confirm_token`），不触碰数据。
- 不带 `--inspect` 时必须显式传 `--confirm-token`（只接受命令行参数，不读环境
  变量，避免令牌意外经环境外泄）；非空目标还要 `--replace`。模块给出的中文
  拒绝信息原样作为退出码 1 的原因。
"""

from __future__ import annotations

from typing import Any

from campusflow.application.transfer import ArchiveInfo, ExportResult, ImportResult
from campusflow.core.config import Settings
from campusflow.domain.errors import DomainError
from campusflow.infrastructure.transfer import (
    export_workspace,
    import_archive,
    inspect_archive,
)
from campusflow.sidecar.errors import SidecarError
from campusflow.sidecar.logs import EventLogger, scrub_text


def _credentials(items: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [item.model_dump() for item in items]


def export_payload(result: ExportResult) -> dict[str, Any]:
    return {
        "path": str(result.archive_path),
        "size": result.archive_size,
        "sha256": result.archive_sha256,
        "confirm_token": result.confirm_token,
        "created_at": result.created_at.isoformat(),
        "database_size": result.database_size,
        "database_sha256": result.database_sha256,
        "material_files": result.material_files,
        "material_bytes": result.material_bytes,
        "counts": dict(result.counts),
        "missing_referenced_files": list(result.missing_referenced_files),
        "omitted_material_files": list(result.omitted_material_files),
        "excluded_credentials": _credentials(result.excluded_credentials),
    }


def archive_payload(info: ArchiveInfo) -> dict[str, Any]:
    return {
        "path": info.path,
        "format_version": info.format_version,
        "created_at": info.created_at.isoformat(),
        "app_version": info.app_version,
        "schema_head": info.schema_head,
        "platform": info.platform,
        "counts": dict(info.counts),
        "database_size": info.database_size,
        "database_sha256": info.database_sha256,
        "material_count": info.material_count,
        "material_bytes": info.material_bytes,
        "missing_referenced_files": list(info.missing_referenced_files),
        "omitted_material_files": list(info.omitted_material_files),
        "excluded_credentials": _credentials(info.excluded_credentials),
        "archive_size": info.archive_size,
        "archive_sha256": info.archive_sha256,
        "confirm_token": info.confirm_token,
        "requires_migration": info.requires_migration,
        "migration_message": info.migration_message,
        "requires_reauthorization": list(info.requires_reauthorization),
    }


def import_payload(result: ImportResult) -> dict[str, Any]:
    return {
        "path": str(result.archive_path),
        "archive_sha256": result.archive_sha256,
        "restored_counts": dict(result.restored_counts),
        "migrated_from": result.migrated_from,
        "migrated_to": result.migrated_to,
        "replaced_database": result.replaced_database,
        "replaced_storage": (
            str(result.replaced_storage) if result.replaced_storage is not None else None
        ),
        "backup_archive": (
            str(result.backup_archive) if result.backup_archive is not None else None
        ),
        "requires_reauthorization": list(result.requires_reauthorization),
        "warnings": list(result.warnings),
    }


def run_export(
    settings: Settings,
    logger: EventLogger,
    output: str,
    *,
    workspace_id: int | None = None,
) -> dict[str, Any]:
    try:
        result = export_workspace(settings, output, workspace_id=workspace_id)
    except DomainError as exc:
        raise SidecarError(scrub_text(str(exc), [])) from None
    payload = export_payload(result)
    logger.info(
        "export_completed",
        path=payload["path"],
        size=payload["size"],
        material_files=payload["material_files"],
    )
    return payload


def inspect_archive_payload(settings: Settings, archive: str) -> dict[str, Any]:
    try:
        info = inspect_archive(archive, settings=settings)
    except DomainError as exc:
        raise SidecarError(scrub_text(str(exc), [])) from None
    return archive_payload(info)


def run_import(
    settings: Settings,
    logger: EventLogger,
    archive: str,
    *,
    replace: bool = False,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    try:
        result = import_archive(settings, archive, replace=replace, confirm_token=confirm_token)
    except DomainError as exc:
        raise SidecarError(scrub_text(str(exc), [])) from None
    payload = import_payload(result)
    logger.info(
        "import_completed",
        path=payload["path"],
        migrated_to=payload["migrated_to"],
        replaced_database=payload["replaced_database"],
    )
    return payload


__all__ = [
    "archive_payload",
    "import_payload",
    "inspect_archive_payload",
    "run_export",
    "run_import",
]
