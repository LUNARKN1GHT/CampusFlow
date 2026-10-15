"""API 输入输出 schema。"""

from datetime import date, datetime, time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from campusflow.domain.candidates import FieldOrigin
from campusflow.domain.states import DueSourceType, EventRecurrence, TaskPriority, TaskProgress


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

    @model_validator(mode="after")
    def reject_null_required_fields(self) -> "CourseUpdate":
        for field in ("semester_id", "name"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} 不能为 null")
        return self


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
    target_date: date | None = None
    due_source: DueSourceType = DueSourceType.USER
    due_note: str | None = Field(default=None, max_length=500)
    priority: TaskPriority = TaskPriority.MEDIUM
    estimated_minutes: int | None = Field(default=None, gt=0)
    remaining_minutes: int | None = Field(default=None, ge=0)


class TaskUpdate(BaseModel):
    course_id: int | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    due_date: date | None = None
    due_time: time | None = None
    target_date: date | None = None
    due_source: DueSourceType | None = None
    due_note: str | None = Field(default=None, max_length=500)
    priority: TaskPriority | None = None
    estimated_minutes: int | None = Field(default=None, gt=0)
    remaining_minutes: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def reject_null_required_fields(self) -> "TaskUpdate":
        for field in ("title", "priority", "due_source"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} 不能为 null")
        return self


class TaskProgressPatch(BaseModel):
    progress: TaskProgress
    reason: str | None = Field(default=None, max_length=500)


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    course_id: int | None
    # 来源状态（D009）：available 还有来源资料 / invalidated 来源已被删除 /
    # none 本来就没有来源。前端据此显示"引用已不可用"，不再自己推断。
    source_state: str
    source_invalidated_at: datetime | None
    source_invalidated_note: str | None
    title: str
    description: str | None
    due_date: date | None
    due_time: time | None
    target_date: date | None
    due_source: DueSourceType
    due_note: str | None
    progress: TaskProgress
    priority: TaskPriority
    estimated_minutes: int | None
    remaining_minutes: int | None
    overdue: bool
    created_at: datetime
    updated_at: datetime


class TaskProgressChangeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    task_id: int
    from_progress: TaskProgress
    to_progress: TaskProgress
    reason: str | None
    previous_remaining_minutes: int | None
    new_remaining_minutes: int | None
    changed_at: datetime


class FixedEventCreate(BaseModel):
    course_id: int | None = None
    title: str = Field(min_length=1, max_length=300)
    starts_at: datetime
    ends_at: datetime
    location: str | None = Field(default=None, max_length=200)
    recurrence: EventRecurrence = EventRecurrence.NONE
    repeat_until: date | None = None


class FixedEventUpdate(BaseModel):
    course_id: int | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    location: str | None = Field(default=None, max_length=200)
    recurrence: EventRecurrence | None = None
    repeat_until: date | None = None

    @model_validator(mode="after")
    def reject_null_required_fields(self) -> "FixedEventUpdate":
        for field in ("title", "starts_at", "ends_at", "recurrence"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} 不能为 null")
        return self


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


class FixedEventOccurrenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    source_event_id: int
    course_id: int | None
    title: str
    starts_at: datetime
    ends_at: datetime
    location: str | None


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


class RuleOut(BaseModel):
    """正式课程规则（R006）：只含经核对确认转换的规则。"""

    model_config = ConfigDict(from_attributes=True)
    id: int
    course_id: int | None
    semester_id: int | None
    title: str
    rule_text: str
    conditions_text: str | None
    scope_text: str | None
    effective_text: str | None
    source_material_id: int | None
    source_candidate_id: int | None
    # 来源状态（对齐 D009）：available 还有来源 / invalidated 来源已被删除 / none 无来源
    source_state: str
    source_invalidated_at: datetime | None
    source_invalidated_note: str | None
    created_at: datetime
    updated_at: datetime


class SourceCitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    chunk_id: int
    quote: str
    locator_type: str
    locator_value: str


class SourceFieldOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    name: str
    value: Any
    origin: FieldOrigin
    note: str | None
    verifiable: bool
    citations: list[SourceCitationOut]


class SourceCorrectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    field_name: str
    previous_value: Any
    new_value: Any
    new_origin: FieldOrigin
    operator: str
    note: str | None
    changed_at: datetime


class SourceDetailOut(BaseModel):
    """R007：任务或日程的来源详情。"""

    model_config = ConfigDict(from_attributes=True)
    object_type: str
    object_id: int
    source_type: str
    source_state: str
    source_note: str | None
    material_id: int | None
    material_title: str | None
    version_no: int | None
    candidate_id: int | None
    due_source: str | None
    due_note: str | None
    fields: list[SourceFieldOut]
    corrections: list[SourceCorrectionOut]
