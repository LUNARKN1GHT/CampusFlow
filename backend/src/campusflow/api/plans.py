"""M5 计划草案 HTTP 路由（P013）。"""

from datetime import UTC, date, datetime, timedelta
from typing import Annotated
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
from campusflow.domain.planning import AssumptionOptions

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
    settings = get_settings(repos, workspace.id)
    today = datetime.now(UTC).astimezone(ZoneInfo(settings.timezone)).date()
    start = payload.start_date or today
    end = payload.end_date or (start + timedelta(days=13))
    return start, end


@router.post("/plans/drafts", response_model=PlanDraftOut, status_code=201)
def create_plan_draft(
    payload: PlanDraftCreate, workspace: CurrentWorkspace, repos: Repos
) -> PlanDraftOut:
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
    return use_cases.overview(repos, workspace.id, semester_id=semester_id)


@router.get("/plans/current", response_model=PlanDraftOut | None)
def get_current_plan(
    workspace: CurrentWorkspace,
    repos: Repos,
    semester_id: Annotated[int | None, Query(ge=1)] = None,
) -> PlanDraftOut | None:
    draft = use_cases.current_plan(repos, workspace.id, semester_id=semester_id)
    return PlanDraftOut.from_data(draft) if draft is not None else None


@router.get("/plans/drafts/{draft_id}/conflicts", response_model=list[dict])
def list_plan_conflicts(draft_id: int, workspace: CurrentWorkspace, repos: Repos) -> list[dict]:
    return use_cases.draft_conflicts(repos, workspace.id, draft_id)


@router.patch("/plans/blocks/{block_id}", response_model=PlanDraftOut)
def move_plan_block(
    block_id: int, payload: PlanBlockMoveIn, workspace: CurrentWorkspace, repos: Repos
) -> PlanDraftOut:
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
    draft = use_cases.set_block_lock(
        repos,
        workspace.id,
        block_id,
        locked=payload.locked,
        expected_version=payload.expected_version,
    )
    return PlanDraftOut.from_data(draft)
