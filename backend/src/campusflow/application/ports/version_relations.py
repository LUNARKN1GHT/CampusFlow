"""N010 版本关系存储端口。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from campusflow.application.ports.repositories import MaterialData, MaterialVersionData
from campusflow.domain.version_relations import VersionRelationKind


@dataclass(frozen=True)
class VersionContext:
    material: MaterialData
    version: MaterialVersionData


@dataclass(frozen=True)
class VersionRelationData:
    id: int
    workspace_id: int
    source_version_id: int
    target_version_id: int
    kind: VersionRelationKind
    statement: str
    actor: str
    source_snapshot: dict
    target_snapshot: dict
    created_at: datetime


@dataclass(frozen=True)
class VersionAcceptanceData:
    id: int
    workspace_id: int
    baseline_candidate_id: int
    comparison_candidate_id: int
    baseline_version_id: int
    comparison_version_id: int
    accepted_fields: tuple[str, ...]
    reason: str
    operator: str
    evidence_snapshot: tuple[dict, ...]
    previous_review_status: str
    created_at: datetime


class VersionRelations(Protocol):
    def lock_workspace(self, workspace_id: int) -> None: ...

    def context(
        self, workspace_id: int, version_id: int, *, lock: bool = False
    ) -> VersionContext | None: ...

    def find(
        self, workspace_id: int, source_id: int, target_id: int, kind: VersionRelationKind
    ) -> VersionRelationData | None: ...

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
    ) -> VersionRelationData: ...

    def list_for_version(self, workspace_id: int, version_id: int) -> list[VersionRelationData]: ...

    def find_acceptance(
        self,
        workspace_id: int,
        baseline_candidate_id: int,
        comparison_candidate_id: int,
        accepted_fields: tuple[str, ...],
    ) -> VersionAcceptanceData | None: ...

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
    ) -> VersionAcceptanceData: ...

    def list_acceptances(
        self, workspace_id: int, candidate_id: int
    ) -> list[VersionAcceptanceData]: ...
