"""`upgrade`：P011 桌面升级迁移的 CLI 入口。

版本门禁、升级前备份、失败恢复与保留清理全部由 `application.upgrade` 与
`infrastructure.backup.postgres` 负责；这里只折算退出码与输出。

- `--check` 只读：`inspect_schema` 不写库。`up_to_date`／`uninitialized` 退出码 0
  （后者明确提示先运行 bootstrap）；`upgrade_required` 未加 `--apply` 时退出码 1；
  `database_ahead` 一律退出码 1，且不降级、不迁移。
- `--check --apply`：需要升级时按默认端口执行正式升级（先备份再迁移）。
- 不带 `--check`：直接 `upgrade_if_needed`，输出备份路径、from→to 与耗时；
  失败时退出码 1，迁移失败的消息里带恢复备份路径。
"""

from __future__ import annotations

import time
from typing import Any

from campusflow.application.upgrade import (
    UpgradeError,
    UpgradeReport,
    UpgradeState,
    inspect_schema,
    upgrade_if_needed,
)
from campusflow.core.config import Settings
from campusflow.sidecar.errors import SidecarError
from campusflow.sidecar.logs import EventLogger, scrub_text


def _snapshot_payload(settings: Settings) -> dict[str, Any]:
    snapshot = inspect_schema(settings)
    return {
        "state": str(snapshot.state),
        "database_revision": snapshot.database_revision,
        "bundled_head": snapshot.bundled_head,
        "requires_bootstrap": snapshot.state is UpgradeState.UNINITIALIZED,
        "requires_upgrade": snapshot.state is UpgradeState.UPGRADE_REQUIRED,
        "action": None,
        "backup": None,
        "duration_seconds": None,
    }


def _report_fields(report: UpgradeReport) -> dict[str, Any]:
    return {
        "state": str(report.state),
        "database_revision": report.previous_revision,
        "bundled_head": report.bundled_head,
        "requires_bootstrap": False,
        "requires_upgrade": False,
        "action": report.action,
        "backup": str(report.backup.path) if report.backup is not None else None,
        "pruned_backups": list(report.pruned_backups),
    }


def _apply(settings: Settings, logger: EventLogger) -> dict[str, Any]:
    started = time.monotonic()
    try:
        report = upgrade_if_needed(settings)
    except UpgradeError as exc:
        # P011 的失败文案已说明备份是否恢复并在迁移失败时给出备份路径。
        logger.error("upgrade_failed", error=type(exc).__name__)
        raise SidecarError(scrub_text(str(exc), [])) from None
    duration = time.monotonic() - started
    payload = _report_fields(report)
    payload["duration_seconds"] = round(duration, 3)
    logger.info(
        "upgrade_completed",
        action=payload["action"],
        previous_revision=payload["database_revision"],
        bundled_head=payload["bundled_head"],
        backup=payload["backup"],
        duration_seconds=payload["duration_seconds"],
    )
    return payload


def check_upgrade(
    settings: Settings, logger: EventLogger, *, apply: bool = False
) -> tuple[dict[str, Any], int]:
    """只读检查；`apply=True` 时在需要升级的情况下执行正式升级。"""
    payload = _snapshot_payload(settings)
    state = UpgradeState(payload["state"])
    if state is UpgradeState.DATABASE_AHEAD:
        # 只读检查也不写库：输出状态并退出 1，由上层决定如何提示用户安装新版本。
        logger.info("upgrade_checked", state=payload["state"], database_ahead=True)
        return payload, 1
    if state is UpgradeState.UNINITIALIZED:
        logger.info("upgrade_checked", state=payload["state"], requires_bootstrap=True)
        return payload, 0
    if state is UpgradeState.UP_TO_DATE:
        logger.info("upgrade_checked", state=payload["state"], action="none")
        return payload, 0
    if not apply:
        logger.info("upgrade_checked", state=payload["state"], requires_upgrade=True)
        return payload, 1
    return _apply(settings, logger), 0


def apply_upgrade(settings: Settings, logger: EventLogger) -> dict[str, Any]:
    """默认升级入口：与 `--check --apply` 使用同一套 P011 端口。"""
    return _apply(settings, logger)


def describe(payload: dict[str, Any]) -> str:
    """给非 JSON 输出用的简短中文说明；不含连接串或口令。"""
    state = payload.get("state")
    if payload.get("action") in ("created_schema", "upgraded"):
        action = "已升级" if payload["action"] == "upgraded" else "已建表（空库）"
        backup = payload.get("backup") or "无（空库无需备份）"
        seconds = payload.get("duration_seconds")
        timing = f"，耗时 {seconds} 秒" if seconds is not None else ""
        return (
            f"升级完成（{action}）：{payload.get('database_revision') or '（空）'} -> "
            f"{payload.get('bundled_head')}，备份 {backup}{timing}"
        )
    if state == str(UpgradeState.UP_TO_DATE):
        return (
            f"数据库已是最新：{payload.get('database_revision')}"
            f"（内置版本 {payload.get('bundled_head')}），本次未迁移"
        )
    if state == str(UpgradeState.UNINITIALIZED):
        return "数据库尚未初始化：请先运行 campusflow-sidecar bootstrap 完成首启初始化"
    if state == str(UpgradeState.UPGRADE_REQUIRED):
        return (
            f"数据库需要升级：{payload.get('database_revision')} -> "
            f"{payload.get('bundled_head')}；当前为只读检查，未修改数据库。"
            "请运行 campusflow-sidecar upgrade（或 upgrade --check --apply）执行升级"
        )
    if state == str(UpgradeState.DATABASE_AHEAD):
        return (
            f"数据库结构版本（{payload.get('database_revision')}）高于应用内置版本"
            f"（{payload.get('bundled_head')}）；本应用不会降级，也未修改数据库。"
            "请安装较新的 CampusFlow 应用"
        )
    return f"数据库结构状态：{state}"


__all__ = ["apply_upgrade", "check_upgrade", "describe"]
