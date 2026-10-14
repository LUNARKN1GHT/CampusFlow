"""X006：以明确依据计算，保留日期精度、范围、来源及待核对状态。"""

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select

from campusflow.application.candidates import save_candidates
from campusflow.application.relative_dates import resolve_task_deadline
from campusflow.domain.candidates import (
    CandidateDraft,
    CandidateField,
    CandidateKind,
    CandidateScope,
    FieldEvidence,
    FieldOrigin,
)
from campusflow.domain.errors import DomainError, NotFoundError
from campusflow.domain.relative_dates import parse_date_anchor, resolve_relative_date
from campusflow.domain.states import ReviewStatus
from campusflow.infrastructure.db.models import ExtractedItem, Task
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.storage.local import LocalFileStorage

ZONE = "Asia/Shanghai"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("今天", "2026-09-28"),
        ("明天", "2026-09-29"),
        ("后天", "2026-09-30"),
        ("本周五", "2026-10-02"),
        ("这周日", "2026-10-04"),
        ("下周三 18:00 截止", "2026-10-07"),
        ("下下周一提交", "2026-10-12"),
        ("请于下周五前提交。", "2026-10-09"),
    ],
)
def test_explicit_calendar_week_relative_to_publication(text, expected):
    result = resolve_relative_date(text, timezone=ZONE, publication=date(2026, 9, 28))
    assert result.day.isoformat() == expected
    assert result.anchor == date(2026, 9, 28)
    assert result.rule and result.anchor_kind == "publication"
    assert result.relation == ("before" if "前" in text else "on")


@pytest.mark.parametrize("prefix", ["不是", "暂定", "如果批准则", "取消"])
def test_short_excerpt_cannot_hide_uncertain_original_context(client, source, prefix):
    data = source(due=prefix + "下周五提交")
    field = data["draft"].fields[0]
    excerpt = "下周五提交"
    data["draft"] = replace(
        data["draft"],
        fields=(
            replace(
                field,
                value=excerpt,
                evidence=(replace(field.evidence[0], quote=excerpt),),
            ),
        ),
    )
    result = convert(client, data, publication_evidence=data["anchor"])
    assert result.fields[-1].value is None
    assert "条件、暂定或变更" in result.fields[-1].missing_reason


def test_basis_cannot_come_from_another_material(client, source):
    first = source()
    second = source()
    with pytest.raises(DomainError, match="当前原文版本"):
        convert(client, first, publication_evidence=second["anchor"])


def test_missing_due_text_stays_missing_and_cannot_become_a_date(client, source):
    data = source()
    field = replace(data["draft"].fields[0], value=None, evidence=(), missing_reason="原文未明确")
    data["draft"] = replace(data["draft"], fields=(field,))
    assert convert(client, data, publication_evidence=data["anchor"]).fields[-1].value is None


@pytest.mark.parametrize(
    ("anchor", "text", "expected"),
    [
        (date(2026, 12, 31), "明天", date(2027, 1, 1)),
        (date(2028, 2, 28), "后天", date(2028, 3, 1)),
        (date(2026, 10, 4), "下周一", date(2026, 10, 5)),
        (date(2026, 10, 4), "本周一", date(2026, 9, 28)),
    ],
)
def test_year_leap_day_and_sunday_boundaries(anchor, text, expected):
    assert resolve_relative_date(text, timezone=ZONE, publication=anchor).day == expected


@pytest.mark.parametrize(
    "text",
    [
        "周五",
        "下周",
        "10月15日",
        "下周五或下周六",
        "下周五到下周日",
        "下周五下午",
        "暂定下周五",
        "不是下周五",
        "下周五取消",
        "下周五25:00",
        "下周五18:90",
        "第零周周一",
        "第八周五",
        "下周五北京时间18:00",
        "下周五之前一天",
        "",
    ],
)
def test_ambiguous_invalid_or_unsupported_text_never_gets_a_date(text):
    result = resolve_relative_date(text, timezone=ZONE, publication=date(2026, 9, 28))
    assert result.day is None and result.range_start is None and result.reason


def test_missing_anchor_zone_and_date_only_precision():
    assert resolve_relative_date("明天", timezone=ZONE).day is None
    assert resolve_relative_date("明天", timezone=None, publication=date(2026, 9, 28)).day is None
    result = resolve_relative_date("下周五", timezone=ZONE, publication=date(2026, 9, 28))
    assert result.day == date(2026, 10, 9) and result.clock is None
    assert "不补" in result.reason
    assert (
        resolve_relative_date("明天", timezone="invalid", publication=date(2026, 9, 28)).day is None
    )
    assert resolve_relative_date("明天", timezone=ZONE, publication=date.max).day is None


def test_teaching_week_uses_explicit_start_not_semester_start():
    args = dict(
        timezone=ZONE,
        week_one=date(2026, 9, 7),
        semester_start=date(2026, 9, 1),
        semester_end=date(2027, 1, 31),
    )
    result = resolve_relative_date("第八周周五 9:05:03", **args)
    assert result.day == date(2026, 10, 30) and result.clock.isoformat() == "09:05:03"
    interval = resolve_relative_date("第八周", **args)
    assert interval.day is None and interval.clock is None
    assert (interval.range_start, interval.range_end) == (date(2026, 10, 26), date(2026, 11, 1))
    assert "缺少星期几" in interval.reason
    assert resolve_relative_date("第8周星期五", **args).day == result.day
    for text in ("第54周周一", "第零周周一", "第十十周周一", "第30周周一"):
        assert resolve_relative_date(text, **args).day is None
    assert resolve_relative_date("第八周周五", **(args | {"week_one": None})).day is None
    assert (
        resolve_relative_date("第八周周五", **(args | {"week_one": date(2026, 9, 8)})).day is None
    )


@pytest.mark.parametrize(
    ("text", "kind", "expected"),
    [
        ("发布日期：2026年9月28日", "publication", date(2026, 9, 28)),
        ("published_at: 2026-09-27T23:30:00-04:00", "publication", date(2026, 9, 28)),
        ("发布时间：2026-09-28 9:05:03", "publication", date(2026, 9, 28)),
        ("第一教学周周一：2026-09-07，按自然周连续计数", "teaching_week", date(2026, 9, 7)),
    ],
)
def test_anchors_have_explicit_year_and_local_date(text, kind, expected):
    assert parse_date_anchor(text, kind, ZONE) == expected


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("发布日期：9月28日", "publication"),
        ("导入日期：2026-09-28", "publication"),
        ("不是发布日期：2026-09-28", "publication"),
        ("发布日期：2026-02-30", "publication"),
        ("发布日期：2026-09-28或2026-09-29", "publication"),
        ("学期开始：2026-09-07", "teaching_week"),
        ("第一教学周周一：2026-09-08", "teaching_week"),
        ("第一教学周周一：2026-09-07", "teaching_week"),
        ("发布日期：2026-09-28T09:00:00+08:99", "publication"),
    ],
)
def test_invalid_or_unproven_anchor_is_rejected(text, kind):
    with pytest.raises(DomainError):
        parse_date_anchor(text, kind, ZONE)


@pytest.mark.parametrize(
    ("publication", "text"),
    [
        (date(2026, 3, 7), "明天 02:30"),
        (date(2026, 10, 31), "明天 01:30"),
    ],
)
def test_daylight_saving_nonexistent_or_ambiguous_time_is_not_guessed(publication, text):
    result = resolve_relative_date(text, timezone="America/New_York", publication=publication)
    assert result.day is None and "时刻" in result.reason


@pytest.fixture
def source(client, tmp_path, monkeypatch):
    monkeypatch.setattr(client.app.state, "file_storage", LocalFileStorage(tmp_path))

    def create(due="下周三 18:00 截止", anchor="发布日期：2026年9月28日", semester=None):
        payload = {"title": "日期通知", "content": due + "\n\n" + anchor}
        if semester:
            payload["semester_id"] = semester
        response = client.post("/api/v1/materials/text", json=payload)
        assert response.status_code == 201
        data = response.json()
        data["wid"] = client.get("/api/v1/settings").json()["workspace_id"]
        chunks = data["chunks"]
        data["anchor"] = FieldEvidence(chunks[1]["id"], anchor)
        data["draft"] = CandidateDraft(
            CandidateKind.TASK,
            (
                CandidateField(
                    "due_text", due, FieldOrigin.DOCUMENT, (FieldEvidence(chunks[0]["id"], due),)
                ),
            ),
            CandidateScope(semester_id=semester),
        )
        return data

    return create


def convert(client, data, **kwargs):
    with client.app.state.session_factory() as session:
        return resolve_task_deadline(
            build_repositories(session),
            data["wid"],
            data["material"]["id"],
            1,
            data["draft"],
            source_timezone=ZONE,
            **kwargs,
        )


def test_conversion_roundtrip_keeps_original_evidence_and_pending(client, source):
    data = source()
    result = convert(client, data, publication_evidence=data["anchor"])
    resolved = result.fields[-1]
    assert result.fields[0] == data["draft"].fields[0]
    assert resolved.value["date"] == "2026-10-07" and resolved.value["time"] == "18:00:00"
    assert resolved.origin == FieldOrigin.INFERRED
    assert data["anchor"] in resolved.evidence
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        assert session.scalar(select(func.count()).select_from(ExtractedItem)) == 0
        saved = save_candidates(repos, data["wid"], data["material"]["id"], 1, [result])[0]
        loaded = repos.candidates.get(data["wid"], saved.id)
        assert loaded.draft == result and loaded.review_status == ReviewStatus.PENDING
        assert session.scalar(select(func.count()).select_from(Task)) == 0
    data["draft"] = result
    assert convert(client, data, publication_evidence=data["anchor"]) == result


@pytest.mark.parametrize(
    "case", ["missing", "partial-year", "absolute-missing-year", "metadata-only"]
)
def test_missing_basis_never_uses_import_or_current_year(client, source, case):
    data = source(
        due="10月15日提交" if case == "absolute-missing-year" else "下周五提交",
        anchor="发布日期：9月28日" if case == "partial-year" else "发布日期：2026年9月28日",
    )
    if case == "metadata-only":
        response = client.patch(
            f"/api/v1/materials/{data['material']['id']}",
            json={"published_at": "2026-09-28T09:00:00+08:00"},
        )
        assert response.status_code == 200
    kwargs = (
        {} if case in ("missing", "metadata-only") else {"publication_evidence": data["anchor"]}
    )
    result = convert(client, data, **kwargs)
    assert result.fields[-1].value is None and result.fields[-1].missing_reason


@pytest.mark.parametrize(
    "case", ["foreign", "version", "archived", "fake-quote", "scope", "cut-negation"]
)
def test_authorization_and_evidence_cannot_be_bypassed(client, source, case):
    data = source(
        anchor="不是发布日期：2026年9月28日"
        if case == "cut-negation"
        else "发布日期：2026年9月28日"
    )
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        if case == "archived":
            repos.materials.set_archived(data["material"]["id"], True)
            repos.uow.commit()
        evidence = data["anchor"]
        if case == "fake-quote":
            evidence = replace(evidence, quote="发布日期：2027年9月28日")
        if case == "cut-negation":
            evidence = replace(evidence, quote="发布日期：2026年9月28日")
        draft = data["draft"]
        if case == "scope":
            draft = replace(draft, scope=CandidateScope(semester_id=999))
        with pytest.raises((DomainError, NotFoundError)):
            resolve_task_deadline(
                repos,
                data["wid"] + (100 if case == "foreign" else 0),
                data["material"]["id"],
                99 if case == "version" else 1,
                draft,
                source_timezone=ZONE,
                publication_evidence=evidence,
            )


def test_teaching_week_requires_applicable_semester_and_saves_range(client, source):
    sid = client.post(
        "/api/v1/semesters",
        json={
            "name": "2026秋",
            "start_date": "2026-09-01",
            "end_date": "2027-01-31",
        },
    ).json()["id"]
    data = source("第八周", "第一教学周周一：2026-09-07，按自然周连续计数", sid)
    result = convert(client, data, calendar_evidence=data["anchor"])
    assert result.fields[-1].value["date"] is None
    assert result.fields[-1].value["range_start"] == "2026-10-26"
    data2 = source("第八周周五", "第一教学周周一：2026-09-07，按自然周连续计数")
    assert convert(client, data2, calendar_evidence=data2["anchor"]).fields[-1].value is None


def test_b012_relative_date_and_missing_year_fixture_contract():
    root = Path(__file__).resolve().parents[2] / "samples/synthetic/b012"
    expected = json.loads((root / "expected.json").read_text(encoding="utf-8"))["scenarios"]
    text = (root / "relative-date.txt").read_text(encoding="utf-8")
    anchor_text = next(line for line in text.splitlines() if line.startswith("published_at:"))
    anchor = parse_date_anchor(anchor_text, "publication", ZONE)
    assert "下周三 18:00 截止" in text
    result = resolve_relative_date("下周三 18:00 截止", timezone=ZONE, publication=anchor)
    assert f"{result.day}T{result.clock}+08:00" == expected[0]["expected"]["deadline"]
    missing = (root / "missing-year-time.txt").read_text(encoding="utf-8")
    assert "10 月 15 日" in missing
    assert resolve_relative_date("10 月 15 日", timezone=ZONE, publication=anchor).day is None
