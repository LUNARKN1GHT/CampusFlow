"""`bootstrap`：桌面模式首启初始化与就绪（P007 的 CLI 入口）。

只做装配与输出：initdb、建库、迁移与凭据由 `application.bootstrap` 负责，
这里不复制其中任何步骤。约定：

- 仅桌面模式可用；服务端请继续使用 `migrate`。
- `--check` 只读：只调用 `inspect` 与 `pg_ctl status`，不创建目录、不启动集群。
- 普通运行：未初始化时先 `initialize`（原子临时目录）再 `ensure_ready`，
  已初始化时只 `ensure_ready`（幂等，同一数据目录返回同一连接）。
- 返回的 JSON 是唯一允许出现本地登录口令的位置（Electron 自动登录需要）；
  stderr 事件、`--log-file` 与 `state.json` 都不包含口令。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from campusflow.application.bootstrap import (
    BootstrapError,
    BootstrapReport,
    BootstrapState,
    BootstrapStatus,
    ensure_ready,
    initialize,
    inspect,
)
from campusflow.core.config import Settings
from campusflow.infrastructure.db.postgres_cluster import (
    LocalPostgresCluster,
    PgBinaries,
    PostgresError,
)
from campusflow.sidecar.errors import SidecarError, UsageError
from campusflow.sidecar.logs import EventLogger, scrub_text


def require_desktop_mode(settings: Settings) -> None:
    """bootstrap 只用于桌面模式；服务端误用按参数错误处理（退出码 2）。"""
    if settings.deployment_mode != "desktop":
        raise UsageError(
            "bootstrap 只适用于桌面模式（CAMPUSFLOW_DEPLOYMENT_MODE=desktop）；"
            "服务端请使用 migrate 命令"
        )


def cluster_running(settings: Settings, state: BootstrapState) -> bool:
    """只读检查集群进程是否存在；与 P007 的就绪检查一致，但不启动任何进程。"""
    if state.state is None:
        return False
    try:
        binaries = PgBinaries.discover(settings.pg_bin)
    except PostgresError:
        return False
    cluster = LocalPostgresCluster(
        state.paths.pgdata,
        binaries,
        port=state.state.port,
        socket_dir=state.paths.run,
        log_file=state.paths.postgres_log,
    )
    return cluster.is_running()


def _base_payload(
    *,
    ready: bool,
    status: BootstrapStatus,
    initialized: bool,
    data_dir: Path,
    schema_head: str | None,
    secret_backend: str | None,
    warnings: tuple[str, ...] = (),
    messages: tuple[str, ...] = (),
    started_postgres: bool = False,
    postgres_running: bool = False,
    database_url: str | None = None,
    local_username: str | None = None,
    local_password: str | None = None,
) -> dict[str, Any]:
    return {
        "ready": ready,
        "status": str(status),
        "initialized": initialized,
        "data_dir": str(data_dir),
        "database_url": database_url,
        "local_username": local_username,
        "local_password": local_password,
        "schema_head": schema_head,
        "secret_backend": secret_backend,
        "warnings": list(warnings),
        "messages": list(messages),
        "started_postgres": started_postgres,
        "postgres_running": postgres_running,
    }


def _report_payload(report: BootstrapReport, *, initialized_now: bool) -> dict[str, Any]:
    state = report.connection
    return _base_payload(
        ready=report.ready,
        status=report.status,
        initialized=initialized_now,
        data_dir=report.paths.root,
        database_url=state.database_url() if state is not None else None,
        local_username=report.local_username,
        local_password=(
            report.local_password.reveal() if report.local_password is not None else None
        ),
        schema_head=report.schema_head,
        secret_backend=report.secret_backend,
        warnings=report.warnings,
        messages=report.messages,
        started_postgres=report.started_postgres,
        postgres_running=report.postgres_running,
    )


def _check_payload(settings: Settings, logger: EventLogger) -> dict[str, Any]:
    """只读检查：不初始化、不迁移、不启动；`ready` 不冒充“数据库已验证可用”。"""
    state = inspect(settings)
    running = cluster_running(settings, state)
    payload = _base_payload(
        ready=False,
        status=state.status,
        initialized=state.initialized,
        data_dir=state.paths.root,
        schema_head=state.state.schema_head if state.state is not None else None,
        secret_backend=state.state.secret_backend if state.state is not None else None,
        warnings=state.warnings,
        messages=state.reasons,
        postgres_running=running,
    )
    logger.info(
        "bootstrap_checked",
        status=payload["status"],
        initialized=payload["initialized"],
        postgres_running=running,
    )
    return payload


def _remember(report: BootstrapReport, secrets: list[str | None]) -> None:
    if report.connection is not None:
        secrets.append(report.connection.password.reveal())
    if report.local_password is not None:
        secrets.append(report.local_password.reveal())


def _failure_message(exc: BootstrapError, secrets: list[str | None]) -> str:
    """错误文案脱敏后附加诊断目录；日志目录本身不含口令。"""
    message = scrub_text(str(exc), secrets)
    if exc.log_dir is not None and "诊断日志" not in message:
        message = f"{message}（诊断日志目录：{exc.log_dir}）"
    return message


def run_bootstrap(
    settings: Settings, logger: EventLogger, *, check: bool = False
) -> dict[str, Any]:
    """执行首启或就绪检查，返回可直接序列化的报告。

    失败抛 `SidecarError`（退出码 1），消息中给出口令外可展示的诊断信息。
    """
    require_desktop_mode(settings)
    if check:
        return _check_payload(settings, logger)

    secrets: list[str | None] = []
    initialized_now = False
    try:
        current = inspect(settings)
        created: BootstrapReport | None = None
        if current.status is BootstrapStatus.UNINITIALIZED:
            created = initialize(settings)
            _remember(created, secrets)
            initialized_now = created.initialized
            if created.initialized:
                logger.info(
                    "bootstrap_initialized",
                    data_dir=str(created.paths.root),
                    schema_head=created.schema_head,
                )
        report = ensure_ready(settings)
        if created is not None and created.warnings:
            # 初始化阶段的凭据降级等提示不会在 ensure_ready 里重放，必须合并展示。
            report = replace(
                report,
                warnings=tuple(dict.fromkeys((*created.warnings, *report.warnings))),
            )
    except BootstrapError as exc:
        raise SidecarError(_failure_message(exc, secrets)) from None
    _remember(report, secrets)

    if not report.ready:
        reasons = "；".join(report.messages) or "数据库尚未就绪"
        raise SidecarError(
            f"桌面数据目录未就绪（{report.status}）：{reasons}；"
            "请检查数据目录后重试，或运行 doctor 查看诊断"
        )
    payload = _report_payload(report, initialized_now=initialized_now)
    logger.info(
        "bootstrap_ready",
        status=payload["status"],
        initialized=initialized_now,
        started_postgres=payload["started_postgres"],
        postgres_running=payload["postgres_running"],
        schema_head=payload["schema_head"],
    )
    return payload


def state_summary(settings: Settings) -> dict[str, Any]:
    """供 `doctor` 展示首启状态：只读，不启动、不初始化。"""
    try:
        state = inspect(settings)
    except Exception:  # 数据目录不可读等情况下只报告不可用，不打断诊断
        return {
            "ok": False,
            "critical": False,
            "status": "unavailable",
            "initialized": False,
            "postgres_running": False,
        }
    running = cluster_running(settings, state)
    return {
        "ok": state.initialized,
        "critical": False,
        "status": str(state.status),
        "initialized": state.initialized,
        "postgres_running": running,
        "reasons": list(state.reasons),
    }


__all__ = [
    "cluster_running",
    "require_desktop_mode",
    "run_bootstrap",
    "state_summary",
]
