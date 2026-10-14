"""X003：保守检查截止原文的完整性，不换算日期、不构造截止时间。"""

import re
import unicodedata
from datetime import date

from campusflow.domain.candidates import ReviewReason

# 只认与月日相连的年份；发布年、学年和正文其他年份不能补成截止年。
_CHINESE_DATE = re.compile(
    r"(?<![0-9])(?:(?P<year>[0-9]{4})\s*年\s*)?"
    r"(?P<month>[0-9]{1,2})\s*月\s*(?P<day>[0-9]{1,2})\s*[日号]"
)
_NUMERIC_DATE = re.compile(
    r"(?<![0-9./-])(?P<year>[0-9]{4})\s*(?P<sep>[-/.])\s*"
    r"(?P<month>[0-9]{1,2})\s*(?P=sep)\s*(?P<day>[0-9]{1,2})(?![0-9])"
)
_PARTIAL_DATE = re.compile(
    r"(?<![0-9./-])(?P<month>[0-9]{1,2})\s*[-/]\s*"
    r"(?P<day>[0-9]{1,2})(?![0-9./-])"
)
_CLOCK = re.compile(
    r"(?<![0-9:])(?P<hour>[0-9]{1,2})\s*:\s*(?P<minute>[0-9]{2})"
    r"(?:\s*:\s*(?P<second>[0-9]{2}))?(?![0-9:])"
)
_OFFSET = re.compile(
    r"(?:T[0-9]{2}:[0-9]{2}(?::[0-9]{2})?|UTC|GMT)"
    r"(?P<offset>[+-][0-9]{2}:[0-9]{2})"
)
_CHINESE_CLOCK = re.compile(
    r"(?<![0-9])(?P<hour>[0-9]{1,2})\s*[点时]"
    r"(?:(?P<half>半)|\s*(?P<minute>[0-9]{1,2})\s*分)?"
    r"(?:\s*(?P<second>[0-9]{1,2})\s*秒)?(?!\s*[0-9])"
)
_RELATIVE = re.compile(
    r"今天|明天|后天|本周|这周|下周|下下周|周[一二三四五六日天]|"
    r"星期[一二三四五六日天]|第\s*[0-9一二三四五六七八九十]+\s*周|"
    r"[0-9一二三四五六七八九十]+\s*[天日]后|月底|月末|年末"
)
_UNCERTAIN = re.compile(
    r"左右|大约|暂定|另行|取消|不再|待定|届时|课前|课后|下课|上课|"
    r"上午|下午|中午|晚上|凌晨|[aApP]\.?[mM]\.?"
)


def deadline_review_reasons(text: str | None) -> tuple[ReviewReason, ...]:
    """只检查所提取的截止原文，不读取发布时间、当前时间或其他字段。

    空结果只代表识别到常见写法，并不表示候选已确认或已生成可排期日期。
    不支持或歧义写法保留原文并提示复核，X006 再处理有依据的日期换算。
    """
    value = unicodedata.normalize("NFKC", text or "")
    reasons: list[ReviewReason] = []

    def add(code: str, message: str) -> None:
        reasons.append(ReviewReason(code, message, "due_text"))

    matches = list(_CHINESE_DATE.finditer(value)) + list(_NUMERIC_DATE.finditer(value))
    spans = [m.span() for m in matches]
    matches += [
        m
        for m in _PARTIAL_DATE.finditer(value)
        if not any(start <= m.start() and m.end() <= end for start, end in spans)
    ]
    if not matches or any(not m.groupdict().get("year") for m in matches):
        add("missing_year", "截止原文未包含可识别的明确年份，不根据发布时间或当前年份补写")
    invalid_date = False
    for match in matches:
        # 2000 仅用于校验无年份的月日范围（允许 2 月 29 日），不返回或保存该年份。
        year = int(match.groupdict().get("year") or 2000)
        try:
            date(year, int(match["month"]), int(match["day"]))
        except ValueError:
            invalid_date = True
    if invalid_date:
        add("invalid_date", "截止原文含无效日期，请核对原始通知")
    if len(matches) > 1:
        add("ambiguous_date", "截止原文出现多个日期或日期区间，需核对适用条件和最终截止点")
    if _RELATIVE.search(value):
        add("relative_date", "截止原文含相对日期或周次，需另行核对发布日及校历依据后换算")
    if value.strip() and not matches and not _RELATIVE.search(value):
        add("unrecognized_date", "暂不能识别截止日期写法，请根据原文核对，不自动转换")

    offsets = [m.span("offset") for m in _OFFSET.finditer(value)]
    clocks = [
        m
        for m in _CLOCK.finditer(value)
        if not any(start <= m.start() and m.end() <= end for start, end in offsets)
    ] + list(_CHINESE_CLOCK.finditer(value))
    valid_clocks = []
    for match in clocks:
        parts = match.groupdict()
        hour = int(parts["hour"])
        minute = int(parts.get("minute") or (30 if parts.get("half") else 0))
        second = int(parts.get("second") or 0)
        if hour <= 23 and minute <= 59 and second <= 59:
            valid_clocks.append(match)
    if not valid_clocks:
        add("missing_time", "截止原文未包含可识别的有效具体时刻，不补成午夜或 23:59")
    if len(valid_clocks) != len(clocks):
        add("invalid_time", "截止原文含无效时刻，请核对小时、分钟或秒")
    if len(clocks) > 1:
        add("ambiguous_time", "截止原文出现多个时刻，需核对适用条件和最终截止点")
    if _UNCERTAIN.search(value):
        add("deadline_needs_review", "截止描述含时间限定、近似或变更措辞，保留原文并人工核对")
    return tuple(reasons)
