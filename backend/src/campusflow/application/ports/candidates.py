"""候选存储端口：字段和证据分别保存，持久化实现不泄漏到应用层。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from campusflow.domain.candidates import CandidateDraft
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
