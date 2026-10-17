"""N010 SQLAlchemy 关系存储，事务由应用用例提交。"""

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from campusflow.application.ports.version_relations import VersionContext, VersionRelationData
from campusflow.domain.errors import NotFoundError
from campusflow.domain.version_relations import VersionRelationKind
from campusflow.infrastructure.db.models import (
    Material,
    MaterialVersion,
    MaterialVersionRelation,
    Workspace,
)
from campusflow.infrastructure.repositories import _material_to_data, _material_version_to_data


def _data(row: MaterialVersionRelation) -> VersionRelationData:
    return VersionRelationData(
        row.id,
        row.workspace_id,
        row.source_version_id,
        row.target_version_id,
        VersionRelationKind(row.kind),
        row.statement,
        row.actor,
        dict(row.source_snapshot),
        dict(row.target_snapshot),
        row.created_at,
    )


class SqlVersionRelations:
    def __init__(self, session: Session):
        self.session = session

    def lock_workspace(self, workspace_id: int) -> None:
        if (
            self.session.scalar(
                select(Workspace.id).where(Workspace.id == workspace_id).with_for_update()
            )
            is None
        ):
            raise NotFoundError("工作空间不存在")

    def context(
        self, workspace_id: int, version_id: int, *, lock: bool = False
    ) -> VersionContext | None:
        material_id = self.session.scalar(
            select(MaterialVersion.material_id).where(MaterialVersion.id == version_id)
        )
        query = select(Material).where(
            Material.id == material_id, Material.workspace_id == workspace_id
        )
        if lock:
            query = query.with_for_update()
        material = self.session.scalar(query.execution_options(populate_existing=True))
        if material is None:
            return None
        query = select(MaterialVersion).where(
            MaterialVersion.id == version_id, MaterialVersion.material_id == material.id
        )
        if lock:
            query = query.with_for_update()
        version = self.session.scalar(query.execution_options(populate_existing=True))
        return (
            VersionContext(_material_to_data(material), _material_version_to_data(version))
            if version
            else None
        )

    def find(
        self, workspace_id: int, source_id: int, target_id: int, kind: VersionRelationKind
    ) -> VersionRelationData | None:
        row = self.session.scalar(
            select(MaterialVersionRelation).where(
                MaterialVersionRelation.workspace_id == workspace_id,
                MaterialVersionRelation.source_version_id == source_id,
                MaterialVersionRelation.target_version_id == target_id,
                MaterialVersionRelation.kind == kind,
            )
        )
        return _data(row) if row else None

    def create(
        self,
        workspace_id: int,
        source_id: int,
        target_id: int,
        kind: VersionRelationKind,
        statement: str,
        actor: str,
        source_snapshot: dict,
        target_snapshot: dict,
    ) -> VersionRelationData:
        row = MaterialVersionRelation(
            workspace_id=workspace_id,
            source_version_id=source_id,
            target_version_id=target_id,
            kind=kind,
            statement=statement,
            actor=actor,
            source_snapshot=source_snapshot,
            target_snapshot=target_snapshot,
        )
        self.session.add(row)
        self.session.flush()
        return _data(row)

    def list_for_version(self, workspace_id: int, version_id: int) -> list[VersionRelationData]:
        rows = self.session.scalars(
            select(MaterialVersionRelation)
            .where(
                MaterialVersionRelation.workspace_id == workspace_id,
                or_(
                    MaterialVersionRelation.source_version_id == version_id,
                    MaterialVersionRelation.target_version_id == version_id,
                ),
            )
            .order_by(MaterialVersionRelation.id)
        )
        return [_data(row) for row in rows]
