"""M5 排期输入汇总：确认任务、固定日程、可用时间与偏好（P002）。

原始数据只读；输入快照用于版本核对，不参与业务判断。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from campusflow.application import schedule as schedule_use_cases
from campusflow.application.courses import require_semester_in_workspace
from campusflow.application.ports.repositories import Repositories, TaskData
from campusflow.application.reminders import sync_reminders
from campusflow.application.settings import get_settings
from campusflow.domain.errors import ConflictError, DomainError, NotFoundError
from campusflow.domain.planning import (
    AssumptionOptions,
    AvailabilityRule,
    BlockSuggestion,
    ExcludedTask,
    FixedEventOccurrence,
    PlanningDependency,
    PlanningInput,
    PlanningTask,
    PlanOutcome,
    PreservedBlock,
    allocate_tasks,
    build_outcome,
    detect_overlaps,
    detect_risks,
    input_fingerprint,
    input_snapshot,
    resolve_assumptions,
)
from campusflow.domain.states import TaskProgress
from campusflow.domain.timezones import in_workspace_timezone

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
    preserved_blocks: tuple[PreservedBlock, ...] = (),
) -> CollectedPlanInput:
    if semester_id is not None:
        require_semester_in_workspace(repos, workspace_id, semester_id)
    settings = get_settings(repos, workspace_id)
    timezone = ZoneInfo(settings.timezone)
    moment = (now or datetime.now(UTC)).astimezone(timezone)

    raw_tasks: list[dict] = []
    tasks: list[PlanningTask] = []
    excluded: list[ExcludedTask] = []
    preserved_minutes: dict[int, int] = {}
    for block in preserved_blocks:
        if block.task_id is not None:
            preserved_minutes[block.task_id] = (
                preserved_minutes.get(block.task_id, 0) + block.minutes
            )
    scoped_tasks = repos.tasks.list(workspace_id, semester_id=semester_id)
    container_ids = {
        task.parent_task_id for task in scoped_tasks if task.parent_task_id is not None
    }
    for task in scoped_tasks:
        progress = TaskProgress(task.progress)
        if progress not in PLANNABLE_PROGRESS:
            excluded.append(
                ExcludedTask(task_id=task.id, title=task.title, progress=progress.value)
            )
            continue
        if task.id in container_ids:
            # A004：父任务只做汇总容器，子任务各自排期，避免父子耗时重复计入
            excluded.append(
                ExcludedTask(
                    task_id=task.id,
                    title=task.title,
                    progress=progress.value,
                    reason="container",
                )
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
                locked_minutes=preserved_minutes.get(task.id, 0),
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
                "parent_task_id": task.parent_task_id,
                "updated_at": task.updated_at.isoformat(),
            }
        )

    scope_ids = {task.id for task in repos.tasks.list(workspace_id, semester_id=semester_id)}
    all_tasks = {task.id: task for task in repos.tasks.list(workspace_id)}
    dependencies: list[PlanningDependency] = []
    for link in repos.tasks.list_workspace_dependencies(workspace_id):
        if link.task_id not in scope_ids:
            continue
        prerequisite = all_tasks.get(link.depends_on_task_id)
        if prerequisite is None:
            state = "missing"
            title = f"任务 {link.depends_on_task_id}"
        else:
            title = prerequisite.title
            if prerequisite.progress == TaskProgress.DONE:
                state = "done"
            elif prerequisite.progress == TaskProgress.CANCELLED:
                state = "cancelled"
            elif prerequisite.id not in scope_ids:
                state = "out_of_scope"
            else:
                state = "planned"
        dependencies.append(
            PlanningDependency(
                task_id=link.task_id,
                depends_on_task_id=link.depends_on_task_id,
                prerequisite_title=title,
                state=state,
            )
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
        dependencies=tuple(dependencies),
        preserved_blocks=preserved_blocks,
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
    preserved_blocks: tuple[PreservedBlock, ...] = (),
    change: str = "generated",
):
    """生成并保存两周草案（P002–P013）；未接受前不改变当前计划。

    A005：preserved_blocks 原样带入新草案并占用可用时间，对应任务只排剩余分钟。
    """
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
        preserved_blocks=preserved_blocks,
    )
    resolved = resolve_assumptions(collected.inputs, options or AssumptionOptions())
    allocation = allocate_tasks(collected.inputs, resolved)
    outcome: PlanOutcome = build_outcome(collected.inputs, resolved, allocation)
    preserved_blocks_suggestions = tuple(
        BlockSuggestion(
            task_id=block.task_id,
            title=block.title,
            starts_at=block.start,
            ends_at=block.end,
            minutes=block.minutes,
            seq=block.seq,
            reason=("保留已锁定安排" if block.locked else "保留不在本次重排范围的安排"),
            constraint_source=(
                {
                    "kind": "preserved",
                    "value": block.block_id,
                    "text": "重排时原样保留的时间块",
                },
            ),
            locked=block.locked,
        )
        for block in preserved_blocks
    )
    all_blocks = tuple(
        sorted(
            (*preserved_blocks_suggestions, *outcome.blocks),
            key=lambda block: (block.starts_at, block.seq),
        )
    )
    conflicts = detect_overlaps(collected.inputs, all_blocks)
    conflicts.extend(detect_risks(collected.inputs, resolved, outcome, all_blocks))
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
    blocks = repos.plans.create_blocks(draft.id, [_block_dict(block) for block in all_blocks])
    repos.plans.add_version(
        draft.id,
        version_no=draft.version,
        change=change,
        snapshot=_draft_snapshot(draft, blocks),
    )
    repos.uow.commit()
    draft.blocks = blocks
    return draft


def list_drafts(repos: Repositories, workspace_id: int, *, semester_id: int | None = None):
    return repos.plans.list_drafts(workspace_id, semester_id=semester_id)


def get_draft(repos: Repositories, workspace_id: int, draft_id: int):
    draft = repos.plans.get_draft(draft_id)
    if draft is None or draft.workspace_id != workspace_id:
        raise NotFoundError("计划草案不存在")
    return draft


def _preserved_blocks_from_snapshot(snapshot: dict) -> tuple[PreservedBlock, ...]:
    return tuple(
        PreservedBlock(
            block_id=int(item["block_id"]),
            task_id=item.get("task_id"),
            title=str(item["title"]),
            start=datetime.fromisoformat(item["starts_at"]),
            end=datetime.fromisoformat(item["ends_at"]),
            minutes=int(item["minutes"]),
            locked=bool(item.get("locked", False)),
        )
        for item in snapshot.get("preserved_blocks", [])
    )


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
        preserved_blocks=_preserved_blocks_from_snapshot(draft.input_snapshot),
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
    # 接受后才把最新安排落到提醒；未接受草案不触发重算
    sync_reminders(repos, workspace_id)
    repos.uow.commit()
    return repos.plans.get_draft(draft.id)


def current_plan(repos: Repositories, workspace_id: int, *, semester_id: int | None = None):
    return repos.plans.get_current(workspace_id, semester_id=semester_id)


def reschedule_plan(
    repos: Repositories,
    workspace_id: int,
    *,
    semester_id: int | None = None,
    range_start: date | None = None,
    range_end: date | None = None,
    task_ids: list[int] | None = None,
    now: datetime | None = None,
):
    """基于当前已接受计划做局部重排（A005）。

    锁定时间块始终原样保留；指定 task_ids 时，范围外任务的现有时间块也保留。
    其余任务在剩余可用时间内重新安排，结果仍是待接受草案。
    """
    if semester_id is not None:
        require_semester_in_workspace(repos, workspace_id, semester_id)
    else:
        semester_id = None
    current = current_plan(repos, workspace_id, semester_id=semester_id)
    if current is None:
        raise NotFoundError("当前没有已接受的计划，无法局部重排")
    effective_start = range_start or current.range_start
    effective_end = range_end or current.range_end
    if effective_end < effective_start:
        raise DomainError("规划结束日期不得早于开始日期")
    timezone = ZoneInfo(get_settings(repos, workspace_id).timezone)
    scope: set[int] | None = None
    if task_ids is not None:
        scope = set(task_ids)
        if not scope:
            raise DomainError("局部重排至少需要指定一个任务")
        for task_id in scope:
            task = repos.tasks.get(task_id)
            if task is None or task.workspace_id != workspace_id:
                raise NotFoundError("任务不存在")

    preserved: list[PreservedBlock] = []
    for block in current.blocks:
        block_start = block.starts_at.astimezone(timezone).date()
        block_end = block.ends_at.astimezone(timezone).date()
        outside_range = block_start < effective_start or block_end > effective_end
        keep = (
            block.locked
            or outside_range
            or (scope is not None and (block.task_id is None or block.task_id not in scope))
        )
        if keep:
            preserved.append(
                PreservedBlock(
                    block_id=block.id,
                    task_id=block.task_id,
                    title=block.title,
                    start=block.starts_at,
                    end=block.ends_at,
                    minutes=block.minutes,
                    locked=block.locked,
                )
            )

    return generate_draft(
        repos,
        workspace_id,
        semester_id=semester_id,
        range_start=effective_start,
        range_end=effective_end,
        now=now,
        preserved_blocks=tuple(preserved),
        change="rescheduled",
    )


_TASK_FIELDS = {
    "title": "标题变化",
    "due_date": "截止日期变化",
    "due_time": "截止时刻变化",
    "remaining_minutes": "剩余耗时变化",
    "estimated_minutes": "预计耗时变化",
    "priority": "优先级变化",
    "progress": "执行进度变化",
    "parent_task_id": "子任务归属变化",
    "target_date": "个人目标日期变化",
    "course_id": "课程归属变化",
}


def _snapshot_diff(old: dict, new: dict) -> tuple[list[dict], set[int], bool]:
    suggestions: list[dict] = []
    affected: set[int] = set()
    all_tasks = False

    old_tasks = {item["id"]: item for item in old.get("tasks", [])}
    new_tasks = {item["id"]: item for item in new.get("tasks", [])}
    for task_id in sorted(set(old_tasks) | set(new_tasks)):
        before = old_tasks.get(task_id)
        after = new_tasks.get(task_id)
        if before is None or after is None:
            affected.add(task_id)
            title = (after or before or {}).get("title", f"任务 {task_id}")
            suggestions.append(
                {
                    "code": "task_added" if before is None else "task_removed",
                    "task_id": task_id,
                    "message": (
                        f"新增任务《{title}》，需要排入"
                        if before is None
                        else f"任务《{title}》已不在当前排期范围"
                    ),
                }
            )
            continue
        changed = [name for name in _TASK_FIELDS if before.get(name) != after.get(name)]
        if not changed:
            continue
        affected.add(task_id)
        for name in changed:
            suggestions.append(
                {
                    "code": f"task_{name}_changed",
                    "task_id": task_id,
                    "field": name,
                    "message": (
                        f"任务《{after.get('title', task_id)}》{_TASK_FIELDS[name]}，"
                        "建议检查对应时间块"
                    ),
                }
            )

    if old.get("availability") != new.get("availability"):
        all_tasks = True
        suggestions.append(
            {
                "code": "availability_changed",
                "message": "可用时间变化，建议重新检查全部任务的时间块",
            }
        )
    if old.get("settings") != new.get("settings"):
        all_tasks = True
        suggestions.append(
            {
                "code": "settings_changed",
                "message": "每日/每周容量或休息偏好变化，建议重新检查负荷",
            }
        )
    if old.get("fixed_events") != new.get("fixed_events"):
        all_tasks = True
        suggestions.append(
            {
                "code": "fixed_events_changed",
                "message": "固定日程变化，建议重新检查时间冲突",
            }
        )
    if old.get("dependencies") != new.get("dependencies"):
        for item in new.get("dependencies", []):
            affected.add(item["task_id"])
        suggestions.append(
            {
                "code": "dependencies_changed",
                "message": "任务依赖变化，建议检查后续任务的开始时间",
            }
        )
    old_excluded = {item["id"]: item for item in old.get("excluded_tasks", [])}
    new_excluded = {item["id"]: item for item in new.get("excluded_tasks", [])}
    for task_id in sorted(set(old_excluded) | set(new_excluded)):
        if old_excluded.get(task_id) == new_excluded.get(task_id):
            continue
        affected.add(task_id)
        title = (new_excluded.get(task_id) or old_excluded.get(task_id) or {}).get(
            "title", f"任务 {task_id}"
        )
        suggestions.append(
            {
                "code": "excluded_state_changed",
                "task_id": task_id,
                "message": f"任务《{title}》是否参与排期发生变化",
            }
        )
    return suggestions, affected, all_tasks


def adjustment_suggestions(
    repos: Repositories, workspace_id: int, *, semester_id: int | None = None
) -> dict:
    """比较当前输入与已接受计划，只提出调整建议（A006）；不生成草案、不改计划。"""
    if semester_id is not None:
        require_semester_in_workspace(repos, workspace_id, semester_id)
    current = current_plan(repos, workspace_id, semester_id=semester_id)
    if current is None:
        return {
            "has_plan": False,
            "has_changes": False,
            "auto_applied": False,
            "requires_confirmation": True,
            "current_plan_id": None,
            "current_plan_version": None,
            "affected_task_ids": [],
            "affected_all_tasks": False,
            "suggested_task_ids": [],
            "suggestions": [],
            "message": "当前没有已接受的计划，无法比较变化",
        }

    snapshot = current.input_snapshot
    segment_minutes = int(snapshot.get("settings", {}).get("segment_minutes", 90))
    collected = collect_inputs(
        repos,
        workspace_id,
        semester_id=semester_id,
        range_start=current.range_start,
        range_end=current.range_end,
        now=datetime.now(UTC),
        segment_minutes=segment_minutes,
        preserved_blocks=_preserved_blocks_from_snapshot(snapshot),
    )
    has_changes = collected.fingerprint != current.input_fingerprint
    suggestions, affected, all_tasks = _snapshot_diff(snapshot, collected.snapshot)
    if has_changes and not suggestions:
        suggestions.append(
            {
                "code": "input_changed",
                "message": "排期输入已变化，建议生成调整草案后核对",
            }
        )
    elif not has_changes:
        suggestions = []
        affected.clear()
        all_tasks = False
    suggested = [] if all_tasks else sorted(affected)
    return {
        "has_plan": True,
        "has_changes": has_changes,
        "auto_applied": False,
        "requires_confirmation": True,
        "current_plan_id": current.id,
        "current_plan_version": current.version,
        "affected_task_ids": sorted(affected),
        "affected_all_tasks": all_tasks,
        "suggested_task_ids": suggested,
        "suggestions": suggestions,
        "message": (
            "检测到排期输入变化；是否生成局部草案由你决定，当前计划尚未修改"
            if has_changes
            else "当前输入与已接受计划一致，无需调整"
        ),
    }


def create_adjustment_draft(
    repos: Repositories,
    workspace_id: int,
    *,
    semester_id: int | None = None,
    task_ids: list[int] | None = None,
    now: datetime | None = None,
):
    """用户显式确认后才生成调整草案（A006）；仍不影响当前计划直到接受。"""
    summary = adjustment_suggestions(repos, workspace_id, semester_id=semester_id)
    if not summary["has_plan"]:
        raise NotFoundError("当前没有已接受的计划，无法生成调整草案")
    if not summary["has_changes"]:
        raise ConflictError("当前输入与已接受计划一致，无需生成调整草案")
    scope = task_ids
    if scope is None:
        scope = None if summary["affected_all_tasks"] else (summary["suggested_task_ids"] or None)
    return reschedule_plan(
        repos,
        workspace_id,
        semester_id=semester_id,
        task_ids=scope,
        now=now,
    )


def draft_conflicts(repos: Repositories, workspace_id: int, draft_id: int) -> list[dict]:
    return get_draft(repos, workspace_id, draft_id).conflicts


def overview(
    repos: Repositories,
    workspace_id: int,
    *,
    semester_id: int | None = None,
    now: datetime | None = None,
) -> dict:
    """近期任务与今日安排总览（P018）；只读，不修改计划。"""
    from campusflow.application import tasks as task_use_cases
    from campusflow.domain.states import ReviewStatus

    settings = get_settings(repos, workspace_id)
    timezone = ZoneInfo(settings.timezone)
    moment = (now or datetime.now(UTC)).astimezone(timezone)
    today = moment.date()
    current = current_plan(repos, workspace_id, semester_id=semester_id)

    today_blocks = []
    next_block = None
    if current is not None:
        for block in current.blocks:
            local_start = block.starts_at.astimezone(timezone)
            local_end = block.ends_at.astimezone(timezone)
            if local_start.date() != today:
                continue
            item = {
                "id": block.id,
                "title": block.title,
                "task_id": block.task_id,
                "starts_at": local_start.isoformat(),
                "ends_at": local_end.isoformat(),
                "minutes": block.minutes,
                "locked": block.locked,
                "reason": block.reason,
            }
            today_blocks.append(item)
            if local_start >= moment and next_block is None:
                next_block = item
        today_blocks.sort(key=lambda item: item["starts_at"])

    horizon = today + timedelta(days=7)
    tasks = task_use_cases.list_tasks(repos, workspace_id, semester_id=semester_id)
    active = [
        task
        for task in tasks
        if task.progress.value not in {"done", "cancelled"}
        and task.due_date is not None
        and task.due_date <= horizon
    ]
    active.sort(key=lambda task: (task.due_date or date.max, task.id))
    upcoming = [
        {
            "id": task.id,
            "title": task.title,
            "due_date": task.due_date.isoformat() if task.due_date else None,
            "due_time": task.due_time.isoformat() if task.due_time else None,
            "priority": task.priority.value,
            "progress": task.progress.value,
            "overdue": task.overdue,
        }
        for task in active[:8]
    ]

    pending_reviews = len(
        repos.candidates.list_for_review(
            workspace_id, review_statuses=(ReviewStatus.PENDING,), semester_id=semester_id
        )
    )
    unscheduled = 0
    if current is not None:
        unscheduled = sum(
            1 for item in current.unscheduled if item.get("status") in {"partial", "unscheduled"}
        )
    return {
        "today": today.isoformat(),
        "timezone": settings.timezone,
        "has_plan": current is not None,
        "plan_status": current.status if current is not None else None,
        "plan_version": current.version if current is not None else None,
        "today_blocks": today_blocks,
        "next_block": next_block,
        "upcoming_tasks": upcoming,
        "pending_review_count": pending_reviews,
        "unscheduled_count": unscheduled,
        "conflict_count": len(current.conflicts) if current is not None else 0,
    }


def _snapshot_with_block(draft, target: dict) -> dict:
    snapshot = _draft_snapshot(draft, draft.blocks)
    snapshot["blocks"] = [
        {
            "id": block.id,
            "task_id": block.task_id,
            "title": block.title,
            "starts_at": (
                target["starts_at"].isoformat()
                if block.id == target["id"]
                else block.starts_at.isoformat()
            ),
            "ends_at": (
                target["ends_at"].isoformat()
                if block.id == target["id"]
                else block.ends_at.isoformat()
            ),
            "minutes": target["minutes"] if block.id == target["id"] else block.minutes,
            "locked": target["locked"] if block.id == target["id"] else block.locked,
        }
        for block in draft.blocks
    ]
    return snapshot


def _require_adjustable_block(repos: Repositories, workspace_id: int, block_id: int):
    block = repos.plans.get_block(block_id)
    if block is None:
        raise NotFoundError("时间块不存在")
    draft = get_draft(repos, workspace_id, block.draft_id)
    if draft.status != "accepted":
        raise DomainError("只有已接受计划中的时间块可以调整")
    return block, draft


def move_block(
    repos: Repositories,
    workspace_id: int,
    block_id: int,
    *,
    starts_at: datetime,
    ends_at: datetime,
    expected_version: int,
    now: datetime | None = None,
):
    """移动已接受计划的时间块：不改变任务 DDL，不占用固定日程或已有时间块（P014）。"""
    block, draft = _require_adjustable_block(repos, workspace_id, block_id)
    settings = get_settings(repos, workspace_id)
    timezone = ZoneInfo(settings.timezone)
    starts_at = in_workspace_timezone(starts_at, timezone)
    ends_at = in_workspace_timezone(ends_at, timezone)
    if ends_at <= starts_at:
        raise DomainError("结束时间必须晚于开始时间")
    if now is not None and ends_at <= now:
        raise DomainError("不能把时间块移动到已经过去的时段")

    range_start = datetime.combine(draft.range_start, time.min, tzinfo=timezone)
    range_end = datetime.combine(draft.range_end + timedelta(days=1), time.min, tzinfo=timezone)
    if starts_at < range_start or ends_at > range_end:
        raise DomainError("时间块必须保留在计划范围内")

    minutes = int((ends_at - starts_at).total_seconds() // 60)
    for other in draft.blocks:
        if other.id == block.id:
            continue
        if starts_at < other.ends_at and other.starts_at < ends_at:
            raise DomainError("时间块与已安排时间块重叠，请调整后再保存")
    events = schedule_use_cases.list_fixed_event_occurrences(
        repos, workspace_id, draft.range_start, draft.range_end, semester_id=draft.semester_id
    )
    for event in events:
        if starts_at < event.ends_at and event.starts_at < ends_at:
            raise DomainError("时间块与固定日程重叠，请调整后再保存")

    snapshot = _snapshot_with_block(
        draft,
        {
            "id": block.id,
            "starts_at": starts_at,
            "ends_at": ends_at,
            "minutes": minutes,
            "locked": block.locked,
        },
    )
    updated = repos.plans.bump_version(
        draft.id,
        expected_version=expected_version,
        change="moved",
        snapshot=snapshot,
    )
    if updated is None:
        raise ConflictError("计划版本已更新，请刷新后重试")
    reason = (
        f"{block.reason}；用户手动移动到 {starts_at.astimezone(timezone).strftime('%m-%d %H:%M')}"
    )
    repos.plans.update_block(
        block.id,
        starts_at=starts_at,
        ends_at=ends_at,
        minutes=minutes,
        locked=block.locked,
        reason=reason,
    )
    repos.uow.commit()
    return repos.plans.get_draft(draft.id)


def set_block_lock(
    repos: Repositories,
    workspace_id: int,
    block_id: int,
    *,
    locked: bool,
    expected_version: int,
):
    """锁定或解锁已接受计划的时间块（P014）。"""
    block, draft = _require_adjustable_block(repos, workspace_id, block_id)
    snapshot = _snapshot_with_block(
        draft,
        {
            "id": block.id,
            "starts_at": block.starts_at,
            "ends_at": block.ends_at,
            "minutes": block.minutes,
            "locked": locked,
        },
    )
    updated = repos.plans.bump_version(
        draft.id,
        expected_version=expected_version,
        change="locked" if locked else "unlocked",
        snapshot=snapshot,
    )
    if updated is None:
        raise ConflictError("计划版本已更新，请刷新后重试")
    repos.plans.update_block(
        block.id,
        starts_at=block.starts_at,
        ends_at=block.ends_at,
        minutes=block.minutes,
        locked=locked,
        reason=block.reason,
    )
    repos.uow.commit()
    return repos.plans.get_draft(draft.id)
