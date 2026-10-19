"""N015：按课程、范围、时间和内容生成重复建议；绝不自动删除或合并。

结果始终 auto_merge=false；跨学期、跨课程或跨教学班的候选只列入排除项，
范围信息缺失的候选标为需人工核对，不当作同一事项。
"""

from dataclasses import dataclass
from difflib import SequenceMatcher

from campusflow.application.candidates import get_candidate
from campusflow.application.ports.repositories import Repositories

# 参与比较的字段白名单：时间需完全相同，内容只看可表征事项的文本；
# 不比较原文摘录或描述全文，避免无关长文本拉高相似度。
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
    # 只取生效值中的非空文本；被用户清空的字段不参与重复判断。
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
    # 包含关系直接视为完全相同，避免长标题被序列相似度稀释成很低的分数。
    if left == right or left in right or right in left:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def _scope_state(target, other) -> tuple[str, str | None]:
    """学期、课程、教学班任一已知且不同即排除；范围缺失不算相同。"""
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
    """生成只读重复建议，按分数降序返回；从不删除、合并或改核对状态。

    标题或时间至少一项相同才进入候选，再加内容相似度；固定权重只用于排序，
    不是自动合并依据，最终是否合并仍需用户预览并确认。
    """
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
            # 标题与时间都不同则直接跳过，避免只靠内容相似产生大量误报。
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
