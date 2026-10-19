"""X008：把解析未覆盖范围转成明确的候选核对原因，截断来源不视为已确认。

解析器报告"某页/某区域无法识别"时，该范围没有可用原文，也就无法为候选提供
证据。缺少这块范围不代表候选的要求是完整的——截图被切掉一半、扫描页空白都很
可能正好包含剩余要求，因此候选必须带上核对原因，而不是按"解析成功"处理。
"""

from collections.abc import Sequence
from dataclasses import dataclass

from campusflow.domain.candidates import ReviewReason

SOURCE_INCOMPLETE = "source_incomplete"

# 原因指向整条候选（field 为 None）：未覆盖范围没有可归属的字段证据。
# 展示用的定位文本上限，与处理作业的覆盖说明保持同一口径（J001 隐私规则：
# 只描述位置与原因，不含原文全文）。
MAX_LOCATOR_SUMMARY = 430
MAX_LISTED_RANGES = 20
MAX_REASON_LENGTH = 500

# 定位类型的展示模板；未收录的类型由 describe_range 退回“类型 值”，不丢范围。
_LOCATOR_TEMPLATES = {
    "page": "第 {} 页",
    "paragraph": "第 {} 段",
    "region": "区域 {}（归一化坐标）",
    "table_cell": "表格单元格 {}",
    "web": "网页元素 {}",
    "document": "整份文档",
}


@dataclass(frozen=True)
class UncoveredRange:
    """解析未覆盖的一段范围：定位与原因，不含原文。"""

    locator_type: str
    locator_value: str
    reason: str


def describe_range(item: UncoveredRange) -> str:
    """把定位转成可读文本；未知定位类型退回"类型 值"，不丢弃范围。"""
    template = _LOCATOR_TEMPLATES.get(item.locator_type)
    if template is None:
        location = f"{item.locator_type} {item.locator_value}".strip()
    elif "{}" in template:
        location = template.format(item.locator_value)
    else:
        location = template
    return f"{location}（{item.reason}）"


def coverage_review_reasons(ranges: Sequence[UncoveredRange]) -> tuple[ReviewReason, ...]:
    """未覆盖范围非空即产生一条候选级核对原因；空结果表示本次解析未报告未覆盖范围。

    空结果只表示没有已知的未覆盖范围，不代表候选已确认：核对状态仍由 X001 的
    pending 起算，只有用户能把它改成已确认。
    """
    unique = list(
        dict.fromkeys(
            (
                str(item.locator_type)[:60],
                str(item.locator_value)[:120],
                item.reason[:MAX_REASON_LENGTH],
            )
            for item in ranges
            if item.locator_type and item.reason.strip()
        )
    )
    if not unique:
        return ()
    labels = [describe_range(UncoveredRange(*key)) for key in unique[:MAX_LISTED_RANGES]]
    detail = "、".join(labels)
    if len(detail) > MAX_LOCATOR_SUMMARY:
        detail = detail[:MAX_LOCATOR_SUMMARY]
    message = (
        f"来源未完整解析，以下范围没有可用原文：{detail}"
        f"（共 {len(unique)} 处未覆盖范围）。"
        "未覆盖范围可能包含本候选的完整要求，因此不视为已确认，请核对原始资料。"
    )
    return (ReviewReason(SOURCE_INCOMPLETE, message[:1000]),)
