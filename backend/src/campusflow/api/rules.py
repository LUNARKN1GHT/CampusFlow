"""课程规则 HTTP 路由（R006）。"""

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
    return [
        RuleOut.model_validate(r)
        for r in use_cases.list_rules(
            repos, workspace.id, course_id=course_id, semester_id=semester_id
        )
    ]
