"""待核对事项 HTTP 路由（R001 列表、R008 核对详情与状态转换）。"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from campusflow.api.auth import require_session
from campusflow.api.deps import get_current_workspace, get_repositories
from campusflow.application import candidates as use_cases
from campusflow.application.ports.candidates import CandidateSummaryData
from campusflow.application.ports.repositories import Repositories, WorkspaceData
from campusflow.domain.candidates import FieldOrigin
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


class CandidateScopeOut(BaseModel):
    course_id: int | None
    semester_id: int | None
    class_name: str | None
    course_name: str | None
    semester_name: str | None


class CandidateSourceOut(BaseModel):
    material_id: int
    material_title: str
    version_id: int
    version_no: int


class CandidateEvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    chunk_id: int
    quote: str
    locator_type: str
    locator_value: str
    verifiable: bool


class CandidateFieldOut(BaseModel):
    """原始提取值与叠加修正后的生效值并列展示，两者出处可以不同。"""

    name: str
    value: Any
    origin: FieldOrigin
    effective_value: Any
    effective_origin: FieldOrigin
    explanation: str | None
    missing_reason: str | None
    verifiable: bool
    evidence: list[CandidateEvidenceOut]


class FieldRevisionOut(BaseModel):
    id: int
    field_name: str
    previous_value: Any
    new_value: Any
    previous_origin: FieldOrigin
    new_origin: FieldOrigin
    operator: str
    note: str | None
    created_at: datetime


class ReviewChangeOut(BaseModel):
    id: int
    from_status: ReviewStatus
    to_status: ReviewStatus
    operator: str
    reason: str | None
    changed_at: datetime


class CandidateReviewDetailOut(BaseModel):
    """核对详情：逐字段取值与证据、修正与核对留痕、缺失必需字段与可选转换。"""

    id: int
    kind: str
    review_status: ReviewStatus
    created_at: datetime
    scope: CandidateScopeOut
    source: CandidateSourceOut
    fields: list[CandidateFieldOut]
    revisions: list[FieldRevisionOut]
    review_reasons: list[ReviewReasonOut]
    review_changes: list[ReviewChangeOut]
    missing_required_fields: list[str]
    allowed_transitions: list[ReviewStatus]


class ReviewStatusPatch(BaseModel):
    """核对状态转换：只接受 ReviewStatus，与任务执行进度是两套状态。"""

    status: ReviewStatus
    reason: str | None = Field(default=None, max_length=500)
    acknowledge_missing: bool = False


def _to_detail_out(detail: use_cases.CandidateReviewDetail) -> CandidateReviewDetailOut:
    candidate = detail.candidate
    return CandidateReviewDetailOut(
        id=candidate.id,
        kind=candidate.draft.kind.value,
        review_status=candidate.review_status,
        created_at=candidate.created_at,
        scope=CandidateScopeOut(
            course_id=candidate.draft.scope.course_id,
            semester_id=candidate.draft.scope.semester_id,
            class_name=candidate.draft.scope.class_name,
            course_name=detail.course_name,
            semester_name=detail.semester_name,
        ),
        source=CandidateSourceOut(
            material_id=detail.material_id,
            material_title=detail.material_title,
            version_id=detail.version_id,
            version_no=detail.version_no,
        ),
        fields=[
            CandidateFieldOut(
                name=field.name,
                value=field.value,
                origin=field.origin,
                effective_value=field.effective_value,
                effective_origin=field.effective_origin,
                explanation=field.explanation,
                missing_reason=field.missing_reason,
                verifiable=field.verifiable,
                evidence=[CandidateEvidenceOut.model_validate(item) for item in field.evidence],
            )
            for field in detail.fields
        ],
        revisions=[
            FieldRevisionOut(
                id=record.id,
                field_name=record.revision.field_name,
                previous_value=record.revision.previous_value,
                new_value=record.revision.new_value,
                previous_origin=record.revision.previous_origin,
                new_origin=record.revision.new_origin,
                operator=record.revision.operator,
                note=record.revision.note,
                created_at=record.created_at,
            )
            for record in candidate.revisions
        ],
        review_reasons=[
            ReviewReasonOut(code=reason.code, message=reason.message, field=reason.field)
            for reason in candidate.draft.review_reasons
        ],
        review_changes=[
            ReviewChangeOut(
                id=record.id,
                from_status=record.change.from_status,
                to_status=record.change.to_status,
                operator=record.change.operator,
                reason=record.change.reason,
                changed_at=record.changed_at,
            )
            for record in candidate.review_changes
        ],
        missing_required_fields=list(detail.missing_required_fields),
        allowed_transitions=list(detail.allowed_transitions),
    )


@router.get("/candidates/{candidate_id}", response_model=CandidateReviewDetailOut)
def get_candidate_detail(
    candidate_id: int, workspace: CurrentWorkspace, repos: Repos
) -> CandidateReviewDetailOut:
    return _to_detail_out(use_cases.review_detail(repos, workspace.id, candidate_id))


@router.patch("/candidates/{candidate_id}/review-status", response_model=CandidateReviewDetailOut)
def patch_candidate_review_status(
    candidate_id: int,
    payload: ReviewStatusPatch,
    workspace: CurrentWorkspace,
    repos: Repos,
    operator: Annotated[str, Depends(require_session)],
) -> CandidateReviewDetailOut:
    """确认守卫仍由应用层负责：缺必需字段且未显式确认时返回 400 与中文原因。"""
    use_cases.set_candidate_review_status(
        repos,
        workspace.id,
        candidate_id,
        payload.status,
        operator=operator,
        reason=payload.reason,
        acknowledge_missing=payload.acknowledge_missing,
    )
    return _to_detail_out(use_cases.review_detail(repos, workspace.id, candidate_id))
