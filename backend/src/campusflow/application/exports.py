"""A018／A019：任务清单 CSV 与日历草案 ICS 导出（范围、预览与下载绑定）。

导出范围完全由用户显式给出的 ``task_ids``（任务模式）或 ``plan_id``
（计划模式）决定，不回退为"全部任务"，也不读取候选事项或草稿计划。
范围、字段和隐私边界见 docs/export-scope.md（A017）。ICS 只写 UTC 时间点
或全天日期，不补造时刻、不写描述与原文；计划模式不接受草案或被替代计划。
计划模式的 CSV 清单留待后续任务，当前明确拒绝，不生成空文件。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from campusflow.application import schedule as schedule_use_cases
from campusflow.application.ports.repositories import (
    PlanBlockData,
    PlanDraftData,
    Repositories,
    TaskData,
    WorkspaceData,
)
from campusflow.domain.errors import ConflictError, DomainError, NotFoundError
from campusflow.domain.materials import SOURCE_STATE_INVALIDATED, source_state
from campusflow.domain.source_detail import citation_is_verifiable
from campusflow.domain.states import EventRecurrence, TaskPriority, TaskProgress
from campusflow.domain.timezones import in_workspace_timezone

# 字段定义见 docs/export-scope.md §3.1；表头固定，空值留空而不写 null。
EXPORT_VERSION = "1"
CSV_COLUMNS = (
    "export_version",
    "task_id",
    "course_id",
    "course_name",
    "title",
    "due_date",
    "due_time",
    "due_precision",
    "due_source",
    "target_date",
    "priority",
    "progress",
    "estimated_minutes",
    "remaining_minutes",
    "source_state",
    "source_title",
    "source_version_no",
    "source_locator",
)

_PRIORITY_RANK = {
    TaskPriority.HIGH: 0,
    TaskPriority.MEDIUM: 1,
    TaskPriority.LOW: 2,
}

_CSV_MEDIA_TYPE = "text/csv; charset=utf-8"
_ICS_MEDIA_TYPE = "text/calendar; charset=utf-8"
_ICS_PRODID = "-//CampusFlow//Calendar Export 1//ZH"
# 带时刻的截止只写 15 分钟显示窗口，不表示耗时，也不延长截止（A017 §3.4）。
_DUE_DISPLAY_MINUTES = 15
_ICS_MAX_OCTETS = 75


@dataclass(frozen=True)
class ExportRequest:
    """一次导出的完整范围；预览与下载必须携带同一份范围。"""

    mode: str
    format: str
    task_ids: tuple[int, ...] = ()
    plan_id: int | None = None
    include_source_hints: bool = False
    include_fixed_events: bool = False
    scope_fingerprint: str | None = None


@dataclass(frozen=True)
class ExportItem:
    """预览与 CSV 共用的一行；不含描述、截止备注或原文引用。"""

    task_id: int
    title: str
    course_id: int | None
    course_name: str | None
    due_date: date | None
    due_time: time | None
    due_precision: str
    due_source: str
    target_date: date | None
    priority: str
    progress: str
    estimated_minutes: int | None
    remaining_minutes: int | None
    source_state: str
    source_title: str | None = None
    source_version_no: int | None = None
    source_locator: str | None = None
    included: bool = True
    exclusion_reason: str | None = None


@dataclass(frozen=True)
class ExportEvent:
    """一个待序列化的 VEVENT；时间点一律为 UTC，全天日期保持浮动 DATE。"""

    uid: str
    summary: str
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    all_day_start: date | None = None
    all_day_end: date | None = None
    transp: str = "OPAQUE"
    location: str | None = None
    sequence: int | None = None
    last_modified: datetime | None = None
    properties: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ExportPreview:
    mode: str
    format: str
    timezone: str
    task_ids: tuple[int, ...]
    item_count: int
    row_count: int
    date_start: date | None
    date_end: date | None
    has_date_only: bool
    plan_id: int | None = None
    plan_version: int | None = None
    semester_id: int | None = None
    range_start: date | None = None
    range_end: date | None = None
    event_count: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    excluded: tuple[dict[str, object], ...] = ()
    warnings: tuple[dict[str, object], ...] = ()
    items: tuple[ExportItem, ...] = ()
    events: tuple[ExportEvent, ...] = ()
    scope_fingerprint: str = ""
    can_download: bool = False


@dataclass(frozen=True)
class ExportFile:
    filename: str
    content: bytes
    media_type: str


@dataclass(frozen=True)
class _SourceHint:
    state: str
    title: str | None = None
    version_no: int | None = None
    locator: str | None = None


@dataclass(frozen=True)
class _PlanExport:
    events: tuple[ExportEvent, ...]
    counts: dict[str, int]
    excluded: tuple[dict[str, object], ...]
    warnings: tuple[dict[str, object], ...]
    fingerprint: str


def _iso(value: date | time | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _utc_stamp(value: datetime) -> str:
    return _utc(value).strftime("%Y%m%dT%H%M%SZ")


def _format_due_time(value: time) -> str:
    """只有存在时刻才写；秒为 0 时按 HH:MM，保留真实存在的秒。"""
    if value.microsecond:
        return value.isoformat()
    if value.second:
        return value.strftime("%H:%M:%S")
    return value.strftime("%H:%M")


def _due_precision(task: TaskData) -> str:
    if task.due_date is None:
        return "none"
    return "date" if task.due_time is None else "datetime"


def _require_workspace_timezone(repos: Repositories, workspace_id: int) -> WorkspaceData:
    workspace = repos.workspaces.get(workspace_id)
    if workspace is None:
        raise NotFoundError("工作空间不存在")
    try:
        ZoneInfo(workspace.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        # 导出不回落 UTC：时区不可读时拒绝生成文件（A017 §6）。
        raise DomainError("工作空间时区无法读取，已停止导出") from exc
    return workspace


def _source_hint(repos: Repositories, task: TaskData) -> _SourceHint:
    """解析任务的来源提示；只有仍可核对的来源才给出标题、版本与定位。

    来源已删除、归档或引用无法逐字定位时统一标为 invalidated，且整体省略
    来源提示，不能输出旧标题或脱离原文的定位（A017 §2）。
    """
    if task.source_material_id is None:
        state = source_state(task.source_material_id, task.source_invalidated_at)
        return _SourceHint(state)
    material = repos.materials.get(task.source_material_id)
    if material is None or material.archived or material.workspace_id != task.workspace_id:
        return _SourceHint(SOURCE_STATE_INVALIDATED)
    if task.source_candidate_id is None:
        # 有来源资料但没有候选版本：只保留标题，版本与定位留空，不猜测。
        return _SourceHint("available", material.title)
    candidate = repos.candidates.get(task.workspace_id, task.source_candidate_id)
    if candidate is None:
        return _SourceHint(SOURCE_STATE_INVALIDATED)
    version = repos.materials.get_version(candidate.source_version_id)
    if version is None or version.material_id != material.id:
        return _SourceHint(SOURCE_STATE_INVALIDATED)
    chunks = {chunk.id: chunk for chunk in repos.materials.list_chunks(version.id)}
    locator: str | None = None
    unverifiable = False
    for candidate_field in candidate.draft.fields:
        for evidence in candidate_field.evidence:
            chunk = chunks.get(evidence.chunk_id)
            if chunk is not None and citation_is_verifiable(chunk.text, evidence.quote):
                if locator is None:
                    locator = f"{chunk.locator_type}:{chunk.locator_value}"
            else:
                unverifiable = True
    if locator is None and unverifiable:
        return _SourceHint(SOURCE_STATE_INVALIDATED)
    return _SourceHint("available", material.title, version.version_no, locator)


def _source_hint_text(hint: _SourceHint) -> str | None:
    """提示属性只到标题、版本号和定位，绝不写入原文引文（A017 §3.3）。"""
    if hint.title is None:
        return None
    text = hint.title
    if hint.version_no is not None:
        text += f" v{hint.version_no}"
    if hint.locator is not None:
        text += f" · {hint.locator}"
    return text


def _validate_supported(request: ExportRequest) -> None:
    if request.mode == "tasks":
        if request.plan_id is not None:
            raise DomainError("任务清单导出不接受计划标识")
        if not request.task_ids:
            raise DomainError("请显式选择要导出的任务，不提供整空间导出")
        if request.format not in {"csv", "ics"}:
            raise DomainError("当前仅支持 CSV 任务清单与 ICS 日历导出")
        return
    if request.mode == "plan":
        if request.task_ids:
            raise DomainError("计划导出不接受任务标识，请只提供计划标识")
        if request.plan_id is None:
            raise DomainError("请指定要导出的已接受计划，不自动选择计划")
        if request.format == "csv":
            raise DomainError("计划清单导出由后续任务提供")
        if request.format != "ics":
            raise DomainError("当前仅支持 ICS 日历导出")
        return
    raise DomainError("不支持的导出模式")


def _load_tasks(
    repos: Repositories, workspace_id: int, task_ids: tuple[int, ...]
) -> tuple[list[TaskData], tuple[int, ...]]:
    """去重并按 ID 稳定排序；跨空间或不存在的 ID 直接 404，不静默跳过。"""
    unique_ids = tuple(sorted({task_id for task_id in task_ids}))
    tasks: list[TaskData] = []
    for task_id in unique_ids:
        task = repos.tasks.get(task_id)
        if task is None or task.workspace_id != workspace_id:
            raise NotFoundError("任务不存在或不属于当前工作空间")
        tasks.append(task)
    return tasks, unique_ids


def _build_items(
    repos: Repositories,
    workspace_id: int,
    tasks: list[TaskData],
    *,
    include_source_hints: bool,
) -> list[ExportItem]:
    course_names: dict[int, str | None] = {}
    items: list[ExportItem] = []
    for task in tasks:
        course_name: str | None = None
        if task.course_id is not None:
            if task.course_id not in course_names:
                course = repos.courses.get(task.course_id)
                course_names[task.course_id] = (
                    course.name
                    if course is not None and course.workspace_id == workspace_id
                    else None
                )
            course_name = course_names[task.course_id]
        hint = _source_hint(repos, task)
        cancelled = task.progress == TaskProgress.CANCELLED
        items.append(
            ExportItem(
                task_id=task.id,
                title=task.title,
                course_id=task.course_id,
                course_name=course_name,
                due_date=task.due_date,
                due_time=task.due_time,
                due_precision=_due_precision(task),
                due_source=task.due_source.value,
                target_date=task.target_date,
                priority=task.priority.value,
                progress=task.progress.value,
                estimated_minutes=task.estimated_minutes,
                remaining_minutes=task.remaining_minutes,
                source_state=hint.state,
                source_title=hint.title if include_source_hints else None,
                source_version_no=hint.version_no if include_source_hints else None,
                source_locator=hint.locator if include_source_hints else None,
                included=not cancelled,
                exclusion_reason="cancelled_task" if cancelled else None,
            )
        )
    # 排序固定为截止日期（空值在后）、优先级（高到低）、task_id（A017 §3.1）。
    items.sort(
        key=lambda item: (
            item.due_date is None,
            item.due_date or date.max,
            _PRIORITY_RANK.get(TaskPriority(item.priority), 1),
            item.task_id,
        )
    )
    return items


def _tasks_scope_fingerprint(
    request: ExportRequest,
    workspace: WorkspaceData,
    tasks: list[TaskData],
    items: list[ExportItem],
) -> str:
    """范围与对象修订摘要；预览与下载必须一致，任何字段变化都触发 409。"""
    tasks_by_id = {task.id: task for task in tasks}
    canonical = {
        "mode": request.mode,
        "format": request.format,
        "workspace_id": workspace.id,
        "task_ids": sorted(tasks_by_id),
        "include_source_hints": request.include_source_hints,
        "include_fixed_events": request.include_fixed_events,
        "timezone": workspace.timezone,
        "tasks": [
            {
                "id": item.task_id,
                "updated_at": _iso(tasks_by_id[item.task_id].updated_at),
                "title": item.title,
                "course_id": item.course_id,
                "due_date": _iso(item.due_date),
                "due_time": _iso(item.due_time),
                "target_date": _iso(item.target_date),
                "due_source": item.due_source,
                "priority": item.priority,
                "progress": item.progress,
                "estimated_minutes": item.estimated_minutes,
                "remaining_minutes": item.remaining_minutes,
                "source_material_id": tasks_by_id[item.task_id].source_material_id,
                "source_invalidated_at": _iso(tasks_by_id[item.task_id].source_invalidated_at),
                "source_candidate_id": tasks_by_id[item.task_id].source_candidate_id,
                "source_state": item.source_state,
                "source_title": item.source_title,
                "source_version_no": item.source_version_no,
                "source_locator": item.source_locator,
            }
            for item in sorted(items, key=lambda item: item.task_id)
        ],
    }
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _notices(
    reasons: list[tuple[str, int, str]],
) -> tuple[dict[str, object], ...]:
    return tuple(
        {"code": code, "count": count, "message": message}
        for code, count, message in reasons
        if count > 0
    )


def _require_accepted_plan(
    repos: Repositories, workspace_id: int, plan_id: int | None
) -> PlanDraftData:
    """计划模式只接受当前已接受计划；草案或被替代计划明确拒绝（A017 §1）。"""
    if plan_id is None:
        raise DomainError("请指定要导出的已接受计划，不自动选择计划")
    draft = repos.plans.get_draft(plan_id)
    if draft is None or draft.workspace_id != workspace_id:
        raise NotFoundError("计划不存在或不属于当前工作空间")
    if draft.status == "draft":
        raise DomainError("计划尚未接受，请先在计划页接受后再导出")
    if draft.status == "superseded":
        raise DomainError("计划已被替代，请导出当前已接受的计划")
    if draft.status != "accepted":
        raise DomainError("只有已接受计划可以导出")
    current = repos.plans.get_current(workspace_id, semester_id=draft.semester_id)
    if current is None or current.id != draft.id:
        raise DomainError("该计划已不是当前已接受计划，请重新选择")
    return draft


def _plan_timezone(draft: PlanDraftData) -> str:
    """计划时区取接受时的输入快照；缺失或非法时不回落 UTC（A017 §6）。"""
    raw = draft.input_snapshot.get("timezone")
    if not isinstance(raw, str) or not raw:
        raise DomainError("计划时区无法读取，已停止导出")
    try:
        ZoneInfo(raw)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise DomainError("计划时区无法读取，已停止导出") from exc
    return raw


def _task_events(
    tasks_by_id: dict[int, TaskData],
    items: list[ExportItem],
    *,
    timezone: str,
) -> list[ExportEvent]:
    """每个有截止的所选任务一个 VEVENT；只有日期时保持全天，不补时刻。"""
    zone = ZoneInfo(timezone)
    events: list[ExportEvent] = []
    for item in items:
        if not item.included or item.due_date is None:
            continue
        task = tasks_by_id[item.task_id]
        properties: list[tuple[str, str]] = [
            ("X-CAMPUSFLOW-KIND", "task-due"),
            ("X-CAMPUSFLOW-TASK-ID", str(item.task_id)),
            ("X-CAMPUSFLOW-DUE-PRECISION", item.due_precision.upper()),
            ("X-CAMPUSFLOW-DUE-SOURCE", item.due_source),
            ("X-CAMPUSFLOW-PROGRESS", item.progress),
            ("X-CAMPUSFLOW-PRIORITY", item.priority),
            ("X-CAMPUSFLOW-SOURCE-STATE", item.source_state),
        ]
        if item.course_name:
            properties.append(("X-CAMPUSFLOW-COURSE", item.course_name))
        if item.source_title is not None:
            hint = item.source_title
            if item.source_version_no is not None:
                hint += f" v{item.source_version_no}"
            if item.source_locator is not None:
                hint += f" · {item.source_locator}"
            properties.append(("X-CAMPUSFLOW-SOURCE-HINT", hint))
        if item.due_time is None:
            events.append(
                ExportEvent(
                    uid=f"campusflow-task-{item.task_id}-due@campusflow.local",
                    summary=item.title,
                    all_day_start=item.due_date,
                    all_day_end=item.due_date + timedelta(days=1),
                    transp="TRANSPARENT",
                    last_modified=task.updated_at,
                    properties=tuple(properties),
                )
            )
            continue
        starts_at = in_workspace_timezone(
            datetime.combine(item.due_date, item.due_time), zone
        ).astimezone(UTC)
        events.append(
            ExportEvent(
                uid=f"campusflow-task-{item.task_id}-due@campusflow.local",
                summary=item.title,
                starts_at=starts_at,
                ends_at=starts_at + timedelta(minutes=_DUE_DISPLAY_MINUTES),
                transp="TRANSPARENT",
                last_modified=task.updated_at,
                properties=tuple(properties),
            )
        )
    return events


def _preview_tasks(repos: Repositories, workspace_id: int, request: ExportRequest) -> ExportPreview:
    workspace = _require_workspace_timezone(repos, workspace_id)
    tasks, unique_ids = _load_tasks(repos, workspace_id, request.task_ids)
    items = _build_items(
        repos,
        workspace_id,
        tasks,
        include_source_hints=request.include_source_hints,
    )
    included = [item for item in items if item.included]
    cancelled = sum(1 for item in items if not item.included)

    date_only = sum(1 for item in included if item.due_precision == "date")
    timed = sum(1 for item in included if item.due_precision == "datetime")
    no_due = sum(1 for item in included if item.due_precision == "none")
    invalidated = sum(1 for item in included if item.source_state == SOURCE_STATE_INVALIDATED)
    dated = [item.due_date for item in included if item.due_date is not None]

    events: tuple[ExportEvent, ...] = ()
    if request.format == "ics":
        events = tuple(
            _task_events(
                {task.id: task for task in tasks},
                included,
                timezone=workspace.timezone,
            )
        )

    excluded = _notices([("cancelled_task", cancelled, "已取消任务不导出")])
    if request.format == "ics":
        warnings = _notices(
            [
                ("date_only_ddl", date_only, "只有日期的截止按全天导出，不补具体时刻"),
                ("missing_due_date", no_due, "没有截止日期，日历不导出"),
                ("source_invalidated", invalidated, "来源已失效或无法定位，来源提示已省略"),
                ("cancelled_task", cancelled, "已取消任务已排除"),
            ]
        )
    else:
        warnings = _notices(
            [
                ("date_only_ddl", date_only, "只有日期的截止按全天日期导出，不补具体时刻"),
                ("missing_due_date", no_due, "没有截止日期，清单保留并标注 due_precision=none"),
                ("source_invalidated", invalidated, "来源已失效或无法定位，来源提示已省略"),
                ("cancelled_task", cancelled, "已取消任务已排除"),
            ]
        )
    if request.format == "ics":
        can_download = bool(events)
    else:
        can_download = bool(included)
    if not can_download:
        warnings = warnings + (
            {"code": "empty_export", "count": 0, "message": "没有可导出的任务，下载已禁用"},
        )

    return ExportPreview(
        mode=request.mode,
        format=request.format,
        timezone=workspace.timezone,
        task_ids=unique_ids,
        item_count=len(unique_ids),
        row_count=len(included),
        date_start=min(dated) if dated else None,
        date_end=max(dated) if dated else None,
        has_date_only=date_only > 0,
        event_count=len(events),
        counts={
            "date_only": date_only,
            "timed": timed,
            "no_due": no_due,
            "scheduled": 0,
            "unscheduled": 0,
            "source_invalidated": invalidated,
            "cancelled": cancelled,
        },
        excluded=excluded,
        warnings=warnings,
        items=tuple(items),
        events=events,
        scope_fingerprint=_tasks_scope_fingerprint(request, workspace, tasks, items),
        can_download=can_download,
    )


def _fixed_event_events(
    repos: Repositories,
    workspace_id: int,
    draft: PlanDraftData,
) -> tuple[list[ExportEvent], list[dict[str, object]]]:
    """把计划范围内的固定日程发生项展开为独立 VEVENT；不写 RRULE。"""
    occurrences = schedule_use_cases.list_fixed_event_occurrences(
        repos,
        workspace_id,
        draft.range_start,
        draft.range_end,
        semester_id=draft.semester_id,
    )
    events: list[ExportEvent] = []
    snapshot: list[dict[str, object]] = []
    for occurrence in occurrences:
        event = repos.schedule.get_fixed_event(occurrence.source_event_id)
        weekly = (
            event is not None
            and event.recurrence == EventRecurrence.WEEKLY
            and event.repeat_until is not None
        )
        starts_at = _utc(occurrence.starts_at)
        ends_at = _utc(occurrence.ends_at)
        uid = f"campusflow-fixed-{occurrence.source_event_id}"
        if weekly:
            # 每周发生项以 UTC 开始时间区分；时间修改会生成新 UID（A017 §6）。
            uid += f"-{_utc_stamp(starts_at)}"
        uid += "@campusflow.local"
        properties = (
            ("X-CAMPUSFLOW-KIND", "fixed-event"),
            ("X-CAMPUSFLOW-FIXED-EVENT-ID", str(occurrence.source_event_id)),
        )
        events.append(
            ExportEvent(
                uid=uid,
                summary=occurrence.title,
                starts_at=starts_at,
                ends_at=ends_at,
                transp="OPAQUE",
                location=occurrence.location,
                properties=properties,
            )
        )
        snapshot.append(
            {
                "event_id": occurrence.source_event_id,
                "title": occurrence.title,
                "starts_at": starts_at.isoformat(),
                "ends_at": ends_at.isoformat(),
                "location": occurrence.location,
            }
        )
    return events, snapshot


def _plan_fingerprint(
    request: ExportRequest,
    workspace: WorkspaceData,
    draft: PlanDraftData,
    timezone: str,
    blocks: list[PlanBlockData],
    tasks_by_id: dict[int, TaskData],
    hints: dict[int, _SourceHint],
    fixed_snapshot: list[dict[str, object]],
) -> str:
    canonical = {
        "mode": request.mode,
        "format": request.format,
        "workspace_id": workspace.id,
        "plan_id": draft.id,
        "plan_version": draft.version,
        "plan_status": draft.status,
        "plan_updated_at": _iso(draft.updated_at),
        "semester_id": draft.semester_id,
        "range_start": _iso(draft.range_start),
        "range_end": _iso(draft.range_end),
        "timezone": timezone,
        "include_source_hints": request.include_source_hints,
        "include_fixed_events": request.include_fixed_events,
        "blocks": [
            {
                "id": block.id,
                "task_id": block.task_id,
                "kind": block.kind,
                "title": block.title,
                "starts_at": _utc(block.starts_at).isoformat(),
                "ends_at": _utc(block.ends_at).isoformat(),
                "minutes": block.minutes,
                "seq": block.seq,
                "locked": block.locked,
            }
            for block in blocks
        ],
        "tasks": [
            {
                "id": task.id,
                "updated_at": _iso(task.updated_at),
                "title": task.title,
                "due_date": _iso(task.due_date),
                "due_time": _iso(task.due_time),
                "progress": task.progress.value,
                "priority": task.priority.value,
                "source_material_id": task.source_material_id,
                "source_invalidated_at": _iso(task.source_invalidated_at),
                "source_candidate_id": task.source_candidate_id,
                "source_state": hints[task.id].state if task.id in hints else task.source_state,
                "source_title": hints[task.id].title if task.id in hints else None,
                "source_version_no": hints[task.id].version_no if task.id in hints else None,
                "source_locator": hints[task.id].locator if task.id in hints else None,
            }
            for task in sorted(tasks_by_id.values(), key=lambda task: task.id)
        ],
        "fixed_events": fixed_snapshot,
    }
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _build_plan_export(
    repos: Repositories,
    workspace_id: int,
    request: ExportRequest,
    workspace: WorkspaceData,
    draft: PlanDraftData,
    timezone: str,
) -> _PlanExport:
    blocks = sorted(draft.blocks, key=lambda block: (block.starts_at, block.seq, block.id))
    tasks_by_id: dict[int, TaskData] = {}
    hints: dict[int, _SourceHint] = {}
    events: list[ExportEvent] = []
    cancelled = 0
    buffer = 0
    orphan = 0
    invalidated = 0

    for block in blocks:
        if block.kind == "buffer":
            buffer += 1
            continue
        if block.kind != "task" or block.task_id is None:
            orphan += 1
            continue
        task = tasks_by_id.get(block.task_id)
        if task is None:
            task = repos.tasks.get(block.task_id)
            if task is None or task.workspace_id != workspace_id:
                orphan += 1
                continue
            tasks_by_id[task.id] = task
        if task.progress == TaskProgress.CANCELLED:
            cancelled += 1
            continue
        hint = hints.get(task.id)
        if hint is None:
            hint = _source_hint(repos, task)
            hints[task.id] = hint
        if hint.state == SOURCE_STATE_INVALIDATED:
            invalidated += 1
        properties: list[tuple[str, str]] = [
            ("X-CAMPUSFLOW-KIND", "plan-block"),
            ("X-CAMPUSFLOW-TASK-ID", str(task.id)),
            ("X-CAMPUSFLOW-PLAN-ID", str(draft.id)),
            ("X-CAMPUSFLOW-BLOCK-ID", str(block.id)),
            ("X-CAMPUSFLOW-DRAFT-VERSION", str(draft.version)),
            ("X-CAMPUSFLOW-LOCKED", "TRUE" if block.locked else "FALSE"),
            ("X-CAMPUSFLOW-PROGRESS", task.progress.value),
            ("X-CAMPUSFLOW-PRIORITY", task.priority.value),
            ("X-CAMPUSFLOW-SOURCE-STATE", hint.state),
        ]
        hint_text = _source_hint_text(hint)
        if request.include_source_hints and hint_text is not None:
            properties.append(("X-CAMPUSFLOW-SOURCE-HINT", hint_text))
        events.append(
            ExportEvent(
                uid=f"campusflow-plan-{draft.id}-block-{block.id}@campusflow.local",
                summary=block.title,
                starts_at=_utc(block.starts_at),
                ends_at=_utc(block.ends_at),
                transp="OPAQUE",
                sequence=draft.version,
                last_modified=draft.updated_at,
                properties=tuple(properties),
            )
        )

    scheduled = len(events)
    unscheduled = sum(
        1 for item in draft.unscheduled if item.get("status") in {"unscheduled", "partial"}
    )

    fixed_snapshot: list[dict[str, object]] = []
    fixed_total = len(
        schedule_use_cases.list_fixed_events(repos, workspace_id, semester_id=draft.semester_id)
    )
    warning_reasons: list[tuple[str, int, str]] = [
        ("unscheduled_task", unscheduled, "未排入任务没有时间块，日历不导出"),
        ("buffer_block", buffer, "提交缓冲时间块已排除"),
        ("cancelled_task", cancelled, "已取消任务的时间块已排除"),
        ("source_invalidated", invalidated, "来源已失效或无法定位，来源提示已省略"),
    ]
    if request.include_fixed_events:
        fixed_events, fixed_snapshot = _fixed_event_events(repos, workspace_id, draft)
        events.extend(fixed_events)
        if not fixed_events and fixed_total:
            warning_reasons.append(
                ("fixed_event_out_of_range", fixed_total, "固定日程不在计划范围内，未导出")
            )
    elif fixed_total:
        # 默认不导出固定日程，但明确告知可以勾选（A017 §2）。
        warning_reasons.append(
            ("fixed_event_disabled", fixed_total, "固定日程默认不导出，可在选项中勾选")
        )

    excluded = _notices(
        [
            ("cancelled_task", cancelled, "已取消任务的时间块不导出"),
            ("buffer_block", buffer, "提交缓冲时间块不导出"),
            ("orphan_block", orphan, "没有对应任务的时间块不导出"),
            ("unscheduled_task", unscheduled, "未排入任务没有时间块，日历无法定位"),
        ]
    )
    warnings = _notices(warning_reasons)
    if not events:
        warnings = warnings + (
            {
                "code": "empty_export",
                "count": 0,
                "message": "没有可导出的时间块，下载已禁用",
            },
        )

    return _PlanExport(
        events=tuple(events),
        counts={
            "date_only": 0,
            "timed": scheduled,
            "no_due": 0,
            "scheduled": scheduled,
            "unscheduled": unscheduled,
            "source_invalidated": invalidated,
            "cancelled": cancelled,
        },
        excluded=excluded,
        warnings=warnings,
        fingerprint=_plan_fingerprint(
            request,
            workspace,
            draft,
            timezone,
            blocks,
            tasks_by_id,
            hints,
            fixed_snapshot,
        ),
    )


def _preview_plan(repos: Repositories, workspace_id: int, request: ExportRequest) -> ExportPreview:
    workspace = _require_workspace_timezone(repos, workspace_id)
    draft = _require_accepted_plan(repos, workspace_id, request.plan_id)
    timezone = _plan_timezone(draft)
    plan = _build_plan_export(repos, workspace_id, request, workspace, draft, timezone)
    return ExportPreview(
        mode=request.mode,
        format=request.format,
        timezone=timezone,
        task_ids=(),
        item_count=len(draft.blocks),
        row_count=0,
        date_start=draft.range_start,
        date_end=draft.range_end,
        has_date_only=False,
        plan_id=draft.id,
        plan_version=draft.version,
        semester_id=draft.semester_id,
        range_start=draft.range_start,
        range_end=draft.range_end,
        event_count=len(plan.events),
        counts=plan.counts,
        excluded=plan.excluded,
        warnings=plan.warnings,
        events=plan.events,
        scope_fingerprint=plan.fingerprint,
        can_download=bool(plan.events),
    )


def preview_export(repos: Repositories, workspace_id: int, request: ExportRequest) -> ExportPreview:
    _validate_supported(request)
    if request.mode == "tasks":
        return _preview_tasks(repos, workspace_id, request)
    return _preview_plan(repos, workspace_id, request)


def _csv_cell(value: object | None) -> str:
    if value is None:
        return ""
    if isinstance(value, time):
        return _format_due_time(value)
    return str(value)


def render_tasks_csv(items: list[ExportItem]) -> bytes:
    """UTF-8 BOM + CRLF + RFC 4180 引号；空值留空，不写 null。"""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for item in items:
        writer.writerow(
            [
                EXPORT_VERSION,
                _csv_cell(item.task_id),
                _csv_cell(item.course_id),
                _csv_cell(item.course_name),
                item.title,
                _csv_cell(item.due_date),
                _csv_cell(item.due_time),
                item.due_precision,
                item.due_source,
                _csv_cell(item.target_date),
                item.priority,
                item.progress,
                _csv_cell(item.estimated_minutes),
                _csv_cell(item.remaining_minutes),
                item.source_state,
                _csv_cell(item.source_title),
                _csv_cell(item.source_version_no),
                _csv_cell(item.source_locator),
            ]
        )
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def _escape_ics_text(value: str) -> str:
    """ICS 文本转义：反斜杠、逗号、分号和换行（A017 §3.2）。"""
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\\", "\\\\")
    return normalized.replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold_ics_line(line: str) -> list[str]:
    """按 75 字节折行，续行以一个空格开头；绝不切断 UTF-8 字符。"""
    chunks: list[str] = []
    current = ""
    size = 0
    limit = _ICS_MAX_OCTETS
    for char in line:
        char_size = len(char.encode("utf-8"))
        if current and size + char_size > limit:
            chunks.append(current)
            current = char
            size = char_size
            # 续行带一个空格前缀，正文只剩 74 字节。
            limit = _ICS_MAX_OCTETS - 1
        else:
            current += char
            size += char_size
    chunks.append(current)
    return chunks[:1] + [" " + chunk for chunk in chunks[1:]]


def render_ics(
    events: tuple[ExportEvent, ...] | list[ExportEvent],
    *,
    timezone: str,
    now: datetime,
) -> bytes:
    """序列化日历；UTC 时间点、CRLF、75 字节折行，无 DESCRIPTION 与 VALARM。"""
    stamp = _utc_stamp(now)
    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{_ICS_PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "CLASS:PRIVATE",
        f"X-WR-TIMEZONE:{timezone}",
    ]
    for event in events:
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{event.uid}")
        lines.append(f"DTSTAMP:{stamp}")
        if event.last_modified is not None:
            lines.append(f"LAST-MODIFIED:{_utc_stamp(event.last_modified)}")
        if event.sequence is not None:
            lines.append(f"SEQUENCE:{event.sequence}")
        if event.all_day_start is not None and event.all_day_end is not None:
            lines.append(f"DTSTART;VALUE=DATE:{event.all_day_start:%Y%m%d}")
            lines.append(f"DTEND;VALUE=DATE:{event.all_day_end:%Y%m%d}")
        else:
            if event.starts_at is None or event.ends_at is None:
                raise DomainError("日历事件缺少起止时间，已停止导出")
            lines.append(f"DTSTART:{_utc_stamp(event.starts_at)}")
            lines.append(f"DTEND:{_utc_stamp(event.ends_at)}")
        lines.append(f"SUMMARY:{_escape_ics_text(event.summary)}")
        if event.location:
            lines.append(f"LOCATION:{_escape_ics_text(event.location)}")
        lines.append("STATUS:CONFIRMED")
        lines.append(f"TRANSP:{event.transp}")
        for name, value in event.properties:
            lines.append(f"{name}:{_escape_ics_text(value)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    folded = [piece for line in lines for piece in _fold_ics_line(line)]
    return ("\r\n".join(folded) + "\r\n").encode("utf-8")


def export_file(repos: Repositories, workspace_id: int, request: ExportRequest) -> ExportFile:
    """生成下载文件；范围指纹与预览不一致时要求重新预览。"""
    preview = preview_export(repos, workspace_id, request)
    if not request.scope_fingerprint:
        raise DomainError("缺少导出范围指纹，请先预览后再下载")
    if request.scope_fingerprint != preview.scope_fingerprint:
        raise ConflictError("导出范围已变化，请重新预览后再下载")
    if not preview.can_download:
        if request.format == "ics":
            raise DomainError("没有可导出的日历事件，未生成文件")
        raise DomainError("没有可导出的任务，未生成文件")
    now = datetime.now(UTC)
    local_now = now.astimezone(ZoneInfo(preview.timezone))
    if request.format == "ics":
        prefix = "campusflow-tasks" if request.mode == "tasks" else "campusflow-plan"
        return ExportFile(
            filename=f"{prefix}-{local_now:%Y%m%d}.ics",
            content=render_ics(preview.events, timezone=preview.timezone, now=now),
            media_type=_ICS_MEDIA_TYPE,
        )
    rows = [item for item in preview.items if item.included]
    return ExportFile(
        filename=f"campusflow-tasks-{local_now:%Y%m%d}.csv",
        content=render_tasks_csv(rows),
        media_type=_CSV_MEDIA_TYPE,
    )


__all__ = [
    "CSV_COLUMNS",
    "EXPORT_VERSION",
    "ExportEvent",
    "ExportFile",
    "ExportItem",
    "ExportPreview",
    "ExportRequest",
    "export_file",
    "preview_export",
    "render_ics",
    "render_tasks_csv",
]
