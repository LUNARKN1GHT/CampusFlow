"""`backup`／`restore`：数据库自定义格式转储 + 资料存储目录的单一归档。

归档是未压缩的 tar（pg_dump 自定义格式自带压缩），成员固定为：

- `manifest.json`：格式版本、时间、脱敏后的连接串、迁移头与文件数；
- `database.dump`：`pg_dump -Fc` 输出；
- `storage/…`：资料私有存储目录的原样副本。

恢复默认先尝试导入临时库再改名（失败不动原库），角色没有建库权限时退回
直接 `pg_restore --clean --if-exists`；两种方式都要求显式 `--replace` 才会
覆盖已有数据库。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from campusflow.core.config import Settings
from campusflow.sidecar.dbtools import (
    connect,
    database_has_content,
    database_name,
    migration_state,
    redacted_database_url,
    script_heads,
)
from campusflow.sidecar.errors import SidecarError
from campusflow.sidecar.logs import EventLogger, redact_text

ARCHIVE_FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"
DATABASE_MEMBER = "database.dump"
STORAGE_PREFIX = "storage"
# 归档内的 manifest 只应该很小；异常大文件说明归档被改写，直接拒绝读取。
MAX_MANIFEST_BYTES = 64 * 1024
TOOL_TIMEOUT_SECONDS = 3600
_IDENTIFIER = re.compile(r"[A-Za-z0-9_]+")


@dataclass
class BackupResult:
    path: Path
    archive_bytes: int
    storage_files: int
    database: str
    alembic_head: list[str] = field(default_factory=list)
    alembic_current: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "archive_bytes": self.archive_bytes,
            "storage_files": self.storage_files,
            "database": self.database,
            "alembic_head": self.alembic_head,
            "alembic_current": self.alembic_current,
        }


@dataclass
class RestoreResult:
    archive: Path
    database: str
    mode: str
    manifest: dict[str, Any]
    safety_backup: Path | None
    storage_files: int
    storage_dir: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "archive": str(self.archive),
            "database": self.database,
            "mode": self.mode,
            "safety_backup": str(self.safety_backup) if self.safety_backup else None,
            "storage_files": self.storage_files,
            "storage_dir": self.storage_dir,
            "alembic_head": self.manifest.get("alembic_head"),
        }


def find_pg_tool(name: str) -> str:
    """定位 PostgreSQL 客户端工具：优先 `CAMPUSFLOW_PG_BIN`，其次 PATH。

    打包桌面版不依赖用户安装 PostgreSQL，因此需要显式目录；找不到时给出
    可执行的下一步，而不是让 pg_dump 抛 FileNotFoundError。
    """
    configured = os.environ.get("CAMPUSFLOW_PG_BIN")
    if configured:
        binary_dir = Path(configured).expanduser()
        for candidate in (binary_dir / name, binary_dir / f"{name}.exe"):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        raise SidecarError(f"CAMPUSFLOW_PG_BIN={configured} 下找不到可执行的 {name}")
    found = shutil.which(name)
    if found is None:
        raise SidecarError(
            f"未找到 {name}：请安装 PostgreSQL 客户端，或用 CAMPUSFLOW_PG_BIN 指向其 bin 目录"
        )
    return found


def _pg_environment(database_url: str) -> tuple[str, dict[str, str]]:
    """返回不含口令的 libpq 地址与环境变量；口令只经 PGPASSWORD 传递。"""
    url = make_url(database_url)
    env = os.environ.copy()
    # 避免外部环境里的 PGSERVICE／PGPASSFILE 改变目标，导致备份到错误的库。
    for variable in ("PGSERVICE", "PGSERVICEFILE", "PGSYSCONFDIR"):
        env.pop(variable, None)
    password = url.password
    if password:
        env["PGPASSWORD"] = password
    else:
        env.pop("PGPASSWORD", None)
    dsn = url.set(drivername="postgresql", password=None).render_as_string(hide_password=False)
    return dsn, env


def _error_summary(stderr: str | None, stdout: str | None) -> str:
    """只挑错误行做提示；工具输出可能很长，整段贴出来反而看不清原因。"""
    lines = [line.strip() for line in (stderr or stdout or "").splitlines() if line.strip()]
    errors = [line for line in lines if "ERROR" in line or "错误" in line]
    picked = errors[:2] if errors else lines[-2:]
    return redact_text(" / ".join(picked)[:300]) if picked else "无输出"


def _run_tool(command: list[str], *, env: dict[str, str], failure: str) -> str:
    """执行外部工具；失败只回传脱敏后的错误摘要，不打印完整命令。"""
    try:
        completed = subprocess.run(
            command,
            env=env,
            capture_output=True,
            text=True,
            timeout=TOOL_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:  # PG_BIN 目录存在但缺少依赖库等
        raise SidecarError(f"{failure}：无法执行 {Path(command[0]).name}") from exc
    except subprocess.TimeoutExpired as exc:
        raise SidecarError(f"{failure}：超过 {TOOL_TIMEOUT_SECONDS} 秒未完成") from exc
    if completed.returncode != 0:
        hint = _error_summary(completed.stderr, completed.stdout)
        raise SidecarError(f"{failure}（退出码 {completed.returncode}）：{hint}")
    return completed.stdout


def _tool_version(tool: str, env: dict[str, str]) -> str:
    try:
        completed = subprocess.run(
            [tool, "--version"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return redact_text((completed.stdout or "").strip())


def _count_files(root: Path) -> int:
    if not root.is_dir():
        return 0
    return sum(1 for path in root.rglob("*") if path.is_file())


def _ensure_output_path(output: Path, *, overwrite: bool) -> Path:
    resolved = output.expanduser()
    if resolved.exists() and not overwrite:
        raise SidecarError(f"输出文件已存在：{resolved}（如需覆盖请加 --overwrite）")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def run_backup(
    settings: Settings,
    logger: EventLogger,
    output: str | Path,
    *,
    overwrite: bool = False,
) -> BackupResult:
    """把数据库与存储目录写成单个 tar 归档，并返回可展示的摘要。"""
    target = _ensure_output_path(Path(output), overwrite=overwrite)
    pg_dump = find_pg_tool("pg_dump")
    dsn, env = _pg_environment(settings.database_url)
    created_at = datetime.now(UTC)
    storage = Path(settings.storage_dir).expanduser()
    head = script_heads(settings)

    engine = connect(settings)
    try:
        state = migration_state(settings, engine)
        if not state["current"]:
            logger.warning(
                "backup_database_not_migrated", database=database_name(settings.database_url)
            )
    finally:
        engine.dispose()

    # 归档先写进同目录临时文件，成功后再改名，避免留下半个归档。
    with tempfile.TemporaryDirectory(prefix=".campusflow-backup-", dir=target.parent) as temp:
        staging = Path(temp)
        dump_path = staging / DATABASE_MEMBER
        _run_tool(
            [
                pg_dump,
                "--format=custom",
                "--no-owner",
                "--no-privileges",
                f"--file={dump_path}",
                dsn,
            ],
            env=env,
            failure="pg_dump 失败",
        )
        if not dump_path.is_file() or dump_path.stat().st_size == 0:
            raise SidecarError("pg_dump 没有生成数据库转储文件")

        storage_files = 0
        if storage.is_dir():
            shutil.copytree(storage, staging / STORAGE_PREFIX, symlinks=True)
            storage_files = _count_files(staging / STORAGE_PREFIX)
        else:
            (staging / STORAGE_PREFIX).mkdir()

        manifest = {
            "archive_format_version": ARCHIVE_FORMAT_VERSION,
            "created_at": created_at.isoformat(timespec="seconds"),
            "database_url": redacted_database_url(settings.database_url),
            "database": database_name(settings.database_url),
            "alembic_head": head,
            "alembic_current": state["current"],
            "storage_file_count": storage_files,
            "pg_dump_version": _tool_version(pg_dump, env),
        }
        (staging / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        partial = target.with_name(f".{target.name}.partial")
        try:
            with tarfile.open(partial, "w") as tar:
                tar.add(staging / MANIFEST_NAME, arcname=MANIFEST_NAME)
                tar.add(staging / DATABASE_MEMBER, arcname=DATABASE_MEMBER)
                tar.add(staging / STORAGE_PREFIX, arcname=STORAGE_PREFIX)
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)

    result = BackupResult(
        path=target,
        archive_bytes=target.stat().st_size,
        storage_files=storage_files,
        database=database_name(settings.database_url),
        alembic_head=head,
        alembic_current=state["current"],
    )
    logger.info("backup_completed", **result.as_dict())
    return result


def read_manifest(archive: Path) -> dict[str, Any]:
    """读取并校验归档 manifest；格式版本不支持时不猜测内容。"""
    if not archive.is_file():
        raise SidecarError(f"归档不存在：{archive}")
    try:
        with tarfile.open(archive) as tar:
            names = set(tar.getnames())
            if DATABASE_MEMBER not in names:
                raise SidecarError("归档缺少 database.dump，不是 CampusFlow 备份")
            try:
                member = tar.getmember(MANIFEST_NAME)
            except KeyError as exc:
                raise SidecarError("归档缺少 manifest.json，无法确认格式版本") from exc
            if member.size > MAX_MANIFEST_BYTES:
                raise SidecarError("归档 manifest.json 异常过大，拒绝读取")
            handle = tar.extractfile(member)
            if handle is None:
                raise SidecarError("归档 manifest.json 无法读取")
            raw = handle.read().decode("utf-8")
    except tarfile.TarError as exc:
        raise SidecarError("归档不是有效的 tar 文件") from exc
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SidecarError("归档 manifest.json 不是有效 JSON") from exc
    if not isinstance(manifest, dict):
        raise SidecarError("归档 manifest.json 结构无效")
    version = manifest.get("archive_format_version")
    if version != ARCHIVE_FORMAT_VERSION:
        raise SidecarError(
            f"不支持的归档格式版本：{version!r}（当前支持 {ARCHIVE_FORMAT_VERSION}）"
        )
    return manifest


def _maintenance_engine(database_url: str):
    """连到维护库 `postgres`：建库、改名与删库都要在目标库之外执行。"""
    url = make_url(database_url).set(drivername="postgresql+psycopg", database="postgres")
    return create_engine(
        url.render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
        connect_args={"connect_timeout": 3},
    )


def _scratch_name(database: str) -> str:
    suffix = f"_restore_{int(time.time())}"
    return f"{database[: 63 - len(suffix)]}{suffix}"


def _restore_into_scratch(
    settings: Settings,
    dump_path: Path,
    logger: EventLogger,
    pg_restore: str,
) -> bool:
    """恢复进临时库后改名；角色无建库权限或目标仍在连接时返回 False 走直接恢复。"""
    database = database_name(settings.database_url)
    if not _IDENTIFIER.fullmatch(database):
        logger.warning("restore_scratch_skipped", reason="database_name_not_simple")
        return False
    scratch = _scratch_name(database)
    dsn, env = _pg_environment(settings.database_url)
    scratch_dsn = make_url(dsn).set(database=scratch).render_as_string(hide_password=False)
    engine = _maintenance_engine(settings.database_url)
    created = False
    try:
        with engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{scratch}" TEMPLATE template0'))
        created = True
    except Exception:
        logger.warning("restore_scratch_unavailable", reason="create_database_rejected")
        return False
    finally:
        engine.dispose()

    try:
        _run_tool(
            [pg_restore, "--no-owner", "--no-privileges", "--dbname", scratch_dsn, str(dump_path)],
            env=env,
            failure="pg_restore 失败",
        )
        # 先把旧库改名留作回退，再把临时库改成目标名；任一步失败都恢复原名。
        retired = f"{database[: 63 - 15]}_old_{int(time.time())}"
        engine = _maintenance_engine(settings.database_url)
        try:
            with engine.connect() as connection:
                connection.execute(text(f'ALTER DATABASE "{database}" RENAME TO "{retired}"'))
                try:
                    connection.execute(text(f'ALTER DATABASE "{scratch}" RENAME TO "{database}"'))
                except Exception:
                    connection.execute(text(f'ALTER DATABASE "{retired}" RENAME TO "{database}"'))
                    raise
                try:
                    connection.execute(text(f'DROP DATABASE "{retired}" WITH (FORCE)'))
                except Exception:
                    # 旧库没删掉不影响新库可用，交给用户手工清理。
                    logger.warning("restore_retired_database_kept", database=retired)
        except Exception as exc:
            if isinstance(exc, SidecarError):
                raise
            raise SidecarError(
                "临时库恢复成功但改名为目标库失败：目标数据库可能仍有连接，请先停止应用后重试"
            ) from exc
        finally:
            engine.dispose()
        return True
    finally:
        if created:
            _drop_database_quietly(settings.database_url, scratch)


def _drop_database_quietly(database_url: str, name: str) -> None:
    engine = _maintenance_engine(database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    except Exception:
        pass
    finally:
        engine.dispose()


def _reject_unsafe_member(name: str) -> None:
    """拒绝绝对路径、盘符与 `..` 片段；其余字符（含中文文件名）允许。"""
    normalized = name.replace("\\", "/")
    parts = normalized.split("/")
    if normalized.startswith("/") or ".." in parts or ":" in parts[0]:
        raise SidecarError("归档包含不安全的路径，拒绝解压")


def _extract_storage(archive: Path, destination: Path) -> int:
    """把归档内 storage/ 解到 destination，只接受普通相对路径成员。"""
    with tarfile.open(archive) as tar:
        members = []
        for member in tar.getmembers():
            if member.name == STORAGE_PREFIX or member.name.startswith(f"{STORAGE_PREFIX}/"):
                _reject_unsafe_member(member.name)
                if member.issym() or member.islnk():
                    raise SidecarError("归档包含符号链接，拒绝解压")
                members.append(member)
        tar.extractall(destination, members=members, filter="data")
    root = destination / STORAGE_PREFIX
    return _count_files(root)


def _replace_storage(archive: Path, settings: Settings, logger: EventLogger) -> int:
    """覆盖式恢复存储：原目录先改名留档，失败也能找回旧文件。"""
    current = Path(settings.storage_dir).expanduser()
    staging = current.parent / f".{current.name}.restore-{int(time.time())}"
    staging.mkdir(parents=True, exist_ok=True)
    try:
        count = _extract_storage(archive, staging)
        restored = staging / STORAGE_PREFIX
        retired: Path | None = None
        if current.exists():
            retired = current.with_name(f"{current.name}.pre-restore-{int(time.time())}")
            current.rename(retired)
        restored.rename(current)
        if retired is not None:
            logger.info("storage_replaced", previous=str(retired))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return count


def _merge_storage(archive: Path, settings: Settings) -> int:
    """合并式恢复：只写入归档内文件，不删除现有文件。"""
    current = Path(settings.storage_dir).expanduser()
    current.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".restore-", dir=current.parent))
    try:
        count = _extract_storage(archive, staging)
        shutil.copytree(staging / STORAGE_PREFIX, current, dirs_exist_ok=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return count


def _extension_toc_entries(listing: str) -> dict[str, str]:
    """从 `pg_restore --list` 输出取扩展相关条目：编号 → 扩展名。

    扩展及其注释需要超级用户权限。目标库已经装好同一扩展时跳过这些条目，
    否则普通业务角色连 `DROP EXTENSION`／`CREATE EXTENSION` 都会失败。
    """
    entries: dict[str, str] = {}
    for line in listing.splitlines():
        if not line.strip() or line.startswith(";"):
            continue
        index, _, rest = line.partition("; ")
        tokens = rest.split()
        if len(tokens) < 4:
            continue
        if tokens[2] == "EXTENSION" and len(tokens) > 4:
            entries[index.strip()] = tokens[4]
        elif tokens[2] == "COMMENT" and "EXTENSION" in tokens:
            position = tokens.index("EXTENSION")
            if position + 1 < len(tokens):
                entries[index.strip()] = tokens[position + 1]
    return entries


def _filtered_toc_list(
    pg_restore: str, dump_path: Path, env: dict[str, str], existing_extensions: set[str]
) -> Path | None:
    """目标库已装好的扩展从恢复清单里去掉；没有要跳过的条目时返回 None。"""
    listing = _run_tool([pg_restore, "--list", str(dump_path)], env=env, failure="读取归档目录失败")
    entries = _extension_toc_entries(listing)
    skip = {index for index, name in entries.items() if name in existing_extensions}
    if not skip:
        return None
    kept = []
    for line in listing.splitlines(keepends=True):
        index = line.partition("; ")[0].strip()
        if line.strip() and not line.startswith(";") and index in skip:
            continue
        kept.append(line)
    handle, name = tempfile.mkstemp(prefix=".campusflow-toc-", suffix=".list")
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.writelines(kept)
    return Path(name)


def _existing_extensions(settings: Settings) -> set[str]:
    engine = connect(settings)
    try:
        with engine.connect() as connection:
            return {row[0] for row in connection.execute(text("SELECT extname FROM pg_extension"))}
    finally:
        engine.dispose()


def _restore_dump(
    pg_restore: str,
    dump_path: Path,
    dsn: str,
    env: dict[str, str],
    existing_extensions: set[str],
) -> None:
    """直接恢复：`--clean --if-exists` 覆盖同名对象；已装扩展从清单里跳过。"""
    command = [
        pg_restore,
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-privileges",
        "--dbname",
        dsn,
    ]
    toc_list = _filtered_toc_list(pg_restore, dump_path, env, existing_extensions)
    if toc_list is not None:
        command.extend(["--use-list", str(toc_list)])
    command.append(str(dump_path))
    try:
        _run_tool(command, env=env, failure="pg_restore 失败")
    except SidecarError as exc:
        if "vector" in str(exc):
            raise SidecarError(
                f"{exc}；目标库缺少 vector 扩展，请先用超级用户执行 CREATE EXTENSION vector"
            ) from None
        raise
    finally:
        if toc_list is not None:
            toc_list.unlink(missing_ok=True)


def run_restore(
    settings: Settings,
    logger: EventLogger,
    archive: str | Path,
    *,
    replace: bool = False,
    safety_backup: str | Path | None = None,
    strategy: str = "auto",
) -> RestoreResult:
    """从归档恢复数据库与存储；覆盖已有数据库必须显式 replace。"""
    archive_path = Path(archive).expanduser()
    manifest = read_manifest(archive_path)
    pg_restore = find_pg_tool("pg_restore")
    database = database_name(settings.database_url)
    archived_database = manifest.get("database")
    # 允许恢复到不同库名的目标（换机恢复），但要在日志里留痕，便于排查混用。
    if archived_database and archived_database != database:
        logger.warning(
            "restore_name_mismatch",
            archive_database=archived_database,
            target_database=database,
        )

    engine = connect(settings)
    try:
        non_empty = database_has_content(engine)
        current_heads = migration_state(settings, engine)["current"]
    finally:
        engine.dispose()
    if non_empty and not replace:
        raise SidecarError("目标数据库已有数据：恢复会覆盖现有内容，确认后请追加 --replace")

    backup_result: BackupResult | None = None
    if non_empty and replace:
        # 覆盖前先留一份安全备份；备份失败则不继续删除任何数据。
        default_name = f"campusflow-safety-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.tar"
        target = (
            Path(safety_backup).expanduser()
            if safety_backup
            else archive_path.parent / default_name
        )
        backup_result = run_backup(settings, logger, target, overwrite=True)
        logger.info("restore_safety_backup", path=str(backup_result.path))
    elif current_heads:
        logger.info("restore_over_existing_schema", current=current_heads)

    staging = Path(tempfile.mkdtemp(prefix=".campusflow-restore-"))
    try:
        with tarfile.open(archive_path) as tar:
            member = tar.getmember(DATABASE_MEMBER)
            handle = tar.extractfile(member)
            if handle is None:
                raise SidecarError("归档 database.dump 无法读取")
            dump_path = staging / DATABASE_MEMBER
            with dump_path.open("wb") as target_file:
                shutil.copyfileobj(handle, target_file)
        dsn, env = _pg_environment(settings.database_url)
        mode = "direct"
        if strategy == "auto" and non_empty:
            try:
                if _restore_into_scratch(settings, dump_path, logger, pg_restore):
                    mode = "scratch"
                else:
                    logger.warning("restore_scratch_fallback", mode="direct")
            except SidecarError as exc:
                # 临时库路径失败时原库未改动；退回直接恢复并保留失败原因。
                logger.warning("restore_scratch_failed", message=str(exc))
        if mode == "direct":
            _restore_dump(pg_restore, dump_path, dsn, env, _existing_extensions(settings))
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    if replace:
        storage_files = _replace_storage(archive_path, settings, logger)
    else:
        storage_files = _merge_storage(archive_path, settings)

    result = RestoreResult(
        archive=archive_path,
        database=database,
        mode=mode,
        manifest=manifest,
        safety_backup=backup_result.path if backup_result else None,
        storage_files=storage_files,
        storage_dir=str(Path(settings.storage_dir).expanduser()),
    )
    logger.info("restore_completed", **result.as_dict())
    return result
