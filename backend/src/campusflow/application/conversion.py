"""R004/R005：把已确认的任务或日程候选转换为正式事项，关联候选与来源资料。"""

from zoneinfo import ZoneInfo

from campusflow.application.candidates import get_candidate
from campusflow.application.ports.repositories import (
    FixedEventData,
    Repositories,
    TaskData,
)
from campusflow.application.scope import require_in_workspace
from campusflow.application.settings import get_settings
from campusflow.domain.candidate_conversion import build_task_conversion
from campusflow.domain.candidates import require_confirmed_for_conversion
from campusflow.domain.errors import DomainError, NotFoundError
from campusflow.domain.event_conversion import build_event_conversion
from campusflow.domain.schedule import validate_fixed_event
from campusflow.domain.states import EventRecurrence, TaskPriority
from campusflow.domain.timezones import in_workspace_timezone


def _require_course_in_workspace(
    repos: Repositories, workspace_id: int, course_id: int | None
) -> None:
    if course_id is None:
        return
    course = repos.courses.get(course_id)
    if course is None:
        raise NotFoundError("课程不存在")
    require_in_workspace(course.workspace_id, workspace_id, "课程不存在")


def convert_candidate_to_task(
    repos: Repositories, workspace_id: int, candidate_id: int
) -> TaskData:
    """转换已确认的任务候选；重复调用返回既有任务，不重复创建。"""
    candidate = get_candidate(repos, workspace_id, candidate_id)
    require_confirmed_for_conversion(candidate.review_status)
    existing = repos.tasks.get_by_source_candidate(candidate.id)
    if existing is not None:
        return existing
    course_id = candidate.draft.scope.course_id
    _require_course_in_workspace(repos, workspace_id, course_id)
    version = repos.materials.get_version(candidate.source_version_id)
    if version is None:
        raise NotFoundError("候选来源版本不存在")
    conversion = build_task_conversion(candidate.draft.kind, candidate.effective_draft().fields)
    try:
        task = repos.tasks.create(
            workspace_id,
            course_id,
            conversion.title,
            conversion.description,
            conversion.due_date,
            conversion.due_time,
            None,
            conversion.due_source,
            conversion.due_note,
            TaskPriority.MEDIUM,
            conversion.estimated_minutes,
            conversion.estimated_minutes,
            source_material_id=version.material_id,
            source_candidate_id=candidate.id,
        )
        repos.uow.commit()
    except Exception:
        repos.uow.rollback()
        raise
    return task


def convert_candidate_to_event(
    repos: Repositories, workspace_id: int, candidate_id: int
) -> FixedEventData:
    """转换已确认的日程候选；起止不可确定时拒绝，重复调用返回既有日程。"""
    candidate = get_candidate(repos, workspace_id, candidate_id)
    require_confirmed_for_conversion(candidate.review_status)
    existing = repos.schedule.get_fixed_event_by_source_candidate(candidate.id)
    if existing is not None:
        return existing
    course_id = candidate.draft.scope.course_id
    _require_course_in_workspace(repos, workspace_id, course_id)
    conversion = build_event_conversion(candidate.draft.kind, candidate.effective_draft().fields)
    timezone = ZoneInfo(get_settings(repos, workspace_id).timezone)
    starts_at = in_workspace_timezone(conversion.starts_at, timezone)
    ends_at = in_workspace_timezone(conversion.ends_at, timezone)
    error = validate_fixed_event(starts_at, ends_at, EventRecurrence.NONE, None)
    if error:
        raise DomainError(error)
    try:
        event = repos.schedule.create_fixed_event(
            workspace_id,
            course_id,
            conversion.title,
            starts_at,
            ends_at,
            conversion.location,
            EventRecurrence.NONE,
            None,
            source_candidate_id=candidate.id,
        )
        repos.uow.commit()
    except Exception:
        repos.uow.rollback()
        raise
    return event
