"""R004/R005/R006：把已确认的任务、日程或规则候选转换为正式事项，关联候选与来源资料。"""

from dataclasses import dataclass
from zoneinfo import ZoneInfo

from campusflow.application.candidates import CandidateData, get_candidate
from campusflow.application.ports.repositories import (
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
    rule = repos.rules.get_by_source_candidate(candidate.id)
    return None if rule is None else CandidateConversion(CandidateKind.RULE, rule.id, rule.title)


def convert_candidate(
    repos: Repositories, workspace_id: int, candidate_id: int
) -> tuple[CandidateConversion, bool]:
    """按类型把已确认候选转换为正式事项，返回 (事项, 本次是否新建)。

    重复点击或请求重试命中既有的来源候选关联，返回同一事项并把 created 置为
    false，界面据此提示「已存在」而不是再造一条。
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
