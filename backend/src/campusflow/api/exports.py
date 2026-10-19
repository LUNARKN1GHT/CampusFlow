"""A018／A019：清单与日历导出 HTTP 路由（预览与文件下载）。

范围由请求显式给出；预览返回范围指纹，下载必须携带同一指纹。
计划模式支持 ICS；计划模式的 CSV 清单留待后续任务，返回 400 而不是空文件。
"""

from datetime import date, time
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field

from campusflow.api.deps import get_current_workspace, get_repositories
from campusflow.application import exports as use_cases
from campusflow.application.ports.repositories import Repositories, WorkspaceData

router = APIRouter(tags=["exports"])

CurrentWorkspace = Annotated[WorkspaceData, Depends(get_current_workspace)]
Repos = Annotated[Repositories, Depends(get_repositories)]


class ExportOptionsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # 来源提示只到标题、版本号与定位；固定日程仅计划模式展开，且不写 RRULE。
    include_source_hints: bool = False
    include_fixed_events: bool = False


class ExportRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["tasks", "plan"]
    format: Literal["csv", "ics"]
    task_ids: list[int] | None = None
    plan_id: int | None = None
    options: ExportOptionsIn = Field(default_factory=ExportOptionsIn)
    scope_fingerprint: str | None = Field(default=None, max_length=128)


class ExportScopeOut(BaseModel):
    task_ids: list[int]
    plan_id: int | None = None
    plan_version: int | None = None
    semester_id: int | None = None
    range_start: date | None = None
    range_end: date | None = None


class ExportDateRangeOut(BaseModel):
    start: date | None
    end: date | None
    has_date_only: bool


class ExportCountsOut(BaseModel):
    date_only: int
    timed: int
    no_due: int
    scheduled: int
    unscheduled: int
    source_invalidated: int
    cancelled: int


class ExportNoticeOut(BaseModel):
    code: str
    count: int
    message: str


class ExportItemOut(BaseModel):
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
    source_title: str | None
    source_version_no: int | None
    source_locator: str | None
    included: bool
    exclusion_reason: str | None


class ExportPreviewOut(BaseModel):
    mode: str
    format: str
    scope: ExportScopeOut
    timezone: str
    item_count: int
    row_count: int
    event_count: int
    date_range: ExportDateRangeOut
    counts: ExportCountsOut
    excluded: list[ExportNoticeOut]
    warnings: list[ExportNoticeOut]
    items: list[ExportItemOut]
    scope_fingerprint: str
    can_download: bool


def _to_request(payload: ExportRequestIn) -> use_cases.ExportRequest:
    return use_cases.ExportRequest(
        mode=payload.mode,
        format=payload.format,
        task_ids=tuple(payload.task_ids or ()),
        plan_id=payload.plan_id,
        include_source_hints=payload.options.include_source_hints,
        include_fixed_events=payload.options.include_fixed_events,
        scope_fingerprint=payload.scope_fingerprint,
    )


@router.post("/exports/preview", response_model=ExportPreviewOut)
def preview_export(
    payload: ExportRequestIn, workspace: CurrentWorkspace, repos: Repos
) -> ExportPreviewOut:
    """只读预览并返回范围指纹；items 含被排除项，供界面解释排除原因。"""
    preview = use_cases.preview_export(repos, workspace.id, _to_request(payload))
    return ExportPreviewOut(
        mode=preview.mode,
        format=preview.format,
        scope=ExportScopeOut(
            task_ids=list(preview.task_ids),
            plan_id=preview.plan_id,
            plan_version=preview.plan_version,
            semester_id=preview.semester_id,
            range_start=preview.range_start,
            range_end=preview.range_end,
        ),
        timezone=preview.timezone,
        item_count=preview.item_count,
        row_count=preview.row_count,
        event_count=preview.event_count,
        date_range=ExportDateRangeOut(
            start=preview.date_start,
            end=preview.date_end,
            has_date_only=preview.has_date_only,
        ),
        counts=ExportCountsOut(**preview.counts),
        excluded=[ExportNoticeOut(**notice) for notice in preview.excluded],
        warnings=[ExportNoticeOut(**notice) for notice in preview.warnings],
        items=[ExportItemOut.model_validate(item, from_attributes=True) for item in preview.items],
        scope_fingerprint=preview.scope_fingerprint,
        can_download=preview.can_download,
    )


@router.post("/exports", response_class=Response)
def download_export(
    payload: ExportRequestIn, workspace: CurrentWorkspace, repos: Repos
) -> Response:
    """下载必须携带预览指纹；重新计算后不一致返回 409，避免导出未看过的范围。

    范围只来自显式选择的任务或一个当前已接受计划，不会回退整空间。
    """
    export = use_cases.export_file(repos, workspace.id, _to_request(payload))
    return Response(
        content=export.content,
        media_type=export.media_type,
        headers={"Content-Disposition": f'attachment; filename="{export.filename}"'},
    )
