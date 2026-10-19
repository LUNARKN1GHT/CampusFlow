"""任务用例：编排校验、仓储与事务。

核对状态（M2 的 ExtractedItem）与任务执行进度相互独立；
手动任务只维护执行进度。update_task 接收"合并后的最终值"（由 API 层
把补丁与当前值合并后传入），None 表示该字段确实要清空。
所有按 ID 的操作校验对象归属当前空间（W006）。
"""

from dataclasses import replace
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from campusflow.application.courses import require_semester_in_workspace
from campusflow.application.ports.repositories import Repositories, TaskData, TaskDependencyData
from campusflow.application.scope import require_in_workspace
from campusflow.domain.dates import is_overdue, validate_due
from campusflow.domain.errors import DomainError, NotFoundError
from campusflow.domain.states import DueSourceType, TaskPriority, TaskProgress


def _apply_overdue(repos: Repositories, workspace_id: int, tasks: list[TaskData]) -> list[TaskData]:
    workspace = repos.workspaces.get(workspace_id)
    if workspace is None:
        return tasks
    now = datetime.now(ZoneInfo(workspace.timezone))
    return [
        replace(task, overdue=is_overdue(task.due_date, task.due_time, task.progress, now))
        for task in tasks
    ]


def list_tasks(
    repos: Repositories,
    workspace_id: int,
    *,
    course_id: int | None = None,
    progress: TaskProgress | None = None,
    semester_id: int | None = None,
    due_from: date | None = None,
    due_to: date | None = None,
) -> list[TaskData]:
    if semester_id is not None:
        require_semester_in_workspace(repos, workspace_id, semester_id)
    if due_from is not None and due_to is not None and due_from > due_to:
        raise DomainError("截止区间起点不能晚于终点")
    tasks = repos.tasks.list(
        workspace_id,
        course_id=course_id,
        progress=progress,
        semester_id=semester_id,
        due_from=due_from,
        due_to=due_to,
    )
    return _apply_overdue(repos, workspace_id, tasks)


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
    task = _require_task_in_workspace(repos, workspace_id, task_id)
    return _apply_overdue(repos, workspace_id, [task])[0]


def create_task(
    repos: Repositories,
    workspace_id: int,
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
    parent_task_id: int | None = None,
) -> TaskData:
    error = validate_due(due_date, due_time)
    if error:
        raise DomainError(error)
    if parent_task_id is not None:
        parent = _require_task_in_workspace(repos, workspace_id, parent_task_id)
        if course_id is None:
            course_id = parent.course_id
    if course_id is not None:
        _require_course_in_workspace(repos, workspace_id, course_id)
    task = repos.tasks.create(
        workspace_id,
        course_id,
        title,
        description,
        due_date,
        due_time,
        target_date,
        due_source,
        due_note,
        priority,
        estimated_minutes,
        remaining_minutes,
        parent_task_id=parent_task_id,
    )
    repos.uow.commit()
    return _apply_overdue(repos, workspace_id, [task])[0]


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
    target_date: date | None,
    due_source: DueSourceType,
    due_note: str | None,
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
        target_date=target_date,
        due_source=due_source,
        due_note=due_note,
        priority=priority,
        estimated_minutes=estimated_minutes,
        remaining_minutes=remaining_minutes,
    )
    repos.uow.commit()
    return _apply_overdue(repos, workspace_id, [updated])[0]


def set_progress(
    repos: Repositories, workspace_id: int, task_id: int, progress: TaskProgress, reason: str | None
) -> TaskData:
    _require_task_in_workspace(repos, workspace_id, task_id)
    task = repos.tasks.set_progress(task_id, progress, reason)
    repos.uow.commit()
    return _apply_overdue(repos, workspace_id, [task])[0]


def list_progress_changes(repos: Repositories, workspace_id: int, task_id: int):
    _require_task_in_workspace(repos, workspace_id, task_id)
    return repos.tasks.list_progress_changes(task_id)


def set_parent(
    repos: Repositories, workspace_id: int, task_id: int, parent_task_id: int | None
) -> TaskData:
    """设置或清除子任务的父任务（A001）。

    只改包含关系，不创建先后依赖；父链上出现自己时拒绝，避免包含关系成环。
    """
    task = _require_task_in_workspace(repos, workspace_id, task_id)
    if parent_task_id is not None:
        parent = _require_task_in_workspace(repos, workspace_id, parent_task_id)
        current = parent
        while current is not None:
            if current.id == task.id:
                raise DomainError("不能把任务设为自己的子任务或其子任务下，包含关系不能成环")
            current = (
                repos.tasks.get(current.parent_task_id)
                if current.parent_task_id is not None
                else None
            )
    updated = repos.tasks.set_parent(task_id, parent_task_id)
    repos.uow.commit()
    return _apply_overdue(repos, workspace_id, [updated])[0]


def _task_ref(task: TaskData) -> dict:
    return {
        "id": task.id,
        "title": task.title,
        "progress": task.progress,
        "due_date": task.due_date,
        "parent_task_id": task.parent_task_id,
    }


def task_relations(repos: Repositories, workspace_id: int, task_id: int) -> dict:
    """任务包含关系与先后依赖（A001），两者分字段返回，不互相推断。"""
    task = _require_task_in_workspace(repos, workspace_id, task_id)
    parent = repos.tasks.get(task.parent_task_id) if task.parent_task_id is not None else None
    subtasks = repos.tasks.list_subtasks(task.id)
    prerequisites = [
        repos.tasks.get(link.depends_on_task_id) for link in repos.tasks.list_dependencies(task.id)
    ]
    dependents = [repos.tasks.get(link.task_id) for link in repos.tasks.list_dependents(task.id)]
    return {
        "task_id": task.id,
        "parent": _task_ref(parent) if parent is not None else None,
        "subtasks": [_task_ref(item) for item in subtasks],
        "prerequisites": [_task_ref(item) for item in prerequisites if item is not None],
        "dependents": [_task_ref(item) for item in dependents if item is not None],
    }


def _existing_dependency(
    repos: Repositories, task_id: int, depends_on_task_id: int
) -> TaskDependencyData | None:
    for link in repos.tasks.list_dependencies(task_id):
        if link.depends_on_task_id == depends_on_task_id:
            return link
    return None


def add_dependency(
    repos: Repositories, workspace_id: int, task_id: int, depends_on_task_id: int
) -> TaskDependencyData:
    """添加"先完成前置任务"的必要依赖（A001）；重复添加保持幂等。"""
    _require_task_in_workspace(repos, workspace_id, task_id)
    _require_task_in_workspace(repos, workspace_id, depends_on_task_id)
    if task_id == depends_on_task_id:
        raise DomainError("任务不能依赖自己")
    existing = _existing_dependency(repos, task_id, depends_on_task_id)
    if existing is not None:
        return existing
    link = repos.tasks.add_dependency(workspace_id, task_id, depends_on_task_id)
    repos.uow.commit()
    return link


def remove_dependency(
    repos: Repositories, workspace_id: int, task_id: int, depends_on_task_id: int
) -> None:
    _require_task_in_workspace(repos, workspace_id, task_id)
    if not repos.tasks.remove_dependency(task_id, depends_on_task_id):
        raise NotFoundError("依赖关系不存在")
    repos.uow.commit()
