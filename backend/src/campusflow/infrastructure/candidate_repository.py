"""X001 候选事项仓储：按事项、字段、证据分表持久化。"""

from dataclasses import asdict

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from campusflow.application.ports.candidates import (
    CandidateData,
    CandidateSummaryData,
    FieldRevisionRecord,
    MergeRecordData,
    ReviewChangeRecord,
)
from campusflow.domain.candidates import (
    CandidateDraft,
    CandidateField,
    CandidateKind,
    CandidateScope,
    FieldEvidence,
    FieldOrigin,
    FieldRevision,
    ReviewChange,
    ReviewReason,
)
from campusflow.domain.errors import NotFoundError
from campusflow.domain.states import ReviewStatus
from campusflow.infrastructure.db.models import (
    CandidateFieldEvidence,
    CandidateFieldRevision,
    CandidateMerge,
    CandidateReviewChange,
    ExtractedField,
    ExtractedItem,
    MaterialVersion,
)


class SqlAlchemyCandidateRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _to_data(self, row: ExtractedItem) -> CandidateData:
        fields = self._session.scalars(
            select(ExtractedField)
            .where(ExtractedField.candidate_id == row.id)
            .order_by(ExtractedField.id)
        ).all()
        evidence_by_field = {field.id: [] for field in fields}
        if fields:
            evidence = self._session.scalars(
                select(CandidateFieldEvidence)
                .where(CandidateFieldEvidence.field_id.in_(evidence_by_field))
                .order_by(CandidateFieldEvidence.id)
            )
            for item in evidence:
                evidence_by_field[item.field_id].append(
                    FieldEvidence(item.source_chunk_id, item.quote)
                )
        revision_rows = self._session.scalars(
            select(CandidateFieldRevision)
            .join(ExtractedField, ExtractedField.id == CandidateFieldRevision.field_id)
            .where(ExtractedField.candidate_id == row.id)
            .order_by(CandidateFieldRevision.id)
        ).all()
        name_by_field = {field.id: field.name for field in fields}
        revisions = tuple(
            FieldRevisionRecord(
                revision.id,
                FieldRevision(
                    field_name=name_by_field[revision.field_id],
                    previous_value=revision.previous_value,
                    new_value=revision.new_value,
                    previous_origin=FieldOrigin(revision.previous_origin),
                    new_origin=FieldOrigin(revision.new_origin),
                    operator=revision.operator,
                    note=revision.note,
                ),
                revision.created_at,
            )
            for revision in revision_rows
        )
        change_rows = self._session.scalars(
            select(CandidateReviewChange)
            .where(CandidateReviewChange.candidate_id == row.id)
            .order_by(CandidateReviewChange.id)
        ).all()
        review_changes = tuple(
            ReviewChangeRecord(
                change.id,
                ReviewChange(
                    from_status=ReviewStatus(change.from_status),
                    to_status=ReviewStatus(change.to_status),
                    operator=change.operator,
                    reason=change.reason,
                ),
                change.changed_at,
            )
            for change in change_rows
        )
        draft = CandidateDraft(
            kind=CandidateKind(row.kind),
            scope=CandidateScope(
                row.semester_id,
                row.course_id,
                row.class_name,
                row.curriculum_plan_id,
            ),
            fields=tuple(
                CandidateField(
                    name=field.name,
                    value=field.value,
                    origin=FieldOrigin(field.origin),
                    evidence=tuple(evidence_by_field[field.id]),
                    explanation=field.explanation,
                    missing_reason=field.missing_reason,
                )
                for field in fields
            ),
            review_reasons=tuple(ReviewReason(**reason) for reason in row.review_reasons),
        )
        return CandidateData(
            row.id,
            row.workspace_id,
            row.source_version_id,
            draft,
            ReviewStatus(row.review_status),
            row.created_at,
            revisions=revisions,
            review_changes=review_changes,
        )

    def create(self, workspace_id: int, version_id: int, draft: CandidateDraft) -> CandidateData:
        row = ExtractedItem(
            workspace_id=workspace_id,
            source_version_id=version_id,
            kind=draft.kind.value,
            review_status=ReviewStatus.PENDING.value,
            semester_id=draft.scope.semester_id,
            course_id=draft.scope.course_id,
            class_name=draft.scope.class_name,
            curriculum_plan_id=draft.scope.curriculum_plan_id,
            review_reasons=[asdict(reason) for reason in draft.review_reasons],
        )
        self._session.add(row)
        self._session.flush()
        for field in draft.fields:
            field_row = ExtractedField(
                candidate_id=row.id,
                name=field.name,
                value=field.value,
                origin=field.origin.value,
                explanation=field.explanation,
                missing_reason=field.missing_reason,
            )
            self._session.add(field_row)
            self._session.flush()
            self._session.add_all(
                [
                    CandidateFieldEvidence(
                        field_id=field_row.id, source_chunk_id=e.chunk_id, quote=e.quote
                    )
                    for e in field.evidence
                ]
            )
        self._session.flush()
        return self._to_data(row)

    def get(self, workspace_id: int, candidate_id: int) -> CandidateData | None:
        row = self._session.scalar(
            select(ExtractedItem).where(
                ExtractedItem.id == candidate_id, ExtractedItem.workspace_id == workspace_id
            )
        )
        return self._to_data(row) if row is not None else None

    def list_by_version(self, workspace_id: int, version_id: int) -> list[CandidateData]:
        rows = self._session.scalars(
            select(ExtractedItem)
            .where(
                ExtractedItem.workspace_id == workspace_id,
                ExtractedItem.source_version_id == version_id,
            )
            .order_by(ExtractedItem.id)
        )
        return [self._to_data(row) for row in rows]

    def list_all(self, workspace_id: int, kind: CandidateKind) -> list[CandidateData]:
        rows = self._session.scalars(
            select(ExtractedItem)
            .where(
                ExtractedItem.workspace_id == workspace_id,
                ExtractedItem.kind == kind.value,
            )
            .order_by(ExtractedItem.id)
        )
        return [self._to_data(row) for row in rows]

    def ids_by_material(self, workspace_id: int, material_id: int) -> list[int]:
        return list(
            self._session.scalars(
                select(ExtractedItem.id)
                .join(MaterialVersion, MaterialVersion.id == ExtractedItem.source_version_id)
                .where(
                    ExtractedItem.workspace_id == workspace_id,
                    MaterialVersion.material_id == material_id,
                )
                .order_by(ExtractedItem.id)
            )
        )

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
        stmt = (
            select(ExtractedItem, MaterialVersion.material_id, MaterialVersion.version_no)
            .join(MaterialVersion, MaterialVersion.id == ExtractedItem.source_version_id)
            .where(ExtractedItem.workspace_id == workspace_id)
        )
        if review_statuses is not None:
            stmt = stmt.where(
                ExtractedItem.review_status.in_([status.value for status in review_statuses])
            )
        if kinds is not None:
            stmt = stmt.where(ExtractedItem.kind.in_(list(kinds)))
        if course_id is not None:
            stmt = stmt.where(ExtractedItem.course_id == course_id)
        if semester_id is not None:
            stmt = stmt.where(ExtractedItem.semester_id == semester_id)
        if material_id is not None:
            stmt = stmt.where(MaterialVersion.material_id == material_id)
        stmt = stmt.order_by(ExtractedItem.id)
        rows = self._session.execute(stmt).all()
        if not rows:
            return []
        label_fields: dict[int, dict[str, object]] = {}
        for row in self._session.execute(
            select(ExtractedField.candidate_id, ExtractedField.name, ExtractedField.value).where(
                ExtractedField.candidate_id.in_([item[0].id for item in rows]),
                ExtractedField.name.in_(
                    ("title", "course_name", "from_course_name", "to_course_name")
                ),
            )
        ).all():
            label_fields.setdefault(row.candidate_id, {})[row.name] = row.value
        summaries = []
        for item, item_material_id, item_version_no in rows:
            labels = label_fields.get(item.id, {})
            title = labels.get("title") or labels.get("course_name")
            if title is None and labels.get("from_course_name") and labels.get("to_course_name"):
                title = f"{labels['from_course_name']} → {labels['to_course_name']}"
            summaries.append(
                CandidateSummaryData(
                    id=item.id,
                    workspace_id=item.workspace_id,
                    kind=item.kind,
                    review_status=ReviewStatus(item.review_status),
                    title=title if isinstance(title, str) else None,
                    course_id=item.course_id,
                    semester_id=item.semester_id,
                    class_name=item.class_name,
                    curriculum_plan_id=item.curriculum_plan_id,
                    source_version_id=item.source_version_id,
                    source_material_id=item_material_id,
                    version_no=item_version_no,
                    review_reasons=tuple(
                        (reason["code"], reason["message"], reason.get("field"))
                        for reason in item.review_reasons
                    ),
                    created_at=item.created_at,
                )
            )
        return summaries

    def add_field_revision(
        self, workspace_id: int, candidate_id: int, revision: FieldRevision
    ) -> FieldRevisionRecord:
        field_row = self._session.scalar(
            select(ExtractedField)
            .join(ExtractedItem, ExtractedItem.id == ExtractedField.candidate_id)
            .where(
                ExtractedItem.workspace_id == workspace_id,
                ExtractedItem.id == candidate_id,
                ExtractedField.name == revision.field_name,
            )
        )
        if field_row is None:
            raise NotFoundError("候选字段不存在")
        row = CandidateFieldRevision(
            field_id=field_row.id,
            previous_value=revision.previous_value,
            new_value=revision.new_value,
            previous_origin=revision.previous_origin.value,
            new_origin=revision.new_origin.value,
            operator=revision.operator,
            note=revision.note,
        )
        self._session.add(row)
        self._session.flush()
        return FieldRevisionRecord(row.id, revision, row.created_at)

    def set_review_status(
        self, workspace_id: int, candidate_id: int, change: ReviewChange
    ) -> ReviewChangeRecord:
        row = self._session.scalar(
            select(ExtractedItem).where(
                ExtractedItem.id == candidate_id, ExtractedItem.workspace_id == workspace_id
            )
        )
        if row is None:
            raise NotFoundError("候选事项不存在")
        row.review_status = change.to_status.value
        record = CandidateReviewChange(
            candidate_id=row.id,
            from_status=change.from_status.value,
            to_status=change.to_status.value,
            operator=change.operator,
            reason=change.reason,
        )
        self._session.add(record)
        self._session.flush()
        return ReviewChangeRecord(record.id, change, record.changed_at)

    def list_by_scope(
        self,
        workspace_id: int,
        semester_id: int | None,
        course_id: int | None,
        class_name: str | None,
    ) -> list[CandidateData]:
        rows = self._session.scalars(
            select(ExtractedItem)
            .where(
                ExtractedItem.workspace_id == workspace_id,
                ExtractedItem.semester_id.is_(None)
                if semester_id is None
                else ExtractedItem.semester_id == semester_id,
                ExtractedItem.course_id.is_(None)
                if course_id is None
                else ExtractedItem.course_id == course_id,
                ExtractedItem.class_name.is_(None)
                if class_name is None
                else ExtractedItem.class_name == class_name,
            )
            .order_by(ExtractedItem.id)
        )
        return [self._to_data(row) for row in rows]

    def set_review_reasons(
        self, workspace_id: int, candidate_id: int, reasons: tuple[ReviewReason, ...]
    ) -> None:
        row = self._session.scalar(
            select(ExtractedItem).where(
                ExtractedItem.id == candidate_id, ExtractedItem.workspace_id == workspace_id
            )
        )
        if row is None:
            return
        # JSONB 列必须整体赋值：原地修改不会被 SQLAlchemy 视为变更
        row.review_reasons = [asdict(reason) for reason in reasons]
        self._session.flush()

    def find_merge(
        self, workspace_id: int, target_candidate_id: int, source_candidate_id: int
    ) -> MergeRecordData | None:
        row = self._session.scalar(
            select(CandidateMerge).where(
                CandidateMerge.workspace_id == workspace_id,
                CandidateMerge.target_candidate_id == target_candidate_id,
                CandidateMerge.source_candidate_id == source_candidate_id,
            )
        )
        return _merge_data(row) if row else None

    def create_merge(
        self,
        workspace_id: int,
        *,
        target_candidate_id: int,
        source_candidate_id: int,
        target_formal_kind: str | None,
        target_formal_id: int | None,
        source_formal_id: int | None,
        preserved_progress: dict,
        source_snapshot: dict,
        reason: str,
        operator: str,
    ) -> MergeRecordData:
        row = CandidateMerge(
            workspace_id=workspace_id,
            target_candidate_id=target_candidate_id,
            source_candidate_id=source_candidate_id,
            target_formal_kind=target_formal_kind,
            target_formal_id=target_formal_id,
            source_formal_id=source_formal_id,
            preserved_progress=preserved_progress,
            source_snapshot=source_snapshot,
            reason=reason,
            operator=operator,
        )
        self._session.add(row)
        self._session.flush()
        return _merge_data(row)

    def list_merges(self, workspace_id: int, candidate_id: int) -> list[MergeRecordData]:
        rows = self._session.scalars(
            select(CandidateMerge)
            .where(
                CandidateMerge.workspace_id == workspace_id,
                or_(
                    CandidateMerge.target_candidate_id == candidate_id,
                    CandidateMerge.source_candidate_id == candidate_id,
                ),
            )
            .order_by(CandidateMerge.id)
        )
        return [_merge_data(row) for row in rows]


def _merge_data(row: CandidateMerge) -> MergeRecordData:
    return MergeRecordData(
        id=row.id,
        target_candidate_id=row.target_candidate_id,
        source_candidate_id=row.source_candidate_id,
        target_formal_kind=row.target_formal_kind,
        target_formal_id=row.target_formal_id,
        source_formal_id=row.source_formal_id,
        preserved_progress=dict(row.preserved_progress),
        source_snapshot=dict(row.source_snapshot),
        reason=row.reason,
        operator=row.operator,
        created_at=row.created_at,
    )
