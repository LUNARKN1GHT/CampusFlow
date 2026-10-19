"""N012：发布信息、范围与替代声明决定生效候选或待核对冲突。"""

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

OLD_CONTENT = "实验报告下周五前提交。"
NEW_CONTENT = "实验报告下周三前提交。"


def upload(client, title, content, published_at=None):
    payload = {"title": title, "content": content}
    if published_at is not None:
        payload["published_at"] = published_at
    response = client.post("/api/v1/materials/text", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def draft(chunk_id, due, title="实验报告", scope=None):
    return CandidateDraft(
        CandidateKind.TASK,
        (
            CandidateField("title", title, FieldOrigin.DOCUMENT, (FieldEvidence(chunk_id, title),)),
            CandidateField("due_text", due, FieldOrigin.DOCUMENT, (FieldEvidence(chunk_id, due),)),
        ),
        scope=scope or CandidateScope(),
    )


def save(client, material, due, scope=None):
    workspace_id = client.get("/api/v1/settings").json()["workspace_id"]
    with client.app.state.session_factory() as session:
        return save_candidates(
            build_repositories(session),
            workspace_id,
            material["material"]["id"],
            1,
            [draft(material["chunks"][0]["id"], due, scope=scope)],
        )[0]


def pair(
    client,
    *,
    old_published="2026-10-01T10:00:00+08:00",
    new_published="2026-10-02T10:00:00+08:00",
    old_scope=None,
    new_scope=None,
):
    old = upload(client, "旧通知", OLD_CONTENT, old_published)
    new = upload(client, "新通知", NEW_CONTENT, new_published)
    return old, new, save(client, old, "下周五", old_scope), save(client, new, "下周三", new_scope)


def add_relation(client, source_version, target_version, statement="修订通知明确替代原通知"):
    response = client.post(
        f"/api/v1/material-versions/{source_version}/relations",
        json={
            "target_version_id": target_version,
            "kind": "replacement_claim",
            "statement": statement,
        },
    )
    assert response.status_code in {200, 201}, response.text
    return response.json()


def assess(client, left, right):
    response = client.get(f"/api/v1/candidates/{left.id}/diff/{right.id}/applicability")
    assert response.status_code == 200, response.text
    return response.json()


def add_scope(client, *, name="一班"):
    workspace_id = client.get("/api/v1/settings").json()["workspace_id"]
    with client.app.state.session_factory() as session:
        semester = Semester(
            workspace_id=workspace_id,
            name=f"秋季-{name}",
            start_date=date(2026, 9, 1),
            end_date=date(2027, 1, 31),
        )
        session.add(semester)
        session.flush()
        course = Course(workspace_id=workspace_id, semester_id=semester.id, name="数据库原理")
        session.add(course)
        session.flush()
        semester_id, course_id = semester.id, course.id
        session.commit()
    return CandidateScope(semester_id=semester_id, course_id=course_id, class_name=name)


def test_missing_replacement_declaration_requires_review_without_writes(client):
    old, new, left, right = pair(client)
    body = assess(client, left, right)
    assert body["status"] == "requires_review"
    assert body["review_required"] is True
    assert body["applied"] is False
    assert body["recommended_candidate_id"] is None
    assert "missing_replacement_declaration" in [item["code"] for item in body["reasons"]]
    assert client.get(f"/api/v1/candidates/{left.id}").json()["review_status"] == "pending"
    assert client.get(f"/api/v1/candidates/{right.id}").json()["review_status"] == "pending"


def test_explicit_replacement_with_same_scope_and_newer_publication_is_applicable(client):
    scope = add_scope(client)
    old, new, left, right = pair(client, old_scope=scope, new_scope=scope)
    relation = add_relation(client, new["chunks"][0]["version_id"], old["chunks"][0]["version_id"])
    body = assess(client, left, right)
    assert body["status"] == "applicable"
    assert body["review_required"] is False
    assert body["applied"] is False
    assert body["recommended_candidate_id"] == right.id
    assert body["relation_id"] == relation["id"]
    assert body["scope_comparison"] == "same"


def test_later_import_of_older_notice_never_overrides(client):
    scope = add_scope(client)
    old, new, left, right = pair(
        client,
        old_published="2026-10-02T10:00:00+08:00",
        new_published="2026-10-01T10:00:00+08:00",
        old_scope=scope,
        new_scope=scope,
    )
    add_relation(client, new["chunks"][0]["version_id"], old["chunks"][0]["version_id"])
    body = assess(client, left, right)
    assert body["status"] == "conflict"
    assert body["recommended_candidate_id"] is None
    assert "published_before_baseline" in [item["code"] for item in body["reasons"]]


def test_different_scope_or_missing_publication_goes_to_review(client):
    old_scope = add_scope(client, name="一班")
    new_scope = add_scope(client, name="二班")
    old, new, left, right = pair(
        client,
        new_published=None,
        old_scope=old_scope,
        new_scope=new_scope,
    )
    add_relation(client, new["chunks"][0]["version_id"], old["chunks"][0]["version_id"])
    body = assess(client, left, right)
    assert body["status"] == "conflict"
    assert "scope_different" in [item["code"] for item in body["reasons"]]
    assert client.get(f"/api/v1/candidates/{right.id}").json()["review_status"] == "pending"
