"""M8 课程路径 HTTP 路由：培养方案与个人方案（C002 起）。

方案按专业、年级与版本隔离，课程、三类关系、别名和修读记录分别保存。
图分析、顺序、下一步与学期建议都只读；模型提取结果先保存为待核对候选。
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from campusflow.api.deps import (
    get_current_workspace,
    get_repositories,
    get_text_generator,
)
from campusflow.application import course_paths as use_cases
from campusflow.application import curriculum_extraction
from campusflow.application.candidates import save_candidates
from campusflow.application.ports.generation import TextGenerator
from campusflow.application.ports.repositories import Repositories, WorkspaceData

router = APIRouter(tags=["course-paths"])

CurrentWorkspace = Annotated[WorkspaceData, Depends(get_current_workspace)]
Repos = Annotated[Repositories, Depends(get_repositories)]
Generator = Annotated[TextGenerator, Depends(get_text_generator)]


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


class CourseAliasCreate(BaseModel):
    curriculum_course_id: int = Field(gt=0)
    alias: str = Field(min_length=1, max_length=200)
    class_name: str | None = Field(default=None, max_length=100)
    semester_id: int | None = None


class CourseAliasOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    curriculum_plan_id: int
    curriculum_course_id: int
    alias: str
    class_name: str | None
    semester_id: int | None
    created_at: datetime


class AliasResolveOut(BaseModel):
    status: str
    matches: list[CurriculumCourseOut]
    matched_by: str | None
    note: str


class CurriculumExtractionRequest(BaseModel):
    material_id: int = Field(gt=0)
    version_no: int = Field(gt=0)
    include_courses: bool = True
    include_relations: bool = True


class ExtractedCandidateOut(BaseModel):
    id: int
    kind: str
    review_status: str


class CurriculumExtractionOut(BaseModel):
    candidates: list[ExtractedCandidateOut]
    pending_review: bool = True


class StudyRecordCreate(BaseModel):
    curriculum_course_id: int = Field(gt=0)
    semester_id: int | None = None
    status: str
    # 成绩与已得学分只接受用户填写；不根据课程学分推断。
    grade: str | None = Field(default=None, max_length=100)
    credits_earned: float | None = Field(default=None, ge=0, le=100)
    note: str | None = Field(default=None, max_length=500)


class StudyRecordUpdate(BaseModel):
    semester_id: int | None = None
    status: str | None = None
    grade: str | None = Field(default=None, max_length=100)
    credits_earned: float | None = Field(default=None, ge=0, le=100)
    note: str | None = Field(default=None, max_length=500)


class StudyRecordOut(BaseModel):
    id: int
    curriculum_plan_id: int
    curriculum_course_id: int
    semester_id: int | None
    semester_name: str | None
    status: str
    grade: str | None
    credits_earned: float | None
    note: str | None
    created_at: datetime
    updated_at: datetime


class StudyStatusEntryOut(BaseModel):
    curriculum_course_id: int
    course_name: str
    course_code: str | None
    status: str
    record_count: int
    repeat_record_count: int
    completed_semester_ids: list[int]
    in_progress_semester_ids: list[int]
    planned_semester_ids: list[int]
    records: list[StudyRecordOut]
    note: str


class StudyStatusOut(BaseModel):
    plan_id: int
    courses: list[StudyStatusEntryOut]
    completed_count: int
    in_progress_count: int
    planned_count: int
    unknown_count: int
    note: str


class GraphNodeOut(BaseModel):
    id: int
    name: str
    code: str | None
    credits: float | None
    offered_terms: list[str]
    study_status: str
    record_count: int


class GraphEvidenceOut(BaseModel):
    chunk_id: int
    quote: str
    field: str
    version_id: int
    material_id: int | None


class GraphEdgeOut(BaseModel):
    id: int
    from_course_id: int
    to_course_id: int
    relation_type: str
    condition_text: str | None
    source_candidate_id: int | None
    evidence: list[GraphEvidenceOut]


class GraphProblemOut(BaseModel):
    code: str
    message: str
    course_ids: list[int]
    relation_ids: list[int]
    path: list[int]
    candidate_ids: list[int]


class CourseGraphOut(BaseModel):
    plan_id: int
    plan_version: str
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]
    cycles: list[GraphProblemOut]
    conflicts: list[GraphProblemOut]
    missing: list[GraphProblemOut]
    pending: list[GraphProblemOut]
    complete: bool
    limitations: list[str]


class CourseOrderOut(BaseModel):
    """C010/C012：强制先修层次；环或缺项时层次为空并给出阻塞问题。"""

    plan_id: int
    plan_version: str
    complete: bool
    blocked: bool
    levels: list[list[int]]
    alternatives: bool
    problems: list[GraphProblemOut]
    limitations: list[str]


class NextCourseOut(BaseModel):
    course_id: int
    name: str
    study_status: str
    available: bool
    unmet: list[str]
    recommended: list[str]


class NextCoursesOut(BaseModel):
    plan_id: int
    plan_version: str
    complete: bool
    blocked: bool
    courses: list[NextCourseOut]
    limitations: list[str]


class SemesterPlanRequest(BaseModel):
    credit_limit: int | None = Field(default=None, ge=1, le=60)
    start_season: str = Field(default="fall", pattern="^(fall|spring)$")
    max_terms: int | None = Field(default=None, ge=1, le=16)


class SuggestedTermOut(BaseModel):
    index: int
    season: str
    course_ids: list[int]
    credits_known: float
    credits_unknown: int
    warnings: list[str]


class SemesterPlanOut(BaseModel):
    plan_id: int
    plan_version: str
    complete: bool
    blocked: bool
    credit_limit: int | None
    terms: list[SuggestedTermOut]
    unplaced: list[dict]
    limitations: list[str]
    assumptions: list[str]
    graduation_audit: bool


def _problem_out(problem) -> GraphProblemOut:
    return GraphProblemOut(
        code=problem.code,
        message=problem.message,
        course_ids=list(problem.course_ids),
        relation_ids=list(problem.relation_ids),
        path=list(problem.path),
        candidate_ids=list(problem.candidate_ids),
    )


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


def _study_record_out(view: use_cases.StudyRecordView) -> StudyRecordOut:
    record = view.record
    return StudyRecordOut(
        id=record.id,
        curriculum_plan_id=view.curriculum_plan_id,
        curriculum_course_id=record.curriculum_course_id,
        semester_id=record.semester_id,
        semester_name=view.semester_name,
        status=record.status,
        grade=record.grade,
        credits_earned=record.credits_earned,
        note=record.note,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


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
        CurriculumPlanOut.model_validate(plan) for plan in use_cases.list_plans(repos, workspace.id)
    ]


@router.post("/curriculum-plans", response_model=CurriculumPlanOut, status_code=201)
def create_plan(
    payload: CurriculumPlanCreate, workspace: CurrentWorkspace, repos: Repos
) -> CurriculumPlanOut:
    """方案按专业、年级与版本标识；同名课程在不同方案中不会混接。"""
    plan = use_cases.create_plan(repos, workspace.id, **payload.model_dump())
    return CurriculumPlanOut.model_validate(plan)


@router.get("/curriculum-plans/{plan_id}", response_model=CurriculumPlanOut)
def get_plan(plan_id: int, workspace: CurrentWorkspace, repos: Repos) -> CurriculumPlanOut:
    return CurriculumPlanOut.model_validate(use_cases.get_plan(repos, workspace.id, plan_id))


@router.put("/curriculum-plans/{plan_id}", response_model=CurriculumPlanOut)
def update_plan(
    plan_id: int,
    payload: CurriculumPlanUpdate,
    workspace: CurrentWorkspace,
    repos: Repos,
) -> CurriculumPlanOut:
    plan = use_cases.update_plan(repos, workspace.id, plan_id, **payload.model_dump())
    return CurriculumPlanOut.model_validate(plan)


@router.get("/curriculum-plans/{plan_id}/courses", response_model=list[CurriculumCourseOut])
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
    course = use_cases.create_course(repos, workspace.id, plan_id, **payload.model_dump())
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
    # PATCH 语义：区分“未提交”和“显式 null”，未提交的字段保持原值。
    current = use_cases.get_course(repos, workspace.id, plan_id, course_id)
    merged = {
        "name": payload.name if payload.name is not None else current.name,
        "code": payload.code if "code" in payload.model_fields_set else current.code,
        "credits": (payload.credits if "credits" in payload.model_fields_set else current.credits),
        "offered_terms": (
            payload.offered_terms if payload.offered_terms is not None else current.offered_terms
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


@router.get("/curriculum-plans/{plan_id}/relations", response_model=list[CourseRelationOut])
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
    """关系类型只接受 prerequisite/corequisite/recommended，两端须同属该方案。"""
    relation = use_cases.create_relation(repos, workspace.id, plan_id, **payload.model_dump())
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


@router.get("/curriculum-plans/{plan_id}/aliases", response_model=list[CourseAliasOut])
def list_plan_aliases(
    plan_id: int, workspace: CurrentWorkspace, repos: Repos
) -> list[CourseAliasOut]:
    return [
        CourseAliasOut.model_validate(alias)
        for alias in use_cases.list_aliases(repos, workspace.id, plan_id)
    ]


@router.post(
    "/curriculum-plans/{plan_id}/aliases",
    response_model=CourseAliasOut,
    status_code=201,
)
def create_plan_alias(
    plan_id: int,
    payload: CourseAliasCreate,
    workspace: CurrentWorkspace,
    repos: Repos,
) -> CourseAliasOut:
    alias = use_cases.create_alias(repos, workspace.id, plan_id, **payload.model_dump())
    return CourseAliasOut.model_validate(alias)


@router.delete("/curriculum-plans/{plan_id}/aliases/{alias_id}", status_code=204)
def delete_plan_alias(
    plan_id: int, alias_id: int, workspace: CurrentWorkspace, repos: Repos
) -> Response:
    use_cases.delete_alias(repos, workspace.id, plan_id, alias_id)
    return Response(status_code=204)


@router.get("/curriculum-plans/{plan_id}/aliases/resolve", response_model=AliasResolveOut)
def resolve_plan_alias(
    plan_id: int,
    workspace: CurrentWorkspace,
    repos: Repos,
    name: Annotated[str, Query(min_length=1, max_length=200)],
    semester_id: Annotated[int | None, Query(ge=1)] = None,
    class_name: Annotated[str | None, Query(max_length=100)] = None,
) -> AliasResolveOut:
    """唯一匹配也只是建议，不自动合并；同名多班或无法唯一确定时返回 ambiguous。"""
    resolved = use_cases.resolve_course_name(
        repos,
        workspace.id,
        plan_id,
        name=name,
        semester_id=semester_id,
        class_name=class_name,
    )
    return AliasResolveOut(
        status=resolved.status,
        matches=[CurriculumCourseOut.model_validate(course) for course in resolved.matches],
        matched_by=resolved.matched_by,
        note=resolved.note,
    )


@router.get("/curriculum-plans/{plan_id}/graph", response_model=CourseGraphOut)
def get_plan_graph(plan_id: int, workspace: CurrentWorkspace, repos: Repos) -> CourseGraphOut:
    """只把已确认关系放入图；成环、缺项、来源矛盾与待核对分别列出。

    complete=false 表示当前资料不足以给出完整顺序，而不是“没有依赖”。
    """
    view = use_cases.build_plan_graph(repos, workspace.id, plan_id)
    graph = view.graph
    return CourseGraphOut(
        plan_id=view.plan.id,
        plan_version=view.plan.version_label,
        nodes=[
            GraphNodeOut(
                id=course.id,
                name=course.name,
                code=course.code,
                credits=course.credits,
                offered_terms=list(course.offered_terms),
                study_status=course.study_status,
                record_count=course.record_count,
            )
            for course in graph.courses
        ],
        edges=[
            GraphEdgeOut(
                id=relation.id,
                from_course_id=relation.from_course_id,
                to_course_id=relation.to_course_id,
                relation_type=relation.relation_type.value,
                condition_text=relation.condition_text,
                source_candidate_id=relation.source_candidate_id,
                evidence=[
                    GraphEvidenceOut(**item) for item in view.edge_evidence.get(relation.id, ())
                ],
            )
            for relation in graph.relations
        ],
        cycles=[_problem_out(item) for item in graph.cycles],
        conflicts=[_problem_out(item) for item in graph.conflicts],
        missing=[_problem_out(item) for item in graph.missing],
        pending=[_problem_out(item) for item in graph.pending],
        complete=graph.complete,
        limitations=list(graph.limitations),
    )


@router.get("/curriculum-plans/{plan_id}/order", response_model=CourseOrderOut)
def get_plan_order(plan_id: int, workspace: CurrentWorkspace, repos: Repos) -> CourseOrderOut:
    """C010/C012：只对强制先修分层；成环或缺项时不返回伪造的完整顺序。"""
    analysis = use_cases.analyze_plan(repos, workspace.id, plan_id)
    return CourseOrderOut(
        plan_id=analysis.plan.id,
        plan_version=analysis.plan.version_label,
        complete=analysis.graph.graph.complete,
        blocked=not analysis.graph.graph.complete,
        levels=[list(level) for level in analysis.order.levels],
        alternatives=analysis.order.alternatives,
        problems=[_problem_out(item) for item in analysis.order.problems],
        limitations=list(analysis.graph.graph.limitations),
    )


@router.get("/curriculum-plans/{plan_id}/next", response_model=NextCoursesOut)
def get_next_courses(plan_id: int, workspace: CurrentWorkspace, repos: Repos) -> NextCoursesOut:
    """C013：按已修/在修状态列出下一步；建议先学单独返回，不当作硬阻塞。

    available 只由强制先修与并修条件决定，recommended 中的课程不阻塞。
    """
    analysis = use_cases.analyze_plan(repos, workspace.id, plan_id)
    return NextCoursesOut(
        plan_id=analysis.plan.id,
        plan_version=analysis.plan.version_label,
        complete=analysis.graph.graph.complete,
        blocked=not analysis.graph.graph.complete,
        courses=[
            NextCourseOut(
                course_id=item.course_id,
                name=item.name,
                study_status=item.study_status,
                available=item.available,
                unmet=list(item.unmet),
                recommended=list(item.recommended),
            )
            for item in analysis.next_courses
        ],
        limitations=list(analysis.graph.graph.limitations),
    )


@router.post("/curriculum-plans/{plan_id}/semester-plan", response_model=SemesterPlanOut)
def get_semester_plan(
    plan_id: int,
    payload: SemesterPlanRequest,
    workspace: CurrentWorkspace,
    repos: Repos,
) -> SemesterPlanOut:
    """C014：按已知开课季节与学分上限辅助排学期；不代替选课或毕业审核。

    credit_limit 缺省取方案设置；开课季节或学分缺失时降级并写入 assumptions，
    graduation_audit 恒为 false。
    """
    analysis = use_cases.analyze_plan(
        repos,
        workspace.id,
        plan_id,
        credit_limit=payload.credit_limit,
        start_season=payload.start_season,
        max_terms=payload.max_terms,
    )
    suggestion = analysis.semester
    effective_limit = (
        payload.credit_limit
        if payload.credit_limit is not None
        else analysis.plan.credit_limit_per_term
    )
    return SemesterPlanOut(
        plan_id=analysis.plan.id,
        plan_version=analysis.plan.version_label,
        complete=analysis.graph.graph.complete,
        blocked=not analysis.graph.graph.complete,
        credit_limit=effective_limit,
        terms=[
            SuggestedTermOut(
                index=term.index,
                season=term.season,
                course_ids=list(term.course_ids),
                credits_known=term.credits_known,
                credits_unknown=term.credits_unknown,
                warnings=list(term.warnings),
            )
            for term in suggestion.terms
        ],
        unplaced=[dict(item) for item in suggestion.unplaced],
        limitations=list(suggestion.limitations),
        assumptions=list(suggestion.assumptions),
        graduation_audit=suggestion.graduation_audit,
    )


@router.post("/curriculum-plans/{plan_id}/extract", response_model=CurriculumExtractionOut)
def extract_curriculum(
    plan_id: int,
    payload: CurriculumExtractionRequest,
    workspace: CurrentWorkspace,
    repos: Repos,
    generator: Generator,
) -> CurriculumExtractionOut:
    """显式请求才调用模型；结果全部保存为待核对候选，不直接写正式课程或关系。

    include_courses/include_relations 可只提取其中一类；未确认的候选不参与图分析。
    """
    drafts = curriculum_extraction.extract_curriculum_drafts(
        repos,
        workspace.id,
        plan_id,
        payload.material_id,
        payload.version_no,
        generator,
        include_courses=payload.include_courses,
        include_relations=payload.include_relations,
    )
    saved = save_candidates(
        repos,
        workspace.id,
        payload.material_id,
        payload.version_no,
        drafts,
    )
    return CurriculumExtractionOut(
        candidates=[
            ExtractedCandidateOut(
                id=candidate.id,
                kind=candidate.draft.kind.value,
                review_status=candidate.review_status.value,
            )
            for candidate in saved
        ],
        note="提取结果已保存为待核对候选；确认前不会写入正式课程或依赖关系。",
    )


@router.get("/curriculum-plans/{plan_id}/study-records", response_model=list[StudyRecordOut])
def list_plan_study_records(
    plan_id: int, workspace: CurrentWorkspace, repos: Repos
) -> list[StudyRecordOut]:
    return [
        _study_record_out(view)
        for view in use_cases.list_study_records(repos, workspace.id, plan_id)
    ]


@router.post(
    "/curriculum-plans/{plan_id}/study-records",
    response_model=StudyRecordOut,
    status_code=201,
)
def create_plan_study_record(
    plan_id: int,
    payload: StudyRecordCreate,
    workspace: CurrentWorkspace,
    repos: Repos,
) -> StudyRecordOut:
    """修读状态只接受 completed/in_progress/planned；成绩与学分不由课程学分推断。"""
    view = use_cases.create_study_record(repos, workspace.id, plan_id, **payload.model_dump())
    return _study_record_out(view)


@router.patch(
    "/curriculum-plans/{plan_id}/study-records/{record_id}",
    response_model=StudyRecordOut,
)
def update_plan_study_record(
    plan_id: int,
    record_id: int,
    payload: StudyRecordUpdate,
    workspace: CurrentWorkspace,
    repos: Repos,
) -> StudyRecordOut:
    current = use_cases.get_study_record(repos, workspace.id, plan_id, record_id)
    merged = {
        "semester_id": (
            payload.semester_id
            if "semester_id" in payload.model_fields_set
            else current.record.semester_id
        ),
        "status": (payload.status if payload.status is not None else current.record.status),
        # 未提交的字段保持原值；修改状态不会补造成绩或已得学分。
        "grade": (payload.grade if "grade" in payload.model_fields_set else current.record.grade),
        "credits_earned": (
            payload.credits_earned
            if "credits_earned" in payload.model_fields_set
            else current.record.credits_earned
        ),
        "note": (payload.note if "note" in payload.model_fields_set else current.record.note),
    }
    view = use_cases.update_study_record(repos, workspace.id, plan_id, record_id, **merged)
    return _study_record_out(view)


@router.delete("/curriculum-plans/{plan_id}/study-records/{record_id}", status_code=204)
def delete_plan_study_record(
    plan_id: int, record_id: int, workspace: CurrentWorkspace, repos: Repos
) -> Response:
    use_cases.delete_study_record(repos, workspace.id, plan_id, record_id)
    return Response(status_code=204)


@router.get("/curriculum-plans/{plan_id}/study-status", response_model=StudyStatusOut)
def get_plan_study_status(
    plan_id: int, workspace: CurrentWorkspace, repos: Repos
) -> StudyStatusOut:
    """同一课程可有多条修读记录（重修）；汇总状态由全部记录聚合，重复记录单独计数。"""
    summary = use_cases.get_study_status(repos, workspace.id, plan_id)
    return StudyStatusOut(
        plan_id=summary.plan_id,
        courses=[
            StudyStatusEntryOut(
                curriculum_course_id=entry.course.id,
                course_name=entry.course.name,
                course_code=entry.course.code,
                status=entry.status,
                record_count=len(entry.records),
                repeat_record_count=max(0, len(entry.records) - 1),
                completed_semester_ids=list(entry.completed_semester_ids),
                in_progress_semester_ids=list(entry.in_progress_semester_ids),
                planned_semester_ids=list(entry.planned_semester_ids),
                records=[_study_record_out(view) for view in entry.records],
                note=entry.note,
            )
            for entry in summary.entries
        ],
        completed_count=summary.counts["completed"],
        in_progress_count=summary.counts["in_progress"],
        planned_count=summary.counts["planned"],
        unknown_count=summary.counts["unknown"],
        note=summary.note,
    )


@router.get("/learning-profile", response_model=LearnerProfileOut)
def get_profile(workspace: CurrentWorkspace, repos: Repos) -> LearnerProfileOut:
    """缺失的专业、年级或方案列在 missing_fields，不自动猜测当前方案。"""
    return _profile_out(use_cases.get_profile(repos, workspace.id))


@router.put("/learning-profile", response_model=LearnerProfileOut)
def update_profile(
    payload: LearnerProfileUpdate, workspace: CurrentWorkspace, repos: Repos
) -> LearnerProfileOut:
    """专业/年级与所选方案不一致时拒绝，避免跨年级混用课程关系。"""
    return _profile_out(use_cases.update_profile(repos, workspace.id, **payload.model_dump()))


@router.get("/learning-profile/switch-preview", response_model=SwitchPreviewOut)
def preview_switch(
    workspace: CurrentWorkspace,
    repos: Repos,
    plan_id: Annotated[int, Query(ge=1)],
) -> SwitchPreviewOut:
    """只读展示切换影响；原方案数据不删除、不迁移，真正切换走 PUT /learning-profile。"""
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
