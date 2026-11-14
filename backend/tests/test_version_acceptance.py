"""N014：接受字段差异、旧候选失效与证据历史。"""

from datetime import date

from campusflow.application.candidates import save_candidates
from campusflow.application.version_acceptance import accept_change
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
from campusflow.infrastructure.version_relations import SqlVersionRelations

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


def _workspace_id(client) -> int:
    return client.get("/api/v1/settings").json()["workspace_id"]


class _LaggingAcceptanceStore:
    """模拟并发请求在锁外读到的旧快照：前 skip 次查不到接受记录。"""

    def __init__(self, inner, skip: int) -> None:
        self.inner = inner
        self.skip = skip
        self.calls = 0

    def find_acceptance(self, *args):
        self.calls += 1
        if self.calls <= self.skip:
            return None
        return self.inner.find_acceptance(*args)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def _accept_in_session(client, store, left, right):
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        return accept_change(
            repos,
            store(SqlVersionRelations(session)),
            _workspace_id(client),
            baseline_candidate_id=left.id,
            comparison_candidate_id=right.id,
            field_names=("due_text",),
            reason="并发接受截止时间",
            operator="student",
        )


def test_repeat_acceptance_returns_candidate_current_status(client):
    left, right = pair(client)
    first = accept(client, left, right, ["due_text"])
    assert first.status_code == 201, first.text
    second = accept(client, left, right, ["due_text"])
    assert second.status_code == 200, second.text
    body = second.json()
    # #624：幂等响应应显示候选当前状态（已失效），旧值另用 previous_review_status 保留。
    assert body["baseline_review_status"] == "superseded"
    assert body["previous_review_status"] == "pending"
    history = client.get(f"/api/v1/candidates/{left.id}/acceptances").json()
    assert len(history) == 1
    assert history[0]["baseline_review_status"] == "superseded"
    assert history[0]["previous_review_status"] == "pending"
    assert client.get(f"/api/v1/candidates/{left.id}").json()["review_status"] == "superseded"


def test_locked_recheck_returns_record_written_by_other_request(client):
    left, right = pair(client)
    first = accept(client, left, right, ["due_text"])
    assert first.status_code == 201, first.text
    # 锁前查到 None、取锁后重新查询命中既有记录，说明并发第二个请求不会重复写入。
    result = _accept_in_session(
        client, lambda store: _LaggingAcceptanceStore(store, skip=1), left, right
    )
    assert result.created is False
    assert result.record.id == first.json()["id"]
    assert result.baseline_review_status == "superseded"
    assert len(client.get(f"/api/v1/candidates/{left.id}/acceptances").json()) == 1


def test_unique_conflict_returns_existing_acceptance(client):
    left, right = pair(client)
    workspace_id = _workspace_id(client)
    # 并发请求已完成写入但候选状态尚未被本请求看到；唯一约束兜底后应返回既有记录。
    with client.app.state.session_factory() as other:
        winner = SqlVersionRelations(other).create_acceptance(
            workspace_id,
            baseline_candidate_id=left.id,
            comparison_candidate_id=right.id,
            baseline_version_id=left.source_version_id,
            comparison_version_id=right.source_version_id,
            accepted_fields=("due_text",),
            reason="并发请求先写入",
            operator="student",
            evidence_snapshot=(),
            previous_review_status="pending",
        )
        other.commit()
    result = _accept_in_session(
        client, lambda store: _LaggingAcceptanceStore(store, skip=2), left, right
    )
    assert result.created is False
    assert result.record.id == winner.id
    history = client.get(f"/api/v1/candidates/{left.id}/acceptances").json()
    assert len(history) == 1
    assert history[0]["id"] == winner.id
