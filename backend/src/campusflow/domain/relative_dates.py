"""X006：有明确依据的有限日期语法；不读取当前时间，不猜年份或时刻。"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from campusflow.domain.errors import DomainError

_DATE = r"(?:[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{4}年[0-9]{1,2}月[0-9]{1,2}日)"
_CLOCK = r"[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?"
_WEEKDAY = "一二三四五六日天"
# 只接受完整的单一表达；多出修饰词时 fullmatch 失败，保留原文而不是截取一部分换算。
_EXPRESSION = re.compile(
    rf"(?:请|须|应)?(?:在|于)?\s*"
    rf"(?P<relative>今天|明天|后天|(?:本周|这周|下周|下下周)[{_WEEKDAY}]|"
    rf"第(?P<week>[0-9]{{1,2}}|[一二三四五六七八九十]{{1,3}})周"
    rf"(?:(?:周|星期)(?P<weekday>[{_WEEKDAY}]))?)"
    rf"\s*(?P<clock>{_CLOCK})?\s*(?P<before>前)?"
    r"(?:截止|提交|交|完成|提交作业|提交报告)?[。!]?"
)


@dataclass(frozen=True)
class DateResolution:
    """一次换算结果；day 与 range 二选一，缺少时刻或依据时由 reason 说明保留了什么。"""

    day: date | None = None
    clock: time | None = None
    range_start: date | None = None
    range_end: date | None = None
    anchor: date | None = None
    anchor_kind: str | None = None
    rule: str | None = None
    reason: str | None = None
    relation: str | None = None


def source_zone(name: str | None) -> ZoneInfo:
    """必须显式给出来源时区；不用本机时区或个人设置兜底，避免换机器后结果漂移。"""
    if not name:
        raise DomainError("缺少明确的来源时区，不能使用电脑或个人设置代替")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise DomainError("来源时区无效") from None


def parse_date_anchor(text: str, kind: str, timezone: str | None) -> date:
    """只接受完整的发布声明或教学周声明；调用方还须验证原文与适用范围。"""
    zone = source_zone(timezone)
    prefixes = {
        "publication": r"(?:发布时间|发布日期|发布于|published_at)",
        "teaching_week": r"(?:第一教学周周一|教学第一周周一|第1教学周周一)",
    }
    if kind not in prefixes:
        raise DomainError("未知日期依据类型")
    value = unicodedata.normalize("NFKC", text).strip()
    calendar_rule = r",\s*按自然周连续计数" if kind == "teaching_week" else ""
    match = re.fullmatch(
        rf"{prefixes[kind]}\s*:?\s*(?P<day>{_DATE})"
        rf"(?:[T ](?P<clock>{_CLOCK})(?P<offset>Z|[+-][0-9]{{2}}:[0-9]{{2}})?)?"
        rf"{calendar_rule}[。.]?",
        value,
    )
    if not match:
        raise DomainError("依据须有完整年份；校历还须明确第一教学周周一及按自然周连续计数")
    raw_day = re.sub(r"年|月", "-", match["day"]).removesuffix("日")
    try:
        year, month, day = map(int, raw_day.split("-"))
        result = date(year, month, day)
        if match["clock"]:
            if kind != "publication":
                raise ValueError
            clock = time(*map(int, match["clock"].split(":")))
            timestamp = datetime.combine(result, clock)
            if match["offset"]:
                offset = match["offset"]
                if offset != "Z" and (int(offset[1:3]) > 23 or int(offset[4:]) > 59):
                    raise ValueError
                timestamp = datetime.fromisoformat(timestamp.isoformat() + match["offset"])
                result = timestamp.astimezone(zone).date()
            else:
                _require_unique_local_time(timestamp, zone)
        if kind == "teaching_week" and result.weekday() != 0:
            raise ValueError
        return result
    except (ValueError, OverflowError):
        raise DomainError("日期依据无效，或教学第一周起点不是周一") from None


def _require_unique_local_time(value: datetime, zone: ZoneInfo) -> None:
    """拒绝夏令时跳过/重复的本地时刻，不能悄悄选一个偏移。"""
    offsets = set()
    for fold in (0, 1):
        aware = value.replace(tzinfo=zone, fold=fold)
        if aware.astimezone(ZoneInfo("UTC")).astimezone(zone).replace(tzinfo=None) == value:
            offsets.add(aware.utcoffset())
    if len(offsets) != 1:
        raise DomainError("该来源时区的时刻不存在或有两个对应时间，请核对")


def _week_number(text: str) -> int:
    """把阿拉伯数字或中文数字的周次转成整数；无法识别时返回 0，由调用方判为无效。"""
    if text.isascii() and text.isdigit():
        return int(text)
    digits = {c: i for i, c in enumerate("一二三四五六七八九", 1)}
    if text in digits:
        return digits[text]
    if re.fullmatch(r"[一二三四五]?十[一二三四五六七八九]?", text):
        left, right = text.split("十")
        return digits.get(left, 1) * 10 + digits.get(right, 0)
    return 0


def resolve_relative_date(
    text: str | None,
    *,
    timezone: str | None,
    publication: date | None = None,
    week_one: date | None = None,
    semester_start: date | None = None,
    semester_end: date | None = None,
) -> DateResolution:
    """只对单一明确表达计算；无星期的教学周返回范围，不返回截止日。"""
    match = _EXPRESSION.fullmatch(unicodedata.normalize("NFKC", text or "").strip())
    if not match:
        return DateResolution(reason="日期表达不在明确支持范围内，保留原文核对")
    try:
        zone = source_zone(timezone)
        if match["week"]:
            number = _week_number(match["week"])
            if not week_one or not semester_start or not semester_end:
                return DateResolution(reason="缺少适用学期及明确的第一教学周周一起点")
            if (
                type(week_one) is not date
                or week_one.weekday() != 0
                or not 1 <= number <= 53
                or not semester_start <= week_one <= semester_end
            ):
                return DateResolution(reason="教学周编号、起点或学期范围无效")
            start = week_one + timedelta(weeks=number - 1)
            end = start + timedelta(days=6)
            if start < semester_start or end > semester_end:
                return DateResolution(reason="教学周超出适用学期范围，需核对校历")
            anchor, kind = week_one, "teaching_week"
            rule = f"第一教学周从 {week_one.isoformat()} 周一开始，逐周连续计数至第{number}周"
            if not match["weekday"]:
                return DateResolution(
                    range_start=start,
                    range_end=end,
                    anchor=anchor,
                    anchor_kind=kind,
                    rule=rule,
                    reason="只明确教学周，缺少星期几，不能确定某一天或截止时刻",
                    relation="before" if match["before"] else "on",
                )
            # 周内序号以周一为 0；“周日/周天”映射为第 6 天，不做其他解释。
            day = start + timedelta(days=min(_WEEKDAY.index(match["weekday"]), 6))
        else:
            if type(publication) is not date:
                return DateResolution(reason="缺少带完整年份的明确发布日期")
            anchor, kind = publication, "publication"
            expression = match["relative"]
            if expression in ("今天", "明天", "后天"):
                offset = {"今天": 0, "明天": 1, "后天": 2}[expression]
                day = publication + timedelta(days=offset)
                rule = f"以来源时区的发布日期 {publication.isoformat()} 加 {offset} 天"
            else:
                # 以发布日期所在周为本周、周一为周首；跨月跨年交给 date 运算，不手工修正。
                weeks = {"本周": 0, "这周": 0, "下周": 1, "下下周": 2}[expression[:-1]]
                weekday = min(_WEEKDAY.index(expression[-1]), 6)
                day = publication + timedelta(days=7 * weeks + weekday - publication.weekday())
                rule = f"以 {publication.isoformat()} 所在周为本周，周一为周首，偏移 {weeks} 周"
        clock = None
        if match["clock"]:
            clock = time(*map(int, match["clock"].split(":")))
            _require_unique_local_time(datetime.combine(day, clock), zone)
        return DateResolution(
            day=day,
            clock=clock,
            anchor=anchor,
            anchor_kind=kind,
            rule=rule,
            reason=None if clock else "原文未写具体时刻，保留日期精度，不补午夜或23:59",
            relation="before" if match["before"] else "on",
        )
    except (DomainError, ValueError, OverflowError) as error:
        message = str(error) if isinstance(error, DomainError) else "日期或时刻无效，无法换算"
        return DateResolution(reason=message)
