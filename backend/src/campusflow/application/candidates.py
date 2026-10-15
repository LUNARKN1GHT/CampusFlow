"""候选保存、核对列表与核对详情边界：核对空间、真实片段与摘录后原子保存，按核对状态和缺失原因列出，详情给出逐字段原始值与生效值及引用可验证状态。"""

from dataclasses import dataclass

from campusflow.application.materials import _require_associations, get_material
from campusflow.application.ports.candidates import CandidateData, CandidateSummaryData
from campusflow.application.ports.repositories import Repositories, SourceChunkData
from campusflow.application.source_uncertainty import refresh_conflict_reasons, scope_key
from campusflow.domain.candidates import (
    REVIEW_TRANSITIONS,
    CandidateDraft,
    CandidateField,
    FieldEvidence,
    FieldOrigin,
    FieldRevision,
    JsonValue,
    ReviewChange,
    apply_revisions,
    missing_required_fields,
    validate_candidate,
    validate_field_revision,
    validate_review_change,
)
from campusflow.domain.errors import DomainError, NotFoundError
from campusflow.domain.source_detail import citation_is_verifiable
from campusflow.domain.states import ReviewStatus


def save_candidates(
    repos: Repositories,
    workspace_id: int,
    material_id: int,
    version_no: int,
    drafts: list[CandidateDraft],
) -> list[CandidateData]:
    material = get_material(repos, workspace_id, material_id)
    if material.archived:
        raise DomainError("资料已归档，请恢复后再提取候选事项")
    versions = repos.materials.list_versions(material_id)
    version = next((v for v in versions if v.version_no == version_no), None)
    if version is None:
        raise NotFoundError("来源版本不存在")
    if len(drafts) > 100:
        raise DomainError("单次最多保存 100 个候选事项")
    sources = {chunk.id: chunk for chunk in repos.materials.list_chunks(version.id)}
    # 先验证整批，避免后面的候选不合法时留下前面的半成品。
    for draft in drafts:
        validate_candidate(draft)
        _require_associations(repos, workspace_id, draft.scope.semester_id, draft.scope.course_id)
        if (
            material.semester_id is not None
            and draft.scope.semester_id is not None
            and material.semester_id != draft.scope.semester_id
        ):
            raise DomainError("候选学期与资料适用学期不一致")
        if draft.scope.course_id is not None:
            course = repos.courses.get(draft.scope.course_id)
            if (
                material.semester_id is not None
                and course.semester_id != material.semester_id
                or material.course_id is not None
                and course.id != material.course_id
            ):
                raise DomainError("候选课程与资料适用范围不一致")
        for field in draft.fields:
            for evidence in field.evidence:
                source = sources.get(evidence.chunk_id)
                if source is None:
                    raise DomainError("证据片段不属于当前原文版本，或来源已不可用")
                if evidence.quote not in source.text:
                    raise DomainError("证据摘录无法在对应原文片段中找到")
    try:
        created = [repos.candidates.create(workspace_id, version.id, draft) for draft in drafts]
        # X008：保存后重算同一适用范围内的取值冲突。先入库再比对，才能发现跨来源
        # 的不一致，并把冲突原因同时挂到新旧两侧——冲突证据并列保留，不删任何一侧。
        refresh_conflict_reasons(repos, workspace_id, [scope_key(d.scope) for d in drafts])
        # 刷新可能给本批候选补上了冲突原因，重新读取以返回最新内容
        result = [
            item
            for item in (repos.candidates.get(workspace_id, c.id) for c in created)
            if item is not None
        ]
        repos.uow.commit()
    except Exception:
        repos.uow.rollback()
        raise
    return result


def get_candidate(repos: Repositories, workspace_id: int, candidate_id: int) -> CandidateData:
    candidate = repos.candidates.get(workspace_id, candidate_id)
    if candidate is None:
        raise NotFoundError("候选事项不存在")
    return candidate


def revise_candidate_field(
    repos: Repositories,
    workspace_id: int,
    candidate_id: int,
    field_name: str,
    *,
    value: JsonValue,
    origin: FieldOrigin,
    operator: str,
    note: str | None = None,
) -> CandidateData:
    """追加一条字段修正，保留原始提取与证据，返回叠加修正后的候选。"""
    candidate = get_candidate(repos, workspace_id, candidate_id)
    field = next((item for item in candidate.draft.fields if item.name == field_name), None)
    if field is None:
        raise DomainError("候选事项中没有这个字段")
    current = apply_revisions(field, candidate.field_revisions(field_name))
    revision = FieldRevision(
        field_name=field_name,
        previous_value=current.value,
        new_value=value,
        previous_origin=current.origin,
        new_origin=origin,
        operator=operator,
        note=note,
    )
    validate_field_revision(current, revision)
    try:
        repos.candidates.add_field_revision(workspace_id, candidate_id, revision)
        repos.uow.commit()
    except Exception:
        repos.uow.rollback()
        raise
    return get_candidate(repos, workspace_id, candidate_id)


def _require_confirmation_ready(
    candidate: CandidateData, acknowledge_missing: bool, reason: str | None
) -> None:
    missing = missing_required_fields(candidate.draft.kind, candidate.effective_draft().fields)
    if not missing:
        return
    if not acknowledge_missing:
        raise DomainError(
            "缺少必须字段：" + "、".join(missing) + "；请先补充字段，或显式确认后继续"
        )
    if reason is None or not reason.strip():
        raise DomainError("缺少必须字段时显式确认必须填写原因")


def set_candidate_review_status(
    repos: Repositories,
    workspace_id: int,
    candidate_id: int,
    status: ReviewStatus,
    *,
    operator: str,
    reason: str | None = None,
    acknowledge_missing: bool = False,
) -> CandidateData:
    """按合法转换更新候选核对状态并留痕；确认前检查必须字段。"""
    candidate = get_candidate(repos, workspace_id, candidate_id)
    change = ReviewChange(candidate.review_status, status, operator, reason)
    validate_review_change(change)
    if status is ReviewStatus.CONFIRMED:
        _require_confirmation_ready(candidate, acknowledge_missing, reason)
    try:
        repos.candidates.set_review_status(workspace_id, candidate_id, change)
        repos.uow.commit()
    except Exception:
        repos.uow.rollback()
        raise
    return get_candidate(repos, workspace_id, candidate_id)


DEFAULT_REVIEW_STATUSES = (ReviewStatus.PENDING,)


def _require_review_filters_in_workspace(
    repos: Repositories,
    workspace_id: int,
    *,
    course_id: int | None,
    semester_id: int | None,
    material_id: int | None,
) -> None:
    _require_associations(repos, workspace_id, semester_id, course_id)
    if material_id is not None:
        get_material(repos, workspace_id, material_id)


def list_review_candidates(
    repos: Repositories,
    workspace_id: int,
    *,
    statuses: tuple[ReviewStatus, ...] = DEFAULT_REVIEW_STATUSES,
    kinds: tuple[str, ...] | None = None,
    course_id: int | None = None,
    semester_id: int | None = None,
    material_id: int | None = None,
    reason_code: str | None = None,
) -> list[CandidateSummaryData]:
    """R001：按核对状态、类型、适用范围与缺失原因列出候选。

    核对状态与任务执行进度是两套独立状态，这里只接受 ReviewStatus；
    默认只看待核对，已确认／已忽略／已失效需显式指定。
    """
    _require_review_filters_in_workspace(
        repos, workspace_id, course_id=course_id, semester_id=semester_id, material_id=material_id
    )
    summaries = repos.candidates.list_for_review(
        workspace_id,
        review_statuses=statuses,
        kinds=kinds,
        course_id=course_id,
        semester_id=semester_id,
        material_id=material_id,
    )
    if reason_code is not None:
        summaries = [
            item
            for item in summaries
            if any(code == reason_code for code, _, _ in item.review_reasons)
        ]
    return summaries


@dataclass(frozen=True)
class ReviewCounts:
    """待核对列表计数：按核对状态与缺失原因分别统计，供界面显示。"""

    total: int
    by_status: dict[str, int]
    by_reason: dict[str, int]


def review_counts(
    repos: Repositories,
    workspace_id: int,
    *,
    kinds: tuple[str, ...] | None = None,
    course_id: int | None = None,
    semester_id: int | None = None,
    material_id: int | None = None,
) -> ReviewCounts:
    """统计全部核对状态下的候选数量，覆盖范围与列表一致（不含原因筛选）。"""
    _require_review_filters_in_workspace(
        repos, workspace_id, course_id=course_id, semester_id=semester_id, material_id=material_id
    )
    summaries = repos.candidates.list_for_review(
        workspace_id,
        kinds=kinds,
        course_id=course_id,
        semester_id=semester_id,
        material_id=material_id,
    )
    by_status: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for item in summaries:
        by_status[item.review_status.value] = by_status.get(item.review_status.value, 0) + 1
        for code in {reason_code for reason_code, _, _ in item.review_reasons}:
            by_reason[code] = by_reason.get(code, 0) + 1
    return ReviewCounts(len(summaries), by_status, by_reason)


# R008：前端核对界面的详情读模型。转换按钮按固定顺序展示，界面才不会出现闪烁的可选状态。
_TRANSITION_ORDER = (
    ReviewStatus.CONFIRMED,
    ReviewStatus.IGNORED,
    ReviewStatus.SUPERSEDED,
    ReviewStatus.PENDING,
)


@dataclass(frozen=True)
class CandidateEvidenceView:
    chunk_id: int
    quote: str
    locator_type: str
    locator_value: str
    verifiable: bool


@dataclass(frozen=True)
class CandidateFieldView:
    """一个候选字段的原始提取值与叠加 R002 修正后的生效值，以及仍可定位的引用。"""

    name: str
    value: JsonValue
    origin: FieldOrigin
    effective_value: JsonValue
    effective_origin: FieldOrigin
    explanation: str | None
    missing_reason: str | None
    evidence: tuple[CandidateEvidenceView, ...]
    verifiable: bool


@dataclass(frozen=True)
class CandidateReviewDetail:
    candidate: CandidateData
    material_id: int
    material_title: str
    version_id: int
    version_no: int
    course_name: str | None
    semester_name: str | None
    fields: tuple[CandidateFieldView, ...]
    missing_required_fields: tuple[str, ...]
    allowed_transitions: tuple[ReviewStatus, ...]


def _evidence_view(
    evidence: FieldEvidence, chunks: dict[int, SourceChunkData]
) -> CandidateEvidenceView:
    chunk = chunks.get(evidence.chunk_id)
    return CandidateEvidenceView(
        chunk_id=evidence.chunk_id,
        quote=evidence.quote,
        # 片段已不存在时不返回定位信息，否则界面会把跳不过去的原文位置显示成可核对的引用
        locator_type=chunk.locator_type if chunk is not None else "",
        locator_value=chunk.locator_value if chunk is not None else "",
        verifiable=citation_is_verifiable(
            chunk.text if chunk is not None else None, evidence.quote
        ),
    )


def _field_view(
    field: CandidateField, effective: CandidateField, chunks: dict[int, SourceChunkData]
) -> CandidateFieldView:
    """原始字段决定引用与可验证状态（沿用 R007 规则）；说明与缺失原因展示生效值。"""
    evidence = tuple(_evidence_view(item, chunks) for item in field.evidence)
    verifiable = all(item.verifiable for item in evidence)
    if (
        field.origin in (FieldOrigin.DOCUMENT, FieldOrigin.INFERRED)
        and field.value is not None
        and not any(item.verifiable for item in evidence)
    ):
        verifiable = False
    return CandidateFieldView(
        name=field.name,
        value=field.value,
        origin=field.origin,
        effective_value=effective.value,
        effective_origin=effective.origin,
        explanation=effective.explanation,
        missing_reason=effective.missing_reason,
        evidence=evidence,
        verifiable=verifiable,
    )


def review_detail(
    repos: Repositories, workspace_id: int, candidate_id: int
) -> CandidateReviewDetail:
    """R008：核对详情 —— 逐字段取值、出处、原文定位与可执行的核对转换。"""
    candidate = get_candidate(repos, workspace_id, candidate_id)
    version = repos.materials.get_version(candidate.source_version_id)
    material = repos.materials.get(version.material_id) if version is not None else None
    if version is None or material is None:
        raise NotFoundError("候选来源资料不存在")
    chunks = {chunk.id: chunk for chunk in repos.materials.list_chunks(version.id)}
    effective_fields = candidate.effective_draft().fields
    by_name = {field.name: field for field in effective_fields}
    scope = candidate.draft.scope
    course = repos.courses.get(scope.course_id) if scope.course_id is not None else None
    semester = repos.semesters.get(scope.semester_id) if scope.semester_id is not None else None
    # 适用范围只展示本人空间里的名称，查不到就留空而不是伪造归属
    course_name = (
        course.name if course is not None and course.workspace_id == workspace_id else None
    )
    semester_name = (
        semester.name if semester is not None and semester.workspace_id == workspace_id else None
    )
    return CandidateReviewDetail(
        candidate=candidate,
        material_id=material.id,
        material_title=material.title,
        version_id=version.id,
        version_no=version.version_no,
        course_name=course_name,
        semester_name=semester_name,
        fields=tuple(
            _field_view(field, by_name[field.name], chunks) for field in candidate.draft.fields
        ),
        missing_required_fields=missing_required_fields(candidate.draft.kind, effective_fields),
        allowed_transitions=tuple(
            status
            for status in _TRANSITION_ORDER
            if status in REVIEW_TRANSITIONS[candidate.review_status]
        ),
    )
