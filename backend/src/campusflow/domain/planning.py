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


@dataclass(frozen=True)
class FreeWindow:
    start: datetime
    end: datetime

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


def merge_intervals(intervals: list[Interval]) -> list[Interval]:
    """合并重叠区间；首尾相接（end == 下个 start）不算重叠，但可安全合并为连续区间。"""
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda item: (item.start, item.end))
    merged = [ordered[0]]
    for current in ordered[1:]:
        previous = merged[-1]
        if current.start <= previous.end:
            if current.end > previous.end:
                merged[-1] = Interval(previous.start, current.end)
        else:
            merged.append(current)
    return merged


def subtract_intervals(windows: list[Interval], busy: list[Interval]) -> list[Interval]:
    """从可用区间扣除忙碌区间；重叠的忙碌输入只扣一次。"""
    result: list[Interval] = []
    merged_busy = merge_intervals(busy)
    for window in windows:
        segments = [window]
        for blocker in merged_busy:
            next_segments: list[Interval] = []
            for segment in segments:
                if blocker.end <= segment.start or blocker.start >= segment.end:
                    next_segments.append(segment)
                    continue
                if blocker.start > segment.start:
                    next_segments.append(Interval(segment.start, blocker.start))
                if blocker.end < segment.end:
                    next_segments.append(Interval(blocker.end, segment.end))
            segments = next_segments
        result.extend(segment for segment in segments if segment.end > segment.start)
    return result


def task_order_key(task: PlanningTask) -> tuple:
    """明确截止优先，其次重要性；没有截止的任务排在最后。"""
    priority_rank = {"high": 0, "medium": 1, "low": 2}.get(task.priority, 3)
    due_key = task.due_at.timestamp() if task.due_at is not None else float("inf")
    target_key = (
        task.target_date.toordinal() if task.target_date is not None else date.max.toordinal()
    )
    return (due_key, priority_rank, target_key, task.task_id)


@dataclass(frozen=True)
class BlockSuggestion:
    task_id: int | None
    title: str
    starts_at: datetime
    ends_at: datetime
    minutes: int
    seq: int
    reason: str
    constraint_source: tuple[dict[str, Any], ...]
    kind: str = "task"
    locked: bool = False


@dataclass(frozen=True)
class TaskDisposition:
    task_id: int
    title: str
    status: str
    scheduled_minutes: int
    remaining_minutes: int
    reason_code: str | None
    message: str
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class AllocationResult:
    blocks: tuple[BlockSuggestion, ...]
    dispositions: tuple[TaskDisposition, ...]
    capacity_gap_minutes: int


def allocate_tasks(inputs: PlanningInput, resolved: ResolvedInput) -> AllocationResult:
    """按截止与可用时间分配任务；不越过确定截止，也不占用固定日程。

    这里只处理硬约束；每日／每周容量、休息与分段在后续规则中叠加。
    """
    windows = [
        [window.start, window.end] for window in build_free_windows(inputs)
    ]
    blocks: list[BlockSuggestion] = []
    dispositions: list[TaskDisposition] = []
    daily_used: dict[date, int] = {}
    weekly_used: dict[tuple[int, int], int] = {}
    day_last_end: dict[date, datetime] = {}

    for task in sorted(resolved.tasks, key=task_order_key):
        if task.minutes is None:
            dispositions.append(
                TaskDisposition(
                    task_id=task.task_id,
                    title=task.title,
                    status="unscheduled",
                    scheduled_minutes=0,
                    remaining_minutes=0,
                    reason_code="missing_duration",
                    message="缺少剩余耗时，暂未排入",
                    options=("补充剩余耗时", "选择耗时假设后重新生成"),
                )
            )
            continue
        if task.minutes == 0:
            dispositions.append(
                TaskDisposition(
                    task_id=task.task_id,
                    title=task.title,
                    status="unscheduled",
                    scheduled_minutes=0,
                    remaining_minutes=0,
                    reason_code="no_remaining_work",
                    message="剩余耗时为 0，不需要排期",
                    options=(),
                )
            )
            continue

        remaining = task.minutes
        scheduled = 0
        seq = 0
        deadline = task.due_at
        for window in windows:
            if remaining <= 0:
                break
            window_start, window_end = window
            if deadline is not None and window_start >= deadline:
                break

            day = window_start.date()
            day_end = day_last_end.get(day)
            if day_end is not None:
                earliest = day_end + timedelta(minutes=inputs.break_minutes)
                if earliest > window_start:
                    window_start = earliest
                    window[0] = earliest
            daily_remaining = inputs.daily_capacity_minutes - daily_used.get(day, 0)
            week = day.isocalendar()[:2]
            weekly_remaining = inputs.weekly_capacity_minutes - weekly_used.get(week, 0)
            if daily_remaining <= 0 or weekly_remaining <= 0:
                window[0] = window_end
                continue

            latest_end = min(window_end, deadline) if deadline is not None else window_end
            available = int((latest_end - window_start).total_seconds() // 60)
            if available <= 0:
                continue
            minutes = min(remaining, available, daily_remaining, weekly_remaining)
            if minutes <= 0:
                window[0] = window_end
                continue
            block_end = window_start + timedelta(minutes=minutes)
            reason = (
                f"在截止 {deadline.isoformat()} 前安排"
                if deadline is not None
                else "在可用时间内安排"
            )
            constraint_source: list[dict[str, Any]] = [
                {"kind": "availability", "text": "使用工作空间可用时间"},
                {
                    "kind": "priority",
                    "value": task.priority,
                    "text": "按任务重要性与截止顺序安排",
                },
                {
                    "kind": "daily_capacity",
                    "value": inputs.daily_capacity_minutes,
                    "text": "不超过每日学习容量",
                },
                {
                    "kind": "weekly_capacity",
                    "value": inputs.weekly_capacity_minutes,
                    "text": "不超过每周学习容量",
                },
            ]
            if inputs.break_minutes > 0:
                constraint_source.append(
                    {
                        "kind": "break",
                        "value": inputs.break_minutes,
                        "text": "同一任务段之间保留休息",
                    }
                )
            if deadline is not None:
                constraint_source.append(
                    {
                        "kind": "deadline",
                        "value": deadline.isoformat(),
                        "date_only": task.due_date_only,
                        "text": "任务明确截止时间",
                    }
                )
            blocks.append(
                BlockSuggestion(
                    task_id=task.task_id,
                    title=task.title,
                    starts_at=window_start,
                    ends_at=block_end,
                    minutes=minutes,
                    seq=seq,
                    reason=reason,
                    constraint_source=tuple(constraint_source),
                )
            )
            window[0] = block_end
            remaining -= minutes
            scheduled += minutes
            seq += 1
            daily_used[day] = daily_used.get(day, 0) + minutes
            weekly_used[week] = weekly_used.get(week, 0) + minutes
            day_last_end[day] = block_end

        if remaining == 0:
            status = "scheduled"
            reason_code = None
            message = "已完整排入"
            options: tuple[str, ...] = ()
        elif scheduled > 0:
            status = "partial"
            if deadline is not None:
                reason_code = "insufficient_capacity_before_due"
                message = f"已排 {scheduled} 分钟，仍有 {remaining} 分钟无法在截止前安排"
                options = ("拆分任务", "增加可用时间", "调整截止日期", "接受缺口")
            else:
                reason_code = "insufficient_capacity_in_range"
                message = f"已排 {scheduled} 分钟，仍有 {remaining} 分钟超出本次范围容量"
                options = ("增加可用时间", "扩大规划范围", "降低其他任务安排", "接受缺口")
        elif deadline is not None and deadline <= inputs.now:
            status = "unscheduled"
            reason_code = "due_passed"
            message = "截止时间已过，未安排新的时间块"
            options = ("更新截止日期", "取消或重新打开任务", "接受逾期")
        else:
            status = "unscheduled"
            reason_code = "no_available_time"
            message = "没有可用的学习时间"
            options = ("增加可用时间", "调整截止日期", "降低其他任务安排")
        dispositions.append(
            TaskDisposition(
                task_id=task.task_id,
                title=task.title,
                status=status,
                scheduled_minutes=scheduled,
                remaining_minutes=remaining,
                reason_code=reason_code,
                message=message,
                options=options,
            )
        )

    gap = sum(item.remaining_minutes for item in dispositions)
    return AllocationResult(
        blocks=tuple(blocks), dispositions=tuple(dispositions), capacity_gap_minutes=gap
    )


def build_free_windows(inputs: PlanningInput) -> list[FreeWindow]:
    """按工作空间时区展开可用时段，扣除固定日程并裁掉已过去的时间。"""
    timezone = _zone(inputs.timezone)
    availability: list[Interval] = []
    current_date = inputs.range_start
    while current_date <= inputs.range_end:
        for rule in inputs.availability:
            if rule.day_of_week != current_date.weekday():
                continue
            availability.append(
                Interval(
                    datetime.combine(current_date, rule.start_time, tzinfo=timezone),
                    datetime.combine(current_date, rule.end_time, tzinfo=timezone),
                )
            )
        current_date += timedelta(days=1)

    busy = [Interval(event.start, event.end) for event in inputs.fixed_events]
    free = subtract_intervals(merge_intervals(availability), busy)
    floor = inputs.now
    clipped: list[Interval] = []
    for window in free:
        start = max(window.start, floor)
        if window.end > start:
            clipped.append(Interval(start, window.end))
    return [FreeWindow(start=window.start, end=window.end) for window in clipped]
