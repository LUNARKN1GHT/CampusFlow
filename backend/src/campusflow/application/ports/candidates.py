"""候选存储端口：字段和证据分别保存，持久化实现不泄漏到应用层。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from campusflow.domain.candidates import CandidateDraft, ReviewReason
from campusflow.domain.states import ReviewStatus


@dataclass(frozen=True)
class CandidateData:
    id: int
    workspace_id: int
    source_version_id: int
    draft: CandidateDraft
    review_status: ReviewStatus
    created_at: datetime


class CandidateRepository(Protocol):
    def create(
        self, workspace_id: int, version_id: int, draft: CandidateDraft
    ) -> CandidateData: ...

    def get(self, workspace_id: int, candidate_id: int) -> CandidateData | None: ...

    def list_by_version(self, workspace_id: int, version_id: int) -> list[CandidateData]: ...

    def ids_by_material(self, workspace_id: int, material_id: int) -> list[int]: ...

    def list_by_scope(
        self,
        workspace_id: int,
        semester_id: int | None,
        course_id: int | None,
        class_name: str | None,
    ) -> list[CandidateData]:
        """同一适用范围内的全部候选（含不同来源版本），用于比对取值冲突（X008）。"""
        ...

    def set_review_reasons(
        self, workspace_id: int, candidate_id: int, reasons: tuple[ReviewReason, ...]
    ) -> None:
        """只替换核对原因，不改动字段、证据与核对状态（X008 冲突原因刷新）。"""
        ...
