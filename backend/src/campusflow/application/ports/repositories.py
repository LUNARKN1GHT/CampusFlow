"""应用层端口：仓储接口与数据传输对象。

应用层只依赖本文件定义的接口；具体实现位于 infrastructure/repositories.py。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Protocol

from campusflow.application.ports.candidates import CandidateRepository
from campusflow.domain.states import (
    DueSourceType,
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
    weekly_capacity_minutes: int = 1680


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
    created_at: datetime
    updated_at: datetime
    # R004：由候选核对转换而来的任务保留候选关联，用于幂等与来源详情
    source_candidate_id: int | None = None
    # 读取时按工作空间时区派生，不落库
    overdue: bool = False

    @property
    def source_state(self) -> str:
        """来源状态（D009）：还有来源 / 来源已失效 / 本来就没有来源。

        由领域规则判定，接口与前端只读这一个字段，不再各自推断。
        """
        from campusflow.domain.materials import source_state

        return source_state(self.source_material_id, self.source_invalidated_at)


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
    # R005：由核对转换而来时指向来源候选，唯一约束保证一个候选只建一个日程
    source_candidate_id: int | None = None
    # R007：来源失效留痕（对齐任务的 D009）
    source_invalidated_at: datetime | None = None
    source_invalidated_note: str | None = None

    @property
    def source_state(self) -> str:
        """来源状态（R007）：还有来源候选 / 来源已失效 / 本来就没有来源。"""
        from campusflow.domain.materials import source_state

        return source_state(self.source_candidate_id, self.source_invalidated_at)


@dataclass
class RuleData:
    """R006：由已确认规则候选转换而来的课程规则。"""

    id: int
    workspace_id: int
    course_id: int | None
    semester_id: int | None
    title: str
    rule_text: str
    conditions_text: str | None
    scope_text: str | None
    effective_text: str | None
    source_material_id: int | None
    source_candidate_id: int | None
    source_invalidated_at: datetime | None
    source_invalidated_note: str | None
    created_at: datetime
    updated_at: datetime

    @property
    def source_state(self) -> str:
        """来源状态（R006，对齐任务 D009）：还有来源 / 来源已失效 / 本来就没有来源。"""
        from campusflow.domain.materials import source_state

        return source_state(self.source_material_id, self.source_invalidated_at)


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
    # 版本元数据：网页快照存文本字段，解析覆盖范围（X008）存结构化对象。
    source_metadata: dict[str, object] = field(default_factory=dict)


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
    table_group: str | None = None


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
    table_group: str | None


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
    enqueued_at: datetime | None
    delivery_attempts: int
    started_at: datetime | None
    retryable: bool | None
    created_at: datetime
    updated_at: datetime
    # Q004：索引作业的清单与心跳；其它阶段保持默认值。
    heartbeat_at: datetime | None = None
    manifest_id: str | None = None
    auto_retry_count: int = 0


@dataclass
class ProcessingJobAttemptData:
    """作业尝试历史（J005）：一条记录就是一次结束了的尝试。"""

    id: int
    job_id: int
    attempt_no: int
    stage: str
    status: str
    scope: str | None
    error_reason: str | None
    retryable: bool
    created_at: datetime


class JobRepository(Protocol):
    def create(
        self, workspace_id: int, material_id: int, version_id: int, stage: str
    ) -> ProcessingJobData: ...

    def get(self, job_id: int) -> ProcessingJobData | None: ...

    def claim(self, job_id: int) -> bool:
        """幂等领取（J004）：仅当作业仍为 pending 时原子置为 running。

        重复消息里只有一条能领取成功，其余返回 False；未提交（不存在）或已结束
        的作业同样领取失败，因此不会重复解析、重复生成片段。领取时记录开始时间，
        供超时回收识别（J005）。
        """
        ...

    def record_attempt(
        self,
        job_id: int,
        *,
        status: str,
        scope: str | None,
        error_reason: str | None,
        retryable: bool | None = None,
    ) -> ProcessingJobData | None:
        """记录一次尝试结果（J001、J005）。

        只有终态（完成/部分成功/失败）才追加一条尝试历史，保留成功阶段与失败
        原因；running 之类的中间状态只更新当前状态。retryable 表示本次失败是否
        值得重试，仅在失败时写入作业。
        """
        ...

    def list_attempts(self, job_id: int) -> list[ProcessingJobAttemptData]:
        """按尝试顺序列出终态历史（J005），供状态查询与诊断。"""
        ...

    def list_stale_running(
        self,
        *,
        timeout_seconds: int,
        embed_timeout_seconds: int | None = None,
    ) -> list[ProcessingJobData]:
        """列出领取后超过阈值仍停在处理中的作业（J005 超时回收）。

        Q004 起按阶段用不同阈值：解析看 started_at，索引看最近心跳，
        避免按块推进的长作业被误判超时。
        """
        ...

    def set_manifest(self, job_id: int, manifest_id: str) -> ProcessingJobData | None:
        """登记索引作业当前使用的完整清单（Q004）。"""
        ...

    def heartbeat(self, job_id: int) -> ProcessingJobData | None:
        """刷新作业心跳（Q004）：索引作业按块推进时调用。"""
        ...

    def create_or_requeue(
        self, workspace_id: int, material_id: int, version_id: int, stage: str
    ) -> ProcessingJobData:
        """创建或重置同一版本的阶段作业（Q004 索引作业专用）。

        已存在且为终态时重置为待处理（保留已完成的块），已待处理或处理中时
        不改变状态，保证一份版本最多一个执行者。
        """
        ...

    def prepare_auto_retry(self, job_id: int, *, max_auto_retries: int) -> ProcessingJobData | None:
        """原子准备一次自动重投（Q004），达到上限或不满足条件时返回 None。"""
        ...

    def list_auto_retryable(
        self, *, stage: str, max_auto_retries: int, limit: int = 50
    ) -> list[ProcessingJobData]:
        """列出还能自动重投的失败/部分成功作业（Q004）。"""
        ...

    def list_by_material(self, material_id: int) -> list[ProcessingJobData]:
        """列出某份资料的全部处理作业（D009 删除影响预览：还有几个没跑完）。"""
        ...

    def prepare_retry(self, job_id: int) -> ProcessingJobData | None:
        """把可重试的失败/部分成功作业重置为待处理，供重新投递（J005）。

        只有标记为可重试的作业能被重置；永久失败返回 None，避免反复重投。
        重置后重新进入待投递状态（清空投递时间与开始时间），失败原因留在
        尝试历史里。
        """
        ...

    def record_delivery(self, job_id: int, *, delivered: bool) -> ProcessingJobData | None:
        """登记一次投递尝试（J003）。

        成功投递时写入 enqueued_at；失败只累加尝试次数，记录保持"待投递"。
        """
        ...

    def list_undelivered(
        self, *, limit: int = 100, workspace_id: int | None = None
    ) -> list[ProcessingJobData]:
        """列出尚未成功投递的作业（按创建顺序），供补偿扫描重投（J004）。"""
        ...


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
        weekly_capacity_minutes: int,
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
        semester_id: int | None = None,
        due_from: date | None = None,
        due_to: date | None = None,
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
        target_date: date | None,
        due_source: DueSourceType,
        due_note: str | None,
        priority: TaskPriority,
        estimated_minutes: int | None,
        remaining_minutes: int | None,
        *,
        source_material_id: int | None = None,
        source_candidate_id: int | None = None,
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
        target_date: date | None,
        due_source: DueSourceType,
        due_note: str | None,
        priority: TaskPriority,
        estimated_minutes: int | None,
        remaining_minutes: int | None,
    ) -> TaskData | None: ...

    def set_progress(
        self, task_id: int, progress: TaskProgress, reason: str | None
    ) -> TaskData | None: ...

    def list_by_source_material(self, material_id: int) -> list[TaskData]: ...

    def get_by_source_candidate(self, candidate_id: int) -> TaskData | None:
        """按来源候选查已转换的任务（R004 幂等）。"""
        ...

    def unlink_source_material(
        self, material_id: int, *, invalidated_at: datetime, note: str
    ) -> int:
        """解除资料与任务的来源关联并留下失效痕迹（D008/D009）。返回解除数量。

        只置空来源会让保留的任务与手工建的任务无从区分，所以同时写入
        失效时间与说明，界面据此提示"引用已不可用"。
        """
        ...

    def delete(self, task_id: int) -> bool: ...

    def list_progress_changes(self, task_id: int) -> list[TaskProgressChangeData]: ...


class MaterialRepository(Protocol):
    def get(self, material_id: int) -> MaterialData | None: ...

    def get_version(self, version_id: int) -> MaterialVersionData | None:
        """按版本编号取版本，供来源关联使用（R004）。"""
        ...

    def exists(self, material_id: int) -> bool:
        """资料是否仍然存在（J007）。

        直接查库、不读会话里已加载的对象：晚到的作业必须看到删除后的真实状态，
        而不是本事务早先读到的旧对象。
        """
        ...

    def version_numbers_by_id(self, material_id: int) -> dict[int, int]:
        """版本 id → 版本号（J007），同样直接查库。

        用于判断作业的目标版本是否已被更新的版本取代；空字典表示资料已无版本。
        """
        ...

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
        *,
        source_metadata: dict[str, object] | None = None,
    ) -> MaterialVersionData: ...

    def merge_version_metadata(self, version_id: int, values: dict[str, object]) -> None:
        """在版本元数据上合并若干键，保留其他键（X008 写入解析覆盖范围）。"""
        ...

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
    def list_fixed_events(
        self, workspace_id: int, *, semester_id: int | None = None
    ) -> list[FixedEventData]: ...

    def get_fixed_event(self, event_id: int) -> FixedEventData | None: ...

    def get_fixed_event_by_source_candidate(self, candidate_id: int) -> FixedEventData | None:
        """按来源候选查已转换的固定日程（R005 幂等）。"""
        ...

    def list_ids_by_source_candidates(self, candidate_ids: list[int]) -> list[int]:
        """列出由这些候选转换来的固定日程编号（R007）。"""
        ...

    def invalidate_source_candidates(
        self, candidate_ids: list[int], *, invalidated_at: datetime, note: str
    ) -> int:
        """来源候选被删除时解除日程关联并留下失效痕迹（R007）。返回解除数量。"""
        ...

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
        *,
        source_candidate_id: int | None = None,
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


class RuleRepository(Protocol):
    def create(
        self,
        workspace_id: int,
        course_id: int | None,
        semester_id: int | None,
        title: str,
        rule_text: str,
        conditions_text: str | None,
        scope_text: str | None,
        effective_text: str | None,
        *,
        source_material_id: int | None = None,
        source_candidate_id: int | None = None,
    ) -> RuleData: ...

    def get(self, rule_id: int) -> RuleData | None: ...

    def get_by_source_candidate(self, candidate_id: int) -> RuleData | None:
        """按来源候选查已转换的课程规则（R006 幂等）。"""
        ...

    def list(
        self,
        workspace_id: int,
        *,
        course_id: int | None = None,
        semester_id: int | None = None,
    ) -> list[RuleData]: ...

    def list_ids_by_source_candidates(self, candidate_ids: list[int]) -> list[int]:
        """列出由这些候选转换来的课程规则编号（R006 删除影响预览）。"""
        ...

    def invalidate_source_candidates(
        self, candidate_ids: list[int], *, invalidated_at: datetime, note: str
    ) -> int:
        """来源候选被删除时解除规则关联并留下失效痕迹（R006）。返回解除数量。"""
        ...


@dataclass
class PlanBlockData:
    """P001：建议时间块。minutes 与起止时间同时保存，便于校验分段总量。"""

    id: int
    draft_id: int
    task_id: int | None
    kind: str
    title: str
    starts_at: datetime
    ends_at: datetime
    minutes: int
    seq: int
    locked: bool
    reason: str
    constraint_source: list[dict]
    created_at: datetime


@dataclass
class PlanVersionData:
    id: int
    draft_id: int
    version_no: int
    change: str
    snapshot: dict
    created_at: datetime


@dataclass
class PlanDraftData:
    id: int
    workspace_id: int
    semester_id: int | None
    range_start: date
    range_end: date
    status: str
    version: int
    input_snapshot: dict
    input_fingerprint: str
    assumptions: list[dict]
    gaps: list[dict]
    unscheduled: list[dict]
    conflicts: list[dict]
    coverage: dict
    created_at: datetime
    updated_at: datetime
    accepted_at: datetime | None
    blocks: list[PlanBlockData] = field(default_factory=list)


class PlanRepository(Protocol):
    def create_draft(
        self,
        workspace_id: int,
        *,
        semester_id: int | None,
        range_start: date,
        range_end: date,
        input_snapshot: dict,
        input_fingerprint: str,
        assumptions: list[dict],
        gaps: list[dict],
        unscheduled: list[dict],
        conflicts: list[dict],
        coverage: dict,
    ) -> PlanDraftData: ...

    def get_draft(self, draft_id: int) -> PlanDraftData | None: ...

    def list_drafts(
        self, workspace_id: int, *, semester_id: int | None = None
    ) -> list[PlanDraftData]: ...

    def get_current(
        self, workspace_id: int, *, semester_id: int | None = None
    ) -> PlanDraftData | None:
        """当前已接受计划；同一空间／学期最多一份 accepted。"""
        ...

    def create_blocks(self, draft_id: int, blocks: list[dict]) -> list[PlanBlockData]: ...

    def list_blocks(self, draft_id: int) -> list[PlanBlockData]: ...

    def get_block(self, block_id: int) -> PlanBlockData | None: ...

    def update_block(
        self,
        block_id: int,
        *,
        starts_at: datetime,
        ends_at: datetime,
        minutes: int,
        locked: bool,
        reason: str,
    ) -> PlanBlockData | None: ...

    def bump_version(
        self,
        draft_id: int,
        *,
        expected_version: int,
        change: str,
        snapshot: dict,
        status: str | None = None,
        accepted_at: datetime | None = None,
    ) -> PlanDraftData | None:
        """原子校验版本并递增；版本不匹配返回 None（P013 并发保护）。"""
        ...

    def supersede_others(
        self, workspace_id: int, *, semester_id: int | None, keep_draft_id: int
    ) -> None: ...

    def add_version(
        self, draft_id: int, *, version_no: int, change: str, snapshot: dict
    ) -> PlanVersionData: ...

    def list_versions(self, draft_id: int) -> list[PlanVersionData]: ...


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
    candidates: CandidateRepository
    rules: RuleRepository
    plans: PlanRepository
    uow: UnitOfWork
