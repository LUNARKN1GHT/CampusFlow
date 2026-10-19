"""计划草案与当前已接受计划 HTTP 路由（M5/P013）。

生成、重排与调整建议都只产生或展示待接受草案；只有显式接受才改变当前计划。
移动时间块不改变任务截止，版本冲突统一返回 409。
"""

from datetime import UTC, date, datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from campusflow.api.deps import get_current_workspace, get_repositories
from campusflow.application import planning as use_cases
from campusflow.application.ports.repositories import (
    PlanDraftData,
    Repositories,
    WorkspaceData,
)
from campusflow.application.settings import get_settings
from campusflow.domain.errors import DomainError
from campusflow.domain.planning import AssumptionOptions, resolve_planning_range

router = APIRouter(tags=["plans"])

CurrentWorkspace = Annotated[WorkspaceData, Depends(get_current_workspace)]
Repos = Annotated[Repositories, Depends(get_repositories)]


class PlanAssumptionsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default_duration_minutes: int | None = Field(default=None, gt=0)
    task_durations: dict[int, int] = Field(default_factory=dict)
    task_due_dates: dict[int, date] = Field(default_factory=dict)
    segment_minutes: int | None = Field(default=None, gt=0)
    buffer_minutes: int | None = Field(default=None, ge=0)

    def to_options(self) -> AssumptionOptions:
        return AssumptionOptions(
            default_duration_minutes=self.default_duration_minutes,
            task_durations=self.task_durations,
            task_due_dates=self.task_due_dates,
            segment_minutes=self.segment_minutes,
            buffer_minutes=self.buffer_minutes,
        )


class PlanDraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    semester_id: int | None = Field(default=None, ge=1)
    range_type: Literal["today", "this_week", "two_weeks", "custom"] | None = None
    start_date: date | None = None
    end_date: date | None = None
    assumptions: PlanAssumptionsIn = Field(default_factory=PlanAssumptionsIn)


class PlanAcceptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)


class PlanBlockMoveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    starts_at: datetime
    ends_at: datetime
    expected_version: int = Field(ge=1)


class PlanRescheduleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    semester_id: int | None = Field(default=None, ge=1)
    start_date: date | None = None
    end_date: date | None = None
    task_ids: list[int] | None = Field(default=None, min_length=1, max_length=100)
    reason: str | None = Field(default=None, max_length=500)


class AdjustmentDraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    semester_id: int | None = Field(default=None, ge=1)
    task_ids: list[int] | None = Field(default=None, min_length=1, max_length=100)
    reason: str | None = Field(default=None, max_length=500)


class PlanBlockLockIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    locked: bool
    expected_version: int = Field(ge=1)


class PlanBlockOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    draft_id: int
    task_id: int | None
    kind: str
    title: str
    starts_at: datetime
    ends_at: datetime
    minutes: int
    seq: int
    locked: bool
    reason: str
    constraint_source: list[dict]


class PlanDraftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    semester_id: int | None
    range_start: date
    range_end: date
    timezone: str
    status: str
    version: int
    input_fingerprint: str
    assumptions: list[dict]
    gaps: list[dict]
    unscheduled: list[dict]
    conflicts: list[dict]
    coverage: dict
    created_at: datetime
    accepted_at: datetime | None
    blocks: list[PlanBlockOut]

    @classmethod
    def from_data(cls, draft: PlanDraftData) -> "PlanDraftOut":
        return cls(
            id=draft.id,
            semester_id=draft.semester_id,
            range_start=draft.range_start,
            range_end=draft.range_end,
            timezone=str(draft.input_snapshot.get("timezone", "UTC")),
            status=draft.status,
            version=draft.version,
            input_fingerprint=draft.input_fingerprint,
            assumptions=draft.assumptions,
            gaps=draft.gaps,
            unscheduled=draft.unscheduled,
            conflicts=draft.conflicts,
            coverage=draft.coverage,
            created_at=draft.created_at,
            accepted_at=draft.accepted_at,
            blocks=[PlanBlockOut.model_validate(block) for block in draft.blocks],
        )


def _default_range(workspace: WorkspaceData, repos: Repositories, payload: PlanDraftCreate):
    """解析预设区间；预设不接受自定义起止，custom 必须由调用方给出边界。"""
    settings = get_settings(repos, workspace.id)
    today = datetime.now(UTC).astimezone(ZoneInfo(settings.timezone)).date()
    kind = payload.range_type
    if kind is None:
        kind = (
            "custom"
            if payload.start_date is not None or payload.end_date is not None
            else "two_weeks"
        )
    if kind != "custom" and (payload.start_date is not None or payload.end_date is not None):
        raise DomainError("预设规划区间不接受自定义起止日期；如需指定日期请使用 custom")
    return resolve_planning_range(kind, today, start=payload.start_date, end=payload.end_date)


@router.post("/plans/drafts", response_model=PlanDraftOut, status_code=201)
def create_plan_draft(
    payload: PlanDraftCreate, workspace: CurrentWorkspace, repos: Repos
) -> PlanDraftOut:
    """生成并保存待接受草案；接受前当前计划与任务截止都不变。"""
    start, end = _default_range(workspace, repos, payload)
    draft = use_cases.generate_draft(
        repos,
        workspace.id,
        semester_id=payload.semester_id,
        range_start=start,
        range_end=end,
        options=payload.assumptions.to_options(),
    )
    return PlanDraftOut.from_data(draft)


@router.post("/plans/reschedule", response_model=PlanDraftOut, status_code=201)
def reschedule_plan(
    payload: PlanRescheduleIn, workspace: CurrentWorkspace, repos: Repos
) -> PlanDraftOut:
    """基于当前已接受计划局部重排；锁定块与范围外块保留，结果仍是草案。

    没有已接受计划时返回 404，不会退化成从零生成。
    """
    draft = use_cases.reschedule_plan(
        repos,
        workspace.id,
        semester_id=payload.semester_id,
        range_start=payload.start_date,
        range_end=payload.end_date,
        task_ids=payload.task_ids,
    )
    return PlanDraftOut.from_data(draft)


@router.get("/plans/adjustments", response_model=dict)
def get_plan_adjustments(
    workspace: CurrentWorkspace,
    repos: Repos,
    semester_id: Annotated[int | None, Query(ge=1)] = None,
) -> dict:
    """只读比较输入变化，auto_applied 恒为 false；不会改写当前计划。"""
    return use_cases.adjustment_suggestions(repos, workspace.id, semester_id=semester_id)


@router.post("/plans/adjustments/draft", response_model=PlanDraftOut, status_code=201)
def create_plan_adjustment_draft(
    payload: AdjustmentDraftIn, workspace: CurrentWorkspace, repos: Repos
) -> PlanDraftOut:
    """仅在用户显式请求后生成待接受草案；输入与已接受计划一致时返回 409。"""
    draft = use_cases.create_adjustment_draft(
        repos,
        workspace.id,
        semester_id=payload.semester_id,
        task_ids=payload.task_ids,
    )
    return PlanDraftOut.from_data(draft)


@router.get("/plans/drafts", response_model=list[PlanDraftOut])
def list_plan_drafts(
    workspace: CurrentWorkspace,
    repos: Repos,
    semester_id: Annotated[int | None, Query(ge=1)] = None,
) -> list[PlanDraftOut]:
    drafts = use_cases.list_drafts(repos, workspace.id, semester_id=semester_id)
    return [
        PlanDraftOut.from_data(use_cases.get_draft(repos, workspace.id, draft.id))
        for draft in drafts
    ]


@router.get("/plans/drafts/{draft_id}", response_model=PlanDraftOut)
def get_plan_draft(draft_id: int, workspace: CurrentWorkspace, repos: Repos) -> PlanDraftOut:
    return PlanDraftOut.from_data(use_cases.get_draft(repos, workspace.id, draft_id))


@router.post("/plans/drafts/{draft_id}/accept", response_model=PlanDraftOut)
def accept_plan_draft(
    draft_id: int, payload: PlanAcceptIn, workspace: CurrentWorkspace, repos: Repos
) -> PlanDraftOut:
    """接受草案成为当前计划；输入变化、版本过期或已有更新计划都返回 409。

    接受后重算提醒，但不会改写任何任务截止时间。
    """
    draft = use_cases.accept_draft(
        repos,
        workspace.id,
        draft_id,
        expected_version=payload.expected_version,
    )
    return PlanDraftOut.from_data(draft)


@router.get("/plans/overview", response_model=dict)
def get_plan_overview(
    workspace: CurrentWorkspace,
    repos: Repos,
    semester_id: Annotated[int | None, Query(ge=1)] = None,
) -> dict:
    """今日总览只读；时间块只取当前已接受计划，没有计划时仍列出近期任务。"""
    return use_cases.overview(repos, workspace.id, semester_id=semester_id)


@router.get("/plans/current", response_model=PlanDraftOut | None)
def get_current_plan(
    workspace: CurrentWorkspace,
    repos: Repos,
    semester_id: Annotated[int | None, Query(ge=1)] = None,
) -> PlanDraftOut | None:
    """没有已接受计划时返回 null，而不是最近一份草案。"""
    draft = use_cases.current_plan(repos, workspace.id, semester_id=semester_id)
    return PlanDraftOut.from_data(draft) if draft is not None else None


@router.get("/plans/drafts/{draft_id}/conflicts", response_model=list[dict])
def list_plan_conflicts(draft_id: int, workspace: CurrentWorkspace, repos: Repos) -> list[dict]:
    """返回草案生成时保存的冲突与风险；检查限制在 coverage 中说明。"""
    return use_cases.draft_conflicts(repos, workspace.id, draft_id)


@router.patch("/plans/blocks/{block_id}", response_model=PlanDraftOut)
def move_plan_block(
    block_id: int, payload: PlanBlockMoveIn, workspace: CurrentWorkspace, repos: Repos
) -> PlanDraftOut:
    """只允许移动已接受计划中的块；不修改任务 DDL。

    越出计划范围、与时间块或固定日程重叠返回 400；版本过期返回 409。
    """
    draft = use_cases.move_block(
        repos,
        workspace.id,
        block_id,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        expected_version=payload.expected_version,
    )
    return PlanDraftOut.from_data(draft)


@router.post("/plans/blocks/{block_id}/lock", response_model=PlanDraftOut)
def set_plan_block_lock(
    block_id: int, payload: PlanBlockLockIn, workspace: CurrentWorkspace, repos: Repos
) -> PlanDraftOut:
    """锁定只表示后续重排原样保留该块，不改变时间块本身或任务截止。"""
    draft = use_cases.set_block_lock(
        repos,
        workspace.id,
        block_id,
        locked=payload.locked,
        expected_version=payload.expected_version,
    )
    return PlanDraftOut.from_data(draft)
