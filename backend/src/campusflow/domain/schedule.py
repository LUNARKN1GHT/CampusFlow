"""固定日程与可用时间的领域规则。

固定日程时间必须先按工作空间时区解释（带 tzinfo）；每周重复由应用层
按墙上时间展开，本模块只做静态校验，不生成具体实例。
时区设置变化不会改写已保存日程的时间点。
"""

from datetime import UTC, date, datetime, time

from campusflow.domain.states import EventRecurrence


def validate_fixed_event(
    starts_at: datetime,
    ends_at: datetime,
    recurrence: EventRecurrence,
    repeat_until: date | None,
) -> str | None:
    """校验固定日程，合法返回 None，否则返回中文错误信息。

    naive 时间拒绝而不是默认 UTC：不知道偏移就无法判断两个时刻的先后。
    WEEKLY 必须有重复截止日且不得早于首次开始日；repeat_until 含当天。
    """
    if starts_at.tzinfo is None or ends_at.tzinfo is None:
        return "日程时间必须先按工作空间时区转换"
    # 统一换算到 UTC 再比较，避免两端时区不同导致墙上时间比较出错。
    if ends_at.astimezone(UTC) <= starts_at.astimezone(UTC):
        return "结束时间必须晚于开始时间"
    if recurrence == EventRecurrence.WEEKLY and repeat_until is None:
        return "每周重复的日程必须提供重复截止日期"
    if recurrence == EventRecurrence.WEEKLY and repeat_until < starts_at.date():
        return "重复截止日期不得早于首次开始日期"
    return None


def validate_availability_slot(day_of_week: int, start_time: time, end_time: time) -> str | None:
    """校验可用时间段，合法返回 None，否则返回中文错误信息。

    可用时段按星期几重复，不跨越午夜；跨日时段由用户拆分为两天分别录入，
    避免一个时段同时属于两天而导致容量重复累加。
    """
    if not 0 <= day_of_week <= 6:
        return "星期取值必须在 0（周一）到 6（周日）之间"
    if end_time <= start_time:
        return "结束时间必须晚于开始时间；跨日时段请拆分为两天分别录入"
    return None
