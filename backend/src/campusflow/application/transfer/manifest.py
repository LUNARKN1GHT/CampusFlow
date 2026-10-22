"""P010：`.campusflow` 归档清单（manifest.json）的结构与兼容性规则。

归档布局（ZIP）：

    manifest.json          本文件描述的清单，UTF-8 JSON，字段固定
    database.dump          pg_dump -Fc 逻辑备份（已排除机器绑定凭据表数据）
    materials/<存储键>      私有存储目录中的原始文件，保持相对路径与 `/` 分隔

清单是导入前唯一可信的"应该有什么"：条目数、文件大小与 SHA-256 都先与
清单核对，再恢复数据；清单本身不包含任何密钥、令牌或会话内容。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from campusflow.application.transfer.errors import ArchiveVersionError

# 归档格式版本：结构或语义发生不兼容变化时递增；导入只接受 <= 当前版本。
FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"
DATABASE_NAME = "database.dump"
MATERIALS_PREFIX = "materials"
ARCHIVE_SUFFIX = ".campusflow"

# 清单里的条目数键 -> 实际表名。键名面向用户与验收，表名跟随内部模型。
COUNT_TABLES: dict[str, str] = {
    "workspaces": "workspaces",
    "semesters": "semesters",
    "courses": "courses",
    "materials": "materials",
    "material_versions": "material_versions",
    "candidates": "extracted_items",
    "tasks": "tasks",
    "plans": "plan_drafts",
    "time_blocks": "plan_blocks",
    "indexed_blocks": "indexed_blocks",
}

# 只截取表数据、保留表结构：外部日历状态与机器/账户绑定，换机后必须重新授权。
# 依赖这些连接的表一并排除，避免恢复时外键指向不存在的连接。
EXCLUDED_TABLE_DATA: tuple[tuple[str, str], ...] = (
    ("calendar_connections", "外部日历连接与 OAuth 令牌密文，随账户与机器绑定；导入后必须重新授权"),
    ("calendar_oauth_states", "OAuth state 哈希，一次性且与授权会话绑定"),
    ("calendar_drafts", "外部日历写入草案，确认指纹绑定旧连接"),
    ("calendar_draft_items", "外部日历草案条目，随草案一并排除"),
    ("calendar_operations", "外部写入执行记录，幂等键与旧连接绑定"),
    ("calendar_event_links", "外部事件链接，随连接一并失效"),
)

# 只存在于环境变量中的凭据：从不写入数据库，因此也不会进入归档。
EXCLUDED_ENVIRONMENT_CREDENTIALS: tuple[tuple[str, str], ...] = (
    ("ZHIPU_API_KEY", "模型供应商密钥，仅从环境变量读取"),
    ("DEEPSEEK_API_KEY", "模型供应商密钥，仅从环境变量读取"),
    ("GOOGLE_CALENDAR_CLIENT_SECRET", "Google OAuth 客户端密钥，仅从环境变量读取"),
    ("CAMPUSFLOW_CALENDAR_TOKEN_KEY", "外部日历令牌的加密密钥，只在本机使用"),
    ("CAMPUSFLOW_LOCAL_PASSWORD", "本机登录口令，只在本机使用"),
)

# 恢复临时库前必须已安装的数据库扩展（结构依赖，不随归档携带）。
# 桌面安装包自带 pgvector；缺失时导入必须以中文错误提示安装或更换数据目录。
REQUIRED_EXTENSIONS: tuple[str, ...] = ("vector",)

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


def validate_relative_posix_path(value: str) -> str:
    """归档内部路径必须是相对 POSIX 路径：拒绝盘符、反斜杠、绝对路径与 `..`。

    这是跨系统可移植性的底线：macOS 导出、Windows 导入只依赖 `/` 分隔的
    相对路径，绝不把本机绝对路径写进归档。
    """
    if not value:
        raise ValueError("归档路径不能为空")
    if "\\" in value:
        raise ValueError(f"归档路径不得包含反斜杠：{value!r}")
    if value.startswith("/") or ":" in value:
        raise ValueError(f"归档路径必须是相对路径：{value!r}")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"归档路径不得包含空段、`.` 或 `..`：{value!r}")
    return value


class FileEntry(BaseModel):
    """归档内的一个文件；大小与 SHA-256 在导入前逐一核对。"""

    model_config = ConfigDict(extra="forbid")

    path: str
    size: int = Field(ge=0)
    sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("path")
    @classmethod
    def _check_path(cls, value: str) -> str:
        # 相对存储键本身就是 POSIX 相对路径，这里只校验不重写。
        return validate_relative_posix_path(value)


class DatabaseDump(FileEntry):
    """逻辑备份条目；文件名固定，便于旧版本读取。"""

    path: Literal["database.dump"] = DATABASE_NAME


class ArchiveCounts(BaseModel):
    """关键表的条目数；导入后必须逐项与恢复结果一致。"""

    model_config = ConfigDict(extra="forbid")

    workspaces: int = Field(ge=0)
    semesters: int = Field(ge=0)
    courses: int = Field(ge=0)
    materials: int = Field(ge=0)
    material_versions: int = Field(ge=0)
    candidates: int = Field(ge=0)
    tasks: int = Field(ge=0)
    plans: int = Field(ge=0)
    time_blocks: int = Field(ge=0)
    indexed_blocks: int = Field(ge=0)

    def as_dict(self) -> dict[str, int]:
        return {
            "workspaces": self.workspaces,
            "semesters": self.semesters,
            "courses": self.courses,
            "materials": self.materials,
            "material_versions": self.material_versions,
            "candidates": self.candidates,
            "tasks": self.tasks,
            "plans": self.plans,
            "time_blocks": self.time_blocks,
            "indexed_blocks": self.indexed_blocks,
        }


class ExcludedCredential(BaseModel):
    """被显式排除、导入后需要重新录入或授权的凭据。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    kind: Literal["table_data", "environment", "process_session"]
    reason: str
    requires_reentry: bool = True


class ArchiveManifest(BaseModel):
    """manifest.json 的完整结构；字段缺失或类型不符时整包拒绝。"""

    model_config = ConfigDict(extra="ignore")

    format_version: int = Field(ge=1)
    created_at: datetime
    app_version: str
    schema_head: str
    platform: str
    database: DatabaseDump
    materials: list[FileEntry]
    missing_referenced_files: list[str] = Field(default_factory=list)
    omitted_material_files: list[str] = Field(default_factory=list)
    counts: ArchiveCounts
    excluded_credentials: list[ExcludedCredential] = Field(default_factory=list)

    @field_validator("created_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("created_at 必须带时区（UTC）")
        return value.astimezone(UTC)

    @field_validator("schema_head")
    @classmethod
    def _check_schema_head(cls, value: str) -> str:
        if not value:
            raise ValueError("schema_head 不能为空")
        return value


@dataclass(frozen=True)
class ArchiveInfo:
    """检查通过的归档摘要；`confirm_token` 绑定归档文件摘要，供导入确认使用。"""

    path: str
    format_version: int
    created_at: datetime
    app_version: str
    schema_head: str
    platform: str
    counts: dict[str, int]
    database_size: int
    database_sha256: str
    material_count: int
    material_bytes: int
    missing_referenced_files: tuple[str, ...]
    omitted_material_files: tuple[str, ...]
    excluded_credentials: tuple[ExcludedCredential, ...]
    archive_size: int
    archive_sha256: str
    confirm_token: str
    requires_migration: bool
    migration_message: str | None

    @property
    def requires_reauthorization(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.excluded_credentials if item.requires_reentry)


def confirm_token_for_digest(digest: str) -> str:
    """确认令牌就是归档文件摘要；不接受只凭路径或时间确认。"""
    return f"sha256:{digest}"


def ensure_supported_format(manifest: ArchiveManifest, supported: int = FORMAT_VERSION) -> None:
    """格式版本高于当前应用时拒绝：新结构可能带旧程序无法识别的字段与语义。"""
    if manifest.format_version > supported:
        raise ArchiveVersionError(
            f"归档格式版本 {manifest.format_version} 高于当前应用支持的 {supported}，"
            "请升级 CampusFlow 后再导入；本机数据未做任何修改。"
        )
