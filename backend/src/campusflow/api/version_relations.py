"""资料版本关系 HTTP 路由（N010）。

只记录用户声明的替代或关联及两端快照，不判定版本生效，也不改写原文、
片段或正式事项；变更接受与对照界面分别留给 N014/N017。
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field

from campusflow.api.auth import require_session
from campusflow.api.deps import get_current_workspace, get_repositories, get_version_relations
from campusflow.application import version_relations as use_cases
from campusflow.application.ports.repositories import Repositories, WorkspaceData
from campusflow.application.ports.version_relations import VersionRelations
from campusflow.domain.version_relations import VersionRelationKind

router = APIRouter(prefix="/material-versions", tags=["material-version-relations"])
CurrentWorkspace = Annotated[WorkspaceData, Depends(get_current_workspace)]
Repos = Annotated[Repositories, Depends(get_repositories)]
Relations = Annotated[VersionRelations, Depends(get_version_relations)]
Actor = Annotated[str, Depends(require_session)]


class RelationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_version_id: int = Field(gt=0, strict=True)
    kind: VersionRelationKind
    statement: str = Field(min_length=1, max_length=1000)


class RelationOut(BaseModel):
    id: int
    source_version_id: int
    target_version_id: int
    kind: VersionRelationKind
    statement: str
    actor: str
    source_snapshot: dict
    target_snapshot: dict
    created_at: datetime
    current_scope_comparison: Literal["same", "different", "unknown"]
    metadata_changed: bool
    source_archived: bool
    target_archived: bool
    # 关系只是待核对记录：固定声明未生效且需要复核，不能当作已完成的替代。
    replacement_effective: Literal[False] = False
    review_required: Literal[True] = True
    origin: Literal["user"] = "user"


def _out(view: use_cases.RelationView) -> RelationOut:
    relation = view.relation
    return RelationOut(
        id=relation.id,
        source_version_id=relation.source_version_id,
        target_version_id=relation.target_version_id,
        kind=relation.kind,
        statement=relation.statement,
        actor=relation.actor,
        source_snapshot=relation.source_snapshot,
        target_snapshot=relation.target_snapshot,
        created_at=relation.created_at,
        current_scope_comparison=view.current_scope_comparison,
        metadata_changed=view.metadata_changed,
        source_archived=view.source_archived,
        target_archived=view.target_archived,
    )


@router.post("/{version_id}/relations", response_model=RelationOut, status_code=201)
def record_relation(
    version_id: int,
    payload: RelationIn,
    workspace: CurrentWorkspace,
    repos: Repos,
    store: Relations,
    actor: Actor,
    response: Response,
) -> RelationOut:
    """记录用户声明的版本关系；两端必须属于当前空间且资料未归档。

    同一对版本与说明重复提交按幂等返回既有记录（200）；说明不同则拒绝覆盖。
    """
    view, created = use_cases.record_relation(
        repos,
        store,
        workspace.id,
        source_version_id=version_id,
        target_version_id=payload.target_version_id,
        kind=payload.kind,
        statement=payload.statement,
        actor=actor,
    )
    response.status_code = 201 if created else 200
    return _out(view)


@router.get("/{version_id}/relations", response_model=list[RelationOut])
def list_relations(
    version_id: int, workspace: CurrentWorkspace, store: Relations
) -> list[RelationOut]:
    """只读；附带当前范围比较与归档状态，不修改版本或关系。"""
    return [_out(view) for view in use_cases.list_relations(store, workspace.id, version_id)]
