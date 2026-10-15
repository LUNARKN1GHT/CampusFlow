"""R004：已确认任务候选转换为正式任务的字段规则。纯业务模型，不依赖数据库。"""

from dataclasses import dataclass
from datetime import date, datetime, time

from campusflow.domain.candidates import (
    CandidateField,
    CandidateKind,
    FieldOrigin,
    JsonValue,
)
from campusflow.domain.errors import DomainError
from campusflow.domain.states import DueSourceType

TITLE_FIELD = "title"
DUE_FIELD = "due_text"
ESTIMATED_FIELD = "estimated_minutes"
DESCRIPTION_PARTS = (("submission_method", "提交方式"), ("deliverable", "产出物"))


@dataclass(frozen=True)
class TaskConversion:
    """候选转为正式任务时的字段取值；日期保持原精度，不补造时刻。"""

    title: str
    description: str | None
    due_date: date | None
    due_time: time | None
    due_source: DueSourceType
    due_note: str | None
    estimated_minutes: int | None


def parse_due_text(value: JsonValue) -> tuple[date | None, time | None]:
    """把候选截止原文解析为具体日期与可选时刻；解析不出时返回空，不猜日期。

    只有日期的写法不会补出 00:00 或 23:59；带时区偏移的时刻不在此处换算，
    避免把不确定的时点当成确定截止。
    """
    if not isinstance(value, str):
        return None, None
    text = value.strip()
    if not text:
        return None, None
    if "T" in text or " " in text:
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None, None
        if parsed.tzinfo is not None:
            return None, None
        return parsed.date(), parsed.time()
    try:
        return date.fromisoformat(text), None
    except ValueError:
        return None, None


def due_source_for(origin: FieldOrigin) -> DueSourceType:
    """截止字段的出处映射为正式任务的来源类型；规划建议按用户可改的来源处理。"""
    if origin is FieldOrigin.DOCUMENT:
        return DueSourceType.DOCUMENT
    if origin is FieldOrigin.INFERRED:
        return DueSourceType.INFERRED
    return DueSourceType.USER


def _estimated_minutes(field: CandidateField | None) -> int | None:
    if field is None:
        return None
    value = field.value
    if type(value) is int and value > 0:
        return value
    return None


def _description(by_name: dict[str, CandidateField]) -> str | None:
    parts = []
    for name, label in DESCRIPTION_PARTS:
        field = by_name.get(name)
        value = field.value if field is not None else None
        if isinstance(value, str) and value.strip():
            parts.append(f"{label}：{value}")
    return "\n".join(parts) if parts else None


def build_task_conversion(
    kind: CandidateKind, fields: tuple[CandidateField, ...]
) -> TaskConversion:
    """按任务候选的生效字段生成正式任务取值；缺标题或类型不符时拒绝转换。"""
    if kind is not CandidateKind.TASK:
        raise DomainError("只有任务候选可以转换为正式任务")
    by_name = {field.name: field for field in fields}
    title_field = by_name.get(TITLE_FIELD)
    title = title_field.value if title_field is not None else None
    if not isinstance(title, str) or not title.strip():
        raise DomainError("任务候选缺少标题，无法转换为正式任务")
    due_date: date | None = None
    due_time: time | None = None
    due_source = DueSourceType.USER
    due_note: str | None = None
    due_field = by_name.get(DUE_FIELD)
    if due_field is not None and due_field.value is not None:
        due_date, due_time = parse_due_text(due_field.value)
        due_source = due_source_for(due_field.origin)
        if due_date is None and isinstance(due_field.value, str):
            due_note = due_field.value.strip() or None
    return TaskConversion(
        title=title,
        description=_description(by_name),
        due_date=due_date,
        due_time=due_time,
        due_source=due_source,
        due_note=due_note,
        estimated_minutes=_estimated_minutes(by_name.get(ESTIMATED_FIELD)),
    )
