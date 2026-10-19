"""M8：培养方案、个人方案与后续课程路径用例。"""

from dataclasses import dataclass

from campusflow.application.ports.repositories import (
    CurriculumPlanData,
    LearnerProfileData,
    Repositories,
)
from campusflow.domain.course_paths import (
    profile_missing_fields,
    validate_plan_fields,
    validate_profile_fields,
)
from campusflow.domain.errors import NotFoundError


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
