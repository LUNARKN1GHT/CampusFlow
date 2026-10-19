"""X001：候选事项、字段出处和证据。纯业务模型，不依赖数据库或模型 SDK。"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from campusflow.domain.errors import DomainError
from campusflow.domain.states import ReviewStatus

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]


class CandidateKind(StrEnum):
    TASK = "task"
    EVENT = "event"
    RULE = "rule"
    # C003：培养方案课程与三类课程关系的提取候选；确认后才转换为正式课程/关系。
    CURRICULUM_COURSE = "curriculum_course"
    COURSE_RELATION = "course_relation"


class FieldOrigin(StrEnum):
    DOCUMENT = "document"  # 原文规定
    USER = "user"  # 用户补充
    INFERRED = "inferred"  # 系统推断
    SUGGESTION = "suggestion"  # 规划建议，例如耗时估计


@dataclass(frozen=True)
class FieldEvidence:
    """一条原文证据：指向具体片段，并保存能逐字找回的摘录。"""

    chunk_id: int
    quote: str


@dataclass(frozen=True)
class CandidateField:
    """候选字段的原始提取值；用户修正另存为 FieldRevision，不回写这里。

    ``origin`` 决定证据要求：原文规定与系统推断必须带证据，推断与规划建议必须写说明；
    ``value`` 为空表示缺失，此时必须给出 ``missing_reason``。
    """

    name: str
    value: JsonValue
    origin: FieldOrigin
    evidence: tuple[FieldEvidence, ...] = ()
    explanation: str | None = None
    missing_reason: str | None = None


@dataclass(frozen=True)
class CandidateScope:
    """适用范围；未标清的维度保留 None，表示“尚不清楚”，不是通配所有对象。"""

    semester_id: int | None = None
    course_id: int | None = None
    class_name: str | None = None
    # C003：培养方案候选必须绑定唯一适用方案，避免不同版本规则混用。
    curriculum_plan_id: int | None = None


@dataclass(frozen=True)
class ReviewReason:
    code: str
    message: str
    field: str | None = None


@dataclass(frozen=True)
class CandidateDraft:
    """尚未落库的提取草稿；须通过 validate_candidate 才能保存，保存后固定为待核对。"""

    kind: CandidateKind
    fields: tuple[CandidateField, ...]
    scope: CandidateScope = CandidateScope()
    review_reasons: tuple[ReviewReason, ...] = ()


def _text(value: str | None, limit: int, label: str, *, required: bool = False) -> None:
    if value is None and not required:
        return
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise DomainError(f"{label}为空、过长或含无效字符")


def _json_value(value: JsonValue, depth: int = 0) -> None:
    """递归确认字段值只含可保存的 JSON 标量/容器，且嵌套不超过 8 层。"""
    if depth > 8:
        raise DomainError("字段值嵌套过深")
    if type(value) is dict:
        if any(type(key) is not str or "\x00" in key for key in value):
            raise DomainError("字段值的键必须为有效文本")
        for item in value.values():
            _json_value(item, depth + 1)
    elif type(value) is list:
        for item in value:
            _json_value(item, depth + 1)
    elif type(value) not in (str, int, float, bool, type(None)):
        raise DomainError("字段值必须是可保存的结构化数据")
    elif isinstance(value, str) and "\x00" in value:
        raise DomainError("字段值含无效字符")


def _validate_field(field: CandidateField, names: set[str]) -> None:
    """校验单个字段的出处与证据；同时把字段名登记到 ``names`` 以发现重名。"""
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", field.name) or field.name in names:
        raise DomainError("候选字段名无效或重复")
    names.add(field.name)
    if not isinstance(field.origin, FieldOrigin):
        raise DomainError("字段必须标明原文、用户补充、系统推断或规划建议")
    _json_value(field.value)
    try:
        encoded = json.dumps(field.value, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, OverflowError):
        raise DomainError("字段值不能包含无效数字或不可保存的数据") from None
    if len(encoded) > 32000:
        raise DomainError("字段值过长")
    _text(field.missing_reason, 500, "缺失原因", required=field.value is None)
    _text(field.explanation, 1000, "字段说明")
    if field.value is not None:
        if field.origin in (FieldOrigin.DOCUMENT, FieldOrigin.INFERRED) and not field.evidence:
            raise DomainError("原文规定或系统推断必须关联来源证据")
        if field.origin in (FieldOrigin.INFERRED, FieldOrigin.SUGGESTION):
            _text(field.explanation, 1000, "推断依据或建议说明", required=True)
    if len(field.evidence) > 32:
        raise DomainError("单个字段的证据数量过多")
    seen = set()
    for evidence in field.evidence:
        if type(evidence.chunk_id) is not int or evidence.chunk_id < 1:
            raise DomainError("证据片段编号无效")
        _text(evidence.quote, 6000, "原文摘录", required=True)
        key = (evidence.chunk_id, evidence.quote)
        if key in seen:
            raise DomainError("同一字段的证据重复")
        seen.add(key)


def validate_candidate(draft: CandidateDraft) -> None:
    """提取结果唯一的准入校验：字段出处、证据、适用范围与待核对原因必须自洽。

    这里只判断结构是否可信，不判断语义是否正确；不合规时直接拒绝，
    避免把没有依据的取值保存成“原文规定”。
    """
    if not isinstance(draft.kind, CandidateKind):
        raise DomainError("候选类型必须是任务、日程或规则")
    if not 1 <= len(draft.fields) <= 64:
        raise DomainError("候选事项必须包含 1 至 64 个字段")
    names: set[str] = set()
    for item in draft.fields:
        _validate_field(item, names)
    for identifier in (
        draft.scope.semester_id,
        draft.scope.course_id,
        draft.scope.curriculum_plan_id,
    ):
        if identifier is not None and (type(identifier) is not int or identifier < 1):
            raise DomainError("适用学期、课程或培养方案编号无效")
    plan_kinds = (CandidateKind.CURRICULUM_COURSE, CandidateKind.COURSE_RELATION)
    if draft.kind in plan_kinds and draft.scope.curriculum_plan_id is None:
        raise DomainError("培养方案候选必须绑定适用方案")
    if draft.kind not in plan_kinds and draft.scope.curriculum_plan_id is not None:
        raise DomainError("只有培养方案候选可以绑定适用方案")
    _text(draft.scope.class_name, 100, "教学班")
    if len(draft.review_reasons) > 64:
        raise DomainError("待核对原因数量过多")
    for reason in draft.review_reasons:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason.code):
            raise DomainError("核对原因编号无效")
        _text(reason.message, 1000, "核对原因", required=True)
        if reason.field is not None and reason.field not in names:
            raise DomainError("核对原因指向了不存在的字段")


@dataclass(frozen=True)
class FieldRevision:
    """R002：一次字段修正。保存修正前后值、操作者与来源类型。

    原始提取与证据不改动；修正值不再沿用原文证据，因此不能声明为原文规定。
    """

    field_name: str
    previous_value: JsonValue
    new_value: JsonValue
    previous_origin: FieldOrigin
    new_origin: FieldOrigin
    operator: str
    note: str | None = None


def _field_after_revision(field: CandidateField, revision: FieldRevision) -> CandidateField:
    # 修正值由用户提供，不继承原文证据；空值缺理由时按缺失字段处理。
    return CandidateField(
        name=field.name,
        value=revision.new_value,
        origin=revision.new_origin,
        evidence=(),
        explanation=revision.note,
        missing_reason=revision.note if revision.new_value is None else None,
    )


def validate_field_revision(field: CandidateField, revision: FieldRevision) -> None:
    """核对修正前后的取值一致，并确认修正结果符合字段出处规则。"""
    if revision.field_name != field.name:
        raise DomainError("修正记录的字段名与目标字段不一致")
    if not isinstance(revision.previous_origin, FieldOrigin):
        raise DomainError("修正前的来源类型无效")
    if revision.previous_origin is not field.origin or revision.previous_value != field.value:
        raise DomainError("修正前取值与字段当前状态不一致")
    _text(revision.operator, 100, "操作者", required=True)
    _text(revision.note, 1000, "修正说明")
    _validate_field(_field_after_revision(field, revision), set())


def apply_revisions(field: CandidateField, revisions: Sequence[FieldRevision]) -> CandidateField:
    """按先后顺序叠加同一字段的修正，得到当前生效字段。

    修正值由用户给出，不继承原文证据；每叠加一次都重新走字段出处校验。
    """
    current = field
    for revision in revisions:
        current = _field_after_revision(current, revision)
    return current


def effective_fields(
    fields: Sequence[CandidateField], revisions: Sequence[FieldRevision]
) -> tuple[CandidateField, ...]:
    """原始字段叠加修正后的生效取值，供核对界面展示。

    只读视图：不改写原始字段或证据，返回顺序与 ``fields`` 一致。
    """
    by_name: dict[str, list[FieldRevision]] = {}
    for revision in revisions:
        by_name.setdefault(revision.field_name, []).append(revision)
    return tuple(apply_revisions(field, by_name.get(field.name, ())) for field in fields)


# R003：按类型定义的必需字段，与提取 spec 的 allow_missing=False 保持一致。
REQUIRED_FIELDS: dict[CandidateKind, tuple[str, ...]] = {
    CandidateKind.TASK: ("title",),
    CandidateKind.EVENT: ("title", "activity_text"),
    CandidateKind.RULE: (),
    CandidateKind.CURRICULUM_COURSE: ("course_name",),
    CandidateKind.COURSE_RELATION: ("from_course_name", "to_course_name", "relation_type"),
}

_REVIEW_LABELS = {
    ReviewStatus.PENDING: "待核对",
    ReviewStatus.CONFIRMED: "已确认",
    ReviewStatus.IGNORED: "已忽略",
    ReviewStatus.SUPERSEDED: "已失效",
}

# 用户可以撤销核对决定退回待核对；已失效只允许重新打开，不再直接跳转。
REVIEW_TRANSITIONS: dict[ReviewStatus, frozenset[ReviewStatus]] = {
    ReviewStatus.PENDING: frozenset(
        {ReviewStatus.CONFIRMED, ReviewStatus.IGNORED, ReviewStatus.SUPERSEDED}
    ),
    ReviewStatus.CONFIRMED: frozenset({ReviewStatus.PENDING, ReviewStatus.SUPERSEDED}),
    ReviewStatus.IGNORED: frozenset({ReviewStatus.PENDING}),
    ReviewStatus.SUPERSEDED: frozenset({ReviewStatus.PENDING}),
}


@dataclass(frozen=True)
class ReviewChange:
    """R003：一次候选核对状态变更，保存转换方向、操作者与原因。"""

    from_status: ReviewStatus
    to_status: ReviewStatus
    operator: str
    reason: str | None = None


def validate_review_change(change: ReviewChange) -> None:
    """只校验核对状态轴上的转换；任务执行进度独立维护，两种状态不能互相套用。"""
    if not isinstance(change.from_status, ReviewStatus) or not isinstance(
        change.to_status, ReviewStatus
    ):
        raise DomainError("核对状态无效")
    if change.to_status is change.from_status:
        raise DomainError(f"候选已经处于{_REVIEW_LABELS[change.from_status]}状态")
    if change.to_status not in REVIEW_TRANSITIONS[change.from_status]:
        raise DomainError(
            f"不允许从{_REVIEW_LABELS[change.from_status]}转换为{_REVIEW_LABELS[change.to_status]}"
        )
    _text(change.operator, 100, "操作者", required=True)
    _text(change.reason, 500, "变更原因")


def missing_required_fields(
    kind: CandidateKind, fields: Sequence[CandidateField]
) -> tuple[str, ...]:
    """按候选类型列出仍然缺失的必需字段名，供确认前提示。"""
    present = {field.name: field for field in fields}
    return tuple(
        name
        for name in REQUIRED_FIELDS.get(kind, ())
        if (field := present.get(name)) is None or field.value is None
    )


def require_confirmed_for_conversion(status: ReviewStatus) -> None:
    """被忽略或已失效的候选不参与正式约束，只有已确认项可以转换。"""
    if status is not ReviewStatus.CONFIRMED:
        raise DomainError("只有已确认的候选事项可以转换为正式事项")
