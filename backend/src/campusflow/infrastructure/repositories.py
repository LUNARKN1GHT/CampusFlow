"""SQLAlchemy 仓储实现。ORM 模型只在本层使用，向外返回端口 DTO。"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from campusflow.application.ports.repositories import (
    AvailabilitySlotData,
    CourseData,
    FixedEventData,
    MaterialData,
    MaterialVersionData,
    NewSourceChunk,
    PlanBlockData,
    PlanDraftData,
    PlanVersionData,
    ProcessingJobAttemptData,
    ProcessingJobData,
    ReminderRuleData,
    ReminderTriggerData,
    Repositories,
    RuleData,
    SemesterData,
    SourceChunkData,
    TaskData,
    TaskDependencyData,
    TaskProgressChangeData,
    WorkspaceData,
)
from campusflow.domain.errors import DomainError
from campusflow.domain.states import (
    DueSourceType,
    EventRecurrence,
    MaterialSourceType,
    MaterialStatus,
    TaskPriority,
    TaskProgress,
)
from campusflow.infrastructure.candidate_repository import SqlAlchemyCandidateRepository
from campusflow.infrastructure.db.models import (
    AvailabilitySlot,
    Course,
    CourseRule,
    FixedEvent,
    Material,
    MaterialVersion,
    MaterialVersionRelation,
    PlanBlock,
    PlanDraft,
    PlanVersion,
    ProcessingJob,
    ProcessingJobAttempt,
    ReminderRule,
    ReminderTrigger,
    Semester,
    SourceChunk,
    Task,
    TaskDependency,
    TaskProgressChange,
    Workspace,
)


def _row_missing(session: Session, model: type, row_id: int) -> bool:
    """这一行是否已不在数据库里（J007）。

    会话缓存里的对象不知道自己已被别的会话删除：直接修改它会打出 0 行的
    UPDATE，psycopg 能如实报告影响行数，SQLAlchemy 随即抛 StaleDataError。
    所以改写之前先用一次标量查询确认真相，确认真相之后再决定是否碰 ORM 对象——
    资料删除后晚到的作业，一行都不能写回去。
    """
    return session.scalar(select(model.id).where(model.id == row_id)) is None


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


def _workspace_to_data(row: Workspace) -> WorkspaceData:
    return WorkspaceData(
        id=row.id,
        name=row.name,
        timezone=row.timezone,
        daily_capacity_minutes=row.daily_capacity_minutes,
        weekly_capacity_minutes=row.weekly_capacity_minutes,
        break_minutes=row.break_minutes,
        buffer_minutes=row.buffer_minutes,
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
        source_material_id=row.source_material_id,
        source_invalidated_at=row.source_invalidated_at,
        source_invalidated_note=row.source_invalidated_note,
        title=row.title,
        description=row.description,
        due_date=row.due_date,
        due_time=row.due_time,
        target_date=row.target_date,
        due_source=DueSourceType(row.due_source),
        due_note=row.due_note,
        progress=TaskProgress(row.progress),
        priority=TaskPriority(row.priority),
        estimated_minutes=row.estimated_minutes,
        remaining_minutes=row.remaining_minutes,
        created_at=row.created_at,
        updated_at=row.updated_at,
        source_candidate_id=row.source_candidate_id,
        parent_task_id=row.parent_task_id,
    )


def _dependency_to_data(row: TaskDependency) -> TaskDependencyData:
    return TaskDependencyData(
        id=row.id,
        workspace_id=row.workspace_id,
        task_id=row.task_id,
        depends_on_task_id=row.depends_on_task_id,
        created_at=row.created_at,
    )


def _progress_change_to_data(row: TaskProgressChange) -> TaskProgressChangeData:
    return TaskProgressChangeData(
        id=row.id,
        task_id=row.task_id,
        from_progress=TaskProgress(row.from_progress),
        to_progress=TaskProgress(row.to_progress),
        reason=row.reason,
        previous_remaining_minutes=row.previous_remaining_minutes,
        new_remaining_minutes=row.new_remaining_minutes,
        changed_at=row.changed_at,
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
        source_candidate_id=row.source_candidate_id,
        source_invalidated_at=row.source_invalidated_at,
        source_invalidated_note=row.source_invalidated_note,
    )


def _slot_to_data(row: AvailabilitySlot) -> AvailabilitySlotData:
    return AvailabilitySlotData(
        id=row.id,
        workspace_id=row.workspace_id,
        day_of_week=row.day_of_week,
        start_time=row.start_time,
        end_time=row.end_time,
    )


def _rule_to_data(row: CourseRule) -> RuleData:
    return RuleData(
        id=row.id,
        workspace_id=row.workspace_id,
        course_id=row.course_id,
        semester_id=row.semester_id,
        title=row.title,
        rule_text=row.rule_text,
        conditions_text=row.conditions_text,
        scope_text=row.scope_text,
        effective_text=row.effective_text,
        source_material_id=row.source_material_id,
        source_candidate_id=row.source_candidate_id,
        source_invalidated_at=row.source_invalidated_at,
        source_invalidated_note=row.source_invalidated_note,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _material_to_data(row: Material) -> MaterialData:
    return MaterialData(
        id=row.id,
        workspace_id=row.workspace_id,
        semester_id=row.semester_id,
        course_id=row.course_id,
        class_name=row.class_name,
        title=row.title,
        publisher=row.publisher,
        published_at=row.published_at,
        source_type=MaterialSourceType(row.source_type),
        source_url=row.source_url,
        status=MaterialStatus(row.status),
        archived=row.archived,
        imported_at=row.imported_at,
    )


def _material_version_to_data(row: MaterialVersion) -> MaterialVersionData:
    return MaterialVersionData(
        id=row.id,
        material_id=row.material_id,
        version_no=row.version_no,
        note=row.note,
        storage_key=row.storage_key,
        checksum=row.checksum,
        created_at=row.created_at,
        source_metadata=dict(row.source_metadata),
    )


def _chunk_to_data(row: SourceChunk) -> SourceChunkData:
    bbox = None
    if row.bbox_x0 is not None:
        bbox = (row.bbox_x0, row.bbox_y0, row.bbox_x1, row.bbox_y1)
    return SourceChunkData(
        id=row.id,
        version_id=row.version_id,
        seq=row.seq,
        locator_type=row.locator_type,
        locator_value=row.locator_value,
        text=row.text,
        page=row.page,
        paragraph=row.paragraph,
        bbox=bbox,
        confidence=row.confidence,
        table_group=row.table_group,
    )


class SqlAlchemyMaterialRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, material_id: int) -> MaterialData | None:
        row = self._session.get(Material, material_id)
        return _material_to_data(row) if row is not None else None

    def lock_for_delete(self, workspace_id: int, material_id: int) -> bool:
        return (
            self._session.scalar(
                select(Material.id)
                .where(
                    Material.id == material_id,
                    Material.workspace_id == workspace_id,
                )
                .with_for_update()
            )
            is not None
        )

    def version_relation_ids(self, material_id: int) -> list[int]:
        versions = select(MaterialVersion.id).where(MaterialVersion.material_id == material_id)
        return list(
            self._session.scalars(
                select(MaterialVersionRelation.id)
                .where(
                    MaterialVersionRelation.source_version_id.in_(versions)
                    | MaterialVersionRelation.target_version_id.in_(versions)
                )
                .order_by(MaterialVersionRelation.id)
            )
        )

    def get_version(self, version_id: int) -> MaterialVersionData | None:
        row = self._session.get(MaterialVersion, version_id)
        return _material_version_to_data(row) if row is not None else None

    def exists(self, material_id: int) -> bool:
        """直接查库判断资料是否存在（J007）：绕过会话缓存，读到最新已提交状态。"""
        return not _row_missing(self._session, Material, material_id)

    def version_numbers_by_id(self, material_id: int) -> dict[int, int]:
        """直接查库取版本号（J007）：只取标量列，不受已加载实体影响。"""
        rows = self._session.execute(
            select(MaterialVersion.id, MaterialVersion.version_no).where(
                MaterialVersion.material_id == material_id
            )
        )
        return {row.id: row.version_no for row in rows}

    def list(
        self,
        workspace_id: int,
        *,
        semester_id: int | None = None,
        course_id: int | None = None,
        source_type: MaterialSourceType | None = None,
        status: MaterialStatus | None = None,
        include_archived: bool = False,
    ) -> list[MaterialData]:
        stmt = select(Material).where(Material.workspace_id == workspace_id)
        if semester_id is not None:
            stmt = stmt.where(Material.semester_id == semester_id)
        if course_id is not None:
            stmt = stmt.where(Material.course_id == course_id)
        if source_type is not None:
            stmt = stmt.where(Material.source_type == source_type)
        if status is not None:
            stmt = stmt.where(Material.status == status)
        if not include_archived:
            stmt = stmt.where(Material.archived.is_(False))
        stmt = stmt.order_by(Material.imported_at.desc())
        return [_material_to_data(row) for row in self._session.scalars(stmt)]

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
    ) -> MaterialData | None:
        row = self._session.get(Material, material_id)
        if row is None:
            return None
        row.title = title
        row.publisher = publisher
        row.published_at = published_at
        row.source_url = source_url
        row.semester_id = semester_id
        row.course_id = course_id
        row.class_name = class_name
        self._session.flush()
        return _material_to_data(row)

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
    ) -> MaterialData:
        row = Material(
            workspace_id=workspace_id,
            semester_id=semester_id,
            course_id=course_id,
            class_name=class_name,
            title=title,
            publisher=publisher,
            published_at=published_at,
            source_type=source_type,
            source_url=source_url,
        )
        self._session.add(row)
        self._session.flush()
        return _material_to_data(row)

    def list_version_numbers(self, material_id: int) -> list[int]:
        stmt = (
            select(MaterialVersion.version_no)
            .where(MaterialVersion.material_id == material_id)
            .order_by(MaterialVersion.version_no)
        )
        return list(self._session.scalars(stmt))

    def create_version(
        self,
        material_id: int,
        version_no: int,
        note: str | None,
        storage_key: str | None,
        checksum: str | None = None,
        *,
        source_metadata: dict[str, object] | None = None,
    ) -> MaterialVersionData:
        row = MaterialVersion(
            material_id=material_id,
            version_no=version_no,
            note=note,
            storage_key=storage_key,
            checksum=checksum,
            source_metadata=dict(source_metadata or {}),
        )
        self._session.add(row)
        self._session.flush()
        return _material_version_to_data(row)

    def merge_version_metadata(self, version_id: int, values: dict[str, object]) -> None:
        row = self._session.get(MaterialVersion, version_id)
        if row is None:
            return
        # JSONB 列必须整体赋值：原地修改不会被 SQLAlchemy 视为变更
        merged = dict(row.source_metadata or {})
        merged.update(values)
        row.source_metadata = merged
        self._session.flush()

    def set_archived(self, material_id: int, archived: bool) -> MaterialData | None:
        row = self._session.get(Material, material_id)
        if row is None:
            return None
        row.archived = archived
        self._session.flush()
        return _material_to_data(row)

    def set_status(self, material_id: int, status: MaterialStatus) -> MaterialData | None:
        # 资料可能已被删除（J007）：晚到的作业不能把它的状态写回来
        if _row_missing(self._session, Material, material_id):
            return None
        row = self._session.get(Material, material_id)
        if row is None:
            return None
        row.status = status
        self._session.flush()
        return _material_to_data(row)

    def delete(self, material_id: int) -> bool:
        row = self._session.get(Material, material_id)
        if row is None:
            return False
        self._session.delete(row)  # 版本与片段随 ondelete=CASCADE 一并删除
        self._session.flush()
        return True

    def find_material_by_checksum(self, workspace_id: int, checksum: str) -> MaterialData | None:
        stmt = (
            select(Material)
            .join(MaterialVersion, MaterialVersion.material_id == Material.id)
            .where(Material.workspace_id == workspace_id)
            .where(MaterialVersion.checksum == checksum)
            .order_by(MaterialVersion.version_no.desc())
            .limit(1)
        )
        row = self._session.scalars(stmt).first()
        return _material_to_data(row) if row is not None else None

    def list_versions(self, material_id: int) -> list[MaterialVersionData]:
        stmt = (
            select(MaterialVersion)
            .where(MaterialVersion.material_id == material_id)
            .order_by(MaterialVersion.version_no)
        )
        return [_material_version_to_data(row) for row in self._session.scalars(stmt)]

    def add_chunks(self, chunks: list[NewSourceChunk]) -> list[SourceChunkData]:
        """幂等写入：依赖 uq_source_chunks_version_seq 唯一约束 +
        ON CONFLICT DO NOTHING，重试重复提交不产生重复片段，
        冲突时返回已存在的行（D017 验收）。"""
        if not chunks:
            return []
        stmt = (
            pg_insert(SourceChunk)
            .values(
                [
                    {
                        "version_id": chunk.version_id,
                        "seq": chunk.seq,
                        "locator_type": chunk.locator_type,
                        "locator_value": chunk.locator_value,
                        "text": chunk.text,
                        "page": chunk.page,
                        "paragraph": chunk.paragraph,
                        "bbox_x0": chunk.bbox[0] if chunk.bbox else None,
                        "bbox_y0": chunk.bbox[1] if chunk.bbox else None,
                        "bbox_x1": chunk.bbox[2] if chunk.bbox else None,
                        "bbox_y1": chunk.bbox[3] if chunk.bbox else None,
                        "confidence": chunk.confidence,
                        "table_group": chunk.table_group,
                    }
                    for chunk in chunks
                ]
            )
            .on_conflict_do_nothing(constraint="uq_source_chunks_version_seq")
        )
        self._session.execute(stmt)
        self._session.flush()
        return self.list_chunks(chunks[0].version_id)

    def list_chunks(self, version_id: int) -> list[SourceChunkData]:
        stmt = (
            select(SourceChunk)
            .where(SourceChunk.version_id == version_id)
            .order_by(SourceChunk.seq)
        )
        return [_chunk_to_data(row) for row in self._session.scalars(stmt)]


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


class SqlAlchemyWorkspaceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, workspace_id: int) -> WorkspaceData | None:
        row = self._session.get(Workspace, workspace_id)
        return _workspace_to_data(row) if row is not None else None

    def get_or_create_default(self, name: str, timezone: str) -> WorkspaceData:
        row = self._session.scalar(select(Workspace).where(Workspace.is_default.is_(True)))
        if row is not None:
            return _workspace_to_data(row)
        # 并发初始化安全：依赖 uq_workspaces_default 部分唯一索引 + ON CONFLICT DO NOTHING，
        # 冲突时不插入，随后重新读取胜出者创建的那一行。
        stmt = (
            pg_insert(Workspace)
            .values(name=name, timezone=timezone, is_default=True)
            .on_conflict_do_nothing(
                index_elements=["is_default"],
                index_where=text("is_default"),
            )
        )
        self._session.execute(stmt)
        self._session.flush()
        row = self._session.scalar(select(Workspace).where(Workspace.is_default.is_(True)))
        if row is None:
            raise RuntimeError("默认工作空间创建失败")
        return _workspace_to_data(row)

    def update_settings(
        self,
        workspace_id: int,
        *,
        timezone: str,
        daily_capacity_minutes: int,
        break_minutes: int,
        buffer_minutes: int,
        weekly_capacity_minutes: int,
    ) -> WorkspaceData | None:
        row = self._session.get(Workspace, workspace_id)
        if row is None:
            return None
        row.timezone = timezone
        row.daily_capacity_minutes = daily_capacity_minutes
        row.weekly_capacity_minutes = weekly_capacity_minutes
        row.break_minutes = break_minutes
        row.buffer_minutes = buffer_minutes
        self._session.flush()
        return _workspace_to_data(row)


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
        semester_id: int | None = None,
        due_from: date | None = None,
        due_to: date | None = None,
    ) -> list[TaskData]:
        stmt = select(Task).where(Task.workspace_id == workspace_id)
        if semester_id is not None:
            # 按课程学期筛选：未关联课程的任务不属于任何学期，只在未筛选学期时返回
            stmt = stmt.join(Course, Task.course_id == Course.id).where(
                Course.workspace_id == workspace_id, Course.semester_id == semester_id
            )
        if course_id is not None:
            stmt = stmt.where(Task.course_id == course_id)
        if progress is not None:
            stmt = stmt.where(Task.progress == progress)
        if due_from is not None:
            stmt = stmt.where(Task.due_date >= due_from)
        if due_to is not None:
            stmt = stmt.where(Task.due_date <= due_to)
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
        target_date: date | None,
        due_source: DueSourceType,
        due_note: str | None,
        priority: TaskPriority,
        estimated_minutes: int | None,
        remaining_minutes: int | None,
        *,
        source_material_id: int | None = None,
        source_candidate_id: int | None = None,
        parent_task_id: int | None = None,
    ) -> TaskData:
        row = Task(
            workspace_id=workspace_id,
            course_id=course_id,
            parent_task_id=parent_task_id,
            source_material_id=source_material_id,
            source_candidate_id=source_candidate_id,
            title=title,
            description=description,
            due_date=due_date,
            due_time=due_time,
            target_date=target_date,
            due_source=due_source,
            due_note=due_note,
            priority=priority,
            estimated_minutes=estimated_minutes,
            remaining_minutes=remaining_minutes,
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
        target_date: date | None,
        due_source: DueSourceType,
        due_note: str | None,
        priority: TaskPriority,
        estimated_minutes: int | None,
        remaining_minutes: int | None,
    ) -> TaskData | None:
        row = self._session.get(Task, task_id)
        if row is None:
            return None
        row.course_id = course_id
        row.title = title
        row.description = description
        row.due_date = due_date
        row.due_time = due_time
        row.target_date = target_date
        row.due_source = due_source
        row.due_note = due_note
        row.priority = priority
        row.estimated_minutes = estimated_minutes
        row.remaining_minutes = remaining_minutes
        self._session.flush()
        return _task_to_data(row)

    def set_progress(
        self, task_id: int, progress: TaskProgress, reason: str | None
    ) -> TaskData | None:
        row = self._session.get(Task, task_id)
        if row is None:
            return None
        previous_progress = TaskProgress(row.progress)
        previous_remaining = row.remaining_minutes
        if progress in (TaskProgress.DONE, TaskProgress.CANCELLED):
            row.remaining_minutes = 0
        elif previous_progress in (TaskProgress.DONE, TaskProgress.CANCELLED):
            row.remaining_minutes = row.estimated_minutes
        row.progress = progress
        change = TaskProgressChange(
            task_id=row.id,
            from_progress=previous_progress,
            to_progress=progress,
            reason=reason,
            previous_remaining_minutes=previous_remaining,
            new_remaining_minutes=row.remaining_minutes,
        )
        self._session.add(change)
        self._session.flush()
        return _task_to_data(row)

    def list_progress_changes(self, task_id: int) -> list[TaskProgressChangeData]:
        stmt = (
            select(TaskProgressChange)
            .where(TaskProgressChange.task_id == task_id)
            .order_by(TaskProgressChange.changed_at.desc(), TaskProgressChange.id.desc())
        )
        return [_progress_change_to_data(row) for row in self._session.scalars(stmt)]

    def set_parent(self, task_id: int, parent_task_id: int | None) -> TaskData | None:
        row = self._session.get(Task, task_id)
        if row is None:
            return None
        row.parent_task_id = parent_task_id
        self._session.flush()
        return _task_to_data(row)

    def list_subtasks(self, parent_task_id: int) -> list[TaskData]:
        stmt = select(Task).where(Task.parent_task_id == parent_task_id).order_by(Task.id)
        return [_task_to_data(row) for row in self._session.scalars(stmt)]

    def list_dependencies(self, task_id: int) -> list[TaskDependencyData]:
        stmt = (
            select(TaskDependency)
            .where(TaskDependency.task_id == task_id)
            .order_by(TaskDependency.id)
        )
        return [_dependency_to_data(row) for row in self._session.scalars(stmt)]

    def list_dependents(self, depends_on_task_id: int) -> list[TaskDependencyData]:
        stmt = (
            select(TaskDependency)
            .where(TaskDependency.depends_on_task_id == depends_on_task_id)
            .order_by(TaskDependency.id)
        )
        return [_dependency_to_data(row) for row in self._session.scalars(stmt)]

    def list_workspace_dependencies(self, workspace_id: int) -> list[TaskDependencyData]:
        stmt = (
            select(TaskDependency)
            .where(TaskDependency.workspace_id == workspace_id)
            .order_by(TaskDependency.id)
        )
        return [_dependency_to_data(row) for row in self._session.scalars(stmt)]

    def add_dependency(
        self, workspace_id: int, task_id: int, depends_on_task_id: int
    ) -> TaskDependencyData:
        row = TaskDependency(
            workspace_id=workspace_id,
            task_id=task_id,
            depends_on_task_id=depends_on_task_id,
        )
        self._session.add(row)
        self._session.flush()
        return _dependency_to_data(row)

    def remove_dependency(self, task_id: int, depends_on_task_id: int) -> bool:
        row = self._session.scalar(
            select(TaskDependency).where(
                TaskDependency.task_id == task_id,
                TaskDependency.depends_on_task_id == depends_on_task_id,
            )
        )
        if row is None:
            return False
        self._session.delete(row)
        self._session.flush()
        return True

    def list_by_source_material(self, material_id: int) -> list[TaskData]:
        stmt = select(Task).where(Task.source_material_id == material_id).order_by(Task.id)
        return [_task_to_data(row) for row in self._session.scalars(stmt)]

    def get_by_source_candidate(self, candidate_id: int) -> TaskData | None:
        row = self._session.scalar(select(Task).where(Task.source_candidate_id == candidate_id))
        return _task_to_data(row) if row is not None else None

    def unlink_source_material(
        self, material_id: int, *, invalidated_at: datetime, note: str
    ) -> int:
        rows = list(
            self._session.scalars(select(Task).where(Task.source_material_id == material_id))
        )
        for row in rows:
            row.source_material_id = None
            # 留痕（D009）：任务留下"来源曾是这份资料、现在已被删除"的记录，
            # 否则它与手工建的任务在界面上完全一样
            row.source_invalidated_at = invalidated_at
            row.source_invalidated_note = note
        self._session.flush()
        return len(rows)

    def delete(self, task_id: int) -> bool:
        row = self._session.get(Task, task_id)
        if row is None:
            return False
        self._session.delete(row)
        self._session.flush()
        return True


class SqlAlchemyScheduleRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_fixed_events(
        self, workspace_id: int, *, semester_id: int | None = None
    ) -> list[FixedEventData]:
        stmt = (
            select(FixedEvent)
            .where(FixedEvent.workspace_id == workspace_id)
            .order_by(FixedEvent.starts_at)
        )
        if semester_id is not None:
            stmt = stmt.join(Course, FixedEvent.course_id == Course.id).where(
                Course.workspace_id == workspace_id, Course.semester_id == semester_id
            )
        return [_fixed_event_to_data(row) for row in self._session.scalars(stmt)]

    def get_fixed_event(self, event_id: int) -> FixedEventData | None:
        row = self._session.get(FixedEvent, event_id)
        return _fixed_event_to_data(row) if row is not None else None

    def get_fixed_event_by_source_candidate(self, candidate_id: int) -> FixedEventData | None:
        row = self._session.scalar(
            select(FixedEvent).where(FixedEvent.source_candidate_id == candidate_id)
        )
        return _fixed_event_to_data(row) if row is not None else None

    def list_ids_by_source_candidates(self, candidate_ids: list[int]) -> list[int]:
        if not candidate_ids:
            return []
        return list(
            self._session.scalars(
                select(FixedEvent.id)
                .where(FixedEvent.source_candidate_id.in_(candidate_ids))
                .order_by(FixedEvent.id)
            )
        )

    def invalidate_source_candidates(
        self, candidate_ids: list[int], *, invalidated_at: datetime, note: str
    ) -> int:
        if not candidate_ids:
            return 0
        rows = list(
            self._session.scalars(
                select(FixedEvent).where(FixedEvent.source_candidate_id.in_(candidate_ids))
            )
        )
        for row in rows:
            row.source_candidate_id = None
            # 留痕（R007/D009）：日程留下"来源曾是这份资料、现在已被删除"的记录，
            # 否则它与手工录入的日程在界面上完全一样
            row.source_invalidated_at = invalidated_at
            row.source_invalidated_note = note
        self._session.flush()
        return len(rows)

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
            source_candidate_id=source_candidate_id,
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


class SqlAlchemyRuleRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

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
    ) -> RuleData:
        row = CourseRule(
            workspace_id=workspace_id,
            course_id=course_id,
            semester_id=semester_id,
            title=title,
            rule_text=rule_text,
            conditions_text=conditions_text,
            scope_text=scope_text,
            effective_text=effective_text,
            source_material_id=source_material_id,
            source_candidate_id=source_candidate_id,
        )
        self._session.add(row)
        self._session.flush()
        return _rule_to_data(row)

    def get(self, rule_id: int) -> RuleData | None:
        row = self._session.get(CourseRule, rule_id)
        return _rule_to_data(row) if row is not None else None

    def get_by_source_candidate(self, candidate_id: int) -> RuleData | None:
        row = self._session.scalar(
            select(CourseRule).where(CourseRule.source_candidate_id == candidate_id)
        )
        return _rule_to_data(row) if row is not None else None

    def list(
        self,
        workspace_id: int,
        *,
        course_id: int | None = None,
        semester_id: int | None = None,
    ) -> list[RuleData]:
        stmt = select(CourseRule).where(CourseRule.workspace_id == workspace_id)
        if course_id is not None:
            stmt = stmt.where(CourseRule.course_id == course_id)
        if semester_id is not None:
            stmt = stmt.where(CourseRule.semester_id == semester_id)
        stmt = stmt.order_by(CourseRule.id)
        return [_rule_to_data(row) for row in self._session.scalars(stmt)]

    def list_ids_by_source_candidates(self, candidate_ids: list[int]) -> list[int]:
        if not candidate_ids:
            return []
        return list(
            self._session.scalars(
                select(CourseRule.id)
                .where(CourseRule.source_candidate_id.in_(candidate_ids))
                .order_by(CourseRule.id)
            )
        )

    def invalidate_source_candidates(
        self, candidate_ids: list[int], *, invalidated_at: datetime, note: str
    ) -> int:
        if not candidate_ids:
            return 0
        rows = list(
            self._session.scalars(
                select(CourseRule).where(CourseRule.source_candidate_id.in_(candidate_ids))
            )
        )
        for row in rows:
            row.source_material_id = None
            row.source_candidate_id = None
            # 留痕（R006/D009）：规则留下"来源曾是这份资料、现在已被删除"的记录，
            # 否则它与手工录入的规则在界面上完全一样
            row.source_invalidated_at = invalidated_at
            row.source_invalidated_note = note
        self._session.flush()
        return len(rows)


def _job_to_data(row: ProcessingJob) -> ProcessingJobData:
    return ProcessingJobData(
        id=row.id,
        workspace_id=row.workspace_id,
        material_id=row.material_id,
        version_id=row.version_id,
        stage=row.stage,
        status=row.status,
        scope=row.scope,
        error_reason=row.error_reason,
        attempts=row.attempts,
        enqueued_at=row.enqueued_at,
        delivery_attempts=row.delivery_attempts,
        started_at=row.started_at,
        retryable=row.retryable,
        created_at=row.created_at,
        updated_at=row.updated_at,
        heartbeat_at=row.heartbeat_at,
        manifest_id=row.manifest_id,
        auto_retry_count=row.auto_retry_count,
    )


def _attempt_to_data(row: ProcessingJobAttempt) -> ProcessingJobAttemptData:
    return ProcessingJobAttemptData(
        id=row.id,
        job_id=row.job_id,
        attempt_no=row.attempt_no,
        stage=row.stage,
        status=row.status,
        scope=row.scope,
        error_reason=row.error_reason,
        retryable=row.retryable,
        created_at=row.created_at,
    )


class SqlAlchemyJobRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self, workspace_id: int, material_id: int, version_id: int, stage: str
    ) -> ProcessingJobData:
        row = ProcessingJob(
            workspace_id=workspace_id, material_id=material_id, version_id=version_id, stage=stage
        )
        self._session.add(row)
        self._session.flush()
        return _job_to_data(row)

    def get(self, job_id: int) -> ProcessingJobData | None:
        row = self._session.get(ProcessingJob, job_id)
        return _job_to_data(row) if row is not None else None

    def claim(self, job_id: int) -> bool:
        from campusflow.domain.states import JobStatus

        result = self._session.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == job_id, ProcessingJob.status == JobStatus.PENDING)
            .values(status=JobStatus.RUNNING, started_at=func.now(), updated_at=func.now())
        )
        self._session.flush()
        return bool(result.rowcount)

    def record_attempt(
        self,
        job_id: int,
        *,
        status: str,
        scope: str | None,
        error_reason: str | None,
        retryable: bool | None = None,
    ) -> ProcessingJobData | None:
        from campusflow.domain.jobs import TERMINAL_JOB_STATUSES, sanitize_error_reason
        from campusflow.domain.states import JobStatus

        # 作业随资料删除（J007）：晚到的 Worker 来记录结果时作业可能已经没了，
        # 此时什么都不写（尝试历史同样随作业删除，写进去只会撞外键）
        if _row_missing(self._session, ProcessingJob, job_id):
            return None
        row = self._session.get(ProcessingJob, job_id)
        if row is None:
            return None
        row.status = status
        row.scope = scope
        row.error_reason = sanitize_error_reason(error_reason)
        # 可重试性只对"没完全成功"有意义（J005）：失败要区分临时与永久，
        # 部分成功但失败范围是临时的也允许只补那些阶段；完成则无需重投
        row.retryable = bool(retryable) if status in (JobStatus.FAILED, JobStatus.PARTIAL) else None
        row.attempts = row.attempts + 1
        # 尝试历史只在终态落一条（J005）：中间状态来回切换不写历史
        if status in TERMINAL_JOB_STATUSES:
            self._session.add(
                ProcessingJobAttempt(
                    job_id=job_id,
                    attempt_no=row.attempts,
                    stage=row.stage,
                    status=status,
                    scope=scope,
                    error_reason=sanitize_error_reason(error_reason),
                    retryable=bool(retryable),
                )
            )
        self._session.flush()
        return _job_to_data(row)

    def list_attempts(self, job_id: int) -> list[ProcessingJobAttemptData]:
        rows = self._session.scalars(
            select(ProcessingJobAttempt)
            .where(ProcessingJobAttempt.job_id == job_id)
            .order_by(ProcessingJobAttempt.attempt_no)
        )
        return [_attempt_to_data(row) for row in rows]

    def list_stale_running(
        self, *, timeout_seconds: int, embed_timeout_seconds: int | None = None
    ) -> list[ProcessingJobData]:
        from campusflow.domain.states import JobStage, JobStatus

        embed_threshold = datetime.now(UTC) - timedelta(
            seconds=timeout_seconds if embed_timeout_seconds is None else embed_timeout_seconds
        )
        threshold = datetime.now(UTC) - timedelta(seconds=timeout_seconds)
        # 索引作业按块推进，长时间没有新块完成不代表卡死；用最近心跳判断。
        rows = self._session.scalars(
            select(ProcessingJob)
            .where(
                ProcessingJob.status == JobStatus.RUNNING,
                ProcessingJob.started_at.is_not(None),
                or_(
                    and_(
                        ProcessingJob.stage != JobStage.EMBED,
                        ProcessingJob.started_at <= threshold,
                    ),
                    and_(
                        ProcessingJob.stage == JobStage.EMBED,
                        func.coalesce(ProcessingJob.heartbeat_at, ProcessingJob.started_at)
                        <= embed_threshold,
                    ),
                ),
            )
            .order_by(ProcessingJob.id)
        )
        return [_job_to_data(row) for row in rows]

    def list_by_material(self, material_id: int) -> list[ProcessingJobData]:
        rows = self._session.scalars(
            select(ProcessingJob)
            .where(ProcessingJob.material_id == material_id)
            .order_by(ProcessingJob.id)
        )
        return [_job_to_data(row) for row in rows]

    def prepare_retry(self, job_id: int) -> ProcessingJobData | None:
        from campusflow.domain.jobs import can_retry
        from campusflow.domain.states import JobStatus

        if _row_missing(self._session, ProcessingJob, job_id):
            return None  # 作业已随资料删除（J007），没有可重投的目标
        row = self._session.get(ProcessingJob, job_id)
        # 只有"没完全成功且标记为可重试"的作业能重投：永久失败重试也不会
        # 成功，反复重投只会白烧资源（J005/J006）
        if row is None or not can_retry(row.status, row.retryable):
            return None
        row.status = JobStatus.PENDING
        row.scope = None
        row.error_reason = None
        row.retryable = None
        row.started_at = None
        row.heartbeat_at = None
        row.manifest_id = None
        # 人工重试重新计算自动重投额度，避免历史自动重投次数把人工重试挡住
        row.auto_retry_count = 0
        # 清空投递时间，重新回到"待投递"：投递失败时仍会被补偿扫描捞起来
        row.enqueued_at = None
        self._session.flush()
        return _job_to_data(row)

    def record_delivery(self, job_id: int, *, delivered: bool) -> ProcessingJobData | None:
        # 投递前资料可能刚被删除（J007）：作业没了就不必再登记投递
        if _row_missing(self._session, ProcessingJob, job_id):
            return None
        row = self._session.get(ProcessingJob, job_id)
        if row is None:
            return None
        row.delivery_attempts = row.delivery_attempts + 1
        if delivered:
            row.enqueued_at = datetime.now(UTC)
        self._session.flush()
        return _job_to_data(row)

    def list_undelivered(
        self, *, limit: int = 100, workspace_id: int | None = None
    ) -> list[ProcessingJobData]:
        from campusflow.domain.states import JobStatus

        query = select(ProcessingJob).where(
            ProcessingJob.enqueued_at.is_(None), ProcessingJob.status == JobStatus.PENDING
        )
        if workspace_id is not None:
            query = query.where(ProcessingJob.workspace_id == workspace_id)
        rows = self._session.scalars(query.order_by(ProcessingJob.id).limit(limit))
        return [_job_to_data(row) for row in rows]

    def set_manifest(self, job_id: int, manifest_id: str) -> ProcessingJobData | None:
        if _row_missing(self._session, ProcessingJob, job_id):
            return None
        row = self._session.get(ProcessingJob, job_id)
        if row is None:
            return None
        row.manifest_id = manifest_id
        self._session.flush()
        return _job_to_data(row)

    def heartbeat(self, job_id: int) -> ProcessingJobData | None:
        if _row_missing(self._session, ProcessingJob, job_id):
            return None
        row = self._session.get(ProcessingJob, job_id)
        if row is None:
            return None
        row.heartbeat_at = datetime.now(UTC)
        self._session.flush()
        return _job_to_data(row)

    def create_or_requeue(
        self, workspace_id: int, material_id: int, version_id: int, stage: str
    ) -> ProcessingJobData:
        from campusflow.domain.states import JobStage, JobStatus

        stage = JobStage(stage)
        # 先插入再读取：外部唯一索引兜底并发，重复的解析结果不会造出第二个索引作业。
        self._session.execute(
            pg_insert(ProcessingJob)
            .values(
                workspace_id=workspace_id,
                material_id=material_id,
                version_id=version_id,
                stage=stage,
            )
            .on_conflict_do_nothing(
                index_elements=["version_id"],
                index_where=text("stage = 'embed'"),
            )
        )
        row = self._session.scalar(
            select(ProcessingJob)
            .where(ProcessingJob.version_id == version_id, ProcessingJob.stage == stage)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise DomainError("作业创建失败，请稍后重试")
        # 待处理或处理中不改状态：避免两个执行者同时消费同一作业。
        if row.status in (JobStatus.PENDING, JobStatus.RUNNING):
            return _job_to_data(row)
        # 终态重置：成功块保留，已完成的块不会重复调用模型。
        row.status = JobStatus.PENDING
        row.scope = None
        row.error_reason = None
        row.retryable = None
        row.started_at = None
        row.heartbeat_at = None
        row.enqueued_at = None
        row.manifest_id = None
        row.auto_retry_count = 0
        self._session.flush()
        return _job_to_data(row)

    def prepare_auto_retry(self, job_id: int, *, max_auto_retries: int) -> ProcessingJobData | None:
        from campusflow.domain.states import JobStage, JobStatus

        result = self._session.execute(
            update(ProcessingJob)
            .where(
                ProcessingJob.id == job_id,
                ProcessingJob.stage == JobStage.EMBED,
                ProcessingJob.status.in_([JobStatus.FAILED, JobStatus.PARTIAL]),
                ProcessingJob.retryable.is_(True),
                ProcessingJob.auto_retry_count < max_auto_retries,
            )
            .values(
                status=JobStatus.PENDING,
                scope=None,
                error_reason=None,
                retryable=None,
                started_at=None,
                heartbeat_at=None,
                enqueued_at=None,
                manifest_id=None,
                auto_retry_count=ProcessingJob.auto_retry_count + 1,
                updated_at=func.now(),
            )
        )
        if not result.rowcount:
            return None
        self._session.flush()
        row = self._session.get(ProcessingJob, job_id)
        return _job_to_data(row) if row is not None else None

    def list_auto_retryable(
        self, *, stage: str, max_auto_retries: int, limit: int = 50
    ) -> list[ProcessingJobData]:
        from campusflow.domain.states import JobStatus

        rows = self._session.scalars(
            select(ProcessingJob)
            .where(
                ProcessingJob.stage == stage,
                ProcessingJob.status.in_([JobStatus.FAILED, JobStatus.PARTIAL]),
                ProcessingJob.retryable.is_(True),
                ProcessingJob.auto_retry_count < max_auto_retries,
            )
            .order_by(ProcessingJob.id)
            .limit(limit)
        )
        return [_job_to_data(row) for row in rows]


def _reminder_rule_to_data(row: ReminderRule) -> ReminderRuleData:
    return ReminderRuleData(
        id=row.id,
        workspace_id=row.workspace_id,
        task_id=row.task_id,
        advance_minutes=row.advance_minutes,
        repeat_minutes=row.repeat_minutes,
        paused=row.paused,
        enabled=row.enabled,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _reminder_trigger_to_data(row: ReminderTrigger) -> ReminderTriggerData:
    return ReminderTriggerData(
        id=row.id,
        workspace_id=row.workspace_id,
        rule_id=row.rule_id,
        task_id=row.task_id,
        kind=row.kind,
        dedupe_key=row.dedupe_key,
        scheduled_for=row.scheduled_for,
        message=row.message,
        status=row.status,
        payload=row.payload,
        created_at=row.created_at,
        updated_at=row.updated_at,
        sent_at=row.sent_at,
        read_at=row.read_at,
    )


def _plan_draft_to_data(row: PlanDraft) -> PlanDraftData:
    return PlanDraftData(
        id=row.id,
        workspace_id=row.workspace_id,
        semester_id=row.semester_id,
        range_start=row.range_start,
        range_end=row.range_end,
        status=row.status,
        version=row.version,
        input_snapshot=row.input_snapshot,
        input_fingerprint=row.input_fingerprint,
        assumptions=row.assumptions,
        gaps=row.gaps,
        unscheduled=row.unscheduled,
        conflicts=row.conflicts,
        coverage=row.coverage,
        created_at=row.created_at,
        updated_at=row.updated_at,
        accepted_at=row.accepted_at,
    )


def _plan_block_to_data(row: PlanBlock) -> PlanBlockData:
    return PlanBlockData(
        id=row.id,
        draft_id=row.draft_id,
        task_id=row.task_id,
        kind=row.kind,
        title=row.title,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        minutes=row.minutes,
        seq=row.seq,
        locked=row.locked,
        reason=row.reason,
        constraint_source=row.constraint_source,
        created_at=row.created_at,
    )


def _plan_version_to_data(row: PlanVersion) -> PlanVersionData:
    return PlanVersionData(
        id=row.id,
        draft_id=row.draft_id,
        version_no=row.version_no,
        change=row.change,
        snapshot=row.snapshot,
        created_at=row.created_at,
    )


class SqlAlchemyPlanRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

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
    ) -> PlanDraftData:
        row = PlanDraft(
            workspace_id=workspace_id,
            semester_id=semester_id,
            range_start=range_start,
            range_end=range_end,
            input_snapshot=input_snapshot,
            input_fingerprint=input_fingerprint,
            assumptions=assumptions,
            gaps=gaps,
            unscheduled=unscheduled,
            conflicts=conflicts,
            coverage=coverage,
        )
        self._session.add(row)
        self._session.flush()
        return _plan_draft_to_data(row)

    def get_draft(self, draft_id: int) -> PlanDraftData | None:
        row = self._session.get(PlanDraft, draft_id)
        if row is None:
            return None
        data = _plan_draft_to_data(row)
        data.blocks = self.list_blocks(row.id)
        return data

    def list_drafts(
        self, workspace_id: int, *, semester_id: int | None = None
    ) -> list[PlanDraftData]:
        stmt = select(PlanDraft).where(PlanDraft.workspace_id == workspace_id)
        if semester_id is not None:
            stmt = stmt.where(PlanDraft.semester_id == semester_id)
        stmt = stmt.order_by(PlanDraft.created_at.desc(), PlanDraft.id.desc())
        return [_plan_draft_to_data(row) for row in self._session.scalars(stmt)]

    def get_current(
        self, workspace_id: int, *, semester_id: int | None = None
    ) -> PlanDraftData | None:
        stmt = select(PlanDraft).where(
            PlanDraft.workspace_id == workspace_id, PlanDraft.status == "accepted"
        )
        if semester_id is None:
            stmt = stmt.where(PlanDraft.semester_id.is_(None))
        else:
            stmt = stmt.where(PlanDraft.semester_id == semester_id)
        row = self._session.scalar(stmt.order_by(PlanDraft.accepted_at.desc(), PlanDraft.id.desc()))
        if row is None:
            return None
        data = _plan_draft_to_data(row)
        data.blocks = self.list_blocks(row.id)
        return data

    def create_blocks(self, draft_id: int, blocks: list[dict]) -> list[PlanBlockData]:
        rows = [
            PlanBlock(
                draft_id=draft_id,
                task_id=block.get("task_id"),
                kind=block.get("kind", "task"),
                title=block["title"],
                starts_at=block["starts_at"],
                ends_at=block["ends_at"],
                minutes=block["minutes"],
                seq=block.get("seq", 0),
                locked=block.get("locked", False),
                reason=block["reason"],
                constraint_source=block.get("constraint_source", []),
            )
            for block in blocks
        ]
        self._session.add_all(rows)
        self._session.flush()
        return [_plan_block_to_data(row) for row in rows]

    def list_blocks(self, draft_id: int) -> list[PlanBlockData]:
        stmt = (
            select(PlanBlock)
            .where(PlanBlock.draft_id == draft_id)
            .order_by(PlanBlock.starts_at, PlanBlock.seq, PlanBlock.id)
        )
        return [_plan_block_to_data(row) for row in self._session.scalars(stmt)]

    def get_block(self, block_id: int) -> PlanBlockData | None:
        row = self._session.get(PlanBlock, block_id)
        return _plan_block_to_data(row) if row is not None else None

    def update_block(
        self,
        block_id: int,
        *,
        starts_at: datetime,
        ends_at: datetime,
        minutes: int,
        locked: bool,
        reason: str,
    ) -> PlanBlockData | None:
        row = self._session.get(PlanBlock, block_id)
        if row is None:
            return None
        row.starts_at = starts_at
        row.ends_at = ends_at
        row.minutes = minutes
        row.locked = locked
        row.reason = reason
        self._session.flush()
        return _plan_block_to_data(row)

    def reassign_task(self, source_task_id: int, target_task_id: int) -> int:
        rows = self._session.scalars(
            select(PlanBlock).where(PlanBlock.task_id == source_task_id)
        ).all()
        for row in rows:
            row.task_id = target_task_id
        self._session.flush()
        return len(rows)

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
        values: dict[str, object] = {
            "version": PlanDraft.version + 1,
            "updated_at": func.now(),
        }
        if status is not None:
            values["status"] = status
        if accepted_at is not None:
            values["accepted_at"] = accepted_at
        result = self._session.execute(
            update(PlanDraft)
            .where(
                PlanDraft.id == draft_id,
                PlanDraft.version == expected_version,
                PlanDraft.status.in_(["draft", "accepted"]),
            )
            .values(**values)
        )
        if not result.rowcount:
            return None
        self._session.flush()
        row = self._session.get(PlanDraft, draft_id)
        if row is None:
            return None
        self.add_version(draft_id, version_no=row.version, change=change, snapshot=snapshot)
        return _plan_draft_to_data(row)

    def supersede_others(
        self, workspace_id: int, *, semester_id: int | None, keep_draft_id: int
    ) -> None:
        stmt = update(PlanDraft).where(
            PlanDraft.workspace_id == workspace_id,
            PlanDraft.status == "accepted",
            PlanDraft.id != keep_draft_id,
        )
        if semester_id is None:
            stmt = stmt.where(PlanDraft.semester_id.is_(None))
        else:
            stmt = stmt.where(PlanDraft.semester_id == semester_id)
        self._session.execute(stmt.values(status="superseded", updated_at=func.now()))

    def add_version(
        self, draft_id: int, *, version_no: int, change: str, snapshot: dict
    ) -> PlanVersionData:
        row = PlanVersion(
            draft_id=draft_id, version_no=version_no, change=change, snapshot=snapshot
        )
        self._session.add(row)
        self._session.flush()
        return _plan_version_to_data(row)

    def list_versions(self, draft_id: int) -> list[PlanVersionData]:
        stmt = (
            select(PlanVersion)
            .where(PlanVersion.draft_id == draft_id)
            .order_by(PlanVersion.version_no)
        )
        return [_plan_version_to_data(row) for row in self._session.scalars(stmt)]


class SqlAlchemyReminderRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_rules(self, workspace_id: int) -> list[ReminderRuleData]:
        stmt = (
            select(ReminderRule)
            .where(ReminderRule.workspace_id == workspace_id)
            .order_by(ReminderRule.task_id.nulls_first(), ReminderRule.id)
        )
        return [_reminder_rule_to_data(row) for row in self._session.scalars(stmt)]

    def get_rule(self, rule_id: int) -> ReminderRuleData | None:
        row = self._session.get(ReminderRule, rule_id)
        return _reminder_rule_to_data(row) if row is not None else None

    def get_task_rule(self, workspace_id: int, task_id: int) -> ReminderRuleData | None:
        row = self._session.scalar(
            select(ReminderRule).where(
                ReminderRule.workspace_id == workspace_id,
                ReminderRule.task_id == task_id,
            )
        )
        return _reminder_rule_to_data(row) if row is not None else None

    def get_default_rule(self, workspace_id: int) -> ReminderRuleData | None:
        row = self._session.scalar(
            select(ReminderRule).where(
                ReminderRule.workspace_id == workspace_id,
                ReminderRule.task_id.is_(None),
            )
        )
        return _reminder_rule_to_data(row) if row is not None else None

    def create_rule(
        self,
        workspace_id: int,
        *,
        task_id: int | None,
        advance_minutes: int,
        repeat_minutes: int | None,
        paused: bool,
        enabled: bool,
    ) -> ReminderRuleData:
        row = ReminderRule(
            workspace_id=workspace_id,
            task_id=task_id,
            advance_minutes=advance_minutes,
            repeat_minutes=repeat_minutes,
            paused=paused,
            enabled=enabled,
        )
        self._session.add(row)
        self._session.flush()
        return _reminder_rule_to_data(row)

    def update_rule(
        self,
        rule_id: int,
        *,
        advance_minutes: int,
        repeat_minutes: int | None,
        paused: bool,
        enabled: bool,
    ) -> ReminderRuleData | None:
        row = self._session.get(ReminderRule, rule_id)
        if row is None:
            return None
        row.advance_minutes = advance_minutes
        row.repeat_minutes = repeat_minutes
        row.paused = paused
        row.enabled = enabled
        row.version += 1
        self._session.flush()
        return _reminder_rule_to_data(row)

    def delete_rule(self, rule_id: int) -> bool:
        row = self._session.get(ReminderRule, rule_id)
        if row is None:
            return False
        self._session.delete(row)
        self._session.flush()
        return True

    def list_triggers(
        self,
        workspace_id: int,
        *,
        statuses: tuple[str, ...] | None = None,
        unread_only: bool = False,
        limit: int = 200,
    ) -> list[ReminderTriggerData]:
        stmt = select(ReminderTrigger).where(ReminderTrigger.workspace_id == workspace_id)
        if statuses is not None:
            stmt = stmt.where(ReminderTrigger.status.in_(statuses))
        if unread_only:
            stmt = stmt.where(ReminderTrigger.status == "sent", ReminderTrigger.read_at.is_(None))
        stmt = stmt.order_by(ReminderTrigger.scheduled_for.desc(), ReminderTrigger.id.desc()).limit(
            limit
        )
        return [_reminder_trigger_to_data(row) for row in self._session.scalars(stmt)]

    def get_trigger(self, trigger_id: int) -> ReminderTriggerData | None:
        row = self._session.get(ReminderTrigger, trigger_id)
        return _reminder_trigger_to_data(row) if row is not None else None

    def upsert_triggers(self, rows: list[dict]) -> list[ReminderTriggerData]:
        if not rows:
            return []
        insert_stmt = pg_insert(ReminderTrigger).values(rows)
        statement = insert_stmt.on_conflict_do_update(
            index_elements=["dedupe_key"],
            set_={
                "status": "pending",
                "message": insert_stmt.excluded.message,
                "payload": insert_stmt.excluded.payload,
                "updated_at": func.now(),
            },
            # 仍需要的触发恢复为待发送；已读或已忽略的不重新弹出
            where=ReminderTrigger.status == "cancelled",
        ).returning(ReminderTrigger.id)
        created_ids = list(self._session.scalars(statement))
        self._session.flush()
        return [
            _reminder_trigger_to_data(row)
            for row in (self._session.get(ReminderTrigger, item) for item in created_ids)
            if row is not None
        ]

    def set_trigger_status(
        self,
        trigger_id: int,
        *,
        status: str,
        read_at: datetime | None = None,
        sent_at: datetime | None = None,
    ) -> ReminderTriggerData | None:
        row = self._session.get(ReminderTrigger, trigger_id)
        if row is None:
            return None
        row.status = status
        if read_at is not None:
            row.read_at = read_at
        if sent_at is not None:
            row.sent_at = sent_at
        self._session.flush()
        return _reminder_trigger_to_data(row)

    def cancel_pending_for_task(self, workspace_id: int, task_id: int) -> int:
        result = self._session.execute(
            update(ReminderTrigger)
            .where(
                ReminderTrigger.workspace_id == workspace_id,
                ReminderTrigger.task_id == task_id,
                ReminderTrigger.status == "pending",
            )
            .values(status="cancelled", updated_at=func.now())
        )
        self._session.flush()
        return result.rowcount or 0

    def cancel_pending_not_in_keys(
        self, workspace_id: int, task_id: int, keep_keys: set[str]
    ) -> int:
        stmt = update(ReminderTrigger).where(
            ReminderTrigger.workspace_id == workspace_id,
            ReminderTrigger.task_id == task_id,
            ReminderTrigger.status == "pending",
        )
        if keep_keys:
            stmt = stmt.where(ReminderTrigger.dedupe_key.not_in(keep_keys))
        result = self._session.execute(stmt.values(status="cancelled", updated_at=func.now()))
        self._session.flush()
        return result.rowcount or 0

    def cancel_pending_reviews(self, workspace_id: int) -> int:
        result = self._session.execute(
            update(ReminderTrigger)
            .where(
                ReminderTrigger.workspace_id == workspace_id,
                ReminderTrigger.kind == "review",
                ReminderTrigger.status == "pending",
            )
            .values(status="cancelled", updated_at=func.now())
        )
        self._session.flush()
        return result.rowcount or 0

    def restore_future_cancelled(self, workspace_id: int, task_id: int, now: datetime) -> int:
        result = self._session.execute(
            update(ReminderTrigger)
            .where(
                ReminderTrigger.workspace_id == workspace_id,
                ReminderTrigger.task_id == task_id,
                ReminderTrigger.status == "cancelled",
                ReminderTrigger.scheduled_for > now,
            )
            .values(status="pending", updated_at=func.now())
        )
        self._session.flush()
        return result.rowcount or 0

    def mark_due_sent(self, workspace_id: int, now: datetime) -> int:
        result = self._session.execute(
            update(ReminderTrigger)
            .where(
                ReminderTrigger.workspace_id == workspace_id,
                ReminderTrigger.status == "pending",
                ReminderTrigger.scheduled_for <= now,
            )
            .values(status="sent", sent_at=now, updated_at=func.now())
        )
        self._session.flush()
        return result.rowcount or 0


def build_repositories(session: Session) -> Repositories:
    """装配全部仓储与工作单元（API 装配、Worker、投递补偿共用）。"""
    return Repositories(
        workspaces=SqlAlchemyWorkspaceRepository(session),
        semesters=SqlAlchemySemesterRepository(session),
        courses=SqlAlchemyCourseRepository(session),
        tasks=SqlAlchemyTaskRepository(session),
        schedule=SqlAlchemyScheduleRepository(session),
        materials=SqlAlchemyMaterialRepository(session),
        jobs=SqlAlchemyJobRepository(session),
        candidates=SqlAlchemyCandidateRepository(session),
        rules=SqlAlchemyRuleRepository(session),
        plans=SqlAlchemyPlanRepository(session),
        reminders=SqlAlchemyReminderRepository(session),
        uow=SqlAlchemyUnitOfWork(session),
    )
