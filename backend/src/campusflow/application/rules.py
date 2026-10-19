"""R006：已确认课程规则的查询用例（只读，不修改规则或候选状态）。"""

from campusflow.application.ports.repositories import Repositories, RuleData
from campusflow.application.scope import (
    require_course_in_workspace,
    require_semester_in_workspace,
)


def list_rules(
    repos: Repositories,
    workspace_id: int,
    *,
    course_id: int | None = None,
    semester_id: int | None = None,
) -> list[RuleData]:
    """按工作空间列出正式课程规则，可按课程或学期过滤。

    只有经核对确认并转换的规则才会出现在这里；未确认候选不当作正式要求。
    """
    # 先校验过滤条件的归属，跨空间 ID 按不存在处理，防止借查询探测他人数据。
    require_course_in_workspace(repos, workspace_id, course_id)
    require_semester_in_workspace(repos, workspace_id, semester_id)
    return repos.rules.list(workspace_id, course_id=course_id, semester_id=semester_id)
