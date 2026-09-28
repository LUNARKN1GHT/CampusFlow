"""SQLAlchemy 仓储实现。ORM 模型只在本层使用，向外返回端口 DTO。"""

from datetime import date, datetime, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from campusflow.application.ports.repositories import (
    AvailabilitySlotData,
    CourseData,
    FixedEventData,
    SemesterData,
    TaskData,
)
from campusflow.domain.states import EventRecurrence, TaskPriority, TaskProgress
from campusflow.infrastructure.db.models import (
    AvailabilitySlot,
    Course,
    FixedEvent,
    Semester,
    Task,
)


class SqlAlchemyUnitOfWork:
    def __init__(self, session: Session) -> None:
        self._session = session

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()


def _semester_to_data(row: Semester) -> SemesterData:
    return SemesterData(
        id=row.id,
        workspace_id=row.workspace_id,
        name=row.name,
        start_date=row.start_date,
        end_date=row.end_date,
        archived=row.archived,
    )


def _course_to_data(row: Course) -> CourseData:
    return CourseData(
        id=row.id,
        workspace_id=row.workspace_id,
        semester_id=row.semester_id,
        name=row.name,
        code=row.code,
        teacher=row.teacher,
        class_name=row.class_name,
    )


def _task_to_data(row: Task) -> TaskData:
    return TaskData(
        id=row.id,
        workspace_id=row.workspace_id,
        course_id=row.course_id,
        title=row.title,
        description=row.description,
        due_date=row.due_date,
        due_time=row.due_time,
        progress=TaskProgress(row.progress),
        priority=TaskPriority(row.priority),
        estimated_minutes=row.estimated_minutes,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _fixed_event_to_data(row: FixedEvent) -> FixedEventData:
    return FixedEventData(
        id=row.id,
        workspace_id=row.workspace_id,
        course_id=row.course_id,
        title=row.title,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        location=row.location,
        recurrence=EventRecurrence(row.recurrence),
        repeat_until=row.repeat_until,
    )


def _slot_to_data(row: AvailabilitySlot) -> AvailabilitySlotData:
    return AvailabilitySlotData(
        id=row.id,
        workspace_id=row.workspace_id,
        day_of_week=row.day_of_week,
        start_time=row.start_time,
        end_time=row.end_time,
    )


class SqlAlchemySemesterRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list(self, workspace_id: int, *, include_archived: bool = False) -> list[SemesterData]:
        stmt = select(Semester).where(Semester.workspace_id == workspace_id)
        if not include_archived:
            stmt = stmt.where(Semester.archived.is_(False))
        stmt = stmt.order_by(Semester.start_date)
        return [_semester_to_data(row) for row in self._session.scalars(stmt)]

    def get(self, semester_id: int) -> SemesterData | None:
        row = self._session.get(Semester, semester_id)
        return _semester_to_data(row) if row is not None else None

    def create(
        self, workspace_id: int, name: str, start_date: date, end_date: date
    ) -> SemesterData:
        row = Semester(
            workspace_id=workspace_id,
            name=name,
            start_date=start_date,
            end_date=end_date,
            archived=False,
        )
        self._session.add(row)
        self._session.flush()
        return _semester_to_data(row)

    def set_archived(self, semester_id: int, archived: bool) -> SemesterData | None:
        row = self._session.get(Semester, semester_id)
        if row is None:
            return None
        row.archived = archived
        self._session.flush()
        return _semester_to_data(row)


class SqlAlchemyCourseRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list(self, workspace_id: int, semester_id: int | None = None) -> list[CourseData]:
        stmt = select(Course).where(Course.workspace_id == workspace_id)
        if semester_id is not None:
            stmt = stmt.where(Course.semester_id == semester_id)
        stmt = stmt.order_by(Course.name)
        return [_course_to_data(row) for row in self._session.scalars(stmt)]

    def get(self, course_id: int) -> CourseData | None:
        row = self._session.get(Course, course_id)
        return _course_to_data(row) if row is not None else None

    def create(
        self,
        workspace_id: int,
        semester_id: int,
        name: str,
        code: str | None,
        teacher: str | None,
        class_name: str | None,
    ) -> CourseData:
        row = Course(
            workspace_id=workspace_id,
            semester_id=semester_id,
            name=name,
            code=code,
            teacher=teacher,
            class_name=class_name,
        )
        self._session.add(row)
        self._session.flush()
        return _course_to_data(row)

    def update(
        self,
        course_id: int,
        *,
        semester_id: int,
        name: str,
        code: str | None,
        teacher: str | None,
        class_name: str | None,
    ) -> CourseData | None:
        row = self._session.get(Course, course_id)
        if row is None:
            return None
        row.semester_id = semester_id
        row.name = name
        row.code = code
        row.teacher = teacher
        row.class_name = class_name
        self._session.flush()
        return _course_to_data(row)


class SqlAlchemyTaskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list(
        self,
        workspace_id: int,
        *,
        course_id: int | None = None,
        progress: TaskProgress | None = None,
    ) -> list[TaskData]:
        stmt = select(Task).where(Task.workspace_id == workspace_id)
        if course_id is not None:
            stmt = stmt.where(Task.course_id == course_id)
        if progress is not None:
            stmt = stmt.where(Task.progress == progress)
        stmt = stmt.order_by(Task.due_date.nulls_last(), Task.id)
        return [_task_to_data(row) for row in self._session.scalars(stmt)]

    def get(self, task_id: int) -> TaskData | None:
        row = self._session.get(Task, task_id)
        return _task_to_data(row) if row is not None else None

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
    ) -> TaskData:
        row = Task(
            workspace_id=workspace_id,
            course_id=course_id,
            title=title,
            description=description,
            due_date=due_date,
            due_time=due_time,
            priority=priority,
            estimated_minutes=estimated_minutes,
        )
        self._session.add(row)
        self._session.flush()
        return _task_to_data(row)

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
    ) -> TaskData | None:
        row = self._session.get(Task, task_id)
        if row is None:
            return None
        row.course_id = course_id
        row.title = title
        row.description = description
        row.due_date = due_date
        row.due_time = due_time
        row.priority = priority
        row.estimated_minutes = estimated_minutes
        self._session.flush()
        return _task_to_data(row)

    def set_progress(self, task_id: int, progress: TaskProgress) -> TaskData | None:
        row = self._session.get(Task, task_id)
        if row is None:
            return None
        row.progress = progress
        self._session.flush()
        return _task_to_data(row)


class SqlAlchemyScheduleRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_fixed_events(self, workspace_id: int) -> list[FixedEventData]:
        stmt = (
            select(FixedEvent)
            .where(FixedEvent.workspace_id == workspace_id)
            .order_by(FixedEvent.starts_at)
        )
        return [_fixed_event_to_data(row) for row in self._session.scalars(stmt)]

    def get_fixed_event(self, event_id: int) -> FixedEventData | None:
        row = self._session.get(FixedEvent, event_id)
        return _fixed_event_to_data(row) if row is not None else None

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
    ) -> FixedEventData:
        row = FixedEvent(
            workspace_id=workspace_id,
            course_id=course_id,
            title=title,
            starts_at=starts_at,
            ends_at=ends_at,
            location=location,
            recurrence=recurrence,
            repeat_until=repeat_until,
        )
        self._session.add(row)
        self._session.flush()
        return _fixed_event_to_data(row)

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
    ) -> FixedEventData | None:
        row = self._session.get(FixedEvent, event_id)
        if row is None:
            return None
        row.course_id = course_id
        row.title = title
        row.starts_at = starts_at
        row.ends_at = ends_at
        row.location = location
        row.recurrence = recurrence
        row.repeat_until = repeat_until
        self._session.flush()
        return _fixed_event_to_data(row)

    def delete_fixed_event(self, event_id: int) -> bool:
        row = self._session.get(FixedEvent, event_id)
        if row is None:
            return False
        self._session.delete(row)
        self._session.flush()
        return True

    def list_availability_slots(self, workspace_id: int) -> list[AvailabilitySlotData]:
        stmt = (
            select(AvailabilitySlot)
            .where(AvailabilitySlot.workspace_id == workspace_id)
            .order_by(AvailabilitySlot.day_of_week, AvailabilitySlot.start_time)
        )
        return [_slot_to_data(row) for row in self._session.scalars(stmt)]

    def create_availability_slot(
        self, workspace_id: int, day_of_week: int, start_time: time, end_time: time
    ) -> AvailabilitySlotData:
        row = AvailabilitySlot(
            workspace_id=workspace_id,
            day_of_week=day_of_week,
            start_time=start_time,
            end_time=end_time,
        )
        self._session.add(row)
        self._session.flush()
        return _slot_to_data(row)

    def delete_availability_slot(self, slot_id: int) -> bool:
        row = self._session.get(AvailabilitySlot, slot_id)
        if row is None:
            return False
        self._session.delete(row)
        self._session.flush()
        return True
