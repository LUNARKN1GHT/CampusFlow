"""任务用例：编排校验、仓储与事务。

核对状态（M2 的 ExtractedItem）与任务执行进度相互独立；
手动任务只维护执行进度。update_task 接收"合并后的最终值"（由 API 层
把补丁与当前值合并后传入），None 表示该字段确实要清空。
所有按 ID 的操作校验对象归属当前空间（W006）。
"""

from datetime import date, time

from campusflow.application.courses import require_semester_in_workspace
from campusflow.application.ports.repositories import Repositories, TaskData
from campusflow.application.scope import require_in_workspace
from campusflow.domain.dates import validate_due
from campusflow.domain.errors import DomainError, NotFoundError
from campusflow.domain.states import TaskPriority, TaskProgress


def list_tasks(
    repos: Repositories,
    workspace_id: int,
    *,
    course_id: int | None = None,
    progress: TaskProgress | None = None,
    semester_id: int | None = None,
) -> list[TaskData]:
    if semester_id is not None:
        require_semester_in_workspace(repos, workspace_id, semester_id)
    return repos.tasks.list(
        workspace_id, course_id=course_id, progress=progress, semester_id=semester_id
    )


def _require_task_in_workspace(repos: Repositories, workspace_id: int, task_id: int) -> TaskData:
    task = repos.tasks.get(task_id)
    if task is None:
        raise NotFoundError("任务不存在")
    require_in_workspace(task.workspace_id, workspace_id, "任务不存在")
    return task


def _require_course_in_workspace(repos: Repositories, workspace_id: int, course_id: int) -> None:
    course = repos.courses.get(course_id)
    if course is None:
        raise NotFoundError("课程不存在")
    require_in_workspace(course.workspace_id, workspace_id, "课程不存在")


def get_task(repos: Repositories, workspace_id: int, task_id: int) -> TaskData:
    return _require_task_in_workspace(repos, workspace_id, task_id)


def create_task(
    repos: Repositories,
    workspace_id: int,
    *,
    course_id: int | None,
    title: str,
    description: str | None,
    due_date: date | None,
    due_time: time | None,
    priority: TaskPriority,
    estimated_minutes: int | None,
    remaining_minutes: int | None,
) -> TaskData:
    error = validate_due(due_date, due_time)
    if error:
        raise DomainError(error)
    if course_id is not None:
        _require_course_in_workspace(repos, workspace_id, course_id)
    task = repos.tasks.create(
        workspace_id,
        course_id,
        title,
        description,
        due_date,
        due_time,
        priority,
        estimated_minutes,
        remaining_minutes,
    )
    repos.uow.commit()
    return task


def update_task(
    repos: Repositories,
    workspace_id: int,
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
) -> TaskData:
    _require_task_in_workspace(repos, workspace_id, task_id)
    error = validate_due(due_date, due_time)
    if error:
        raise DomainError(error)
    if course_id is not None:
        _require_course_in_workspace(repos, workspace_id, course_id)
    updated = repos.tasks.update(
        task_id,
        course_id=course_id,
        title=title,
        description=description,
        due_date=due_date,
        due_time=due_time,
        priority=priority,
        estimated_minutes=estimated_minutes,
        remaining_minutes=remaining_minutes,
    )
    repos.uow.commit()
    return updated


def set_progress(
    repos: Repositories, workspace_id: int, task_id: int, progress: TaskProgress, reason: str | None
) -> TaskData:
    _require_task_in_workspace(repos, workspace_id, task_id)
    task = repos.tasks.set_progress(task_id, progress, reason)
    repos.uow.commit()
    return task


def list_progress_changes(repos: Repositories, workspace_id: int, task_id: int):
    _require_task_in_workspace(repos, workspace_id, task_id)
    return repos.tasks.list_progress_changes(task_id)
