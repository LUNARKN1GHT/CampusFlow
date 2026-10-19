"""N012：按发布信息、范围与替代声明判定生效候选；不写库、不自动接受。"""

from dataclasses import dataclass
from datetime import datetime

from campusflow.application.candidate_diff import CandidateDiff, compare_candidates
from campusflow.application.ports.repositories import Repositories
from campusflow.application.ports.version_relations import VersionRelations
from campusflow.domain.version_relations import VersionRelationKind, compare_scope


@dataclass(frozen=True)
class ApplicabilityReason:
    code: str
    message: str


@dataclass(frozen=True)
class VersionApplicability:
    diff: CandidateDiff
    status: str
    recommended_candidate_id: int | None
    review_required: bool
    applied: bool
    relation_id: int | None
    relation_kind: str | None
    scope_comparison: str
    publication: dict
    reasons: tuple[ApplicabilityReason, ...]


def _published_at(material, version) -> datetime | None:
    if material.published_at is not None:
        return material.published_at
    value = version.source_metadata.get("published_at")
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _publication(material, version) -> dict:
    return {
        "material_id": material.id,
        "version_id": version.id,
        "publisher": material.publisher,
        "published_at": _published_at(material, version).isoformat()
        if _published_at(material, version)
        else None,
        "published_text": version.source_metadata.get("published_text"),
    }


def assess_applicability(
    repos: Repositories,
    store: VersionRelations,
    workspace_id: int,
    baseline_candidate_id: int,
    comparison_candidate_id: int,
) -> VersionApplicability:
    diff = compare_candidates(repos, workspace_id, baseline_candidate_id, comparison_candidate_id)
    left, right = diff.left, diff.right
    left_material = repos.materials.get(left.material_id)
    right_material = repos.materials.get(right.material_id)
    left_version = repos.materials.get_version(left.source_version_id)
    right_version = repos.materials.get_version(right.source_version_id)
    if None in (left_material, right_material, left_version, right_version):
        from campusflow.domain.errors import NotFoundError

        raise NotFoundError("候选来源版本不存在")
    publication = {
        "baseline": _publication(left_material, left_version),
        "comparison": _publication(right_material, right_version),
    }
    scope_comparison = compare_scope(
        {
            "semester_id": left.scope.semester_id,
            "course_id": left.scope.course_id,
            "class_name": left.scope.class_name,
        },
        {
            "semester_id": right.scope.semester_id,
            "course_id": right.scope.course_id,
            "class_name": right.scope.class_name,
        },
    )
    forward = store.find(
        workspace_id,
        right.source_version_id,
        left.source_version_id,
        VersionRelationKind.REPLACEMENT_CLAIM,
    )
    backward = store.find(
        workspace_id,
        left.source_version_id,
        right.source_version_id,
        VersionRelationKind.REPLACEMENT_CLAIM,
    )
    reasons: list[ApplicabilityReason] = []
    status = "requires_review"
    recommended: int | None = None
    review_required = True
    relation = forward or backward
    if forward is not None:
        reasons.append(
            ApplicabilityReason("replacement_declared", "新版本声明替代旧版本，方向明确。")
        )
    elif backward is not None:
        reasons.append(
            ApplicabilityReason("baseline_claims_replacement", "旧版本声明替代新版本，方向相反。")
        )
        status = "conflict"
    else:
        reasons.append(
            ApplicabilityReason(
                "missing_replacement_declaration",
                "没有明确的替代声明；导入更晚不能自动覆盖旧通知。",
            )
        )

    if scope_comparison == "different":
        reasons.append(
            ApplicabilityReason("scope_different", "两个版本适用学期、课程或教学班不同。")
        )
        status = "conflict"
    elif scope_comparison == "unknown":
        reasons.append(ApplicabilityReason("scope_unknown", "适用范围信息不完整，需要用户核对。"))
        status = "requires_review"

    left_at = _published_at(left_material, left_version)
    right_at = _published_at(right_material, right_version)
    if left_at is not None and right_at is not None:
        if right_at < left_at:
            reasons.append(
                ApplicabilityReason(
                    "published_before_baseline", "新版本发布时间早于旧版本，不能据此覆盖。"
                )
            )
            status = "conflict"
        else:
            reasons.append(
                ApplicabilityReason("publication_not_older", "新版本发布时间不早于旧版本。")
            )
    else:
        reasons.append(
            ApplicabilityReason("publication_unknown", "缺少完整发布时间，不能独立判新生效。")
        )
        if status not in {"conflict"} and forward is not None:
            status = "requires_review"

    if left_material.archived or right_material.archived:
        reasons.append(
            ApplicabilityReason("source_archived", "来源资料已归档，需恢复后再作为当前依据。")
        )
        status = "requires_review"

    if (
        forward is not None
        and scope_comparison == "same"
        and left_at is not None
        and right_at is not None
        and right_at >= left_at
        and not left_material.archived
        and not right_material.archived
    ):
        status = "applicable"
        recommended = right.candidate_id
        review_required = False

    return VersionApplicability(
        diff=diff,
        status=status,
        recommended_candidate_id=recommended,
        review_required=review_required,
        applied=False,
        relation_id=relation.id if relation else None,
        relation_kind=relation.kind.value if relation else None,
        scope_comparison=scope_comparison,
        publication=publication,
        reasons=tuple(reasons),
    )
