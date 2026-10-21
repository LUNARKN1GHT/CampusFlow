"""PostgreSQL 升级前备份：pg_dump 自定义归档 + 资料目录快照 + manifest。

口令只通过环境变量传给 pg_dump／pg_restore，不出现在命令行、manifest
或日志中。恢复前先校验全部 SHA-256，校验失败绝不清空当前数据库。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import make_url

from campusflow.application.upgrade import (
    BackupError,
    BackupInfo,
    redact_database_url,
    redact_text,
)
from campusflow.core.config import Settings
from campusflow.infrastructure.db.engine import create_db_engine
from campusflow.infrastructure.db.schema import database_revision

logger = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"
DUMP_NAME = "database.dump"
MATERIALS_NAME = "materials"
BACKUP_PREFIX = "pre-upgrade-"

_RESET_SCHEMA_SQL = """
DO $$
DECLARE item record;
BEGIN
  FOR item IN
    SELECT c.relname, c.relkind
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = current_schema()
      AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend d WHERE d.objid = c.oid AND d.deptype = 'e'
      )
  LOOP
    EXECUTE format(
      'DROP %s IF EXISTS %I.%I CASCADE',
      CASE item.relkind
        WHEN 'v' THEN 'VIEW'
        WHEN 'm' THEN 'MATERIALIZED VIEW'
        WHEN 'S' THEN 'SEQUENCE'
        WHEN 'f' THEN 'FOREIGN TABLE'
        ELSE 'TABLE'
      END,
      current_schema(), item.relname
    );
  END LOOP;

  FOR item IN
    SELECT p.oid::regprocedure AS signature
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = current_schema()
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend d WHERE d.objid = p.oid AND d.deptype = 'e'
      )
  LOOP
    EXECUTE 'DROP ROUTINE IF EXISTS ' || item.signature || ' CASCADE';
  END LOOP;

  FOR item IN
    SELECT t.typname
    FROM pg_type t
    JOIN pg_namespace n ON n.oid = t.typnamespace
    WHERE n.nspname = current_schema()
      AND t.typtype IN ('e', 'c', 'd')
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend d WHERE d.objid = t.oid AND d.deptype = 'e'
      )
  LOOP
    EXECUTE format('DROP TYPE IF EXISTS %I.%I CASCADE', current_schema(), item.typname);
  END LOOP;
END $$;
"""


def backup_root(settings: Settings) -> Path:
    """备份根目录：显式 data_dir 优先，否则取资料目录的父目录。"""
    if settings.data_dir:
        base = Path(settings.data_dir).expanduser()
    else:
        base = Path(settings.storage_dir).expanduser().resolve().parent
    return (base / "backups").resolve()


def locate_pg_tool(name: str, pg_bin: str | None) -> str:
    """先查 CAMPUSFLOW_PG_BIN 目录，再查 PATH；缺失时给出可操作的中文错误。"""
    executable = f"{name}.exe" if os.name == "nt" else name
    attempted: list[str] = []
    if pg_bin:
        candidate = Path(pg_bin).expanduser() / executable
        attempted.append(str(candidate))
        if candidate.is_file():
            return str(candidate)
    found = shutil.which(executable)
    if found:
        return found
    attempted.append(executable)
    raise BackupError(
        f"未找到 {executable}。请安装 PostgreSQL 客户端，或通过 "
        f"CAMPUSFLOW_PG_BIN 指定其所在目录（已检查：{'、'.join(attempted)}）。"
    )


def _database_url_parts(settings: Settings) -> tuple[str, dict[str, str]]:
    """返回数据库名与子进程环境；口令只进入环境变量。"""
    url = make_url(settings.database_url)
    database = url.database
    if not database:
        raise BackupError("数据库连接缺少数据库名，无法创建升级前备份。")
    environment = os.environ.copy()
    values = {
        "PGHOST": url.host,
        "PGPORT": str(url.port) if url.port is not None else None,
        "PGUSER": url.username,
        "PGPASSWORD": url.password,
        "PGDATABASE": database,
        "PGSSLMODE": url.query.get("sslmode"),
    }
    for key, value in values.items():
        if value is None:
            environment.pop(key, None)
        else:
            environment[key] = value
    return database, environment


def _run_tool(command: list[str], environment: dict[str, str], settings: Settings) -> None:
    try:
        completed = subprocess.run(
            command,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise BackupError(
            f"无法执行 {Path(command[0]).name}：{redact_text(str(exc), settings.database_url)}"
        ) from exc
    if completed.returncode != 0:
        detail = redact_text(completed.stderr.strip(), settings.database_url)
        detail = detail[:800] if detail else "未返回错误说明"
        raise BackupError(f"{Path(command[0]).name} 执行失败：{detail}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _unique_directory(root: Path, name: str) -> Path:
    candidate = root / name
    suffix = 2
    while candidate.exists():
        candidate = root / f"{name}-{suffix}"
        suffix += 1
    return candidate


def _snapshot_storage(storage_root: Path, target: Path) -> dict[str, Any]:
    """复制资料目录并逐文件计算校验和；不跟随符号链接。"""
    files: list[dict[str, Any]] = []
    skipped_symlinks = 0
    if not storage_root.exists():
        return {
            "directory": MATERIALS_NAME,
            "file_count": 0,
            "total_bytes": 0,
            "skipped_symlinks": 0,
            "files": files,
        }
    target.mkdir(parents=True, exist_ok=True)
    for source in sorted(storage_root.rglob("*")):
        if source.is_symlink():
            skipped_symlinks += 1
            continue
        if not source.is_file():
            continue
        relative = source.relative_to(storage_root).as_posix()
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        with source.open("rb") as reader, destination.open("wb") as writer:
            while chunk := reader.read(1024 * 1024):
                writer.write(chunk)
                digest.update(chunk)
        files.append(
            {
                "path": relative,
                "size_bytes": destination.stat().st_size,
                "sha256": digest.hexdigest(),
            }
        )
    return {
        "directory": MATERIALS_NAME,
        "file_count": len(files),
        "total_bytes": sum(item["size_bytes"] for item in files),
        "skipped_symlinks": skipped_symlinks,
        "files": files,
    }


def create_pre_upgrade_backup(
    settings: Settings,
    *,
    db_revision: str | None,
    app_head: str,
    now: datetime | None = None,
) -> BackupInfo:
    """在 ``<data_dir>/backups/pre-upgrade-<UTC>/`` 保存数据库与资料快照。

    任何一步失败都会删除未完成的备份目录再抛错，不留下看似可用的半成品。
    """
    root = backup_root(settings)
    root.mkdir(parents=True, exist_ok=True)
    created_at = (now or datetime.now(UTC)).astimezone(UTC)
    backup_dir = _unique_directory(root, f"{BACKUP_PREFIX}{created_at:%Y%m%dT%H%M%SZ}")
    backup_dir.mkdir(parents=True)
    try:
        dump_path = backup_dir / DUMP_NAME
        database, environment = _database_url_parts(settings)
        pg_dump = locate_pg_tool("pg_dump", settings.pg_bin)
        _run_tool(
            [
                pg_dump,
                "--format=custom",
                "--no-owner",
                "--no-privileges",
                "--file",
                str(dump_path),
                "--dbname",
                database,
            ],
            environment,
            settings,
        )
        materials = _snapshot_storage(Path(settings.storage_dir), backup_dir / MATERIALS_NAME)
        manifest: dict[str, Any] = {
            "format_version": 1,
            "kind": "pre-upgrade",
            "created_at": created_at.isoformat(),
            "app_schema_head": app_head,
            "database_revision": db_revision,
            "database": {
                "name": database,
                "dump": DUMP_NAME,
                "size_bytes": dump_path.stat().st_size,
                "sha256": _sha256(dump_path),
            },
            "materials": materials,
            "database_url": redact_database_url(settings.database_url),
        }
        (backup_dir / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("backup_created: %s", backup_dir.name)
        return BackupInfo(path=backup_dir, manifest=manifest)
    except Exception:
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise


def _load_manifest(backup: BackupInfo) -> dict[str, Any]:
    manifest_path = backup.path / MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError(f"升级前备份缺少可读的 {MANIFEST_NAME}：{backup.path}") from exc
    if not isinstance(manifest, dict):
        raise BackupError(f"升级前备份的 {MANIFEST_NAME} 结构无效：{backup.path}")
    return manifest


def _verify_backup(backup: BackupInfo, manifest: dict[str, Any]) -> None:
    """恢复前校验数据库归档与每个资料文件，失败时保持当前数据库不变。"""
    database = manifest.get("database") or {}
    dump_path = backup.path / str(database.get("dump") or DUMP_NAME)
    if not dump_path.is_file():
        raise BackupError(f"升级前备份缺少数据库归档：{dump_path}")
    expected_dump = str(database.get("sha256") or "")
    if not expected_dump or _sha256(dump_path) != expected_dump:
        raise BackupError(f"升级前备份的数据库归档校验失败：{dump_path}")
    materials = manifest.get("materials") or {}
    files = materials.get("files") or []
    for item in files:
        relative = str(item.get("path") or "")
        if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise BackupError(f"升级前备份包含非法资料路径：{relative!r}")
        candidate = backup.path / MATERIALS_NAME / relative
        if not candidate.is_file():
            raise BackupError(f"升级前备份缺少资料文件：{relative}")
        if _sha256(candidate) != str(item.get("sha256") or ""):
            raise BackupError(f"升级前备份的资料文件校验失败：{relative}")


def _current_schema(settings: Settings) -> str:
    engine = create_db_engine(settings)
    try:
        with engine.connect() as connection:
            schema = connection.execute(text("SELECT current_schema()")).scalar()
    finally:
        engine.dispose()
    return str(schema or "public")


def _restore_environment(settings: Settings, schema: str) -> tuple[str, dict[str, str]]:
    database, environment = _database_url_parts(settings)
    existing = environment.get("PGOPTIONS", "")
    environment["PGOPTIONS"] = f"{existing} -c search_path={schema},public".strip()
    return database, environment


def _filtered_toc(dump_path: Path, settings: Settings) -> Path:
    """生成恢复清单：跳过扩展及其注释、schema 创建，避免无权限改扩展。"""
    pg_restore = locate_pg_tool("pg_restore", settings.pg_bin)
    database, environment = _database_url_parts(settings)
    command = [pg_restore, "--list", str(dump_path)]
    try:
        completed = subprocess.run(
            command, env=environment, capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise BackupError(f"无法执行 pg_restore：{exc}") from exc
    if completed.returncode != 0:
        detail = redact_text(completed.stderr.strip(), settings.database_url)
        raise BackupError(f"读取数据库归档清单失败：{detail}")
    kept = []
    for line in completed.stdout.splitlines():
        if " EXTENSION " in line or "COMMENT - EXTENSION" in line:
            continue
        if " SCHEMA - " in line or "COMMENT - SCHEMA" in line:
            continue
        kept.append(line)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", suffix=".list", delete=False, prefix="campusflow-restore-"
    )
    with handle:
        handle.write("\n".join(kept) + "\n")
    return Path(handle.name)


def restore_pre_upgrade_backup(settings: Settings, backup: BackupInfo) -> None:
    """校验备份、清空当前 schema 中的应用对象，再恢复数据库与资料。

    只清理当前 schema 中非扩展拥有的对象，因此不会尝试删除 vector 扩展。
    恢复后重新读取 revision，必须与 manifest 记录一致，否则抛错并保留备份。
    """
    manifest = _load_manifest(backup)
    _verify_backup(backup, manifest)
    dump_path = backup.path / str((manifest.get("database") or {}).get("dump") or DUMP_NAME)
    schema = _current_schema(settings)
    engine = create_db_engine(settings)
    try:
        with engine.begin() as connection:
            connection.execute(text(_RESET_SCHEMA_SQL))
    finally:
        engine.dispose()

    toc_path: Path | None = None
    try:
        toc_path = _filtered_toc(dump_path, settings)
        pg_restore = locate_pg_tool("pg_restore", settings.pg_bin)
        database, environment = _restore_environment(settings, schema)
        _run_tool(
            [
                pg_restore,
                "--no-owner",
                "--no-privileges",
                "--exit-on-error",
                "--use-list",
                str(toc_path),
                "--dbname",
                database,
                str(dump_path),
            ],
            environment,
            settings,
        )
    finally:
        if toc_path is not None:
            toc_path.unlink(missing_ok=True)

    expected = manifest.get("database_revision")
    engine = create_db_engine(settings)
    try:
        restored = database_revision(engine)
    finally:
        engine.dispose()
    if restored != expected and str(restored) != str(expected):
        raise BackupError(
            f"备份恢复后数据库版本为 {restored or '空'}，与备份记录的 {expected} 不一致。"
        )

    materials = manifest.get("materials") or {}
    storage_root = Path(settings.storage_dir)
    for item in materials.get("files") or []:
        relative = str(item.get("path") or "")
        if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise BackupError(f"升级前备份包含非法资料路径：{relative!r}")
        source = backup.path / MATERIALS_NAME / relative
        destination = storage_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if _sha256(destination) != str(item.get("sha256") or ""):
            raise BackupError(f"资料恢复后校验失败：{relative}")
    logger.info("backup_restored: %s", backup.path.name)


def prune_pre_upgrade_backups(
    settings: Settings, *, keep: int, current: Path | None
) -> tuple[str, ...]:
    """只保留最新的 ``keep`` 份升级前备份；恢复所用的当前备份永不删除。"""
    root = backup_root(settings)
    if not root.is_dir():
        return ()
    candidates = sorted(
        (
            path
            for path in root.iterdir()
            if path.is_dir()
            and path.name.startswith(BACKUP_PREFIX)
            and (path / MANIFEST_NAME).is_file()
        ),
        key=lambda path: path.name,
        reverse=True,
    )
    kept = set(candidates[: max(keep, 1)])
    if current is not None:
        kept.add(current)
    pruned: list[str] = []
    for candidate in candidates:
        if candidate in kept:
            continue
        try:
            shutil.rmtree(candidate)
            pruned.append(candidate.name)
        except OSError:
            logger.warning("backup_prune_failed: %s", candidate.name)
    return tuple(pruned)
