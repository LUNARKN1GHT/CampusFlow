"""P010：`.campusflow` 全量迁移包的错误类型。

所有错误都在触碰用户数据之前抛出，或按"失败不影响原有数据"的方式处理；
错误文案必须是中文且说明下一步，不能只留下英文堆栈或静默忽略。
"""

from __future__ import annotations

from campusflow.domain.errors import ConflictError, DomainError


class TransferError(DomainError):
    """迁移包相关错误（400）：归档不可用、目标不允许导入或工具缺失。"""


class ArchiveMissingError(TransferError):
    """归档文件不存在或不是普通文件。"""


class ArchiveFormatError(TransferError):
    """归档结构损坏：不是 ZIP、缺少清单、条目越界或校验和不符。"""


class ArchiveVersionError(TransferError):
    """格式版本或数据库版本与当前应用不兼容。"""


class ConfirmationRequiredError(TransferError):
    """缺少确认令牌：导入会替换本机数据，必须先显式确认具体归档。"""


class ConfirmationMismatchError(ConflictError):
    """确认令牌与归档摘要不一致（归档被替换或检查后又被修改）。"""


class TargetNotEmptyError(ConflictError):
    """目标数据库已有数据；未声明 replace 时不得覆盖。"""


class DatabaseToolError(TransferError):
    """pg_dump／pg_restore 或数据库管理操作失败。"""


class DatabaseToolMissingError(DatabaseToolError):
    """找不到需要的 PostgreSQL 客户端工具。"""


class ImportVerificationError(TransferError):
    """恢复后的数据与清单不一致（条目数或文件校验和），已放弃切换。"""
