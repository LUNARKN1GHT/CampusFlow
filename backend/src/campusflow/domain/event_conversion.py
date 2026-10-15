"""R005：已确认日程候选转换为固定日程的时间规则。纯业务模型，不依赖数据库。"""

from dataclasses import dataclass
from datetime import datetime

from campusflow.domain.candidates import CandidateField, CandidateKind, JsonValue
from campusflow.domain.errors import DomainError

TITLE_FIELD = "title"
START_FIELD = "start_text"
END_FIELD = "end_text"
LOCATION_FIELD = "location"
TITLE_LIMIT = 300
LOCATION_LIMIT = 200


@dataclass(frozen=True)
class EventConversion:
    """候选转为固定日程时的取值；起止必须是可确定的日期与时刻。"""

    title: str
    starts_at: datetime
    ends_at: datetime
    location: str | None


def parse_event_time(value: JsonValue) -> datetime | None:
    """解析日程起止原文；缺日期或时刻时返回 None，不按全天或常见时长补造。"""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or ("T" not in text and " " not in text):
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _value_of(field: CandidateField | None) -> JsonValue:
    return field.value if field is not None else None


def _text_field(
    by_name: dict[str, CandidateField], name: str, limit: int, label: str
) -> str | None:
    value = _value_of(by_name.get(name))
    if not isinstance(value, str) or not value.strip():
        return None
    if len(value) > limit:
        raise DomainError(f"{label}超过 {limit} 个字符，请在核对后缩短再转换")
    return value


def build_event_conversion(
    kind: CandidateKind, fields: tuple[CandidateField, ...]
) -> EventConversion:
    """按日程候选的生效字段生成固定日程取值；起止不完整时拒绝转换。"""
    if kind is not CandidateKind.EVENT:
        raise DomainError("只有日程候选可以转换为固定日程")
    by_name = {field.name: field for field in fields}
    title = _text_field(by_name, TITLE_FIELD, TITLE_LIMIT, "日程标题")
    if title is None:
        raise DomainError("日程候选缺少标题，无法转换为固定日程")
    starts_at = parse_event_time(_value_of(by_name.get(START_FIELD)))
    ends_at = parse_event_time(_value_of(by_name.get(END_FIELD)))
    if starts_at is None or ends_at is None:
        raise DomainError("日程候选缺少可确定的开始或结束时间，无法创建固定日程")
    return EventConversion(
        title=title,
        starts_at=starts_at,
        ends_at=ends_at,
        location=_text_field(by_name, LOCATION_FIELD, LOCATION_LIMIT, "地点原文"),
    )
