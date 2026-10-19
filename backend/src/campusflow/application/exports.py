"""A018：任务清单 CSV 导出（范围、预览与下载绑定）。

导出范围完全由用户显式给出的 ``task_ids`` 决定，不回退为"全部任务"，
也不读取候选事项或草稿计划。范围、字段和隐私边界见 docs/export-scope.md
（A017）。本模块只实现任务模式的 CSV；计划模式与 ICS 由 A019 接入，
其它组合一律拒绝，不生成空文件，也不在服务端留存导出结果。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass, field
from datetime import date, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from campusflow.application.ports.repositories import (
    Repositories,
    TaskData,
    WorkspaceData,
)
from campusflow.domain.errors import ConflictError, DomainError, NotFoundError
from campusflow.domain.materials import SOURCE_STATE_INVALIDATED, source_state
from campusflow.domain.source_detail import citation_is_verifiable
from campusflow.domain.states import TaskPriority, TaskProgress

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
    counts: dict[str, int] = field(default_factory=dict)
    excluded: tuple[dict[str, object], ...] = ()
    warnings: tuple[dict[str, object], ...] = ()
    items: tuple[ExportItem, ...] = ()
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


def _iso(value: date | time | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


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


def _validate_supported(request: ExportRequest) -> None:
    if request.mode != "tasks":
        raise DomainError("计划导出由后续任务提供，当前仅支持任务清单导出")
    if request.format != "csv":
        raise DomainError("ICS 日历导出由后续任务提供，当前仅支持 CSV 任务清单")
    if request.plan_id is not None:
        raise DomainError("任务清单导出不接受计划标识")
    if not request.task_ids:
        raise DomainError("请显式选择要导出的任务，不提供整空间导出")


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


def _scope_fingerprint(
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


def preview_export(repos: Repositories, workspace_id: int, request: ExportRequest) -> ExportPreview:
    _validate_supported(request)
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

    excluded = _notices([("cancelled_task", cancelled, "已取消任务不导出")])
    warnings = _notices(
        [
            ("date_only_ddl", date_only, "只有日期的截止按全天日期导出，不补具体时刻"),
            ("missing_due_date", no_due, "没有截止日期，清单保留并标注 due_precision=none"),
            ("source_invalidated", invalidated, "来源已失效或无法定位，来源提示已省略"),
            ("cancelled_task", cancelled, "已取消任务已排除"),
        ]
    )
    if not included:
        warnings = warnings + (
            {"code": "empty_export", "count": 0, "message": "没有可导出的任务，下载已禁用"},
        )

    fingerprint = _scope_fingerprint(request, workspace, tasks, items)
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
        items=items,
        scope_fingerprint=fingerprint,
        can_download=bool(included),
    )


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


def export_file(repos: Repositories, workspace_id: int, request: ExportRequest) -> ExportFile:
    """生成下载文件；范围指纹与预览不一致时要求重新预览。"""
    preview = preview_export(repos, workspace_id, request)
    if not request.scope_fingerprint:
        raise DomainError("缺少导出范围指纹，请先预览后再下载")
    if request.scope_fingerprint != preview.scope_fingerprint:
        raise ConflictError("导出范围已变化，请重新预览后再下载")
    if not preview.can_download:
        raise DomainError("没有可导出的任务，未生成文件")
    rows = [item for item in preview.items if item.included]
    now = datetime.now(ZoneInfo(preview.timezone))
    return ExportFile(
        filename=f"campusflow-tasks-{now:%Y%m%d}.csv",
        content=render_tasks_csv(rows),
        media_type=_CSV_MEDIA_TYPE,
    )


__all__ = [
    "CSV_COLUMNS",
    "EXPORT_VERSION",
    "ExportFile",
    "ExportItem",
    "ExportPreview",
    "ExportRequest",
    "export_file",
    "preview_export",
    "render_tasks_csv",
]
