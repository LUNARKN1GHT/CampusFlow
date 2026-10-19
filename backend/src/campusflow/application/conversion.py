"""R004/R005/R006：把已确认的任务、日程或规则候选转换为正式事项。

只有已确认候选可以转换；每次都先查来源候选关联，命中即返回既有事项，
因此重试与重复点击不会重复创建。每个转换在独立事务内提交，失败回滚。
"""

from dataclasses import dataclass
from zoneinfo import ZoneInfo

from campusflow.application.candidates import CandidateData, get_candidate
from campusflow.application.course_paths import resolve_course_name
from campusflow.application.ports.repositories import (
    CourseRelationData,
    CurriculumCourseData,
    FixedEventData,
    Repositories,
    RuleData,
    TaskData,
)
from campusflow.application.scope import (
    require_course_in_workspace,
    require_semester_in_workspace,
)
from campusflow.application.settings import get_settings
from campusflow.domain.candidate_conversion import build_task_conversion
from campusflow.domain.candidates import (
    CandidateKind,
    require_confirmed_for_conversion,
)
from campusflow.domain.course_paths import (
    parse_relation_type_text,
    validate_course_fields,
)
from campusflow.domain.errors import DomainError, NotFoundError
from campusflow.domain.event_conversion import build_event_conversion
from campusflow.domain.rule_conversion import build_rule_conversion
from campusflow.domain.schedule import validate_fixed_event
from campusflow.domain.states import EventRecurrence, TaskPriority
from campusflow.domain.timezones import in_workspace_timezone


@dataclass(frozen=True)
class CandidateConversion:
    """候选已生成的正式事项；只带编号与标题，不复制正式事项的全部字段。"""

    kind: CandidateKind
    object_id: int
    title: str


def find_candidate_conversion(
    repos: Repositories, candidate: CandidateData
) -> CandidateConversion | None:
    """候选是否已生成正式事项；没有就返回 None，不猜也不占位。"""
    if candidate.draft.kind is CandidateKind.TASK:
        task = repos.tasks.get_by_source_candidate(candidate.id)
        return (
            None if task is None else CandidateConversion(CandidateKind.TASK, task.id, task.title)
        )
    if candidate.draft.kind is CandidateKind.EVENT:
        event = repos.schedule.get_fixed_event_by_source_candidate(candidate.id)
        return (
            None
            if event is None
            else CandidateConversion(CandidateKind.EVENT, event.id, event.title)
        )
    if candidate.draft.kind is CandidateKind.CURRICULUM_COURSE:
        course = repos.paths.get_course_by_source_candidate(candidate.id)
        return (
            None
            if course is None
            else CandidateConversion(CandidateKind.CURRICULUM_COURSE, course.id, course.name)
        )
    if candidate.draft.kind is CandidateKind.COURSE_RELATION:
        relation = repos.paths.get_relation_by_source_candidate(candidate.id)
        return (
            None
            if relation is None
            else CandidateConversion(
                CandidateKind.COURSE_RELATION,
                relation.id,
                f"{relation.from_course_id} → {relation.to_course_id}",
            )
        )
    rule = repos.rules.get_by_source_candidate(candidate.id)
    return None if rule is None else CandidateConversion(CandidateKind.RULE, rule.id, rule.title)


def convert_candidate(
    repos: Repositories, workspace_id: int, candidate_id: int
) -> tuple[CandidateConversion, bool]:
    """按类型把已确认候选转换为正式事项，返回 (事项, 本次是否新建)。

    未确认候选直接拒绝；重复点击或请求重试命中既有的来源候选关联，返回同一
    事项并把 created 置为 false，界面据此提示「已存在」而不是再造一条。
    """
    candidate = get_candidate(repos, workspace_id, candidate_id)
    require_confirmed_for_conversion(candidate.review_status)
    existing = find_candidate_conversion(repos, candidate)
    if existing is not None:
        return existing, False
    if candidate.draft.kind is CandidateKind.TASK:
        task = convert_candidate_to_task(repos, workspace_id, candidate_id)
        return CandidateConversion(CandidateKind.TASK, task.id, task.title), True
    if candidate.draft.kind is CandidateKind.EVENT:
        event = convert_candidate_to_event(repos, workspace_id, candidate_id)
        return CandidateConversion(CandidateKind.EVENT, event.id, event.title), True
    if candidate.draft.kind is CandidateKind.CURRICULUM_COURSE:
        course = convert_candidate_to_curriculum_course(repos, workspace_id, candidate_id)
        return (
            CandidateConversion(CandidateKind.CURRICULUM_COURSE, course.id, course.name),
            True,
        )
    if candidate.draft.kind is CandidateKind.COURSE_RELATION:
        relation = convert_candidate_to_course_relation(repos, workspace_id, candidate_id)
        return (
            CandidateConversion(
                CandidateKind.COURSE_RELATION,
                relation.id,
                f"{relation.from_course_id} → {relation.to_course_id}",
            ),
            True,
        )
    rule = convert_candidate_to_rule(repos, workspace_id, candidate_id)
    return CandidateConversion(CandidateKind.RULE, rule.id, rule.title), True


def convert_candidate_to_task(
    repos: Repositories, workspace_id: int, candidate_id: int
) -> TaskData:
    """转换已确认的任务候选；重复调用返回既有任务，不重复创建。"""
    candidate = get_candidate(repos, workspace_id, candidate_id)
    require_confirmed_for_conversion(candidate.review_status)
    existing = repos.tasks.get_by_source_candidate(candidate.id)
    if existing is not None:
        return existing
    course_id = candidate.draft.scope.course_id
    require_course_in_workspace(repos, workspace_id, course_id)
    version = repos.materials.get_version(candidate.source_version_id)
    if version is None:
        raise NotFoundError("候选来源版本不存在")
    conversion = build_task_conversion(candidate.draft.kind, candidate.effective_draft().fields)
    # 截止保持原精度：只有日期就不补时刻，解析不出的写法只留原文备注。
    try:
        task = repos.tasks.create(
            workspace_id,
            course_id,
            conversion.title,
            conversion.description,
            conversion.due_date,
            conversion.due_time,
            None,
            conversion.due_source,
            conversion.due_note,
            TaskPriority.MEDIUM,
            conversion.estimated_minutes,
            conversion.estimated_minutes,
            source_material_id=version.material_id,
            source_candidate_id=candidate.id,
        )
        repos.uow.commit()
    except Exception:
        repos.uow.rollback()
        raise
    return task


def convert_candidate_to_event(
    repos: Repositories, workspace_id: int, candidate_id: int
) -> FixedEventData:
    """转换已确认的日程候选；起止不可确定时拒绝，重复调用返回既有日程。"""
    candidate = get_candidate(repos, workspace_id, candidate_id)
    require_confirmed_for_conversion(candidate.review_status)
    existing = repos.schedule.get_fixed_event_by_source_candidate(candidate.id)
    if existing is not None:
        return existing
    course_id = candidate.draft.scope.course_id
    require_course_in_workspace(repos, workspace_id, course_id)
    conversion = build_event_conversion(candidate.draft.kind, candidate.effective_draft().fields)
    # 无偏移的本地时间按工作空间时区解释；夏令时缺失或歧义时刻会在此明确报错。
    timezone = ZoneInfo(get_settings(repos, workspace_id).timezone)
    starts_at = in_workspace_timezone(conversion.starts_at, timezone)
    ends_at = in_workspace_timezone(conversion.ends_at, timezone)
    error = validate_fixed_event(starts_at, ends_at, EventRecurrence.NONE, None)
    if error:
        raise DomainError(error)
    try:
        event = repos.schedule.create_fixed_event(
            workspace_id,
            course_id,
            conversion.title,
            starts_at,
            ends_at,
            conversion.location,
            EventRecurrence.NONE,
            None,
            source_candidate_id=candidate.id,
        )
        repos.uow.commit()
    except Exception:
        repos.uow.rollback()
        raise
    return event


def _plan_scope(repos: Repositories, workspace_id: int, candidate) -> int:
    # 课程与课程关系只能写入当前空间的培养方案，未绑定方案的候选不能转换。
    plan_id = candidate.draft.scope.curriculum_plan_id
    if plan_id is None:
        raise DomainError("候选未绑定培养方案，不能转换为正式课程数据")
    plan = repos.paths.get_plan(plan_id)
    if plan is None or plan.workspace_id != workspace_id:
        raise NotFoundError("培养方案不存在")
    return plan_id


def convert_candidate_to_curriculum_course(
    repos: Repositories, workspace_id: int, candidate_id: int
) -> CurriculumCourseData:
    """把已确认的课程候选写入方案课程；重复调用返回既有行。"""
    candidate = get_candidate(repos, workspace_id, candidate_id)
    require_confirmed_for_conversion(candidate.review_status)
    existing = repos.paths.get_course_by_source_candidate(candidate.id)
    if existing is not None:
        return existing
    plan_id = _plan_scope(repos, workspace_id, candidate)
    fields = {field.name: field for field in candidate.effective_draft().fields}
    name = fields["course_name"].value
    code = fields.get("course_code")
    credits_field = fields.get("credits")
    credits = (
        float(credits_field.value)
        if credits_field is not None and type(credits_field.value) in (int, float)
        else None
    )
    terms_field = fields.get("offered_terms")
    terms = (
        [str(item) for item in terms_field.value]
        if terms_field is not None and isinstance(terms_field.value, list)
        else []
    )
    normalized_terms = validate_course_fields(
        name=name if isinstance(name, str) else "",
        code=code.value if code is not None and isinstance(code.value, str) else None,
        credits=credits,
        offered_terms=terms,
    )
    try:
        course = repos.paths.create_course(
            workspace_id,
            plan_id,
            name=name if isinstance(name, str) else "",
            code=code.value if code is not None and isinstance(code.value, str) else None,
            credits=credits,
            offered_terms=normalized_terms,
            source_candidate_id=candidate.id,
        )
        repos.uow.commit()
    except Exception:
        repos.uow.rollback()
        raise
    return course


def _match_course(
    repos: Repositories,
    workspace_id: int,
    plan_id: int,
    name: object,
    candidate,
    label: str,
) -> CurriculumCourseData:
    if not isinstance(name, str) or not name.strip():
        raise DomainError(f"{label}为空，不能确定课程关系")
    scope = candidate.draft.scope
    result = resolve_course_name(
        repos,
        workspace_id,
        plan_id,
        name=name,
        semester_id=scope.semester_id,
        class_name=scope.class_name,
    )
    if result.status != "matched":
        raise DomainError(
            f"{label}“{name}”在方案内无法唯一匹配（{result.status}），请先核对课程或别名"
        )
    # 只有唯一匹配才落库；同名多班等情况保留候选待核对，不猜课程。
    return result.matches[0]


def convert_candidate_to_course_relation(
    repos: Repositories, workspace_id: int, candidate_id: int
) -> CourseRelationData:
    """把已确认的关系候选写入正式关系；课程无法唯一匹配时拒绝，保留候选待核对。"""
    candidate = get_candidate(repos, workspace_id, candidate_id)
    require_confirmed_for_conversion(candidate.review_status)
    existing = repos.paths.get_relation_by_source_candidate(candidate.id)
    if existing is not None:
        return existing
    plan_id = _plan_scope(repos, workspace_id, candidate)
    fields = {field.name: field for field in candidate.effective_draft().fields}
    relation = parse_relation_type_text(fields["relation_type"].value)
    if relation is None:
        raise DomainError("关系类型无法确定为强制先修、并修或建议先学")
    source = _match_course(
        repos, workspace_id, plan_id, fields["from_course_name"].value, candidate, "前置课程"
    )
    target = _match_course(
        repos, workspace_id, plan_id, fields["to_course_name"].value, candidate, "受约束课程"
    )
    condition = fields.get("condition_text")
    condition_text = (
        condition.value.strip()
        if condition is not None and isinstance(condition.value, str) and condition.value.strip()
        else None
    )
    try:
        created = repos.paths.create_relation(
            workspace_id,
            plan_id,
            from_course_id=source.id,
            to_course_id=target.id,
            relation_type=relation.value,
            condition_text=condition_text,
            source_candidate_id=candidate.id,
        )
        repos.uow.commit()
    except Exception:
        repos.uow.rollback()
        raise
    return created


def convert_candidate_to_rule(
    repos: Repositories, workspace_id: int, candidate_id: int
) -> RuleData:
    """转换已确认的规则候选（R006）；重复调用返回既有规则，不重复创建。

    未确认（待核对/已忽略/已失效）的候选不能当作正式要求；
    条件、范围与生效声明只保留原文，不做日期换算。
    """
    candidate = get_candidate(repos, workspace_id, candidate_id)
    require_confirmed_for_conversion(candidate.review_status)
    existing = repos.rules.get_by_source_candidate(candidate.id)
    if existing is not None:
        return existing
    scope = candidate.draft.scope
    require_course_in_workspace(repos, workspace_id, scope.course_id)
    require_semester_in_workspace(repos, workspace_id, scope.semester_id)
    version = repos.materials.get_version(candidate.source_version_id)
    if version is None:
        raise NotFoundError("候选来源版本不存在")
    conversion = build_rule_conversion(candidate.draft.kind, candidate.effective_draft().fields)
    try:
        rule = repos.rules.create(
            workspace_id,
            scope.course_id,
            scope.semester_id,
            conversion.title,
            conversion.rule_text,
            conversion.conditions_text,
            conversion.scope_text,
            conversion.effective_text,
            source_material_id=version.material_id,
            source_candidate_id=candidate.id,
        )
        repos.uow.commit()
    except Exception:
        repos.uow.rollback()
        raise
    return rule
