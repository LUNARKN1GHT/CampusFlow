"""桌面模式首启初始化与恢复（P007）。

启动编排模块：与 `main` 一样装配基础设施适配器（本机 PostgreSQL、Alembic、
凭据存储），把首启用例集中在一处。

流程：校验桌面模式 → 在 `<data_dir>.init-<random>` 临时目录中 initdb、启动、
建库、启用 pgvector、执行迁移 → 写入口令与 state.json → 停止集群 → 原子改名
为 `<data_dir>`。任何一步失败都不留下可用外观的数据目录：日志移到
`<data_dir>.failed-<timestamp>/logs` 供诊断。

契约：

- `initialize` 结束时集群是停止的：原子改名要求先停止服务器。调用方随后调用
  `ensure_ready` 启动并取得连接信息。
- `ensure_ready` 的 `ready=True` 才表示数据库可用；`uninitialized`／
  `incompatible`／凭据丢失属于预期状态，返回报告而不抛错；启动或连接失败属于
  运行故障，抛 `BootstrapError` 并给出日志位置。
- `state.json` 只保存非敏感元数据；口令只出现在凭据存储与内存报告中，桌面模式
  不回退到 `student/campusflow-dev`。
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import tempfile
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from urllib.parse import quote

from campusflow.application.ports.secrets import Secret, SecretStore
from campusflow.core.config import Settings
from campusflow.infrastructure.db.migrations import upgrade_to_head
from campusflow.infrastructure.db.postgres_cluster import (
    LocalPostgresCluster,
    PgBinaries,
    PostgresError,
    find_free_port,
    socket_directory,
)
from campusflow.infrastructure.db.provisioning import (
    DatabaseTarget,
    ProvisioningError,
    create_database,
    ensure_vector_extension,
    read_schema_head,
)
from campusflow.infrastructure.secrets import SecretBackendError, create_secret_store

logger = logging.getLogger(__name__)

# state.json 格式版本：应用只接受同版本；更新版本视为不兼容，旧版本要求升级。
STATE_FORMAT_VERSION = 1
STATE_FILENAME = "state.json"
DB_HOST = "127.0.0.1"
DATABASE_NAME = "campusflow"
DATABASE_USER = "campusflow"
DEFAULT_LOCAL_USERNAME = "campusflow"
DB_PASSWORD_ACCOUNT = "db-password"
LOCAL_PASSWORD_ACCOUNT = "local-password"
SECRET_BYTES = 32
_LAYOUT_DIRECTORIES = ("materials", "logs", "backups", "run")

__all__ = [
    "BootstrapError",
    "BootstrapReport",
    "BootstrapState",
    "BootstrapStatus",
    "DataPaths",
    "PgConnection",
    "StateFile",
    "connection_env",
    "ensure_ready",
    "initialize",
    "inspect",
]


class BootstrapError(RuntimeError):
    """首启初始化／恢复无法继续；消息给用户看，不含任何口令。"""

    def __init__(self, message: str, *, log_dir: Path | None = None) -> None:
        super().__init__(message)
        self.log_dir = log_dir


class BootstrapStatus(StrEnum):
    UNINITIALIZED = "uninitialized"
    INITIALIZED = "initialized"
    PARTIALLY_INITIALIZED = "partially_initialized"
    INCOMPATIBLE = "incompatible"


@dataclass(frozen=True)
class DataPaths:
    """数据目录布局；全部为绝对路径。"""

    root: Path
    pgdata: Path
    materials: Path
    logs: Path
    backups: Path
    run: Path
    state_file: Path
    secrets_file: Path
    postgres_log: Path

    @classmethod
    def under(cls, root: Path) -> DataPaths:
        root = Path(root)
        return cls(
            root=root,
            pgdata=root / "pgdata",
            materials=root / "materials",
            logs=root / "logs",
            backups=root / "backups",
            run=root / "run",
            state_file=root / STATE_FILENAME,
            secrets_file=root / "secrets.json",
            postgres_log=root / "logs" / "postgres.log",
        )


@dataclass(frozen=True)
class StateFile:
    """state.json：只保存非敏感元数据，不含任何口令。"""

    format_version: int
    created_at: str
    pg_version: str
    port: int
    database: str
    username: str
    schema_head: str
    secret_backend: str

    REQUIRED_FIELDS = (
        "format_version",
        "created_at",
        "pg_version",
        "port",
        "database",
        "username",
        "schema_head",
        "secret_backend",
    )

    def to_dict(self) -> dict[str, object]:
        return {
            "format_version": self.format_version,
            "created_at": self.created_at,
            "pg_version": self.pg_version,
            "port": self.port,
            "database": self.database,
            "username": self.username,
            "schema_head": self.schema_head,
            "secret_backend": self.secret_backend,
        }

    @classmethod
    def parse(cls, raw: object) -> StateFile:
        if not isinstance(raw, dict):
            raise ValueError("state.json 顶层必须是对象")
        missing = [name for name in cls.REQUIRED_FIELDS if name not in raw]
        if missing:
            raise ValueError(f"state.json 缺少字段：{', '.join(missing)}")
        format_version = raw["format_version"]
        port = raw["port"]
        if not isinstance(format_version, int) or isinstance(format_version, bool):
            raise ValueError("format_version 必须是整数")
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError("port 必须是 1–65535 的整数")
        values: dict[str, str] = {}
        for name in ("created_at", "pg_version", "database", "username", "schema_head"):
            value = raw[name]
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} 必须是非空字符串")
            values[name] = value
        secret_backend = raw["secret_backend"]
        if not isinstance(secret_backend, str) or not secret_backend:
            raise ValueError("secret_backend 必须是非空字符串")
        return cls(
            format_version=format_version,
            created_at=values["created_at"],
            pg_version=values["pg_version"],
            port=port,
            database=values["database"],
            username=values["username"],
            schema_head=values["schema_head"],
            secret_backend=secret_backend,
        )


@dataclass(frozen=True)
class BootstrapState:
    """`inspect` 的结果：只描述现状，不启动、不写库、不伪造就绪。"""

    status: BootstrapStatus
    paths: DataPaths
    state: StateFile | None = None
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def initialized(self) -> bool:
        return self.status is BootstrapStatus.INITIALIZED


@dataclass(frozen=True)
class PgConnection:
    """连接信息；口令只在内存中，`database_url()` 是唯一取值入口。"""

    host: str
    port: int
    database: str
    username: str
    password: Secret = field(repr=False)

    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.username}:{quote(self.password.reveal(), safe='')}"
            f"@{self.host}:{self.port}/{self.database}"
        )

    def target(self) -> DatabaseTarget:
        return DatabaseTarget(
            host=self.host,
            port=self.port,
            database=self.database,
            username=self.username,
            password=self.password.reveal(),
        )


@dataclass(frozen=True)
class BootstrapReport:
    """初始化／就绪结果。`ready` 才表示数据库可用。"""

    status: BootstrapStatus
    paths: DataPaths
    ready: bool
    initialized: bool = False
    started_postgres: bool = False
    postgres_running: bool = False
    connection: PgConnection | None = None
    local_username: str | None = None
    local_password: Secret | None = field(default=None, repr=False)
    schema_head: str | None = None
    secret_backend: str | None = None
    warnings: tuple[str, ...] = ()
    messages: tuple[str, ...] = ()


def connection_env(report: BootstrapReport) -> dict[str, str]:
    """返回只供本进程使用的连接环境变量；不写文件、不进日志。"""
    if not report.ready or report.connection is None:
        raise BootstrapError("数据库尚未就绪，不能生成连接环境变量")
    values = {"CAMPUSFLOW_DATABASE_URL": report.connection.database_url()}
    if report.local_password is not None:
        values["CAMPUSFLOW_LOCAL_PASSWORD"] = report.local_password.reveal()
    return values


def inspect(settings: Settings, *, secret_store: SecretStore | None = None) -> BootstrapState:
    """检查数据目录现状；任何不确定情况都退到 partially_initialized。"""
    root = _require_desktop(settings)
    paths = DataPaths.under(root)
    if not root.exists():
        return BootstrapState(BootstrapStatus.UNINITIALIZED, paths, reasons=("数据目录不存在",))
    if not root.is_dir():
        return BootstrapState(BootstrapStatus.INCOMPATIBLE, paths, reasons=("数据目录不是目录",))
    if not paths.state_file.is_file():
        try:
            empty = not any(root.iterdir())
        except OSError:
            empty = False
        if empty:
            return BootstrapState(BootstrapStatus.UNINITIALIZED, paths, reasons=("数据目录为空",))
        return BootstrapState(
            BootstrapStatus.PARTIALLY_INITIALIZED, paths, reasons=("数据目录缺少 state.json",)
        )
    try:
        raw = json.loads(paths.state_file.read_text(encoding="utf-8"))
        state = StateFile.parse(raw)
    except (OSError, ValueError) as exc:
        return BootstrapState(
            BootstrapStatus.PARTIALLY_INITIALIZED,
            paths,
            reasons=(f"state.json 无法读取：{exc}",),
        )
    if state.format_version > STATE_FORMAT_VERSION:
        return BootstrapState(
            BootstrapStatus.INCOMPATIBLE,
            paths,
            state,
            reasons=(
                f"数据目录由更新版本创建（格式 {state.format_version} > {STATE_FORMAT_VERSION}）",
            ),
        )
    if state.format_version < STATE_FORMAT_VERSION:
        return BootstrapState(
            BootstrapStatus.INCOMPATIBLE,
            paths,
            state,
            reasons=("state.json 格式过旧，需要升级后再使用",),
        )
    if not (paths.pgdata / "PG_VERSION").is_file():
        return BootstrapState(
            BootstrapStatus.PARTIALLY_INITIALIZED,
            paths,
            state,
            reasons=("PostgreSQL 数据目录缺失或不完整",),
        )

    warnings: list[str] = []
    stores = _candidate_stores(settings, root, state.secret_backend, secret_store)
    db_password, failed_backends = _read_secret(stores, DB_PASSWORD_ACCOUNT)
    if failed_backends:
        return BootstrapState(
            BootstrapStatus.PARTIALLY_INITIALIZED,
            paths,
            state,
            reasons=(f"无法读取本机凭据存储：{', '.join(failed_backends)}",),
        )
    if db_password is None:
        return BootstrapState(
            BootstrapStatus.PARTIALLY_INITIALIZED,
            paths,
            state,
            reasons=("数据库口令不在本机凭据存储中（凭据丢失）",),
        )
    local_password, failed = _read_secret(stores, LOCAL_PASSWORD_ACCOUNT)
    if failed:
        return BootstrapState(
            BootstrapStatus.PARTIALLY_INITIALIZED,
            paths,
            state,
            reasons=(f"无法读取本机凭据存储：{', '.join(failed)}",),
        )
    if local_password is None:
        warnings.append("本地登录口令缺失，下次就绪检查会重新生成")
    return BootstrapState(
        BootstrapStatus.INITIALIZED,
        paths,
        state,
        warnings=tuple(warnings),
    )


def initialize(settings: Settings, *, secret_store: SecretStore | None = None) -> BootstrapReport:
    """首次初始化；已初始化时直接返回现状，不重复 initdb 或迁移。"""
    root = _require_desktop(settings)
    paths = DataPaths.under(root)
    current = inspect(settings, secret_store=secret_store)
    if current.status is BootstrapStatus.INITIALIZED:
        report = _report_from_state(
            current,
            ready=False,
            messages=("数据目录已初始化，本次未重复创建",),
        )
        return replace(report, postgres_running=_cluster_running(settings, current))
    if current.status is not BootstrapStatus.UNINITIALIZED:
        reasons = "；".join(current.reasons) or "未知原因"
        raise BootstrapError(f"数据目录已存在但不完整或不兼容，拒绝覆盖：{reasons}")

    try:
        binaries = PgBinaries.discover(settings.pg_bin)
    except PostgresError as exc:
        raise BootstrapError(str(exc)) from exc

    root.parent.mkdir(parents=True, exist_ok=True)
    if root.exists():
        try:
            root.rmdir()  # 只接受空目录；非空一律拒绝，避免误删已有数据
        except OSError as exc:
            raise BootstrapError("数据目录已存在且非空，拒绝覆盖") from exc

    work = Path(tempfile.mkdtemp(prefix=f"{root.name}.init-", dir=root.parent))
    work_paths = DataPaths.under(work)
    _create_layout(work_paths)

    db_password = secrets.token_urlsafe(SECRET_BYTES)
    local_password = secrets.token_urlsafe(SECRET_BYTES)
    local_username = _local_username(settings)
    port = find_free_port()
    socket_path, socket_outside = socket_directory(work, port)
    warnings: list[str] = []
    if socket_outside:
        warnings.append("数据目录路径过长，PostgreSQL 套接字改用临时目录（回环连接不受影响）")

    cluster = LocalPostgresCluster(
        work_paths.pgdata,
        binaries,
        port=port,
        socket_dir=socket_path,
        log_file=work_paths.postgres_log,
    )
    installed_store: SecretStore | None = None
    try:
        cluster.initdb(DATABASE_USER, db_password)
        cluster.start()
        admin = DatabaseTarget(
            host=DB_HOST,
            port=port,
            database="postgres",
            username=DATABASE_USER,
            password=db_password,
        )
        create_database(admin, DATABASE_NAME, owner=DATABASE_USER)
        ensure_vector_extension(admin.with_database(DATABASE_NAME))
        database_url = (
            f"postgresql+psycopg://{DATABASE_USER}:{quote(db_password, safe='')}"
            f"@{DB_HOST}:{port}/{DATABASE_NAME}"
        )
        expected_head = upgrade_to_head(database_url, settings.migrations_dir)
        actual_head = read_schema_head(admin.with_database(DATABASE_NAME))
        if actual_head != expected_head:
            raise BootstrapError(
                f"迁移后数据库迁移头不一致（数据库 {actual_head}，脚本 {expected_head}）"
            )
        installed_store, store_warnings = _install_secrets(
            settings,
            work,
            secret_store,
            {DB_PASSWORD_ACCOUNT: db_password, LOCAL_PASSWORD_ACCOUNT: local_password},
        )
        warnings.extend(store_warnings)
        state = StateFile(
            format_version=STATE_FORMAT_VERSION,
            created_at=datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            pg_version=binaries.version,
            port=port,
            database=DATABASE_NAME,
            username=DATABASE_USER,
            schema_head=actual_head,
            secret_backend=installed_store.backend,
        )
        _write_state(work_paths.state_file, state)
        cluster.stop()
    except BaseException as exc:
        log_dir = _preserve_failure(work, root, cluster, socket_path if socket_outside else None)
        _discard_secrets(installed_store)
        if isinstance(exc, BootstrapError):
            message = str(exc)
        elif isinstance(exc, (PostgresError, ProvisioningError, SecretBackendError)):
            message = str(exc)
        else:
            message = f"初始化过程出现未预期错误：{exc.__class__.__name__}"
        hint = f"；诊断日志保留在 {log_dir}" if log_dir is not None else ""
        raise BootstrapError(f"初始化失败：{message}{hint}", log_dir=log_dir) from exc

    try:
        work.rename(root)
    except OSError as exc:
        log_dir = _preserve_failure(work, root, cluster, socket_path if socket_outside else None)
        _discard_secrets(installed_store)
        hint = f"；诊断日志保留在 {log_dir}" if log_dir is not None else ""
        raise BootstrapError(f"无法把临时目录改名为数据目录{hint}", log_dir=log_dir) from exc

    logger.info("桌面数据目录初始化完成：端口 %s，迁移头 %s", port, actual_head)
    return BootstrapReport(
        status=BootstrapStatus.INITIALIZED,
        paths=paths,
        ready=False,
        initialized=True,
        postgres_running=False,
        connection=PgConnection(
            host=DB_HOST,
            port=port,
            database=DATABASE_NAME,
            username=DATABASE_USER,
            password=Secret(db_password),
        ),
        local_username=local_username,
        local_password=Secret(local_password),
        schema_head=actual_head,
        secret_backend=installed_store.backend,
        warnings=tuple(warnings),
        messages=("初始化完成，数据库已停止；调用 ensure_ready 启动并取得连接信息",),
    )


def ensure_ready(settings: Settings, *, secret_store: SecretStore | None = None) -> BootstrapReport:
    """启动已初始化的集群并校验凭据；凭据丢失时明确提示而不是回落默认口令。"""
    root = _require_desktop(settings)
    paths = DataPaths.under(root)
    current = inspect(settings, secret_store=secret_store)
    if current.status is BootstrapStatus.UNINITIALIZED:
        return BootstrapReport(
            status=current.status,
            paths=paths,
            ready=False,
            messages=("数据目录尚未初始化，请先执行初始化",),
        )
    if current.status is BootstrapStatus.INCOMPATIBLE:
        return BootstrapReport(
            status=current.status,
            paths=paths,
            ready=False,
            messages=("数据目录格式不兼容，不能继续使用", *current.reasons),
        )
    if current.status is BootstrapStatus.PARTIALLY_INITIALIZED:
        return BootstrapReport(
            status=current.status,
            paths=paths,
            ready=False,
            messages=(
                "本地数据不完整或凭据丢失，无法自动恢复；桌面模式不会使用默认口令，"
                "请重新登录，或从备份恢复数据目录",
                *current.reasons,
            ),
        )

    state = current.state
    if state is None:  # initialized 状态必然带状态文件；这里只是防御性检查
        raise BootstrapError("数据状态缺失，无法启动数据库")
    warnings = list(current.warnings)
    stores = _candidate_stores(settings, root, state.secret_backend, secret_store)
    db_password, _ = _read_secret(stores, DB_PASSWORD_ACCOUNT)
    if db_password is None:
        return BootstrapReport(
            status=BootstrapStatus.PARTIALLY_INITIALIZED,
            paths=paths,
            ready=False,
            messages=(
                "数据库口令丢失，桌面模式不会使用默认口令；请从备份恢复数据目录或重新初始化",
            ),
        )
    local_password, _ = _read_secret(stores, LOCAL_PASSWORD_ACCOUNT)
    if local_password is None:
        local_password = secrets.token_urlsafe(SECRET_BYTES)
        _, store_warnings = _install_secrets(
            settings,
            root,
            secret_store,
            {LOCAL_PASSWORD_ACCOUNT: local_password},
            preferred=state.secret_backend,
            allow_fallback=True,
        )
        warnings.extend(store_warnings)
        warnings.append("本地登录口令缺失，已重新生成并写入凭据存储")
        logger.warning("本地登录口令缺失，已重新生成")

    socket_path, socket_outside = socket_directory(root, state.port)
    if socket_outside:
        warnings.append("数据目录路径过长，PostgreSQL 套接字改用临时目录（回环连接不受影响）")
    cluster = LocalPostgresCluster(
        paths.pgdata,
        _discover_binaries(settings),
        port=state.port,
        socket_dir=socket_path,
        log_file=paths.postgres_log,
    )
    started = False
    try:
        if not cluster.is_running():
            cluster.start()
            started = True
        cluster.wait_ready()
    except PostgresError as exc:
        if started:
            try:
                cluster.stop()
            except PostgresError:
                logger.warning("启动失败后停止 PostgreSQL 未成功，请检查残留进程")
        raise BootstrapError(
            f"{exc}；日志位于 {paths.postgres_log}", log_dir=paths.logs
        ) from exc

    connection = PgConnection(
        host=DB_HOST,
        port=state.port,
        database=state.database,
        username=state.username,
        password=Secret(db_password),
    )
    try:
        head = read_schema_head(connection.target())
    except ProvisioningError as exc:
        raise BootstrapError(
            f"数据库已启动但无法连接：{exc}；日志位于 {paths.postgres_log}", log_dir=paths.logs
        ) from exc
    if head != state.schema_head:
        warnings.append(
            f"数据库迁移头与记录不一致（当前 {head or '未迁移'}，记录 {state.schema_head}），"
            "请执行迁移后再使用"
        )
        logger.warning("数据库迁移头与 state.json 不一致")

    return BootstrapReport(
        status=BootstrapStatus.INITIALIZED,
        paths=paths,
        ready=True,
        initialized=False,
        started_postgres=started,
        postgres_running=True,
        connection=connection,
        local_username=_local_username(settings),
        local_password=Secret(local_password),
        schema_head=head or state.schema_head,
        secret_backend=state.secret_backend,
        warnings=tuple(warnings),
        messages=("数据库已就绪",),
    )


def _require_desktop(settings: Settings) -> Path:
    if settings.deployment_mode != "desktop":
        raise BootstrapError("首启初始化只用于桌面模式（CAMPUSFLOW_DEPLOYMENT_MODE=desktop）")
    if not settings.data_dir:
        raise BootstrapError("桌面模式必须通过 CAMPUSFLOW_DATA_DIR 指定数据目录")
    root = Path(settings.data_dir).expanduser()
    if not root.is_absolute():
        raise BootstrapError("桌面模式的 CAMPUSFLOW_DATA_DIR 必须是绝对路径")
    if root.parent == root:
        raise BootstrapError("数据目录不能是文件系统根目录")
    return root


def _local_username(settings: Settings) -> str:
    # 桌面模式不使用 `student` 默认值；用户显式设置时尊重其选择。
    if "local_username" in settings.model_fields_set:
        return settings.local_username
    return DEFAULT_LOCAL_USERNAME


def _discover_binaries(settings: Settings) -> PgBinaries:
    try:
        return PgBinaries.discover(settings.pg_bin)
    except PostgresError as exc:
        raise BootstrapError(str(exc)) from exc


def _cluster_running(settings: Settings, current: BootstrapState) -> bool:
    """只读检查集群进程是否存在；找不到工具时如实返回 False。"""
    if current.state is None:
        return False
    try:
        binaries = PgBinaries.discover(settings.pg_bin)
    except PostgresError:
        return False
    # 只查进程状态，不创建套接字目录或临时文件。
    cluster = LocalPostgresCluster(
        current.paths.pgdata,
        binaries,
        port=current.state.port,
        socket_dir=current.paths.run,
        log_file=current.paths.postgres_log,
    )
    return cluster.is_running()


def _create_layout(paths: DataPaths) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    os.chmod(paths.root, 0o700)
    for name in _LAYOUT_DIRECTORIES:
        directory = paths.root / name
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)


def _write_state(path: Path, state: StateFile) -> None:
    handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".state-", suffix=".tmp")
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(state.to_dict(), stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def _candidate_stores(
    settings: Settings,
    root: Path,
    backend: str,
    injected: SecretStore | None,
) -> list[SecretStore]:
    """按「注入 → 状态文件记录 → 文件兜底」顺序尝试，重复后端只保留一个。"""
    stores: list[SecretStore] = []
    if injected is not None:
        stores.append(injected)
    for name in (backend, "file"):
        if any(store.backend == name for store in stores):
            continue
        try:
            stores.append(create_secret_store(name, root))
        except SecretBackendError:
            continue
    return stores


def _read_secret(stores: list[SecretStore], account: str) -> tuple[str | None, list[str]]:
    """返回（口令，读取失败的后端名）；失败与缺失分开报告，不把错误当缺失。"""
    failed: list[str] = []
    for store in stores:
        try:
            value = store.get(account)
        except Exception:  # 后端故障只影响本后端，继续尝试兜底实现
            failed.append(store.backend)
            continue
        if value:
            return value, failed
    return None, failed


def _install_secrets(
    settings: Settings,
    root: Path,
    injected: SecretStore | None,
    values: dict[str, str],
    *,
    preferred: str | None = None,
    allow_fallback: bool | None = None,
) -> tuple[SecretStore, list[str]]:
    """写入并读回校验；密钥链不可用时可降级到 0600 文件并给出警告。

    `preferred` 用于已有数据目录时沿用 state.json 记录的后端，避免读写落到不同
    存储；`allow_fallback` 决定不可用时是否降级（初始化的 auto 模式与恢复总是降级）。
    """
    backend = preferred or settings.secret_backend
    fallback_allowed = allow_fallback if allow_fallback is not None else backend == "auto"
    warnings: list[str] = []
    if injected is not None:
        _write_and_verify(injected, values)
        return injected, warnings
    if backend == "file":
        store = create_secret_store("file", root)
        _write_and_verify(store, values)
        return store, warnings
    backend_store: SecretStore | None = None
    try:
        backend_store = create_secret_store(backend, root)
        _write_and_verify(backend_store, values)
        return backend_store, warnings
    except Exception as exc:
        # 只清理本次写入的条目；初始化失败不会留下任何可用凭据。
        _discard_secrets(backend_store)
        if not fallback_allowed:
            raise BootstrapError("所选凭据后端不可用，未写入任何数据") from exc
        logger.warning("系统密钥链不可用，回退到 0600 本地凭据文件")
        warnings.append(
            f"系统密钥链不可用（{exc.__class__.__name__}），口令改为保存在数据目录的 0600 文件中"
        )
        store = create_secret_store("file", root)
        _write_and_verify(store, values)
        return store, warnings


def _write_and_verify(store: SecretStore, values: dict[str, str]) -> None:
    for account, value in values.items():
        store.set(account, value)
    for account, value in values.items():
        if store.get(account) != value:
            raise BootstrapError("凭据写入后无法读回，已中止初始化")


def _discard_secrets(store: SecretStore | None) -> None:
    if store is None:
        return
    for account in (DB_PASSWORD_ACCOUNT, LOCAL_PASSWORD_ACCOUNT):
        try:
            store.delete(account)
        except Exception:  # 清理失败不影响错误上报
            logger.warning("清理凭据条目失败：%s", account)


def _preserve_failure(
    work: Path,
    root: Path,
    cluster: LocalPostgresCluster,
    fallback_socket: Path | None,
) -> Path | None:
    """停止集群、把日志移到 `<data_dir>.failed-<timestamp>`，不留下可用外观的目录。"""
    try:
        cluster.stop()
    except Exception:
        logger.warning("初始化失败后停止 PostgreSQL 未成功，请检查残留进程")
    log_dir: Path | None = None
    try:
        failed_root = _failed_directory(root)
        failed_root.mkdir(parents=True)
        if (work / "logs").is_dir():
            shutil.copytree(work / "logs", failed_root / "logs")
            log_dir = failed_root / "logs"
    except OSError:
        logger.exception("无法保留初始化失败日志")
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if fallback_socket is not None:
            shutil.rmtree(fallback_socket, ignore_errors=True)
    return log_dir


def _failed_directory(root: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    candidate = Path(f"{root}.failed-{stamp}")
    counter = 1
    while candidate.exists():
        counter += 1
        candidate = Path(f"{root}.failed-{stamp}-{counter}")
    return candidate


def _report_from_state(
    current: BootstrapState,
    *,
    ready: bool,
    messages: tuple[str, ...],
) -> BootstrapReport:
    state = current.state
    return BootstrapReport(
        status=current.status,
        paths=current.paths,
        ready=ready,
        schema_head=state.schema_head if state is not None else None,
        secret_backend=state.secret_backend if state is not None else None,
        warnings=current.warnings,
        messages=messages,
    )
