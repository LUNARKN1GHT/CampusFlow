"""M5 排期纯领域规则测试（不连接数据库）。"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from campusflow.domain.planning import (
    AssumptionOptions,
    AvailabilityRule,
    PlanningInput,
    PlanningTask,
    input_fingerprint,
    input_snapshot,
    resolve_assumptions,
    two_week_range,
)


def _input(**overrides) -> PlanningInput:
    values = {
        "timezone": "Asia/Shanghai",
        "range_start": date(2026, 10, 18),
        "range_end": date(2026, 10, 31),
        "now": datetime(2026, 10, 18, 8, tzinfo=ZoneInfo("Asia/Shanghai")),
        "tasks": (),
        "excluded_tasks": (),
        "fixed_events": (),
        "availability": (AvailabilityRule(0, time(18), time(22)),),
        "daily_capacity_minutes": 240,
        "weekly_capacity_minutes": 1680,
        "break_minutes": 15,
        "buffer_minutes": 30,
        "segment_minutes": 90,
    }
    values.update(overrides)
    return PlanningInput(**values)


def test_two_week_range_includes_today_and_fourteenth_day() -> None:
    start, end = two_week_range(date(2026, 10, 18))
    assert (start, end) == (date(2026, 10, 18), date(2026, 10, 31))
    assert (end - start).days == 13


def test_input_fingerprint_is_stable_and_changes_with_inputs() -> None:
    base = _input()
    snapshot = input_snapshot(base)
    assert input_fingerprint(snapshot) == input_fingerprint(input_snapshot(base))
    changed = _input(
        tasks=(
            PlanningTask(
                task_id=1,
                title="论文",
                course_id=None,
                priority="high",
                minutes=120,
                due_at=datetime(2026, 10, 20, 12, tzinfo=UTC),
                due_date_only=False,
                target_date=None,
                estimated=False,
            ),
        )
    )
    assert input_fingerprint(snapshot) != input_fingerprint(input_snapshot(changed))


def test_snapshot_keeps_date_only_due_as_date() -> None:
    inputs = _input(
        tasks=(
            PlanningTask(
                task_id=1,
                title="作业",
                course_id=3,
                priority="medium",
                minutes=60,
                due_at=datetime(2026, 10, 20, 23, 59, tzinfo=ZoneInfo("Asia/Shanghai")),
                due_date_only=True,
                target_date=None,
                estimated=True,
            ),
        )
    )
    snapshot = input_snapshot(inputs)
    assert snapshot["tasks"][0]["due_date_only"] is True
    assert snapshot["tasks"][0]["minutes"] == 60
    assert snapshot["range_end"] == "2026-10-31"


def test_excluded_tasks_are_not_part_of_plannable_tasks() -> None:
    inputs = _input()
    snapshot = input_snapshot(inputs)
    assert snapshot["excluded_tasks"] == []
    assert snapshot["tasks"] == []
    assert timedelta(days=13) == inputs.range_end - inputs.range_start


def _task(**overrides) -> PlanningTask:
    values = {
        "task_id": 1,
        "title": "论文",
        "course_id": None,
        "priority": "high",
        "minutes": None,
        "due_at": None,
        "due_date_only": False,
        "target_date": None,
        "estimated": False,
    }
    values.update(overrides)
    return PlanningTask(**values)


def test_missing_duration_and_due_date_become_gaps_without_assumptions() -> None:
    resolved = resolve_assumptions(_input(tasks=(_task(),)), AssumptionOptions())
    assert resolved.tasks[0].minutes is None
    assert resolved.tasks[0].due_at is None
    codes = {gap["code"] for gap in resolved.gaps}
    assert codes == {"missing_duration", "missing_due_date"}
    assert resolved.assumptions == ()


def test_user_selected_assumptions_are_applied_and_echoed() -> None:
    resolved = resolve_assumptions(
        _input(tasks=(_task(),)),
        AssumptionOptions(
            default_duration_minutes=90,
            task_due_dates={1: date(2026, 10, 25)},
            segment_minutes=60,
            buffer_minutes=0,
        ),
    )
    task = resolved.tasks[0]
    assert task.minutes == 90
    assert task.duration_assumed is True
    assert task.due_at is not None and task.due_at.date() == date(2026, 10, 25)
    assert task.due_assumed is True
    assert task.due_date_only is True
    assert resolved.segment_minutes == 60
    assert resolved.buffer_minutes == 0
    kinds = {item["kind"] for item in resolved.assumptions}
    assert kinds == {"duration", "due_date"}
    assert all("假设" in item["text"] for item in resolved.assumptions)
    assert resolved.gaps == ()


def test_zero_duration_does_not_silently_enter_schedule() -> None:
    resolved = resolve_assumptions(_input(tasks=(_task(minutes=0),)), AssumptionOptions())
    assert resolved.tasks[0].minutes == 0
    assert {gap["code"] for gap in resolved.gaps} == {
        "missing_due_date",
        "no_remaining_work",
    }
