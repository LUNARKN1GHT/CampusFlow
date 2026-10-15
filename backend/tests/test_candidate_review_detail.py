"""R008：候选核对详情与核对状态转换接口，为前端核对界面提供后端支撑。"""

from datetime import date

import pytest

from campusflow.application.candidates import (
    review_detail,
    revise_candidate_field,
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
from campusflow.domain.errors import NotFoundError
from campusflow.domain.states import ReviewStatus
from campusflow.infrastructure.db.models import Course, Semester, SourceChunk, Workspace
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.storage.local import LocalFileStorage

# 粘贴文本按空行切成两个段落片段：第 1 段含任务与截止时间，第 2 段是机房规则。
CONTENT = "下周五 18:00 前提交实验报告，附件在课程平台下载。\n\n机房开放时间见附件表格。"
REASONS = (ReviewReason("missing_time", "需要核对提交时刻", "due_time"),)


def task_draft(chunk_id, *, scope=None, title="实验报告"):
    """一个带原文引用、缺失字段与耗时建议的任务候选草稿。"""
    titled = CandidateField(
        "title",
        title,
        FieldOrigin.DOCUMENT,
        (FieldEvidence(chunk_id, "实验报告"),) if title else (),
        missing_reason=None if title else "截图未包含任务名",
    )
    fields = (
        titled,
        CandidateField(
            "due_date", "下周五", FieldOrigin.DOCUMENT, (FieldEvidence(chunk_id, "下周五"),)
        ),
        CandidateField("due_time", None, FieldOrigin.DOCUMENT, missing_reason="原文没有精确时刻"),
        CandidateField("estimated_minutes", 90, FieldOrigin.SUGGESTION, explanation="初步耗时建议"),
    )
    return CandidateDraft(
        CandidateKind.TASK, fields, scope=scope or CandidateScope(), review_reasons=REASONS
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


def save(source, draft=None):
    client, material, chunks = source
    with client.app.state.session_factory() as session:
        return save_candidates(
            build_repositories(session),
            material["workspace_id"],
            material["id"],
            1,
            [draft or task_draft(chunks[0]["id"])],
        )[0]


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


def detail(client, candidate_id):
    response = client.get(f"/api/v1/candidates/{candidate_id}")
    assert response.status_code == 200, response.text
    return response.json()


def patch_status(client, candidate_id, payload):
    return client.patch(f"/api/v1/candidates/{candidate_id}/review-status", json=payload)


def by_name(body):
    return {field["name"]: field for field in body["fields"]}


def test_detail_returns_scope_source_and_per_field_origins(source):
    """详情带适用范围、来源资料与版本，逐字段给原始值、生效值与可验证引用定位。"""
    client, material, chunks = source
    semester_id, course_id = create_course(source)
    saved = save(
        source, task_draft(chunks[0]["id"], scope=CandidateScope(semester_id, course_id, "2 班"))
    )

    body = detail(client, saved.id)
    assert body["id"] == saved.id
    assert body["kind"] == "task"
    assert body["review_status"] == "pending"
    assert body["created_at"]
    assert body["scope"] == {
        "course_id": course_id,
        "semester_id": semester_id,
        "class_name": "2 班",
        "course_name": "数据库",
        "semester_name": "秋季",
    }
    assert body["source"] == {
        "material_id": material["id"],
        "material_title": "课程通知",
        "version_id": chunks[0]["version_id"],
        "version_no": 1,
    }
    assert body["review_reasons"] == [
        {"code": "missing_time", "message": "需要核对提交时刻", "field": "due_time"}
    ]
    assert body["missing_required_fields"] == []
    assert body["allowed_transitions"] == ["confirmed", "ignored", "superseded"]
    assert body["revisions"] == [] and body["review_changes"] == []

    fields = by_name(body)
    title = fields["title"]
    assert title["value"] == "实验报告"
    assert title["origin"] == "document" and title["effective_origin"] == "document"
    assert title["verifiable"] is True
    assert title["evidence"] == [
        {
            "chunk_id": chunks[0]["id"],
            "quote": "实验报告",
            "locator_type": chunks[0]["locator_type"],
            "locator_value": chunks[0]["locator_value"],
            "verifiable": True,
        }
    ]
    # 缺值字段只保留缺失原因，不伪造取值与引用
    due_time = fields["due_time"]
    assert due_time["value"] is None and due_time["effective_value"] is None
    assert due_time["missing_reason"] == "原文没有精确时刻"
    assert due_time["evidence"] == []
    # 规划建议本来就没有原文引用，按 R007 规则不算不可验证
    assert fields["estimated_minutes"]["origin"] == "suggestion"
    assert fields["estimated_minutes"]["explanation"] == "初步耗时建议"
    assert fields["estimated_minutes"]["verifiable"] is True

    # 详情路由不能遮住 /candidates/summary
    assert client.get("/api/v1/candidates/summary").status_code == 200


def test_field_revision_shows_effective_value_and_keeps_original_extraction(source):
    """R002 修正后详情并列给出原始提取值与生效值，修正留痕出现在 revisions。"""
    client, _, chunks = source
    saved = save(source)
    revise(
        source,
        saved.id,
        "due_date",
        value="2026-10-20",
        origin=FieldOrigin.USER,
        note="按群通知补全日期",
    )

    due_date = by_name(detail(client, saved.id))["due_date"]
    assert due_date["value"] == "下周五"
    assert due_date["origin"] == "document"
    assert due_date["effective_value"] == "2026-10-20"
    assert due_date["effective_origin"] == "user"
    assert due_date["explanation"] == "按群通知补全日期"
    # 原始提取的证据不改写，仍可定位到原文
    assert due_date["evidence"] == [
        {
            "chunk_id": chunks[0]["id"],
            "quote": "下周五",
            "locator_type": chunks[0]["locator_type"],
            "locator_value": chunks[0]["locator_value"],
            "verifiable": True,
        }
    ]

    body = detail(client, saved.id)
    assert len(body["revisions"]) == 1
    revision = body["revisions"][0]
    assert revision["field_name"] == "due_date"
    assert revision["previous_value"] == "下周五"
    assert revision["new_value"] == "2026-10-20"
    assert revision["previous_origin"] == "document"
    assert revision["new_origin"] == "user"
    assert revision["operator"] == "student"
    assert revision["note"] == "按群通知补全日期"
    assert revision["created_at"]
    assert revision["id"] > 0


def test_citation_is_not_verifiable_after_chunk_text_is_replaced(source):
    """保存时摘录与原文一致，之后片段文本被替换：引用不再可验证，但定位信息仍可展示。"""
    client, _, chunks = source
    saved = save(source)
    assert by_name(detail(client, saved.id))["title"]["verifiable"] is True
    with client.app.state.session_factory() as session:
        chunk = session.get(SourceChunk, chunks[0]["id"])
        chunk.text = "内容已按新版本重新解析，原摘录不再逐字出现。"
        session.commit()

    title = by_name(detail(client, saved.id))["title"]
    assert title["value"] == "实验报告"
    assert title["verifiable"] is False
    assert title["evidence"][0]["verifiable"] is False
    assert title["evidence"][0]["quote"] == "实验报告"
    assert title["evidence"][0]["locator_value"] == chunks[0]["locator_value"]


def test_missing_chunk_clears_citation_but_keeps_extracted_value(source):
    """来源片段被删除后连带清掉证据行：字段值仍如实显示，但不再声称有可验证引用。"""
    client, _, chunks = source
    saved = save(source)
    with client.app.state.session_factory() as session:
        session.delete(session.get(SourceChunk, chunks[0]["id"]))
        session.commit()

    body = detail(client, saved.id)
    fields = by_name(body)
    assert fields["title"]["value"] == "实验报告"
    assert fields["title"]["evidence"] == []
    assert fields["title"]["verifiable"] is False
    assert fields["due_date"]["verifiable"] is False
    # 没有原文取值要求的字段不受影响
    assert fields["estimated_minutes"]["verifiable"] is True


def test_confirm_with_missing_required_field_needs_explicit_acknowledgement(source):
    """缺必需字段时确认返回 400 与中文原因；显式确认并写原因后才转换并留痕。"""
    client, _, chunks = source
    saved = save(source, task_draft(chunks[0]["id"], title=None))

    body = detail(client, saved.id)
    assert body["missing_required_fields"] == ["title"]
    assert body["fields"][0]["value"] is None

    rejected = patch_status(client, saved.id, {"status": "confirmed"})
    assert rejected.status_code == 400, rejected.text
    assert "缺少必须字段" in rejected.json()["detail"]
    assert "title" in rejected.json()["detail"]
    assert detail(client, saved.id)["review_status"] == "pending"

    acknowledged = patch_status(
        client,
        saved.id,
        {"status": "confirmed", "acknowledge_missing": True, "reason": "按群内口头通知确认"},
    )
    assert acknowledged.status_code == 200, acknowledged.text
    body = acknowledged.json()
    assert body["review_status"] == "confirmed"
    assert body["missing_required_fields"] == ["title"]
    assert body["allowed_transitions"] == ["superseded", "pending"]
    change = body["review_changes"][-1]
    assert change["from_status"] == "pending"
    assert change["to_status"] == "confirmed"
    assert change["operator"] == "student"
    assert change["reason"] == "按群内口头通知确认"
    assert change["changed_at"]

    missing_reason = patch_status(
        client, saved.id, {"status": "pending", "acknowledge_missing": True}
    )
    assert missing_reason.status_code == 200


def test_ignore_transition_updates_status_and_allowed_transitions(source):
    """忽略不需要字段确认；已忽略只能退回待核对，不能直接确认。"""
    client, _, chunks = source
    saved = save(source, task_draft(chunks[0]["id"]))

    ignored = patch_status(client, saved.id, {"status": "ignored", "reason": "与本学期无关"})
    assert ignored.status_code == 200, ignored.text
    body = ignored.json()
    # 未关联课程与学期的候选不展示名称，不拿 ID 冒充适用范围
    assert body["scope"] == {
        "course_id": None,
        "semester_id": None,
        "class_name": None,
        "course_name": None,
        "semester_name": None,
    }
    assert body["review_status"] == "ignored"
    assert body["allowed_transitions"] == ["pending"]
    assert len(body["review_changes"]) == 1
    change = body["review_changes"][0]
    assert change["from_status"] == "pending"
    assert change["to_status"] == "ignored"
    assert change["operator"] == "student"
    assert change["reason"] == "与本学期无关"
    assert change["changed_at"]

    rejected = patch_status(client, saved.id, {"status": "confirmed"})
    assert rejected.status_code == 400
    assert "不允许从已忽略转换为已确认" in rejected.json()["detail"]

    reopened = patch_status(client, saved.id, {"status": "pending"})
    assert reopened.status_code == 200
    assert reopened.json()["review_status"] == "pending"
    assert [record["to_status"] for record in reopened.json()["review_changes"]] == [
        "ignored",
        "pending",
    ]


def test_review_status_patch_and_task_progress_are_not_mixed(source):
    """转换接口只接受 ReviewStatus：任务执行进度值一律 422 拒绝。"""
    client, _, _ = source
    saved = save(source)
    for progress in ("not_started", "in_progress", "blocked", "completed", "cancelled"):
        assert patch_status(client, saved.id, {"status": progress}).status_code == 422
    assert patch_status(client, saved.id, {"status": "ignored"}).status_code == 200
    assert detail(client, saved.id)["review_status"] == "ignored"


def test_unknown_or_foreign_workspace_candidate_is_not_found(source):
    client, material, _ = source
    saved = save(source)
    assert client.get("/api/v1/candidates/999999").status_code == 404
    assert patch_status(client, 999999, {"status": "confirmed"}).status_code == 404

    with client.app.state.session_factory() as session:
        other = Workspace(name="他人空间")
        session.add(other)
        session.commit()
        other_id = other.id

    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        with pytest.raises(NotFoundError):
            review_detail(repos, other_id, saved.id)
        with pytest.raises(NotFoundError):
            set_candidate_review_status(
                repos, other_id, saved.id, ReviewStatus.CONFIRMED, operator="student"
            )
    assert detail(client, saved.id)["source"]["material_id"] == material["id"]
