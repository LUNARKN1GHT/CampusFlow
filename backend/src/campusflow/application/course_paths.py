"""M8：培养方案、个人方案与后续课程路径用例。"""

from dataclasses import dataclass

from campusflow.application.ports.repositories import (
    CourseAliasData,
    CourseRelationData,
    CourseStudyRecordData,
    CurriculumCourseData,
    CurriculumPlanData,
    LearnerProfileData,
    Repositories,
)
from campusflow.domain.course_paths import (
    UNKNOWN_STUDY_STATUS,
    aggregate_study_status,
    alias_scope_applies,
    normalize_course_name,
    profile_missing_fields,
    validate_alias_text,
    validate_course_fields,
    validate_plan_fields,
    validate_profile_fields,
    validate_relation_fields,
    validate_study_record_fields,
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


def delete_course(repos: Repositories, workspace_id: int, plan_id: int, course_id: int) -> None:
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


def delete_relation(repos: Repositories, workspace_id: int, plan_id: int, relation_id: int) -> None:
    get_relation(repos, workspace_id, plan_id, relation_id)
    try:
        if not repos.paths.delete_relation(relation_id):
            raise NotFoundError("课程关系不存在")
        repos.uow.commit()
    except Exception:
        repos.uow.rollback()
        raise


def list_aliases(repos: Repositories, workspace_id: int, plan_id: int) -> list[CourseAliasData]:
    get_plan(repos, workspace_id, plan_id)
    return repos.paths.list_aliases(plan_id)


def _require_semester_scope(
    repos: Repositories, workspace_id: int, semester_id: int | None
) -> None:
    if semester_id is None:
        return
    semester = repos.semesters.get(semester_id)
    if semester is None or semester.workspace_id != workspace_id:
        raise NotFoundError("学期不存在")


def create_alias(
    repos: Repositories,
    workspace_id: int,
    plan_id: int,
    *,
    curriculum_course_id: int,
    alias: str,
    class_name: str | None,
    semester_id: int | None,
) -> CourseAliasData:
    get_course(repos, workspace_id, plan_id, curriculum_course_id)
    validate_alias_text(alias)
    if class_name is not None and (not class_name.strip() or len(class_name) > 100):
        raise DomainError("教学班名称无效")
    _require_semester_scope(repos, workspace_id, semester_id)
    try:
        created = repos.paths.create_alias(
            workspace_id,
            plan_id,
            curriculum_course_id=curriculum_course_id,
            alias=alias.strip(),
            class_name=class_name.strip() if class_name else None,
            semester_id=semester_id,
        )
        repos.uow.commit()
    except Exception:
        repos.uow.rollback()
        raise
    return created


def delete_alias(repos: Repositories, workspace_id: int, plan_id: int, alias_id: int) -> None:
    get_plan(repos, workspace_id, plan_id)
    alias = repos.paths.get_alias(alias_id)
    if alias is None or alias.workspace_id != workspace_id:
        raise NotFoundError("课程别名不存在")
    if alias.curriculum_plan_id != plan_id:
        raise DomainError("别名不属于该培养方案")
    try:
        if not repos.paths.delete_alias(alias_id):
            raise NotFoundError("课程别名不存在")
        repos.uow.commit()
    except Exception:
        repos.uow.rollback()
        raise


@dataclass(frozen=True)
class StudyRecordView:
    record: CourseStudyRecordData
    curriculum_plan_id: int
    semester_name: str | None


def _semester_names(
    repos: Repositories, workspace_id: int, semester_ids: list[int | None]
) -> dict[int, str]:
    names: dict[int, str] = {}
    for semester_id in sorted({sid for sid in semester_ids if sid is not None}):
        semester = repos.semesters.get(semester_id)
        if semester is not None and semester.workspace_id == workspace_id:
            names[semester_id] = semester.name
    return names


def _study_record_views(
    repos: Repositories,
    workspace_id: int,
    plan_id: int,
    records: list[CourseStudyRecordData],
) -> list[StudyRecordView]:
    names = _semester_names(repos, workspace_id, [record.semester_id for record in records])
    return [
        StudyRecordView(
            record,
            plan_id,
            names.get(record.semester_id) if record.semester_id is not None else None,
        )
        for record in records
    ]


def list_study_records(
    repos: Repositories, workspace_id: int, plan_id: int
) -> list[StudyRecordView]:
    get_plan(repos, workspace_id, plan_id)
    return _study_record_views(
        repos, workspace_id, plan_id, repos.paths.list_study_records(plan_id)
    )


def get_study_record(
    repos: Repositories, workspace_id: int, plan_id: int, record_id: int
) -> StudyRecordView:
    get_plan(repos, workspace_id, plan_id)
    record = repos.paths.get_study_record(record_id)
    if record is None or record.workspace_id != workspace_id:
        raise NotFoundError("修读记录不存在")
    course = repos.paths.get_course(record.curriculum_course_id)
    if course is None or course.curriculum_plan_id != plan_id:
        raise DomainError("修读记录不属于该培养方案")
    return _study_record_views(repos, workspace_id, plan_id, [record])[0]


def create_study_record(
    repos: Repositories,
    workspace_id: int,
    plan_id: int,
    *,
    curriculum_course_id: int,
    semester_id: int | None,
    status: str,
    grade: str | None,
    credits_earned: float | None,
    note: str | None,
) -> StudyRecordView:
    get_course(repos, workspace_id, plan_id, curriculum_course_id)
    _require_semester_scope(repos, workspace_id, semester_id)
    parsed = validate_study_record_fields(
        status=status,
        grade=grade,
        credits_earned=credits_earned,
        note=note,
    )
    try:
        record = repos.paths.create_study_record(
            workspace_id,
            curriculum_course_id=curriculum_course_id,
            semester_id=semester_id,
            status=parsed.value,
            grade=grade.strip() if grade else None,
            credits_earned=float(credits_earned) if credits_earned is not None else None,
            note=note.strip() if note else None,
        )
        repos.uow.commit()
    except Exception:
        repos.uow.rollback()
        raise
    return _study_record_views(repos, workspace_id, plan_id, [record])[0]


def update_study_record(
    repos: Repositories,
    workspace_id: int,
    plan_id: int,
    record_id: int,
    *,
    semester_id: int | None,
    status: str,
    grade: str | None,
    credits_earned: float | None,
    note: str | None,
) -> StudyRecordView:
    """只更新用户显式提交的字段；修改状态不会补造成绩或已得学分。"""
    current = get_study_record(repos, workspace_id, plan_id, record_id)
    _require_semester_scope(repos, workspace_id, semester_id)
    parsed = validate_study_record_fields(
        status=status,
        grade=grade,
        credits_earned=credits_earned,
        note=note,
    )
    try:
        record = repos.paths.update_study_record(
            current.record.id,
            semester_id=semester_id,
            status=parsed.value,
            grade=grade.strip() if grade else None,
            credits_earned=float(credits_earned) if credits_earned is not None else None,
            note=note.strip() if note else None,
        )
        if record is None:
            raise NotFoundError("修读记录不存在")
        repos.uow.commit()
    except Exception:
        repos.uow.rollback()
        raise
    return _study_record_views(repos, workspace_id, plan_id, [record])[0]


def delete_study_record(
    repos: Repositories, workspace_id: int, plan_id: int, record_id: int
) -> None:
    get_study_record(repos, workspace_id, plan_id, record_id)
    try:
        if not repos.paths.delete_study_record(record_id):
            raise NotFoundError("修读记录不存在")
        repos.uow.commit()
    except Exception:
        repos.uow.rollback()
        raise


@dataclass(frozen=True)
class StudyStatusEntry:
    course: CurriculumCourseData
    status: str
    records: tuple[StudyRecordView, ...]
    completed_semester_ids: tuple[int, ...]
    in_progress_semester_ids: tuple[int, ...]
    planned_semester_ids: tuple[int, ...]
    note: str


@dataclass(frozen=True)
class StudyStatusSummary:
    plan_id: int
    entries: tuple[StudyStatusEntry, ...]
    counts: dict[str, int]
    note: str


def _study_entry_note(records: tuple[StudyRecordView, ...]) -> str:
    if not records:
        return "没有修读记录，状态未知；未知不代表未修或已修。"
    if len(records) > 1:
        note = (
            f"共 {len(records)} 条修读记录，按已完成 > 在修 > 待修聚合；"
            "重复修读分别列出，不合并学分或成绩。"
        )
    else:
        note = "状态来自用户填写的修读记录。"
    if any(view.record.semester_id is None for view in records):
        note += " 有记录未填写学期。"
    return note


def get_study_status(repos: Repositories, workspace_id: int, plan_id: int) -> StudyStatusSummary:
    """逐门课程汇总状态；没有记录的课程显式标为 unknown，不推断已修。"""
    get_plan(repos, workspace_id, plan_id)
    courses = repos.paths.list_courses(plan_id)
    views = _study_record_views(
        repos, workspace_id, plan_id, repos.paths.list_study_records(plan_id)
    )
    grouped: dict[int, list[StudyRecordView]] = {}
    for view in views:
        grouped.setdefault(view.record.curriculum_course_id, []).append(view)

    counts = {"completed": 0, "in_progress": 0, "planned": 0, UNKNOWN_STUDY_STATUS: 0}
    entries: list[StudyStatusEntry] = []
    for course in courses:
        records = tuple(grouped.get(course.id, []))
        status = aggregate_study_status(view.record.status for view in records)
        counts[status] += 1
        entries.append(
            StudyStatusEntry(
                course=course,
                status=status,
                records=records,
                completed_semester_ids=_semester_ids(records, "completed"),
                in_progress_semester_ids=_semester_ids(records, "in_progress"),
                planned_semester_ids=_semester_ids(records, "planned"),
                note=_study_entry_note(records),
            )
        )
    return StudyStatusSummary(
        plan_id=plan_id,
        entries=tuple(entries),
        counts=counts,
        note=(
            "汇总只使用用户填写的修读记录，不按课程学分或其他课程推断学分与成绩；"
            "unknown 表示没有记录，不代表未修或已修。"
        ),
    )


def _semester_ids(records: tuple[StudyRecordView, ...], status: str) -> tuple[int, ...]:
    ids: list[int] = []
    for view in records:
        semester_id = view.record.semester_id
        if view.record.status == status and semester_id is not None and semester_id not in ids:
            ids.append(semester_id)
    return tuple(ids)


@dataclass(frozen=True)
class AliasResolution:
    status: str
    matches: tuple[CurriculumCourseData, ...]
    matched_by: str | None
    note: str


def resolve_course_name(
    repos: Repositories,
    workspace_id: int,
    plan_id: int,
    *,
    name: str,
    semester_id: int | None = None,
    class_name: str | None = None,
) -> AliasResolution:
    """严格匹配正式课名或显式别名；多于一个课程时返回歧义，不自动选择。

    这里只做查询：不会写入绑定，也不会合并任何真实课程或教学班。
    """
    get_plan(repos, workspace_id, plan_id)
    validate_alias_text(name)
    _require_semester_scope(repos, workspace_id, semester_id)
    target = normalize_course_name(name)
    courses = {course.id: course for course in repos.paths.list_courses(plan_id)}
    matched: dict[int, str] = {}
    for course in courses.values():
        if normalize_course_name(course.name) == target:
            matched[course.id] = "name"
    for alias in repos.paths.list_aliases(plan_id):
        if normalize_course_name(alias.alias) != target:
            continue
        if not alias_scope_applies(
            alias_class_name=alias.class_name,
            alias_semester_id=alias.semester_id,
            class_name=class_name,
            semester_id=semester_id,
        ):
            continue
        matched.setdefault(alias.curriculum_course_id, "alias")
    matches = tuple(courses[course_id] for course_id in matched if course_id in courses)
    if len(matches) == 1:
        course = matches[0]
        status = "matched"
        matched_by = matched[course.id]
        note = "唯一匹配结果仅供确认，不自动合并课程或教学班。"
    elif len(matches) > 1:
        status = "ambiguous"
        matched_by = None
        note = "该名称对应多门课程或教学班范围不清，不能自动匹配，请人工选择。"
    else:
        status = "not_found"
        matched_by = None
        note = "方案内没有找到匹配的课程名称或别名。"
    return AliasResolution(status, matches, matched_by, note)


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
