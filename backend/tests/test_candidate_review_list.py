"""R001：待核对事项列表、缺失原因筛选与计数。"""

from datetime import date

import pytest

from campusflow.application.candidates import (
    save_candidates,
    set_candidate_review_status,
)
from campusflow.domain.candidates import (
    CandidateDraft,
    CandidateField,
    CandidateKind,
    CandidateScope,
    FieldEvidence,
    FieldOrigin,
    ReviewReason,
)
from campusflow.domain.states import ReviewStatus
from campusflow.infrastructure.db.models import Course, Semester, Workspace
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.storage.local import LocalFileStorage

CONTENT = (
    "实验报告：下周五提交课程平台。\n\n机房开放时间见附件表格。\n\n实验课迟到三次取消考试资格。"
)
DEFAULT_REASONS = (ReviewReason("missing_time", "需要核对提交时刻", "due_time"),)


def task_draft(
    chunk_id,
    *,
    scope=None,
    reasons=DEFAULT_REASONS,
    kind=CandidateKind.TASK,
):
    return CandidateDraft(
        kind,
        (
            CandidateField(
                "title",
                "实验报告",
                FieldOrigin.DOCUMENT,
                (FieldEvidence(chunk_id, "实验报告"),),
            ),
            CandidateField(
                "due_time", None, FieldOrigin.DOCUMENT, missing_reason="原文没有具体时刻"
            ),
        ),
        scope=scope or CandidateScope(),
        review_reasons=tuple(reasons),
    )


def rule_draft(chunk_id, *, reasons=()):
    return CandidateDraft(
        CandidateKind.RULE,
        (
            CandidateField(
                "title",
                "机房开放规则",
                FieldOrigin.DOCUMENT,
                (FieldEvidence(chunk_id, "机房开放时间"),),
            ),
            CandidateField(
                "rule_text",
                "机房开放时间见附件表格",
                FieldOrigin.DOCUMENT,
                (FieldEvidence(chunk_id, "机房开放时间见附件表格"),),
            ),
        ),
        review_reasons=tuple(reasons),
    )


@pytest.fixture
def source(client, tmp_path, monkeypatch):
    monkeypatch.setattr(client.app.state, "file_storage", LocalFileStorage(tmp_path))
    response = client.post("/api/v1/materials/text", json={"title": "课程通知", "content": CONTENT})
    assert response.status_code == 201
    result = response.json()
    workspace_id = client.get("/api/v1/settings").json()["workspace_id"]
    result["material"]["workspace_id"] = workspace_id
    return client, result["material"], result["chunks"]


def save(source, drafts):
    client, material, chunks = source
    with client.app.state.session_factory() as session:
        return save_candidates(
            build_repositories(session), material["workspace_id"], material["id"], 1, drafts
        )


def create_course(source, *, name="数据库"):
    """在当前空间直接落库一个学期与课程，返回 (semester_id, course_id)。"""
    client, material, _ = source
    with client.app.state.session_factory() as session:
        semester = Semester(
            workspace_id=material["workspace_id"],
            name="秋季",
            start_date=date(2026, 9, 1),
            end_date=date(2027, 1, 1),
        )
        session.add(semester)
        session.flush()
        course = Course(workspace_id=material["workspace_id"], semester_id=semester.id, name=name)
        session.add(course)
        session.commit()
        return semester.id, course.id


def confirm(source, candidate_id, status=ReviewStatus.CONFIRMED):
    client, material, _ = source
    with client.app.state.session_factory() as session:
        set_candidate_review_status(
            build_repositories(session),
            material["workspace_id"],
            candidate_id,
            status,
            operator="student",
        )


def test_list_defaults_to_pending_items_with_reasons_and_source(source):
    client, material, chunks = source
    task, rule = save(source, [task_draft(chunks[0]["id"]), rule_draft(chunks[1]["id"])])
    confirm(source, rule.id)

    listed = client.get("/api/v1/candidates")
    assert listed.status_code == 200
    body = listed.json()
    assert [item["id"] for item in body] == [task.id]
    item = body[0]
    assert item["kind"] == "task"
    assert item["review_status"] == "pending"
    assert item["title"] == "实验报告"
    assert item["source_material_id"] == material["id"]
    assert item["version_no"] == 1
    assert item["review_reasons"] == [
        {"code": "missing_time", "message": "需要核对提交时刻", "field": "due_time"}
    ]

    confirmed = client.get("/api/v1/candidates?status=confirmed")
    assert [item["id"] for item in confirmed.json()] == [rule.id]
    both = client.get("/api/v1/candidates?status=pending&status=confirmed")
    assert len(both.json()) == 2


def test_reason_filter_and_counts_group_by_code(source):
    client, material, chunks = source
    truncated = ReviewReason("truncated", "截图不完整", "rule_text")
    duplicated = (
        ReviewReason("missing_time", "缺时刻", "due_time"),
        ReviewReason("missing_time", "重复原因", "due_time"),
    )
    pending_task, pending_rule, confirmed_rule = save(
        source,
        [
            task_draft(chunks[0]["id"]),
            rule_draft(chunks[1]["id"], reasons=[truncated]),
            rule_draft(chunks[1]["id"], reasons=[truncated]),
        ],
    )
    confirm(source, confirmed_rule.id)
    with client.app.state.session_factory() as session:
        (duplicate_reason_task,) = save_candidates(
            build_repositories(session),
            material["workspace_id"],
            material["id"],
            1,
            [task_draft(chunks[0]["id"], reasons=duplicated)],
        )

    only_missing_time = client.get("/api/v1/candidates?reason=missing_time")
    # 第 4 个候选带两条同代码原因，仍只出现一次
    assert [item["id"] for item in only_missing_time.json()] == [
        pending_task.id,
        duplicate_reason_task.id,
    ]
    # 默认只列待核对：另一条同原因候选已确认，需显式指定状态才计入列表
    only_truncated = client.get("/api/v1/candidates?reason=truncated")
    assert [item["id"] for item in only_truncated.json()] == [pending_rule.id]
    truncated_all = client.get(
        "/api/v1/candidates?reason=truncated&status=pending&status=confirmed"
    )
    assert len(truncated_all.json()) == 2
    assert client.get("/api/v1/candidates?reason=unknown_code").json() == []

    counts = client.get("/api/v1/candidates/summary")
    assert counts.status_code == 200
    body = counts.json()
    assert body["total"] == 4
    assert body["by_status"] == {"pending": 3, "confirmed": 1}
    # 同一候选上的重复原因只计一次
    assert body["by_reason"] == {"missing_time": 2, "truncated": 2}


def test_list_filters_by_course_kind_and_material(source):
    client, material, chunks = source
    semester_id, course_id = create_course(source)
    scoped, plain = save(
        source,
        [
            task_draft(chunks[0]["id"], scope=CandidateScope(semester_id, course_id)),
            rule_draft(chunks[1]["id"]),
        ],
    )

    assert [
        item["id"] for item in client.get(f"/api/v1/candidates?course_id={course_id}").json()
    ] == [scoped.id]
    assert [
        item["id"] for item in client.get(f"/api/v1/candidates?semester_id={semester_id}").json()
    ] == [scoped.id]
    assert [item["id"] for item in client.get("/api/v1/candidates?kind=rule").json()] == [plain.id]
    assert len(client.get(f"/api/v1/candidates?material_id={material['id']}").json()) == 2
    counts = client.get(f"/api/v1/candidates/summary?course_id={course_id}").json()
    assert counts["total"] == 1

    assert client.get("/api/v1/candidates?material_id=999999").status_code == 404


def test_review_status_and_task_progress_filters_are_not_mixed(source):
    """核对状态与任务执行进度是两套状态：接口只接受 ReviewStatus。"""
    client, _, _ = source
    save(source, [task_draft(1)])
    for progress in ("not_started", "in_progress", "blocked", "completed", "cancelled"):
        assert client.get(f"/api/v1/candidates?status={progress}").status_code == 422
    assert client.get("/api/v1/candidates?status=pending").status_code == 200
    listed = client.get("/api/v1/candidates").json()
    assert all(
        item["review_status"] in {status.value for status in ReviewStatus} for item in listed
    )
    assert "progress" not in listed[0]


def test_foreign_workspace_course_or_semester_filter_returns_not_found(source):
    client, material, chunks = source
    save(source, [task_draft(chunks[0]["id"])])
    with client.app.state.session_factory() as session:
        other = Workspace(name="他人空间")
        session.add(other)
        session.flush()
        foreign_semester = Semester(
            workspace_id=other.id,
            name="其他学期",
            start_date=date(2026, 9, 1),
            end_date=date(2027, 1, 1),
        )
        session.add(foreign_semester)
        session.flush()
        foreign_course = Course(
            workspace_id=other.id, semester_id=foreign_semester.id, name="其他课"
        )
        session.add(foreign_course)
        session.commit()
        foreign_course_id = foreign_course.id
        foreign_semester_id = foreign_semester.id

    assert client.get(f"/api/v1/candidates?course_id={foreign_course_id}").status_code == 404
    assert client.get(f"/api/v1/candidates?semester_id={foreign_semester_id}").status_code == 404
    assert (
        client.get(f"/api/v1/candidates/summary?course_id={foreign_course_id}").status_code == 404
    )
    assert client.get("/api/v1/candidates").json() != []


def test_list_entries_link_to_source_version_for_uncovered_ranges(source):
    """列表项带来源资料与版本号，可据此跳到原文片段查看未覆盖范围。"""
    client, material, chunks = source
    save(source, [task_draft(chunks[0]["id"])])
    item = client.get("/api/v1/candidates").json()[0]
    assert item["source_material_id"] == material["id"]
    assert item["version_no"] == 1

    version_chunks = client.get(f"/api/v1/materials/{item['source_material_id']}/versions/1/chunks")
    assert version_chunks.status_code == 200
    assert {chunk["id"] for chunk in version_chunks.json()} >= {c["id"] for c in chunks}
