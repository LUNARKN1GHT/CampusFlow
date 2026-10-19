"""日期与截止时间规则。

只有日期、没有时刻的 DDL 保持日期精度：展示和排期都不得把
`2026-10-15` 自动补成 `00:00` 或 `23:59` 的确定时刻，只能按"当天内完成"处理。
时区设置变化也不会改写日期精度或补出时刻。
"""

from datetime import date, datetime, time

from campusflow.domain.states import TaskProgress


def validate_due(due_date: date | None, due_time: time | None) -> str | None:
    """校验截止时间组合，合法返回 None，否则返回中文错误信息。

    时刻必须依附于日期；只有日期的 DDL 合法，不得为了比较而补午夜或 23:59。
    """
    if due_time is not None and due_date is None:
        return "设定截止时刻时必须同时提供截止日期"
    return None


def is_overdue(
    due_date: date | None,
    due_time: time | None,
    progress: TaskProgress,
    now: datetime,
) -> bool:
    """按截止精度与执行进度派生逾期标记；now 为工作空间时区的当前时刻。

    逾期是独立于进度的派生标记，不改写进行中或受阻状态；
    已完成或已取消的任务不再逾期。只有日期的 DDL 在当天内不算逾期，
    不为它伪造午夜或 23:59 的确定时刻。
    """
    if progress in (TaskProgress.DONE, TaskProgress.CANCELLED):
        return False
    if due_date is None:
        return False
    if due_time is None:
        # 仅日期时按日历日比较：当天任何时刻都仍未逾期。
        return now.date() > due_date
    return now > datetime.combine(due_date, due_time, tzinfo=now.tzinfo)
