"""X001 候选事项仓储：按事项、字段、证据分表持久化。"""

from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from campusflow.application.ports.candidates import CandidateData
from campusflow.domain.candidates import (
    CandidateDraft,
    CandidateField,
    CandidateKind,
    CandidateScope,
    FieldEvidence,
    FieldOrigin,
    ReviewReason,
)
from campusflow.domain.states import ReviewStatus
from campusflow.infrastructure.db.models import (
    CandidateFieldEvidence,
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
        draft = CandidateDraft(
            kind=CandidateKind(row.kind),
            scope=CandidateScope(row.semester_id, row.course_id, row.class_name),
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
