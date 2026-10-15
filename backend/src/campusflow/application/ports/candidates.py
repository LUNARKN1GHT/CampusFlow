"""候选存储端口：字段和证据分别保存，持久化实现不泄漏到应用层。"""

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

from campusflow.domain.candidates import (
    CandidateDraft,
    FieldRevision,
    ReviewChange,
    ReviewReason,
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


@dataclass(frozen=True)
class CandidateSummaryData:
    """R001：待核对列表用的轻量候选信息。

    列表只展示判断所需的最小字段，逐字段取值与证据仍由 get_candidate 读取；
    标题取原始提取值，未提取到标题时为空，不拿别的字段凑数。
    """

    id: int
    workspace_id: int
    kind: str
    review_status: ReviewStatus
    title: str | None
    course_id: int | None
    semester_id: int | None
    class_name: str | None
    source_version_id: int
    source_material_id: int
    version_no: int
    review_reasons: tuple[tuple[str, str, str | None], ...]
    created_at: datetime


class CandidateRepository(Protocol):
    def create(
        self, workspace_id: int, version_id: int, draft: CandidateDraft
    ) -> CandidateData: ...

    def get(self, workspace_id: int, candidate_id: int) -> CandidateData | None: ...

    def list_by_version(self, workspace_id: int, version_id: int) -> list[CandidateData]: ...

    def list_for_review(
        self,
        workspace_id: int,
        *,
        review_statuses: tuple[ReviewStatus, ...] | None = None,
        kinds: tuple[str, ...] | None = None,
        course_id: int | None = None,
        semester_id: int | None = None,
        material_id: int | None = None,
    ) -> list[CandidateSummaryData]:
        """按核对状态、类型与适用范围列出候选（R001）。

        缺失原因（review_reasons）由调用方在领域层过滤：原因存在 JSON 列里，
        代码取值随 X 系列提取任务增加，用 SQL 硬编码条件会漏掉新增原因。
        """
        ...

    def ids_by_material(self, workspace_id: int, material_id: int) -> list[int]: ...

    def add_field_revision(
        self, workspace_id: int, candidate_id: int, revision: FieldRevision
    ) -> FieldRevisionRecord: ...

    def set_review_status(
        self, workspace_id: int, candidate_id: int, change: ReviewChange
    ) -> ReviewChangeRecord: ...

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
