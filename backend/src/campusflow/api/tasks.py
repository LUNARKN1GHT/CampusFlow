"""任务 HTTP 路由。"""

from dataclasses import replace
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from campusflow.api.deps import get_current_workspace, get_repositories
from campusflow.api.schemas import (
    SourceDetailOut,
    TaskCreate,
    TaskDependencyIn,
    TaskDependencyOut,
    TaskOut,
    TaskParentIn,
    TaskProgressChangeOut,
    TaskProgressPatch,
    TaskRelationsOut,
    TaskUpdate,
)
from campusflow.application import source_detail as source_use_cases
from campusflow.application import tasks as use_cases
from campusflow.application.ports.repositories import Repositories, WorkspaceData
from campusflow.domain.states import TaskProgress

router = APIRouter(tags=["tasks"])

CurrentWorkspace = Annotated[WorkspaceData, Depends(get_current_workspace)]
Repos = Annotated[Repositories, Depends(get_repositories)]


@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(
    workspace: CurrentWorkspace,
    repos: Repos,
    course_id: Annotated[int | None, Query()] = None,
    progress: Annotated[TaskProgress | None, Query()] = None,
    semester_id: Annotated[int | None, Query()] = None,
    due_from: Annotated[
        date | None, Query(description="截止日期区间起点（含）；未关联课程的任务不属于任何学期")
    ] = None,
    due_to: Annotated[date | None, Query(description="截止日期区间终点（含）")] = None,
) -> list[TaskOut]:
    return [
        TaskOut.model_validate(t)
        for t in use_cases.list_tasks(
            repos,
            workspace.id,
            course_id=course_id,
            progress=progress,
            semester_id=semester_id,
            due_from=due_from,
            due_to=due_to,
        )
    ]


@router.post("/tasks", response_model=TaskOut, status_code=201)
def create_task(payload: TaskCreate, workspace: CurrentWorkspace, repos: Repos) -> TaskOut:
    task = use_cases.create_task(
        repos,
        workspace.id,
        course_id=payload.course_id,
        title=payload.title,
        description=payload.description,
        due_date=payload.due_date,
        due_time=payload.due_time,
        target_date=payload.target_date,
        due_source=payload.due_source,
        due_note=payload.due_note,
        priority=payload.priority,
        estimated_minutes=payload.estimated_minutes,
        remaining_minutes=(
            payload.remaining_minutes
            if payload.remaining_minutes is not None
            else payload.estimated_minutes
        ),
        parent_task_id=payload.parent_task_id,
    )
    return TaskOut.model_validate(task)


@router.get("/tasks/{task_id}", response_model=TaskOut)
def get_task(task_id: int, workspace: CurrentWorkspace, repos: Repos) -> TaskOut:
    return TaskOut.model_validate(use_cases.get_task(repos, workspace.id, task_id))


@router.patch("/tasks/{task_id}", response_model=TaskOut)
def update_task(
    task_id: int, payload: TaskUpdate, workspace: CurrentWorkspace, repos: Repos
) -> TaskOut:
    current = use_cases.get_task(repos, workspace.id, task_id)
    merged = replace(current, **payload.model_dump(exclude_unset=True))
    task = use_cases.update_task(
        repos,
        workspace.id,
        task_id,
        course_id=merged.course_id,
        title=merged.title,
        description=merged.description,
        due_date=merged.due_date,
        due_time=merged.due_time,
        target_date=merged.target_date,
        due_source=merged.due_source,
        due_note=merged.due_note,
        priority=merged.priority,
        estimated_minutes=merged.estimated_minutes,
        remaining_minutes=merged.remaining_minutes,
    )
    return TaskOut.model_validate(task)


@router.patch("/tasks/{task_id}/progress", response_model=TaskOut)
def update_task_progress(
    task_id: int, payload: TaskProgressPatch, workspace: CurrentWorkspace, repos: Repos
) -> TaskOut:
    task = use_cases.set_progress(repos, workspace.id, task_id, payload.progress, payload.reason)
    return TaskOut.model_validate(task)


@router.get("/tasks/{task_id}/progress-history", response_model=list[TaskProgressChangeOut])
def list_task_progress_history(
    task_id: int, workspace: CurrentWorkspace, repos: Repos
) -> list[TaskProgressChangeOut]:
    return [
        TaskProgressChangeOut.model_validate(change)
        for change in use_cases.list_progress_changes(repos, workspace.id, task_id)
    ]


@router.get("/tasks/{task_id}/relations", response_model=TaskRelationsOut)
def get_task_relations(task_id: int, workspace: CurrentWorkspace, repos: Repos) -> TaskRelationsOut:
    return TaskRelationsOut.model_validate(use_cases.task_relations(repos, workspace.id, task_id))


@router.patch("/tasks/{task_id}/parent", response_model=TaskOut)
def set_task_parent(
    task_id: int, payload: TaskParentIn, workspace: CurrentWorkspace, repos: Repos
) -> TaskOut:
    task = use_cases.set_parent(repos, workspace.id, task_id, parent_task_id=payload.parent_task_id)
    return TaskOut.model_validate(task)


@router.post("/tasks/{task_id}/dependencies", response_model=TaskDependencyOut, status_code=201)
def add_task_dependency(
    task_id: int, payload: TaskDependencyIn, workspace: CurrentWorkspace, repos: Repos
) -> TaskDependencyOut:
    link = use_cases.add_dependency(
        repos, workspace.id, task_id, depends_on_task_id=payload.depends_on_task_id
    )
    return TaskDependencyOut.model_validate(link)


@router.delete("/tasks/{task_id}/dependencies/{depends_on_task_id}", status_code=204)
def remove_task_dependency(
    task_id: int, depends_on_task_id: int, workspace: CurrentWorkspace, repos: Repos
) -> None:
    use_cases.remove_dependency(repos, workspace.id, task_id, depends_on_task_id)


@router.get("/tasks/{task_id}/source", response_model=SourceDetailOut)
def get_task_source(task_id: int, workspace: CurrentWorkspace, repos: Repos) -> SourceDetailOut:
    return SourceDetailOut.model_validate(
        source_use_cases.task_source_detail(repos, workspace.id, task_id)
    )
