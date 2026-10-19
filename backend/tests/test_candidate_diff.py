"""N011：两个候选的新旧字段差异、来源证据与“缺失/明确删除”区分。"""

from dataclasses import replace
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from campusflow.application.candidate_diff import compare_candidates
from campusflow.application.candidates import (
    revise_candidate_field,
    save_candidates,
)
from campusflow.domain.candidate_diff import (
    DiffStatus,
    FieldCategory,
    FieldState,
    classify_field,
    classify_scope,
    field_category,
    field_sort_key,
)
from campusflow.domain.candidates import (
    CandidateDraft,
    CandidateField,
    CandidateKind,
    CandidateScope,
    FieldEvidence,
    FieldOrigin,
)
from campusflow.domain.errors import NotFoundError
from campusflow.infrastructure.db.models import (
    CandidateFieldEvidence,
    CandidateFieldRevision,
    CandidateReviewChange,
    Course,
    ExtractedField,
    ExtractedItem,
    Semester,
    SourceChunk,
)
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.storage.local import LocalFileStorage

OLD_CONTENT = "实验报告下周五前提交到课程平台。地点为实验楼 301。需提交 PDF 文件。"
NEW_CONTENT = "实验报告下周三前提交到课程平台。地点为实验楼 305。需提交 PDF 文件。"
CANCELLED_CONTENT = "实验报告已取消，无需提交。"


def state(value, effective, count=0, *, present=True) -> FieldState:
    return FieldState(
        present=present,
        value=value,
        effective_value=effective,
        verifiable_evidence_count=count,
    )


@pytest.mark.parametrize(
    ("name", "category"),
    [
        ("due_text", FieldCategory.DATE),
        ("due_date", FieldCategory.DATE),
        ("due_time", FieldCategory.DATE),
        ("start_text", FieldCategory.DATE),
        ("location", FieldCategory.LOCATION),
        ("submission_method", FieldCategory.REQUIREMENT),
        ("deliverable", FieldCategory.REQUIREMENT),
        ("rule_text", FieldCategory.REQUIREMENT),
        ("scope_text", FieldCategory.SCOPE),
        ("title", FieldCategory.OTHER),
        ("custom_field", FieldCategory.OTHER),
    ],
)
def test_field_category_mapping(name, category):
    assert field_category(name) is category


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (state(None, None), state(None, None), DiffStatus.UNCHANGED),
        (state(None, None, present=False), state("x", "x"), DiffStatus.ADDED),
        (state("a", "a"), state("b", "b"), DiffStatus.CHANGED),
        (state("a", "a"), state("a", "a"), DiffStatus.UNCHANGED),
        (state("a", "a"), state(None, None, present=False), DiffStatus.MISSING),
        (state("a", "a"), state(None, None, 1), DiffStatus.EXPLICITLY_REMOVED),
        (state("a", "a"), state(None, None, 0), DiffStatus.MISSING),
        (state("a", "a"), state("a", None, 1), DiffStatus.MISSING),
        (state("b", "b"), state(None, "a", 1), DiffStatus.CHANGED),
        (state("", ""), state(None, None), DiffStatus.MISSING),
        (state(0, 0), state(False, False), DiffStatus.CHANGED),
        (
            state({"b": 1, "a": 2}, {"b": 1, "a": 2}),
            state({"a": 2, "b": 1}, {"a": 2, "b": 1}),
            DiffStatus.UNCHANGED,
        ),
        (state(1, 1), state(True, True), DiffStatus.CHANGED),
        (state(None, None, present=False), state(None, None), DiffStatus.UNCHANGED),
    ],
)
def test_field_status_table(left, right, expected):
    assert classify_field(left, right) is expected


def test_explicit_removal_requires_null_raw_value_and_verifiable_evidence():
    assert state(None, None, 1).explicitly_removed is True
    assert state(None, None, 0).explicitly_removed is False
    assert state("原文有值", None, 1).explicitly_removed is False
    assert state(None, None, 1, present=False).explicitly_removed is False


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (None, None, DiffStatus.UNCHANGED),
        (None, "A", DiffStatus.ADDED),
        ("A", None, DiffStatus.MISSING),
        (1, 1, DiffStatus.UNCHANGED),
        (1, 2, DiffStatus.CHANGED),
        ("2 班", "3 班", DiffStatus.CHANGED),
    ],
)
def test_scope_status_table(left, right, expected):
    assert classify_scope(left, right) is expected


def test_field_order_is_stable_and_category_based():
    names = ["title", "scope_text", "location", "due_text", "custom", "deliverable"]
    ordered = sorted(names, key=field_sort_key)
    assert ordered == ["due_text", "location", "deliverable", "scope_text", "custom", "title"]


def text_field(name, value, chunk_id, *, quote=None):
    """构造原文规定字段；空值可附一条取消证据，也可完全没有证据。"""
    if value is None:
        evidence = (FieldEvidence(chunk_id, quote),) if quote else ()
        return CandidateField(
            name,
            None,
            FieldOrigin.DOCUMENT,
            evidence,
            missing_reason="原文未提供或已明确取消",
        )
    return CandidateField(
        name,
        value,
        FieldOrigin.DOCUMENT,
        (FieldEvidence(chunk_id, quote or str(value)),),
    )


def task_draft(
    chunk_id,
    *,
    due="下周五",
    location="实验楼 301",
    submission="课程平台",
    due_quote=None,
    include_deliverable=True,
):
    fields = [
        CandidateField(
            "title", "实验报告", FieldOrigin.DOCUMENT, (FieldEvidence(chunk_id, "实验报告"),)
        ),
        text_field("due_text", due, chunk_id, quote=due_quote),
        text_field("location", location, chunk_id),
        text_field("submission_method", submission, chunk_id),
    ]
    if include_deliverable:
        fields.append(text_field("deliverable", "PDF 文件", chunk_id))
    return CandidateDraft(CandidateKind.TASK, tuple(fields))


@pytest.fixture
def sources(client, tmp_path, monkeypatch):
    monkeypatch.setattr(client.app.state, "file_storage", LocalFileStorage(tmp_path))
    workspace_id = client.get("/api/v1/settings").json()["workspace_id"]

    def upload(title, content):
        response = client.post("/api/v1/materials/text", json={"title": title, "content": content})
        assert response.status_code == 201, response.text
        result = response.json()
        result["material"]["workspace_id"] = workspace_id
        return result

    return client, upload, workspace_id


def save(client, material, draft):
    with client.app.state.session_factory() as session:
        return save_candidates(
            build_repositories(session),
            material["workspace_id"],
            material["id"],
            1,
            [draft],
        )[0]


def compare(client, left_id, right_id):
    response = client.get(f"/api/v1/candidates/{left_id}/diff/{right_id}")
    assert response.status_code == 200, response.text
    return response.json()


def field_map(body):
    return {item["name"]: item for item in body["fields"]}


def save_pair(
    client,
    sources,
    *,
    new_due="下周三",
    new_location="实验楼 305",
    new_include_deliverable=True,
):
    _, upload, _ = sources
    old = upload("旧通知", OLD_CONTENT)
    new = upload("新通知", NEW_CONTENT)
    left = save(client, old["material"], task_draft(old["chunks"][0]["id"]))
    right = save(
        client,
        new["material"],
        task_draft(
            new["chunks"][0]["id"],
            due=new_due,
            location=new_location,
            include_deliverable=new_include_deliverable,
        ),
    )
    return old, new, left, right


def test_diff_points_each_change_to_its_own_source(sources):
    client, _, _ = sources
    old, new, left, right = save_pair(client, sources, new_include_deliverable=False)
    # 新候选没有交付物字段，用于验证“字段不存在”与“值为空”分开显示。

    body = compare(client, left.id, right.id)
    assert body["left_role"] == "baseline" and body["right_role"] == "comparison"
    assert body["comparison_basis"] == "effective_value"
    assert body["left"]["candidate_id"] == left.id
    assert body["left"]["material_title"] == "旧通知"
    assert body["right"]["candidate_id"] == right.id
    assert body["right"]["material_title"] == "新通知"

    fields = field_map(body)
    assert fields["title"]["status"] == "unchanged"
    due = fields["due_text"]
    assert due["status"] == "changed"
    assert due["left"]["value"] == "下周五" and due["right"]["value"] == "下周三"
    left_evidence = due["left"]["evidence"][0]
    right_evidence = due["right"]["evidence"][0]
    assert left_evidence["chunk_id"] == old["chunks"][0]["id"]
    assert right_evidence["chunk_id"] == new["chunks"][0]["id"]
    assert left_evidence["quote"] == "下周五" and right_evidence["quote"] == "下周三"
    assert left_evidence["locator_value"] and right_evidence["locator_value"]
    assert left_evidence["verifiable"] is True and right_evidence["verifiable"] is True

    assert fields["location"]["status"] == "changed"
    assert fields["submission_method"]["status"] == "unchanged"
    deliverable = fields["deliverable"]
    assert deliverable["status"] == "missing"
    assert deliverable["left"]["present"] is True
    assert deliverable["right"]["present"] is False
    assert deliverable["right"]["origin"] is None
    assert deliverable["right"]["evidence"] == []
    assert body["summary"] == {
        "total": 5,
        "unchanged": 2,
        "changed": 2,
        "added": 0,
        "missing": 1,
        "explicitly_removed": 0,
    }
    assert [item["name"] for item in body["fields"]] == sorted(fields, key=field_sort_key)
    assert compare(client, left.id, right.id)["fields"] == body["fields"]


def test_missing_and_explicit_removal_are_distinct(sources):
    client, upload, _ = sources
    old = upload("旧通知", OLD_CONTENT)
    new = upload("取消通知", CANCELLED_CONTENT)
    left = save(client, old["material"], task_draft(old["chunks"][0]["id"]))
    right = save(
        client,
        new["material"],
        task_draft(
            new["chunks"][0]["id"],
            due=None,
            due_quote="已取消",
            location=None,
            submission=None,
            include_deliverable=False,
        ),
    )

    body = compare(client, left.id, right.id)
    fields = field_map(body)
    due = fields["due_text"]
    assert due["status"] == "explicitly_removed"
    assert due["right"]["present"] is True
    assert due["right"]["value"] is None
    assert due["right"]["missing_reason"]
    assert due["right"]["explicitly_removed"] is True
    assert due["right"]["verifiable"] is True
    assert due["right"]["evidence"][0]["quote"] == "已取消"

    location = fields["location"]
    assert location["status"] == "missing"
    assert location["right"]["present"] is True
    assert location["right"]["value"] is None
    assert location["right"]["explicitly_removed"] is False
    assert location["right"]["evidence"] == []

    assert body["summary"]["explicitly_removed"] == 1
    assert body["summary"]["missing"] == 3


def test_unverifiable_evidence_degrades_explicit_removal_to_missing(sources):
    client, upload, _ = sources
    old = upload("旧通知", OLD_CONTENT)
    new = upload("取消通知", CANCELLED_CONTENT)
    left = save(client, old["material"], task_draft(old["chunks"][0]["id"]))
    right = save(
        client,
        new["material"],
        task_draft(
            new["chunks"][0]["id"],
            due=None,
            due_quote="已取消",
            location=None,
            submission=None,
            include_deliverable=False,
        ),
    )
    assert field_map(compare(client, left.id, right.id))["due_text"]["status"] == (
        "explicitly_removed"
    )

    # 原文片段被改写后，摘录不再能逐字找到；“附过证据”不等于“证据仍可验证”。
    with client.app.state.session_factory() as session:
        session.execute(
            update(SourceChunk)
            .where(SourceChunk.id == new["chunks"][0]["id"])
            .values(text="实验报告有新的安排。")
        )
        session.commit()

    due = field_map(compare(client, left.id, right.id))["due_text"]
    assert due["status"] == "missing"
    assert due["right"]["verifiable"] is False
    assert due["right"]["explicitly_removed"] is False
    assert due["right"]["evidence"][0]["verifiable"] is False


def test_effective_values_drive_comparison_but_raw_value_and_evidence_stay(sources):
    client, _, workspace_id = sources
    _, _, left, right = save_pair(client, sources)

    def revise(value, note):
        with client.app.state.session_factory() as session:
            return revise_candidate_field(
                build_repositories(session),
                workspace_id,
                right.id,
                "due_text",
                value=value,
                origin=FieldOrigin.USER,
                operator="student",
                note=note,
            )

    revise("下周四", "用户核对为下周四")
    due = field_map(compare(client, left.id, right.id))["due_text"]
    assert due["status"] == "changed"
    assert due["right"]["value"] == "下周三"
    assert due["right"]["effective_value"] == "下周四"
    assert due["right"]["origin"] == "document"
    assert due["right"]["effective_origin"] == "user"
    assert due["right"]["evidence"][0]["quote"] == "下周三"

    revise("下周五", "与旧通知一致")
    due = field_map(compare(client, left.id, right.id))["due_text"]
    assert due["status"] == "unchanged"
    assert due["right"]["value"] == "下周三"
    assert due["right"]["effective_value"] == "下周五"

    revise(None, "用户清空该字段")
    due = field_map(compare(client, left.id, right.id))["due_text"]
    assert due["status"] == "missing"
    assert due["right"]["explicitly_removed"] is False


def test_swapping_direction_reports_added_instead_of_missing(sources):
    client, _, _ = sources
    _, _, left, right = save_pair(client, sources, new_include_deliverable=False)
    body = compare(client, right.id, left.id)
    deliverable = field_map(body)["deliverable"]
    assert deliverable["status"] == "added"
    assert deliverable["left"]["present"] is False
    assert deliverable["right"]["present"] is True
    assert deliverable["right"]["candidate_id"] == left.id
    assert body["left"]["candidate_id"] == right.id


def test_scope_differences_are_listed_without_applying_them(sources):
    client, upload, workspace_id = sources

    def add_scope(semester_name, course_name):
        with client.app.state.session_factory() as session:
            semester = Semester(
                workspace_id=workspace_id,
                name=semester_name,
                start_date=date(2026, 9, 1),
                end_date=date(2027, 1, 1),
            )
            session.add(semester)
            session.flush()
            course = Course(
                workspace_id=workspace_id,
                semester_id=semester.id,
                name=course_name,
            )
            session.add(course)
            session.commit()
            return semester.id, course.id

    first_semester, first_course = add_scope("秋季", "数据库")
    second_semester, second_course = add_scope("春季", "线性代数")
    old = upload("旧通知", OLD_CONTENT)
    new = upload("新通知", NEW_CONTENT)
    left = save(
        client,
        old["material"],
        replace(
            task_draft(old["chunks"][0]["id"]),
            scope=CandidateScope(first_semester, first_course, "2 班"),
        ),
    )
    right = save(
        client,
        new["material"],
        replace(
            task_draft(new["chunks"][0]["id"], due="下周三", location="实验楼 305"),
            scope=CandidateScope(second_semester, second_course),
        ),
    )

    body = compare(client, left.id, right.id)
    differences = {item["component"]: item for item in body["scope"]["differences"]}
    assert differences["semester"]["status"] == "changed"
    assert differences["semester"]["left_name"] == "秋季"
    assert differences["semester"]["right_name"] == "春季"
    assert differences["course"]["status"] == "changed"
    assert differences["course"]["left_name"] == "数据库"
    assert differences["course"]["right_name"] == "线性代数"
    assert differences["class_name"]["status"] == "missing"
    assert differences["class_name"]["left_value"] == "2 班"
    assert differences["class_name"]["right_value"] is None
    # 只读对照：不产出“新版生效/旧版被替代”的字段。
    assert set(body) == {
        "left_role",
        "right_role",
        "comparison_basis",
        "left",
        "right",
        "scope",
        "fields",
        "summary",
    }


def test_same_candidate_different_kind_and_missing_candidate_are_rejected(sources):
    client, upload, _ = sources
    old = upload("旧通知", OLD_CONTENT)
    left = save(client, old["material"], task_draft(old["chunks"][0]["id"]))
    rule = save(
        client,
        old["material"],
        CandidateDraft(
            CandidateKind.RULE,
            (CandidateField("title", "实验安排", FieldOrigin.USER),),
        ),
    )

    same = client.get(f"/api/v1/candidates/{left.id}/diff/{left.id}")
    assert same.status_code == 400 and "同一个候选" in same.json()["detail"]
    different = client.get(f"/api/v1/candidates/{left.id}/diff/{rule.id}")
    assert different.status_code == 400 and "同一类型" in different.json()["detail"]
    missing = client.get(f"/api/v1/candidates/{left.id}/diff/{left.id + 99999}")
    assert missing.status_code == 404 and "候选事项不存在" in missing.json()["detail"]


def test_other_workspace_and_anonymous_access_are_hidden(sources):
    client, _, workspace_id = sources
    _, _, left, right = save_pair(client, sources)
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        with pytest.raises(NotFoundError):
            compare_candidates(repos, workspace_id + 100, left.id, right.id)

    anonymous = TestClient(client.app)
    try:
        response = anonymous.get(f"/api/v1/candidates/{left.id}/diff/{right.id}")
        assert response.status_code == 401
    finally:
        anonymous.close()


def test_archived_source_remains_readable_and_flagged(sources):
    client, _, _ = sources
    old, _, left, right = save_pair(client, sources)
    response = client.post(f"/api/v1/materials/{old['material']['id']}/archive")
    assert response.status_code == 200, response.text

    body = compare(client, left.id, right.id)
    assert body["left"]["material_archived"] is True
    assert body["right"]["material_archived"] is False
    assert body["left"]["material_title"] == "旧通知"
    due = field_map(body)["due_text"]
    assert due["left"]["evidence"][0]["verifiable"] is True


def test_any_review_status_is_comparable(sources):
    client, _, _ = sources
    _, _, left, right = save_pair(client, sources)
    response = client.patch(
        f"/api/v1/candidates/{right.id}/review-status", json={"status": "confirmed"}
    )
    assert response.status_code == 200, response.text

    body = compare(client, left.id, right.id)
    assert body["left"]["review_status"] == "pending"
    assert body["right"]["review_status"] == "confirmed"


def test_compare_is_read_only_and_does_not_commit(sources, monkeypatch):
    client, _, workspace_id = sources
    _, _, left, right = save_pair(client, sources)
    models = (
        ExtractedItem,
        ExtractedField,
        CandidateFieldEvidence,
        CandidateFieldRevision,
        CandidateReviewChange,
    )
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)

        def fail_commit():
            raise AssertionError("只读比较不应提交事务")

        monkeypatch.setattr(repos.uow, "commit", fail_commit)
        before = {
            model.__name__: session.scalar(select(func.count()).select_from(model))
            for model in models
        }
        result = compare_candidates(repos, workspace_id, left.id, right.id)
        after = {
            model.__name__: session.scalar(select(func.count()).select_from(model))
            for model in models
        }
    assert result.summary.total == 5
    assert before == after


def test_existing_candidate_routes_still_work(sources):
    client, _, _ = sources
    _, _, left, right = save_pair(client, sources)
    assert client.get("/api/v1/candidates/summary").status_code == 200
    assert client.get(f"/api/v1/candidates/{left.id}").status_code == 200
    assert client.get(f"/api/v1/candidates/{left.id}/diff/{right.id}").status_code == 200
