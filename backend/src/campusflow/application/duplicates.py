"""N015：按课程、范围、时间和内容生成重复建议；绝不自动删除或合并。"""

from dataclasses import dataclass
from difflib import SequenceMatcher

from campusflow.application.candidates import get_candidate
from campusflow.application.ports.repositories import Repositories

TIME_FIELDS = ("due_date", "due_time", "due_text", "start_text", "end_text", "effective_text")
CONTENT_FIELDS = (
    "submission_method",
    "submission",
    "deliverable",
    "location",
    "rule_text",
    "activity_text",
    "scope_text",
)


@dataclass(frozen=True)
class DuplicateSuggestion:
    candidate_id: int
    source_version_id: int
    title: str | None
    score: float
    reasons: tuple[str, ...]
    matched_fields: tuple[str, ...]
    review_status: str
    scope_same: bool


@dataclass(frozen=True)
class ExcludedCandidate:
    candidate_id: int
    code: str
    message: str


@dataclass(frozen=True)
class DuplicateReport:
    candidate_id: int
    kind: str
    suggestions: tuple[DuplicateSuggestion, ...]
    excluded: tuple[ExcludedCandidate, ...]
    auto_merge: bool = False


def _values(candidate) -> dict[str, str]:
    result = {}
    for field in candidate.effective_draft().fields:
        if field.value is None:
            continue
        text = str(field.value).strip()
        if text:
            result[field.name] = text
    return result


def _similar(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right or left in right or right in left:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def _scope_state(target, other) -> tuple[str, str | None]:
    target = target.draft.scope
    other = other.draft.scope
    if (
        target.semester_id is not None
        and other.semester_id is not None
        and target.semester_id != other.semester_id
    ):
        return "excluded", "different_semester"
    if (
        target.course_id is not None
        and other.course_id is not None
        and target.course_id != other.course_id
    ):
        return "excluded", "different_course"
    if (
        target.class_name
        and other.class_name
        and target.class_name.strip() != other.class_name.strip()
    ):
        return "excluded", "different_class"
    if target.semester_id is None or target.course_id is None:
        return "unknown", None
    return "same", None


def suggest_duplicates(
    repos: Repositories, workspace_id: int, candidate_id: int
) -> DuplicateReport:
    target = get_candidate(repos, workspace_id, candidate_id)
    target_values = _values(target)
    target_title = target_values.get("title")
    suggestions: list[DuplicateSuggestion] = []
    excluded: list[ExcludedCandidate] = []
    for other in repos.candidates.list_all(workspace_id, target.draft.kind):
        if other.id == candidate_id:
            continue
        scope_state, exclusion = _scope_state(target, other)
        if scope_state == "excluded":
            excluded.append(
                ExcludedCandidate(
                    candidate_id=other.id,
                    code=exclusion or "different_scope",
                    message="学期、课程或教学班不同，不能当作同一事项。",
                )
            )
            continue
        values = _values(other)
        title_same = bool(target_title) and target_title == values.get("title")
        matched_time = [
            name
            for name in TIME_FIELDS
            if name in target_values and name in values and target_values[name] == values[name]
        ]
        content_score = max(
            (
                _similar(target_values.get(name, ""), values.get(name, ""))
                for name in CONTENT_FIELDS
                if name in target_values and name in values
            ),
            default=0.0,
        )
        if not title_same and not matched_time:
            continue
        score = round(
            (0.45 if title_same else 0.0) + (0.40 if matched_time else 0.0) + 0.15 * content_score,
            3,
        )
        if score < 0.6:
            continue
        reasons = []
        if title_same:
            reasons.append("标题相同")
        if matched_time:
            reasons.append("时间字段相同")
        if content_score >= 0.6:
            reasons.append("内容高度相似")
        if scope_state == "unknown":
            reasons.append("范围信息不完整，需人工核对")
        suggestions.append(
            DuplicateSuggestion(
                candidate_id=other.id,
                source_version_id=other.source_version_id,
                title=values.get("title"),
                score=score,
                reasons=tuple(reasons),
                matched_fields=tuple((["title"] if title_same else []) + matched_time),
                review_status=other.review_status.value,
                scope_same=scope_state == "same",
            )
        )
    suggestions.sort(key=lambda item: (-item.score, item.candidate_id))
    return DuplicateReport(
        candidate_id=candidate_id,
        kind=target.draft.kind.value,
        suggestions=tuple(suggestions),
        excluded=tuple(excluded),
    )
