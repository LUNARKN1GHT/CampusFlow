"""R004：已确认任务候选转换为正式任务；幂等且保留日期精度。"""

from datetime import date, time

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from campusflow.application.candidates import (
    get_candidate,
    revise_candidate_field,
    save_candidates,
    set_candidate_review_status,
)
from campusflow.application.conversion import convert_candidate_to_task
from campusflow.domain.candidate_conversion import (
    build_task_conversion,
    due_source_for,
    parse_due_text,
)
from campusflow.domain.candidates import (
    CandidateDraft,
    CandidateField,
    CandidateKind,
    FieldEvidence,
    FieldOrigin,
)
from campusflow.domain.errors import DomainError
from campusflow.domain.states import (
    DueSourceType,
    ReviewStatus,
    TaskProgress,
)
from campusflow.infrastructure.db.models import ExtractedItem, Task
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.storage.local import LocalFileStorage

CONTENT = "下周五（2026-10-16）提交实验报告。\n\n保留原文定位。"
QUOTE_TITLE = "实验报告"
QUOTE_RELATIVE = "下周五"
QUOTE_DATE = "2026-10-16"


def task_draft(chunk_id=1, *, title="实验报告", due=QUOTE_DATE, estimated=90):
    fields = [
        CandidateField(
            "title", title, FieldOrigin.DOCUMENT, (FieldEvidence(chunk_id, QUOTE_TITLE),)
        ),
        CandidateField(
            "submission_method",
            "课程平台",
            FieldOrigin.DOCUMENT,
            (FieldEvidence(chunk_id, QUOTE_RELATIVE),),
        ),
        CandidateField(
            "deliverable",
            "实验报告 PDF",
            FieldOrigin.DOCUMENT,
            (FieldEvidence(chunk_id, QUOTE_TITLE),),
        ),
        CandidateField(
            "due_text", due, FieldOrigin.DOCUMENT, (FieldEvidence(chunk_id, QUOTE_RELATIVE),)
        ),
    ]
    if estimated is not None:
        fields.append(
            CandidateField(
                "estimated_minutes", estimated, FieldOrigin.SUGGESTION, explanation="初步耗时建议"
            )
        )
    return CandidateDraft(CandidateKind.TASK, tuple(fields))


def event_draft(chunk_id=1):
    return CandidateDraft(
        CandidateKind.EVENT,
        (
            CandidateField(
                "title", "讲座", FieldOrigin.DOCUMENT, (FieldEvidence(chunk_id, QUOTE_TITLE),)
            ),
            CandidateField(
                "activity_text",
                "下周五提交实验报告",
                FieldOrigin.DOCUMENT,
                (FieldEvidence(chunk_id, QUOTE_RELATIVE),),
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


def save(source, draft=None, *, confirm=True):
    client, material, chunks = source
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        saved = save_candidates(
            repos,
            material["workspace_id"],
            material["id"],
            1,
            [draft or task_draft(chunks[0]["id"])],
        )[0]
        if confirm:
            set_candidate_review_status(
                repos,
                material["workspace_id"],
                saved.id,
                ReviewStatus.CONFIRMED,
                operator="student",
            )
        return get_candidate(repos, material["workspace_id"], saved.id)


def convert(source, candidate_id):
    client, material, _ = source
    with client.app.state.session_factory() as session:
        return convert_candidate_to_task(
            build_repositories(session), material["workspace_id"], candidate_id
        )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-10-16", (date(2026, 10, 16), None)),
        ("2026-10-16T14:30", (date(2026, 10, 16), time(14, 30))),
        ("2026-10-16 14:30:15", (date(2026, 10, 16), time(14, 30, 15))),
        ("  2026-10-16  ", (date(2026, 10, 16), None)),
        ("下周五", (None, None)),
        ("10-16", (None, None)),
        ("2026-13-40", (None, None)),
        ("2026-10-16T14:30+08:00", (None, None)),
        ("", (None, None)),
        (None, (None, None)),
        (30, (None, None)),
    ],
)
def test_parse_due_text_keeps_precision_and_rejects_unknown(value, expected):
    assert parse_due_text(value) == expected


def test_due_source_mapping_covers_all_origins():
    assert due_source_for(FieldOrigin.DOCUMENT) is DueSourceType.DOCUMENT
    assert due_source_for(FieldOrigin.INFERRED) is DueSourceType.INFERRED
    assert due_source_for(FieldOrigin.USER) is DueSourceType.USER
    assert due_source_for(FieldOrigin.SUGGESTION) is DueSourceType.USER


def test_build_task_conversion_maps_fields_and_keeps_relative_text():
    fields = task_draft(due=QUOTE_RELATIVE).fields
    conversion = build_task_conversion(CandidateKind.TASK, fields)
    assert conversion.title == "实验报告"
    assert conversion.description == "提交方式：课程平台\n产出物：实验报告 PDF"
    assert conversion.due_date is None and conversion.due_time is None
    assert conversion.due_note == QUOTE_RELATIVE
    assert conversion.due_source is DueSourceType.DOCUMENT
    assert conversion.estimated_minutes == 90


def test_build_task_conversion_rejects_non_task_and_missing_title():
    with pytest.raises(DomainError, match="只有任务候选"):
        build_task_conversion(CandidateKind.EVENT, event_draft().fields)
    blank_title = task_draft(title="   ").fields
    with pytest.raises(DomainError, match="缺少标题"):
        build_task_conversion(CandidateKind.TASK, blank_title)
    no_estimate = build_task_conversion(CandidateKind.TASK, task_draft(estimated=None).fields)
    assert no_estimate.estimated_minutes is None


def test_convert_creates_linked_task_and_keeps_candidate_confirmed(source):
    client, material, _ = source
    candidate = save(source)
    task = convert(source, candidate.id)
    assert task.title == "实验报告"
    assert task.description == "提交方式：课程平台\n产出物：实验报告 PDF"
    assert task.due_date == date(2026, 10, 16)
    assert task.due_time is None
    assert task.due_source is DueSourceType.DOCUMENT
    assert task.estimated_minutes == 90 and task.remaining_minutes == 90
    assert task.source_candidate_id == candidate.id
    assert task.source_material_id == material["id"]
    assert task.course_id is None
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        reloaded = get_candidate(repos, material["workspace_id"], candidate.id)
        assert reloaded.review_status is ReviewStatus.CONFIRMED
        assert session.scalar(select(func.count()).select_from(Task)) == 1


def test_repeated_conversion_returns_existing_task(source):
    client, _, _ = source
    candidate = save(source)
    first = convert(source, candidate.id)
    second = convert(source, candidate.id)
    assert first.id == second.id
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Task)) == 1


def test_only_confirmed_task_candidates_convert(source):
    client, _, _ = source
    pending = save(source, confirm=False)
    with pytest.raises(DomainError, match="只有已确认"):
        convert(source, pending.id)
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        set_candidate_review_status(
            repos,
            pending.workspace_id,
            pending.id,
            ReviewStatus.IGNORED,
            operator="student",
        )
    with pytest.raises(DomainError, match="只有已确认"):
        convert(source, pending.id)
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Task)) == 0


def test_event_candidate_cannot_become_task(source):
    saved = save(source, event_draft())
    with pytest.raises(DomainError, match="只有任务候选"):
        convert(source, saved.id)


def test_relative_due_text_keeps_note_without_fabricating_date(source):
    saved = save(source, task_draft(due=QUOTE_RELATIVE))
    task = convert(source, saved.id)
    assert task.due_date is None and task.due_time is None
    assert task.due_note == QUOTE_RELATIVE
    assert task.due_source is DueSourceType.DOCUMENT


def test_user_corrected_due_marks_user_source_and_keeps_precision(source):
    client, material, chunks = source
    saved = save(source, task_draft(due=QUOTE_RELATIVE))
    with client.app.state.session_factory() as session:
        revise_candidate_field(
            build_repositories(session),
            material["workspace_id"],
            saved.id,
            "due_text",
            value="2026-10-20",
            origin=FieldOrigin.USER,
            operator="student",
        )
    task = convert(source, saved.id)
    assert task.due_date == date(2026, 10, 20) and task.due_time is None
    assert task.due_source is DueSourceType.USER
    assert task.due_note is None
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        reloaded = get_candidate(repos, material["workspace_id"], saved.id)
        due = next(f for f in reloaded.draft.fields if f.name == "due_text")
        assert due.value == QUOTE_RELATIVE


def test_task_progress_is_independent_from_review_status(source):
    client, material, _ = source
    candidate = save(source)
    task = convert(source, candidate.id)
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        repos.tasks.set_progress(task.id, TaskProgress.DONE, None)
        repos.uow.commit()
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        reloaded = get_candidate(repos, material["workspace_id"], candidate.id)
        assert reloaded.review_status is ReviewStatus.CONFIRMED
        assert repos.tasks.get(task.id).progress is TaskProgress.DONE


def test_one_task_per_candidate_is_enforced_by_database(source):
    client, material, _ = source
    candidate = save(source)
    convert(source, candidate.id)
    with client.app.state.session_factory() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "INSERT INTO tasks "
                    "(workspace_id, title, due_source, progress, priority, source_candidate_id) "
                    "VALUES (:workspace_id, '重复任务', 'user', 'not_started', 'medium', :cid)"
                ),
                {"workspace_id": material["workspace_id"], "cid": candidate.id},
            )
            session.commit()
        session.rollback()


def test_deleting_candidate_keeps_task_and_clears_link(source):
    client, material, _ = source
    candidate = save(source)
    task = convert(source, candidate.id)
    with client.app.state.session_factory() as session:
        session.delete(session.get(ExtractedItem, candidate.id))
        session.commit()
        row = session.get(Task, task.id)
        assert row is not None and row.source_candidate_id is None
