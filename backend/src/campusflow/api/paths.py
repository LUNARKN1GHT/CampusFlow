"""M8 课程路径 HTTP 路由：培养方案与个人方案（C002 起）。"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from campusflow.api.deps import get_current_workspace, get_repositories
from campusflow.application import course_paths as use_cases
from campusflow.application.ports.repositories import Repositories, WorkspaceData

router = APIRouter(tags=["course-paths"])

CurrentWorkspace = Annotated[WorkspaceData, Depends(get_current_workspace)]
Repos = Annotated[Repositories, Depends(get_repositories)]


class CurriculumPlanCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    major: str = Field(min_length=1, max_length=100)
    grade_year: int
    version_label: str = Field(min_length=1, max_length=100)
    credit_limit_per_term: int | None = Field(default=None, gt=0)


class CurriculumPlanUpdate(CurriculumPlanCreate):
    pass


class CurriculumPlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    major: str
    grade_year: int
    version_label: str
    credit_limit_per_term: int | None
    source_material_id: int | None
    source_version_id: int | None
    created_at: datetime
    updated_at: datetime


class CurriculumCourseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    code: str | None = Field(default=None, max_length=50)
    credits: float | None = Field(default=None, ge=0, le=100)
    offered_terms: list[str] = Field(default_factory=list)


class CurriculumCourseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    code: str | None = Field(default=None, max_length=50)
    credits: float | None = Field(default=None, ge=0, le=100)
    offered_terms: list[str] | None = None


class CurriculumCourseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    curriculum_plan_id: int
    name: str
    code: str | None
    credits: float | None
    offered_terms: list[str]
    source_candidate_id: int | None
    created_at: datetime
    updated_at: datetime


class CourseRelationCreate(BaseModel):
    from_course_id: int = Field(gt=0)
    to_course_id: int = Field(gt=0)
    relation_type: str
    condition_text: str | None = Field(default=None, max_length=1000)


class CourseRelationUpdate(BaseModel):
    relation_type: str
    condition_text: str | None = Field(default=None, max_length=1000)


class CourseRelationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    curriculum_plan_id: int
    from_course_id: int
    to_course_id: int
    relation_type: str
    condition_text: str | None
    source_candidate_id: int | None
    created_by: str
    created_at: datetime
    updated_at: datetime


class LearnerProfileUpdate(BaseModel):
    major: str | None = Field(default=None, max_length=100)
    grade_year: int | None = None
    curriculum_plan_id: int | None = None


class LearnerProfileOut(BaseModel):
    workspace_id: int
    major: str | None
    grade_year: int | None
    curriculum_plan_id: int | None
    missing_fields: list[str]
    current_plan: CurriculumPlanOut | None


class SwitchPreviewOut(BaseModel):
    current_plan: CurriculumPlanOut | None
    target_plan: CurriculumPlanOut
    relation_count: int
    course_count: int
    study_record_count: int
    note: str


def _profile_out(view: use_cases.ProfileView) -> LearnerProfileOut:
    return LearnerProfileOut(
        workspace_id=view.profile.workspace_id,
        major=view.profile.major,
        grade_year=view.profile.grade_year,
        curriculum_plan_id=view.profile.curriculum_plan_id,
        missing_fields=list(view.missing_fields),
        current_plan=(
            None
            if view.current_plan is None
            else CurriculumPlanOut.model_validate(view.current_plan)
        ),
    )


@router.get("/curriculum-plans", response_model=list[CurriculumPlanOut])
def list_plans(workspace: CurrentWorkspace, repos: Repos) -> list[CurriculumPlanOut]:
    return [
        CurriculumPlanOut.model_validate(plan)
        for plan in use_cases.list_plans(repos, workspace.id)
    ]


@router.post("/curriculum-plans", response_model=CurriculumPlanOut, status_code=201)
def create_plan(
    payload: CurriculumPlanCreate, workspace: CurrentWorkspace, repos: Repos
) -> CurriculumPlanOut:
    plan = use_cases.create_plan(repos, workspace.id, **payload.model_dump())
    return CurriculumPlanOut.model_validate(plan)


@router.get("/curriculum-plans/{plan_id}", response_model=CurriculumPlanOut)
def get_plan(plan_id: int, workspace: CurrentWorkspace, repos: Repos) -> CurriculumPlanOut:
    return CurriculumPlanOut.model_validate(
        use_cases.get_plan(repos, workspace.id, plan_id)
    )


@router.put("/curriculum-plans/{plan_id}", response_model=CurriculumPlanOut)
def update_plan(
    plan_id: int,
    payload: CurriculumPlanUpdate,
    workspace: CurrentWorkspace,
    repos: Repos,
) -> CurriculumPlanOut:
    plan = use_cases.update_plan(repos, workspace.id, plan_id, **payload.model_dump())
    return CurriculumPlanOut.model_validate(plan)


@router.get(
    "/curriculum-plans/{plan_id}/courses", response_model=list[CurriculumCourseOut]
)
def list_plan_courses(
    plan_id: int, workspace: CurrentWorkspace, repos: Repos
) -> list[CurriculumCourseOut]:
    return [
        CurriculumCourseOut.model_validate(course)
        for course in use_cases.list_courses(repos, workspace.id, plan_id)
    ]


@router.post(
    "/curriculum-plans/{plan_id}/courses",
    response_model=CurriculumCourseOut,
    status_code=201,
)
def create_plan_course(
    plan_id: int,
    payload: CurriculumCourseCreate,
    workspace: CurrentWorkspace,
    repos: Repos,
) -> CurriculumCourseOut:
    course = use_cases.create_course(
        repos, workspace.id, plan_id, **payload.model_dump()
    )
    return CurriculumCourseOut.model_validate(course)


@router.patch(
    "/curriculum-plans/{plan_id}/courses/{course_id}",
    response_model=CurriculumCourseOut,
)
def update_plan_course(
    plan_id: int,
    course_id: int,
    payload: CurriculumCourseUpdate,
    workspace: CurrentWorkspace,
    repos: Repos,
) -> CurriculumCourseOut:
    current = use_cases.get_course(repos, workspace.id, plan_id, course_id)
    merged = {
        "name": payload.name if payload.name is not None else current.name,
        "code": payload.code if "code" in payload.model_fields_set else current.code,
        "credits": (
            payload.credits if "credits" in payload.model_fields_set else current.credits
        ),
        "offered_terms": (
            payload.offered_terms
            if payload.offered_terms is not None
            else current.offered_terms
        ),
    }
    course = use_cases.update_course(repos, workspace.id, plan_id, course_id, **merged)
    return CurriculumCourseOut.model_validate(course)


@router.delete("/curriculum-plans/{plan_id}/courses/{course_id}", status_code=204)
def delete_plan_course(
    plan_id: int, course_id: int, workspace: CurrentWorkspace, repos: Repos
) -> Response:
    use_cases.delete_course(repos, workspace.id, plan_id, course_id)
    return Response(status_code=204)


@router.get(
    "/curriculum-plans/{plan_id}/relations", response_model=list[CourseRelationOut]
)
def list_plan_relations(
    plan_id: int,
    workspace: CurrentWorkspace,
    repos: Repos,
    relation_type: Annotated[str | None, Query()] = None,
) -> list[CourseRelationOut]:
    return [
        CourseRelationOut.model_validate(relation)
        for relation in use_cases.list_relations(
            repos, workspace.id, plan_id, relation_type=relation_type
        )
    ]


@router.post(
    "/curriculum-plans/{plan_id}/relations",
    response_model=CourseRelationOut,
    status_code=201,
)
def create_plan_relation(
    plan_id: int,
    payload: CourseRelationCreate,
    workspace: CurrentWorkspace,
    repos: Repos,
) -> CourseRelationOut:
    relation = use_cases.create_relation(
        repos, workspace.id, plan_id, **payload.model_dump()
    )
    return CourseRelationOut.model_validate(relation)


@router.patch(
    "/curriculum-plans/{plan_id}/relations/{relation_id}",
    response_model=CourseRelationOut,
)
def update_plan_relation(
    plan_id: int,
    relation_id: int,
    payload: CourseRelationUpdate,
    workspace: CurrentWorkspace,
    repos: Repos,
) -> CourseRelationOut:
    relation = use_cases.update_relation(
        repos, workspace.id, plan_id, relation_id, **payload.model_dump()
    )
    return CourseRelationOut.model_validate(relation)


@router.delete("/curriculum-plans/{plan_id}/relations/{relation_id}", status_code=204)
def delete_plan_relation(
    plan_id: int, relation_id: int, workspace: CurrentWorkspace, repos: Repos
) -> Response:
    use_cases.delete_relation(repos, workspace.id, plan_id, relation_id)
    return Response(status_code=204)


@router.get("/learning-profile", response_model=LearnerProfileOut)
def get_profile(workspace: CurrentWorkspace, repos: Repos) -> LearnerProfileOut:
    return _profile_out(use_cases.get_profile(repos, workspace.id))


@router.put("/learning-profile", response_model=LearnerProfileOut)
def update_profile(
    payload: LearnerProfileUpdate, workspace: CurrentWorkspace, repos: Repos
) -> LearnerProfileOut:
    return _profile_out(
        use_cases.update_profile(repos, workspace.id, **payload.model_dump())
    )


@router.get("/learning-profile/switch-preview", response_model=SwitchPreviewOut)
def preview_switch(
    workspace: CurrentWorkspace,
    repos: Repos,
    plan_id: Annotated[int, Query(ge=1)],
) -> SwitchPreviewOut:
    preview = use_cases.preview_switch_plan(repos, workspace.id, plan_id)
    return SwitchPreviewOut(
        current_plan=(
            None
            if preview.current_plan is None
            else CurriculumPlanOut.model_validate(preview.current_plan)
        ),
        target_plan=CurriculumPlanOut.model_validate(preview.target_plan),
        relation_count=preview.relation_count,
        course_count=preview.course_count,
        study_record_count=preview.study_record_count,
        note=preview.note,
    )
