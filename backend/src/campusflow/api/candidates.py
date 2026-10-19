"""待核对事项 HTTP 路由。

包含 R001 列表、R008 核对详情与状态转换、R009 修正与生成入口、N011 新旧差异。
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from campusflow.api.auth import require_session
from campusflow.api.deps import get_current_workspace, get_repositories, get_version_relations
from campusflow.application import candidate_diff as diffs
from campusflow.application import (
    candidate_merge,
    change_impact,
    duplicates,
    version_acceptance,
    version_applicability,
)
from campusflow.application import candidates as use_cases
from campusflow.application import conversion as conversions
from campusflow.application.ports.candidates import CandidateSummaryData
from campusflow.application.ports.repositories import Repositories, WorkspaceData
from campusflow.application.ports.version_relations import VersionRelations
from campusflow.domain.candidate_diff import DiffStatus, FieldCategory, ScopeComponent
from campusflow.domain.candidates import FieldOrigin
from campusflow.domain.states import ReviewStatus

router = APIRouter(tags=["candidates"])

CurrentWorkspace = Annotated[WorkspaceData, Depends(get_current_workspace)]
Repos = Annotated[Repositories, Depends(get_repositories)]
Relations = Annotated[VersionRelations, Depends(get_version_relations)]


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
    curriculum_plan_id: int | None
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
        curriculum_plan_id=item.curriculum_plan_id,
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


class CandidateConversionOut(BaseModel):
    """候选已生成的正式事项；界面据此提供打开入口，不复制正式事项字段。"""

    kind: str
    object_id: int
    title: str


class CandidateConversionResultOut(CandidateConversionOut):
    """转换结果额外带 created：false 表示命中既有事项，本次没有新建。"""

    created: bool


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
    converted: CandidateConversionOut | None


class DiffFieldSideOut(BaseModel):
    """一侧字段；present=false 表示该侧没有这个字段，而不是字段值为空。"""

    candidate_id: int
    source_version_id: int
    present: bool
    value: Any
    effective_value: Any
    origin: FieldOrigin | None
    effective_origin: FieldOrigin | None
    explanation: str | None
    missing_reason: str | None
    explicitly_removed: bool
    verifiable: bool
    evidence: list[CandidateEvidenceOut]


class CandidateFieldDifferenceOut(BaseModel):
    """一个字段在新旧两侧的对照；左侧为基准/旧，右侧为待比较/新。"""

    name: str
    category: FieldCategory
    status: DiffStatus
    left: DiffFieldSideOut
    right: DiffFieldSideOut


class ScopeDifferenceOut(BaseModel):
    component: ScopeComponent
    status: DiffStatus
    left_value: int | str | None
    right_value: int | str | None
    left_name: str | None
    right_name: str | None


class ScopeComparisonOut(BaseModel):
    left: CandidateScopeOut
    right: CandidateScopeOut
    differences: list[ScopeDifferenceOut]


class CandidateDiffSourceOut(BaseModel):
    """一侧候选及其来源版本；字段证据由逐字段条目给出。"""

    candidate_id: int
    source_version_id: int
    material_id: int
    material_title: str
    version_no: int
    material_archived: bool
    kind: str
    review_status: ReviewStatus
    scope: CandidateScopeOut


class DiffSummaryOut(BaseModel):
    total: int
    unchanged: int
    changed: int
    added: int
    missing: int
    explicitly_removed: int


class CandidateDiffOut(BaseModel):
    """只读新旧对照；不判断哪个版本生效，也不自动配对候选。"""

    left_role: Literal["baseline"] = "baseline"
    right_role: Literal["comparison"] = "comparison"
    comparison_basis: Literal["effective_value"] = "effective_value"
    left: CandidateDiffSourceOut
    right: CandidateDiffSourceOut
    scope: ScopeComparisonOut
    fields: list[CandidateFieldDifferenceOut]
    summary: DiffSummaryOut


class ReviewStatusPatch(BaseModel):
    """核对状态转换：只接受 ReviewStatus，与任务执行进度是两套状态。"""

    status: ReviewStatus
    reason: str | None = Field(default=None, max_length=500)
    acknowledge_missing: bool = False


class FieldRevisionPatch(BaseModel):
    """R002 逐字段修正：只追加修正记录，原始提取与证据不会被改写。"""

    value: Any
    origin: FieldOrigin
    note: str | None = Field(default=None, max_length=1000)


def _to_conversion_out(
    conversion: conversions.CandidateConversion | None,
) -> CandidateConversionOut | None:
    if conversion is None:
        return None
    return CandidateConversionOut(
        kind=conversion.kind.value,
        object_id=conversion.object_id,
        title=conversion.title,
    )


def _detail_out(
    repos: Repositories, workspace_id: int, candidate_id: int
) -> CandidateReviewDetailOut:
    """详情与已生成的正式事项一起返回，界面据此决定是否给出打开入口。"""
    detail = use_cases.review_detail(repos, workspace_id, candidate_id)
    return _to_detail_out(detail, conversions.find_candidate_conversion(repos, detail.candidate))


def _to_detail_out(
    detail: use_cases.CandidateReviewDetail,
    conversion: conversions.CandidateConversion | None,
) -> CandidateReviewDetailOut:
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
        converted=_to_conversion_out(conversion),
    )


def _diff_scope_out(scope: diffs.ScopeView) -> CandidateScopeOut:
    return CandidateScopeOut(
        course_id=scope.course_id,
        semester_id=scope.semester_id,
        class_name=scope.class_name,
        course_name=scope.course_name,
        semester_name=scope.semester_name,
    )


def _diff_side_out(side: diffs.DiffFieldSide) -> DiffFieldSideOut:
    return DiffFieldSideOut(
        candidate_id=side.candidate_id,
        source_version_id=side.source_version_id,
        present=side.present,
        value=side.value,
        effective_value=side.effective_value,
        origin=side.origin,
        effective_origin=side.effective_origin,
        explanation=side.explanation,
        missing_reason=side.missing_reason,
        explicitly_removed=side.explicitly_removed,
        verifiable=side.verifiable,
        evidence=[CandidateEvidenceOut.model_validate(item) for item in side.evidence],
    )


def _diff_source_out(source: diffs.DiffSourceView) -> CandidateDiffSourceOut:
    return CandidateDiffSourceOut(
        candidate_id=source.candidate_id,
        source_version_id=source.source_version_id,
        material_id=source.material_id,
        material_title=source.material_title,
        version_no=source.version_no,
        material_archived=source.material_archived,
        kind=source.kind.value,
        review_status=source.review_status,
        scope=_diff_scope_out(source.scope),
    )


def _diff_out(diff: diffs.CandidateDiff) -> CandidateDiffOut:
    return CandidateDiffOut(
        left=_diff_source_out(diff.left),
        right=_diff_source_out(diff.right),
        scope=ScopeComparisonOut(
            left=_diff_scope_out(diff.scope.left),
            right=_diff_scope_out(diff.scope.right),
            differences=[
                ScopeDifferenceOut(
                    component=item.component,
                    status=item.status,
                    left_value=item.left_value,
                    right_value=item.right_value,
                    left_name=item.left_name,
                    right_name=item.right_name,
                )
                for item in diff.scope.differences
            ],
        ),
        fields=[
            CandidateFieldDifferenceOut(
                name=item.name,
                category=item.category,
                status=item.status,
                left=_diff_side_out(item.left),
                right=_diff_side_out(item.right),
            )
            for item in diff.fields
        ],
        summary=DiffSummaryOut(
            total=diff.summary.total,
            unchanged=diff.summary.unchanged,
            changed=diff.summary.changed,
            added=diff.summary.added,
            missing=diff.summary.missing,
            explicitly_removed=diff.summary.explicitly_removed,
        ),
    )


@router.get("/candidates/{candidate_id}", response_model=CandidateReviewDetailOut)
def get_candidate_detail(
    candidate_id: int, workspace: CurrentWorkspace, repos: Repos
) -> CandidateReviewDetailOut:
    return _detail_out(repos, workspace.id, candidate_id)


@router.get(
    "/candidates/{candidate_id}/diff/{other_candidate_id}",
    response_model=CandidateDiffOut,
)
def get_candidate_diff(
    candidate_id: int,
    other_candidate_id: int,
    workspace: CurrentWorkspace,
    repos: Repos,
) -> CandidateDiffOut:
    """只读新旧差异：candidate_id 为基准/旧，other_candidate_id 为待比较/新。"""
    return _diff_out(
        diffs.compare_candidates(repos, workspace.id, candidate_id, other_candidate_id)
    )


class ImpactedBlockOut(BaseModel):
    id: int
    draft_id: int
    draft_status: str
    title: str
    starts_at: str
    ends_at: str
    locked: bool


class ImpactedTaskOut(BaseModel):
    id: int
    title: str
    progress: str
    completed: bool
    due_date: str | None
    due_time: str | None
    source_state: str
    plan_blocks: list[ImpactedBlockOut]


class ImpactedEventOut(BaseModel):
    id: int
    title: str
    starts_at: str
    ends_at: str
    location: str | None


class ImpactedRuleOut(BaseModel):
    id: int
    title: str


class ChangeImpactOut(BaseModel):
    summary: dict
    unsupported: list[dict]
    tasks: list[ImpactedTaskOut]
    fixed_events: list[ImpactedEventOut]
    rules: list[ImpactedRuleOut]
    diff: CandidateDiffOut


class ApplicabilityReasonOut(BaseModel):
    code: str
    message: str


class PublicationSideOut(BaseModel):
    material_id: int
    version_id: int
    publisher: str | None
    published_at: str | None
    published_text: str | None


class VersionApplicabilityOut(BaseModel):
    status: Literal["applicable", "conflict", "requires_review"]
    recommended_candidate_id: int | None
    review_required: bool
    applied: Literal[False] = False
    relation_id: int | None
    relation_kind: str | None
    scope_comparison: Literal["same", "different", "unknown"]
    publication: dict[str, PublicationSideOut]
    reasons: list[ApplicabilityReasonOut]
    diff: CandidateDiffOut


@router.get(
    "/candidates/{candidate_id}/diff/{other_candidate_id}/impact",
    response_model=ChangeImpactOut,
)
def get_candidate_change_impact(
    candidate_id: int,
    other_candidate_id: int,
    workspace: CurrentWorkspace,
    repos: Repos,
) -> ChangeImpactOut:
    """N013：只读列出旧候选已转换的任务、日程、规则及计划时间块。"""
    result = change_impact.collect_impact(repos, workspace.id, candidate_id, other_candidate_id)
    return ChangeImpactOut(
        summary=result.summary,
        unsupported=result.unsupported,
        tasks=[
            ImpactedTaskOut(
                id=item.id,
                title=item.title,
                progress=item.progress,
                completed=item.completed,
                due_date=item.due_date,
                due_time=item.due_time,
                source_state=item.source_state,
                plan_blocks=[ImpactedBlockOut(**block.__dict__) for block in item.plan_blocks],
            )
            for item in result.tasks
        ],
        fixed_events=[ImpactedEventOut(**item.__dict__) for item in result.fixed_events],
        rules=[ImpactedRuleOut(**item.__dict__) for item in result.rules],
        diff=_diff_out(result.diff),
    )


@router.get(
    "/candidates/{candidate_id}/diff/{other_candidate_id}/applicability",
    response_model=VersionApplicabilityOut,
)
def get_candidate_applicability(
    candidate_id: int,
    other_candidate_id: int,
    workspace: CurrentWorkspace,
    repos: Repos,
    relations: Relations,
) -> VersionApplicabilityOut:
    """N012：只判定生效候选或冲突；不写库，接受变更由 N014 处理。"""
    result = version_applicability.assess_applicability(
        repos, relations, workspace.id, candidate_id, other_candidate_id
    )
    return VersionApplicabilityOut(
        status=result.status,
        recommended_candidate_id=result.recommended_candidate_id,
        review_required=result.review_required,
        relation_id=result.relation_id,
        relation_kind=result.relation_kind,
        scope_comparison=result.scope_comparison,
        publication={key: PublicationSideOut(**value) for key, value in result.publication.items()},
        reasons=[
            ApplicabilityReasonOut(code=item.code, message=item.message) for item in result.reasons
        ],
        diff=_diff_out(result.diff),
    )


class DuplicateSuggestionOut(BaseModel):
    candidate_id: int
    source_version_id: int
    title: str | None
    score: float
    reasons: list[str]
    matched_fields: list[str]
    review_status: str
    scope_same: bool


class ExcludedCandidateOut(BaseModel):
    candidate_id: int
    code: str
    message: str


class DuplicateReportOut(BaseModel):
    candidate_id: int
    kind: str
    suggestions: list[DuplicateSuggestionOut]
    excluded: list[ExcludedCandidateOut]
    auto_merge: Literal[False] = False


@router.get("/candidates/{candidate_id}/duplicates", response_model=DuplicateReportOut)
def get_candidate_duplicates(
    candidate_id: int,
    workspace: CurrentWorkspace,
    repos: Repos,
) -> DuplicateReportOut:
    """N015：只读重复建议，不删除、不合并、不改核对状态。"""
    report = duplicates.suggest_duplicates(repos, workspace.id, candidate_id)
    return DuplicateReportOut(
        candidate_id=report.candidate_id,
        kind=report.kind,
        suggestions=[
            DuplicateSuggestionOut(
                candidate_id=item.candidate_id,
                source_version_id=item.source_version_id,
                title=item.title,
                score=item.score,
                reasons=list(item.reasons),
                matched_fields=list(item.matched_fields),
                review_status=item.review_status,
                scope_same=item.scope_same,
            )
            for item in report.suggestions
        ],
        excluded=[
            ExcludedCandidateOut(
                candidate_id=item.candidate_id, code=item.code, message=item.message
            )
            for item in report.excluded
        ],
    )


class VersionAcceptanceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field_names: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=1000)
    acknowledge_conflict: bool = False


class VersionAcceptanceOut(BaseModel):
    id: int
    baseline_candidate_id: int
    comparison_candidate_id: int
    baseline_version_id: int
    comparison_version_id: int
    accepted_fields: list[str]
    reason: str
    operator: str
    evidence_snapshot: list[dict]
    previous_review_status: str
    created_at: datetime
    baseline_review_status: str
    created: bool


def _acceptance_out(result, *, created: bool) -> VersionAcceptanceOut:
    record = result.record
    return VersionAcceptanceOut(
        id=record.id,
        baseline_candidate_id=record.baseline_candidate_id,
        comparison_candidate_id=record.comparison_candidate_id,
        baseline_version_id=record.baseline_version_id,
        comparison_version_id=record.comparison_version_id,
        accepted_fields=list(record.accepted_fields),
        reason=record.reason,
        operator=record.operator,
        evidence_snapshot=list(record.evidence_snapshot),
        previous_review_status=record.previous_review_status,
        created_at=record.created_at,
        baseline_review_status="superseded" if created else result.baseline_review_status,
        created=created,
    )


@router.post(
    "/candidates/{candidate_id}/diff/{other_candidate_id}/accept",
    response_model=VersionAcceptanceOut,
    status_code=201,
)
def post_version_acceptance(
    candidate_id: int,
    other_candidate_id: int,
    payload: VersionAcceptanceIn,
    workspace: CurrentWorkspace,
    repos: Repos,
    relations: Relations,
    operator: Annotated[str, Depends(require_session)],
    response: Response,
) -> VersionAcceptanceOut:
    """N014：接受具体字段差异；事务内失效旧候选并保存证据历史。"""
    result = version_acceptance.accept_change(
        repos,
        relations,
        workspace.id,
        baseline_candidate_id=candidate_id,
        comparison_candidate_id=other_candidate_id,
        field_names=tuple(payload.field_names),
        reason=payload.reason,
        operator=operator,
        acknowledge_conflict=payload.acknowledge_conflict,
    )
    response.status_code = 201 if result.created else 200
    return _acceptance_out(result, created=result.created)


@router.get(
    "/candidates/{candidate_id}/acceptances",
    response_model=list[VersionAcceptanceOut],
)
def get_version_acceptances(
    candidate_id: int,
    workspace: CurrentWorkspace,
    repos: Repos,
    relations: Relations,
) -> list[VersionAcceptanceOut]:
    use_cases.get_candidate(repos, workspace.id, candidate_id)
    return [
        _acceptance_out(
            version_acceptance.AcceptanceResult(record, False, record.previous_review_status),
            created=False,
        )
        for record in version_acceptance.list_acceptances(relations, workspace.id, candidate_id)
    ]


class MergeSourceOut(BaseModel):
    candidate_id: int
    version_id: int
    title: str | None
    review_status: str
    formal_kind: str
    formal_id: int | None
    progress: str | None
    plan_block_count: int


class MergePreviewOut(BaseModel):
    target_candidate_id: int
    kind: str
    target_title: str | None
    target_formal_id: int | None
    target_progress: str | None
    kept_fields: dict
    sources: list[MergeSourceOut]
    token: str
    auto_merge: Literal[False] = False


class MergeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_candidate_ids: list[int] = Field(min_length=1)
    preview_token: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=1000)


class MergeRecordOut(BaseModel):
    id: int
    target_candidate_id: int
    source_candidate_id: int
    target_formal_kind: str | None
    target_formal_id: int | None
    source_formal_id: int | None
    preserved_progress: dict
    source_snapshot: dict
    reason: str
    operator: str
    created_at: datetime


class MergeResultOut(BaseModel):
    records: list[MergeRecordOut]
    created: bool
    preview: MergePreviewOut


def _merge_preview_out(preview) -> MergePreviewOut:
    return MergePreviewOut(
        target_candidate_id=preview.target_candidate_id,
        kind=preview.kind,
        target_title=preview.target_title,
        target_formal_id=preview.target_formal_id,
        target_progress=preview.target_progress,
        kept_fields=preview.kept_fields,
        sources=[MergeSourceOut(**item.__dict__) for item in preview.sources],
        token=preview.token,
    )


@router.get("/candidates/{candidate_id}/merge-preview", response_model=MergePreviewOut)
def get_merge_preview(
    candidate_id: int,
    workspace: CurrentWorkspace,
    repos: Repos,
    source_ids: Annotated[list[int] | None, Query()] = None,
) -> MergePreviewOut:
    return _merge_preview_out(
        candidate_merge.preview_merge(repos, workspace.id, candidate_id, tuple(source_ids or []))
    )


@router.post("/candidates/{candidate_id}/merge", response_model=MergeResultOut)
def post_merge_candidates(
    candidate_id: int,
    payload: MergeIn,
    workspace: CurrentWorkspace,
    repos: Repos,
    operator: Annotated[str, Depends(require_session)],
) -> MergeResultOut:
    result = candidate_merge.execute_merge(
        repos,
        workspace.id,
        target_candidate_id=candidate_id,
        source_ids=tuple(payload.source_candidate_ids),
        preview_token=payload.preview_token,
        reason=payload.reason,
        operator=operator,
    )
    return MergeResultOut(
        records=[MergeRecordOut(**record.__dict__) for record in result.records],
        created=result.created,
        preview=_merge_preview_out(result.preview),
    )


@router.get("/candidates/{candidate_id}/merges", response_model=list[MergeRecordOut])
def get_candidate_merges(
    candidate_id: int, workspace: CurrentWorkspace, repos: Repos
) -> list[MergeRecordOut]:
    return [
        MergeRecordOut(**record.__dict__)
        for record in candidate_merge.list_merges(repos, workspace.id, candidate_id)
    ]


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
    return _detail_out(repos, workspace.id, candidate_id)


@router.patch(
    "/candidates/{candidate_id}/fields/{field_name}", response_model=CandidateReviewDetailOut
)
def patch_candidate_field(
    candidate_id: int,
    field_name: str,
    payload: FieldRevisionPatch,
    workspace: CurrentWorkspace,
    repos: Repos,
    operator: Annotated[str, Depends(require_session)],
) -> CandidateReviewDetailOut:
    """逐字段修正复用 R002 用例：追加修正记录，原始提取与证据不改写。

    用户填写的值只能标为用户补充或规划建议；标成原文规定或系统推断会被
    应用层拒绝（缺原文证据），错误以中文原因返回。
    """
    use_cases.revise_candidate_field(
        repos,
        workspace.id,
        candidate_id,
        field_name,
        value=payload.value,
        origin=payload.origin,
        operator=operator,
        note=payload.note,
    )
    return _detail_out(repos, workspace.id, candidate_id)


@router.post("/candidates/{candidate_id}/convert", response_model=CandidateConversionResultOut)
def post_candidate_conversion(
    candidate_id: int,
    workspace: CurrentWorkspace,
    repos: Repos,
) -> CandidateConversionResultOut:
    """把已确认候选转换为正式事项；重复调用返回既有事项并把 created 置为 false。"""
    conversion, created = conversions.convert_candidate(repos, workspace.id, candidate_id)
    return CandidateConversionResultOut(
        kind=conversion.kind.value,
        object_id=conversion.object_id,
        title=conversion.title,
        created=created,
    )
