"""API 输入输出 schema。"""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

from campusflow.domain.states import EventRecurrence, TaskPriority, TaskProgress

LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def _assume_local(value: datetime) -> datetime:
    """前端未带时区的日期时间按本地时区理解。"""
    return value if value.tzinfo is not None else value.replace(tzinfo=LOCAL_TZ)


class SemesterCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    start_date: date
    end_date: date


class SemesterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    start_date: date
    end_date: date
    archived: bool


class SemesterArchive(BaseModel):
    archived: bool


class CourseCreate(BaseModel):
    semester_id: int
    name: str = Field(min_length=1, max_length=200)
    code: str | None = Field(default=None, max_length=50)
    teacher: str | None = Field(default=None, max_length=100)
    class_name: str | None = Field(default=None, max_length=100)


class CourseUpdate(BaseModel):
    semester_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    code: str | None = Field(default=None, max_length=50)
    teacher: str | None = Field(default=None, max_length=100)
    class_name: str | None = Field(default=None, max_length=100)


class CourseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    semester_id: int
    name: str
    code: str | None
    teacher: str | None
    class_name: str | None


class TaskCreate(BaseModel):
    course_id: int | None = None
    title: str = Field(min_length=1, max_length=300)
    description: str | None = None
    due_date: date | None = None
    due_time: time | None = None
    priority: TaskPriority = TaskPriority.MEDIUM
    estimated_minutes: int | None = Field(default=None, gt=0)


class TaskUpdate(BaseModel):
    course_id: int | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    due_date: date | None = None
    due_time: time | None = None
    priority: TaskPriority | None = None
    estimated_minutes: int | None = Field(default=None, gt=0)


class TaskProgressPatch(BaseModel):
    progress: TaskProgress


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
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


class FixedEventCreate(BaseModel):
    course_id: int | None = None
    title: str = Field(min_length=1, max_length=300)
    starts_at: datetime
    ends_at: datetime
    location: str | None = Field(default=None, max_length=200)
    recurrence: EventRecurrence = EventRecurrence.NONE
    repeat_until: date | None = None

    @field_validator("starts_at", "ends_at")
    @classmethod
    def assume_local(cls, value: datetime) -> datetime:
        return _assume_local(value)


class FixedEventUpdate(BaseModel):
    course_id: int | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    location: str | None = Field(default=None, max_length=200)
    recurrence: EventRecurrence | None = None
    repeat_until: date | None = None

    @field_validator("starts_at", "ends_at")
    @classmethod
    def assume_local(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _assume_local(value)


class FixedEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    course_id: int | None
    title: str
    starts_at: datetime
    ends_at: datetime
    location: str | None
    recurrence: EventRecurrence
    repeat_until: date | None


class AvailabilitySlotCreate(BaseModel):
    day_of_week: int = Field(ge=0, le=6)
    start_time: time
    end_time: time


class AvailabilitySlotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    day_of_week: int
    start_time: time
    end_time: time
