"""学期与课程用例：编排校验、仓储与事务。

update_* 系列接收"合并后的最终值"（由 API 层把补丁与当前值合并后传入），
None 表示该字段确实要清空。所有按 ID 的操作校验对象归属当前空间（W006）。
"""

from datetime import date

from campusflow.application.ports.repositories import CourseData, Repositories, SemesterData
from campusflow.application.scope import require_in_workspace
from campusflow.domain.errors import DomainError, NotFoundError
from campusflow.domain.semesters import validate_semester_range


def list_semesters(
    repos: Repositories, workspace_id: int, *, include_archived: bool = False
) -> list[SemesterData]:
    return repos.semesters.list(workspace_id, include_archived=include_archived)


def create_semester(
    repos: Repositories, workspace_id: int, *, name: str, start_date: date, end_date: date
) -> SemesterData:
    error = validate_semester_range(start_date, end_date)
    if error:
        raise DomainError(error)
    semester = repos.semesters.create(workspace_id, name, start_date, end_date)
    repos.uow.commit()
    return semester


def set_semester_archived(
    repos: Repositories, workspace_id: int, semester_id: int, *, archived: bool
) -> SemesterData:
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
