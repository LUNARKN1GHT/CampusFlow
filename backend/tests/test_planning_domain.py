"""M5 排期纯领域规则测试（不连接数据库）。"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from campusflow.domain.planning import (
    AssumptionOptions,
    AvailabilityRule,
    FixedEventOccurrence,
    PlanningInput,
    PlanningTask,
    allocate_tasks,
    build_free_windows,
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


def _window_minutes(windows) -> int:
    return sum(window.minutes for window in windows)


def test_adjacent_availability_boundaries_do_not_overlap() -> None:
    inputs = _input(
        range_start=date(2026, 10, 19),
        range_end=date(2026, 10, 19),
        now=datetime(2026, 10, 19, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        availability=(
            AvailabilityRule(0, time(18), time(20)),
            AvailabilityRule(0, time(20), time(22)),
        ),
    )
    windows = build_free_windows(inputs)
    assert len(windows) == 1
    assert _window_minutes(windows) == 240


def test_overlapping_availability_is_counted_once() -> None:
    inputs = _input(
        range_start=date(2026, 10, 19),
        range_end=date(2026, 10, 19),
        now=datetime(2026, 10, 19, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        availability=(
            AvailabilityRule(0, time(18), time(21)),
            AvailabilityRule(0, time(20), time(22)),
        ),
    )
    windows = build_free_windows(inputs)
    assert _window_minutes(windows) == 240


def test_fixed_event_is_subtracted_and_duplicate_events_not_double_counted() -> None:
    tz = ZoneInfo("Asia/Shanghai")
    event = FixedEventOccurrence(
        event_id=1,
        title="晚课",
        start=datetime(2026, 10, 19, 19, tzinfo=tz),
        end=datetime(2026, 10, 19, 20, tzinfo=tz),
    )
    inputs = _input(
        range_start=date(2026, 10, 19),
        range_end=date(2026, 10, 19),
        now=datetime(2026, 10, 19, 0, tzinfo=tz),
        availability=(AvailabilityRule(0, time(18), time(22)),),
        fixed_events=(event, event),
    )
    windows = build_free_windows(inputs)
    assert [(w.start.hour, w.end.hour) for w in windows] == [(18, 19), (20, 22)]
    assert _window_minutes(windows) == 180


def test_past_part_of_today_is_not_available() -> None:
    tz = ZoneInfo("Asia/Shanghai")
    inputs = _input(
        range_start=date(2026, 10, 19),
        range_end=date(2026, 10, 19),
        now=datetime(2026, 10, 19, 19, 30, tzinfo=tz),
        availability=(AvailabilityRule(0, time(18), time(22)),),
    )
    windows = build_free_windows(inputs)
    assert len(windows) == 1
    assert windows[0].start == datetime(2026, 10, 19, 19, 30, tzinfo=tz)
    assert _window_minutes(windows) == 150


def _resolved(inputs: PlanningInput, options: AssumptionOptions | None = None):
    return resolve_assumptions(inputs, options or AssumptionOptions())


def test_allocation_respects_earlier_deadline_first() -> None:
    tz = ZoneInfo("Asia/Shanghai")
    inputs = _input(
        range_start=date(2026, 10, 19),
        range_end=date(2026, 10, 20),
        now=datetime(2026, 10, 19, 0, tzinfo=tz),
        availability=(AvailabilityRule(0, time(18), time(22)),),
        tasks=(
            PlanningTask(
                task_id=1,
                title="先交",
                course_id=None,
                priority="low",
                minutes=90,
                due_at=datetime(2026, 10, 19, 19, 30, tzinfo=tz),
                due_date_only=False,
                target_date=None,
                estimated=False,
            ),
            PlanningTask(
                task_id=2,
                title="后交",
                course_id=None,
                priority="high",
                minutes=60,
                due_at=datetime(2026, 10, 20, 23, 59, tzinfo=tz),
                due_date_only=True,
                target_date=None,
                estimated=False,
            ),
        ),
    )
    result = allocate_tasks(inputs, _resolved(inputs))
    first = [block for block in result.blocks if block.task_id == 1]
    assert len(first) == 1
    assert first[0].minutes == 90
    assert first[0].ends_at <= datetime(2026, 10, 19, 19, 30, tzinfo=tz)
    assert all(item.remaining_minutes == 0 for item in result.dispositions)
    assert result.capacity_gap_minutes == 0


def test_allocation_never_crosses_deadline_and_keeps_shortfall_reason() -> None:
    tz = ZoneInfo("Asia/Shanghai")
    inputs = _input(
        range_start=date(2026, 10, 19),
        range_end=date(2026, 10, 19),
        now=datetime(2026, 10, 19, 0, tzinfo=tz),
        availability=(AvailabilityRule(0, time(18), time(22)),),
        tasks=(
            PlanningTask(
                task_id=1,
                title="大作业",
                course_id=None,
                priority="high",
                minutes=180,
                due_at=datetime(2026, 10, 19, 19, 0, tzinfo=tz),
                due_date_only=False,
                target_date=None,
                estimated=True,
            ),
        ),
    )
    result = allocate_tasks(inputs, _resolved(inputs))
    assert len(result.blocks) == 1
    assert result.blocks[0].minutes == 60
    disposition = result.dispositions[0]
    assert disposition.status == "partial"
    assert disposition.reason_code == "insufficient_capacity_before_due"
    assert disposition.remaining_minutes == 120
    assert result.capacity_gap_minutes == 120


def test_passed_deadline_is_not_scheduled() -> None:
    tz = ZoneInfo("Asia/Shanghai")
    inputs = _input(
        range_start=date(2026, 10, 19),
        range_end=date(2026, 10, 19),
        now=datetime(2026, 10, 19, 20, tzinfo=tz),
        availability=(AvailabilityRule(0, time(18), time(22)),),
        tasks=(
            PlanningTask(
                task_id=1,
                title="逾期",
                course_id=None,
                priority="high",
                minutes=60,
                due_at=datetime(2026, 10, 19, 19, tzinfo=tz),
                due_date_only=False,
                target_date=None,
                estimated=False,
            ),
        ),
    )
    result = allocate_tasks(inputs, _resolved(inputs))
    assert result.blocks == ()
    assert result.dispositions[0].reason_code == "due_passed"


def test_no_availability_keeps_unscheduled_reason() -> None:
    tz = ZoneInfo("Asia/Shanghai")
    inputs = _input(
        range_start=date(2026, 10, 19),
        range_end=date(2026, 10, 19),
        now=datetime(2026, 10, 19, 0, tzinfo=tz),
        availability=(),
        tasks=(
            PlanningTask(
                task_id=1,
                title="作业",
                course_id=None,
                priority="medium",
                minutes=60,
                due_at=None,
                due_date_only=False,
                target_date=None,
                estimated=False,
            ),
        ),
    )
    result = allocate_tasks(inputs, _resolved(inputs))
    assert result.dispositions[0].status == "unscheduled"
    assert result.dispositions[0].reason_code == "no_available_time"


def _sixty_minute_task(task_id: int, priority: str = "medium") -> PlanningTask:
    return PlanningTask(
        task_id=task_id,
        title=f"任务{task_id}",
        course_id=None,
        priority=priority,
        minutes=60,
        due_at=None,
        due_date_only=False,
        target_date=None,
        estimated=False,
    )


def test_daily_capacity_limits_total_planned_minutes() -> None:
    tz = ZoneInfo("Asia/Shanghai")
    inputs = _input(
        range_start=date(2026, 10, 19),
        range_end=date(2026, 10, 19),
        now=datetime(2026, 10, 19, 0, tzinfo=tz),
        availability=(AvailabilityRule(0, time(18), time(22)),),
        tasks=(
            PlanningTask(
                task_id=1,
                title="甲",
                course_id=None,
                priority="high",
                minutes=90,
                due_at=None,
                due_date_only=False,
                target_date=None,
                estimated=False,
            ),
            PlanningTask(
                task_id=2,
                title="乙",
                course_id=None,
                priority="medium",
                minutes=90,
                due_at=None,
                due_date_only=False,
                target_date=None,
                estimated=False,
            ),
        ),
        daily_capacity_minutes=120,
    )
    result = allocate_tasks(inputs, _resolved(inputs))
    assert sum(block.minutes for block in result.blocks) == 120
    assert result.dispositions[0].scheduled_minutes == 90
    assert result.dispositions[1].status == "partial"
    assert result.dispositions[1].reason_code == "insufficient_capacity_in_range"
    assert result.capacity_gap_minutes == 60


def test_break_is_kept_between_blocks() -> None:
    tz = ZoneInfo("Asia/Shanghai")
    inputs = _input(
        range_start=date(2026, 10, 19),
        range_end=date(2026, 10, 19),
        now=datetime(2026, 10, 19, 0, tzinfo=tz),
        availability=(AvailabilityRule(0, time(18), time(22)),),
        tasks=(_sixty_minute_task(1), _sixty_minute_task(2)),
        break_minutes=15,
    )
    result = allocate_tasks(inputs, _resolved(inputs))
    first, second = sorted(result.blocks, key=lambda block: block.starts_at)
    assert first.ends_at == datetime(2026, 10, 19, 19, 0, tzinfo=tz)
    assert second.starts_at == datetime(2026, 10, 19, 19, 15, tzinfo=tz)
    assert (second.starts_at - first.ends_at).total_seconds() == 15 * 60


def test_priority_orders_tasks_without_deadline() -> None:
    tz = ZoneInfo("Asia/Shanghai")
    inputs = _input(
        range_start=date(2026, 10, 19),
        range_end=date(2026, 10, 19),
        now=datetime(2026, 10, 19, 0, tzinfo=tz),
        availability=(AvailabilityRule(0, time(18), time(22)),),
        tasks=(_sixty_minute_task(1, "low"), _sixty_minute_task(2, "high")),
        break_minutes=0,
    )
    result = allocate_tasks(inputs, _resolved(inputs))
    first = min(result.blocks, key=lambda block: block.starts_at)
    assert first.task_id == 2
    assert any(
        source["kind"] == "priority" for source in result.blocks[0].constraint_source
    )


def test_weekly_capacity_is_respected_across_days() -> None:
    tz = ZoneInfo("Asia/Shanghai")
    inputs = _input(
        range_start=date(2026, 10, 19),
        range_end=date(2026, 10, 20),
        now=datetime(2026, 10, 19, 0, tzinfo=tz),
        availability=(
            AvailabilityRule(0, time(18), time(22)),
            AvailabilityRule(1, time(18), time(22)),
        ),
        tasks=(
            PlanningTask(
                task_id=1,
                title="周任务",
                course_id=None,
                priority="high",
                minutes=90,
                due_at=None,
                due_date_only=False,
                target_date=None,
                estimated=False,
            ),
        ),
        weekly_capacity_minutes=60,
    )
    result = allocate_tasks(inputs, _resolved(inputs))
    assert sum(block.minutes for block in result.blocks) == 60
    assert result.dispositions[0].remaining_minutes == 30
    assert all(
        any(source["kind"] == "weekly_capacity" for source in block.constraint_source)
        for block in result.blocks
    )


def test_zero_duration_does_not_silently_enter_schedule() -> None:
    resolved = resolve_assumptions(_input(tasks=(_task(minutes=0),)), AssumptionOptions())
    assert resolved.tasks[0].minutes == 0
    assert {gap["code"] for gap in resolved.gaps} == {
        "missing_due_date",
        "no_remaining_work",
    }
