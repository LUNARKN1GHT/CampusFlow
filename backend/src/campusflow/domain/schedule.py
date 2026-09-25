"""固定日程与可用时间的领域规则。"""

from datetime import date, datetime, time

from campusflow.domain.states import EventRecurrence


def validate_fixed_event(
    starts_at: datetime,
    ends_at: datetime,
    recurrence: EventRecurrence,
    repeat_until: date | None,
) -> str | None:
    """校验固定日程，合法返回 None，否则返回中文错误信息。"""
    if ends_at <= starts_at:
        return "结束时间必须晚于开始时间"
    if recurrence == EventRecurrence.WEEKLY and repeat_until is None:
        return "每周重复的日程必须提供重复截止日期"
    if recurrence == EventRecurrence.WEEKLY and repeat_until < starts_at.date():
        return "重复截止日期不得早于首次开始日期"
    return None


def validate_availability_slot(day_of_week: int, start_time: time, end_time: time) -> str | None:
    """校验可用时间段，合法返回 None，否则返回中文错误信息。"""
    if not 0 <= day_of_week <= 6:
        return "星期取值必须在 0（周一）到 6（周日）之间"
    if end_time <= start_time:
        return "结束时间必须晚于开始时间"
    return None
