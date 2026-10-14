"""R007：任务与日程的来源详情；资料删除后不再返回可验证引用。"""

import pytest
from sqlalchemy import select

from campusflow.application.candidates import (
    revise_candidate_field,
    save_candidates,
    set_candidate_review_status,
)
from campusflow.application.conversion import (
    convert_candidate_to_event,
    convert_candidate_to_task,
)
from campusflow.application.source_detail import task_source_detail
from campusflow.domain.candidates import (
    CandidateDraft,
    CandidateField,
    CandidateKind,
    FieldEvidence,
    FieldOrigin,
)
from campusflow.domain.errors import NotFoundError
from campusflow.domain.states import ReviewStatus
from campusflow.infrastructure.db.models import SourceChunk
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.storage.local import LocalFileStorage

CONTENT = "2026-10-16 前提交实验报告，讲座在三教 301 举行。\n\n保留原文定位。"
QUOTE_TITLE = "实验报告"
QUOTE_SUBMIT = "前提交"
QUOTE_DATE = "2026-10-16"
QUOTE_LOCATION = "三教 301"


def task_draft(chunk_id=1):
    return CandidateDraft(
        CandidateKind.TASK,
        (
            CandidateField(
                "title", QUOTE_TITLE, FieldOrigin.DOCUMENT, (FieldEvidence(chunk_id, QUOTE_TITLE),)
            ),
            CandidateField(
                "submission_method",
                "课程平台",
                FieldOrigin.DOCUMENT,
                (FieldEvidence(chunk_id, QUOTE_SUBMIT),),
            ),
            CandidateField(
                "due_text", QUOTE_DATE, FieldOrigin.DOCUMENT, (FieldEvidence(chunk_id, QUOTE_DATE),)
            ),
            CandidateField(
                "estimated_minutes", 90, FieldOrigin.SUGGESTION, explanation="初步耗时建议"
            ),
        ),
    )


def event_draft(chunk_id=1):
    return CandidateDraft(
        CandidateKind.EVENT,
        (
            CandidateField(
                "title", "讲座", FieldOrigin.DOCUMENT, (FieldEvidence(chunk_id, QUOTE_TITLE),)
            ),
            CandidateField(
                "activity_text",
                "讲座在三教 301 举行",
                FieldOrigin.DOCUMENT,
                (FieldEvidence(chunk_id, QUOTE_LOCATION),),
            ),
            CandidateField(
                "start_text",
                "2026-10-16T14:00",
                FieldOrigin.DOCUMENT,
                (FieldEvidence(chunk_id, QUOTE_DATE),),
            ),
            CandidateField(
                "end_text",
                "2026-10-16T16:00",
                FieldOrigin.DOCUMENT,
                (FieldEvidence(chunk_id, QUOTE_DATE),),
            ),
            CandidateField(
                "location",
                QUOTE_LOCATION,
                FieldOrigin.DOCUMENT,
                (FieldEvidence(chunk_id, QUOTE_LOCATION),),
            ),
        ),
    )


@pytest.fixture
def source(client, tmp_path, monkeypatch):
    monkeypatch.setattr(client.app.state, "file_storage", LocalFileStorage(tmp_path))
    response = client.post("/api/v1/materials/text", json={"title": "课程通知", "content": CONTENT})
    assert response.status_code == 201
    result = response.json()
    result["material"]["workspace_id"] = client.get("/api/v1/settings").json()["workspace_id"]
    return client, result["material"], result["chunks"]


def make_candidate(source, draft, *, confirm=True):
    client, material, chunks = source
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        saved = save_candidates(
            repos,
            material["workspace_id"],
            material["id"],
            1,
            [draft(chunks[0]["id"])],
        )[0]
        if confirm:
            set_candidate_review_status(
                repos,
                material["workspace_id"],
                saved.id,
                ReviewStatus.CONFIRMED,
                operator="student",
            )
        return saved.id, material["workspace_id"]


def convert(source, candidate_id, *, event=False):
    client, material, _ = source
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        if event:
            return convert_candidate_to_event(repos, material["workspace_id"], candidate_id)
        return convert_candidate_to_task(repos, material["workspace_id"], candidate_id)


def test_task_source_detail_reports_origins_and_verifiable_citations(source):
    client, material, _ = source
    candidate_id, workspace_id = make_candidate(source, task_draft)
    task = convert(source, candidate_id)
    response = client.get(f"/api/v1/tasks/{task.id}/source")
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["object_type"] == "task"
    assert detail["object_id"] == task.id
    assert detail["source_type"] == "extracted"
    assert detail["source_state"] == "available"
    assert detail["candidate_id"] == candidate_id
    assert detail["material_id"] == material["id"]
    assert detail["material_title"] == "课程通知"
    assert detail["version_no"] == 1
    assert detail["due_source"] == "document"
    fields = {field["name"]: field for field in detail["fields"]}
    due = fields["due_text"]
    assert due["origin"] == "document" and due["value"] == QUOTE_DATE
    assert due["verifiable"] is True
    assert due["citations"] == [
        {
            "chunk_id": due["citations"][0]["chunk_id"],
            "quote": QUOTE_DATE,
            "locator_type": due["citations"][0]["locator_type"],
            "locator_value": due["citations"][0]["locator_value"],
        }
    ]
    assert fields["estimated_minutes"]["origin"] == "suggestion"
    assert fields["estimated_minutes"]["note"] == "初步耗时建议"
    assert detail["corrections"] == []


def test_user_correction_appears_as_user_supplement(source):
    client, material, _ = source
    candidate_id, workspace_id = make_candidate(source, task_draft)
    with client.app.state.session_factory() as session:
        revise_candidate_field(
            build_repositories(session),
            workspace_id,
            candidate_id,
            "due_text",
            value="2026-10-20",
            origin=FieldOrigin.USER,
            operator="student",
            note="按群通知调整",
        )
    task = convert(source, candidate_id)
    detail = client.get(f"/api/v1/tasks/{task.id}/source").json()
    corrections = detail["corrections"]
    assert len(corrections) == 1
    assert corrections[0]["field_name"] == "due_text"
    assert corrections[0]["previous_value"] == QUOTE_DATE
    assert corrections[0]["new_value"] == "2026-10-20"
    assert corrections[0]["new_origin"] == "user"
    assert corrections[0]["operator"] == "student"
    fields = {field["name"]: field for field in detail["fields"]}
    # 原始提取与证据保留，修正不改写候选字段
    assert fields["due_text"]["value"] == QUOTE_DATE
    assert fields["due_text"]["citations"][0]["quote"] == QUOTE_DATE


def test_manual_task_is_reported_as_manual(source):
    client, _, _ = source
    created = client.post("/api/v1/tasks", json={"title": "自建任务"})
    assert created.status_code == 201
    detail = client.get(f"/api/v1/tasks/{created.json()['id']}/source").json()
    assert detail["source_type"] == "manual"
    assert detail["source_state"] == "none"
    assert detail["material_id"] is None and detail["candidate_id"] is None
    assert detail["fields"] == [] and detail["corrections"] == []
    assert detail["source_note"] is None


def test_event_source_detail_and_manual_event(source):
    client, material, _ = source
    candidate_id, _ = make_candidate(source, event_draft)
    event = convert(source, candidate_id, event=True)
    detail = client.get(f"/api/v1/fixed-events/{event.id}/source").json()
    assert detail["object_type"] == "fixed_event"
    assert detail["source_type"] == "extracted"
    assert detail["source_state"] == "available"
    assert detail["candidate_id"] == candidate_id
    assert detail["material_id"] == material["id"]
    fields = {field["name"]: field for field in detail["fields"]}
    assert fields["location"]["value"] == QUOTE_LOCATION
    assert fields["location"]["verifiable"] is True
    manual = client.post(
        "/api/v1/fixed-events",
        json={
            "title": "自习",
            "starts_at": "2026-10-21T09:00:00",
            "ends_at": "2026-10-21T11:00:00",
        },
    )
    assert manual.status_code == 201
    manual_detail = client.get(f"/api/v1/fixed-events/{manual.json()['id']}/source").json()
    assert manual_detail["source_type"] == "manual"
    assert manual_detail["source_state"] == "none"
    assert manual_detail["fields"] == []


def test_unknown_object_is_not_found_and_scope_is_enforced(source):
    client, material, _ = source
    assert client.get("/api/v1/tasks/999999/source").status_code == 404
    assert client.get("/api/v1/fixed-events/999999/source").status_code == 404
    candidate_id, _ = make_candidate(source, task_draft)
    task = convert(source, candidate_id)
    with client.app.state.session_factory() as session:
        with pytest.raises(NotFoundError):
            task_source_detail(build_repositories(session), material["workspace_id"] + 100, task.id)


def test_deleted_material_stops_returning_verifiable_citations(source):
    client, material, _ = source
    task_candidate, _ = make_candidate(source, task_draft)
    event_candidate, _ = make_candidate(source, event_draft)
    task = convert(source, task_candidate)
    event = convert(source, event_candidate, event=True)
    mid = material["id"]
    preview = client.get(f"/api/v1/materials/{mid}/delete-impact").json()
    assert preview["linked_fixed_event_ids"] == [event.id]
    response = client.post(
        f"/api/v1/materials/{mid}/delete",
        json={"confirm_token": preview["confirm_token"], "keep_tasks": True},
    )
    assert response.status_code == 204, response.text

    task_detail = client.get(f"/api/v1/tasks/{task.id}/source").json()
    assert task_detail["source_type"] == "extracted"
    assert task_detail["source_state"] == "invalidated"
    assert task_detail["source_note"] is not None
    assert task_detail["candidate_id"] is None
    assert task_detail["fields"] == [] and task_detail["corrections"] == []

    as_event = client.get(f"/api/v1/fixed-events/{event.id}/source")
    assert as_event.status_code == 200
    event_detail = as_event.json()
    assert event_detail["source_type"] == "extracted"
    assert event_detail["source_state"] == "invalidated"
    assert event_detail["fields"] == []
    listed = client.get("/api/v1/fixed-events").json()
    assert [item["id"] for item in listed] == [event.id]


def test_unresolvable_citation_is_not_returned_as_verifiable(source):
    client, material, _ = source
    candidate_id, workspace_id = make_candidate(source, task_draft)
    task = convert(source, candidate_id)
    with client.app.state.session_factory() as session:
        chunk = session.scalars(select(SourceChunk)).first()
        session.delete(chunk)
        session.commit()
    with client.app.state.session_factory() as session:
        detail = task_source_detail(build_repositories(session), workspace_id, task.id)
    due = next(field for field in detail.fields if field.name == "due_text")
    assert due.citations == ()
    assert due.verifiable is False
    assert due.value == QUOTE_DATE  # 值仍如实展示，只是不再声称引用可验证


def test_conversion_manually_removed_event_still_listed_in_preview(source):
    client, material, _ = source
    mid = material["id"]
    empty = client.get(f"/api/v1/materials/{mid}/delete-impact").json()
    assert empty["linked_fixed_event_ids"] == []
    candidate_id, _ = make_candidate(source, event_draft)
    event = convert(source, candidate_id, event=True)
    filled = client.get(f"/api/v1/materials/{mid}/delete-impact").json()
    assert filled["linked_fixed_event_ids"] == [event.id]
    assert filled["confirm_token"] != empty["confirm_token"]
