"""API 输入输出 schema。

状态字段直接复用领域枚举，避免 API 与领域各定义一套；需要精确契约的
请求模型设置 extra="forbid"，未知字段返回 422，防止拼写错误被静默忽略。
"""

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
    """只切换归档标记；不会删除或改写该学期的课程、任务与日程。"""

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
        # PATCH 可省略字段，但已提交的学期与名称不能被显式 null 清空。
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
    # A001：创建子任务时指定父任务；包含关系与依赖关系分开保存
    parent_task_id: int | None = Field(default=None, ge=1)


class TaskUpdate(BaseModel):
    """未提交字段保持原值；title/priority/due_source 不允许显式 null。"""

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
    """只改执行进度并追加历史；reason 不改变截止时间，也不修改候选核对状态。"""

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
    parent_task_id: int | None
    # 逾期由截止时间与完成情况实时计算，不是持久化的执行状态。
    overdue: bool
    created_at: datetime
    updated_at: datetime


class TaskLinkOut(BaseModel):
    id: int
    title: str
    progress: TaskProgress
    due_date: date | None
    parent_task_id: int | None


class TaskRollupOut(BaseModel):
    """只汇总一层子任务；总耗时为 0 时 percent_complete 为 null，不按缺失值估算。"""

    subtask_count: int
    estimated_minutes: int
    remaining_minutes: int
    missing_duration_count: int
    progress_counts: dict[str, int]
    percent_complete: int | None


class TaskRelationsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: int
    parent: TaskLinkOut | None
    subtasks: list[TaskLinkOut]
    prerequisites: list[TaskLinkOut]
    dependents: list[TaskLinkOut]
    rollup: TaskRollupOut


class SubtaskDraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=300)
    priority: TaskPriority = TaskPriority.MEDIUM
    estimated_minutes: int | None = Field(default=None, gt=0)
    remaining_minutes: int | None = Field(default=None, ge=0)
    due_date: date | None = None


class SubtaskBatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # 单次最多 20 项；父任务必须是顶层任务，不支持多层嵌套。
    items: list[SubtaskDraftIn] = Field(min_length=1, max_length=20)


class SplitSuggestionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    titles: list[str] | None = Field(default=None, max_length=20)


class TaskParentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parent_task_id: int | None = Field(default=None, ge=1)


class TaskDependencyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # 必要前置依赖，不是父子包含关系；全空间成环时整条边被拒绝。
    depends_on_task_id: int = Field(ge=1)


class TaskDependencyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    task_id: int
    depends_on_task_id: int
    created_at: datetime


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
    """无偏移时间按工作空间时区解释；每周重复必须给出 repeat_until（含当天）。"""

    course_id: int | None = None
    title: str = Field(min_length=1, max_length=300)
    starts_at: datetime
    ends_at: datetime
    location: str | None = Field(default=None, max_length=200)
    recurrence: EventRecurrence = EventRecurrence.NONE
    repeat_until: date | None = None


class FixedEventUpdate(BaseModel):
    """未提交字段保持原值；title/starts_at/ends_at/recurrence 不允许显式 null。"""

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
    # day_of_week 以周一为 0、周日为 6；时段按工作空间时区解释且不跨午夜。
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
    # document=原文规定、user=用户补充、inferred=系统推断、suggestion=规划建议；
    # 用户填写的值不能声明为 document。
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
