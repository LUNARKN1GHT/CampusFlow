"""N011：读取两个候选的字段差异。只读用例，不判断版本生效、不写任何表。"""

from collections import Counter
from dataclasses import dataclass

from campusflow.application.candidates import (
    CandidateEvidenceView,
    CandidateFieldView,
    field_view,
    get_candidate,
)
from campusflow.application.ports.candidates import CandidateData
from campusflow.application.ports.repositories import Repositories
from campusflow.domain.candidate_diff import (
    DiffStatus,
    FieldCategory,
    FieldState,
    ScopeComponent,
    classify_field,
    classify_scope,
    field_category,
    field_sort_key,
)
from campusflow.domain.candidates import CandidateKind, FieldOrigin, JsonValue
from campusflow.domain.errors import DomainError, NotFoundError
from campusflow.domain.states import ReviewStatus


@dataclass(frozen=True)
class ScopeView:
    semester_id: int | None
    semester_name: str | None
    course_id: int | None
    course_name: str | None
    class_name: str | None


@dataclass(frozen=True)
class DiffSourceView:
    candidate_id: int
    source_version_id: int
    material_id: int
    material_title: str
    version_no: int
    material_archived: bool
    kind: CandidateKind
    review_status: ReviewStatus
    scope: ScopeView


@dataclass(frozen=True)
class DiffFieldSide:
    """一侧字段；字段不存在时仍返回本对象，用 present=false 区分“未提及”和“值为空”。"""

    candidate_id: int
    source_version_id: int
    present: bool
    value: JsonValue
    effective_value: JsonValue
    origin: FieldOrigin | None
    effective_origin: FieldOrigin | None
    explanation: str | None
    missing_reason: str | None
    explicitly_removed: bool
    verifiable: bool
    evidence: tuple[CandidateEvidenceView, ...]


@dataclass(frozen=True)
class FieldDifference:
    name: str
    category: FieldCategory
    status: DiffStatus
    left: DiffFieldSide
    right: DiffFieldSide


@dataclass(frozen=True)
class ScopeDifference:
    component: ScopeComponent
    status: DiffStatus
    left_value: int | str | None
    right_value: int | str | None
    left_name: str | None
    right_name: str | None


@dataclass(frozen=True)
class ScopeComparison:
    left: ScopeView
    right: ScopeView
    differences: tuple[ScopeDifference, ...]


@dataclass(frozen=True)
class DiffSummary:
    total: int
    unchanged: int
    changed: int
    added: int
    missing: int
    explicitly_removed: int


@dataclass(frozen=True)
class CandidateDiff:
    left: DiffSourceView
    right: DiffSourceView
    scope: ScopeComparison
    fields: tuple[FieldDifference, ...]
    summary: DiffSummary


@dataclass(frozen=True)
class _Side:
    header: DiffSourceView
    fields: dict[str, CandidateFieldView]


def _load_side(repos: Repositories, workspace_id: int, candidate: CandidateData) -> _Side:
    version = repos.materials.get_version(candidate.source_version_id)
    material = repos.materials.get(version.material_id) if version is not None else None
    if version is None or material is None or material.workspace_id != workspace_id:
        raise NotFoundError("候选来源资料不存在")
    chunks = {chunk.id: chunk for chunk in repos.materials.list_chunks(version.id)}
    effective = {field.name: field for field in candidate.effective_draft().fields}
    fields = {
        field.name: field_view(field, effective[field.name], chunks)
        for field in candidate.draft.fields
    }
    scope = candidate.draft.scope
    semester = repos.semesters.get(scope.semester_id) if scope.semester_id is not None else None
    course = repos.courses.get(scope.course_id) if scope.course_id is not None else None
    # 名称只在属于当前空间时展示，查不到就留空，不伪造归属。
    scope_view = ScopeView(
        semester_id=scope.semester_id,
        semester_name=(
            semester.name
            if semester is not None and semester.workspace_id == workspace_id
            else None
        ),
        course_id=scope.course_id,
        course_name=(
            course.name if course is not None and course.workspace_id == workspace_id else None
        ),
        class_name=scope.class_name,
    )
    header = DiffSourceView(
        candidate_id=candidate.id,
        source_version_id=candidate.source_version_id,
        material_id=material.id,
        material_title=material.title,
        version_no=version.version_no,
        material_archived=material.archived,
        kind=candidate.draft.kind,
        review_status=candidate.review_status,
        scope=scope_view,
    )
    return _Side(header=header, fields=fields)


def _present_field(
    header: DiffSourceView, view: CandidateFieldView
) -> tuple[DiffFieldSide, FieldState]:
    verifiable_count = sum(1 for item in view.evidence if item.verifiable)
    state = FieldState(
        present=True,
        value=view.value,
        effective_value=view.effective_value,
        verifiable_evidence_count=verifiable_count,
    )
    side = DiffFieldSide(
        candidate_id=header.candidate_id,
        source_version_id=header.source_version_id,
        present=True,
        value=view.value,
        effective_value=view.effective_value,
        origin=view.origin,
        effective_origin=view.effective_origin,
        explanation=view.explanation,
        missing_reason=view.missing_reason,
        explicitly_removed=state.explicitly_removed,
        verifiable=view.verifiable,
        evidence=view.evidence,
    )
    return side, state


def _absent_field(header: DiffSourceView) -> tuple[DiffFieldSide, FieldState]:
    state = FieldState(present=False, value=None, effective_value=None)
    side = DiffFieldSide(
        candidate_id=header.candidate_id,
        source_version_id=header.source_version_id,
        present=False,
        value=None,
        effective_value=None,
        origin=None,
        effective_origin=None,
        explanation=None,
        missing_reason=None,
        explicitly_removed=False,
        verifiable=False,
        evidence=(),
    )
    return side, state


def _scope_differences(left: ScopeView, right: ScopeView) -> tuple[ScopeDifference, ...]:
    pairs = (
        (
            ScopeComponent.SEMESTER,
            left.semester_id,
            right.semester_id,
            left.semester_name,
            right.semester_name,
        ),
        (
            ScopeComponent.COURSE,
            left.course_id,
            right.course_id,
            left.course_name,
            right.course_name,
        ),
        (
            ScopeComponent.CLASS_NAME,
            left.class_name,
            right.class_name,
            left.class_name,
            right.class_name,
        ),
    )
    return tuple(
        ScopeDifference(
            component=component,
            status=classify_scope(left_value, right_value),
            left_value=left_value,
            right_value=right_value,
            left_name=left_name,
            right_name=right_name,
        )
        for component, left_value, right_value, left_name, right_name in pairs
    )


def _summary(statuses: list[DiffStatus]) -> DiffSummary:
    counts = Counter(statuses)
    return DiffSummary(
        total=len(statuses),
        unchanged=counts[DiffStatus.UNCHANGED],
        changed=counts[DiffStatus.CHANGED],
        added=counts[DiffStatus.ADDED],
        missing=counts[DiffStatus.MISSING],
        explicitly_removed=counts[DiffStatus.EXPLICITLY_REMOVED],
    )


def compare_candidates(
    repos: Repositories,
    workspace_id: int,
    left_candidate_id: int,
    right_candidate_id: int,
) -> CandidateDiff:
    """比较两个显式指定的同类型候选；left 是基准/旧，right 是待比较/新。"""
    if left_candidate_id == right_candidate_id:
        raise DomainError("不能比较同一个候选事项")
    left_candidate = get_candidate(repos, workspace_id, left_candidate_id)
    right_candidate = get_candidate(repos, workspace_id, right_candidate_id)
    if left_candidate.draft.kind is not right_candidate.draft.kind:
        raise DomainError("只能比较同一类型的候选事项（任务、日程或规则）")

    left = _load_side(repos, workspace_id, left_candidate)
    right = _load_side(repos, workspace_id, right_candidate)
    names = sorted(set(left.fields) | set(right.fields), key=field_sort_key)
    differences: list[FieldDifference] = []
    for name in names:
        left_view = left.fields.get(name)
        right_view = right.fields.get(name)
        left_side, left_state = (
            _present_field(left.header, left_view)
            if left_view is not None
            else _absent_field(left.header)
        )
        right_side, right_state = (
            _present_field(right.header, right_view)
            if right_view is not None
            else _absent_field(right.header)
        )
        differences.append(
            FieldDifference(
                name=name,
                category=field_category(name),
                status=classify_field(left_state, right_state),
                left=left_side,
                right=right_side,
            )
        )
    scope = ScopeComparison(
        left=left.header.scope,
        right=right.header.scope,
        differences=_scope_differences(left.header.scope, right.header.scope),
    )
    # 只读用例：不提交事务，不调用任何写方法。
    return CandidateDiff(
        left=left.header,
        right=right.header,
        scope=scope,
        fields=tuple(differences),
        summary=_summary([item.status for item in differences]),
    )
