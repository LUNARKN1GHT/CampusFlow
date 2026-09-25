"""应用层端口：仓储接口与数据传输对象。

应用层只依赖本文件定义的接口；具体实现位于 infrastructure/repositories.py。
"""

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Protocol

from campusflow.domain.states import EventRecurrence, TaskPriority, TaskProgress


class UnitOfWork(Protocol):
    def commit(self) -> None: ...

    def rollback(self) -> None: ...


@dataclass
class SemesterData:
    id: int
    workspace_id: int
    name: str
    start_date: date
    end_date: date
    archived: bool


@dataclass
class CourseData:
    id: int
    workspace_id: int
    semester_id: int
    name: str
    code: str | None
    teacher: str | None
    class_name: str | None


@dataclass
class TaskData:
    id: int
    workspace_id: int
    course_id: int | None
    title: str
    description: str | None
    due_date: date | None
    due_time: time | None
    progress: TaskProgress
    priority: TaskPriority
    estimated_minutes: int | None
    created_at: datetime
    updated_at: datetime


@dataclass
class FixedEventData:
    id: int
    workspace_id: int
    course_id: int | None
    title: str
    starts_at: datetime
    ends_at: datetime
    location: str | None
    recurrence: EventRecurrence
    repeat_until: date | None


@dataclass
class AvailabilitySlotData:
    id: int
    workspace_id: int
    day_of_week: int
    start_time: time
    end_time: time


class SemesterRepository(Protocol):
    def list(self, workspace_id: int, *, include_archived: bool = False) -> list[SemesterData]: ...

    def get(self, semester_id: int) -> SemesterData | None: ...

    def create(
        self, workspace_id: int, name: str, start_date: date, end_date: date
    ) -> SemesterData: ...

    def set_archived(self, semester_id: int, archived: bool) -> SemesterData | None: ...


class CourseRepository(Protocol):
    def list(self, workspace_id: int, semester_id: int | None = None) -> list[CourseData]: ...

    def get(self, course_id: int) -> CourseData | None: ...

    def create(
        self,
        workspace_id: int,
        semester_id: int,
        name: str,
        code: str | None,
        teacher: str | None,
        class_name: str | None,
    ) -> CourseData: ...

    def update(
        self,
        course_id: int,
        *,
        semester_id: int,
        name: str,
        code: str | None,
        teacher: str | None,
        class_name: str | None,
    ) -> CourseData | None: ...


class TaskRepository(Protocol):
    def list(
        self,
        workspace_id: int,
        *,
        course_id: int | None = None,
        progress: TaskProgress | None = None,
    ) -> list[TaskData]: ...

    def get(self, task_id: int) -> TaskData | None: ...

    def create(
        self,
        workspace_id: int,
        course_id: int | None,
        title: str,
        description: str | None,
        due_date: date | None,
        due_time: time | None,
        priority: TaskPriority,
        estimated_minutes: int | None,
    ) -> TaskData: ...

    def update(
        self,
        task_id: int,
        *,
        course_id: int | None,
        title: str,
        description: str | None,
        due_date: date | None,
        due_time: time | None,
        priority: TaskPriority,
        estimated_minutes: int | None,
    ) -> TaskData | None: ...

    def set_progress(self, task_id: int, progress: TaskProgress) -> TaskData | None: ...


class ScheduleRepository(Protocol):
    def list_fixed_events(self, workspace_id: int) -> list[FixedEventData]: ...

    def get_fixed_event(self, event_id: int) -> FixedEventData | None: ...

    def create_fixed_event(
        self,
        workspace_id: int,
        course_id: int | None,
        title: str,
        starts_at: datetime,
        ends_at: datetime,
        location: str | None,
        recurrence: EventRecurrence,
        repeat_until: date | None,
    ) -> FixedEventData: ...

    def update_fixed_event(
        self,
        event_id: int,
        *,
        course_id: int | None,
        title: str,
        starts_at: datetime,
        ends_at: datetime,
        location: str | None,
        recurrence: EventRecurrence,
        repeat_until: date | None,
    ) -> FixedEventData | None: ...

    def delete_fixed_event(self, event_id: int) -> bool: ...

    def list_availability_slots(self, workspace_id: int) -> list[AvailabilitySlotData]: ...

    def create_availability_slot(
        self, workspace_id: int, day_of_week: int, start_time: time, end_time: time
    ) -> AvailabilitySlotData: ...

    def delete_availability_slot(self, slot_id: int) -> bool: ...


@dataclass
class Repositories:
    """一组仓储与事务控制，由 API 依赖组装。"""

    semesters: SemesterRepository
    courses: CourseRepository
    tasks: TaskRepository
    schedule: ScheduleRepository
    uow: UnitOfWork
