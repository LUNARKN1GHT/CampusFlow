"""R004：把已确认的任务候选转换为正式任务，关联候选与来源资料。"""

from campusflow.application.candidates import get_candidate
from campusflow.application.ports.repositories import Repositories, TaskData
from campusflow.application.scope import require_in_workspace
from campusflow.domain.candidate_conversion import build_task_conversion
from campusflow.domain.candidates import require_confirmed_for_conversion
from campusflow.domain.errors import NotFoundError
from campusflow.domain.states import TaskPriority


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
    if course_id is not None:
        course = repos.courses.get(course_id)
        if course is None:
            raise NotFoundError("课程不存在")
        require_in_workspace(course.workspace_id, workspace_id, "课程不存在")
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
