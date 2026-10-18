"""M5 排期输入汇总：确认任务、固定日程、可用时间与偏好（P002）。

原始数据只读；输入快照用于版本核对，不参与业务判断。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from campusflow.application import schedule as schedule_use_cases
from campusflow.application.courses import require_semester_in_workspace
from campusflow.application.ports.repositories import Repositories, TaskData
from campusflow.application.settings import get_settings
from campusflow.domain.planning import (
    AvailabilityRule,
    ExcludedTask,
    FixedEventOccurrence,
    PlanningInput,
    PlanningTask,
    input_fingerprint,
    input_snapshot,
)
from campusflow.domain.states import TaskProgress

PLANNABLE_PROGRESS = (
    TaskProgress.NOT_STARTED,
    TaskProgress.IN_PROGRESS,
    TaskProgress.BLOCKED,
)


@dataclass
class CollectedPlanInput:
    inputs: PlanningInput
    snapshot: dict
    fingerprint: str


def _due_at(task: TaskData, timezone: ZoneInfo) -> tuple[datetime | None, bool]:
    if task.due_date is None:
        return None, False
    if task.due_time is not None:
        return datetime.combine(task.due_date, task.due_time, tzinfo=timezone), False
    # 只有日期的 DDL 不补造具体时刻；排期时按当天结束作为上限，并单独标注。
    return datetime.combine(task.due_date, time.max, tzinfo=timezone), True


def _duration(task: TaskData) -> tuple[int | None, bool]:
    if task.remaining_minutes is not None:
        return task.remaining_minutes, False
    if task.estimated_minutes is not None:
        return task.estimated_minutes, True
    return None, False


def collect_inputs(
    repos: Repositories,
    workspace_id: int,
    *,
    semester_id: int | None,
    range_start: date,
    range_end: date,
    now: datetime | None = None,
    segment_minutes: int = 90,
) -> CollectedPlanInput:
    if semester_id is not None:
        require_semester_in_workspace(repos, workspace_id, semester_id)
    settings = get_settings(repos, workspace_id)
    timezone = ZoneInfo(settings.timezone)
    moment = (now or datetime.now(UTC)).astimezone(timezone)

    raw_tasks: list[dict] = []
    tasks: list[PlanningTask] = []
    excluded: list[ExcludedTask] = []
    for task in repos.tasks.list(workspace_id, semester_id=semester_id):
        progress = TaskProgress(task.progress)
        if progress not in PLANNABLE_PROGRESS:
            excluded.append(
                ExcludedTask(task_id=task.id, title=task.title, progress=progress.value)
            )
            continue
        due_at, date_only = _due_at(task, timezone)
        minutes, estimated = _duration(task)
        tasks.append(
            PlanningTask(
                task_id=task.id,
                title=task.title,
                course_id=task.course_id,
                priority=task.priority.value,
                minutes=minutes,
                due_at=due_at,
                due_date_only=date_only,
                target_date=task.target_date,
                estimated=estimated,
            )
        )
        raw_tasks.append(
            {
                "id": task.id,
                "title": task.title,
                "course_id": task.course_id,
                "priority": task.priority.value,
                "progress": progress.value,
                "estimated_minutes": task.estimated_minutes,
                "remaining_minutes": task.remaining_minutes,
                "due_date": task.due_date.isoformat() if task.due_date else None,
                "due_time": task.due_time.isoformat() if task.due_time else None,
                "target_date": task.target_date.isoformat() if task.target_date else None,
                "updated_at": task.updated_at.isoformat(),
            }
        )

    events = schedule_use_cases.list_fixed_event_occurrences(
        repos, workspace_id, range_start, range_end, semester_id=semester_id
    )
    fixed_events = tuple(
        FixedEventOccurrence(
            event_id=event.source_event_id,
            title=event.title,
            start=event.starts_at,
            end=event.ends_at,
        )
        for event in events
    )
    availability = tuple(
        AvailabilityRule(
            day_of_week=slot.day_of_week,
            start_time=slot.start_time,
            end_time=slot.end_time,
        )
        for slot in repos.schedule.list_availability_slots(workspace_id)
    )
    inputs = PlanningInput(
        timezone=settings.timezone,
        range_start=range_start,
        range_end=range_end,
        now=moment,
        tasks=tuple(tasks),
        excluded_tasks=tuple(excluded),
        fixed_events=fixed_events,
        availability=availability,
        daily_capacity_minutes=settings.daily_capacity_minutes,
        weekly_capacity_minutes=settings.weekly_capacity_minutes,
        break_minutes=settings.break_minutes,
        buffer_minutes=settings.buffer_minutes,
        segment_minutes=segment_minutes,
    )
    snapshot = input_snapshot(inputs, raw_tasks)
    return CollectedPlanInput(
        inputs=inputs, snapshot=snapshot, fingerprint=input_fingerprint(snapshot)
    )
