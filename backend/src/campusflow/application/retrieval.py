"""Q005：先校验用户选择的范围，再读取可交给后续排序器的候选。

选定学期、课程或教学班后只匹配标注相同的资料；未提供某维度表示不限制该
维度，但候选始终限定在当前工作空间、未归档资料与当前存储版本内。
"""

from campusflow.application.ports.repositories import Repositories
from campusflow.application.ports.retrieval import RetrievalCandidates
from campusflow.application.scope import require_in_workspace
from campusflow.domain.errors import DomainError, NotFoundError
from campusflow.domain.retrieval import RetrievalScope, ScopedCandidates


def validate_retrieval_scope(repos: Repositories, workspace_id: int, scope: RetrievalScope) -> None:
    """确认空间、学期、课程与教学班一致；不读取候选。

    课程必须属于所选学期、教学班一致；不一致时直接拒绝，交给读取层猜测
    会返回看似有效但对象错误的资料。
    """
    if type(workspace_id) is not int or workspace_id <= 0:
        raise NotFoundError("工作空间不存在")
    if repos.workspaces.get(workspace_id) is None:
        raise NotFoundError("工作空间不存在")
    if not isinstance(scope, RetrievalScope):
        raise DomainError("检索范围无效")
    if scope.semester_id is not None:
        semester = repos.semesters.get(scope.semester_id)
        if semester is None:
            raise NotFoundError("学期不存在")
        require_in_workspace(semester.workspace_id, workspace_id, "学期不存在")
    if scope.course_id is not None:
        course = repos.courses.get(scope.course_id)
        if course is None:
            raise NotFoundError("课程不存在")
        require_in_workspace(course.workspace_id, workspace_id, "课程不存在")
        if scope.semester_id is not None and course.semester_id != scope.semester_id:
            raise DomainError("所选课程不属于所选学期")
        if (
            scope.class_name is not None
            and course.class_name is not None
            and course.class_name != scope.class_name
        ):
            raise DomainError("所选课程与教学班不一致")


def collect_retrieval_candidates(
    repos: Repositories,
    reader: RetrievalCandidates,
    workspace_id: int,
    scope: RetrievalScope,
    profile_id: str,
) -> ScopedCandidates:
    """workspace_id 必须来自已认证会话，不由问题文本或模型决定。

    不调用模型、不提交事务。调用方应在返回后结束读取事务，再执行排序/模型请求。
    未提供某个范围表示不限制该维度，不表示可跨工作空间。
    """
    validate_retrieval_scope(repos, workspace_id, scope)
    return reader.collect(workspace_id, scope, profile_id)
