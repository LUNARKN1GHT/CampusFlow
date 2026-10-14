"""候选保存边界：核对空间、适用范围、真实片段与摘录后，原子保存待核对结果。"""

from campusflow.application.materials import _require_associations, get_material
from campusflow.application.ports.candidates import CandidateData
from campusflow.application.ports.repositories import Repositories
from campusflow.domain.candidates import (
    CandidateDraft,
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
        result = [repos.candidates.create(workspace_id, version.id, draft) for draft in drafts]
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
