"""数据库模型（仅基础设施层使用，不直接返回给前端）。

表结构变更流程：修改本文件后执行
    uv run alembic revision --autogenerate -m "说明"
    uv run alembic upgrade head
并检查生成的迁移文件内容。
"""

from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
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
from sqlalchemy.orm import Mapped, mapped_column

from campusflow.domain.states import EventRecurrence, TaskPriority, TaskProgress
from campusflow.infrastructure.db.base import Base


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    daily_capacity_minutes: Mapped[int] = mapped_column(Integer, default=240)
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


class AvailabilitySlot(Base):
    __tablename__ = "availability_slots"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    day_of_week: Mapped[int] = mapped_column(Integer)  # 0=周一 … 6=周日
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)


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
