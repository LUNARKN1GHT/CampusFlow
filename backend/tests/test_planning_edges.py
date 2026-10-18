"""M5 排期边界与不可行输入验证（P010）。"""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from campusflow.domain.planning import (
    AssumptionOptions,
    AvailabilityRule,
    BlockSuggestion,
    FixedEventOccurrence,
    PlanningInput,
    PlanningTask,
    allocate_tasks,
    build_free_windows,
    build_outcome,
    detect_overlaps,
    detect_risks,
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


def _block(task_id: int, start_hour: int, end_hour: int) -> BlockSuggestion:
    return BlockSuggestion(
        task_id=task_id,
        title=f"任务{task_id}",
        starts_at=datetime(2026, 10, 19, start_hour, tzinfo=TZ),
        ends_at=datetime(2026, 10, 19, end_hour, tzinfo=TZ),
        minutes=(end_hour - start_hour) * 60,
        seq=0,
        reason="测试",
        constraint_source=(),
    )


def test_overlapping_fixed_events_are_reported() -> None:
    first = FixedEventOccurrence(
        event_id=1,
        title="课程 A",
        start=datetime(2026, 10, 19, 18, tzinfo=TZ),
        end=datetime(2026, 10, 19, 19, tzinfo=TZ),
    )
    second = FixedEventOccurrence(
        event_id=2,
        title="课程 B",
        start=datetime(2026, 10, 19, 18, 30, tzinfo=TZ),
        end=datetime(2026, 10, 19, 19, 30, tzinfo=TZ),
    )
    conflicts = detect_overlaps(_input(fixed_events=(first, second)), ())
    assert len(conflicts) == 1
    assert conflicts[0]["type"] == "time_overlap"
    assert conflicts[0]["severity"] == "conflict"
    assert len(conflicts[0]["participants"]) == 2


def test_block_overlapping_fixed_event_is_reported() -> None:
    event = FixedEventOccurrence(
        event_id=1,
        title="课程",
        start=datetime(2026, 10, 19, 18, 30, tzinfo=TZ),
        end=datetime(2026, 10, 19, 19, 30, tzinfo=TZ),
    )
    conflicts = detect_overlaps(_input(fixed_events=(event,)), (_block(1, 18, 19),))
    assert len(conflicts) == 1
    assert conflicts[0]["type"] == "time_overlap"


def test_overlapping_blocks_are_reported_and_adjacent_blocks_are_not() -> None:
    adjacent = detect_overlaps(_input(availability=()), (_block(1, 18, 19), _block(2, 19, 20)))
    assert adjacent == []
    overlapping = detect_overlaps(
        _input(availability=()), (_block(1, 18, 19), _block(2, 18, 20))
    )
    assert len(overlapping) == 1
    assert overlapping[0]["type"] == "time_overlap"


def _outcome(inputs: PlanningInput):
    resolved = resolve_assumptions(inputs, AssumptionOptions())
    return resolved, build_outcome(inputs, resolved, allocate_tasks(inputs, resolved))


def test_deadline_risk_is_marked_as_estimate_based() -> None:
    inputs = _input(
        tasks=(
            _task(
                1,
                300,
                due_at=datetime(2026, 10, 19, 19, tzinfo=TZ),
                estimated=True,
            ),
        )
    )
    resolved, outcome = _outcome(inputs)
    risks = detect_risks(inputs, resolved, outcome)
    deadline = next(item for item in risks if item["type"] == "deadline_risk")
    assert deadline["severity"] == "risk"
    assert deadline["estimated"] is True
    assert deadline["shortfall_minutes"] == 240
    assert "估计" in deadline["message"]


def test_confirmed_shortfall_is_a_conflict_not_an_estimate() -> None:
    inputs = _input(
        tasks=(
            _task(
                1,
                300,
                due_at=datetime(2026, 10, 19, 19, tzinfo=TZ),
                estimated=False,
            ),
        )
    )
    resolved, outcome = _outcome(inputs)
    risks = detect_risks(inputs, resolved, outcome)
    deadline = next(item for item in risks if item["type"] == "deadline_risk")
    assert deadline["severity"] == "conflict"
    assert deadline["estimated"] is False
    assert "估计" not in deadline["message"]


def test_daily_and_weekly_load_risks_are_reported() -> None:
    inputs = _input(
        daily_capacity_minutes=120,
        weekly_capacity_minutes=120,
        availability=(),
    )
    resolved, outcome = _outcome(inputs)
    over_capacity = (_block(1, 18, 21),)
    risks = detect_risks(inputs, resolved, outcome, blocks=over_capacity)
    types = [item["type"] for item in risks]
    assert types.count("load_risk") == 2
    daily = next(item for item in risks if item["type"] == "load_risk" and "date" in item)
    assert daily["planned_minutes"] == 180
    assert daily["capacity_minutes"] == 120


def test_free_windows_never_contain_fixed_events() -> None:
    event = FixedEventOccurrence(
        event_id=1,
        title="实验",
        start=datetime(2026, 10, 19, 18, tzinfo=TZ),
        end=datetime(2026, 10, 19, 19, tzinfo=TZ),
    )
    windows = build_free_windows(_input(fixed_events=(event,)))
    assert [(w.start.hour, w.end.hour) for w in windows] == [(19, 22)]
