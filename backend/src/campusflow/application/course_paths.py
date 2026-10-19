"""M8：培养方案、个人方案与后续课程路径用例。"""

from dataclasses import dataclass

from campusflow.application.ports.repositories import (
    CourseRelationData,
    CurriculumCourseData,
    CurriculumPlanData,
    LearnerProfileData,
    Repositories,
)
from campusflow.domain.course_paths import (
    profile_missing_fields,
    validate_course_fields,
    validate_plan_fields,
    validate_profile_fields,
    validate_relation_fields,
)
from campusflow.domain.errors import DomainError, NotFoundError


def list_plans(repos: Repositories, workspace_id: int) -> list[CurriculumPlanData]:
    return repos.paths.list_plans(workspace_id)


def get_plan(repos: Repositories, workspace_id: int, plan_id: int) -> CurriculumPlanData:
    plan = repos.paths.get_plan(plan_id)
    if plan is None or plan.workspace_id != workspace_id:
        raise NotFoundError("培养方案不存在")
    return plan


def create_plan(
    repos: Repositories,
    workspace_id: int,
    *,
    title: str,
    major: str,
    grade_year: int,
    version_label: str,
    credit_limit_per_term: int | None,
) -> CurriculumPlanData:
    validate_plan_fields(
        title=title,
        major=major,
        grade_year=grade_year,
        version_label=version_label,
        credit_limit_per_term=credit_limit_per_term,
    )
    try:
        plan = repos.paths.create_plan(
            workspace_id,
            title=title.strip(),
            major=major.strip(),
            grade_year=grade_year,
            version_label=version_label.strip(),
            credit_limit_per_term=credit_limit_per_term,
        )
        repos.uow.commit()
    except Exception:
        repos.uow.rollback()
        raise
    return plan


def update_plan(
    repos: Repositories,
    workspace_id: int,
    plan_id: int,
    *,
    title: str,
    major: str,
    grade_year: int,
    version_label: str,
    credit_limit_per_term: int | None,
) -> CurriculumPlanData:
    get_plan(repos, workspace_id, plan_id)
    validate_plan_fields(
        title=title,
        major=major,
        grade_year=grade_year,
        version_label=version_label,
        credit_limit_per_term=credit_limit_per_term,
    )
    try:
        plan = repos.paths.update_plan(
            plan_id,
            title=title.strip(),
            major=major.strip(),
            grade_year=grade_year,
            version_label=version_label.strip(),
            credit_limit_per_term=credit_limit_per_term,
        )
        if plan is None:
            raise NotFoundError("培养方案不存在")
        repos.uow.commit()
    except Exception:
        repos.uow.rollback()
        raise
    return plan


def list_courses(
    repos: Repositories, workspace_id: int, plan_id: int
) -> list[CurriculumCourseData]:
    get_plan(repos, workspace_id, plan_id)
    return repos.paths.list_courses(plan_id)


def get_course(
    repos: Repositories, workspace_id: int, plan_id: int, course_id: int
) -> CurriculumCourseData:
    get_plan(repos, workspace_id, plan_id)
    course = repos.paths.get_course(course_id)
    if course is None or course.workspace_id != workspace_id:
        raise NotFoundError("方案课程不存在")
    if course.curriculum_plan_id != plan_id:
        raise DomainError("课程不属于该培养方案")
    return course


def create_course(
    repos: Repositories,
    workspace_id: int,
    plan_id: int,
    *,
    name: str,
    code: str | None,
    credits: float | None,
    offered_terms: list[str],
) -> CurriculumCourseData:
    get_plan(repos, workspace_id, plan_id)
    normalized = validate_course_fields(
        name=name, code=code, credits=credits, offered_terms=offered_terms
    )
    try:
        course = repos.paths.create_course(
            workspace_id,
            plan_id,
            name=name.strip(),
            code=code.strip() if code is not None else None,
            credits=float(credits) if credits is not None else None,
            offered_terms=normalized,
        )
        repos.uow.commit()
    except Exception:
        repos.uow.rollback()
        raise
    return course


def update_course(
    repos: Repositories,
    workspace_id: int,
    plan_id: int,
    course_id: int,
    *,
    name: str,
    code: str | None,
    credits: float | None,
    offered_terms: list[str],
) -> CurriculumCourseData:
    get_course(repos, workspace_id, plan_id, course_id)
    normalized = validate_course_fields(
        name=name, code=code, credits=credits, offered_terms=offered_terms
    )
    try:
        course = repos.paths.update_course(
            course_id,
            name=name.strip(),
            code=code.strip() if code is not None else None,
            credits=float(credits) if credits is not None else None,
            offered_terms=normalized,
        )
        if course is None:
            raise NotFoundError("方案课程不存在")
        repos.uow.commit()
    except Exception:
        repos.uow.rollback()
        raise
    return course


def delete_course(
    repos: Repositories, workspace_id: int, plan_id: int, course_id: int
) -> None:
    get_course(repos, workspace_id, plan_id, course_id)
    try:
        if not repos.paths.delete_course(course_id):
            raise NotFoundError("方案课程不存在")
        repos.uow.commit()
    except Exception:
        repos.uow.rollback()
        raise


def list_relations(
    repos: Repositories,
    workspace_id: int,
    plan_id: int,
    *,
    relation_type: str | None = None,
) -> list[CourseRelationData]:
    get_plan(repos, workspace_id, plan_id)
    if relation_type is not None:
        validate_relation_fields(relation_type=relation_type, condition_text=None)
    return repos.paths.list_relations(plan_id, relation_type=relation_type)


def get_relation(
    repos: Repositories, workspace_id: int, plan_id: int, relation_id: int
) -> CourseRelationData:
    get_plan(repos, workspace_id, plan_id)
    relation = repos.paths.get_relation(relation_id)
    if relation is None or relation.workspace_id != workspace_id:
        raise NotFoundError("课程关系不存在")
    if relation.curriculum_plan_id != plan_id:
        raise DomainError("课程关系不属于该培养方案")
    return relation


def create_relation(
    repos: Repositories,
    workspace_id: int,
    plan_id: int,
    *,
    from_course_id: int,
    to_course_id: int,
    relation_type: str,
    condition_text: str | None,
) -> CourseRelationData:
    get_plan(repos, workspace_id, plan_id)
    validate_relation_fields(relation_type=relation_type, condition_text=condition_text)
    # 两个端点都必须属于同一方案；不同方案的同名课程不会被混接。
    get_course(repos, workspace_id, plan_id, from_course_id)
    get_course(repos, workspace_id, plan_id, to_course_id)
    try:
        relation = repos.paths.create_relation(
            workspace_id,
            plan_id,
            from_course_id=from_course_id,
            to_course_id=to_course_id,
            relation_type=relation_type,
            condition_text=condition_text.strip() if condition_text else None,
        )
        repos.uow.commit()
    except Exception:
        repos.uow.rollback()
        raise
    return relation


def update_relation(
    repos: Repositories,
    workspace_id: int,
    plan_id: int,
    relation_id: int,
    *,
    relation_type: str,
    condition_text: str | None,
) -> CourseRelationData:
    get_relation(repos, workspace_id, plan_id, relation_id)
    validate_relation_fields(relation_type=relation_type, condition_text=condition_text)
    try:
        relation = repos.paths.update_relation(
            relation_id,
            relation_type=relation_type,
            condition_text=condition_text.strip() if condition_text else None,
        )
        if relation is None:
            raise NotFoundError("课程关系不存在")
        repos.uow.commit()
    except Exception:
        repos.uow.rollback()
        raise
    return relation


def delete_relation(
    repos: Repositories, workspace_id: int, plan_id: int, relation_id: int
) -> None:
    get_relation(repos, workspace_id, plan_id, relation_id)
    try:
        if not repos.paths.delete_relation(relation_id):
            raise NotFoundError("课程关系不存在")
        repos.uow.commit()
    except Exception:
        repos.uow.rollback()
        raise


@dataclass(frozen=True)
class ProfileView:
    profile: LearnerProfileData
    current_plan: CurriculumPlanData | None
    missing_fields: tuple[str, ...]


def get_profile(repos: Repositories, workspace_id: int) -> ProfileView:
    profile = repos.paths.get_profile(workspace_id)
    if profile is None:
        profile = LearnerProfileData(workspace_id, None, None, None, None)
    plan = None
    if profile.curriculum_plan_id is not None:
        plan = repos.paths.get_plan(profile.curriculum_plan_id)
        if plan is not None and plan.workspace_id != workspace_id:
            plan = None
    return ProfileView(
        profile,
        plan,
        profile_missing_fields(
            major=profile.major,
            grade_year=profile.grade_year,
            plan_id=plan.id if plan is not None else None,
        ),
    )


def update_profile(
    repos: Repositories,
    workspace_id: int,
    *,
    major: str | None,
    grade_year: int | None,
    curriculum_plan_id: int | None,
) -> ProfileView:
    """保存个人方案；专业/年级与所选方案不一致时拒绝，避免跨年级混用规则。"""
    plan = None
    if curriculum_plan_id is not None:
        plan = get_plan(repos, workspace_id, curriculum_plan_id)
    validate_profile_fields(
        major=major,
        grade_year=grade_year,
        plan_major=plan.major if plan else None,
        plan_grade_year=plan.grade_year if plan else None,
    )
    try:
        repos.paths.upsert_profile(
            workspace_id,
            major=major.strip() if major is not None else None,
            grade_year=grade_year,
            curriculum_plan_id=curriculum_plan_id,
        )
        repos.uow.commit()
    except Exception:
        repos.uow.rollback()
        raise
    return get_profile(repos, workspace_id)


@dataclass(frozen=True)
class SwitchPreview:
    current_plan: CurriculumPlanData | None
    target_plan: CurriculumPlanData
    relation_count: int
    course_count: int
    study_record_count: int
    note: str


def preview_switch_plan(
    repos: Repositories, workspace_id: int, target_plan_id: int
) -> SwitchPreview:
    """切换方案前展示将不再显示的当前方案数据；只读，不修改任何关系。"""
    current = get_profile(repos, workspace_id)
    target = get_plan(repos, workspace_id, target_plan_id)
    counts = repos.paths.plan_impact_counts(
        workspace_id,
        current.profile.curriculum_plan_id,
    )
    if current.profile.curriculum_plan_id == target_plan_id:
        note = "已经是当前方案，切换不会改变任何关系或修读记录。"
    else:
        note = (
            "切换后只显示目标方案内的课程关系与修读记录；"
            "原方案数据不会被删除，也不会自动迁移到新方案。"
        )
    return SwitchPreview(
        current.current_plan,
        target,
        counts.get("relations", 0),
        counts.get("courses", 0),
        counts.get("study_records", 0),
        note,
    )
