"""应用层端口：仓储接口与数据传输对象。

应用层只依赖本文件定义的接口；具体实现位于 infrastructure/repositories.py。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Protocol

from campusflow.domain.states import (
    EventRecurrence,
    MaterialSourceType,
    MaterialStatus,
    TaskPriority,
    TaskProgress,
)


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
class WorkspaceData:
    id: int
    name: str
    timezone: str
    daily_capacity_minutes: int
    break_minutes: int
    buffer_minutes: int


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
    source_material_id: int | None
    title: str
    description: str | None
    due_date: date | None
    due_time: time | None
    progress: TaskProgress
    priority: TaskPriority
    estimated_minutes: int | None
    remaining_minutes: int | None
    created_at: datetime
    updated_at: datetime


@dataclass
class TaskProgressChangeData:
    id: int
    task_id: int
    from_progress: TaskProgress
    to_progress: TaskProgress
    reason: str | None
    previous_remaining_minutes: int | None
    new_remaining_minutes: int | None
    changed_at: datetime


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


@dataclass
class MaterialData:
    """资料元数据（D001）。未知来源字段为 None，不伪造。"""

    id: int
    workspace_id: int
    semester_id: int | None
    course_id: int | None
    class_name: str | None
    title: str
    publisher: str | None
    published_at: datetime | None
    source_type: MaterialSourceType
    source_url: str | None
    status: MaterialStatus
    archived: bool
    imported_at: datetime


@dataclass
class MaterialVersionData:
    id: int
    material_id: int
    version_no: int
    note: str | None
    storage_key: str | None
    checksum: str | None
    created_at: datetime


@dataclass
class NewSourceChunk:
    """待保存的来源片段（尚未分配 id）。

    定位坐标结构化保存：段落号、页码或区域 bbox（0-1 归一化），
    与 locator_type/locator_value 展示文本并存。
    """

    version_id: int
    seq: int
    locator_type: str
    locator_value: str
    text: str
    page: int | None = None
    paragraph: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    confidence: float | None = None


@dataclass
class SourceChunkData:
    """来源片段（D004/D017）。locator 决定定位方式：段落号、页码或区域。"""

    id: int
    version_id: int
    seq: int
    locator_type: str
    locator_value: str
    text: str
    page: int | None
    paragraph: int | None
    bbox: tuple[float, float, float, float] | None
    confidence: float | None


@dataclass
class ProcessingJobData:
    """后台处理作业（J001）。"""

    id: int
    workspace_id: int
    material_id: int
    version_id: int
    stage: str
    status: str
    scope: str | None
    error_reason: str | None
    attempts: int
    created_at: datetime
    updated_at: datetime


class JobRepository(Protocol):
    def create(
        self, workspace_id: int, material_id: int, version_id: int, stage: str
    ) -> ProcessingJobData: ...

    def get(self, job_id: int) -> ProcessingJobData | None: ...

    def record_attempt(
        self,
        job_id: int,
        *,
        status: str,
        scope: str | None,
        error_reason: str | None,
    ) -> ProcessingJobData | None: ...


class SemesterRepository(Protocol):
    def list(self, workspace_id: int, *, include_archived: bool = False) -> list[SemesterData]: ...

    def get(self, semester_id: int) -> SemesterData | None: ...

    def create(
        self, workspace_id: int, name: str, start_date: date, end_date: date
    ) -> SemesterData: ...

    def set_archived(self, semester_id: int, archived: bool) -> SemesterData | None: ...


class WorkspaceRepository(Protocol):
    def get(self, workspace_id: int) -> WorkspaceData | None: ...

    def get_or_create_default(self, name: str, timezone: str) -> WorkspaceData:
        """返回默认空间，不存在时创建；并发调用也必须最多创建一个。"""
        ...

    def update_settings(
        self,
        workspace_id: int,
        *,
        timezone: str,
        daily_capacity_minutes: int,
        break_minutes: int,
        buffer_minutes: int,
    ) -> WorkspaceData | None: ...


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
        remaining_minutes: int | None,
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
        remaining_minutes: int | None,
    ) -> TaskData | None: ...

    def set_progress(
        self, task_id: int, progress: TaskProgress, reason: str | None
    ) -> TaskData | None: ...

    def list_by_source_material(self, material_id: int) -> list[TaskData]: ...

    def unlink_source_material(self, material_id: int) -> int:
        """解除资料与任务的来源关联（删除资料时保留任务）。返回解除数量。"""
        ...

    def delete(self, task_id: int) -> bool: ...

    def list_progress_changes(self, task_id: int) -> list[TaskProgressChangeData]: ...


class MaterialRepository(Protocol):
    def get(self, material_id: int) -> MaterialData | None: ...

    def list(
        self,
        workspace_id: int,
        *,
        semester_id: int | None = None,
        course_id: int | None = None,
        source_type: MaterialSourceType | None = None,
        status: MaterialStatus | None = None,
        include_archived: bool = False,
    ) -> list[MaterialData]: ...

    def create(
        self,
        workspace_id: int,
        *,
        semester_id: int | None,
        course_id: int | None,
        class_name: str | None,
        title: str,
        publisher: str | None,
        published_at: datetime | None,
        source_type: MaterialSourceType,
        source_url: str | None,
    ) -> MaterialData: ...

    def update_metadata(
        self,
        material_id: int,
        *,
        title: str,
        publisher: str | None,
        published_at: datetime | None,
        source_url: str | None,
        semester_id: int | None,
        course_id: int | None,
        class_name: str | None,
    ) -> MaterialData | None: ...

    def list_version_numbers(self, material_id: int) -> list[int]: ...

    def create_version(
        self,
        material_id: int,
        version_no: int,
        note: str | None,
        storage_key: str | None,
        checksum: str | None = None,
    ) -> MaterialVersionData: ...

    def find_material_by_checksum(self, workspace_id: int, checksum: str) -> MaterialData | None:
        """按文件指纹在当前空间查找已有资料（重复导入检测，D006）。"""
        ...

    def set_archived(self, material_id: int, archived: bool) -> MaterialData | None: ...

    def set_status(self, material_id: int, status: MaterialStatus) -> MaterialData | None: ...

    def delete(self, material_id: int) -> bool: ...

    def list_versions(self, material_id: int) -> list[MaterialVersionData]: ...

    def add_chunks(self, chunks: list[NewSourceChunk]) -> list[SourceChunkData]:
        """保存片段。（version_id, seq）唯一约束保证重试幂等：
        重复提交不产生重复片段，返回已有行。"""
        ...

    def list_chunks(self, version_id: int) -> list[SourceChunkData]: ...


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

    workspaces: WorkspaceRepository
    semesters: SemesterRepository
    courses: CourseRepository
    tasks: TaskRepository
    schedule: ScheduleRepository
    materials: MaterialRepository
    jobs: JobRepository
    uow: UnitOfWork
