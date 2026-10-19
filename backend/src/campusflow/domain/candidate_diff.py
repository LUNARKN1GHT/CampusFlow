"""N011：两个候选的字段差异规则。纯业务模型，不依赖数据库或 Web 框架。

只回答“新旧两侧的取值是否不同、差异属于哪一种”，不判断哪个版本生效。
比较基准是叠加用户修正后的生效值；原始提取值与原文证据用于展示和识别“明确删除”。
"""

from dataclasses import dataclass
from enum import StrEnum

from campusflow.domain.candidates import JsonValue
from campusflow.domain.values import canonical_json


class FieldCategory(StrEnum):
    """字段所属的业务类别，供对照界面分组；未识别的字段归入 OTHER，不丢弃。"""

    DATE = "date"
    LOCATION = "location"
    REQUIREMENT = "requirement"
    SCOPE = "scope"
    OTHER = "other"


class DiffStatus(StrEnum):
    UNCHANGED = "unchanged"
    CHANGED = "changed"
    ADDED = "added"
    MISSING = "missing"
    # 新侧原文明确声明取消/删除，并且仍有可验证证据；与“原文未提及”的 MISSING 分开。
    EXPLICITLY_REMOVED = "explicitly_removed"


class ScopeComponent(StrEnum):
    """结构化的适用范围项；与规则字段里的文字范围（scope_text）分开比较。"""

    SEMESTER = "semester"
    COURSE = "course"
    CLASS_NAME = "class_name"


FIELD_CATEGORIES: dict[str, FieldCategory] = {
    "due_text": FieldCategory.DATE,
    "due_date": FieldCategory.DATE,
    "due_time": FieldCategory.DATE,
    "start_text": FieldCategory.DATE,
    "end_text": FieldCategory.DATE,
    "effective_text": FieldCategory.DATE,
    "location": FieldCategory.LOCATION,
    "submission_method": FieldCategory.REQUIREMENT,
    "submission": FieldCategory.REQUIREMENT,
    "deliverable": FieldCategory.REQUIREMENT,
    "participation_conditions": FieldCategory.REQUIREMENT,
    "conditions_text": FieldCategory.REQUIREMENT,
    "rule_text": FieldCategory.REQUIREMENT,
    "scope_text": FieldCategory.SCOPE,
}

_CATEGORY_ORDER = {
    FieldCategory.DATE: 0,
    FieldCategory.LOCATION: 1,
    FieldCategory.REQUIREMENT: 2,
    FieldCategory.SCOPE: 3,
    FieldCategory.OTHER: 4,
}


@dataclass(frozen=True)
class FieldState:
    """一侧字段的取值状态。

    ``value`` 是最初提取值；``effective_value`` 是叠加 R002 修正后的生效值。
    ``verifiable_evidence_count`` 只数当前仍能在原文片段中逐字找到的摘录条数。
    """

    present: bool
    value: JsonValue
    effective_value: JsonValue
    verifiable_evidence_count: int = 0

    @property
    def explicitly_removed(self) -> bool:
        """只有“原文明确为空”且证据仍可验证才成立，用户修正清空不算。"""
        return (
            self.present
            and self.value is None
            and self.effective_value is None
            and self.verifiable_evidence_count > 0
        )


def field_category(name: str) -> FieldCategory:
    return FIELD_CATEGORIES.get(name, FieldCategory.OTHER)


def field_sort_key(name: str) -> tuple[int, str]:
    """按类别顺序再按字段名排序，保证同一对候选每次返回相同顺序。"""
    return (_CATEGORY_ORDER[field_category(name)], name)


def classify_field(left: FieldState, right: FieldState) -> DiffStatus:
    """比较左右两侧的生效值；只有右侧“原文明确为空 + 可验证证据”才算明确删除。"""
    if left.effective_value is None and right.effective_value is None:
        return DiffStatus.UNCHANGED
    if left.effective_value is None:
        return DiffStatus.ADDED
    if right.effective_value is None:
        if right.explicitly_removed:
            return DiffStatus.EXPLICITLY_REMOVED
        return DiffStatus.MISSING
    if canonical_json(left.effective_value) == canonical_json(right.effective_value):
        return DiffStatus.UNCHANGED
    return DiffStatus.CHANGED


def classify_scope(left: int | str | None, right: int | str | None) -> DiffStatus:
    """适用范围没有逐字段证据，因此只可能 unchanged/changed/added/missing。"""
    if left is None and right is None:
        return DiffStatus.UNCHANGED
    if left is None:
        return DiffStatus.ADDED
    if right is None:
        return DiffStatus.MISSING
    if canonical_json(left) == canonical_json(right):
        return DiffStatus.UNCHANGED
    return DiffStatus.CHANGED
