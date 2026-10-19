"""学期与课程 HTTP 路由。

学期与课程都限定在当前工作空间；归档只切换标记，不删除或改写
关联课程、任务、进度与日程，因此归档后仍可按需查询和恢复。
"""

from dataclasses import replace
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from campusflow.api.deps import get_current_workspace, get_repositories
from campusflow.api.schemas import (
    CourseCreate,
    CourseOut,
    CourseUpdate,
    SemesterArchive,
    SemesterCreate,
    SemesterOut,
)
from campusflow.application import courses as use_cases
from campusflow.application.ports.repositories import Repositories, WorkspaceData

router = APIRouter(tags=["courses"])

CurrentWorkspace = Annotated[WorkspaceData, Depends(get_current_workspace)]
Repos = Annotated[Repositories, Depends(get_repositories)]


@router.get("/semesters", response_model=list[SemesterOut])
def list_semesters(
    workspace: CurrentWorkspace,
    repos: Repos,
    include_archived: Annotated[bool, Query()] = False,
) -> list[SemesterOut]:
    """默认隐藏已归档学期；归档学期的历史任务与课程并未删除。"""
    return [
        SemesterOut.model_validate(s)
        for s in use_cases.list_semesters(repos, workspace.id, include_archived=include_archived)
    ]


@router.post("/semesters", response_model=SemesterOut, status_code=201)
def create_semester(
    payload: SemesterCreate, workspace: CurrentWorkspace, repos: Repos
) -> SemesterOut:
    semester = use_cases.create_semester(
        repos,
        workspace.id,
        name=payload.name,
        start_date=payload.start_date,
        end_date=payload.end_date,
    )
    return SemesterOut.model_validate(semester)


@router.patch("/semesters/{semester_id}", response_model=SemesterOut)
def archive_semester(
    semester_id: int, payload: SemesterArchive, workspace: CurrentWorkspace, repos: Repos
) -> SemesterOut:
    """只切换归档标记；不级联删除，传 archived=false 即可恢复。"""
    semester = use_cases.set_semester_archived(
        repos, workspace.id, semester_id, archived=payload.archived
    )
    return SemesterOut.model_validate(semester)


@router.get("/courses", response_model=list[CourseOut])
def list_courses(
    workspace: CurrentWorkspace,
    repos: Repos,
    semester_id: Annotated[int | None, Query()] = None,
) -> list[CourseOut]:
    return [
        CourseOut.model_validate(c)
        for c in use_cases.list_courses(repos, workspace.id, semester_id=semester_id)
    ]


@router.post("/courses", response_model=CourseOut, status_code=201)
def create_course(payload: CourseCreate, workspace: CurrentWorkspace, repos: Repos) -> CourseOut:
    """学期必须属于当前空间；课程挂在指定学期下，不会跨学期共用。"""
    course = use_cases.create_course(
        repos,
        workspace.id,
        semester_id=payload.semester_id,
        name=payload.name,
        code=payload.code,
        teacher=payload.teacher,
        class_name=payload.class_name,
    )
    return CourseOut.model_validate(course)


@router.patch("/courses/{course_id}", response_model=CourseOut)
def update_course(
    course_id: int, payload: CourseUpdate, workspace: CurrentWorkspace, repos: Repos
) -> CourseOut:
    # PATCH 语义：先取当前值再整体提交，未出现的字段保持原值；必填字段的
    # 显式 null 已在 CourseUpdate 中拒绝，这里不会把必填项覆盖成空。
    current = use_cases.get_course(repos, workspace.id, course_id)
    merged = replace(current, **payload.model_dump(exclude_unset=True))
    course = use_cases.update_course(
        repos,
        workspace.id,
        course_id,
        semester_id=merged.semester_id,
        name=merged.name,
        code=merged.code,
        teacher=merged.teacher,
        class_name=merged.class_name,
    )
    return CourseOut.model_validate(course)
