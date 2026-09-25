"""领域规则单元测试（不依赖数据库）。"""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from campusflow.domain.dates import validate_due
from campusflow.domain.schedule import validate_availability_slot, validate_fixed_event
from campusflow.domain.semesters import validate_semester_range
from campusflow.domain.states import EventRecurrence

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
