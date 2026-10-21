"""P010：迁移包装配与实现（ZIP 归档、pg 工具、Alembic 迁移）。

CLI／Electron 调用 `export_workspace`、`inspect_archive`、`import_archive`
三个函数即可；签名与用法见 `assembly.py` 模块文档。
"""

from campusflow.infrastructure.transfer.archive import CampusflowArchive
from campusflow.infrastructure.transfer.assembly import (
    build_transfer_service,
    describe_target,
    export_workspace,
    import_archive,
    inspect_archive,
)
from campusflow.infrastructure.transfer.migrator import AlembicMigrator
from campusflow.infrastructure.transfer.pg import PgToolchain

__all__ = [
    "AlembicMigrator",
    "CampusflowArchive",
    "PgToolchain",
    "build_transfer_service",
    "describe_target",
    "export_workspace",
    "import_archive",
    "inspect_archive",
]
