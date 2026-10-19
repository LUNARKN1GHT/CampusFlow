"""数据库模型（仅基础设施层使用，不直接返回给前端）。

表结构变更流程：修改本文件后执行
    uv run alembic revision --autogenerate -m "说明"
    uv run alembic upgrade head
并检查生成的迁移文件内容。
"""

from datetime import date, datetime, time

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Time,
    UniqueConstraint,
    func,
    text,
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


class IndexedBlock(Base):
    """Q004 逐块进度与成功向量；同一配置/块只有一行。"""

    __tablename__ = "indexed_blocks"
    __table_args__ = (
        CheckConstraint("start_offset >= 0 AND end_offset > start_offset"),
        CheckConstraint("state IN ('pending', 'running', 'done', 'failed', 'skipped')"),
        CheckConstraint("(state = 'done') = (embedding IS NOT NULL)"),
        CheckConstraint("NOT retryable OR state = 'failed'"),
        CheckConstraint(
            "(state = 'running' AND claim_token IS NOT NULL AND lease_until IS NOT NULL) OR "
            "(state <> 'running' AND claim_token IS NULL AND lease_until IS NULL)",
            name="ck_indexed_blocks_claim",
        ),
        CheckConstraint("attempts >= 0", name="ck_indexed_blocks_attempts"),
        # 检索加速（Q004）：按余弦距离的 HNSW 索引，语义搜索不必全表扫描。
        Index(
            "ix_indexed_blocks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": 16, "ef_construction": 64},
        ),
    )
    profile_id: Mapped[str] = mapped_column(ForeignKey("embedding_profiles.id"), primary_key=True)
    block_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("material_versions.id", ondelete="CASCADE"), index=True
    )
    source_chunk_id: Mapped[int] = mapped_column(
        ForeignKey("source_chunks.id", ondelete="CASCADE"), index=True
    )
    source_fingerprint: Mapped[str] = mapped_column(String(64))
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    chunker_version: Mapped[str] = mapped_column(String(100))
    text: Mapped[str] = mapped_column(String)
    state: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    retryable: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    failure_code: Mapped[str | None] = mapped_column(String(40))
    # 领取次数（含第一次）：达到上限后不再调用模型，避免崩溃或临时失败无限重试。
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    embedding: Mapped[list[float] | None] = mapped_column(VECTOR(1024))
    claim_token: Mapped[str | None] = mapped_column(String(32))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IndexManifest(Base):
    """完整来源/块清单快照；身份包含配置、来源、分块设置和解析覆盖。"""

    __tablename__ = "index_manifests"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("material_versions.id", ondelete="CASCADE"), index=True
    )
    profile_id: Mapped[str] = mapped_column(ForeignKey("embedding_profiles.id"))
    snapshot: Mapped[dict] = mapped_column(JSONB)
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
    # 包含关系（A001）：子任务指向父任务，与 task_dependencies 的先后依赖分开保存。
    # 父任务被删除时保留子任务本身，由应用层阻止静默孤立。
    parent_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), index=True
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


class TaskDependency(Base):
    """A001：任务之间的必要先后关系。

    只表达"先完成 depends_on_task_id 才能开始 task_id"，不表示任务包含关系；
    子任务归属由 tasks.parent_task_id 单独保存，两者不混用。
    """

    __tablename__ = "task_dependencies"
    __table_args__ = (
        UniqueConstraint("task_id", "depends_on_task_id", name="uq_task_dependencies_pair"),
        CheckConstraint("task_id <> depends_on_task_id", name="ck_task_dependencies_not_self"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    depends_on_task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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


class CurriculumPlan(Base):
    """C002：培养方案版本。专业、年级与版本共同确定适用规则，不同版本不混用。"""

    __tablename__ = "curriculum_plans"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "major", "grade_year", "version_label", name="uq_curriculum_plan"
        ),
        CheckConstraint("grade_year BETWEEN 1900 AND 2100", name="ck_curriculum_plan_grade_year"),
        CheckConstraint(
            "credit_limit_per_term IS NULL OR credit_limit_per_term > 0",
            name="ck_curriculum_plan_credit_limit",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    major: Mapped[str] = mapped_column(String(100))
    grade_year: Mapped[int] = mapped_column(Integer)
    version_label: Mapped[str] = mapped_column(String(100))
    # 已知规则：每学期学分上限；未知保持 NULL，排学期时不补造限制。
    credit_limit_per_term: Mapped[int | None] = mapped_column(Integer)
    source_material_id: Mapped[int | None] = mapped_column(
        ForeignKey("materials.id", ondelete="SET NULL"), index=True
    )
    source_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("material_versions.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LearnerProfile(Base):
    """C002：个人专业年级与当前适用方案。字段缺失时接口明确提示待补充。"""

    __tablename__ = "learner_profiles"
    __table_args__ = (
        CheckConstraint(
            "grade_year IS NULL OR grade_year BETWEEN 1900 AND 2100",
            name="ck_learner_profile_grade_year",
        ),
    )

    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    major: Mapped[str | None] = mapped_column(String(100))
    grade_year: Mapped[int | None] = mapped_column(Integer)
    curriculum_plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("curriculum_plans.id", ondelete="SET NULL"), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CurriculumCourse(Base):
    """培养方案内课程。学分或开课学期未知保持 NULL/空列表，不补造。"""

    __tablename__ = "curriculum_courses"
    __table_args__ = (
        UniqueConstraint("curriculum_plan_id", "name", name="uq_curriculum_course_name"),
        CheckConstraint(
            "credits IS NULL OR credits >= 0", name="ck_curriculum_course_credits"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    curriculum_plan_id: Mapped[int] = mapped_column(
        ForeignKey("curriculum_plans.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str | None] = mapped_column(String(50))
    credits: Mapped[float | None] = mapped_column(Float)
    # 已知开课季节，如 ["fall", "spring"]；空列表表示未知，不当作每学期都开。
    offered_terms: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    source_candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("extracted_items.id", ondelete="SET NULL"), index=True, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CourseRelation(Base):
    """C004：三类课程关系分开保存。只有 prerequisite 参与拓扑排序。"""

    __tablename__ = "course_relations"
    __table_args__ = (
        UniqueConstraint(
            "curriculum_plan_id",
            "from_course_id",
            "to_course_id",
            "relation_type",
            name="uq_course_relation",
        ),
        CheckConstraint(
            "relation_type IN ('prerequisite', 'corequisite', 'recommended')",
            name="ck_course_relation_type",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    curriculum_plan_id: Mapped[int] = mapped_column(
        ForeignKey("curriculum_plans.id", ondelete="CASCADE"), index=True
    )
    # from_course_id 为先修/并修/建议先学的来源课程，to_course_id 为受限课程。
    # 允许自环，由 C010 在分析时显式报告，而不是在写入时静默丢弃。
    from_course_id: Mapped[int] = mapped_column(
        ForeignKey("curriculum_courses.id", ondelete="CASCADE"), index=True
    )
    to_course_id: Mapped[int] = mapped_column(
        ForeignKey("curriculum_courses.id", ondelete="CASCADE"), index=True
    )
    relation_type: Mapped[str] = mapped_column(String(20))
    condition_text: Mapped[str | None] = mapped_column(String(1000))
    source_candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("extracted_items.id", ondelete="SET NULL"), index=True, unique=True
    )
    created_by: Mapped[str] = mapped_column(String(100), default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CourseAlias(Base):
    """C006：课程简称。只用于名称匹配，不合并课程或教学班。"""

    __tablename__ = "course_aliases"
    __table_args__ = (
        UniqueConstraint(
            "curriculum_course_id",
            "alias",
            "class_name",
            "semester_id",
            name="uq_course_alias",
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    curriculum_plan_id: Mapped[int] = mapped_column(
        ForeignKey("curriculum_plans.id", ondelete="CASCADE"), index=True
    )
    curriculum_course_id: Mapped[int] = mapped_column(
        ForeignKey("curriculum_courses.id", ondelete="CASCADE"), index=True
    )
    alias: Mapped[str] = mapped_column(String(200))
    class_name: Mapped[str | None] = mapped_column(String(100))
    semester_id: Mapped[int | None] = mapped_column(
        ForeignKey("semesters.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CourseStudyRecord(Base):
    """C007：个人修读记录。

    状态未知由“没有记录”表达，不落库成猜测值；成绩与已得学分只保存
    用户显式填写的值，不按课程学分或其它记录推算。同一课程可在不同学期
    重复修读，但同一课程、学期与状态只允许一条记录。
    """

    __tablename__ = "course_study_records"
    __table_args__ = (
        UniqueConstraint(
            "curriculum_course_id",
            "semester_id",
            "status",
            name="uq_course_study_record",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "status IN ('completed', 'in_progress', 'planned')",
            name="ck_course_study_record_status",
        ),
        CheckConstraint(
            "credits_earned IS NULL OR credits_earned >= 0",
            name="ck_course_study_record_credits",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    curriculum_course_id: Mapped[int] = mapped_column(
        ForeignKey("curriculum_courses.id", ondelete="CASCADE"), index=True
    )
    # 学期可未知；删除学期只清空引用，不删除修读记录。
    semester_id: Mapped[int | None] = mapped_column(
        ForeignKey("semesters.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(String(20))
    grade: Mapped[str | None] = mapped_column(String(100))
    credits_earned: Mapped[float | None] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(String(500))
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


class MaterialVersionRelation(Base):
    """用户声明的版本关系；原文件、片段及正式事项保持原位。"""

    __tablename__ = "material_version_relations"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "source_version_id",
            "target_version_id",
            "kind",
            name="uq_material_version_relation",
        ),
        CheckConstraint("source_version_id <> target_version_id"),
        CheckConstraint("kind IN ('same_source', 'replacement_claim')"),
        CheckConstraint("kind <> 'same_source' OR source_version_id < target_version_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    source_version_id: Mapped[int] = mapped_column(
        ForeignKey("material_versions.id", ondelete="CASCADE"), index=True
    )
    target_version_id: Mapped[int] = mapped_column(
        ForeignKey("material_versions.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(30))
    statement: Mapped[str] = mapped_column(String(1000))
    actor: Mapped[str] = mapped_column(String(200))
    source_snapshot: Mapped[dict] = mapped_column(JSONB)
    target_snapshot: Mapped[dict] = mapped_column(JSONB)
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
    __table_args__ = (
        # 一份资料版本最多只有一个向量索引作业（Q004）：重复的解析结果只会重置它，
        # 不会产生第二个执行者。
        Index(
            "uq_processing_jobs_embed_version",
            "version_id",
            unique=True,
            postgresql_where=text("stage = 'embed'"),
        ),
    )

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
    # 心跳（Q004）：索引作业按块推进，长作业借此避免被超时回收误判
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 当前索引作业使用的完整清单；为空表示尚未生成或已重置
    manifest_id: Mapped[str | None] = mapped_column(
        ForeignKey("index_manifests.id", ondelete="SET NULL")
    )
    # 自动重投次数（Q004）：与人工重试分开计数，防止崩溃循环无限重投
    auto_retry_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
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
        CheckConstraint(
            "kind IN ('task', 'event', 'rule', 'curriculum_course', 'course_relation')",
            name="ck_candidate_kind",
        ),
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
    # C003：培养方案课程/关系候选的适用方案，避免跨版本混用。
    curriculum_plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("curriculum_plans.id", ondelete="CASCADE"), index=True
    )
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


class QaConversation(Base):
    """Q014：有范围的问答会话；范围以工作空间为边界。"""

    __tablename__ = "qa_conversations"
    __table_args__ = (
        CheckConstraint("mode IN ('keyword', 'vector', 'hybrid')", name="ck_qa_conversation_mode"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    semester_id: Mapped[int | None] = mapped_column(ForeignKey("semesters.id"))
    course_id: Mapped[int | None] = mapped_column(ForeignKey("courses.id"))
    class_name: Mapped[str | None] = mapped_column(String(100))
    mode: Mapped[str] = mapped_column(String(20), default="hybrid", server_default="hybrid")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PlanDraft(Base):
    """P001：计划草案。输入快照与假设随草案保存，接受状态与版本独立维护。

    时间块与任务原始 DDL 分表保存：调整或移动计划块不会改写任务截止。
    version 用于并发校验，每次生成、接受、移动或锁定都递增。
    """

    __tablename__ = "plan_drafts"
    __table_args__ = (
        CheckConstraint("range_end >= range_start", name="ck_plan_drafts_range"),
        CheckConstraint("version >= 1", name="ck_plan_drafts_version"),
        CheckConstraint(
            "status IN ('draft', 'accepted', 'superseded')", name="ck_plan_drafts_status"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    semester_id: Mapped[int | None] = mapped_column(ForeignKey("semesters.id"), index=True)
    range_start: Mapped[date] = mapped_column(Date)
    range_end: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="draft", server_default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    input_snapshot: Mapped[dict] = mapped_column(JSONB)
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    assumptions: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    gaps: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    unscheduled: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    conflicts: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    coverage: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlanBlock(Base):
    """P001：草案中的建议时间块。task_id 只关联任务，不复制或改写 DDL。"""

    __tablename__ = "plan_blocks"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ck_plan_blocks_interval"),
        CheckConstraint("minutes > 0", name="ck_plan_blocks_minutes"),
        CheckConstraint("kind IN ('task', 'buffer')", name="ck_plan_blocks_kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    draft_id: Mapped[int] = mapped_column(
        ForeignKey("plan_drafts.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20), default="task", server_default="task")
    title: Mapped[str] = mapped_column(String(300))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    minutes: Mapped[int] = mapped_column(Integer)
    seq: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    reason: Mapped[str] = mapped_column(String(1000))
    constraint_source: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlanVersion(Base):
    """P001：草案每次实质变化的快照，保证同一草案的版本可追溯。"""

    __tablename__ = "plan_versions"
    __table_args__ = (UniqueConstraint("draft_id", "version_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    draft_id: Mapped[int] = mapped_column(
        ForeignKey("plan_drafts.id", ondelete="CASCADE"), index=True
    )
    version_no: Mapped[int] = mapped_column(Integer)
    change: Mapped[str] = mapped_column(String(30))
    snapshot: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QaTurn(Base):
    """Q014/Q017：一轮问答的范围、引用与覆盖快照；引用在读取时重新复核。"""

    __tablename__ = "qa_turns"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("qa_conversations.id", ondelete="CASCADE"), index=True
    )
    question: Mapped[str] = mapped_column(String(2000))
    answer: Mapped[str] = mapped_column(String)
    insufficient: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    intent: Mapped[str] = mapped_column(String(20), default="query", server_default="query")
    scope: Mapped[dict] = mapped_column(JSONB)
    citations: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    coverage: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    comparisons: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    conflicts: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    uncertainties: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VersionChangeAcceptance(Base):
    """N014：用户接受的具体字段差异；追加保存，旧候选在事务内标记失效。"""

    __tablename__ = "version_change_acceptances"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "baseline_candidate_id",
            "comparison_candidate_id",
            "accepted_fields",
            name="uq_version_change_acceptance",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    baseline_candidate_id: Mapped[int] = mapped_column(
        ForeignKey("extracted_items.id", ondelete="CASCADE"), index=True
    )
    comparison_candidate_id: Mapped[int] = mapped_column(
        ForeignKey("extracted_items.id", ondelete="CASCADE"), index=True
    )
    baseline_version_id: Mapped[int] = mapped_column(
        ForeignKey("material_versions.id", ondelete="CASCADE")
    )
    comparison_version_id: Mapped[int] = mapped_column(
        ForeignKey("material_versions.id", ondelete="CASCADE")
    )
    accepted_fields: Mapped[list] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(String(1000))
    operator: Mapped[str] = mapped_column(String(100))
    evidence_snapshot: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    previous_review_status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CandidateMerge(Base):
    """N016：候选合并记录；保留来源、进度与正式事项快照，重试幂等。"""

    __tablename__ = "candidate_merges"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "target_candidate_id",
            "source_candidate_id",
            name="uq_candidate_merge",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    target_candidate_id: Mapped[int] = mapped_column(
        ForeignKey("extracted_items.id", ondelete="CASCADE"), index=True
    )
    source_candidate_id: Mapped[int] = mapped_column(
        ForeignKey("extracted_items.id", ondelete="CASCADE"), index=True
    )
    target_formal_kind: Mapped[str | None] = mapped_column(String(20))
    target_formal_id: Mapped[int | None] = mapped_column(Integer)
    source_formal_id: Mapped[int | None] = mapped_column(Integer)
    preserved_progress: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    source_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    reason: Mapped[str] = mapped_column(String(1000))
    operator: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReminderRule(Base):
    """A010：提醒提前量、重复、暂停与关联任务。

    规则和触发记录分表保存；task_id 为空表示工作空间默认规则。
    时间一律按工作空间时区解释，规则本身不保存时区副本。
    """

    __tablename__ = "reminder_rules"
    __table_args__ = (
        CheckConstraint("advance_minutes >= 0", name="ck_reminder_rules_advance"),
        CheckConstraint(
            "repeat_minutes IS NULL OR repeat_minutes > 0", name="ck_reminder_rules_repeat"
        ),
        CheckConstraint("version >= 1", name="ck_reminder_rules_version"),
        Index(
            "uq_reminder_rules_task",
            "workspace_id",
            "task_id",
            unique=True,
            postgresql_where=text("task_id IS NOT NULL"),
        ),
        Index(
            "uq_reminder_rules_default",
            "workspace_id",
            unique=True,
            postgresql_where=text("task_id IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    advance_minutes: Mapped[int] = mapped_column(Integer, default=1440, server_default="1440")
    repeat_minutes: Mapped[int | None] = mapped_column(Integer)
    paused: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ReminderTrigger(Base):
    """A010/A012：一次实际提醒。dedupe_key 由事项、规则版本和触发窗口生成。"""

    __tablename__ = "reminder_triggers"
    __table_args__ = (
        CheckConstraint("kind IN ('deadline', 'review')", name="ck_reminder_triggers_kind"),
        CheckConstraint(
            "status IN ('pending', 'sent', 'dismissed', 'cancelled')",
            name="ck_reminder_triggers_status",
        ),
        UniqueConstraint("dedupe_key", name="uq_reminder_triggers_dedupe"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("reminder_rules.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20))
    dedupe_key: Mapped[str] = mapped_column(String(200))
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    message: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
