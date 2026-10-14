"""X001：字段来源、独立核对状态、真实片段关联与原子持久化。"""

from dataclasses import replace
from datetime import date

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from campusflow.application.candidates import get_candidate, save_candidates
from campusflow.domain.candidates import (
    CandidateDraft,
    CandidateField,
    CandidateKind,
    CandidateScope,
    FieldEvidence,
    FieldOrigin,
    ReviewReason,
    validate_candidate,
)
from campusflow.domain.errors import DomainError, NotFoundError
from campusflow.domain.states import ReviewStatus
from campusflow.infrastructure.db.models import (
    CandidateFieldEvidence,
    Course,
    ExtractedField,
    ExtractedItem,
    Semester,
    Workspace,
)
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.storage.local import LocalFileStorage


def draft_for(chunk_id=1):
    return CandidateDraft(
        CandidateKind.TASK,
        (
            CandidateField(
                "title", "实验报告", FieldOrigin.DOCUMENT, (FieldEvidence(chunk_id, "实验报告"),)
            ),
            CandidateField(
                "submission", "课程平台", FieldOrigin.USER, explanation="用户补充提交方式"
            ),
            CandidateField(
                "due_date",
                "2026-10-16",
                FieldOrigin.INFERRED,
                (FieldEvidence(chunk_id, "下周五"),),
                explanation="发布于 2026-10-05，按周一为周首换算",
            ),
            CandidateField(
                "estimated_minutes", 90, FieldOrigin.SUGGESTION, explanation="初步耗时建议，可修改"
            ),
            CandidateField(
                "due_time", None, FieldOrigin.DOCUMENT, missing_reason="原文没有具体时刻"
            ),
        ),
        review_reasons=(ReviewReason("missing_time", "需要核对提交时刻", "due_time"),),
    )


def test_four_origins_missing_fields_and_review_reasons_are_distinct():
    draft = draft_for()
    validate_candidate(draft)
    assert {field.origin for field in draft.fields} == set(FieldOrigin)
    assert draft.fields[-1].value is None
    assert not hasattr(draft, "progress") and not hasattr(draft, "review_status")


@pytest.mark.parametrize(
    "field",
    [
        CandidateField("title", "无依据", FieldOrigin.DOCUMENT),
        CandidateField(
            "due_date", "2026-10-16", FieldOrigin.INFERRED, (FieldEvidence(1, "下周五"),)
        ),
        CandidateField("estimated_minutes", 90, FieldOrigin.SUGGESTION),
        CandidateField("due_time", None, FieldOrigin.DOCUMENT),
        CandidateField("title", "value", "not_started"),
        CandidateField("x", float("nan"), FieldOrigin.USER),
        CandidateField("x", {1: "invalid key"}, FieldOrigin.USER),
        CandidateField("x", "\x00", FieldOrigin.USER),
        CandidateField("x", "value", FieldOrigin.DOCUMENT, (FieldEvidence(True, "text"),)),
        CandidateField("x", "value", FieldOrigin.DOCUMENT, (FieldEvidence(1, ""),)),
    ],
    ids=[
        "no-evidence",
        "no-inference-basis",
        "no-suggestion-label",
        "no-missing-reason",
        "bad-origin",
        "nan",
        "bad-json-key",
        "nul",
        "bool-id",
        "empty-quote",
    ],
)
def test_invalid_fields_are_rejected(field):
    with pytest.raises(DomainError):
        validate_candidate(CandidateDraft(CandidateKind.TASK, (field,)))


def test_duplicate_fields_and_nonexistent_reason_fields_are_rejected():
    draft = draft_for()
    with pytest.raises(DomainError, match="重复"):
        validate_candidate(replace(draft, fields=draft.fields + draft.fields[:1]))
    with pytest.raises(DomainError, match="不存在的字段"):
        validate_candidate(
            replace(draft, review_reasons=(ReviewReason("missing", "缺失", "unknown"),))
        )


@pytest.fixture
def source(client, tmp_path, monkeypatch):
    monkeypatch.setattr(client.app.state, "file_storage", LocalFileStorage(tmp_path))
    response = client.post(
        "/api/v1/materials/text",
        json={"title": "课程通知", "content": "下周五提交实验报告。\n\n保留原文定位。"},
    )
    assert response.status_code == 201
    result = response.json()
    result["material"]["workspace_id"] = client.get("/api/v1/settings").json()["workspace_id"]
    return client, result["material"], result["chunks"]


def save(source, draft=None):
    client, material, chunks = source
    with client.app.state.session_factory() as session:
        return save_candidates(
            build_repositories(session),
            material["workspace_id"],
            material["id"],
            1,
            [draft or draft_for(chunks[0]["id"])],
        )[0]


def test_candidate_roundtrip_preserves_evidence_and_independent_status(source):
    client, material, chunks = source
    saved = save(source)
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        loaded = get_candidate(repos, material["workspace_id"], saved.id)
        assert loaded.draft == draft_for(chunks[0]["id"])
        assert loaded.review_status == ReviewStatus.PENDING
        assert loaded.source_version_id == chunks[0]["version_id"]
        assert session.scalar(select(func.count()).select_from(ExtractedItem)) == 1
        assert session.scalar(select(func.count()).select_from(ExtractedField)) == 5
        assert session.scalar(select(func.count()).select_from(CandidateFieldEvidence)) == 2
        assert repos.candidates.list_by_version(
            material["workspace_id"], loaded.source_version_id
        ) == [loaded]
    assert client.get("/api/v1/tasks").json() == []


def test_reading_another_workspace_candidate_is_not_found(source):
    client, material, _ = source
    saved = save(source)
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        with pytest.raises(NotFoundError):
            get_candidate(repos, material["workspace_id"] + 100, saved.id)
        assert (
            repos.candidates.list_by_version(
                material["workspace_id"] + 100, saved.source_version_id
            )
            == []
        )


@pytest.mark.parametrize(
    "case", ["missing-chunk", "different-version", "invented-quote", "wrong-workspace"]
)
def test_invalid_source_never_writes_candidates(source, case):
    client, material, chunks = source
    workspace_id = material["workspace_id"]
    chunk_id = chunks[0]["id"]
    draft = draft_for(chunk_id)
    if case == "missing-chunk":
        draft = draft_for(999999)
    elif case == "different-version":
        other = client.post(
            "/api/v1/materials/text", json={"title": "other", "content": "下周五提交实验报告。"}
        ).json()
        draft = draft_for(other["chunks"][0]["id"])
    elif case == "invented-quote":
        draft = replace(
            draft,
            fields=(replace(draft.fields[0], evidence=(FieldEvidence(chunk_id, "不存在的原文"),)),),
        )
    else:
        workspace_id += 100
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        with pytest.raises((DomainError, NotFoundError)):
            save_candidates(repos, workspace_id, material["id"], 1, [draft])
        assert session.scalar(select(func.count()).select_from(ExtractedItem)) == 0


def test_invalid_second_candidate_and_database_failure_leave_no_partial_records(
    source, monkeypatch
):
    client, material, chunks = source
    draft = draft_for(chunks[0]["id"])
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        with pytest.raises(DomainError):
            save_candidates(
                repos, material["workspace_id"], material["id"], 1, [draft, draft_for(999999)]
            )
        assert session.scalar(select(func.count()).select_from(ExtractedItem)) == 0
        create = repos.candidates.create
        calls = 0

        def failing_create(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("simulated database failure")
            return create(*args)

        monkeypatch.setattr(repos.candidates, "create", failing_create)
        with pytest.raises(RuntimeError):
            save_candidates(repos, material["workspace_id"], material["id"], 1, [draft, draft])
        assert session.scalar(select(func.count()).select_from(ExtractedItem)) == 0
        assert session.scalar(select(func.count()).select_from(ExtractedField)) == 0


def test_scope_roundtrip_and_foreign_or_mismatched_scope_is_rejected(source):
    client, material, chunks = source
    with client.app.state.session_factory() as session:
        semester = Semester(
            workspace_id=material["workspace_id"],
            name="秋季",
            start_date=date(2026, 9, 1),
            end_date=date(2027, 1, 1),
        )
        session.add(semester)
        session.flush()
        course = Course(
            workspace_id=material["workspace_id"],
            semester_id=semester.id,
            name="数据库",
            class_name="A",
        )
        session.add(course)
        other = Workspace(name="他人空间")
        session.add(other)
        session.flush()
        foreign = Semester(
            workspace_id=other.id,
            name="其他",
            start_date=date(2026, 9, 1),
            end_date=date(2027, 1, 1),
        )
        session.add(foreign)
        session.commit()
        scope = CandidateScope(semester.id, course.id, "A")
        foreign_scope = CandidateScope(foreign.id)
        mismatch = CandidateScope(foreign.id, course.id)
    draft = replace(draft_for(chunks[0]["id"]), scope=scope)
    assert save(source, draft).draft.scope == scope
    for invalid in (foreign_scope, mismatch):
        with pytest.raises((DomainError, NotFoundError)):
            save(source, replace(draft, scope=invalid))


def test_task_progress_value_cannot_be_written_as_candidate_review_status(source):
    client, _, _ = source
    saved = save(source)
    with client.app.state.session_factory() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                text("UPDATE extracted_items SET review_status='done' WHERE id=:id"),
                {"id": saved.id},
            )
            session.commit()
        session.rollback()
        assert session.get(ExtractedItem, saved.id).review_status == "pending"


def test_deletion_previews_candidates_rejects_stale_confirmation_and_cleans_evidence(source):
    client, material, _ = source
    first = save(source)
    mid = material["id"]
    preview = client.get(f"/api/v1/materials/{mid}/delete-impact").json()
    assert preview["candidate_count"] == 1
    save(source)
    response = client.post(
        f"/api/v1/materials/{mid}/delete",
        json={"confirm_token": preview["confirm_token"], "keep_tasks": True},
    )
    assert response.status_code == 400
    preview = client.get(f"/api/v1/materials/{mid}/delete-impact").json()
    assert preview["candidate_count"] == 2
    response = client.post(
        f"/api/v1/materials/{mid}/delete",
        json={"confirm_token": preview["confirm_token"], "keep_tasks": True},
    )
    assert response.status_code == 204, response.text
    with client.app.state.session_factory() as session:
        assert session.get(ExtractedItem, first.id) is None
        assert session.scalar(select(func.count()).select_from(ExtractedField)) == 0
        assert session.scalar(select(func.count()).select_from(CandidateFieldEvidence)) == 0


def test_archived_source_cannot_produce_new_candidates(source):
    client, material, _ = source
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        repos.materials.set_archived(material["id"], True)
        repos.uow.commit()
    with pytest.raises(DomainError, match="归档"):
        save(source)
