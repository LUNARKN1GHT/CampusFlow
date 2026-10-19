"""N014：接受字段差异、旧候选失效与证据历史。"""

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


def upload(client, title, content, published_at):
    response = client.post(
        "/api/v1/materials/text",
        json={"title": title, "content": content, "published_at": published_at},
    )
    assert response.status_code == 201, response.text
    return response.json()


def make_scope(client, name="一班"):
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
        scope = CandidateScope(semester.id, course.id, name)
        session.commit()
        return scope


def save(client, material, due, scope):
    workspace_id = client.get("/api/v1/settings").json()["workspace_id"]
    chunk_id = material["chunks"][0]["id"]
    draft = CandidateDraft(
        CandidateKind.TASK,
        (
            CandidateField(
                "title", "实验报告", FieldOrigin.DOCUMENT, (FieldEvidence(chunk_id, "实验报告"),)
            ),
            CandidateField("due_text", due, FieldOrigin.DOCUMENT, (FieldEvidence(chunk_id, due),)),
        ),
        scope=scope,
    )
    with client.app.state.session_factory() as session:
        return save_candidates(
            build_repositories(session), workspace_id, material["material"]["id"], 1, [draft]
        )[0]


def pair(
    client,
    *,
    old_published="2026-10-01T10:00:00+08:00",
    new_published="2026-10-02T10:00:00+08:00",
):
    scope = make_scope(client)
    old = upload(client, "旧通知", OLD_CONTENT, old_published)
    new = upload(client, "新通知", NEW_CONTENT, new_published)
    left = save(client, old, "下周五", scope)
    right = save(client, new, "下周三", scope)
    relation = client.post(
        f"/api/v1/material-versions/{new['chunks'][0]['version_id']}/relations",
        json={
            "target_version_id": old["chunks"][0]["version_id"],
            "kind": "replacement_claim",
            "statement": "修订通知明确替代原通知",
        },
    )
    assert relation.status_code in {200, 201}, relation.text
    return left, right


def accept(client, left, right, fields, *, reason="按修订通知接受截止时间", acknowledge=False):
    return client.post(
        f"/api/v1/candidates/{left.id}/diff/{right.id}/accept",
        json={
            "field_names": fields,
            "reason": reason,
            "acknowledge_conflict": acknowledge,
        },
    )


def test_accept_supersedes_old_candidate_and_keeps_evidence(client):
    left, right = pair(client)
    response = accept(client, left, right, ["due_text"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["baseline_review_status"] == "superseded"
    assert body["accepted_fields"] == ["due_text"]
    assert body["reason"] == "按修订通知接受截止时间"
    assert client.get(f"/api/v1/candidates/{left.id}").json()["review_status"] == "superseded"
    history = client.get(f"/api/v1/candidates/{left.id}/acceptances")
    assert history.status_code == 200, history.text
    record = history.json()[0]
    assert record["baseline_version_id"] != record["comparison_version_id"]
    evidence = record["evidence_snapshot"][0]
    assert evidence["name"] == "due_text"
    assert evidence["left"]["evidence"][0]["quote"] == "下周五"
    assert evidence["right"]["evidence"][0]["quote"] == "下周三"


def test_repeat_acceptance_is_idempotent(client):
    left, right = pair(client)
    first = accept(client, left, right, ["due_text"])
    second = accept(client, left, right, ["due_text"])
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    history = client.get(f"/api/v1/candidates/{left.id}/acceptances").json()
    assert len(history) == 1
    assert client.get(f"/api/v1/candidates/{left.id}").json()["review_status"] == "superseded"


def test_conflict_requires_explicit_acknowledgement(client):
    left, right = pair(
        client,
        old_published="2026-10-02T10:00:00+08:00",
        new_published="2026-10-01T10:00:00+08:00",
    )
    response = accept(client, left, right, ["due_text"])
    assert response.status_code == 400
    assert "conflict" in response.json()["detail"]
    assert client.get(f"/api/v1/candidates/{left.id}").json()["review_status"] == "pending"
    acknowledged = accept(client, left, right, ["due_text"], acknowledge=True)
    assert acknowledged.status_code == 201, acknowledged.text
    assert client.get(f"/api/v1/candidates/{left.id}").json()["review_status"] == "superseded"


def test_unknown_or_unchanged_field_is_rejected(client):
    left, right = pair(client)
    unknown = accept(client, left, right, ["channel"])
    assert unknown.status_code == 400
    assert "不在本次差异中" in unknown.json()["detail"]
    unchanged = accept(client, left, right, ["title"])
    assert unchanged.status_code == 400
    assert "没有变化" in unchanged.json()["detail"]


def test_invalid_field_and_reason_are_rejected_without_status_change(client):
    left, right = pair(client)
    response = accept(client, left, right, ["due_text"], reason="")
    assert response.status_code == 422
    assert client.get(f"/api/v1/candidates/{left.id}").json()["review_status"] == "pending"


def test_confirmed_old_candidate_can_be_superseded(client):
    left, right = pair(client)
    confirmed = client.patch(
        f"/api/v1/candidates/{left.id}/review-status",
        json={"status": "confirmed", "reason": "旧通知已核对"},
    )
    assert confirmed.status_code == 200, confirmed.text
    response = accept(client, left, right, ["due_text"])
    assert response.status_code == 201, response.text
    assert client.get(f"/api/v1/candidates/{left.id}").json()["review_status"] == "superseded"
