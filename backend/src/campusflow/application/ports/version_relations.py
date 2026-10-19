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
    """版本关系与字段接受的存储边界；所有查询都限定工作空间。

    关系只记录两端版本与说明，不改写原文件、片段或正式事项；接受记录追加保存，
    谁在何时接受了哪些字段必须可追溯。
    """

    def lock_workspace(self, workspace_id: int) -> None:
        """事务内锁工作空间行，串行化同一空间的重复提交；不存在时抛错。"""
        ...

    def context(
        self, workspace_id: int, version_id: int, *, lock: bool = False
    ) -> VersionContext | None:
        """读取版本及其资料；跨空间或版本不属于该资料时返回 None。"""
        ...

    def find(
        self, workspace_id: int, source_id: int, target_id: int, kind: VersionRelationKind
    ) -> VersionRelationData | None:
        """按方向与类型查既有关系，供创建前判重；不存在返回 None。"""
        ...

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
        """保存关系及两端发布信息/范围快照；调用方先 find，避免重复关系。"""
        ...

    def list_for_version(self, workspace_id: int, version_id: int) -> list[VersionRelationData]: ...

    def find_acceptance(
        self,
        workspace_id: int,
        baseline_candidate_id: int,
        comparison_candidate_id: int,
        accepted_fields: tuple[str, ...],
    ) -> VersionAcceptanceData | None:
        """按两端候选与字段集合查重，保证同一接受重复提交幂等。"""
        ...

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
        """追加接受记录与逐字段证据；旧候选失效由调用方在同一事务内完成。"""
        ...

    def list_acceptances(self, workspace_id: int, candidate_id: int) -> list[VersionAcceptanceData]:
        """返回与候选相关的全部接受历史，用于变更追溯。"""
        ...
