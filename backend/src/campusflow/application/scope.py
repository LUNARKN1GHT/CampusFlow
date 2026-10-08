"""W006：跨空间访问保护。

所有按 ID 读取、修改对象或建立关联的操作，必须校验对象属于当前空间；
归属不符时抛出与"对象不存在"相同的错误，不泄露其他空间的信息。
"""

from campusflow.domain.errors import NotFoundError


def require_in_workspace(
    object_workspace_id: int | None, current_workspace_id: int, not_found_message: str
) -> None:
    """校验对象归属当前空间；不匹配时按"不存在"处理（不泄露其他空间）。"""
    if object_workspace_id != current_workspace_id:
        raise NotFoundError(not_found_message)
