"""M5 两周排期的纯领域规则。

输入统一按工作空间时区解释；计划时间块只引用任务，不修改任务截止。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any


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
