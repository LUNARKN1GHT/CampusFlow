"""M5 两周排期的纯领域规则。

输入统一按工作空间时区解释；计划时间块只引用任务，不修改任务截止。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from campusflow.domain.errors import DomainError


@dataclass(frozen=True)
class Interval:
    start: datetime
    end: datetime

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


@dataclass(frozen=True)
class AvailabilityRule:
    day_of_week: int
    start_time: time
    end_time: time


@dataclass(frozen=True)
class PlanningTask:
    """参与排期的任务。minutes 为 None 表示耗时未知，不能静默按 0 处理。"""

    task_id: int
    title: str
    course_id: int | None
    priority: str
    minutes: int | None
    due_at: datetime | None
    due_date_only: bool
    target_date: date | None
    estimated: bool
    # 记录值来自用户明确选择的假设，而不是原文或已确认事实。
    due_assumed: bool = False
    duration_assumed: bool = False


@dataclass(frozen=True)
class ExcludedTask:
    task_id: int
    title: str
    progress: str


@dataclass(frozen=True)
class FixedEventOccurrence:
    event_id: int
    title: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class PlanningInput:
    timezone: str
    range_start: date
    range_end: date
    now: datetime
    tasks: tuple[PlanningTask, ...]
    excluded_tasks: tuple[ExcludedTask, ...]
    fixed_events: tuple[FixedEventOccurrence, ...]
    availability: tuple[AvailabilityRule, ...]
    daily_capacity_minutes: int
    weekly_capacity_minutes: int
    break_minutes: int
    buffer_minutes: int
    segment_minutes: int = 90


def _iso(value: datetime | date | time | None) -> str | None:
    return value.isoformat() if value is not None else None


def input_snapshot(
    inputs: PlanningInput, raw_tasks: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """保存生成输入的快照：接受计划时用同一输入重新核对版本。"""
    return {
        "timezone": inputs.timezone,
        "range_start": inputs.range_start.isoformat(),
        "range_end": inputs.range_end.isoformat(),
        "settings": {
            "daily_capacity_minutes": inputs.daily_capacity_minutes,
            "weekly_capacity_minutes": inputs.weekly_capacity_minutes,
            "break_minutes": inputs.break_minutes,
            "buffer_minutes": inputs.buffer_minutes,
            "segment_minutes": inputs.segment_minutes,
        },
        "tasks": raw_tasks
        if raw_tasks is not None
        else [
            {
                "id": task.task_id,
                "title": task.title,
                "course_id": task.course_id,
                "priority": task.priority,
                "minutes": task.minutes,
                "due_at": _iso(task.due_at),
                "due_date_only": task.due_date_only,
                "target_date": _iso(task.target_date),
                "estimated": task.estimated,
            }
            for task in inputs.tasks
        ],
        "excluded_tasks": [
            {"id": task.task_id, "title": task.title, "progress": task.progress}
            for task in inputs.excluded_tasks
        ],
        "fixed_events": [
            {
                "id": event.event_id,
                "title": event.title,
                "starts_at": event.start.isoformat(),
                "ends_at": event.end.isoformat(),
            }
            for event in inputs.fixed_events
        ],
        "availability": [
            {
                "day_of_week": rule.day_of_week,
                "start_time": _iso(rule.start_time),
                "end_time": _iso(rule.end_time),
            }
            for rule in inputs.availability
        ],
    }


def input_fingerprint(snapshot: dict[str, Any]) -> str:
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def two_week_range(today: date) -> tuple[date, date]:
    """未来两周（含今天与第 14 天），边界按工作空间时区的今天计算。"""
    return today, today + timedelta(days=13)


@dataclass(frozen=True)
class AssumptionOptions:
    """用户显式选择的规划假设；为空时不补任何值（P003）。"""

    default_duration_minutes: int | None = None
    task_durations: Mapping[int, int] = field(default_factory=dict)
    task_due_dates: Mapping[int, date] = field(default_factory=dict)
    segment_minutes: int | None = None
    buffer_minutes: int | None = None


@dataclass(frozen=True)
class ResolvedInput:
    tasks: tuple[PlanningTask, ...]
    gaps: tuple[dict[str, Any], ...]
    assumptions: tuple[dict[str, Any], ...]
    segment_minutes: int
    buffer_minutes: int


def resolve_assumptions(inputs: PlanningInput, options: AssumptionOptions) -> ResolvedInput:
    """补齐缺失日期／耗时，并把假设与待补充项分开返回。

    未提供假设时保持 minutes=None 并列入 gaps；候选事项不在此处读取，
    因此不会被当作已确认约束。
    """
    if options.default_duration_minutes is not None and options.default_duration_minutes <= 0:
        raise DomainError("默认耗时假设必须大于 0 分钟")
    if options.segment_minutes is not None and options.segment_minutes <= 0:
        raise DomainError("单段时间假设必须大于 0 分钟")
    if options.buffer_minutes is not None and options.buffer_minutes < 0:
        raise DomainError("提交缓冲假设不能为负数")

    timezone = _zone(inputs.timezone)
    gaps: list[dict[str, Any]] = []
    assumptions: list[dict[str, Any]] = []
    tasks: list[PlanningTask] = []

    for task in inputs.tasks:
        minutes = task.minutes
        duration_assumed = task.duration_assumed
        if minutes is None and task.task_id in options.task_durations:
            minutes = options.task_durations[task.task_id]
            if minutes <= 0:
                raise DomainError(f"任务 {task.task_id} 的耗时假设必须大于 0 分钟")
            duration_assumed = True
            assumptions.append(
                {
                    "kind": "duration",
                    "task_id": task.task_id,
                    "title": task.title,
                    "value": minutes,
                    "text": f"按用户假设，任务《{task.title}》需要 {minutes} 分钟",
                }
            )
        elif minutes is None and options.default_duration_minutes is not None:
            minutes = options.default_duration_minutes
            duration_assumed = True
            assumptions.append(
                {
                    "kind": "duration",
                    "task_id": task.task_id,
                    "title": task.title,
                    "value": minutes,
                    "text": (
                        f"按用户选择的默认假设，任务《{task.title}》需要 {minutes} 分钟"
                    ),
                }
            )

        due_at = task.due_at
        due_assumed = task.due_assumed
        if due_at is None and task.task_id in options.task_due_dates:
            assumed_date = options.task_due_dates[task.task_id]
            due_at = datetime.combine(assumed_date, time.max, tzinfo=timezone)
            due_assumed = True
            assumptions.append(
                {
                    "kind": "due_date",
                    "task_id": task.task_id,
                    "title": task.title,
                    "value": assumed_date.isoformat(),
                    "text": (
                        f"按用户假设，任务《{task.title}》的截止日期为 {assumed_date.isoformat()}"
                    ),
                }
            )

        if minutes is None:
            gaps.append(
                {
                    "code": "missing_duration",
                    "field": "duration",
                    "task_id": task.task_id,
                    "title": task.title,
                    "message": f"任务《{task.title}》缺少剩余耗时，暂不排入；可补充耗时或选择假设",
                }
            )
        elif minutes == 0:
            gaps.append(
                {
                    "code": "no_remaining_work",
                    "field": "duration",
                    "task_id": task.task_id,
                    "title": task.title,
                    "message": f"任务《{task.title}》剩余耗时为 0，不需要排期",
                }
            )
        if due_at is None:
            gaps.append(
                {
                    "code": "missing_due_date",
                    "field": "due_date",
                    "task_id": task.task_id,
                    "title": task.title,
                    "message": (
                        f"任务《{task.title}》没有明确截止日期；将按优先级安排，"
                        "但不校验截止风险"
                    ),
                }
            )
        tasks.append(
            PlanningTask(
                task_id=task.task_id,
                title=task.title,
                course_id=task.course_id,
                priority=task.priority,
                minutes=minutes,
                due_at=due_at,
                due_date_only=task.due_date_only if not due_assumed else True,
                target_date=task.target_date,
                estimated=task.estimated or duration_assumed,
                due_assumed=due_assumed,
                duration_assumed=duration_assumed,
            )
        )

    return ResolvedInput(
        tasks=tuple(tasks),
        gaps=tuple(gaps),
        assumptions=tuple(assumptions),
        segment_minutes=options.segment_minutes or inputs.segment_minutes,
        buffer_minutes=(
            options.buffer_minutes if options.buffer_minutes is not None else inputs.buffer_minutes
        ),
    )


def _zone(name: str):
    from zoneinfo import ZoneInfo

    return ZoneInfo(name)
