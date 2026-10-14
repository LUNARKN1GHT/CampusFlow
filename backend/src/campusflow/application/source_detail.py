"""R007：读取任务与日程的来源详情（来源类型、用户补充、原文要求与引用可验证状态）。"""

from dataclasses import replace

from campusflow.application.candidates import get_candidate
from campusflow.application.ports.candidates import CandidateData
from campusflow.application.ports.repositories import Repositories, SourceChunkData
from campusflow.application.ports.source_detail import (
    SourceCitation,
    SourceCorrection,
    SourceDetail,
    SourceField,
)
from campusflow.application.scope import require_in_workspace
from campusflow.domain.candidates import CandidateField, FieldOrigin
from campusflow.domain.errors import NotFoundError
from campusflow.domain.materials import source_state
from campusflow.domain.source_detail import (
    OBJECT_TYPE_EVENT,
    OBJECT_TYPE_TASK,
    citation_is_verifiable,
    source_type,
)


def _citations(
    field: CandidateField, chunks: dict[int, SourceChunkData]
) -> tuple[tuple[SourceCitation, ...], bool]:
    """只返回仍可定位到原文的引用；有任何一条无法验证就标记该字段不可验证。"""
    citations = []
    verifiable = True
    for evidence in field.evidence:
        chunk = chunks.get(evidence.chunk_id)
        if citation_is_verifiable(chunk.text if chunk is not None else None, evidence.quote):
            citations.append(
                SourceCitation(chunk.id, evidence.quote, chunk.locator_type, chunk.locator_value)
            )
        else:
            verifiable = False
    if (
        field.origin in (FieldOrigin.DOCUMENT, FieldOrigin.INFERRED)
        and field.value is not None
        and not citations
    ):
        verifiable = False
    return tuple(citations), verifiable


def _source_fields(
    candidate: CandidateData, chunks: dict[int, SourceChunkData]
) -> tuple[SourceField, ...]:
    fields = []
    for field in candidate.draft.fields:
        citations, verifiable = _citations(field, chunks)
        fields.append(
            SourceField(
                name=field.name,
                value=field.value,
                origin=field.origin,
                note=field.explanation or field.missing_reason,
                verifiable=verifiable,
                citations=citations,
            )
        )
    return tuple(fields)


def _corrections(candidate: CandidateData) -> tuple[SourceCorrection, ...]:
    return tuple(
        SourceCorrection(
            field_name=record.revision.field_name,
            previous_value=record.revision.previous_value,
            new_value=record.revision.new_value,
            new_origin=record.revision.new_origin,
            operator=record.revision.operator,
            note=record.revision.note,
            changed_at=record.created_at,
        )
        for record in candidate.revisions
    )


def _attach_candidate(repos: Repositories, workspace_id: int, detail: SourceDetail) -> SourceDetail:
    """补上候选字段、原文引用与用户修正；没有来源候选时保持原样。"""
    if detail.candidate_id is None:
        return detail
    candidate = get_candidate(repos, workspace_id, detail.candidate_id)
    version = repos.materials.get_version(candidate.source_version_id)
    material = repos.materials.get(version.material_id) if version is not None else None
    chunks = (
        {chunk.id: chunk for chunk in repos.materials.list_chunks(version.id)}
        if version is not None
        else {}
    )
    return replace(
        detail,
        material_id=version.material_id if version is not None else detail.material_id,
        material_title=material.title if material is not None else None,
        version_no=version.version_no if version is not None else None,
        fields=_source_fields(candidate, chunks),
        corrections=_corrections(candidate),
    )


def task_source_detail(repos: Repositories, workspace_id: int, task_id: int) -> SourceDetail:
    task = repos.tasks.get(task_id)
    if task is None:
        raise NotFoundError("任务不存在")
    require_in_workspace(task.workspace_id, workspace_id, "任务不存在")
    return _attach_candidate(
        repos,
        workspace_id,
        SourceDetail(
            object_type=OBJECT_TYPE_TASK,
            object_id=task.id,
            source_type=source_type(
                candidate_id=task.source_candidate_id,
                material_id=task.source_material_id,
                invalidated_at=task.source_invalidated_at,
            ),
            source_state=source_state(task.source_material_id, task.source_invalidated_at),
            source_note=task.source_invalidated_note,
            material_id=task.source_material_id,
            material_title=None,
            version_no=None,
            candidate_id=task.source_candidate_id,
            due_source=task.due_source.value,
            due_note=task.due_note,
        ),
    )


def event_source_detail(repos: Repositories, workspace_id: int, event_id: int) -> SourceDetail:
    event = repos.schedule.get_fixed_event(event_id)
    if event is None:
        raise NotFoundError("固定日程不存在")
    require_in_workspace(event.workspace_id, workspace_id, "固定日程不存在")
    return _attach_candidate(
        repos,
        workspace_id,
        SourceDetail(
            object_type=OBJECT_TYPE_EVENT,
            object_id=event.id,
            source_type=source_type(
                candidate_id=event.source_candidate_id,
                material_id=None,
                invalidated_at=event.source_invalidated_at,
            ),
            source_state=event.source_state,
            source_note=event.source_invalidated_note,
            material_id=None,
            material_title=None,
            version_no=None,
            candidate_id=event.source_candidate_id,
        ),
    )
