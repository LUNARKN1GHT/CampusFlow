"""任务 HTTP 路由。

执行进度、父子包含与先后依赖分开保存：进度是任务状态，依赖是必要前置；
所有读写先限定当前工作空间。父任务只作容器，排期只安排子任务。
"""

from dataclasses import replace
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from campusflow.api.deps import get_current_workspace, get_repositories
from campusflow.api.schemas import (
    SourceDetailOut,
    SplitSuggestionIn,
    SubtaskBatchIn,
    TaskCreate,
    TaskDependencyIn,
    TaskDependencyOut,
    TaskOut,
    TaskParentIn,
    TaskProgressChangeOut,
    TaskProgressPatch,
    TaskRelationsOut,
    TaskRollupOut,
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
    """progress 是任务执行进度，不是候选核对状态；两套枚举不能混传。

    按学期过滤时，未关联课程的任务不属于任何学期，不会命中。
    """
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


@router.get("/tasks/dependencies/check", response_model=dict)
def check_task_dependencies(
    workspace: CurrentWorkspace,
    repos: Repos,
    semester_id: Annotated[int | None, Query(ge=1)] = None,
) -> dict:
    """只读校验依赖：返回成环路径、范围外/缺失前置与逐任务阻塞原因。

    不修改任务或依赖；成环的边在创建时就不会落库。
    """
    return use_cases.dependency_check(repos, workspace.id, semester_id=semester_id)


@router.post("/tasks", response_model=TaskOut, status_code=201)
def create_task(payload: TaskCreate, workspace: CurrentWorkspace, repos: Repos) -> TaskOut:
    """remaining_minutes 缺省等于 estimated_minutes；parent_task_id 表示包含关系。"""
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
    """进度与逾期计算独立；每次变更追加历史并同步提醒，取消后可重新打开。"""
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
    """汇总父子、子任务、前置与后置；包含关系只支持一层。"""
    return TaskRelationsOut.model_validate(use_cases.task_relations(repos, workspace.id, task_id))


@router.get("/tasks/{task_id}/subtasks", response_model=list[TaskOut])
def list_subtasks(task_id: int, workspace: CurrentWorkspace, repos: Repos) -> list[TaskOut]:
    # 先校验父任务属于当前空间，再读取子任务，避免借编号探测其他空间。
    use_cases.get_task(repos, workspace.id, task_id)
    return [TaskOut.model_validate(task) for task in repos.tasks.list_subtasks(task_id)]


@router.get("/tasks/{task_id}/rollup", response_model=TaskRollupOut)
def get_subtask_rollup(task_id: int, workspace: CurrentWorkspace, repos: Repos) -> TaskRollupOut:
    """只读汇总子任务数量、耗时与完成度，不回写父任务字段。"""
    return TaskRollupOut.model_validate(use_cases.subtask_rollup(repos, workspace.id, task_id))


@router.post("/tasks/{task_id}/subtasks", response_model=list[TaskOut], status_code=201)
def create_subtasks(
    task_id: int, payload: SubtaskBatchIn, workspace: CurrentWorkspace, repos: Repos
) -> list[TaskOut]:
    """批量创建子任务（单次最多 20 项）；父任务必须是顶层任务。"""
    drafts = [
        use_cases.SubtaskDraft(
            title=item.title,
            priority=item.priority,
            estimated_minutes=item.estimated_minutes,
            remaining_minutes=item.remaining_minutes,
            due_date=item.due_date,
        )
        for item in payload.items
    ]
    return [
        TaskOut.model_validate(task)
        for task in use_cases.create_subtasks(repos, workspace.id, task_id, drafts)
    ]


@router.post("/tasks/{task_id}/split-suggestions", response_model=dict)
def suggest_task_split(
    task_id: int,
    payload: SplitSuggestionIn,
    workspace: CurrentWorkspace,
    repos: Repos,
) -> dict:
    """只返回可编辑建议（suggestion=true），不创建任务；至少两个标题才有建议。"""
    return use_cases.split_suggestions(repos, workspace.id, task_id, titles=payload.titles)


@router.patch("/tasks/{task_id}/parent", response_model=TaskOut)
def set_task_parent(
    task_id: int, payload: TaskParentIn, workspace: CurrentWorkspace, repos: Repos
) -> TaskOut:
    """设置包含关系；父链成环或超过一层时拒绝，不自动改写依赖。"""
    task = use_cases.set_parent(repos, workspace.id, task_id, parent_task_id=payload.parent_task_id)
    return TaskOut.model_validate(task)


@router.post("/tasks/{task_id}/dependencies", response_model=TaskDependencyOut, status_code=201)
def add_task_dependency(
    task_id: int, payload: TaskDependencyIn, workspace: CurrentWorkspace, repos: Repos
) -> TaskDependencyOut:
    """依赖是必要前置而非包含关系；全空间成环时拒绝并返回具体路径。"""
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
    """手动任务与提取任务分别标识；引用只有在原文仍可定位时才可验证。"""
    return SourceDetailOut.model_validate(
        source_use_cases.task_source_detail(repos, workspace.id, task_id)
    )
