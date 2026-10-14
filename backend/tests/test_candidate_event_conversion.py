"""R005：已确认日程候选转换为固定日程；起止时间必须可确定且转换幂等。"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from campusflow.application.candidates import (
    get_candidate,
    revise_candidate_field,
    save_candidates,
    set_candidate_review_status,
)
from campusflow.application.conversion import convert_candidate_to_event, convert_candidate_to_task
from campusflow.domain.candidates import (
    CandidateDraft,
    CandidateField,
    CandidateKind,
    FieldEvidence,
    FieldOrigin,
)
from campusflow.domain.errors import DomainError
from campusflow.domain.event_conversion import build_event_conversion, parse_event_time
from campusflow.domain.states import EventRecurrence, ReviewStatus
from campusflow.infrastructure.db.models import ExtractedItem, FixedEvent
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.storage.local import LocalFileStorage

CONTENT = "学术讲座 2026-10-20 14:00 至 2026-10-20 16:00 在三教 301 举行。\n\n保留原文定位。"
QUOTE_TITLE = "学术讲座"
QUOTE_START = "2026-10-20 14:00"
QUOTE_END = "2026-10-20 16:00"
QUOTE_LOCATION = "三教 301"


def event_draft(
    chunk_id=1,
    *,
    title=QUOTE_TITLE,
    start=QUOTE_START,
    end=QUOTE_END,
    location=QUOTE_LOCATION,
):
    fields = [
        CandidateField(
            "title", title, FieldOrigin.DOCUMENT, (FieldEvidence(chunk_id, QUOTE_TITLE),)
        ),
        CandidateField(
            "activity_text",
            "学术讲座 2026-10-20 14:00 至 2026-10-20 16:00",
            FieldOrigin.DOCUMENT,
            (FieldEvidence(chunk_id, QUOTE_TITLE),),
        ),
        CandidateField(
            "start_text",
            start,
            FieldOrigin.DOCUMENT,
            (FieldEvidence(chunk_id, QUOTE_START),) if start is not None else (),
            missing_reason=None if start is not None else "原文没有开始时间",
        ),
        CandidateField(
            "end_text",
            end,
            FieldOrigin.DOCUMENT,
            (FieldEvidence(chunk_id, QUOTE_END),) if end is not None else (),
            missing_reason=None if end is not None else "原文没有结束时间",
        ),
        CandidateField(
            "location",
            location,
            FieldOrigin.DOCUMENT,
            (FieldEvidence(chunk_id, QUOTE_LOCATION),) if location is not None else (),
            missing_reason=None if location is not None else "原文没有地点",
        ),
    ]
    return CandidateDraft(CandidateKind.EVENT, tuple(fields))


def task_draft(chunk_id=1):
    return CandidateDraft(
        CandidateKind.TASK,
        (
            CandidateField(
                "title", "实验报告", FieldOrigin.DOCUMENT, (FieldEvidence(chunk_id, QUOTE_TITLE),)
            ),
        ),
    )


@pytest.fixture
def source(client, tmp_path, monkeypatch):
    monkeypatch.setattr(client.app.state, "file_storage", LocalFileStorage(tmp_path))
    response = client.post("/api/v1/materials/text", json={"title": "课程通知", "content": CONTENT})
    assert response.status_code == 201
    result = response.json()
    settings = client.get("/api/v1/settings").json()
    result["material"]["workspace_id"] = settings["workspace_id"]
    return client, result["material"], result["chunks"], settings["timezone"]


def save(source, draft=None, *, confirm=True):
    client, material, chunks, _ = source
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        saved = save_candidates(
            repos,
            material["workspace_id"],
            material["id"],
            1,
            [draft or event_draft(chunks[0]["id"])],
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
    client, material, _, _ = source
    with client.app.state.session_factory() as session:
        return convert_candidate_to_event(
            build_repositories(session), material["workspace_id"], candidate_id
        )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-10-20T14:00", datetime(2026, 10, 20, 14, 0)),
        ("2026-10-20 14:00", datetime(2026, 10, 20, 14, 0)),
        ("2026-10-20T14:00:30", datetime(2026, 10, 20, 14, 0, 30)),
        ("  2026-10-20 14:00  ", datetime(2026, 10, 20, 14, 0)),
        ("2026-10-20", None),
        ("14:00", None),
        ("下周五 14:00", None),
        ("2026-10-20 下午两点", None),
        ("", None),
        (None, None),
        (30, None),
    ],
)
def test_parse_event_time_requires_date_and_clock(value, expected):
    assert parse_event_time(value) == expected


def test_parse_event_time_keeps_explicit_offset():
    parsed = parse_event_time("2026-10-20T14:00+08:00")
    assert parsed is not None and parsed.utcoffset() is not None


def test_build_event_conversion_maps_and_rejects_incomplete_times():
    conversion = build_event_conversion(CandidateKind.EVENT, event_draft().fields)
    assert conversion.title == QUOTE_TITLE
    assert conversion.starts_at == datetime(2026, 10, 20, 14, 0)
    assert conversion.ends_at == datetime(2026, 10, 20, 16, 0)
    assert conversion.location == QUOTE_LOCATION
    with pytest.raises(DomainError, match="只有日程候选"):
        build_event_conversion(CandidateKind.TASK, task_draft().fields)
    with pytest.raises(DomainError, match="缺少可确定的开始或结束时间"):
        build_event_conversion(CandidateKind.EVENT, event_draft(start="2026-10-20").fields)
    with pytest.raises(DomainError, match="缺少可确定的开始或结束时间"):
        build_event_conversion(CandidateKind.EVENT, event_draft(end=None).fields)
    with pytest.raises(DomainError, match="超过 300"):
        build_event_conversion(CandidateKind.EVENT, event_draft(title="讲" * 301).fields)
    with pytest.raises(DomainError, match="超过 200"):
        build_event_conversion(CandidateKind.EVENT, event_draft(location="地" * 201).fields)


def test_convert_creates_linked_event_in_workspace_timezone(source):
    client, material, _, _ = source
    candidate = save(source)
    event = convert(source, candidate.id)
    timezone = ZoneInfo(source[3])
    assert event.title == QUOTE_TITLE
    assert event.starts_at == datetime(2026, 10, 20, 14, 0, tzinfo=timezone)
    assert event.ends_at == datetime(2026, 10, 20, 16, 0, tzinfo=timezone)
    assert event.location == QUOTE_LOCATION
    assert event.recurrence is EventRecurrence.NONE
    assert event.repeat_until is None
    assert event.source_candidate_id == candidate.id
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        reloaded = get_candidate(repos, material["workspace_id"], candidate.id)
        assert reloaded.review_status is ReviewStatus.CONFIRMED
        assert session.scalar(select(func.count()).select_from(FixedEvent)) == 1


def test_explicit_offset_input_is_normalized_not_reinterpreted(source):
    saved = save(source, event_draft(start="2026-10-20T14:00+08:00"))
    event = convert(source, saved.id)
    assert event.starts_at == datetime(2026, 10, 20, 14, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert event.starts_at.astimezone(ZoneInfo("UTC")).hour == 6


def test_repeated_conversion_returns_existing_event(source):
    client, _, _, _ = source
    candidate = save(source)
    first = convert(source, candidate.id)
    second = convert(source, candidate.id)
    assert first.id == second.id
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(FixedEvent)) == 1


def test_incomplete_time_or_illegal_range_blocks_conversion(source):
    client, _, chunks, _ = source
    relative = save(source, event_draft(start="下周五 14:00", end="下周五 16:00"))
    date_only = save(source, event_draft(start="2026-10-20", end="2026-10-20"))
    reversed_range = save(source, event_draft(start=QUOTE_END, end=QUOTE_START))
    for candidate in (relative, date_only, reversed_range):
        with pytest.raises(DomainError):
            convert(source, candidate.id)
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(FixedEvent)) == 0


def test_only_confirmed_event_candidates_convert(source):
    client, material, _, _ = source
    pending = save(source, confirm=False)
    with pytest.raises(DomainError, match="只有已确认"):
        convert(source, pending.id)
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        set_candidate_review_status(
            repos, material["workspace_id"], pending.id, ReviewStatus.IGNORED, operator="student"
        )
    with pytest.raises(DomainError, match="只有已确认"):
        convert(source, pending.id)
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(FixedEvent)) == 0


def test_candidate_kind_direction_is_enforced(source):
    client, material, _, _ = source
    task = save(source, task_draft())
    event = save(source, event_draft())
    with pytest.raises(DomainError, match="只有日程候选"):
        convert(source, task.id)
    with client.app.state.session_factory() as session:
        with pytest.raises(DomainError, match="只有任务候选"):
            convert_candidate_to_task(
                build_repositories(session), material["workspace_id"], event.id
            )


def test_user_corrected_times_fill_missing_event_times(source):
    client, material, _, _ = source
    saved = save(source, event_draft(start=None, end=None))
    with pytest.raises(DomainError, match="缺少可确定的开始或结束时间"):
        convert(source, saved.id)
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        revise_candidate_field(
            repos,
            material["workspace_id"],
            saved.id,
            "start_text",
            value="2026-10-20T09:00",
            origin=FieldOrigin.USER,
            operator="student",
        )
        revise_candidate_field(
            repos,
            material["workspace_id"],
            saved.id,
            "end_text",
            value="2026-10-20T11:00",
            origin=FieldOrigin.USER,
            operator="student",
        )
    event = convert(source, saved.id)
    timezone = ZoneInfo(source[3])
    assert event.starts_at == datetime(2026, 10, 20, 9, 0, tzinfo=timezone)
    assert event.ends_at == datetime(2026, 10, 20, 11, 0, tzinfo=timezone)


def test_one_event_per_candidate_is_enforced_by_database(source):
    client, material, _, _ = source
    candidate = save(source)
    convert(source, candidate.id)
    with client.app.state.session_factory() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "INSERT INTO fixed_events "
                    "(workspace_id, title, starts_at, ends_at, recurrence, source_candidate_id) "
                    "VALUES (:workspace_id, '重复日程', now(), now() + interval '1 hour', "
                    "'none', :cid)"
                ),
                {"workspace_id": material["workspace_id"], "cid": candidate.id},
            )
            session.commit()
        session.rollback()


def test_deleting_candidate_keeps_event_and_clears_link(source):
    client, _, _, _ = source
    candidate = save(source)
    event = convert(source, candidate.id)
    with client.app.state.session_factory() as session:
        session.delete(session.get(ExtractedItem, candidate.id))
        session.commit()
        row = session.get(FixedEvent, event.id)
        assert row is not None and row.source_candidate_id is None
