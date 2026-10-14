"""R002：候选字段修正。保存修正前后值、操作者与来源类型，保留原始提取与证据。"""

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from campusflow.application.candidates import (
    get_candidate,
    revise_candidate_field,
    save_candidates,
)
from campusflow.domain.candidates import (
    CandidateDraft,
    CandidateField,
    CandidateKind,
    FieldEvidence,
    FieldOrigin,
    FieldRevision,
    apply_revisions,
    effective_fields,
    validate_field_revision,
)
from campusflow.domain.errors import DomainError, NotFoundError
from campusflow.domain.states import ReviewStatus
from campusflow.infrastructure.db.models import (
    CandidateFieldEvidence,
    CandidateFieldRevision,
    ExtractedField,
    ExtractedItem,
)
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.storage.local import LocalFileStorage

# 原文：下周五提交实验报告。\n\n保留原文定位。
QUOTES = ("实验报告", "下周五提交")


def draft_for(chunk_id=1):
    return CandidateDraft(
        CandidateKind.TASK,
        (
            CandidateField(
                "title", "实验报告", FieldOrigin.DOCUMENT, (FieldEvidence(chunk_id, QUOTES[0]),)
            ),
            CandidateField(
                "due_date",
                "下周五",
                FieldOrigin.DOCUMENT,
                (FieldEvidence(chunk_id, QUOTES[1]),),
            ),
            CandidateField(
                "submission", None, FieldOrigin.DOCUMENT, missing_reason="原文没有提交方式"
            ),
        ),
    )


def document_field(chunk_id=1):
    return draft_for(chunk_id).fields[1]


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


def revise(source, candidate_id, field_name, **kwargs):
    client, material, _ = source
    kwargs.setdefault("operator", "student")
    with client.app.state.session_factory() as session:
        return revise_candidate_field(
            build_repositories(session),
            material["workspace_id"],
            candidate_id,
            field_name,
            **kwargs,
        )


def test_user_typed_date_cannot_claim_document_origin():
    """用户填写的日期不能伪装成原文规定：声明 document 却没有原文证据时拒绝。"""
    field = document_field()
    masquerade = FieldRevision(
        "due_date", "下周五", "2026-10-20", FieldOrigin.DOCUMENT, FieldOrigin.DOCUMENT, "student"
    )
    with pytest.raises(DomainError):
        validate_field_revision(field, masquerade)


def test_user_revision_replaces_origin_and_drops_document_evidence():
    field = document_field()
    revision = FieldRevision(
        "due_date", "下周五", "2026-10-20", FieldOrigin.DOCUMENT, FieldOrigin.USER, "student"
    )
    validate_field_revision(field, revision)
    effective = apply_revisions(field, (revision,))
    assert effective.value == "2026-10-20"
    assert effective.origin is FieldOrigin.USER
    assert effective.evidence == ()
    # 原始字段保持不变
    assert field.value == "下周五" and field.evidence == (FieldEvidence(1, QUOTES[1]),)


@pytest.mark.parametrize(
    ("revision", "expected"),
    [
        (
            FieldRevision(
                "due_date", "不是当前值", "2026-10-20", FieldOrigin.DOCUMENT, FieldOrigin.USER, "s"
            ),
            "不一致",
        ),
        (
            FieldRevision(
                "due_date", "下周五", "2026-10-20", FieldOrigin.USER, FieldOrigin.USER, "s"
            ),
            "不一致",
        ),
        (
            FieldRevision("due_date", "下周五", None, FieldOrigin.DOCUMENT, FieldOrigin.USER, "s"),
            "缺失原因",
        ),
        (
            FieldRevision(
                "due_date", "下周五", "x", FieldOrigin.DOCUMENT, FieldOrigin.SUGGESTION, "s"
            ),
            "建议说明",
        ),
        (
            FieldRevision(
                "due_date", "下周五", "2026-10-20", FieldOrigin.DOCUMENT, FieldOrigin.USER, "  "
            ),
            "操作者",
        ),
    ],
    ids=["wrong-previous-value", "wrong-previous-origin", "empty-value", "suggestion", "operator"],
)
def test_invalid_revisions_are_rejected(revision, expected):
    with pytest.raises(DomainError, match=expected):
        validate_field_revision(document_field(), revision)


def test_effective_fields_merge_revisions_by_field():
    draft = draft_for()
    revision = FieldRevision(
        "due_date", "下周五", "2026-10-20", FieldOrigin.DOCUMENT, FieldOrigin.USER, "student"
    )
    effective = effective_fields(draft.fields, (revision,))
    by_name = {field.name: field for field in effective}
    assert by_name["due_date"].value == "2026-10-20"
    assert by_name["title"] == draft.fields[0]
    assert by_name["submission"] == draft.fields[2]


def test_revision_roundtrip_keeps_original_extraction_and_evidence(source):
    client, material, chunks = source
    saved = save(source)
    updated = revise(
        source,
        saved.id,
        "due_date",
        value="2026-10-20",
        origin=FieldOrigin.USER,
        note="用户按群通知填写",
    )
    assert updated.draft == draft_for(chunks[0]["id"])
    assert updated.review_status == ReviewStatus.PENDING
    assert len(updated.revisions) == 1
    revision = updated.revisions[0].revision
    assert revision == FieldRevision(
        "due_date",
        "下周五",
        "2026-10-20",
        FieldOrigin.DOCUMENT,
        FieldOrigin.USER,
        "student",
        "用户按群通知填写",
    )
    effective = {
        field.name: field
        for field in effective_fields(updated.draft.fields, updated.field_revisions("due_date"))
    }
    assert effective["due_date"].value == "2026-10-20"
    assert effective["due_date"].origin is FieldOrigin.USER
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        loaded = get_candidate(repos, material["workspace_id"], saved.id)
        assert loaded.draft == draft_for(chunks[0]["id"])
        assert loaded.revisions == updated.revisions
        # 原始字段与证据仍在库里
        assert session.scalar(select(func.count()).select_from(ExtractedField)) == 3
        assert session.scalar(select(func.count()).select_from(CandidateFieldEvidence)) == 2
        counts = select(ExtractedField.name, ExtractedField.value).where(
            ExtractedField.candidate_id == saved.id
        )
        assert dict(session.execute(counts).all())["due_date"] == "下周五"


def test_successive_revisions_append_history(source):
    saved = save(source)
    first = revise(source, saved.id, "due_date", value="2026-10-20", origin=FieldOrigin.USER)
    second = revise(source, saved.id, "due_date", value="2026-10-22", origin=FieldOrigin.USER)
    assert [record.revision.new_value for record in second.revisions] == [
        "2026-10-20",
        "2026-10-22",
    ]
    assert second.revisions[1].revision.previous_value == "2026-10-20"
    assert second.revisions[1].revision.previous_origin is FieldOrigin.USER
    assert len(first.revisions) == 1
    effective = apply_revisions(draft_for().fields[1], second.field_revisions("due_date"))
    assert effective.value == "2026-10-22"


def test_unknown_field_and_foreign_workspace_are_rejected(source):
    client, material, _ = source
    saved = save(source)
    with pytest.raises(DomainError, match="没有这个字段"):
        revise(source, saved.id, "missing_field", value="x", origin=FieldOrigin.USER)
    with client.app.state.session_factory() as session:
        with pytest.raises(NotFoundError):
            revise_candidate_field(
                build_repositories(session),
                material["workspace_id"] + 100,
                saved.id,
                "due_date",
                value="2026-10-20",
                origin=FieldOrigin.USER,
                operator="student",
            )


def test_masquerading_revision_writes_nothing(source):
    client, _, _ = source
    saved = save(source)
    with pytest.raises(DomainError):
        revise(source, saved.id, "due_date", value="2026-10-20", origin=FieldOrigin.DOCUMENT)
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(CandidateFieldRevision)) == 0


def test_failed_commit_rolls_back_revision(source, monkeypatch):
    client, material, _ = source
    saved = save(source)
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)

        def failing_add(*args):
            raise RuntimeError("simulated database failure")

        monkeypatch.setattr(repos.candidates, "add_field_revision", failing_add)
        with pytest.raises(RuntimeError):
            revise_candidate_field(
                repos,
                material["workspace_id"],
                saved.id,
                "due_date",
                value="2026-10-20",
                origin=FieldOrigin.USER,
                operator="student",
            )
        assert session.scalar(select(func.count()).select_from(CandidateFieldRevision)) == 0


def test_revision_origin_check_constraint(source):
    client, _, _ = source
    saved = save(source)
    with client.app.state.session_factory() as session:
        field_id = session.scalar(
            select(ExtractedField.id).where(ExtractedField.candidate_id == saved.id)
        )
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "INSERT INTO candidate_field_revisions "
                    "(field_id, previous_value, new_value, previous_origin, new_origin, operator) "
                    "VALUES (:field_id, '1', '2', 'user', 'done', 'student')"
                ),
                {"field_id": field_id},
            )
            session.commit()
        session.rollback()


def test_revisions_cascade_when_candidate_is_removed(source):
    client, _, _ = source
    saved = save(source)
    revise(source, saved.id, "due_date", value="2026-10-20", origin=FieldOrigin.USER)
    with client.app.state.session_factory() as session:
        session.delete(session.get(ExtractedItem, saved.id))
        session.commit()
        assert session.scalar(select(func.count()).select_from(CandidateFieldRevision)) == 0
        assert session.scalar(select(func.count()).select_from(ExtractedField)) == 0
