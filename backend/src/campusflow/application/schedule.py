"""固定日程与可用时间用例。

无偏移输入按工作空间时区解释，带偏移输入保留时间点；DST 缺失或歧义时刻报错。
每周重复按本地墙上时间展开，单次时长保持固定，供冲突检查与排期使用。
update_fixed_event 接收"合并后的最终值"；所有按 ID 的操作校验空间归属（W006）。
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from campusflow.application.courses import require_semester_in_workspace
from campusflow.application.ports.repositories import (
    AvailabilitySlotData,
    FixedEventData,
    Repositories,
)
from campusflow.application.scope import require_in_workspace
from campusflow.application.settings import get_settings
from campusflow.domain.errors import DomainError, NotFoundError
from campusflow.domain.schedule import validate_availability_slot, validate_fixed_event
from campusflow.domain.states import EventRecurrence
from campusflow.domain.timezones import in_workspace_timezone


@dataclass
class FixedEventOccurrence:
    """展开后的日程实例；每周重复的多个实例共享同一 source_event_id。"""

    source_event_id: int
    course_id: int | None
    title: str
    starts_at: datetime
    ends_at: datetime
    location: str | None


def list_fixed_events(
    repos: Repositories, workspace_id: int, *, semester_id: int | None = None
) -> list[FixedEventData]:
    if semester_id is not None:
        require_semester_in_workspace(repos, workspace_id, semester_id)
    return repos.schedule.list_fixed_events(workspace_id, semester_id=semester_id)


def list_fixed_event_occurrences(
    repos: Repositories,
    workspace_id: int,
    start_date: date,
    end_date: date,
    *,
    semester_id: int | None = None,
) -> list[FixedEventOccurrence]:
    """把固定日程展开为查询闭区间内的实例，日期边界按工作空间时区解释。

    每周重复沿用首次的本地墙上时间，未来 DST 缺失/歧义时报错而不静默挪动；
    单次时长按 UTC 时间差保持不变，结果按 UTC 开始时间排序。
    """
    if end_date < start_date:
        raise DomainError("查询结束日期不得早于开始日期")
    timezone = ZoneInfo(get_settings(repos, workspace_id).timezone)
    range_start = datetime.combine(start_date, time.min, tzinfo=timezone).astimezone(UTC)
    range_end = datetime.combine(
        end_date + timedelta(days=1), time.min, tzinfo=timezone
    ).astimezone(UTC)
    occurrences: list[FixedEventOccurrence] = []

    for event in list_fixed_events(repos, workspace_id, semester_id=semester_id):
        first_start = event.starts_at.astimezone(timezone)
        duration = event.ends_at.astimezone(UTC) - event.starts_at.astimezone(UTC)
        starts = [first_start]
        if event.recurrence == EventRecurrence.WEEKLY and event.repeat_until is not None:
            starts = []
            current = first_start
            while current.date() <= event.repeat_until and current.astimezone(UTC) < range_end:
                # 只转换查询范围内的实例；未来 DST 缺失／重复时间明确报错，不静默挪动。
                if current.astimezone(UTC) + duration > range_start:
                    starts.append(
                        first_start
                        if current == first_start
                        else in_workspace_timezone(current.replace(tzinfo=None), timezone)
                    )
                current += timedelta(days=7)

        for occurrence_start in starts:
            occurrence_end = occurrence_start.astimezone(UTC) + duration
            if occurrence_start.astimezone(UTC) < range_end and occurrence_end > range_start:
                occurrences.append(
                    FixedEventOccurrence(
                        source_event_id=event.id,
                        course_id=event.course_id,
                        title=event.title,
                        starts_at=occurrence_start,
                        ends_at=occurrence_end.astimezone(timezone),
                        location=event.location,
                    )
                )

    return sorted(occurrences, key=lambda occurrence: occurrence.starts_at.astimezone(UTC))


def get_fixed_event(repos: Repositories, workspace_id: int, event_id: int) -> FixedEventData:
    event = _require_event_in_workspace(repos, workspace_id, event_id)
    return event


def _require_event_in_workspace(
    repos: Repositories, workspace_id: int, event_id: int
) -> FixedEventData:
    event = repos.schedule.get_fixed_event(event_id)
    if event is None:
        raise NotFoundError("日程不存在")
    require_in_workspace(event.workspace_id, workspace_id, "日程不存在")
    return event


def _require_course_in_workspace(repos: Repositories, workspace_id: int, course_id: int) -> None:
    course = repos.courses.get(course_id)
    if course is None:
        raise NotFoundError("课程不存在")
    require_in_workspace(course.workspace_id, workspace_id, "课程不存在")


def create_fixed_event(
    repos: Repositories,
    workspace_id: int,
    *,
    course_id: int | None,
    title: str,
    starts_at: datetime,
    ends_at: datetime,
    location: str | None,
    recurrence: EventRecurrence,
    repeat_until: date | None,
) -> FixedEventData:
    """创建固定日程；时间先按工作空间时区解析，课程必须属于当前空间。

    无偏移时间视为工作空间墙上时间，带偏移时间保留原时间点；每周重复必须给出截止日期。
    """
    timezone = ZoneInfo(get_settings(repos, workspace_id).timezone)
    starts_at = in_workspace_timezone(starts_at, timezone)
    ends_at = in_workspace_timezone(ends_at, timezone)
    error = validate_fixed_event(starts_at, ends_at, recurrence, repeat_until)
    if error:
        raise DomainError(error)
    if course_id is not None:
        _require_course_in_workspace(repos, workspace_id, course_id)
    event = repos.schedule.create_fixed_event(
        workspace_id, course_id, title, starts_at, ends_at, location, recurrence, repeat_until
    )
    repos.uow.commit()
    return event


def update_fixed_event(
    repos: Repositories,
    workspace_id: int,
    event_id: int,
    *,
    course_id: int | None,
    title: str,
    starts_at: datetime,
    ends_at: datetime,
    location: str | None,
    recurrence: EventRecurrence,
    repeat_until: date | None,
) -> FixedEventData:
    """写入固定日程的合并后最终值；时间解释与校验规则与创建一致。"""
    _require_event_in_workspace(repos, workspace_id, event_id)
    timezone = ZoneInfo(get_settings(repos, workspace_id).timezone)
    starts_at = in_workspace_timezone(starts_at, timezone)
    ends_at = in_workspace_timezone(ends_at, timezone)
    error = validate_fixed_event(starts_at, ends_at, recurrence, repeat_until)
    if error:
        raise DomainError(error)
    if course_id is not None:
        _require_course_in_workspace(repos, workspace_id, course_id)
    updated = repos.schedule.update_fixed_event(
        event_id,
        course_id=course_id,
        title=title,
        starts_at=starts_at,
        ends_at=ends_at,
        location=location,
        recurrence=recurrence,
        repeat_until=repeat_until,
    )
    repos.uow.commit()
    return updated


def delete_fixed_event(repos: Repositories, workspace_id: int, event_id: int) -> None:
    """删除固定日程；已接受计划不随之改写，变化由调整建议提示。"""
    _require_event_in_workspace(repos, workspace_id, event_id)
    repos.schedule.delete_fixed_event(event_id)
    repos.uow.commit()


def list_availability_slots(repos: Repositories, workspace_id: int) -> list[AvailabilitySlotData]:
    return repos.schedule.list_availability_slots(workspace_id)


def create_availability_slot(
    repos: Repositories,
    workspace_id: int,
    *,
    day_of_week: int,
    start_time: time,
    end_time: time,
) -> AvailabilitySlotData:
    """新增按星期重复的可用时段；同日重叠会被拒绝，跨午夜需拆成两天录入。"""
    error = validate_availability_slot(day_of_week, start_time, end_time)
    if error:
        raise DomainError(error)
    for existing in repos.schedule.list_availability_slots(workspace_id):
        if existing.day_of_week != day_of_week:
            continue
        if start_time < existing.end_time and end_time > existing.start_time:
            raise DomainError("可用时间段与已有时段重叠，请调整后再保存")
    slot = repos.schedule.create_availability_slot(workspace_id, day_of_week, start_time, end_time)
    repos.uow.commit()
    return slot


def delete_availability_slot(repos: Repositories, workspace_id: int, slot_id: int) -> None:
    """删除当前空间的可用时段；已接受计划不随之改写，变化留给调整建议提示。"""
    slot = next(
        (s for s in repos.schedule.list_availability_slots(workspace_id) if s.id == slot_id), None
    )
    if slot is None:
        raise NotFoundError("可用时间段不存在")
    repos.schedule.delete_availability_slot(slot_id)
    repos.uow.commit()
