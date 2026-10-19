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
from campusflow.domain.task_dependencies import cyclic_components


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
    # A005：已保留的时间块（锁定或局部重排范围外）占用的分钟数
    locked_minutes: int = 0


@dataclass(frozen=True)
class PlanningDependency:
    """A003：排期使用的依赖边。

    state 由应用层按工作空间数据判定：planned（前置也在本次排期中）、done（已完成）、
    cancelled / out_of_scope / missing（不可满足）。
    """

    task_id: int
    depends_on_task_id: int
    prerequisite_title: str
    state: str


@dataclass(frozen=True)
class ExcludedTask:
    task_id: int
    title: str
    progress: str
    # progress：已完成/已取消；container：有子任务，父任务本身不参与排期
    reason: str = "progress"


@dataclass(frozen=True)
class FixedEventOccurrence:
    event_id: int
    title: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class PreservedBlock:
    """A005：重排时必须原样保留的时间块（锁定或不在本次重排范围）。"""

    block_id: int
    task_id: int | None
    title: str
    start: datetime
    end: datetime
    minutes: int
    locked: bool

    @property
    def seq(self) -> int:
        return 0


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
    dependencies: tuple[PlanningDependency, ...] = ()
    preserved_blocks: tuple[PreservedBlock, ...] = ()


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
            {
                "id": task.task_id,
                "title": task.title,
                "progress": task.progress,
                "reason": task.reason,
            }
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
        "dependencies": [
            {
                "task_id": link.task_id,
                "depends_on_task_id": link.depends_on_task_id,
                "state": link.state,
            }
            for link in inputs.dependencies
        ],
        "preserved_blocks": [
            {
                "block_id": block.block_id,
                "task_id": block.task_id,
                "title": block.title,
                "starts_at": block.start.isoformat(),
                "ends_at": block.end.isoformat(),
                "minutes": block.minutes,
                "locked": block.locked,
            }
            for block in inputs.preserved_blocks
        ],
    }


def input_fingerprint(snapshot: dict[str, Any]) -> str:
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def two_week_range(today: date) -> tuple[date, date]:
    """未来两周（含今天与第 14 天），边界按工作空间时区的今天计算。"""
    return today, today + timedelta(days=13)


def resolve_planning_range(
    kind: str,
    today: date,
    *,
    start: date | None = None,
    end: date | None = None,
) -> tuple[date, date]:
    """A007：把今天/本周/两周/自定义解析为闭区间日期。

    所有边界都由工作空间时区的"今天"推导；自定义区间必须同时给出起止。
    """
    if kind == "today":
        return today, today
    if kind == "this_week":
        monday = today - timedelta(days=today.weekday())
        return monday, monday + timedelta(days=6)
    if kind == "two_weeks":
        return two_week_range(today)
    if kind == "custom":
        if start is None or end is None:
            raise DomainError("自定义规划区间需要同时提供开始和结束日期")
        if end < start:
            raise DomainError("规划结束日期不得早于开始日期")
        return start, end
    raise DomainError("不支持的规划区间类型")


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
                    "text": (f"按用户选择的默认假设，任务《{task.title}》需要 {minutes} 分钟"),
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
                        f"任务《{task.title}》没有明确截止日期；将按优先级安排，但不校验截止风险"
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
                locked_minutes=task.locked_minutes,
            )
        )

    if options.segment_minutes is not None:
        assumptions.append(
            {
                "kind": "segment",
                "value": options.segment_minutes,
                "text": f"按用户选择，每段学习时间不超过 {options.segment_minutes} 分钟",
            }
        )
    if options.buffer_minutes is not None:
        assumptions.append(
            {
                "kind": "buffer",
                "value": options.buffer_minutes,
                "text": f"按用户选择，在截止前预留 {options.buffer_minutes} 分钟提交缓冲",
            }
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


def _overlap(start_a: datetime, end_a: datetime, start_b: datetime, end_b: datetime) -> bool:
    return start_a < end_b and start_b < end_a


def detect_overlaps(
    inputs: PlanningInput, blocks: tuple[BlockSuggestion, ...]
) -> list[dict[str, Any]]:
    """检测固定日程之间、时间块与固定日程、时间块之间的重叠（P011）。

    重叠是确定的冲突，不按导入顺序或优先级悄悄忽略任何一方。
    """
    conflicts: list[dict[str, Any]] = []
    events = inputs.fixed_events
    for index, first in enumerate(events):
        for second in events[index + 1 :]:
            if _overlap(first.start, first.end, second.start, second.end):
                conflicts.append(
                    {
                        "type": "time_overlap",
                        "severity": "conflict",
                        "participants": [
                            {"kind": "fixed_event", "id": first.event_id, "title": first.title},
                            {"kind": "fixed_event", "id": second.event_id, "title": second.title},
                        ],
                        "starts_at": max(first.start, second.start).isoformat(),
                        "ends_at": min(first.end, second.end).isoformat(),
                        "message": f"固定日程《{first.title}》与《{second.title}》时间重叠",
                        "suggestion": "核对两个固定日程，确认是否需要调整其中一个",
                    }
                )
    for block in blocks:
        for event in events:
            if _overlap(block.starts_at, block.ends_at, event.start, event.end):
                conflicts.append(
                    {
                        "type": "time_overlap",
                        "severity": "conflict",
                        "participants": [
                            {"kind": "plan_block", "id": block.task_id, "title": block.title},
                            {"kind": "fixed_event", "id": event.event_id, "title": event.title},
                        ],
                        "starts_at": max(block.starts_at, event.start).isoformat(),
                        "ends_at": min(block.ends_at, event.end).isoformat(),
                        "message": f"学习时间块《{block.title}》占用了固定日程《{event.title}》",
                        "suggestion": "移动该时间块或调整固定日程",
                    }
                )
    ordered = sorted(blocks, key=lambda block: (block.starts_at, block.ends_at))
    for index, first in enumerate(ordered):
        for second in ordered[index + 1 :]:
            if not _overlap(first.starts_at, first.ends_at, second.starts_at, second.ends_at):
                continue
            conflicts.append(
                {
                    "type": "time_overlap",
                    "severity": "conflict",
                    "participants": [
                        {"kind": "plan_block", "id": first.task_id, "title": first.title},
                        {"kind": "plan_block", "id": second.task_id, "title": second.title},
                    ],
                    "starts_at": max(first.starts_at, second.starts_at).isoformat(),
                    "ends_at": min(first.ends_at, second.ends_at).isoformat(),
                    "message": f"时间块《{first.title}》与《{second.title}》时间重叠",
                    "suggestion": "移动其中一个时间块",
                }
            )
    return conflicts


def detect_risks(
    inputs: PlanningInput,
    resolved: ResolvedInput,
    outcome: PlanOutcome,
    blocks: tuple[BlockSuggestion, ...] | None = None,
) -> list[dict[str, Any]]:
    """截止与负荷风险（P012）：确定缺口与基于耗时估计的风险分开标注。"""
    risks: list[dict[str, Any]] = []
    tasks = {task.task_id: task for task in resolved.tasks}
    for disposition in outcome.dispositions:
        if disposition.remaining_minutes <= 0:
            continue
        task = tasks.get(disposition.task_id)
        if task is None or task.due_at is None:
            continue
        based_on_estimate = task.estimated
        risks.append(
            {
                "type": "deadline_risk",
                "severity": "risk" if based_on_estimate else "conflict",
                "task_id": task.task_id,
                "title": task.title,
                "shortfall_minutes": disposition.remaining_minutes,
                "due_at": task.due_at.isoformat(),
                "estimated": based_on_estimate,
                "message": (
                    f"按当前耗时估计，《{task.title}》还差 "
                    f"{disposition.remaining_minutes} 分钟才能完成"
                    if based_on_estimate
                    else f"剩余可用时间不足以完成《{task.title}》，"
                    f"还差 {disposition.remaining_minutes} 分钟"
                ),
                "suggestion": "提前开始、拆分任务或增加可用时间；必要时调整个人计划",
            }
        )

    planned = blocks if blocks is not None else outcome.blocks
    daily: dict[date, int] = {}
    weekly: dict[tuple[int, int], int] = {}
    for block in planned:
        day = block.starts_at.date()
        daily[day] = daily.get(day, 0) + block.minutes
        week = day.isocalendar()[:2]
        weekly[week] = weekly.get(week, 0) + block.minutes
    for day, minutes in sorted(daily.items()):
        if minutes > inputs.daily_capacity_minutes:
            risks.append(
                {
                    "type": "load_risk",
                    "severity": "risk",
                    "date": day.isoformat(),
                    "planned_minutes": minutes,
                    "capacity_minutes": inputs.daily_capacity_minutes,
                    "message": (
                        f"{day.isoformat()} 安排了 {minutes} 分钟，"
                        f"超过每日容量 {inputs.daily_capacity_minutes} 分钟"
                    ),
                    "suggestion": "将部分任务分散到其他日期",
                }
            )
    for (year, week), minutes in sorted(weekly.items()):
        if minutes > inputs.weekly_capacity_minutes:
            risks.append(
                {
                    "type": "load_risk",
                    "severity": "risk",
                    "iso_year": year,
                    "iso_week": week,
                    "planned_minutes": minutes,
                    "capacity_minutes": inputs.weekly_capacity_minutes,
                    "message": (
                        f"第 {year}-W{week:02d} 周安排了 {minutes} 分钟，"
                        f"超过每周容量 {inputs.weekly_capacity_minutes} 分钟"
                    ),
                    "suggestion": "减少本周安排或将任务移到下周",
                }
            )
    return risks


def _priority_label(priority: str) -> str:
    return {"high": "高", "medium": "中", "low": "低"}.get(priority, "普通")


def _block_reason(
    task: PlanningTask,
    *,
    window_start: datetime,
    deadline: datetime | None,
    buffer_minutes: int,
) -> str:
    parts: list[str] = []
    if deadline is not None:
        deadline_text = deadline.strftime("%Y-%m-%d %H:%M")
        if task.due_date_only:
            deadline_text = f"{deadline.date().isoformat()} 当天结束"
        parts.append(f"为满足 {deadline_text} 的截止要求")
        if buffer_minutes > 0:
            parts.append(f"截止前预留 {buffer_minutes} 分钟提交缓冲")
    else:
        parts.append(f"安排在 {window_start.strftime('%m-%d %H:%M')} 起的可用时间内")
    parts.append(f"按{_priority_label(task.priority)}优先级")
    if task.estimated:
        parts.append("耗时为估计值，存在排期风险")
    return "；".join(parts)


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


@dataclass(frozen=True)
class ExcludedTaskDisposition:
    task_id: int
    title: str
    status: str
    reason_code: str
    message: str
    options: tuple[str, ...]


@dataclass(frozen=True)
class PlanOutcome:
    blocks: tuple[BlockSuggestion, ...]
    dispositions: tuple[TaskDisposition, ...]
    excluded: tuple[ExcludedTaskDisposition, ...]
    gaps: tuple[dict[str, Any], ...]
    assumptions: tuple[dict[str, Any], ...]
    capacity_gap_minutes: int
    adjustment_options: tuple[str, ...]
    coverage: dict[str, Any]

    @property
    def all_task_ids(self) -> set[int]:
        return {item.task_id for item in self.dispositions} | {
            item.task_id for item in self.excluded
        }


def build_outcome(
    inputs: PlanningInput, resolved: ResolvedInput, allocation: AllocationResult
) -> PlanOutcome:
    """汇总未排原因、容量缺口与检查覆盖，保证每个输入任务都有去向（P008）。"""
    excluded = tuple(
        ExcludedTaskDisposition(
            task_id=task.task_id,
            title=task.title,
            status="excluded",
            reason_code="container_with_subtasks" if task.reason == "container" else task.progress,
            message=(
                "任务包含子任务，父任务本身不参与排期，避免与子任务重复计入"
                if task.reason == "container"
                else (
                    "任务已完成，不参与本次排期"
                    if task.progress == "done"
                    else "任务已取消，不参与本次排期"
                )
            ),
            options=(
                ("查看或调整子任务",)
                if task.reason == "container"
                else (("重新打开任务",) if task.progress == "cancelled" else ())
            ),
        )
        for task in inputs.excluded_tasks
    )

    missing_due = [task.title for task in resolved.tasks if task.due_at is None]
    missing_duration = [task.title for task in resolved.tasks if task.minutes is None]
    not_checked: list[str] = []
    if missing_due:
        not_checked.append("以下任务没有明确截止日期，未检查截止风险：" + "、".join(missing_due))
    if missing_duration:
        not_checked.append("以下任务缺少剩余耗时，未检查容量安排：" + "、".join(missing_duration))
    coverage = {
        "tasks_considered": len(resolved.tasks),
        "tasks_scheduled": sum(item.status == "scheduled" for item in allocation.dispositions),
        "tasks_partial": sum(item.status == "partial" for item in allocation.dispositions),
        "tasks_unscheduled": sum(item.status == "unscheduled" for item in allocation.dispositions),
        "tasks_excluded": len(excluded),
        "checks": [
            "deadline",
            "fixed_event_overlap",
            "daily_capacity",
            "weekly_capacity",
            *(["task_dependencies"] if inputs.dependencies else []),
        ],
        "not_checked": not_checked,
        "notes": [
            "未确认候选事项不参与排期；需先在待核对中确认后才会成为排期输入",
            "固定日程与可用时间均按工作空间时区解释",
            "必要依赖按完成时间约束后续任务；前置取消或无法排入时后续任务不安排",
        ],
        "capacity_gap_minutes": allocation.capacity_gap_minutes,
        "adjustment_options": [],
    }

    adjustment_options: tuple[str, ...] = ()
    if allocation.capacity_gap_minutes > 0:
        adjustment_options = (
            "增加每日或每周可用时间",
            "扩大规划范围或调整任务截止",
            "拆分任务或修改耗时估计",
            "接受缺口并保留未排任务",
        )
    coverage["adjustment_options"] = list(adjustment_options)

    return PlanOutcome(
        blocks=allocation.blocks,
        dispositions=allocation.dispositions,
        excluded=excluded,
        gaps=resolved.gaps,
        assumptions=resolved.assumptions,
        capacity_gap_minutes=allocation.capacity_gap_minutes,
        adjustment_options=adjustment_options,
        coverage=coverage,
    )


def _planned_edges(inputs: PlanningInput, task_ids: set[int]) -> list[tuple[int, int]]:
    return [
        (link.task_id, link.depends_on_task_id)
        for link in inputs.dependencies
        if link.state == "planned"
        and link.task_id in task_ids
        and link.depends_on_task_id in task_ids
    ]


def _topological_order(
    tasks: tuple[PlanningTask, ...], edges: list[tuple[int, int]]
) -> list[PlanningTask]:
    """按依赖先后排序；同一批可执行任务仍按截止与优先级排序，结果确定。"""
    by_id = {task.task_id: task for task in tasks}
    indegree = {task_id: 0 for task_id in by_id}
    outgoing: dict[int, list[int]] = {task_id: [] for task_id in by_id}
    for task_id, prerequisite in edges:
        indegree[task_id] += 1
        outgoing[prerequisite].append(task_id)
    ready = [task for task in tasks if indegree[task.task_id] == 0]
    order: list[PlanningTask] = []
    while ready:
        ready.sort(key=task_order_key)
        task = ready.pop(0)
        order.append(task)
        for dependent in outgoing[task.task_id]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(by_id[dependent])
    return order


def allocate_tasks(inputs: PlanningInput, resolved: ResolvedInput) -> AllocationResult:
    """按依赖、截止与可用时间分配任务；不越过确定截止，也不占用固定日程。

    依赖先于优先级：后续任务只能安排在前置任务完成之后。前置取消、缺失、
    越出范围或未能排入时，依赖不可满足，后续任务直接列为未排入。
    这里只处理硬约束；每日／每周容量、休息与分段在后续规则中叠加。
    """
    windows = [[window.start, window.end] for window in build_free_windows(inputs)]
    blocks: list[BlockSuggestion] = []
    dispositions: list[TaskDisposition] = []
    daily_used: dict[date, int] = {}
    weekly_used: dict[tuple[int, int], int] = {}
    day_last_end: dict[date, datetime] = {}

    by_id = {task.task_id: task for task in resolved.tasks}
    planned_edges = _planned_edges(inputs, set(by_id))
    cycle_edges: dict[int, set[int]] = {}
    for task_id, prerequisite in planned_edges:
        cycle_edges.setdefault(task_id, set()).add(prerequisite)
    cycle_members = {
        task_id for component in cyclic_components(cycle_edges) for task_id in component
    }
    unmet: dict[int, list[PlanningDependency]] = {}
    for link in inputs.dependencies:
        if link.task_id in by_id and link.state in {"cancelled", "out_of_scope", "missing"}:
            unmet.setdefault(link.task_id, []).append(link)

    order = _topological_order(resolved.tasks, planned_edges)
    ordered_ids = {task.task_id for task in order}
    prerequisite_links: dict[int, list[PlanningDependency]] = {}
    for link in inputs.dependencies:
        if link.state == "planned" and link.task_id in by_id:
            prerequisite_links.setdefault(link.task_id, []).append(link)

    completed: dict[int, datetime] = {}
    satisfied: dict[int, bool] = {}
    preserved_end: dict[int, datetime] = {}
    for block in inputs.preserved_blocks:
        if block.task_id is None:
            continue
        current_end = preserved_end.get(block.task_id)
        if current_end is None or block.end > current_end:
            preserved_end[block.task_id] = block.end

    def record_blocked(
        task: PlanningTask,
        *,
        reason_code: str,
        message: str,
        options: tuple[str, ...],
    ) -> None:
        satisfied[task.task_id] = False
        dispositions.append(
            TaskDisposition(
                task_id=task.task_id,
                title=task.title,
                status="unscheduled",
                scheduled_minutes=0,
                remaining_minutes=task.minutes or 0,
                reason_code=reason_code,
                message=message,
                options=options,
            )
        )

    def unmet_message(link: PlanningDependency) -> str:
        reasons = {
            "cancelled": "已取消",
            "out_of_scope": "不在当前规划范围",
            "missing": "已不存在",
        }
        return f"前置任务《{link.prerequisite_title}》{reasons[link.state]}，依赖无法满足"

    for task in order:
        if task.task_id in unmet:
            record_blocked(
                task,
                reason_code="dependency_unmet",
                message="；".join(unmet_message(link) for link in unmet[task.task_id]),
                options=("重新打开或恢复前置任务", "删除依赖", "调整任务"),
            )
            continue
        if task.minutes is None:
            satisfied[task.task_id] = False
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
            satisfied[task.task_id] = True
            completed[task.task_id] = inputs.now
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

        links = prerequisite_links.get(task.task_id, [])
        earliest_start: datetime | None = None
        blocked_by_unscheduled: PlanningDependency | None = None
        for link in links:
            if satisfied.get(link.depends_on_task_id) is False:
                blocked_by_unscheduled = link
                break
            prerequisite_end = completed.get(link.depends_on_task_id)
            if prerequisite_end is not None:
                earliest_start = (
                    prerequisite_end
                    if earliest_start is None
                    else max(earliest_start, prerequisite_end)
                )
        if blocked_by_unscheduled is not None:
            record_blocked(
                task,
                reason_code="dependency_unmet",
                message=(
                    f"前置任务《{blocked_by_unscheduled.prerequisite_title}》"
                    "未能排入本次计划，后续任务暂不安排"
                ),
                options=("增加可用时间", "调整前置任务截止", "接受未排入"),
            )
            continue
        if (
            earliest_start is not None
            and task.due_at is not None
            and earliest_start >= task.due_at - timedelta(minutes=resolved.buffer_minutes)
        ):
            record_blocked(
                task,
                reason_code="dependency_deadline",
                message="前置任务完成时间晚于本任务截止，无法在截止前开始",
                options=("调整前置任务时间", "调整本任务截止", "接受逾期"),
            )
            continue

        preserved_minutes = min(task.minutes, task.locked_minutes)
        remaining = task.minutes - preserved_minutes
        scheduled = preserved_minutes
        seq = 0
        deadline = task.due_at
        last_end: datetime | None = preserved_end.get(task.task_id)
        if remaining == 0:
            satisfied[task.task_id] = True
            completed[task.task_id] = last_end or inputs.now
            dispositions.append(
                TaskDisposition(
                    task_id=task.task_id,
                    title=task.title,
                    status="scheduled",
                    scheduled_minutes=preserved_minutes,
                    remaining_minutes=0,
                    reason_code=None,
                    message="已由保留的时间块完整覆盖",
                    options=(),
                )
            )
            continue
        for window in windows:
            if earliest_start is not None:
                if window[1] <= earliest_start:
                    continue
                if window[0] < earliest_start:
                    window[0] = earliest_start
            while remaining > 0:
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
                    break

                latest_end = window_end
                if deadline is not None:
                    latest_end = min(
                        window_end, deadline - timedelta(minutes=resolved.buffer_minutes)
                    )
                available = int((latest_end - window_start).total_seconds() // 60)
                if available <= 0:
                    window[0] = window_end
                    break
                minutes = min(
                    remaining,
                    available,
                    daily_remaining,
                    weekly_remaining,
                    resolved.segment_minutes,
                )
                if minutes <= 0:
                    window[0] = window_end
                    break
                block_end = window_start + timedelta(minutes=minutes)
                reason = _block_reason(
                    task,
                    window_start=window_start,
                    deadline=deadline,
                    buffer_minutes=resolved.buffer_minutes,
                )
                constraint_source: list[dict[str, Any]] = [
                    {
                        "kind": "availability",
                        "value": window_start.isoformat(),
                        "text": "使用工作空间可用时间",
                    },
                    {
                        "kind": "priority",
                        "value": task.priority,
                        "text": "按任务重要性与截止顺序安排",
                    },
                    *(
                        [
                            {
                                "kind": "dependency",
                                "value": [link.depends_on_task_id for link in links],
                                "text": "先完成前置任务："
                                + "、".join(link.prerequisite_title for link in links),
                            }
                        ]
                        if links
                        else []
                    ),
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
                constraint_source.append(
                    {
                        "kind": "segment",
                        "value": resolved.segment_minutes,
                        "text": "大任务分段安排，单段不超过设定时长",
                    }
                )
                if deadline is not None:
                    if resolved.buffer_minutes > 0:
                        constraint_source.append(
                            {
                                "kind": "buffer",
                                "value": resolved.buffer_minutes,
                                "text": "在截止前预留检查与提交缓冲",
                            }
                        )
                    constraint_source.append(
                        {
                            "kind": "deadline",
                            "value": deadline.isoformat(),
                            "date_only": task.due_date_only,
                            "text": "任务明确截止时间",
                        }
                    )
                if task.estimated:
                    constraint_source.append(
                        {
                            "kind": "estimated_duration",
                            "value": task.minutes,
                            "risk": True,
                            "text": "耗时为估计值，排期结果存在风险",
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
                last_end = block_end
                remaining -= minutes
                scheduled += minutes
                seq += 1
                daily_used[day] = daily_used.get(day, 0) + minutes
                weekly_used[week] = weekly_used.get(week, 0) + minutes
                day_last_end[day] = block_end
            if remaining <= 0:
                break

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
        satisfied[task.task_id] = remaining == 0
        if remaining == 0 and last_end is not None:
            completed[task.task_id] = last_end

    # 拓扑排序没有释放的任务：成环节点及其下游，均不能确定可执行顺序
    for task in resolved.tasks:
        if task.task_id in ordered_ids:
            continue
        if task.task_id in cycle_members:
            record_blocked(
                task,
                reason_code="dependency_cycle",
                message="任务位于依赖环中，不能确定可执行顺序",
                options=("删除其中一条依赖", "调整任务顺序"),
            )
        else:
            record_blocked(
                task,
                reason_code="dependency_unmet",
                message="前置任务位于依赖环中，后续任务无法确定开始时间",
                options=("删除其中一条依赖", "调整任务顺序"),
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
    busy.extend(Interval(block.start, block.end) for block in inputs.preserved_blocks)
    free = subtract_intervals(merge_intervals(availability), busy)
    floor = inputs.now
    clipped: list[Interval] = []
    for window in free:
        start = max(window.start, floor)
        if window.end > start:
            clipped.append(Interval(start, window.end))
    return [FreeWindow(start=window.start, end=window.end) for window in clipped]
