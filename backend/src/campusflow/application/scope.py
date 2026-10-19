"""W006：跨空间访问保护。

所有按 ID 读取、修改对象或建立关联的操作，必须校验对象属于当前空间；
归属不符时抛出与"对象不存在"相同的错误，不泄露其他空间的信息。
"""

from campusflow.application.ports.repositories import Repositories
from campusflow.domain.errors import NotFoundError


def require_in_workspace(
    object_workspace_id: int | None, current_workspace_id: int, not_found_message: str
) -> None:
    """校验对象归属当前空间；不匹配时按"不存在"处理（不泄露其他空间）。"""
    if object_workspace_id != current_workspace_id:
        raise NotFoundError(not_found_message)


def require_course_in_workspace(
    repos: Repositories, workspace_id: int, course_id: int | None
) -> None:
    """课程编号为空表示未限定；提供编号时必须是当前空间的课程。"""
    if course_id is None:
        return
    course = repos.courses.get(course_id)
    if course is None:
        raise NotFoundError("课程不存在")
    require_in_workspace(course.workspace_id, workspace_id, "课程不存在")


def require_semester_in_workspace(
    repos: Repositories, workspace_id: int, semester_id: int | None
) -> None:
    """学期编号为空表示未限定；提供编号时必须是当前空间的学期。"""
    if semester_id is None:
        return
    semester = repos.semesters.get(semester_id)
    if semester is None:
        raise NotFoundError("学期不存在")
    require_in_workspace(semester.workspace_id, workspace_id, "学期不存在")
