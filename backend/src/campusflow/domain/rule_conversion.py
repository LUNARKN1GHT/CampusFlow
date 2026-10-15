"""R006：已确认规则候选转换为正式课程规则的字段规则。纯业务模型，不依赖数据库。"""

from dataclasses import dataclass

from campusflow.domain.candidates import CandidateField, CandidateKind, JsonValue
from campusflow.domain.errors import DomainError

TITLE_FIELD = "title"
RULE_TEXT_FIELD = "rule_text"
CONDITIONS_FIELD = "conditions_text"
SCOPE_FIELD = "scope_text"
EFFECTIVE_FIELD = "effective_text"
TITLE_LIMIT = 200


@dataclass(frozen=True)
class RuleConversion:
    """候选转为课程规则时的字段取值；生效声明保留原文，不做任何换算。"""

    title: str
    rule_text: str
    conditions_text: str | None
    scope_text: str | None
    effective_text: str | None


def _value_of(field: CandidateField | None) -> JsonValue:
    return field.value if field is not None else None


def _optional_text(by_name: dict[str, CandidateField], name: str) -> str | None:
    """可选文本字段：取字符串原值，空串或全空白归一为 None，不补造内容。"""
    value = _value_of(by_name.get(name))
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def build_rule_conversion(
    kind: CandidateKind, fields: tuple[CandidateField, ...]
) -> RuleConversion:
    """按规则候选的生效字段生成课程规则取值；缺标题或正文时拒绝转换。

    生效声明（effective_text）只保留原文，不解析生效日期、不换算相对写法：
    缺少年份或依据不足时留待核对，而不是把猜测当成已确认约束。
    """
    if kind is not CandidateKind.RULE:
        raise DomainError("只有规则候选可以转换为课程规则")
    by_name = {field.name: field for field in fields}
    title = _optional_text(by_name, TITLE_FIELD)
    if title is None:
        raise DomainError("规则候选缺少标题，无法转换为课程规则")
    if len(title) > TITLE_LIMIT:
        raise DomainError(f"规则标题超过 {TITLE_LIMIT} 个字符，请在核对后缩短再转换")
    rule_text = _optional_text(by_name, RULE_TEXT_FIELD)
    if rule_text is None:
        raise DomainError("规则候选缺少规则正文，无法转换为课程规则")
    return RuleConversion(
        title=title,
        rule_text=rule_text,
        conditions_text=_optional_text(by_name, CONDITIONS_FIELD),
        scope_text=_optional_text(by_name, SCOPE_FIELD),
        effective_text=_optional_text(by_name, EFFECTIVE_FIELD),
    )
