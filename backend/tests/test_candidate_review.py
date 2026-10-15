"""R003：候选确认、忽略与失效的状态转换，以及确认前的必须字段检查。"""

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from campusflow.application.candidates import (
    get_candidate,
    revise_candidate_field,
    save_candidates,
    set_candidate_review_status,
)
from campusflow.application.event_extraction import EVENT_SPEC
from campusflow.application.task_extraction import TASK_SPEC
from campusflow.domain.candidates import (
    REQUIRED_FIELDS,
    CandidateDraft,
    CandidateField,
    CandidateKind,
    FieldEvidence,
    FieldOrigin,
    ReviewChange,
    effective_fields,
    missing_required_fields,
    require_confirmed_for_conversion,
    validate_review_change,
)
from campusflow.domain.errors import DomainError, NotFoundError
from campusflow.domain.states import ReviewStatus
from campusflow.infrastructure.db.models import (
    CandidateReviewChange,
    ExtractedItem,
)
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.storage.local import LocalFileStorage


def complete_task_draft(chunk_id=1):
    return CandidateDraft(
        CandidateKind.TASK,
        (
            CandidateField(
                "title", "实验报告", FieldOrigin.DOCUMENT, (FieldEvidence(chunk_id, "实验报告"),)
            ),
            CandidateField(
                "due_date",
                "下周五",
                FieldOrigin.DOCUMENT,
                (FieldEvidence(chunk_id, "下周五提交"),),
            ),
        ),
    )


def titleless_task_draft(chunk_id=1):
    return CandidateDraft(
        CandidateKind.TASK,
        (
            CandidateField("title", None, FieldOrigin.DOCUMENT, missing_reason="原文没有任务名称"),
            CandidateField(
                "due_date",
                "下周五",
                FieldOrigin.DOCUMENT,
                (FieldEvidence(chunk_id, "下周五提交"),),
            ),
        ),
    )


def event_draft(chunk_id=1, with_activity=True):
    evidence = (FieldEvidence(chunk_id, "下周五提交"),) if with_activity else ()
    return CandidateDraft(
        CandidateKind.EVENT,
        (
            CandidateField(
                "title", "讲座", FieldOrigin.DOCUMENT, (FieldEvidence(chunk_id, "实验报告"),)
            ),
            CandidateField(
                "activity_text",
                "下周五提交实验报告" if with_activity else None,
                FieldOrigin.DOCUMENT,
                evidence,
                missing_reason=None if with_activity else "原文没有活动描述",
            ),
        ),
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
            [draft or complete_task_draft(chunks[0]["id"])],
        )[0]


def change_status(source, candidate_id, status, **kwargs):
    client, material, _ = source
    kwargs.setdefault("operator", "student")
    with client.app.state.session_factory() as session:
        return set_candidate_review_status(
            build_repositories(session),
            material["workspace_id"],
            candidate_id,
            status,
            **kwargs,
        )


def test_required_fields_stay_in_sync_with_extraction_specs():
    for spec in (TASK_SPEC, EVENT_SPEC):
        assert REQUIRED_FIELDS[spec.kind] == tuple(
            field.name for field in spec.fields if not field.allow_missing
        )
    assert REQUIRED_FIELDS[CandidateKind.RULE] == ()


def test_missing_required_fields_only_counts_type_required_names():
    fields = complete_task_draft().fields
    assert missing_required_fields(CandidateKind.TASK, fields) == ()
    assert missing_required_fields(CandidateKind.TASK, titleless_task_draft().fields) == ("title",)
    assert missing_required_fields(
        CandidateKind.EVENT, event_draft(with_activity=False).fields
    ) == ("activity_text",)


LEGAL_TRANSITIONS = [
    (ReviewStatus.PENDING, ReviewStatus.CONFIRMED),
    (ReviewStatus.PENDING, ReviewStatus.IGNORED),
    (ReviewStatus.PENDING, ReviewStatus.SUPERSEDED),
    (ReviewStatus.CONFIRMED, ReviewStatus.SUPERSEDED),
    (ReviewStatus.CONFIRMED, ReviewStatus.PENDING),
    (ReviewStatus.IGNORED, ReviewStatus.PENDING),
    (ReviewStatus.SUPERSEDED, ReviewStatus.PENDING),
]

ILLEGAL_TRANSITIONS = [
    (ReviewStatus.CONFIRMED, ReviewStatus.IGNORED),
    (ReviewStatus.IGNORED, ReviewStatus.CONFIRMED),
    (ReviewStatus.IGNORED, ReviewStatus.SUPERSEDED),
    (ReviewStatus.SUPERSEDED, ReviewStatus.CONFIRMED),
    (ReviewStatus.SUPERSEDED, ReviewStatus.IGNORED),
]


@pytest.mark.parametrize(("current", "target"), LEGAL_TRANSITIONS)
def test_legal_transitions_are_accepted(current, target):
    validate_review_change(ReviewChange(current, target, "student", "理由"))


@pytest.mark.parametrize(("current", "target"), ILLEGAL_TRANSITIONS)
def test_illegal_transitions_are_rejected(current, target):
    with pytest.raises(DomainError, match="不允许"):
        validate_review_change(ReviewChange(current, target, "student"))


def test_same_status_and_blank_operator_are_rejected():
    with pytest.raises(DomainError, match="已经处于"):
        validate_review_change(ReviewChange(ReviewStatus.PENDING, ReviewStatus.PENDING, "student"))
    with pytest.raises(DomainError, match="操作者"):
        validate_review_change(ReviewChange(ReviewStatus.PENDING, ReviewStatus.CONFIRMED, "  "))


def test_only_confirmed_candidates_are_convertible():
    require_confirmed_for_conversion(ReviewStatus.CONFIRMED)
    for status in (ReviewStatus.PENDING, ReviewStatus.IGNORED, ReviewStatus.SUPERSEDED):
        with pytest.raises(DomainError, match="只有已确认"):
            require_confirmed_for_conversion(status)


def test_confirm_records_transition_and_operator(source):
    client, material, _ = source
    saved = save(source)
    updated = change_status(source, saved.id, ReviewStatus.CONFIRMED, reason="核对原文后确认")
    assert updated.review_status is ReviewStatus.CONFIRMED
    assert len(updated.review_changes) == 1
    record = updated.review_changes[0]
    assert record.change == ReviewChange(
        ReviewStatus.PENDING, ReviewStatus.CONFIRMED, "student", "核对原文后确认"
    )
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        reloaded = get_candidate(repos, material["workspace_id"], saved.id)
        assert reloaded.review_status is ReviewStatus.CONFIRMED
        assert reloaded.review_changes == updated.review_changes
        assert session.scalar(select(func.count()).select_from(CandidateReviewChange)) == 1


def test_confirm_without_required_field_is_blocked(source):
    saved = save(source, titleless_task_draft())
    with pytest.raises(DomainError, match="缺少必须字段：title"):
        change_status(source, saved.id, ReviewStatus.CONFIRMED)
    with pytest.raises(DomainError, match="activity_text"):
        change_status(
            source, save(source, event_draft(with_activity=False)).id, ReviewStatus.CONFIRMED
        )


def test_acknowledging_missing_field_requires_reason_then_records_it(source):
    client, _, _ = source
    saved = save(source, titleless_task_draft())
    with pytest.raises(DomainError, match="必须填写原因"):
        change_status(source, saved.id, ReviewStatus.CONFIRMED, acknowledge_missing=True)
    updated = change_status(
        source,
        saved.id,
        ReviewStatus.CONFIRMED,
        acknowledge_missing=True,
        reason="原文没有名称，按截图人工确认",
    )
    assert updated.review_status is ReviewStatus.CONFIRMED
    assert updated.review_changes[0].change.reason == "原文没有名称，按截图人工确认"
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(CandidateReviewChange)) == 1


def test_required_field_filled_by_user_correction_allows_confirmation(source):
    client, material, _ = source
    saved = save(source, titleless_task_draft())
    with client.app.state.session_factory() as session:
        revise_candidate_field(
            build_repositories(session),
            material["workspace_id"],
            saved.id,
            "title",
            value="实验报告",
            origin=FieldOrigin.USER,
            operator="student",
        )
    updated = change_status(source, saved.id, ReviewStatus.CONFIRMED)
    assert updated.review_status is ReviewStatus.CONFIRMED
    assert (
        missing_required_fields(
            updated.draft.kind,
            effective_fields(updated.draft.fields, [r.revision for r in updated.revisions]),
        )
        == ()
    )


def test_ignore_reopen_and_illegal_reexamination_flow(source):
    client, _, _ = source
    saved = save(source)
    ignored = change_status(source, saved.id, ReviewStatus.IGNORED, reason="与本课程无关")
    assert ignored.review_status is ReviewStatus.IGNORED
    with pytest.raises(DomainError, match="不允许"):
        change_status(source, saved.id, ReviewStatus.CONFIRMED)
    reopened = change_status(source, saved.id, ReviewStatus.PENDING, reason="重新核对")
    assert reopened.review_status is ReviewStatus.PENDING
    assert [record.change.to_status for record in reopened.review_changes] == [
        ReviewStatus.IGNORED,
        ReviewStatus.PENDING,
    ]
    assert len(reopened.review_changes) == 2


def test_failed_transitions_leave_status_and_history_untouched(source):
    client, _, _ = source
    saved = save(source)
    change_status(source, saved.id, ReviewStatus.CONFIRMED)
    for status in (ReviewStatus.CONFIRMED, ReviewStatus.IGNORED):
        with pytest.raises(DomainError):
            change_status(source, saved.id, ReviewStatus(status))
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        reloaded = get_candidate(repos, saved.workspace_id, saved.id)
        assert reloaded.review_status is ReviewStatus.CONFIRMED
        assert len(reloaded.review_changes) == 1


def test_cross_workspace_candidate_cannot_change_status(source):
    client, material, _ = source
    saved = save(source)
    with client.app.state.session_factory() as session:
        with pytest.raises(NotFoundError):
            set_candidate_review_status(
                build_repositories(session),
                material["workspace_id"] + 100,
                saved.id,
                ReviewStatus.IGNORED,
                operator="student",
            )


def test_review_change_status_constraint_and_cascade(source):
    client, _, _ = source
    saved = save(source)
    change_status(source, saved.id, ReviewStatus.IGNORED)
    with client.app.state.session_factory() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "INSERT INTO candidate_review_changes "
                    "(candidate_id, from_status, to_status, operator) "
                    "VALUES (:candidate_id, 'pending', 'done', 'student')"
                ),
                {"candidate_id": saved.id},
            )
            session.commit()
        session.rollback()
        session.delete(session.get(ExtractedItem, saved.id))
        session.commit()
        assert session.scalar(select(func.count()).select_from(CandidateReviewChange)) == 0
