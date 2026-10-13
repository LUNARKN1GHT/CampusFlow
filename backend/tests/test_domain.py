"""领域规则单元测试（不依赖数据库）。"""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from campusflow.domain.dates import is_overdue, validate_due
from campusflow.domain.schedule import validate_availability_slot, validate_fixed_event
from campusflow.domain.semesters import validate_semester_range
from campusflow.domain.states import EventRecurrence, TaskProgress

TZ = ZoneInfo("Asia/Shanghai")


def test_semester_range_requires_end_after_start() -> None:
    assert validate_semester_range(date(2026, 9, 1), date(2027, 1, 31)) is None
    error = validate_semester_range(date(2026, 9, 1), date(2026, 8, 31))
    assert error is not None and "结束" in error


def test_due_time_requires_due_date() -> None:
    assert validate_due(date(2026, 10, 15), None) is None
    assert validate_due(date(2026, 10, 15), time(23, 59)) is None
    error = validate_due(None, time(23, 59))
    assert error is not None and "日期" in error


def test_fixed_event_rules() -> None:
    start = datetime(2026, 9, 28, 14, 0, tzinfo=TZ)
    end = datetime(2026, 9, 28, 16, 0, tzinfo=TZ)
    assert validate_fixed_event(start, end, EventRecurrence.NONE, None) is None
    assert validate_fixed_event(end, start, EventRecurrence.NONE, None) is not None
    assert validate_fixed_event(start, end, EventRecurrence.WEEKLY, None) is not None
    assert validate_fixed_event(start, end, EventRecurrence.WEEKLY, date(2026, 9, 27)) is not None
    assert validate_fixed_event(start, end, EventRecurrence.WEEKLY, date(2026, 12, 28)) is None


def test_availability_slot_rules() -> None:
    assert validate_availability_slot(3, time(19, 0), time(21, 0)) is None
    assert validate_availability_slot(7, time(19, 0), time(21, 0)) is not None
    assert validate_availability_slot(-1, time(19, 0), time(21, 0)) is not None
    assert validate_availability_slot(3, time(21, 0), time(19, 0)) is not None


def test_overdue_rules() -> None:
    due = date(2026, 10, 15)
    same_day_evening = datetime(2026, 10, 15, 23, 0, tzinfo=TZ)
    next_day = datetime(2026, 10, 16, 0, 1, tzinfo=TZ)

    # 只有日期的 DDL：当天任何时刻都不算逾期，不伪造 00:00/23:59
    assert not is_overdue(due, None, TaskProgress.IN_PROGRESS, same_day_evening)
    assert is_overdue(due, None, TaskProgress.IN_PROGRESS, next_day)
    # 带时刻的 DDL 按具体时刻判定
    assert not is_overdue(due, time(23, 30), TaskProgress.IN_PROGRESS, same_day_evening)
    assert is_overdue(due, time(22, 30), TaskProgress.IN_PROGRESS, same_day_evening)
    # 逾期不覆盖执行进度：进行中与受阻只是被标记，完成与取消不再逾期
    assert is_overdue(due, None, TaskProgress.BLOCKED, next_day)
    assert not is_overdue(due, None, TaskProgress.DONE, next_day)
    assert not is_overdue(due, None, TaskProgress.CANCELLED, next_day)
    # 没有截止日期的任务不存在逾期
    assert not is_overdue(None, None, TaskProgress.IN_PROGRESS, next_day)
