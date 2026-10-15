"""R009：逐字段修正与确认后生成正式事项的 HTTP 入口。"""

from datetime import date

import pytest

from campusflow.application.candidates import save_candidates
from campusflow.application.conversion import convert_candidate
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
from campusflow.infrastructure.db.models import Course, Semester, Workspace
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.storage.local import LocalFileStorage

# 粘贴文本按空行切成四个片段：任务、日程、规则各一段，末段是缺标题的残页。
CONTENT = (
    "下周五 18:00 前提交实验报告。\n\n"
    "第 5 周周三 19:00 到 20:30 在 A202 上实验课。\n\n"
    "迟到三次取消考试资格。\n\n"
    "截图不完整，只保留了“下周五之前交”一句。"
)
TASK_PARAGRAPH = 1
EVENT_PARAGRAPH = 2
RULE_PARAGRAPH = 3


@pytest.fixture
def source(client, tmp_path, monkeypatch):
    monkeypatch.setattr(client.app.state, "file_storage", LocalFileStorage(tmp_path))
    response = client.post("/api/v1/materials/text", json={"title": "课程通知", "content": CONTENT})
    assert response.status_code == 201
    result = response.json()
    workspace_id = client.get("/api/v1/settings").json()["workspace_id"]
    result["material"]["workspace_id"] = workspace_id
    return client, result["material"], result["chunks"]


def chunk_id(source, paragraph):
    return next(item["id"] for item in source[2] if item["paragraph"] == paragraph)


def quoted(source, paragraph, name, value, quote):
    return CandidateField(
        name, value, FieldOrigin.DOCUMENT, (FieldEvidence(chunk_id(source, paragraph), quote),)
    )


def save(source, draft):
    client, material, _ = source
    with client.app.state.session_factory() as session:
        return save_candidates(
            build_repositories(session),
            material["workspace_id"],
            material["id"],
            1,
            [draft],
        )[0]


def create_course(source, *, name="数据库"):
    """在当前空间直接落库一个学期与课程，供要求关联范围的规则转换用例使用。"""
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


def task_draft(source, *, scope=None):
    return CandidateDraft(
        CandidateKind.TASK,
        (
            quoted(source, TASK_PARAGRAPH, "title", "提交实验报告", "提交实验报告"),
            quoted(source, TASK_PARAGRAPH, "due_text", "下周五 18:00", "下周五 18:00"),
            CandidateField(
                "estimated_minutes", 90, FieldOrigin.SUGGESTION, explanation="初步耗时建议"
            ),
        ),
        scope=scope or CandidateScope(),
        review_reasons=(ReviewReason("relative_date", "相对日期需要按校历换算", "due_text"),),
    )


def event_draft(source, *, start="2026-10-21 19:00", end="2026-10-21 20:30"):
    """日程候选：start 传 None 时只留相对写法，用来验证起止不完整时的转换拒绝。"""
    fields = [
        quoted(source, EVENT_PARAGRAPH, "title", "实验课", "实验课"),
        quoted(
            source,
            EVENT_PARAGRAPH,
            "activity_text",
            "第 5 周周三 19:00 到 20:30 在 A202 上实验课",
            "在 A202 上实验课",
        ),
    ]
    if start is not None:
        fields.append(quoted(source, EVENT_PARAGRAPH, "start_text", start, "19:00 到"))
        fields.append(quoted(source, EVENT_PARAGRAPH, "end_text", end, "到 20:30"))
    else:
        fields.append(quoted(source, EVENT_PARAGRAPH, "start_text", "第 5 周周三", "第 5 周周三"))
    return CandidateDraft(CandidateKind.EVENT, tuple(fields))


def rule_draft(source, *, scope, rule_text="迟到三次取消考试资格"):
    fields = [quoted(source, RULE_PARAGRAPH, "title", "实验课纪律", "迟到三次")]
    if rule_text is not None:
        fields.append(
            quoted(source, RULE_PARAGRAPH, "rule_text", rule_text, "迟到三次取消考试资格")
        )
    return CandidateDraft(CandidateKind.RULE, tuple(fields), scope=scope)


def confirm(source, candidate_id, *, acknowledge=False, reason=None):
    client, _, _ = source
    return client.patch(
        f"/api/v1/candidates/{candidate_id}/review-status",
        json={"status": "confirmed", "acknowledge_missing": acknowledge, "reason": reason},
    )


def revise(client, candidate_id, field_name, payload):
    return client.patch(f"/api/v1/candidates/{candidate_id}/fields/{field_name}", json=payload)


def convert(client, candidate_id):
    return client.post(f"/api/v1/candidates/{candidate_id}/convert")


def detail(client, candidate_id):
    response = client.get(f"/api/v1/candidates/{candidate_id}")
    assert response.status_code == 200, response.text
    return response.json()


def by_name(body):
    return {field["name"]: field for field in body["fields"]}


def test_field_revision_through_http_keeps_original_and_records_operator(source):
    """修正经 HTTP 入口落库：生效值更新，原始提取与证据保留，留痕记录操作者与说明。"""
    client, _, _ = source
    saved = save(source, task_draft(source))

    response = revise(
        client,
        saved.id,
        "due_text",
        {"value": "2026-10-20 18:00", "origin": "user", "note": "按群通知补全日期"},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    field = by_name(body)["due_text"]
    assert field["value"] == "下周五 18:00" and field["origin"] == "document"
    assert field["effective_value"] == "2026-10-20 18:00"
    assert field["effective_origin"] == "user"
    assert field["explanation"] == "按群通知补全日期"
    # 原始证据不改写，仍能定位到原文
    assert field["evidence"][0]["quote"] == "下周五 18:00"
    assert field["evidence"][0]["verifiable"] is True

    assert len(body["revisions"]) == 1
    revision = body["revisions"][0]
    assert revision["field_name"] == "due_text"
    assert revision["previous_value"] == "下周五 18:00"
    assert revision["new_value"] == "2026-10-20 18:00"
    assert revision["previous_origin"] == "document"
    assert revision["new_origin"] == "user"
    assert revision["operator"] == "student"
    assert revision["note"] == "按群通知补全日期"


def test_revision_cannot_claim_document_origin_and_needs_note_when_cleared(source):
    """用户填写的值不能声明为原文规定；清空取值必须给出缺失原因。"""
    client, _, _ = source
    saved = save(source, task_draft(source))

    claimed = revise(client, saved.id, "title", {"value": "提交实验报告", "origin": "document"})
    assert claimed.status_code == 400, claimed.text
    assert "证据" in claimed.json()["detail"]

    cleared = revise(client, saved.id, "title", {"value": None, "origin": "user"})
    assert cleared.status_code == 400, cleared.text
    assert "缺失原因" in cleared.json()["detail"]

    with_note = revise(
        client,
        saved.id,
        "title",
        {"value": None, "origin": "user", "note": "截图中任务名不完整，暂留空"},
    )
    assert with_note.status_code == 200, with_note.text
    title = by_name(with_note.json())["title"]
    assert title["effective_value"] is None
    assert title["missing_reason"] == "截图中任务名不完整，暂留空"
    # 清空必须字段后，确认又被守卫拦下
    blocked = confirm(source, saved.id)
    assert blocked.status_code == 400
    assert "缺少必须字段" in blocked.json()["detail"]


def test_unknown_field_or_progress_value_origin_is_rejected(source):
    client, _, _ = source
    saved = save(source, task_draft(source))
    missing = revise(client, saved.id, "not_a_field", {"value": "x", "origin": "user"})
    assert missing.status_code == 400
    assert "没有这个字段" in missing.json()["detail"]

    invalid_origin = revise(client, saved.id, "title", {"value": "x", "origin": "not_started"})
    assert invalid_origin.status_code == 422


def test_corrected_field_value_feeds_conversion(source):
    """R009 修正与 R004 转换衔接：补全缺失的截止时刻后才能生成带时刻的任务。"""
    client, _, _ = source
    semester_id, course_id = create_course(source)
    saved = save(source, task_draft(source, scope=CandidateScope(semester_id, course_id, "2 班")))
    before = by_name(detail(client, saved.id))["due_text"]
    assert before["value"] == "下周五 18:00" and before["effective_value"] == "下周五 18:00"

    assert (
        revise(
            client,
            saved.id,
            "due_text",
            {"value": "2026-10-20 18:00", "origin": "user", "note": "按群通知补全日期与时刻"},
        ).status_code
        == 200
    )
    assert confirm(source, saved.id).status_code == 200

    created = convert(client, saved.id)
    assert created.status_code == 200, created.text
    task = client.get(f"/api/v1/tasks/{created.json()['object_id']}").json()
    assert task["due_date"] == "2026-10-20"
    assert task["due_time"] == "18:00:00"


def test_convert_task_creates_once_and_reports_existing_on_retry(source):
    """确认后生成正式任务：首次 created=true，重复调用返回同一任务且 created=false。"""
    client, _, _ = source
    semester_id, course_id = create_course(source)
    saved = save(source, task_draft(source, scope=CandidateScope(semester_id, course_id, "2 班")))
    assert confirm(source, saved.id).status_code == 200

    first = convert(client, saved.id)
    assert first.status_code == 200, first.text
    created = first.json()
    assert created["kind"] == "task"
    assert created["created"] is True
    assert created["title"] == "提交实验报告"

    task = client.get(f"/api/v1/tasks/{created['object_id']}").json()
    assert task["title"] == "提交实验报告"
    assert task["course_id"] == course_id

    retry = convert(client, saved.id)
    assert retry.status_code == 200, retry.text
    assert retry.json()["created"] is False
    assert retry.json()["object_id"] == created["object_id"]
    assert len(client.get("/api/v1/tasks").json()) == 1

    # 详情直接带出已生成事项，界面刷新后仍能给打开入口
    body = detail(client, saved.id)
    assert body["converted"] == {
        "kind": "task",
        "object_id": created["object_id"],
        "title": "提交实验报告",
    }
    assert body["review_status"] == "confirmed"


def test_convert_event_and_rule_by_kind(source):
    client, _, _ = source
    semester_id, course_id = create_course(source)
    scope = CandidateScope(semester_id, course_id, None)
    event = save(source, event_draft(source))
    rule = save(source, rule_draft(source, scope=scope))
    assert confirm(source, event.id).status_code == 200
    assert confirm(source, rule.id).status_code == 200

    converted_event = convert(client, event.id)
    assert converted_event.status_code == 200, converted_event.text
    assert converted_event.json()["kind"] == "event"
    assert converted_event.json()["created"] is True
    events = client.get("/api/v1/fixed-events").json()
    assert [item["id"] for item in events] == [converted_event.json()["object_id"]]

    converted_rule = convert(client, rule.id)
    assert converted_rule.status_code == 200, converted_rule.text
    assert converted_rule.json()["kind"] == "rule"
    assert converted_rule.json()["created"] is True
    rules = client.get("/api/v1/rules").json()
    assert [item["id"] for item in rules] == [converted_rule.json()["object_id"]]

    # 日程与规则同样幂等
    assert convert(client, event.id).json()["created"] is False
    assert convert(client, rule.id).json()["created"] is False
    assert len(client.get("/api/v1/fixed-events").json()) == 1
    assert len(client.get("/api/v1/rules").json()) == 1


def test_convert_requires_confirmed_candidate_and_complete_fields(source):
    client, _, _ = source
    semester_id, course_id = create_course(source)

    pending = save(source, task_draft(source))
    rejected = convert(client, pending.id)
    assert rejected.status_code == 400, rejected.text
    assert "确认" in rejected.json()["detail"]

    # 日程起止只有相对写法时不能解析出日期与时刻，转换必须拒绝而不是按常见时长补造
    relative_event = save(source, event_draft(source, start=None))
    assert confirm(source, relative_event.id).status_code == 200
    rejected_event = convert(client, relative_event.id)
    assert rejected_event.status_code == 400, rejected_event.text
    assert "开始" in rejected_event.json()["detail"] or "时间" in rejected_event.json()["detail"]
    assert client.get("/api/v1/fixed-events").json() == []

    # 规则候选缺正文时确认需显式确认，转换仍按缺字段拒绝
    broken_rule = save(
        source, rule_draft(source, scope=CandidateScope(semester_id, course_id), rule_text=None)
    )
    assert (
        confirm(source, broken_rule.id, acknowledge=True, reason="按群内通知确认").status_code
        == 200
    )
    rejected_rule = convert(client, broken_rule.id)
    assert rejected_rule.status_code == 400, rejected_rule.text
    assert "规则正文" in rejected_rule.json()["detail"]
    assert client.get("/api/v1/rules").json() == []


def test_revision_and_conversion_are_scoped_to_current_workspace(source):
    client, material, _ = source
    saved = save(source, task_draft(source))
    assert revise(client, 999999, "title", {"value": "x", "origin": "user"}).status_code == 404
    assert convert(client, 999999).status_code == 404

    with client.app.state.session_factory() as session:
        other = Workspace(name="他人空间")
        session.add(other)
        session.commit()
        other_id = other.id

    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        with pytest.raises(NotFoundError):
            convert_candidate(repos, other_id, saved.id)

    assert confirm(source, saved.id).status_code == 200
    assert convert(client, saved.id).status_code == 200
    assert detail(client, saved.id)["source"]["material_id"] == material["id"]
