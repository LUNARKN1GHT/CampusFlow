"""X003：只识别原文是否完整，不根据发布时间或系统时间补全。"""

import pytest

from campusflow.domain.deadline_completeness import deadline_review_reasons


@pytest.mark.parametrize(
    "text",
    [
        "2026 年 10 月 8 日 18:00（北京时间）前提交",
        "2026-10-08T18:00:30+08:00",  # 时区偏移单独测试，不把它当第二个时刻
        "2026/10/8 18点30分",
        "2026.10.8 18时",
        "2026年10月8号18点半",
        "２０２６年１０月８日１８：００",
        "2024年2月29日00:00",
    ],
)
def test_complete_common_notation_has_no_missing_flags(text):
    codes = {r.code for r in deadline_review_reasons(text)}
    assert not codes & {"missing_year", "missing_time", "invalid_date", "invalid_time"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (None, {"missing_year", "missing_time"}),
        ("10 月 15 日提交", {"missing_year", "missing_time"}),
        ("10/15 18:00", {"missing_year"}),
        ("2026-10-15", {"missing_time"}),
        ("下周三 18:00", {"missing_year", "relative_date"}),
        ("第八周周五", {"missing_year", "missing_time", "relative_date"}),
        ("2026学年实验在10月15日提交", {"missing_year", "missing_time"}),
        ("二〇二六年十月十五日下午六点", {"unrecognized_date", "deadline_needs_review"}),
        ("另行通知", {"unrecognized_date", "deadline_needs_review"}),
        ("2026-02-29 18:00", {"invalid_date"}),
        ("2026年13月15日 18:00", {"invalid_date"}),
        ("2026-10-15 24:00", {"invalid_time", "missing_time"}),
        ("2026-10-15 18:99", {"invalid_time", "missing_time"}),
        ("2026-10-15 18点99分", {"invalid_time", "missing_time"}),
        ("2026-10-15 18:00:99", {"invalid_time", "missing_time"}),
        ("2026-10-15 18:00左右", {"deadline_needs_review"}),
        ("2026年10月15日至2026年10月16日18:00", {"ambiguous_date"}),
        ("2026-10-15 10:00或18:00", {"ambiguous_time"}),
    ],
)
def test_incomplete_invalid_and_complex_dates_stay_reviewable(text, expected):
    reasons = deadline_review_reasons(text)
    assert expected <= {r.code for r in reasons}
    assert all(r.field == "due_text" for r in reasons)


def test_partial_leap_day_does_not_invent_year_or_reject_valid_possibility():
    codes = {r.code for r in deadline_review_reasons("2月29日18:00")}
    assert "missing_year" in codes
    assert "invalid_date" not in codes


def test_month_day_in_full_numeric_date_does_not_count_twice():
    assert deadline_review_reasons("2026-10-15 18:00") == ()


def test_timezone_offset_is_not_counted_as_a_clock_or_missing_clock_replacement():
    assert deadline_review_reasons("2026-10-15T18:00:30+08:00") == ()
    codes = {r.code for r in deadline_review_reasons("2026-10-15 UTC+08:00")}
    assert "missing_time" in codes


def test_time_range_is_not_mistaken_for_timezone_offset():
    codes = {r.code for r in deadline_review_reasons("2026-10-15 18:00-20:00")}
    assert "ambiguous_time" in codes
