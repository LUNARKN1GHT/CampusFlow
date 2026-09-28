"""学期与课程用例：编排校验、仓储与事务。

update_* 系列接收"合并后的最终值"（由 API 层把补丁与当前值合并后传入），
None 表示该字段确实要清空。
"""

from datetime import date

from campusflow.application.ports.repositories import CourseData, Repositories, SemesterData
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


def set_semester_archived(repos: Repositories, semester_id: int, *, archived: bool) -> SemesterData:
    semester = repos.semesters.set_archived(semester_id, archived)
    if semester is None:
        raise NotFoundError("学期不存在")
    repos.uow.commit()
    return semester


def list_courses(
    repos: Repositories, workspace_id: int, *, semester_id: int | None = None
) -> list[CourseData]:
    return repos.courses.list(workspace_id, semester_id)


def get_course(repos: Repositories, course_id: int) -> CourseData:
    course = repos.courses.get(course_id)
    if course is None:
        raise NotFoundError("课程不存在")
    return course


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
    if repos.semesters.get(semester_id) is None:
        raise NotFoundError("学期不存在")
    course = repos.courses.create(workspace_id, semester_id, name, code, teacher, class_name)
    repos.uow.commit()
    return course


def update_course(
    repos: Repositories,
    course_id: int,
    *,
    semester_id: int,
    name: str,
    code: str | None,
    teacher: str | None,
    class_name: str | None,
) -> CourseData:
    if repos.courses.get(course_id) is None:
        raise NotFoundError("课程不存在")
    if repos.semesters.get(semester_id) is None:
        raise NotFoundError("学期不存在")
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
