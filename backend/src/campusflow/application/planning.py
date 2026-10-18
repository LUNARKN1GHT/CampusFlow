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
from campusflow.domain.errors import ConflictError, DomainError, NotFoundError
from campusflow.domain.planning import (
    AssumptionOptions,
    AvailabilityRule,
    ExcludedTask,
    FixedEventOccurrence,
    PlanningInput,
    PlanningTask,
    PlanOutcome,
    allocate_tasks,
    build_outcome,
    detect_overlaps,
    detect_risks,
    input_fingerprint,
    input_snapshot,
    resolve_assumptions,
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


def _disposition_dict(item) -> dict:
    return {
        "task_id": item.task_id,
        "title": item.title,
        "status": item.status,
        "scheduled_minutes": item.scheduled_minutes,
        "remaining_minutes": item.remaining_minutes,
        "reason_code": item.reason_code,
        "message": item.message,
        "options": list(item.options),
    }


def _excluded_dict(item) -> dict:
    return {
        "task_id": item.task_id,
        "title": item.title,
        "status": item.status,
        "scheduled_minutes": 0,
        "remaining_minutes": 0,
        "reason_code": item.reason_code,
        "message": item.message,
        "options": list(item.options),
    }


def _block_dict(block) -> dict:
    return {
        "task_id": block.task_id,
        "title": block.title,
        "kind": block.kind,
        "starts_at": block.starts_at,
        "ends_at": block.ends_at,
        "minutes": block.minutes,
        "seq": block.seq,
        "locked": block.locked,
        "reason": block.reason,
        "constraint_source": list(block.constraint_source),
    }


def _draft_snapshot(draft, blocks: list) -> dict:
    return {
        "draft_id": draft.id,
        "version": draft.version,
        "status": draft.status,
        "range_start": draft.range_start.isoformat(),
        "range_end": draft.range_end.isoformat(),
        "input_fingerprint": draft.input_fingerprint,
        "assumptions": draft.assumptions,
        "gaps": draft.gaps,
        "unscheduled": draft.unscheduled,
        "conflicts": draft.conflicts,
        "coverage": draft.coverage,
        "blocks": [
            {
                "id": block.id,
                "task_id": block.task_id,
                "title": block.title,
                "starts_at": block.starts_at.isoformat(),
                "ends_at": block.ends_at.isoformat(),
                "minutes": block.minutes,
                "locked": block.locked,
            }
            for block in blocks
        ],
    }


def generate_draft(
    repos: Repositories,
    workspace_id: int,
    *,
    semester_id: int | None,
    range_start: date,
    range_end: date,
    options: AssumptionOptions | None = None,
    now: datetime | None = None,
):
    """生成并保存两周草案（P002–P013）；未接受前不改变当前计划。"""
    if range_end < range_start:
        raise DomainError("规划结束日期不得早于开始日期")
    collected = collect_inputs(
        repos,
        workspace_id,
        semester_id=semester_id,
        range_start=range_start,
        range_end=range_end,
        now=now,
        segment_minutes=(options.segment_minutes if options and options.segment_minutes else 90),
    )
    resolved = resolve_assumptions(collected.inputs, options or AssumptionOptions())
    allocation = allocate_tasks(collected.inputs, resolved)
    outcome: PlanOutcome = build_outcome(collected.inputs, resolved, allocation)
    conflicts = detect_overlaps(collected.inputs, outcome.blocks)
    conflicts.extend(detect_risks(collected.inputs, resolved, outcome))
    unscheduled = [
        _disposition_dict(item) for item in outcome.dispositions if item.status != "scheduled"
    ] + [_excluded_dict(item) for item in outcome.excluded]

    draft = repos.plans.create_draft(
        workspace_id,
        semester_id=semester_id,
        range_start=range_start,
        range_end=range_end,
        input_snapshot=collected.snapshot,
        input_fingerprint=collected.fingerprint,
        assumptions=list(outcome.assumptions),
        gaps=list(outcome.gaps),
        unscheduled=unscheduled,
        conflicts=conflicts,
        coverage=outcome.coverage,
    )
    blocks = repos.plans.create_blocks(
        draft.id, [_block_dict(block) for block in outcome.blocks]
    )
    repos.plans.add_version(
        draft.id,
        version_no=draft.version,
        change="generated",
        snapshot=_draft_snapshot(draft, blocks),
    )
    repos.uow.commit()
    draft.blocks = blocks
    return draft


def list_drafts(
    repos: Repositories, workspace_id: int, *, semester_id: int | None = None
):
    return repos.plans.list_drafts(workspace_id, semester_id=semester_id)


def get_draft(repos: Repositories, workspace_id: int, draft_id: int):
    draft = repos.plans.get_draft(draft_id)
    if draft is None or draft.workspace_id != workspace_id:
        raise NotFoundError("计划草案不存在")
    return draft


def accept_draft(
    repos: Repositories,
    workspace_id: int,
    draft_id: int,
    *,
    expected_version: int,
    now: datetime | None = None,
):
    """接受草案：校验输入版本和草案版本，过期请求不覆盖较新计划（P013）。"""
    draft = get_draft(repos, workspace_id, draft_id)
    if draft.status != "draft":
        raise ConflictError("只有待接受状态的草案可以接受")
    segment_minutes = int(draft.input_snapshot.get("settings", {}).get("segment_minutes", 90))
    collected = collect_inputs(
        repos,
        workspace_id,
        semester_id=draft.semester_id,
        range_start=draft.range_start,
        range_end=draft.range_end,
        now=now or datetime.now(UTC),
        segment_minutes=segment_minutes,
    )
    if collected.fingerprint != draft.input_fingerprint:
        raise ConflictError("排期输入已变化，请重新生成草案后再接受")
    current = repos.plans.get_current(workspace_id, semester_id=draft.semester_id)
    if (
        current is not None
        and current.id != draft.id
        and current.accepted_at is not None
        and current.accepted_at > draft.created_at
    ):
        raise ConflictError("已有较新的已接受计划，请重新生成草案后再接受")

    blocks = repos.plans.list_blocks(draft.id)
    accepted = repos.plans.bump_version(
        draft.id,
        expected_version=expected_version,
        change="accepted",
        snapshot=_draft_snapshot(draft, blocks),
        status="accepted",
        accepted_at=now or datetime.now(UTC),
    )
    if accepted is None:
        raise ConflictError("草案版本已更新，请刷新后重试")
    repos.plans.supersede_others(
        workspace_id, semester_id=draft.semester_id, keep_draft_id=draft.id
    )
    repos.uow.commit()
    return repos.plans.get_draft(draft.id)


def current_plan(repos: Repositories, workspace_id: int, *, semester_id: int | None = None):
    return repos.plans.get_current(workspace_id, semester_id=semester_id)


def draft_conflicts(repos: Repositories, workspace_id: int, draft_id: int) -> list[dict]:
    return get_draft(repos, workspace_id, draft_id).conflicts
