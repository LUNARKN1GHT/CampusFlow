"""候选保存边界：核对空间、适用范围、真实片段与摘录后，原子保存待核对结果。"""

from campusflow.application.materials import _require_associations, get_material
from campusflow.application.ports.candidates import CandidateData
from campusflow.application.ports.repositories import Repositories
from campusflow.domain.candidates import CandidateDraft, validate_candidate
from campusflow.domain.errors import DomainError, NotFoundError


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
