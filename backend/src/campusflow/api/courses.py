"""学期与课程 HTTP 路由。"""

from dataclasses import replace
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from campusflow.api.deps import get_default_workspace_id, get_repositories
from campusflow.api.schemas import (
    CourseCreate,
    CourseOut,
    CourseUpdate,
    SemesterArchive,
    SemesterCreate,
    SemesterOut,
)
from campusflow.application import courses as use_cases
from campusflow.application.ports.repositories import Repositories

router = APIRouter(tags=["courses"])

WorkspaceId = Annotated[int, Depends(get_default_workspace_id)]
Repos = Annotated[Repositories, Depends(get_repositories)]


@router.get("/semesters", response_model=list[SemesterOut])
def list_semesters(
    workspace_id: WorkspaceId,
    repos: Repos,
    include_archived: Annotated[bool, Query()] = False,
) -> list[SemesterOut]:
    return [
        SemesterOut.model_validate(s)
        for s in use_cases.list_semesters(repos, workspace_id, include_archived=include_archived)
    ]


@router.post("/semesters", response_model=SemesterOut, status_code=201)
def create_semester(
    payload: SemesterCreate, workspace_id: WorkspaceId, repos: Repos
) -> SemesterOut:
    semester = use_cases.create_semester(
        repos,
        workspace_id,
        name=payload.name,
        start_date=payload.start_date,
        end_date=payload.end_date,
    )
    return SemesterOut.model_validate(semester)


@router.patch("/semesters/{semester_id}", response_model=SemesterOut)
def archive_semester(semester_id: int, payload: SemesterArchive, repos: Repos) -> SemesterOut:
    semester = use_cases.set_semester_archived(repos, semester_id, archived=payload.archived)
    return SemesterOut.model_validate(semester)


@router.get("/courses", response_model=list[CourseOut])
def list_courses(
    workspace_id: WorkspaceId,
    repos: Repos,
    semester_id: Annotated[int | None, Query()] = None,
) -> list[CourseOut]:
    return [
        CourseOut.model_validate(c)
        for c in use_cases.list_courses(repos, workspace_id, semester_id=semester_id)
    ]


@router.post("/courses", response_model=CourseOut, status_code=201)
def create_course(payload: CourseCreate, workspace_id: WorkspaceId, repos: Repos) -> CourseOut:
    course = use_cases.create_course(
        repos,
        workspace_id,
        semester_id=payload.semester_id,
        name=payload.name,
        code=payload.code,
        teacher=payload.teacher,
        class_name=payload.class_name,
    )
    return CourseOut.model_validate(course)


@router.patch("/courses/{course_id}", response_model=CourseOut)
def update_course(course_id: int, payload: CourseUpdate, repos: Repos) -> CourseOut:
    current = use_cases.get_course(repos, course_id)
    merged = replace(current, **payload.model_dump(exclude_unset=True))
    course = use_cases.update_course(
        repos,
        course_id,
        semester_id=merged.semester_id,
        name=merged.name,
        code=merged.code,
        teacher=merged.teacher,
        class_name=merged.class_name,
    )
    return CourseOut.model_validate(course)
