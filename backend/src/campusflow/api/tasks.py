"""任务 HTTP 路由。"""

from dataclasses import replace
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from campusflow.api.deps import get_default_workspace_id, get_repositories
from campusflow.api.schemas import (
    TaskCreate,
    TaskOut,
    TaskProgressPatch,
    TaskUpdate,
)
from campusflow.application import tasks as use_cases
from campusflow.application.ports.repositories import Repositories
from campusflow.domain.states import TaskProgress

router = APIRouter(tags=["tasks"])

WorkspaceId = Annotated[int, Depends(get_default_workspace_id)]
Repos = Annotated[Repositories, Depends(get_repositories)]


@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(
    workspace_id: WorkspaceId,
    repos: Repos,
    course_id: Annotated[int | None, Query()] = None,
    progress: Annotated[TaskProgress | None, Query()] = None,
) -> list[TaskOut]:
    return [
        TaskOut.model_validate(t)
        for t in use_cases.list_tasks(repos, workspace_id, course_id=course_id, progress=progress)
    ]


@router.post("/tasks", response_model=TaskOut, status_code=201)
def create_task(payload: TaskCreate, workspace_id: WorkspaceId, repos: Repos) -> TaskOut:
    task = use_cases.create_task(
        repos,
        workspace_id,
        course_id=payload.course_id,
        title=payload.title,
        description=payload.description,
        due_date=payload.due_date,
        due_time=payload.due_time,
        priority=payload.priority,
        estimated_minutes=payload.estimated_minutes,
    )
    return TaskOut.model_validate(task)


@router.get("/tasks/{task_id}", response_model=TaskOut)
def get_task(task_id: int, repos: Repos) -> TaskOut:
    return TaskOut.model_validate(use_cases.get_task(repos, task_id))


@router.patch("/tasks/{task_id}", response_model=TaskOut)
def update_task(task_id: int, payload: TaskUpdate, repos: Repos) -> TaskOut:
    current = use_cases.get_task(repos, task_id)
    merged = replace(current, **payload.model_dump(exclude_unset=True))
    task = use_cases.update_task(
        repos,
        task_id,
        course_id=merged.course_id,
        title=merged.title,
        description=merged.description,
        due_date=merged.due_date,
        due_time=merged.due_time,
        priority=merged.priority,
        estimated_minutes=merged.estimated_minutes,
    )
    return TaskOut.model_validate(task)


@router.patch("/tasks/{task_id}/progress", response_model=TaskOut)
def update_task_progress(task_id: int, payload: TaskProgressPatch, repos: Repos) -> TaskOut:
    task = use_cases.set_progress(repos, task_id, payload.progress)
    return TaskOut.model_validate(task)
