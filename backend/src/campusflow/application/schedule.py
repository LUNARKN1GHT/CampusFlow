"""固定日程与可用时间用例。

update_fixed_event 接收"合并后的最终值"（由 API 层把补丁与当前值合并后传入）。
"""

from datetime import date, datetime, time

from campusflow.application.ports.repositories import (
    AvailabilitySlotData,
    FixedEventData,
    Repositories,
)
from campusflow.domain.errors import DomainError, NotFoundError
from campusflow.domain.schedule import validate_availability_slot, validate_fixed_event
from campusflow.domain.states import EventRecurrence


def list_fixed_events(repos: Repositories, workspace_id: int) -> list[FixedEventData]:
    return repos.schedule.list_fixed_events(workspace_id)


def get_fixed_event(repos: Repositories, event_id: int) -> FixedEventData:
    event = repos.schedule.get_fixed_event(event_id)
    if event is None:
        raise NotFoundError("日程不存在")
    return event


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
    error = validate_fixed_event(starts_at, ends_at, recurrence, repeat_until)
    if error:
        raise DomainError(error)
    if course_id is not None and repos.courses.get(course_id) is None:
        raise NotFoundError("课程不存在")
    event = repos.schedule.create_fixed_event(
        workspace_id, course_id, title, starts_at, ends_at, location, recurrence, repeat_until
    )
    repos.uow.commit()
    return event


def update_fixed_event(
    repos: Repositories,
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
    if repos.schedule.get_fixed_event(event_id) is None:
        raise NotFoundError("日程不存在")
    error = validate_fixed_event(starts_at, ends_at, recurrence, repeat_until)
    if error:
        raise DomainError(error)
    if course_id is not None and repos.courses.get(course_id) is None:
        raise NotFoundError("课程不存在")
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


def delete_fixed_event(repos: Repositories, event_id: int) -> None:
    if not repos.schedule.delete_fixed_event(event_id):
        raise NotFoundError("日程不存在")
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
    error = validate_availability_slot(day_of_week, start_time, end_time)
    if error:
        raise DomainError(error)
    slot = repos.schedule.create_availability_slot(workspace_id, day_of_week, start_time, end_time)
    repos.uow.commit()
    return slot


def delete_availability_slot(repos: Repositories, slot_id: int) -> None:
    if not repos.schedule.delete_availability_slot(slot_id):
        raise NotFoundError("可用时间段不存在")
    repos.uow.commit()
