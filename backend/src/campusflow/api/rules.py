"""正式课程规则 HTTP 路由（R006）。

只有经核对确认并转换的规则会出现在这里；未确认候选不当作正式要求。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from campusflow.api.deps import get_current_workspace, get_repositories
from campusflow.api.schemas import RuleOut
from campusflow.application import rules as use_cases
from campusflow.application.ports.repositories import Repositories, WorkspaceData

router = APIRouter(tags=["rules"])

CurrentWorkspace = Annotated[WorkspaceData, Depends(get_current_workspace)]
Repos = Annotated[Repositories, Depends(get_repositories)]


@router.get("/rules", response_model=list[RuleOut])
def list_rules(
    workspace: CurrentWorkspace,
    repos: Repos,
    course_id: Annotated[int | None, Query()] = None,
    semester_id: Annotated[int | None, Query()] = None,
) -> list[RuleOut]:
    """按课程或学期过滤，两者都限定当前空间；跨空间编号按不存在处理。

    来源资料删除后规则保留，但 source_state 会变为 invalidated，不能再声称
    引用可验证。
    """
    return [
        RuleOut.model_validate(r)
        for r in use_cases.list_rules(
            repos, workspace.id, course_id=course_id, semester_id=semester_id
        )
    ]
