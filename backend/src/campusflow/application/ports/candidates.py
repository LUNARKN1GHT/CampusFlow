"""候选存储端口：字段和证据分别保存，持久化实现不泄漏到应用层。"""

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

from campusflow.domain.candidates import (
    CandidateDraft,
    FieldRevision,
    ReviewChange,
    effective_fields,
)
from campusflow.domain.states import ReviewStatus


@dataclass(frozen=True)
class FieldRevisionRecord:
    """R002：一条已保存的字段修正记录。"""

    id: int
    revision: FieldRevision
    created_at: datetime


@dataclass(frozen=True)
class ReviewChangeRecord:
    """R003：一条已保存的核对状态变更记录。"""

    id: int
    change: ReviewChange
    changed_at: datetime


@dataclass(frozen=True)
class CandidateData:
    id: int
    workspace_id: int
    source_version_id: int
    draft: CandidateDraft
    review_status: ReviewStatus
    created_at: datetime
    revisions: tuple[FieldRevisionRecord, ...] = ()
    review_changes: tuple[ReviewChangeRecord, ...] = ()

    def field_revisions(self, field_name: str) -> tuple[FieldRevision, ...]:
        return tuple(
            record.revision for record in self.revisions if record.revision.field_name == field_name
        )

    def effective_draft(self) -> CandidateDraft:
        """原始字段叠加全部修正后的草稿，供核对与确认校验使用。"""
        return replace(
            self.draft,
            fields=effective_fields(
                self.draft.fields, [record.revision for record in self.revisions]
            ),
        )


class CandidateRepository(Protocol):
    def create(
        self, workspace_id: int, version_id: int, draft: CandidateDraft
    ) -> CandidateData: ...

    def get(self, workspace_id: int, candidate_id: int) -> CandidateData | None: ...

    def list_by_version(self, workspace_id: int, version_id: int) -> list[CandidateData]: ...

    def ids_by_material(self, workspace_id: int, material_id: int) -> list[int]: ...

    def add_field_revision(
        self, workspace_id: int, candidate_id: int, revision: FieldRevision
    ) -> FieldRevisionRecord: ...

    def set_review_status(
        self, workspace_id: int, candidate_id: int, change: ReviewChange
    ) -> ReviewChangeRecord: ...
