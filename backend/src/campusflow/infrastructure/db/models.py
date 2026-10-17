"""数据库模型（仅基础设施层使用，不直接返回给前端）。

表结构变更流程：修改本文件后执行
    uv run alembic revision --autogenerate -m "说明"
    uv run alembic upgrade head
并检查生成的迁移文件内容。
"""

from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from campusflow.domain.states import DueSourceType, EventRecurrence, TaskPriority, TaskProgress
from campusflow.infrastructure.db.base import Base


class EmbeddingProfileRecord(Base):
    __tablename__ = "embedding_profiles"
    __table_args__ = (CheckConstraint("dimensions > 0 AND dimensions <= 4096"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(100))
    revision: Mapped[str] = mapped_column(String(100))
    dimensions: Mapped[int] = mapped_column(Integer)
    generation_config: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    daily_capacity_minutes: Mapped[int] = mapped_column(Integer, default=240)
    weekly_capacity_minutes: Mapped[int] = mapped_column(Integer, default=1680)
    break_minutes: Mapped[int] = mapped_column(Integer, default=15)
    buffer_minutes: Mapped[int] = mapped_column(Integer, default=30)
    # 默认空间标记：数据库部分唯一索引保证并发初始化不会创建多个默认空间
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Semester(Base):
    __tablename__ = "semesters"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    semester_id: Mapped[int] = mapped_column(ForeignKey("semesters.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str | None] = mapped_column(String(50))
    teacher: Mapped[str | None] = mapped_column(String(100))
    class_name: Mapped[str | None] = mapped_column(String(100))


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    course_id: Mapped[int | None] = mapped_column(ForeignKey("courses.id"), index=True)
    # 来源资料（核对转任务时由 R 系列写入）；删除资料时可选择保留任务并置空
    source_material_id: Mapped[int | None] = mapped_column(
        ForeignKey("materials.id", ondelete="SET NULL"), index=True
    )
    # 来源失效留痕（D009）：保留任务时，光把 source_material_id 置空会让它
    # 与手工建的任务分不出来。这两列记住"曾经有来源、来源已被删除"。
    source_invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_invalidated_note: Mapped[str | None] = mapped_column(String(300))
    # R004：核对转换而来时指向来源候选，唯一约束保证一个候选只建一个任务；
    # 候选随资料删除后置空，来源失效由上面的两列标记。
    source_candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("extracted_items.id", ondelete="SET NULL"), index=True, unique=True
    )
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(String)
    # 截止时间：due_date 单独一列保持日期精度；due_time 为空表示"当天内完成"，
    # 不得把只有日期的 DDL 补成 00:00 或 23:59 的确定时刻。
    due_date: Mapped[date | None] = mapped_column(Date)
    due_time: Mapped[time | None] = mapped_column(Time)
    progress: Mapped[str] = mapped_column(String(20), default=TaskProgress.NOT_STARTED)
    priority: Mapped[str] = mapped_column(String(20), default=TaskPriority.MEDIUM)
    estimated_minutes: Mapped[int | None] = mapped_column(Integer)
    remaining_minutes: Mapped[int | None] = mapped_column(Integer)
    # 个人安排目标：与课程正式 DDL 相互独立，修改互不影响
    target_date: Mapped[date | None] = mapped_column(Date)
    # 正式截止的来源类型与说明；修改截止本身不自动清空既有说明
    due_source: Mapped[str] = mapped_column(String(20), default=DueSourceType.USER)
    due_note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TaskProgressChange(Base):
    __tablename__ = "task_progress_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    from_progress: Mapped[str] = mapped_column(String(20))
    to_progress: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(String(500))
    previous_remaining_minutes: Mapped[int | None] = mapped_column(Integer)
    new_remaining_minutes: Mapped[int | None] = mapped_column(Integer)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FixedEvent(Base):
    __tablename__ = "fixed_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    course_id: Mapped[int | None] = mapped_column(ForeignKey("courses.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    location: Mapped[str | None] = mapped_column(String(200))
    recurrence: Mapped[str] = mapped_column(String(20), default=EventRecurrence.NONE)
    repeat_until: Mapped[date | None] = mapped_column(Date)
    # R005：核对转换而来时指向来源候选，唯一约束保证一个候选只建一个日程；
    # 候选随资料删除后置空，不伪造来源。
    source_candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("extracted_items.id", ondelete="SET NULL"), index=True, unique=True
    )
    # R007：来源失效留痕（对齐任务的 D009）。候选删除后只靠置空无法与手工日程区分，
    # 因此记住"曾经有来源、来源已被删除"。
    source_invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_invalidated_note: Mapped[str | None] = mapped_column(String(300))


class AvailabilitySlot(Base):
    __tablename__ = "availability_slots"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    day_of_week: Mapped[int] = mapped_column(Integer)  # 0=周一 … 6=周日
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)


class CourseRule(Base):
    """R006：由已确认规则候选转换而来的课程规则。

    条件、适用范围与生效声明只保存原文文本，不解析日期、不换算相对写法；
    缺少年份或依据不足时保留待核对，不把猜测当成已确认约束。
    """

    __tablename__ = "course_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    course_id: Mapped[int | None] = mapped_column(ForeignKey("courses.id"), index=True)
    semester_id: Mapped[int | None] = mapped_column(ForeignKey("semesters.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    rule_text: Mapped[str] = mapped_column(String)
    conditions_text: Mapped[str | None] = mapped_column(String)
    scope_text: Mapped[str | None] = mapped_column(String)
    effective_text: Mapped[str | None] = mapped_column(String)
    # 来源资料（核对转规则时写入）；删除资料时置空并留下失效留痕
    source_material_id: Mapped[int | None] = mapped_column(
        ForeignKey("materials.id", ondelete="SET NULL"), index=True
    )
    # 核对转换而来时指向来源候选，唯一约束保证一个候选只建一条规则；
    # 候选随资料删除后置空，来源失效由下面的两列标记。
    source_candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("extracted_items.id", ondelete="SET NULL"), index=True, unique=True
    )
    # 来源失效留痕（R006，对齐任务的 D009）：记住"曾经有来源、来源已被删除"
    source_invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_invalidated_note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Material(Base):
    """学习资料（D001）。

    发布时间（published_at，可能未知）与导入时间（imported_at，系统生成）
    分开保存；未知来源字段保留 NULL，不伪造。学期/课程/教学班均可为空，
    表示学期公共资料或尚未归类。
    """

    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    semester_id: Mapped[int | None] = mapped_column(ForeignKey("semesters.id"), index=True)
    course_id: Mapped[int | None] = mapped_column(ForeignKey("courses.id"), index=True)
    class_name: Mapped[str | None] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(300))
    publisher: Mapped[str | None] = mapped_column(String(200))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_type: Mapped[str] = mapped_column(String(20))
    source_url: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class MaterialVersion(Base):
    """资料版本（D001）。版本与资料通过 (material_id, version_no) 稳定关联。"""

    __tablename__ = "material_versions"
    __table_args__ = (
        UniqueConstraint("material_id", "version_no", name="uq_material_versions_no"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int] = mapped_column(
        ForeignKey("materials.id", ondelete="CASCADE"), index=True
    )
    version_no: Mapped[int] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(String(500))
    # 原文件在私有存储中的键（D002 生成）；粘贴文本同样落成 .txt 文件
    storage_key: Mapped[str | None] = mapped_column(String(300))
    # 原文件指纹（SHA-256 十六进制），用于完全相同文件的重复导入检测（D006）
    checksum: Mapped[str | None] = mapped_column(String(64), index=True)
    source_metadata: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceChunk(Base):
    """来源片段：资料版本中的最小定位单元（D004/D017）。

    片段严格绑定版本（version_id + seq 唯一，重试幂等）；版本更新后
    旧片段仍指向旧版本文本，引用不会误指向新内容。
    定位坐标结构化保存：段落号、页码、区域 bbox（0-1 归一化）。
    """

    __tablename__ = "source_chunks"
    __table_args__ = (UniqueConstraint("version_id", "seq", name="uq_source_chunks_version_seq"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("material_versions.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)  # 片段在版本内的顺序，从 0 开始
    locator_type: Mapped[str] = mapped_column(String(20))  # paragraph / page / region
    locator_value: Mapped[str] = mapped_column(String(50))  # 展示用定位文本
    # 结构化定位坐标（D017）
    page: Mapped[int | None] = mapped_column(Integer)
    paragraph: Mapped[int | None] = mapped_column(Integer)
    bbox_x0: Mapped[float | None] = mapped_column(Float)
    bbox_y0: Mapped[float | None] = mapped_column(Float)
    bbox_x1: Mapped[float | None] = mapped_column(Float)
    bbox_y1: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    # 跨页表格分组键（D016）：同一表格各分部共享；非表格片段为 NULL
    table_group: Mapped[str | None] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProcessingJob(Base):
    """后台处理作业（J001）：阶段、覆盖范围、错误原因、尝试次数。"""

    __tablename__ = "processing_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    # 作业随资料/版本删除（J007）：资料删除后作业已无有效目标，
    # 保留悬挂的作业行既无意义，也会让"资料删除"这一步被外键卡住
    material_id: Mapped[int] = mapped_column(
        ForeignKey("materials.id", ondelete="CASCADE"), index=True
    )
    version_id: Mapped[int] = mapped_column(
        ForeignKey("material_versions.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str] = mapped_column(String(20))  # JobStage
    status: Mapped[str] = mapped_column(String(20), default="pending")  # JobStatus
    # 覆盖范围说明（如"第 1-3 页完成，第 4 页失败"），不含原文全文
    scope: Mapped[str | None] = mapped_column(String(500))
    error_reason: Mapped[str | None] = mapped_column(String(500))  # 经 sanitize 清洗
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    # Outbox（J003）：提交与投递分离的待投递记录。
    # enqueued_at 为空表示尚未成功投递，可由补偿扫描重投；投递成功后才写入时间。
    enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    delivery_attempts: Mapped[int] = mapped_column(Integer, default=0)
    # 领取时间（J005）：用于识别长期停在 running 的超时作业
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    # 最近一次失败是否可重试（J005）：区分临时故障与永久失败
    retryable: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProcessingJobAttempt(Base):
    """作业尝试历史（J005）：每次终态尝试各留一条，保留成功阶段与失败原因。"""

    __tablename__ = "processing_job_attempts"
    __table_args__ = (UniqueConstraint("job_id", "attempt_no", name="uq_job_attempts_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # 尝试历史随作业删除（J007）：作业都不在了，留痕也无从查询
    job_id: Mapped[int] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="CASCADE"), index=True
    )
    attempt_no: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(20))  # JobStage
    status: Mapped[str] = mapped_column(String(20))  # JobStatus（终态）
    scope: Mapped[str | None] = mapped_column(String(500))  # 覆盖范围，不含原文全文
    error_reason: Mapped[str | None] = mapped_column(String(500))  # 经 sanitize 清洗
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExtractedItem(Base):
    """X001：候选事项仅保存核对状态，不保存任务执行进度。"""

    __tablename__ = "extracted_items"
    __table_args__ = (
        CheckConstraint("kind IN ('task', 'event', 'rule')", name="ck_candidate_kind"),
        CheckConstraint(
            "review_status IN ('pending', 'confirmed', 'ignored', 'superseded')",
            name="ck_candidate_review_status",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    source_version_id: Mapped[int] = mapped_column(
        ForeignKey("material_versions.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20))
    review_status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending"
    )
    semester_id: Mapped[int | None] = mapped_column(ForeignKey("semesters.id"), index=True)
    course_id: Mapped[int | None] = mapped_column(ForeignKey("courses.id"), index=True)
    class_name: Mapped[str | None] = mapped_column(String(100))
    review_reasons: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExtractedField(Base):
    __tablename__ = "extracted_fields"
    __table_args__ = (
        UniqueConstraint("candidate_id", "name", name="uq_candidate_field_name"),
        CheckConstraint(
            "origin IN ('document', 'user', 'inferred', 'suggestion')", name="ck_field_origin"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("extracted_items.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(64))
    value: Mapped[object] = mapped_column(JSONB(none_as_null=False), nullable=False)
    origin: Mapped[str] = mapped_column(String(20))
    explanation: Mapped[str | None] = mapped_column(String(1000))
    missing_reason: Mapped[str | None] = mapped_column(String(500))


class CandidateFieldEvidence(Base):
    __tablename__ = "candidate_field_evidence"
    id: Mapped[int] = mapped_column(primary_key=True)
    field_id: Mapped[int] = mapped_column(
        ForeignKey("extracted_fields.id", ondelete="CASCADE"), index=True
    )
    source_chunk_id: Mapped[int] = mapped_column(
        ForeignKey("source_chunks.id", ondelete="CASCADE"), index=True
    )
    quote: Mapped[str] = mapped_column(String(6000))


class CandidateFieldRevision(Base):
    """R002：字段修正按追加保存，不改写原始提取与证据。"""

    __tablename__ = "candidate_field_revisions"
    __table_args__ = (
        CheckConstraint(
            "previous_origin IN ('document', 'user', 'inferred', 'suggestion')",
            name="ck_revision_previous_origin",
        ),
        CheckConstraint(
            "new_origin IN ('document', 'user', 'inferred', 'suggestion')",
            name="ck_revision_new_origin",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    field_id: Mapped[int] = mapped_column(
        ForeignKey("extracted_fields.id", ondelete="CASCADE"), index=True
    )
    previous_value: Mapped[object] = mapped_column(JSONB(none_as_null=False), nullable=False)
    new_value: Mapped[object] = mapped_column(JSONB(none_as_null=False), nullable=False)
    previous_origin: Mapped[str] = mapped_column(String(20))
    new_origin: Mapped[str] = mapped_column(String(20))
    operator: Mapped[str] = mapped_column(String(100))
    note: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CandidateReviewChange(Base):
    """R003：候选核对状态变更留痕，保存转换方向、操作者与原因。"""

    __tablename__ = "candidate_review_changes"
    __table_args__ = (
        CheckConstraint(
            "from_status IN ('pending', 'confirmed', 'ignored', 'superseded')",
            name="ck_review_change_from_status",
        ),
        CheckConstraint(
            "to_status IN ('pending', 'confirmed', 'ignored', 'superseded')",
            name="ck_review_change_to_status",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("extracted_items.id", ondelete="CASCADE"), index=True
    )
    from_status: Mapped[str] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    operator: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str | None] = mapped_column(String(500))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
