"""学期与课程用例：编排校验、仓储与事务。

归档只切换学期标记，不删除或改写课程、任务、进度与固定日程；默认学期列表隐藏
已归档项，显式包含时历史仍完整可读并可恢复。
update_* 接收"合并后的最终值"，None 表示清空；按 ID 的操作校验空间归属（W006）。
"""

from datetime import date

from campusflow.application.ports.repositories import CourseData, Repositories, SemesterData
from campusflow.application.scope import require_in_workspace
from campusflow.domain.errors import DomainError, NotFoundError
from campusflow.domain.semesters import validate_semester_range


def list_semesters(
    repos: Repositories, workspace_id: int, *, include_archived: bool = False
) -> list[SemesterData]:
    """默认只列未归档学期；include_archived=True 时历史学期照常返回。"""
    return repos.semesters.list(workspace_id, include_archived=include_archived)


def create_semester(
    repos: Repositories, workspace_id: int, *, name: str, start_date: date, end_date: date
) -> SemesterData:
    """创建学期；结束日期不得早于开始日期，校验失败不落库。"""
    error = validate_semester_range(start_date, end_date)
    if error:
        raise DomainError(error)
    semester = repos.semesters.create(workspace_id, name, start_date, end_date)
    repos.uow.commit()
    return semester


def set_semester_archived(
    repos: Repositories, workspace_id: int, semester_id: int, *, archived: bool
) -> SemesterData:
    """切换学期归档标记（W009）：只改标记，不级联修改关联数据，可反复恢复。"""
    current = repos.semesters.get(semester_id)
    if current is None:
        raise NotFoundError("学期不存在")
    require_in_workspace(current.workspace_id, workspace_id, "学期不存在")
    semester = repos.semesters.set_archived(semester_id, archived)
    repos.uow.commit()
    return semester


def list_courses(
    repos: Repositories, workspace_id: int, *, semester_id: int | None = None
) -> list[CourseData]:
    """按学期筛选课程；不筛选时返回全部，包括归档学期的课程。"""
    if semester_id is not None:
        require_semester_in_workspace(repos, workspace_id, semester_id)
    return repos.courses.list(workspace_id, semester_id)


def get_course(repos: Repositories, workspace_id: int, course_id: int) -> CourseData:
    course = repos.courses.get(course_id)
    if course is None:
        raise NotFoundError("课程不存在")
    require_in_workspace(course.workspace_id, workspace_id, "课程不存在")
    return course


def require_semester_in_workspace(repos: Repositories, workspace_id: int, semester_id: int) -> None:
    """校验学期存在且属于当前空间；供任务、日程与排期等用例共用。"""
    semester = repos.semesters.get(semester_id)
    if semester is None:
        raise NotFoundError("学期不存在")
    require_in_workspace(semester.workspace_id, workspace_id, "学期不存在")


def create_course(
    repos: Repositories,
    workspace_id: int,
    *,
    semester_id: int,
    name: str,
    code: str | None,
    teacher: str | None,
    class_name: str | None,
) -> CourseData:
    """创建课程；目标学期必须存在且属于当前空间。"""
    require_semester_in_workspace(repos, workspace_id, semester_id)
    course = repos.courses.create(workspace_id, semester_id, name, code, teacher, class_name)
    repos.uow.commit()
    return course


def update_course(
    repos: Repositories,
    workspace_id: int,
    course_id: int,
    *,
    semester_id: int,
    name: str,
    code: str | None,
    teacher: str | None,
    class_name: str | None,
) -> CourseData:
    """更新课程最终值并可改挂学期；关联任务与日程本身不被改写。"""
    current = repos.courses.get(course_id)
    if current is None:
        raise NotFoundError("课程不存在")
    require_in_workspace(current.workspace_id, workspace_id, "课程不存在")
    require_semester_in_workspace(repos, workspace_id, semester_id)
    updated = repos.courses.update(
        course_id,
        semester_id=semester_id,
        name=name,
        code=code,
        teacher=teacher,
        class_name=class_name,
    )
    repos.uow.commit()
    return updated
