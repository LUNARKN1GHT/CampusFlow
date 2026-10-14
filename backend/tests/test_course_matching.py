"""X007：严格课程匹配、歧义保留、授权范围与待核对结果持久化。"""

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select

from campusflow.application.candidates import save_candidates
from campusflow.application.course_matching import match_candidate_course
from campusflow.domain.candidates import (
    CandidateDraft,
    CandidateField,
    CandidateKind,
    CandidateScope,
    FieldEvidence,
    FieldOrigin,
    ReviewReason,
)
from campusflow.domain.course_matching import CourseOption, match_course
from campusflow.domain.errors import DomainError, NotFoundError
from campusflow.domain.states import ReviewStatus
from campusflow.infrastructure.db.models import Course, ExtractedItem, Semester, Task, Workspace
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.storage.local import LocalFileStorage

CATALOG = [
    CourseOption(1, "大学物理", "PHY2026-A"),
    CourseOption(2, "大学物理", "PHY2026-B"),
    CourseOption(3, "高等数学", None),
]


@pytest.mark.parametrize(
    ("name", "section", "status", "chosen", "options"),
    [
        ("大学物理", None, "ambiguous", None, [1, 2]),
        ("大学物理", "PHY2026-A", "unique_candidate", 1, [1]),
        ("大学物理", "PHY2026-B", "unique_candidate", 2, [2]),
        ("大学物理", "PHY2026-C", "class_mismatch", None, [1, 2]),
        ("高等数学", None, "unique_candidate", 3, [3]),
        ("高等数学", "A班", "class_mismatch", None, [3]),
        ("高数", None, "unmatched", None, [1, 2, 3]),
        ("大学物", None, "unmatched", None, [1, 2, 3]),
        ("大学 物理", None, "unmatched", None, [1, 2, 3]),
        ("大学物理", "phy2026-a", "class_mismatch", None, [1, 2]),
        ("大学物理", "ＰＨＹ２０２６－Ａ", "class_mismatch", None, [1, 2]),
        ("  大学物理  ", " PHY2026-A ", "unique_candidate", 1, [1]),
        (None, "PHY2026-A", "missing_name", None, [1, 2, 3]),
    ],
)
def test_strict_matching_keeps_ambiguity_and_manual_choices(name, section, status, chosen, options):
    result = match_course(CATALOG, name, section)
    assert result.status == status and result.suggested_course_id == chosen
    assert [c.id for c in result.options] == options
    assert result.reason


def test_duplicate_same_name_and_class_never_selects_first_record():
    courses = [CourseOption(9, "大学物理", "A"), CourseOption(1, "大学物理", "A")]
    result = match_course(courses, "大学物理", "A")
    assert result.status == "ambiguous" and result.suggested_course_id is None
    assert [c.id for c in result.matches] == [1, 9]
    assert match_course([], "大学物理", None).options == ()


@pytest.fixture
def setup(client, tmp_path, monkeypatch):
    monkeypatch.setattr(client.app.state, "file_storage", LocalFileStorage(tmp_path))
    wid = client.get("/api/v1/settings").json()["workspace_id"]
    semesters = []
    for year in (2026, 2025):
        response = client.post(
            "/api/v1/semesters",
            json={
                "name": f"{year}秋",
                "start_date": f"{year}-09-01",
                "end_date": f"{year + 1}-01-31",
            },
        )
        assert response.status_code == 201
        semesters.append(response.json()["id"])
    courses = []
    for sid, section in (
        (semesters[0], "PHY2026-A"),
        (semesters[0], "PHY2026-B"),
        (semesters[1], "PHY2026-A"),
    ):
        response = client.post(
            "/api/v1/courses",
            json={
                "semester_id": sid,
                "name": "大学物理",
                "class_name": section,
            },
        )
        assert response.status_code == 201
        courses.append(response.json()["id"])
    return {"wid": wid, "sid": semesters[0], "old_sid": semesters[1], "courses": courses}


@pytest.fixture
def source(client, setup):
    def create(name="大学物理", section=None, **metadata):
        content = "提交报告\n\n" + (f"课程：{name}。" if name else "课程待核对")
        if section:
            content += "\n\n教学班：" + section
        response = client.post(
            "/api/v1/materials/text",
            json={
                "title": "课程通知",
                "content": content,
                **metadata,
            },
        )
        assert response.status_code == 201
        data = response.json()
        data.update(setup)
        chunks = data["chunks"]
        data["name"] = CandidateField(
            "course_name_text",
            name,
            FieldOrigin.DOCUMENT,
            (FieldEvidence(chunks[1]["id"], name),) if name else (),
            missing_reason=None if name else "未写课程名",
        )
        data["section"] = CandidateField(
            "class_name_text",
            section,
            FieldOrigin.DOCUMENT,
            (FieldEvidence(chunks[2]["id"], section),) if section else (),
            missing_reason=None if section else "未写教学班",
        )
        data["draft"] = CandidateDraft(
            CandidateKind.TASK,
            (
                CandidateField(
                    "title",
                    "提交报告",
                    FieldOrigin.DOCUMENT,
                    (FieldEvidence(chunks[0]["id"], "提交报告"),),
                ),
            ),
            CandidateScope(
                metadata.get("semester_id"), metadata.get("course_id"), metadata.get("class_name")
            ),
        )
        return data

    return create


def run_match(client, data, **kwargs):
    with client.app.state.session_factory() as session:
        params = {
            "semester_id": data["sid"],
            "course_name": data["name"],
            "class_name": data["section"],
        }
        params.update(kwargs)
        return match_candidate_course(
            build_repositories(session),
            data["wid"],
            data["material"]["id"],
            1,
            data["draft"],
            **params,
        )


@pytest.mark.parametrize("kind", list(CandidateKind))
def test_supported_candidates_keep_scope_and_save_pending(client, source, kind):
    data = source(section="PHY2026-A")
    data["draft"] = replace(data["draft"], kind=kind)
    result = run_match(client, data)
    matching = result.fields[-1]
    assert matching.value["status"] == "unique_candidate"
    assert matching.value["suggested_course_id"] == data["courses"][0]
    assert result.scope == data["draft"].scope and result.scope.course_id is None
    assert result.fields[0] == data["draft"].fields[0]
    assert matching.origin == FieldOrigin.INFERRED
    assert {e.chunk_id for e in matching.evidence} == {
        data["chunks"][1]["id"],
        data["chunks"][2]["id"],
    }
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        assert session.scalar(select(func.count()).select_from(ExtractedItem)) == 0
        saved = save_candidates(repos, data["wid"], data["material"]["id"], 1, [result])[0]
        loaded = repos.candidates.get(data["wid"], saved.id)
        assert loaded.draft == result and loaded.review_status == ReviewStatus.PENDING
        assert session.scalar(select(func.count()).select_from(Task)) == 0
    data["draft"] = result
    assert run_match(client, data, course_name=None, class_name=None) == result


def test_same_names_cannot_cross_selected_semester_and_names_remain_verbatim(client, source):
    data = source(name="  大学物理  ")
    result = run_match(client, data)
    assert result.fields[-3].value == "  大学物理  "
    value = result.fields[-1].value
    assert value["status"] == "ambiguous" and value["suggested_course_id"] is None
    assert {c["id"] for c in value["options"]} == set(data["courses"][:2])
    old = run_match(client, data, semester_id=data["old_sid"])
    assert old.fields[-1].value["suggested_course_id"] == data["courses"][2]


def test_missing_semester_does_not_search_all_courses(client, source):
    data = source()
    value = run_match(client, data, semester_id=None).fields[-1].value
    assert value["status"] == "missing_semester" and value["options"] == []
    assert value["suggested_course_id"] is None


@pytest.mark.parametrize(
    ("name", "section", "status"),
    [
        ("高数", None, "unmatched"),
        (None, None, "missing_name"),
        ("大学物理", "C班", "class_mismatch"),
    ],
)
def test_unmatched_input_retains_text_and_choices(client, source, name, section, status):
    data = source(name, section)
    result = run_match(client, data)
    assert result.fields[-3].value == name and result.fields[-2].value == section
    value = result.fields[-1].value
    assert value["status"] == status and value["suggested_course_id"] is None
    assert value["option_count"] == 2
    assert result.fields[-1].origin == (
        FieldOrigin.SUGGESTION if not name and not section else FieldOrigin.INFERRED
    )


@pytest.mark.parametrize(
    "case",
    [
        "foreign",
        "missing-version",
        "archived",
        "fake-evidence",
        "wrong-origin",
        "wrong-name",
        "numeric",
        "scope-semester",
    ],
)
def test_invalid_context_and_source_are_rejected_before_output(client, source, case):
    data = source(semester_id=None)
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        if case == "archived":
            repos.materials.set_archived(data["material"]["id"], True)
            repos.uow.commit()
        field = data["name"]
        if case == "fake-evidence":
            field = replace(field, evidence=(FieldEvidence(999999, "大学物理"),))
        elif case == "wrong-origin":
            field = replace(field, origin=FieldOrigin.SUGGESTION, explanation="猜测")
        elif case == "wrong-name":
            field = replace(field, name="other_name")
        elif case == "numeric":
            field = replace(field, value=12)
        if case == "scope-semester":
            data["draft"] = replace(
                data["draft"], scope=CandidateScope(semester_id=data["old_sid"])
            )
        with pytest.raises((DomainError, NotFoundError)):
            match_candidate_course(
                repos,
                data["wid"] + (100 if case == "foreign" else 0),
                data["material"]["id"],
                99 if case == "missing-version" else 1,
                data["draft"],
                semester_id=data["sid"],
                course_name=field,
            )
        assert session.scalar(select(func.count()).select_from(ExtractedItem)) == 0


def test_other_workspace_courses_and_semesters_are_unavailable(client, source):
    data = source()
    with client.app.state.session_factory() as session:
        other = Workspace(name="其他人")
        session.add(other)
        session.flush()
        term = Semester(
            workspace_id=other.id,
            name="其他学期",
            start_date=date(2026, 9, 1),
            end_date=date(2027, 1, 31),
        )
        session.add(term)
        session.flush()
        course = Course(
            workspace_id=other.id, semester_id=term.id, name="大学物理", class_name="PHY2026-A"
        )
        session.add(course)
        session.commit()
        sid, cid = term.id, course.id
    with pytest.raises(NotFoundError):
        run_match(client, data, semester_id=sid)
    assert cid not in {c["id"] for c in run_match(client, data).fields[-1].value["options"]}


def test_other_material_evidence_and_archived_semester_are_rejected(client, source):
    first, second = source(), source()
    with pytest.raises(DomainError, match="当前原文版本"):
        run_match(client, first, course_name=second["name"])
    client.patch(f"/api/v1/semesters/{first['sid']}", json={"archived": True})
    with pytest.raises(DomainError, match="归档"):
        run_match(client, first)


def test_prebound_course_cannot_hide_ambiguity_or_override_conflict(client, source, setup):
    data = source(semester_id=setup["sid"], course_id=setup["courses"][0])
    result = run_match(client, data)
    assert result.fields[-1].value["status"] == "ambiguous"
    conflict = source(section="PHY2026-B", semester_id=setup["sid"], course_id=setup["courses"][0])
    result = run_match(client, conflict)
    assert result.fields[-1].value["status"] == "scope_conflict"
    assert result.fields[-1].value["suggested_course_id"] is None
    assert result.scope == conflict["draft"].scope


def test_known_scope_class_cannot_be_overwritten_by_unique_name(client, source):
    data = source(name="独有课程", class_name="B班")
    client.post(
        "/api/v1/courses",
        json={"semester_id": data["sid"], "name": "独有课程", "class_name": "A班"},
    )
    result = run_match(client, data)
    assert result.fields[-1].value["status"] == "scope_conflict"
    assert result.fields[-1].value["suggested_course_id"] is None


def test_option_limit_never_creates_false_uniqueness(client, source):
    data = source()
    with client.app.state.session_factory() as session:
        session.add_all(
            [
                Course(
                    workspace_id=data["wid"],
                    semester_id=data["sid"],
                    name="大学物理",
                    class_name=f"extra-{i}",
                )
                for i in range(25)
            ]
        )
        session.commit()
    value = run_match(client, data).fields[-1].value
    assert value["status"] == "ambiguous" and value["suggested_course_id"] is None
    assert value["match_count"] == value["option_count"] == 27
    assert value["options_truncated"] is True and len(value["options"]) == 20


def test_model_cannot_override_matching_review_reason(client, source):
    data = source()
    first = run_match(client, data)
    data["draft"] = replace(
        first, review_reasons=(ReviewReason("course_match_review", "已确认", "course_match"),)
    )
    assert "仍须人工确认" in run_match(client, data).review_reasons[-1].message


@pytest.mark.parametrize("sid", [True, False, 0, -1, "1"])
def test_invalid_semester_id_cannot_alias_valid_selected_scope(client, source, setup, sid):
    data = source(semester_id=setup["sid"])
    with pytest.raises(DomainError, match="编号无效"):
        run_match(client, data, semester_id=sid)


def test_name_not_in_its_own_evidence_and_bad_original_evidence_are_rejected(client, source):
    data = source()
    with pytest.raises(DomainError, match="自身证据"):
        run_match(client, data, course_name=replace(data["name"], value="高等数学"))
    original = data["draft"].fields[0]
    data["draft"] = replace(
        data["draft"],
        fields=(
            replace(
                original,
                evidence=(FieldEvidence(999999, "提交报告"),),
            ),
        ),
    )
    with pytest.raises(DomainError, match="当前原文版本"):
        run_match(client, data)


def test_recompute_refreshes_catalog_without_leaving_old_suggestion(client, source):
    data = source(section="PHY2026-A")
    first = run_match(client, data)
    assert first.fields[-1].value["suggested_course_id"] == data["courses"][0]
    response = client.patch(f"/api/v1/courses/{data['courses'][0]}", json={"name": "大学物理实验"})
    assert response.status_code == 200
    data["draft"] = first
    refreshed = run_match(client, data)
    assert refreshed.fields[-1].value["status"] == "class_mismatch"
    assert refreshed.fields[-1].value["suggested_course_id"] is None
    assert sum(f.name == "course_match" for f in refreshed.fields) == 1
    assert refreshed.scope == first.scope


def test_b012_same_course_sections_are_distinct():
    root = Path(__file__).resolve().parents[2] / "samples/synthetic/b012"
    expected = json.loads((root / "expected.json").read_text(encoding="utf-8"))["scenarios"][2][
        "expected"
    ]
    ids = []
    for suffix, record in zip(("a", "b"), expected["records"], strict=True):
        text = (root / f"same-course-section-{suffix}.txt").read_text(encoding="utf-8")
        name = next(
            line.removeprefix("course_name: ")
            for line in text.splitlines()
            if line.startswith("course_name:")
        )
        section = next(
            line.removeprefix("section: ")
            for line in text.splitlines()
            if line.startswith("section:")
        )
        assert section == record["section"]
        ids.append(match_course(CATALOG, name, section).suggested_course_id)
    assert ids == [1, 2] and expected["merge_allowed"] is False
