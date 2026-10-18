"""M5 排期边界与不可行输入验证（P010）。"""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from campusflow.domain.planning import (
    AssumptionOptions,
    AvailabilityRule,
    FixedEventOccurrence,
    PlanningInput,
    PlanningTask,
    allocate_tasks,
    build_free_windows,
    resolve_assumptions,
)

TZ = ZoneInfo("Asia/Shanghai")


def _input(**overrides) -> PlanningInput:
    values = {
        "timezone": "Asia/Shanghai",
        "range_start": date(2026, 10, 19),
        "range_end": date(2026, 10, 19),
        "now": datetime(2026, 10, 19, 0, tzinfo=TZ),
        "tasks": (),
        "excluded_tasks": (),
        "fixed_events": (),
        "availability": (AvailabilityRule(0, time(18), time(22)),),
        "daily_capacity_minutes": 240,
        "weekly_capacity_minutes": 1680,
        "break_minutes": 15,
        "buffer_minutes": 0,
        "segment_minutes": 90,
    }
    values.update(overrides)
    return PlanningInput(**values)


def _task(task_id: int, minutes: int | None, **overrides) -> PlanningTask:
    values = {
        "task_id": task_id,
        "title": f"任务{task_id}",
        "course_id": None,
        "priority": "medium",
        "minutes": minutes,
        "due_at": None,
        "due_date_only": False,
        "target_date": None,
        "estimated": False,
    }
    values.update(overrides)
    return PlanningTask(**values)


def _allocate(inputs: PlanningInput, options: AssumptionOptions | None = None):
    resolved = resolve_assumptions(inputs, options or AssumptionOptions())
    return allocate_tasks(inputs, resolved)


def test_zero_availability_schedules_nothing_and_explains() -> None:
    result = _allocate(_input(availability=(), tasks=(_task(1, 60),)))
    assert result.blocks == ()
    assert result.dispositions[0].reason_code == "no_available_time"
    assert result.capacity_gap_minutes == 60


def test_tight_deadline_at_window_start_cannot_be_scheduled() -> None:
    result = _allocate(
        _input(
            tasks=(
                _task(1, 60, due_at=datetime(2026, 10, 19, 18, tzinfo=TZ)),
            )
        )
    )
    assert result.blocks == ()
    assert result.dispositions[0].status == "unscheduled"


def test_block_can_end_exactly_at_deadline_but_never_after() -> None:
    result = _allocate(
        _input(
            tasks=(
                _task(1, 90, due_at=datetime(2026, 10, 19, 19, 30, tzinfo=TZ)),
            )
        )
    )
    block = result.blocks[0]
    deadline = datetime(2026, 10, 19, 19, 30, tzinfo=TZ)
    assert block.ends_at == deadline
    assert block.ends_at <= deadline


def test_cross_day_planning_keeps_each_block_inside_its_day() -> None:
    inputs = _input(
        range_start=date(2026, 10, 19),
        range_end=date(2026, 10, 20),
        availability=(
            AvailabilityRule(0, time(18), time(22)),
            AvailabilityRule(1, time(18), time(22)),
        ),
        daily_capacity_minutes=180,
        break_minutes=0,
        segment_minutes=90,
        tasks=(_task(1, 300, priority="high"),),
    )
    result = _allocate(inputs)
    assert result.dispositions[0].remaining_minutes == 0
    assert {block.starts_at.date() for block in result.blocks} == {
        date(2026, 10, 19),
        date(2026, 10, 20),
    }
    for block in result.blocks:
        assert block.starts_at.date() == block.ends_at.date()


def test_fixed_event_and_capacity_constraints_hold_for_every_block() -> None:
    event = FixedEventOccurrence(
        event_id=1,
        title="晚课",
        start=datetime(2026, 10, 19, 19, tzinfo=TZ),
        end=datetime(2026, 10, 19, 20, tzinfo=TZ),
    )
    inputs = _input(
        fixed_events=(event,),
        daily_capacity_minutes=180,
        break_minutes=15,
        tasks=(
            _task(1, 200, priority="high", due_at=datetime(2026, 10, 19, 22, tzinfo=TZ)),
            _task(2, 90, priority="low"),
        ),
    )
    result = _allocate(inputs)
    for block in result.blocks:
        assert not (block.starts_at < event.end and block.ends_at > event.start)
        assert block.ends_at <= datetime(2026, 10, 19, 22, tzinfo=TZ)
    scheduled_minutes = sum(block.minutes for block in result.blocks)
    assert scheduled_minutes <= inputs.daily_capacity_minutes
    days = {block.starts_at.date() for block in result.blocks}
    assert all(
        sum(block.minutes for block in result.blocks if block.starts_at.date() == day)
        <= inputs.daily_capacity_minutes
        for day in days
    )


def test_unknown_duration_is_reported_without_scheduling() -> None:
    result = _allocate(_input(tasks=(_task(1, None),)))
    assert result.blocks == ()
    assert result.dispositions[0].reason_code == "missing_duration"
    assert result.capacity_gap_minutes == 0


def test_planning_does_not_mutate_task_deadline() -> None:
    due = datetime(2026, 10, 19, 21, tzinfo=TZ)
    task = _task(1, 60, due_at=due)
    inputs = _input(tasks=(task,))
    _allocate(inputs)
    assert task.due_at == due
    assert inputs.tasks[0].due_at == due


def test_free_windows_never_contain_fixed_events() -> None:
    event = FixedEventOccurrence(
        event_id=1,
        title="实验",
        start=datetime(2026, 10, 19, 18, tzinfo=TZ),
        end=datetime(2026, 10, 19, 19, tzinfo=TZ),
    )
    windows = build_free_windows(_input(fixed_events=(event,)))
    assert [(w.start.hour, w.end.hour) for w in windows] == [(19, 22)]
