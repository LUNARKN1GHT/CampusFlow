"""M7 应用内提醒：规则、生成、幂等与状态联动（A010–A014）。

规则和触发记录分开保存。提醒时间一律按工作空间时区解释。
默认策略：工作空间默认规则提前 1440 分钟、不重复、不暂停。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from campusflow.application.ports.repositories import ReminderRuleData, Repositories
from campusflow.application.settings import get_settings
from campusflow.domain.errors import ConflictError, DomainError, NotFoundError
from campusflow.domain.reminders import (
    deadline_dedupe_key,
    task_review_dedupe_key,
    workspace_review_dedupe_key,
)
from campusflow.domain.states import DueSourceType, ReviewStatus, TaskProgress

DEFAULT_ADVANCE_MINUTES = 1440
DEFAULT_RULE_VERSION = 1
MAX_ADVANCE_MINUTES = 10080


@dataclass(frozen=True)
class EffectiveRule:
    rule_id: int | None
    version: int
    advance_minutes: int
    repeat_minutes: int | None
    paused: bool


def _now(repos: Repositories, workspace_id: int, now: datetime | None) -> datetime:
    timezone = ZoneInfo(get_settings(repos, workspace_id).timezone)
    return (now or datetime.now(UTC)).astimezone(timezone)


def _effective_rule(repos: Repositories, workspace_id: int, task_id: int) -> EffectiveRule | None:
    task_rule = repos.reminders.get_task_rule(workspace_id, task_id)
    if task_rule is not None:
        if not task_rule.enabled:
            return None
        return EffectiveRule(
            task_rule.id,
            task_rule.version,
            task_rule.advance_minutes,
            task_rule.repeat_minutes,
            task_rule.paused,
        )
    default_rule = repos.reminders.get_default_rule(workspace_id)
    if default_rule is not None:
        if not default_rule.enabled:
            return None
        return EffectiveRule(
            default_rule.id,
            default_rule.version,
            default_rule.advance_minutes,
            default_rule.repeat_minutes,
            default_rule.paused,
        )
    return EffectiveRule(None, DEFAULT_RULE_VERSION, DEFAULT_ADVANCE_MINUTES, None, False)


def _validate_rule(advance_minutes: int, repeat_minutes: int | None) -> None:
    if not 0 <= advance_minutes <= MAX_ADVANCE_MINUTES:
        raise DomainError("提前量必须在 0 到 10080 分钟之间")
    if repeat_minutes is not None and not 1 <= repeat_minutes <= MAX_ADVANCE_MINUTES:
        raise DomainError("重复间隔必须在 1 到 10080 分钟之间")


def list_rules(repos: Repositories, workspace_id: int) -> list[dict]:
    default_rule = repos.reminders.get_default_rule(workspace_id)
    rules = repos.reminders.list_rules(workspace_id)
    return [
        {
            "id": rule.id,
            "task_id": rule.task_id,
            "advance_minutes": rule.advance_minutes,
            "repeat_minutes": rule.repeat_minutes,
            "paused": rule.paused,
            "enabled": rule.enabled,
            "version": rule.version,
            "is_default": rule.task_id is None,
        }
        for rule in rules
    ] + (
        []
        if default_rule is not None
        else [
            {
                "id": None,
                "task_id": None,
                "advance_minutes": DEFAULT_ADVANCE_MINUTES,
                "repeat_minutes": None,
                "paused": False,
                "enabled": True,
                "version": DEFAULT_RULE_VERSION,
                "is_default": True,
                "builtin": True,
            }
        ]
    )


def _validate_task(repos: Repositories, workspace_id: int, task_id: int | None) -> None:
    if task_id is None:
        return
    task = repos.tasks.get(task_id)
    if task is None or task.workspace_id != workspace_id:
        raise NotFoundError("任务不存在")


def create_rule(
    repos: Repositories,
    workspace_id: int,
    *,
    task_id: int | None,
    advance_minutes: int,
    repeat_minutes: int | None,
    paused: bool,
    enabled: bool,
) -> ReminderRuleData:
    _validate_rule(advance_minutes, repeat_minutes)
    _validate_task(repos, workspace_id, task_id)
    existing = (
        repos.reminders.get_default_rule(workspace_id)
        if task_id is None
        else repos.reminders.get_task_rule(workspace_id, task_id)
    )
    if existing is not None:
        raise ConflictError("该范围已有提醒规则，请直接更新")
    rule = repos.reminders.create_rule(
        workspace_id,
        task_id=task_id,
        advance_minutes=advance_minutes,
        repeat_minutes=repeat_minutes,
        paused=paused,
        enabled=enabled,
    )
    sync_reminders(repos, workspace_id, task_ids=[] if task_id is None else [task_id])
    repos.uow.commit()
    return rule


def update_rule(
    repos: Repositories,
    workspace_id: int,
    rule_id: int,
    *,
    advance_minutes: int,
    repeat_minutes: int | None,
    paused: bool,
    enabled: bool,
) -> ReminderRuleData:
    rule = repos.reminders.get_rule(rule_id)
    if rule is None or rule.workspace_id != workspace_id:
        raise NotFoundError("提醒规则不存在")
    _validate_rule(advance_minutes, repeat_minutes)
    updated = repos.reminders.update_rule(
        rule_id,
        advance_minutes=advance_minutes,
        repeat_minutes=repeat_minutes,
        paused=paused,
        enabled=enabled,
    )
    if rule.task_id is not None:
        sync_reminders(repos, workspace_id, task_ids=[rule.task_id])
    else:
        sync_reminders(repos, workspace_id)
    repos.uow.commit()
    return updated


def delete_rule(repos: Repositories, workspace_id: int, rule_id: int) -> None:
    rule = repos.reminders.get_rule(rule_id)
    if rule is None or rule.workspace_id != workspace_id:
        raise NotFoundError("提醒规则不存在")
    if rule.task_id is not None:
        repos.reminders.cancel_pending_for_task(workspace_id, rule.task_id)
    repos.reminders.delete_rule(rule_id)
    sync_reminders(repos, workspace_id)
    repos.uow.commit()


def _deadline_rows(
    task,
    rule: EffectiveRule,
    now: datetime,
    timezone: ZoneInfo,
) -> tuple[list[dict], list[dict]]:
    """返回（deadline 行, review 行）。日期 DDL 只说明日期，不伪造时刻。"""
    if task.due_date is None:
        return [], []
    date_only = task.due_time is None
    if task.source_invalidated_at is not None and task.due_source != DueSourceType.USER:
        due_text = (
            f"{task.due_date.isoformat()}（未指定具体时刻）"
            if date_only
            else f"{task.due_date.isoformat()} {task.due_time.strftime('%H:%M')}"
        )
        key = task_review_dedupe_key(
            task_id=task.id, due_date=task.due_date, rule_version=rule.version
        )
        return [], [
            {
                "kind": "review",
                "dedupe_key": key,
                "scheduled_for": now,
                "message": (
                    f"任务《{task.title}》的来源资料已失效，截止要求待核对；"
                    f"现有截止为 {due_text}，尚未确认"
                ),
                "payload": {
                    "task_id": task.id,
                    "due_date": task.due_date.isoformat(),
                    "date_only": date_only,
                    "uncertain": True,
                    "source_invalidated": True,
                },
            }
        ]

    due_at = datetime.combine(
        task.due_date,
        task.due_time if task.due_time is not None else time(9, 0),
        tzinfo=timezone,
    )
    first_window = due_at - timedelta(minutes=rule.advance_minutes)
    windows: list[datetime] = [first_window]
    if rule.repeat_minutes:
        repeat_at = first_window + timedelta(minutes=rule.repeat_minutes)
        while repeat_at <= due_at:
            windows.append(repeat_at)
            repeat_at += timedelta(minutes=rule.repeat_minutes)
    rows: list[dict] = []
    for scheduled in sorted(set(windows)):
        if scheduled < now - timedelta(days=1):
            continue
        key = deadline_dedupe_key(
            task_id=task.id,
            rule_id=rule.rule_id,
            rule_version=rule.version,
            due_date=task.due_date,
            due_time=task.due_time,
            window_start=scheduled,
        )
        if date_only:
            message = (
                f"任务《{task.title}》的截止日期为 {task.due_date.isoformat()}"
                "（未指定具体时刻），请按当天完成安排"
            )
        else:
            message = (
                f"任务《{task.title}》将在 {task.due_date.isoformat()} "
                f"{task.due_time.strftime('%H:%M')} 截止"
            )
        rows.append(
            {
                "kind": "deadline",
                "dedupe_key": key,
                "scheduled_for": scheduled,
                "message": message,
                "payload": {
                    "task_id": task.id,
                    "due_date": task.due_date.isoformat(),
                    "due_time": task.due_time.isoformat() if task.due_time else None,
                    "date_only": date_only,
                    "advance_minutes": rule.advance_minutes,
                    "rule_version": rule.version,
                },
            }
        )
    return rows, []


def sync_reminders(
    repos: Repositories,
    workspace_id: int,
    *,
    task_ids: list[int] | None = None,
    now: datetime | None = None,
) -> dict:
    """按最新任务与规则生成提醒，并撤消失效触发（A011/A012/A014）。

    dedupe_key 唯一约束保证同一事项、规则版本和触发窗口只保留一条记录。
    """
    moment = _now(repos, workspace_id, now)
    timezone = ZoneInfo(get_settings(repos, workspace_id).timezone)
    tasks = repos.tasks.list(workspace_id)
    scope = set(task_ids) if task_ids is not None else {task.id for task in tasks}
    created = 0
    cancelled = 0
    for task in tasks:
        if task.id not in scope:
            continue
        if task.progress in (TaskProgress.DONE, TaskProgress.CANCELLED):
            cancelled += repos.reminders.cancel_pending_for_task(workspace_id, task.id)
            continue
        rule = _effective_rule(repos, workspace_id, task.id)
        if rule is None or rule.paused:
            cancelled += repos.reminders.cancel_pending_for_task(workspace_id, task.id)
            continue
        deadline_rows, review_rows = _deadline_rows(task, rule, moment, timezone)
        desired = list({row["dedupe_key"]: row for row in deadline_rows + review_rows}.values())
        keys = {row["dedupe_key"] for row in desired}
        cancelled += repos.reminders.cancel_pending_not_in_keys(workspace_id, task.id, keys)
        rows = [
            {
                "workspace_id": workspace_id,
                "rule_id": rule.rule_id,
                "task_id": task.id,
                **row,
            }
            for row in desired
        ]
        created += len(repos.reminders.upsert_triggers(rows))

    default_rule = _effective_rule(repos, workspace_id, task_id=-1)
    if task_ids is None or not task_ids:
        pending_reviews = len(
            repos.candidates.list_for_review(workspace_id, review_statuses=(ReviewStatus.PENDING,))
        )
        repos.reminders.cancel_pending_reviews(workspace_id)
        if pending_reviews and default_rule is not None and not default_rule.paused:
            key = workspace_review_dedupe_key(day=moment.date())
            rows = [
                {
                    "workspace_id": workspace_id,
                    "rule_id": default_rule.rule_id,
                    "task_id": None,
                    "kind": "review",
                    "dedupe_key": key,
                    "scheduled_for": moment,
                    "message": (
                        f"有 {pending_reviews} 项待核对事项；确认前不会作为确定约束参与排期"
                    ),
                    "payload": {"pending_review_count": pending_reviews},
                }
            ]
            created += len(repos.reminders.upsert_triggers(rows))

    repos.reminders.mark_due_sent(workspace_id, moment)
    return {"created": created, "cancelled": cancelled, "scheduled_for": moment.isoformat()}


def list_reminders(
    repos: Repositories,
    workspace_id: int,
    *,
    unread_only: bool = False,
    statuses: tuple[str, ...] | None = None,
) -> list[dict]:
    sync_reminders(repos, workspace_id)
    repos.uow.commit()
    triggers = repos.reminders.list_triggers(
        workspace_id, unread_only=unread_only, statuses=statuses
    )
    return [
        {
            "id": item.id,
            "task_id": item.task_id,
            "kind": item.kind,
            "message": item.message,
            "status": item.status,
            "scheduled_for": item.scheduled_for.isoformat(),
            "sent_at": item.sent_at.isoformat() if item.sent_at else None,
            "read_at": item.read_at.isoformat() if item.read_at else None,
            "payload": item.payload,
        }
        for item in triggers
    ]


def reminder_summary(repos: Repositories, workspace_id: int) -> dict:
    sync_reminders(repos, workspace_id)
    repos.uow.commit()
    triggers = repos.reminders.list_triggers(workspace_id, limit=500)
    sent = [item for item in triggers if item.status == "sent"]
    return {
        "unread": sum(1 for item in sent if item.read_at is None),
        "sent": len(sent),
        "pending": sum(1 for item in triggers if item.status == "pending"),
        "dismissed": sum(1 for item in triggers if item.status == "dismissed"),
        "cancelled": sum(1 for item in triggers if item.status == "cancelled"),
        "note": "暂无提醒不代表没有待办或冲突；以任务列表与冲突检查为准",
    }


def set_trigger_action(
    repos: Repositories,
    workspace_id: int,
    trigger_id: int,
    *,
    action: str,
    now: datetime | None = None,
) -> dict:
    trigger = repos.reminders.get_trigger(trigger_id)
    if trigger is None or trigger.workspace_id != workspace_id:
        raise NotFoundError("提醒不存在")
    moment = _now(repos, workspace_id, now)
    if action == "read":
        updated = repos.reminders.set_trigger_status(trigger_id, status="sent", read_at=moment)
    elif action == "dismiss":
        updated = repos.reminders.set_trigger_status(trigger_id, status="dismissed")
    else:
        raise DomainError("不支持的提醒操作")
    repos.uow.commit()
    return {"id": updated.id, "status": updated.status}
