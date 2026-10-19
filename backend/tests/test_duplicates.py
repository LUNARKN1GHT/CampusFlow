"""N015：重复候选建议只读生成，跨学期不合并。"""

from datetime import date

from campusflow.application.candidates import save_candidates
from campusflow.domain.candidates import (
    CandidateDraft,
    CandidateField,
    CandidateKind,
    CandidateScope,
    FieldEvidence,
    FieldOrigin,
)
from campusflow.infrastructure.db.models import Course, Semester
from campusflow.infrastructure.repositories import build_repositories

CONTENT = "实验报告下周五前提交到课程平台。"


def upload(client, title, content=CONTENT):
    response = client.post("/api/v1/materials/text", json={"title": title, "content": content})
    assert response.status_code == 201, response.text
    return response.json()


def save(client, material, scope, *, due="下周五"):
    workspace_id = client.get("/api/v1/settings").json()["workspace_id"]
    chunk_id = material["chunks"][0]["id"]
    fields = (
        CandidateField(
            "title", "实验报告", FieldOrigin.DOCUMENT, (FieldEvidence(chunk_id, "实验报告"),)
        ),
        CandidateField("due_text", due, FieldOrigin.DOCUMENT, (FieldEvidence(chunk_id, due),)),
        CandidateField(
            "submission_method",
            "课程平台",
            FieldOrigin.DOCUMENT,
            (FieldEvidence(chunk_id, "课程平台"),),
        ),
    )
    with client.app.state.session_factory() as session:
        return save_candidates(
            build_repositories(session),
            workspace_id,
            material["material"]["id"],
            1,
            [CandidateDraft(CandidateKind.TASK, fields, scope=scope)],
        )[0]


def make_scope(client, name):
    workspace_id = client.get("/api/v1/settings").json()["workspace_id"]
    with client.app.state.session_factory() as session:
        semester = Semester(
            workspace_id=workspace_id,
            name=f"学期-{name}",
            start_date=date(2026, 9, 1),
            end_date=date(2027, 1, 31),
        )
        session.add(semester)
        session.flush()
        course = Course(workspace_id=workspace_id, semester_id=semester.id, name=f"课程-{name}")
        session.add(course)
        session.flush()
        result = CandidateScope(semester.id, course.id, "一班")
        session.commit()
        return result


def test_same_scope_and_content_suggests_but_never_merges(client):
    scope = make_scope(client, "A")
    target = save(client, upload(client, "通知 A"), scope)
    duplicate = save(client, upload(client, "通知 B"), scope)
    response = client.get(f"/api/v1/candidates/{target.id}/duplicates")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["auto_merge"] is False
    assert [item["candidate_id"] for item in body["suggestions"]] == [duplicate.id]
    suggestion = body["suggestions"][0]
    assert suggestion["scope_same"] is True
    assert suggestion["score"] >= 0.6
    assert "title" in suggestion["matched_fields"]
    assert client.get(f"/api/v1/candidates/{target.id}").json()["review_status"] == "pending"
    assert client.get(f"/api/v1/candidates/{duplicate.id}").json()["review_status"] == "pending"


def test_same_name_in_different_semester_is_excluded(client):
    first = make_scope(client, "A")
    second = make_scope(client, "B")
    target = save(client, upload(client, "通知 A"), first)
    other = save(client, upload(client, "通知 B"), second)
    response = client.get(f"/api/v1/candidates/{target.id}/duplicates")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["suggestions"] == []
    assert body["excluded"][0]["candidate_id"] == other.id
    assert body["excluded"][0]["code"] == "different_semester"


def test_different_date_is_suggested_by_title_and_content_without_merging(client):
    scope = make_scope(client, "A")
    target = save(client, upload(client, "通知 A"), scope)
    save(client, upload(client, "通知 B", "实验报告下周一前提交到课程平台。"), scope, due="下周一")
    response = client.get(f"/api/v1/candidates/{target.id}/duplicates")
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["suggestions"]) == 1
    suggestion = body["suggestions"][0]
    assert suggestion["matched_fields"] == ["title"]
    assert suggestion["score"] == 0.6
    assert body["auto_merge"] is False
