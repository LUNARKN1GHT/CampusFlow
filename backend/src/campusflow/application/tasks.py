"""任务用例：编排校验、仓储与事务。

核对状态（M2 的 ExtractedItem）与任务执行进度相互独立；
手动任务只维护执行进度。update_task 接收"合并后的最终值"（由 API 层
把补丁与当前值合并后传入），None 表示该字段确实要清空。
"""

from datetime import date, time

from campusflow.application.ports.repositories import Repositories, TaskData
from campusflow.domain.dates import validate_due
from campusflow.domain.errors import DomainError, NotFoundError
from campusflow.domain.states import TaskPriority, TaskProgress


def list_tasks(
    repos: Repositories,
    workspace_id: int,
    *,
    course_id: int | None = None,
    progress: TaskProgress | None = None,
) -> list[TaskData]:
    return repos.tasks.list(workspace_id, course_id=course_id, progress=progress)


def get_task(repos: Repositories, task_id: int) -> TaskData:
    task = repos.tasks.get(task_id)
    if task is None:
        raise NotFoundError("任务不存在")
    return task


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
) -> TaskData:
    error = validate_due(due_date, due_time)
    if error:
        raise DomainError(error)
    if course_id is not None and repos.courses.get(course_id) is None:
        raise NotFoundError("课程不存在")
    task = repos.tasks.create(
        workspace_id,
        course_id,
        title,
        description,
        due_date,
        due_time,
        priority,
        estimated_minutes,
    )
    repos.uow.commit()
    return task


def update_task(
    repos: Repositories,
    task_id: int,
    *,
    course_id: int | None,
    title: str,
    description: str | None,
    due_date: date | None,
    due_time: time | None,
    priority: TaskPriority,
    estimated_minutes: int | None,
) -> TaskData:
    if repos.tasks.get(task_id) is None:
        raise NotFoundError("任务不存在")
    error = validate_due(due_date, due_time)
    if error:
        raise DomainError(error)
    if course_id is not None and repos.courses.get(course_id) is None:
        raise NotFoundError("课程不存在")
    updated = repos.tasks.update(
        task_id,
        course_id=course_id,
        title=title,
        description=description,
        due_date=due_date,
        due_time=due_time,
        priority=priority,
        estimated_minutes=estimated_minutes,
    )
    repos.uow.commit()
    return updated


def set_progress(repos: Repositories, task_id: int, progress: TaskProgress) -> TaskData:
    task = repos.tasks.set_progress(task_id, progress)
    if task is None:
        raise NotFoundError("任务不存在")
    repos.uow.commit()
    return task
