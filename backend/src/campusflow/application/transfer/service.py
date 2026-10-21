"""P010：`.campusflow` 全量导出的用例编排。

导出是本机数据库的完整逻辑备份 + 私有存储里的原始资料，不按工作空间裁剪
（裁剪需要重写全部外键，首版不做；传 `workspace_id` 会被明确拒绝）。
凭据表只保留结构、不保留数据；归档未加密，因此只写到用户选定路径并提示。
"""

from __future__ import annotations

import importlib.metadata
import platform
import re
import secrets
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from campusflow.application.transfer.errors import (
    ArchiveMissingError,
    ArchiveVersionError,
    ConfirmationMismatchError,
    ConfirmationRequiredError,
    ImportVerificationError,
    TargetNotEmptyError,
    TransferError,
)
from campusflow.application.transfer.manifest import (
    ARCHIVE_SUFFIX,
    COUNT_TABLES,
    DATABASE_NAME,
    EXCLUDED_ENVIRONMENT_CREDENTIALS,
    EXCLUDED_TABLE_DATA,
    FORMAT_VERSION,
    REQUIRED_EXTENSIONS,
    ArchiveCounts,
    ArchiveInfo,
    ArchiveManifest,
    ExcludedCredential,
    FileEntry,
    confirm_token_for_digest,
    ensure_supported_format,
)
from campusflow.application.transfer.materials import (
    collect_material_files,
    sha256_file,
)
from campusflow.application.transfer.ports import (
    ArchiveStore,
    DatabaseTools,
    SchemaMigrator,
    TargetDatabase,
)

# 归档只在用户显式确认后产生，且不覆盖正在写入的其它文件；文件名保持可读。
_TIME_FORMAT = "%Y%m%dT%H%M%SZ"
_PACKAGE_NAME = "campusflow-backend"


@dataclass(frozen=True)
class ExportResult:
    """导出结果：路径、摘要与清单条目数；摘要同时充当导入确认令牌的输入。"""

    archive_path: Path
    archive_size: int
    archive_sha256: str
    confirm_token: str
    created_at: datetime
    database_size: int
    database_sha256: str
    material_files: int
    material_bytes: int
    counts: dict[str, int]
    missing_referenced_files: tuple[str, ...]
    omitted_material_files: tuple[str, ...]
    excluded_credentials: tuple[ExcludedCredential, ...]


@dataclass(frozen=True)
class ImportResult:
    """导入结果：恢复后的条目数、迁移区间、旧数据去向与需重新录入的凭据。"""

    archive_path: Path
    archive_sha256: str
    restored_counts: dict[str, int]
    migrated_from: str
    migrated_to: str
    replaced_database: str | None
    replaced_storage: Path | None
    backup_archive: Path | None
    requires_reauthorization: tuple[str, ...]
    warnings: tuple[str, ...]


class TransferService:
    """把端口编排成"检查 → 备份 → 临时恢复 → 校验 → 切换"的完整流程。

    调用方（CLI／Electron sidecar／验收脚本）只依赖本类方法；
    不直接接触 pg_dump、SQLAlchemy 或 ZIP 细节。
    """

    def __init__(
        self,
        *,
        target: TargetDatabase,
        storage_dir: Path,
        backup_dir: Path,
        store: ArchiveStore,
        tools: DatabaseTools,
        migrator: SchemaMigrator,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.target = target
        self.storage_dir = Path(storage_dir)
        self.backup_dir = Path(backup_dir)
        self.store = store
        self.tools = tools
        self.migrator = migrator
        self._clock = clock or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------ 导出

    def export(self, output_path: Path | str, *, workspace_id: int | None = None) -> ExportResult:
        """导出整库与全部原始资料到 `output_path`（`.campusflow`）。

        先写临时文件再原子改名；任何一步失败都不在最终路径留下半个归档。
        """
        if workspace_id is not None:
            raise TransferError(
                "首版只支持整库导出，不支持按工作空间裁剪：迁移包必须保持外键完整。"
                "请省略 workspace_id 后重试。"
            )
        output = Path(output_path).expanduser()
        if output.suffix != ARCHIVE_SUFFIX:
            raise TransferError(f"归档文件名必须以 {ARCHIVE_SUFFIX} 结尾：{output}")
        output.parent.mkdir(parents=True, exist_ok=True)

        current_revision = self.tools.current_revision(self.target.url)
        if current_revision is None:
            raise TransferError(
                "目标数据库还没有迁移版本记录（alembic_version 为空），"
                "请先启动应用完成迁移，再导出。"
            )

        storage_root = self.storage_root()
        collected = collect_material_files(storage_root)
        material_entries = tuple(
            FileEntry(path=relative, size=source.stat().st_size, sha256=sha256_file(source))
            for relative, source in collected.files
        )
        referenced = set(self.tools.stored_material_keys(self.target.url))
        present = {relative for relative, _ in collected.files}
        missing = tuple(sorted(referenced - present))

        existing_tables = self.tools.table_names(
            self.target.url, [table for table, _ in EXCLUDED_TABLE_DATA]
        )
        exclude_data = [table for table, _ in EXCLUDED_TABLE_DATA if table in existing_tables]
        counts = self.tools.count_rows(self.target.url, COUNT_TABLES)

        created_at = self._now()
        with tempfile.TemporaryDirectory(dir=output.parent, prefix=".campusflow-dump-") as temp:
            dump_path = Path(temp) / DATABASE_NAME
            self.tools.dump_database(self.target.url, dump_path, exclude_table_data=exclude_data)
            dump_size = dump_path.stat().st_size
            dump_sha256 = sha256_file(dump_path)
            manifest = ArchiveManifest(
                format_version=FORMAT_VERSION,
                created_at=created_at,
                app_version=_app_version(),
                schema_head=current_revision,
                platform=platform.platform(),
                database={
                    "path": DATABASE_NAME,
                    "size": dump_size,
                    "sha256": dump_sha256,
                },
                materials=list(material_entries),
                missing_referenced_files=list(missing),
                omitted_material_files=list(collected.omitted),
                counts=ArchiveCounts(**counts),
                excluded_credentials=_excluded_credentials(),
            )
            self.store.write(output, manifest, dump_path, collected.files)

        archive_size = output.stat().st_size
        archive_sha256 = sha256_file(output)
        return ExportResult(
            archive_path=output,
            archive_size=archive_size,
            archive_sha256=archive_sha256,
            confirm_token=confirm_token_for_digest(archive_sha256),
            created_at=created_at,
            database_size=dump_size,
            database_sha256=dump_sha256,
            material_files=len(material_entries),
            material_bytes=sum(entry.size for entry in material_entries),
            counts=counts,
            missing_referenced_files=missing,
            omitted_material_files=collected.omitted,
            excluded_credentials=_excluded_credentials(),
        )

    # ------------------------------------------------------------------ 检查

    def inspect(self, archive_path: Path | str) -> ArchiveInfo:
        """只读检查归档：结构、格式版本、校验和与数据库版本兼容性。"""
        return self._inspect(archive_path)[1]

    def _inspect(self, archive_path: Path | str) -> tuple[ArchiveManifest, ArchiveInfo]:
        return inspect_archive(archive_path, store=self.store, migrator=self.migrator)

    # ------------------------------------------------------------------ 导入

    def import_archive(
        self,
        archive_path: Path | str,
        *,
        replace: bool = False,
        confirm_token: str | None = None,
    ) -> ImportResult:
        """把归档导入本机：先备份，再恢复到临时库，校验通过后才切换。

        确认令牌必须等于 `inspect()` 返回的 `confirm_token`，即归档文件摘要；
        目标库已有数据时还要求 `replace=True`。任何失败都不会改动本机数据。
        """
        manifest, info = self._inspect(archive_path)
        if not confirm_token:
            raise ConfirmationRequiredError(
                "导入会替换本机数据，必须先检查归档并向用户展示内容，"
                f"再传入确认令牌：{info.confirm_token}"
            )
        if confirm_token.strip() != info.confirm_token:
            raise ConfirmationMismatchError(
                "确认令牌与当前归档摘要不一致：归档可能已被替换或修改。"
                "请重新检查归档并取得新的确认令牌；本机数据未做任何修改。"
            )

        archive = Path(info.path)
        tools = self.tools
        if not tools.database_exists(self.target.name):
            tools.create_database(self.target.name, self.target.role)

        existing_counts = tools.count_rows(self.target.url, COUNT_TABLES)
        has_data = any(existing_counts.values())
        if has_data and not replace:
            summary = "、".join(f"{key} {value}" for key, value in existing_counts.items() if value)
            raise TargetNotEmptyError(
                f"目标数据库 {self.target.name} 已有数据（{summary}）。"
                "导入会整体替换本机数据：确认后请以 replace=True 重新提交同一确认令牌。"
            )

        stamp = self._now().strftime(_TIME_FORMAT)
        unique = secrets.token_hex(2)
        backup_archive: Path | None = None
        if tools.current_revision(self.target.url) is not None:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            backup_archive = self.backup_dir / f"pre-import-{stamp}-{unique}.campusflow"
            self.export(backup_archive)

        scratch_name = f"campusflow_import_{secrets.token_hex(6)}"
        work_dir = self.storage_root().parent / f".campusflow-import-{secrets.token_hex(6)}"
        dump_path = work_dir / "database" / DATABASE_NAME
        materials_dir = work_dir / "materials"
        storage_root = self.storage_root()
        previous_storage: Path | None = None
        previous_database = _previous_database_name(self.target.name, f"{stamp}_{unique}")
        storage_swapped = False
        scratch_created = False

        try:
            work_dir.mkdir(parents=True, exist_ok=False)
            tools.create_database(scratch_name, self.target.role)
            scratch_created = True
            extensions = sorted(
                set(tools.installed_extensions(self.target.url)) | set(REQUIRED_EXTENSIONS)
            )
            tools.provision_extensions(scratch_name, extensions)

            self.store.extract_database(archive, manifest, dump_path)
            scratch_url = tools.connection_url(scratch_name)
            tools.restore_database(scratch_url, dump_path)
            self.migrator.upgrade_to_head(scratch_url)

            restored = tools.count_rows(scratch_url, COUNT_TABLES)
            mismatched = {
                key: (manifest.counts.as_dict()[key], restored.get(key, 0))
                for key in COUNT_TABLES
                if restored.get(key, 0) != manifest.counts.as_dict()[key]
            }
            if mismatched:
                detail = "；".join(
                    f"{key} 期望 {expected}，实际 {actual}"
                    for key, (expected, actual) in mismatched.items()
                )
                raise ImportVerificationError(
                    f"恢复后的数据与归档清单不一致（{detail}）。已放弃切换，本机原有数据保持不变。"
                )

            self.store.extract_materials(archive, manifest, materials_dir)

            if storage_root.exists():
                self.backup_dir.mkdir(parents=True, exist_ok=True)
                previous_storage = self.backup_dir / f"storage-pre-import-{stamp}-{unique}"
                shutil.move(str(storage_root), str(previous_storage))
            storage_root.parent.mkdir(parents=True, exist_ok=True)
            if materials_dir.is_dir():
                shutil.move(str(materials_dir), str(storage_root))
            else:
                storage_root.mkdir(parents=True, exist_ok=True)
            storage_swapped = True

            try:
                tools.switch_database(
                    target=self.target.name,
                    replacement=scratch_name,
                    previous=previous_database,
                )
            except Exception:
                # 数据库切换失败：把资料目录还原，之后再清理临时库。
                if storage_swapped:
                    _discard_path(storage_root)
                    if previous_storage is not None and previous_storage.exists():
                        shutil.move(str(previous_storage), str(storage_root))
                    storage_swapped = False
                raise
            scratch_created = False  # 已改名为目标库，不能再当临时库清理
        except Exception:
            if scratch_created:
                _safe_drop(tools, scratch_name)
            shutil.rmtree(work_dir, ignore_errors=True)
            raise
        else:
            shutil.rmtree(work_dir, ignore_errors=True)

        if not has_data:
            # 原库本来就是空的（或不存在），没有保留价值；删掉它避免改名后的空库堆积。
            _safe_drop(tools, previous_database)
        warnings = self._import_warnings(
            info, previous_storage, previous_database if has_data else None
        )
        return ImportResult(
            archive_path=archive,
            archive_sha256=info.archive_sha256,
            restored_counts=restored,
            migrated_from=info.schema_head,
            migrated_to=self.migrator.head_revision(),
            replaced_database=previous_database if has_data else None,
            replaced_storage=previous_storage,
            backup_archive=backup_archive,
            requires_reauthorization=info.requires_reauthorization,
            warnings=warnings,
        )

    # ------------------------------------------------------------------ 辅助

    def storage_root(self) -> Path:
        root = Path(self.storage_dir).expanduser()
        if not root.is_absolute():
            root = Path.cwd() / root
        return root.resolve()

    def _now(self) -> datetime:
        return self._clock().astimezone(UTC)

    def _import_warnings(
        self, info: ArchiveInfo, previous_storage: Path | None, previous_database: str | None
    ) -> tuple[str, ...]:
        warnings: list[str] = []
        if info.migration_message:
            warnings.append(info.migration_message)
        if info.omitted_material_files:
            warnings.append(
                f"归档跳过了 {len(info.omitted_material_files)} 个非普通文件或符号链接。"
            )
        if info.missing_referenced_files:
            warnings.append(
                f"{len(info.missing_referenced_files)} 个数据库引用的原文件在导出时已缺失，"
                "相关引用将无法定位原文。"
            )
        warnings.append("归档未加密；请把文件保存在受控目录，并按需限制访问权限。")
        if previous_database is not None:
            warnings.append(f"导入前的旧数据库保留为 {previous_database}，确认无误后可手动删除。")
        if previous_storage is not None:
            warnings.append(f"导入前的资料目录保留在 {previous_storage}。")
        if info.requires_reauthorization:
            names = "、".join(info.requires_reauthorization)
            warnings.append(f"以下凭据未随包迁移，需要在设置中重新录入或授权：{names}。")
        return tuple(warnings)


def inspect_archive(
    archive_path: Path | str, *, store: ArchiveStore, migrator: SchemaMigrator
) -> tuple[ArchiveManifest, ArchiveInfo]:
    """只读检查归档（不连接业务数据库）：结构、格式版本、校验和与迁移兼容性。

    任何一项不满足都抛错，调用方据此在触碰数据之前停止导入。
    """
    archive = Path(archive_path).expanduser()
    if not archive.is_file():
        raise ArchiveMissingError(f"归档文件不存在：{archive}")
    manifest = store.read(archive)
    ensure_supported_format(manifest, FORMAT_VERSION)
    store.verify(archive, manifest)

    head = migrator.head_revision()
    known = migrator.known_revisions()
    if manifest.schema_head not in known:
        raise ArchiveVersionError(
            f"归档的数据库版本 {manifest.schema_head} 不在当前应用认识的迁移范围内，"
            f"通常表示归档来自更新的 CampusFlow（当前 head：{head}）。"
            "请升级应用后再导入；本机数据未做任何修改。"
        )
    requires_migration = manifest.schema_head != head
    message = (
        f"归档数据库版本 {manifest.schema_head} 早于当前版本 {head}，"
        "导入时会先执行前向迁移，完成后以当前版本为准。"
        if requires_migration
        else None
    )
    digest = sha256_file(archive)
    info = ArchiveInfo(
        path=str(archive.resolve()),
        format_version=manifest.format_version,
        created_at=manifest.created_at,
        app_version=manifest.app_version,
        schema_head=manifest.schema_head,
        platform=manifest.platform,
        counts=manifest.counts.as_dict(),
        database_size=manifest.database.size,
        database_sha256=manifest.database.sha256,
        material_count=len(manifest.materials),
        material_bytes=sum(entry.size for entry in manifest.materials),
        missing_referenced_files=tuple(manifest.missing_referenced_files),
        omitted_material_files=tuple(manifest.omitted_material_files),
        excluded_credentials=tuple(manifest.excluded_credentials),
        archive_size=archive.stat().st_size,
        archive_sha256=digest,
        confirm_token=confirm_token_for_digest(digest),
        requires_migration=requires_migration,
        migration_message=message,
    )
    return manifest, info


def _app_version() -> str:
    try:
        return importlib.metadata.version(_PACKAGE_NAME)
    except importlib.metadata.PackageNotFoundError:  # 源码直接运行、未安装包时
        return "0.0.0+unknown"


def _excluded_credentials() -> tuple[ExcludedCredential, ...]:
    excluded = [
        ExcludedCredential(name=table, kind="table_data", reason=reason, requires_reentry=True)
        for table, reason in EXCLUDED_TABLE_DATA
    ]
    excluded.extend(
        ExcludedCredential(
            name=name,
            kind="environment",
            reason=reason,
            requires_reentry=name not in {"CAMPUSFLOW_CALENDAR_TOKEN_KEY"},
        )
        for name, reason in EXCLUDED_ENVIRONMENT_CREDENTIALS
    )
    excluded.append(
        ExcludedCredential(
            name="session_cookie",
            kind="process_session",
            reason="本机登录会话 Cookie 只存在于进程内存，导入后需要重新登录",
            requires_reentry=True,
        )
    )
    return tuple(excluded)


def _previous_database_name(target: str, stamp: str) -> str:
    """旧库名保留可读前缀并满足 PostgreSQL 63 字节标识符上限。"""
    suffix = f"_pre_import_{stamp}"
    prefix = re.sub(r"[^0-9A-Za-z_]", "_", target)[: 63 - len(suffix)]
    return f"{prefix}{suffix}"


def _safe_drop(tools: DatabaseTools, name: str) -> None:
    try:
        tools.drop_database(name)
    except Exception:  # 清理失败不掩盖原始错误；临时库名已记录在错误上下文中
        pass


def _discard_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists():
        path.unlink(missing_ok=True)


__all__ = [
    "ExportResult",
    "ImportResult",
    "TransferService",
    "inspect_archive",
]
