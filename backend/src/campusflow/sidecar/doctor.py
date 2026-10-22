"""`doctor`：只读诊断数据库、向量扩展、迁移头、目录可写性与客户端工具。

诊断只读：不解压备份、不写存储目录、不派发作业、不调用模型。输出里没有任何
密钥、口令或连接串原文；模型密钥只报告"是否已配置"这个布尔值。
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text

from campusflow.core.config import Settings
from campusflow.sidecar.backup import find_pg_tool
from campusflow.sidecar.bootstrap import state_summary
from campusflow.sidecar.dbtools import (
    connect,
    database_name,
    migration_state,
)
from campusflow.sidecar.errors import SidecarError
from campusflow.sidecar.logs import EventLogger

# 关键项失败即退出码 1；其余只提示，不阻止桌面首次启动。
CRITICAL_CHECKS = ("database", "vector_extension", "migrations", "storage_dir", "pg_tools")


def _check_directory(path: Path, *, critical: bool) -> dict[str, Any]:
    """目录可写性：实际写入临时探针后删除，比只看权限位更接近真实结果。"""
    check: dict[str, Any] = {
        "ok": False,
        "path": str(path),
        "exists": path.is_dir(),
        "writable": False,
        "critical": critical,
    }
    if not path.is_dir():
        check["status"] = "missing"
        return check
    try:
        with tempfile.NamedTemporaryFile(dir=path, prefix=".campusflow-doctor-", delete=True):
            check["writable"] = True
        check["ok"] = True
        check["status"] = "writable"
    except OSError:
        check["status"] = "read_only"
    return check


def _check_database(settings: Settings) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """返回 database、vector_extension、migrations 三项；数据库不可达时后两项一并失败。"""
    database: dict[str, Any] = {
        "ok": False,
        "critical": True,
        "database": database_name(settings.database_url),
    }
    vector: dict[str, Any] = {"ok": False, "critical": True, "version": None}
    migrations: dict[str, Any] = {
        "ok": False,
        "critical": True,
        "current": [],
        "head": [],
    }
    engine = connect(settings)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            database["ok"] = True
            database["status"] = "connected"
            version = connection.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            ).scalar_one_or_none()
        vector["version"] = version
        if version is not None:
            vector["ok"] = True
            vector["status"] = "installed"
        else:
            vector["status"] = "missing"
        state = migration_state(settings, engine)
        migrations.update(
            current=state["current"],
            head=state["head"],
            ok=state["at_head"],
            status="current" if state["at_head"] else "behind",
        )
    except Exception:
        # 不输出异常原文：连接串与驱动细节都可能带口令或主机信息。
        database.update(ok=False, status="unavailable")
        vector["status"] = "unavailable"
        migrations["status"] = "unavailable"
    finally:
        engine.dispose()
    return database, vector, migrations


def _check_pg_tools() -> dict[str, Any]:
    """备份与恢复都需要客户端工具；缺任一项都算关键失败。"""
    tools: dict[str, Any] = {"ok": False, "critical": True, "pg_dump": None, "pg_restore": None}
    missing: list[str] = []
    for name in ("pg_dump", "pg_restore"):
        try:
            tools[name] = find_pg_tool(name)
        except SidecarError:
            missing.append(name)
    tools["ok"] = not missing
    tools["status"] = "found" if not missing else f"missing:{','.join(missing)}"
    return tools


def collect_report(settings: Settings, *, log_dir: str | Path | None = None) -> dict[str, Any]:
    """汇总全部检查项；`ok` 只取决于关键项。"""
    database, vector, migrations = _check_database(settings)
    checks: dict[str, Any] = {
        "database": database,
        "vector_extension": vector,
        "migrations": migrations,
        "storage_dir": _check_directory(
            Path(settings.storage_dir).expanduser().absolute(), critical=True
        ),
        "pg_tools": _check_pg_tools(),
    }

    data_dir = Path(settings.data_dir).expanduser().absolute() if settings.data_dir else None
    if data_dir is None:
        checks["data_dir"] = {"ok": True, "critical": False, "status": "not_configured"}
    else:
        checks["data_dir"] = _check_directory(data_dir, critical=True)

    # 桌面模式补充首启状态（P007）：只读检查，不初始化、不启动数据库。
    if settings.deployment_mode == "desktop" and data_dir is not None:
        checks["bootstrap"] = state_summary(settings)

    if log_dir is not None:
        checks["log_dir"] = _check_directory(Path(log_dir).expanduser(), critical=False)
    elif data_dir is not None:
        checks["log_dir"] = _check_directory(data_dir / "logs", critical=False)
    else:
        checks["log_dir"] = {"ok": True, "critical": False, "status": "not_configured"}

    model_key = bool(settings.zhipu_api_key or settings.deepseek_api_key)
    checks["model_key"] = {
        "ok": True,
        "critical": False,
        "status": "configured" if model_key else "missing",
        "configured": model_key,
        "provider": settings.text_provider,
    }

    failed = [name for name, check in checks.items() if not check["ok"]]
    critical_failed = [
        name for name, check in checks.items() if not check["ok"] and check["critical"]
    ]
    return {
        "ok": not critical_failed,
        "command": "doctor",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "deployment_mode": settings.deployment_mode,
        "database": database_name(settings.database_url),
        "checks": checks,
        "failed": failed,
        "critical_failed": critical_failed,
        "warnings": [name for name in failed if name not in critical_failed],
    }


def run_doctor(
    settings: Settings,
    logger: EventLogger,
    *,
    log_dir: str | Path | None = None,
) -> dict[str, Any]:
    """执行诊断并写一条结构化事件；退出码由 CLI 按 `ok` 折算。"""
    report = collect_report(settings, log_dir=log_dir)
    logger.info(
        "doctor_completed",
        ok=report["ok"],
        failed=report["failed"],
        critical_failed=report["critical_failed"],
    )
    return report
