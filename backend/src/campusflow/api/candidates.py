"""待核对事项 HTTP 路由（R001）。"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from campusflow.api.deps import get_current_workspace, get_repositories
from campusflow.application import candidates as use_cases
from campusflow.application.ports.candidates import CandidateSummaryData
from campusflow.application.ports.repositories import Repositories, WorkspaceData
from campusflow.domain.states import ReviewStatus

router = APIRouter(tags=["candidates"])

CurrentWorkspace = Annotated[WorkspaceData, Depends(get_current_workspace)]
Repos = Annotated[Repositories, Depends(get_repositories)]


class ReviewReasonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    code: str
    message: str
    field: str | None


class CandidateSummaryOut(BaseModel):
    """待核对列表条目：只含判断所需的最小字段，逐字段取值与证据仍由详情接口读取。"""

    id: int
    kind: str
    review_status: ReviewStatus
    title: str | None
    course_id: int | None
    semester_id: int | None
    class_name: str | None
    source_material_id: int
    source_version_id: int
    version_no: int
    review_reasons: list[ReviewReasonOut]
    created_at: datetime


class ReviewCountsOut(BaseModel):
    total: int
    by_status: dict[str, int]
    by_reason: dict[str, int]


def _to_out(item: CandidateSummaryData) -> CandidateSummaryOut:
    return CandidateSummaryOut(
        id=item.id,
        kind=item.kind,
        review_status=item.review_status,
        title=item.title,
        course_id=item.course_id,
        semester_id=item.semester_id,
        class_name=item.class_name,
        source_material_id=item.source_material_id,
        source_version_id=item.source_version_id,
        version_no=item.version_no,
        review_reasons=[
            ReviewReasonOut(code=code, message=message, field=field_name)
            for code, message, field_name in item.review_reasons
        ],
        created_at=item.created_at,
    )


@router.get("/candidates", response_model=list[CandidateSummaryOut])
def list_candidates(
    workspace: CurrentWorkspace,
    repos: Repos,
    status: Annotated[
        list[ReviewStatus] | None,
        Query(description="核对状态，可多选；默认只看待核对。与任务执行进度是两套状态"),
    ] = None,
    kind: Annotated[list[str] | None, Query(description="事项类型 task/event/rule，可多选")] = None,
    course_id: Annotated[int | None, Query()] = None,
    semester_id: Annotated[int | None, Query()] = None,
    material_id: Annotated[int | None, Query(description="按来源资料筛选")] = None,
    reason: Annotated[str | None, Query(description="缺失原因代码")] = None,
) -> list[CandidateSummaryOut]:
    return [
        _to_out(item)
        for item in use_cases.list_review_candidates(
            repos,
            workspace.id,
            statuses=tuple(status) if status else use_cases.DEFAULT_REVIEW_STATUSES,
            kinds=tuple(kind) if kind else None,
            course_id=course_id,
            semester_id=semester_id,
            material_id=material_id,
            reason_code=reason,
        )
    ]


@router.get("/candidates/summary", response_model=ReviewCountsOut)
def candidate_summary(
    workspace: CurrentWorkspace,
    repos: Repos,
    kind: Annotated[list[str] | None, Query()] = None,
    course_id: Annotated[int | None, Query()] = None,
    semester_id: Annotated[int | None, Query()] = None,
    material_id: Annotated[int | None, Query()] = None,
) -> ReviewCountsOut:
    counts = use_cases.review_counts(
        repos,
        workspace.id,
        kinds=tuple(kind) if kind else None,
        course_id=course_id,
        semester_id=semester_id,
        material_id=material_id,
    )
    return ReviewCountsOut(
        total=counts.total, by_status=counts.by_status, by_reason=counts.by_reason
    )
