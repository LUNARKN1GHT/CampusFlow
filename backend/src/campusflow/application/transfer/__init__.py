"""P010：`.campusflow` 全量迁移包（导出、检查、导入）。

对外入口在 `campusflow.infrastructure.transfer`（装配了归档实现、pg 工具与
Alembic 迁移）；本包只包含用例、清单结构与端口，不依赖 Web/ORM/供应商 SDK。
"""

from campusflow.application.transfer.errors import (
    ArchiveFormatError,
    ArchiveMissingError,
    ArchiveVersionError,
    ConfirmationMismatchError,
    ConfirmationRequiredError,
    DatabaseToolError,
    DatabaseToolMissingError,
    ImportVerificationError,
    TargetNotEmptyError,
    TransferError,
)
from campusflow.application.transfer.manifest import (
    FORMAT_VERSION,
    ArchiveCounts,
    ArchiveInfo,
    ArchiveManifest,
    ExcludedCredential,
    FileEntry,
)
from campusflow.application.transfer.ports import (
    ArchiveStore,
    DatabaseTools,
    SchemaMigrator,
    TargetDatabase,
)
from campusflow.application.transfer.service import (
    ExportResult,
    ImportResult,
    TransferService,
    inspect_archive,
)

__all__ = [
    "FORMAT_VERSION",
    "ArchiveCounts",
    "ArchiveFormatError",
    "ArchiveInfo",
    "ArchiveManifest",
    "ArchiveMissingError",
    "ArchiveStore",
    "ArchiveVersionError",
    "ConfirmationMismatchError",
    "ConfirmationRequiredError",
    "DatabaseToolError",
    "DatabaseToolMissingError",
    "DatabaseTools",
    "ExcludedCredential",
    "ExportResult",
    "FileEntry",
    "ImportResult",
    "ImportVerificationError",
    "SchemaMigrator",
    "TargetDatabase",
    "TargetNotEmptyError",
    "TransferError",
    "TransferService",
    "inspect_archive",
]
