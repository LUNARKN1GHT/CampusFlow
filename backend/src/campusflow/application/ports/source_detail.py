"""R007：任务与日程来源详情的读模型。"""

from dataclasses import dataclass
from datetime import datetime

from campusflow.domain.candidates import FieldOrigin, JsonValue


@dataclass(frozen=True)
class SourceCitation:
    """可验证的原文引用：片段编号、逐字摘录与定位方式。"""

    chunk_id: int
    quote: str
    locator_type: str
    locator_value: str


@dataclass(frozen=True)
class SourceField:
    """一条候选字段及其出处；verifiable 表示引用当前是否仍可验证。"""

    name: str
    value: JsonValue
    origin: FieldOrigin
    note: str | None
    verifiable: bool
    citations: tuple[SourceCitation, ...] = ()


@dataclass(frozen=True)
class SourceCorrection:
    """用户对字段的修正记录（R002），属于用户补充。"""

    field_name: str
    previous_value: JsonValue
    new_value: JsonValue
    new_origin: FieldOrigin
    operator: str
    note: str | None
    changed_at: datetime


@dataclass(frozen=True)
class SourceDetail:
    """任务或日程的来源详情；手动事项没有候选字段与引用。"""

    object_type: str
    object_id: int
    source_type: str
    source_state: str
    source_note: str | None
    material_id: int | None
    material_title: str | None
    version_no: int | None
    candidate_id: int | None
    due_source: str | None = None
    due_note: str | None = None
    fields: tuple[SourceField, ...] = ()
    corrections: tuple[SourceCorrection, ...] = ()
