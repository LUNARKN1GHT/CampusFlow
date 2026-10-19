"""N010 SQLAlchemy 关系存储，事务由应用用例提交。"""

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from campusflow.application.ports.version_relations import (
    VersionAcceptanceData,
    VersionContext,
    VersionRelationData,
)
from campusflow.domain.errors import NotFoundError
from campusflow.domain.version_relations import VersionRelationKind
from campusflow.infrastructure.db.models import (
    Material,
    MaterialVersion,
    MaterialVersionRelation,
    VersionChangeAcceptance,
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

    def find_acceptance(
        self,
        workspace_id: int,
        baseline_candidate_id: int,
        comparison_candidate_id: int,
        accepted_fields: tuple[str, ...],
    ) -> VersionAcceptanceData | None:
        row = self.session.scalar(
            select(VersionChangeAcceptance).where(
                VersionChangeAcceptance.workspace_id == workspace_id,
                VersionChangeAcceptance.baseline_candidate_id == baseline_candidate_id,
                VersionChangeAcceptance.comparison_candidate_id == comparison_candidate_id,
                VersionChangeAcceptance.accepted_fields == list(accepted_fields),
            )
        )
        return _acceptance_data(row) if row else None

    def create_acceptance(
        self,
        workspace_id: int,
        *,
        baseline_candidate_id: int,
        comparison_candidate_id: int,
        baseline_version_id: int,
        comparison_version_id: int,
        accepted_fields: tuple[str, ...],
        reason: str,
        operator: str,
        evidence_snapshot: tuple[dict, ...],
        previous_review_status: str,
    ) -> VersionAcceptanceData:
        row = VersionChangeAcceptance(
            workspace_id=workspace_id,
            baseline_candidate_id=baseline_candidate_id,
            comparison_candidate_id=comparison_candidate_id,
            baseline_version_id=baseline_version_id,
            comparison_version_id=comparison_version_id,
            accepted_fields=list(accepted_fields),
            reason=reason,
            operator=operator,
            evidence_snapshot=list(evidence_snapshot),
            previous_review_status=previous_review_status,
        )
        self.session.add(row)
        self.session.flush()
        return _acceptance_data(row)

    def list_acceptances(self, workspace_id: int, candidate_id: int) -> list[VersionAcceptanceData]:
        rows = self.session.scalars(
            select(VersionChangeAcceptance)
            .where(
                VersionChangeAcceptance.workspace_id == workspace_id,
                or_(
                    VersionChangeAcceptance.baseline_candidate_id == candidate_id,
                    VersionChangeAcceptance.comparison_candidate_id == candidate_id,
                ),
            )
            .order_by(VersionChangeAcceptance.id)
        )
        return [_acceptance_data(row) for row in rows]


def _acceptance_data(row: VersionChangeAcceptance) -> VersionAcceptanceData:
    return VersionAcceptanceData(
        id=row.id,
        workspace_id=row.workspace_id,
        baseline_candidate_id=row.baseline_candidate_id,
        comparison_candidate_id=row.comparison_candidate_id,
        baseline_version_id=row.baseline_version_id,
        comparison_version_id=row.comparison_version_id,
        accepted_fields=tuple(row.accepted_fields),
        reason=row.reason,
        operator=row.operator,
        evidence_snapshot=tuple(row.evidence_snapshot),
        previous_review_status=row.previous_review_status,
        created_at=row.created_at,
    )
