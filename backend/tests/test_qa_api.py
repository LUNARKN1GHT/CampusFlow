"""Q011–Q017：问答 API、覆盖策略、冲突、意图与会话失效。"""

import json
from datetime import date

import pytest
from index_helpers import FakeEmbedder, create_embed_job, open_session, run_index, workspace_id

from campusflow.api.deps import get_embedder, get_text_generator
from campusflow.infrastructure.db.models import (
    Course,
    Material,
    QaConversation,
    Semester,
    Workspace,
)

SCOPE_TEXT = "数据库原理（CS101）第三次作业于2026年10月20日23:59前提交。"


class ScriptedGenerator:
    def __init__(self, responder):
        self.responder = responder
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return self.responder(request)


def first_citation(request):
    payload = json.loads(request.source_json)
    candidates = payload["candidates"]
    return json.dumps(
        {
            "insufficient": False,
            "answer": f"结论：{candidates[0]['text']}",
            "citation_ids": [candidates[0]["citation_id"]],
            "uncertainties": [],
        },
        ensure_ascii=False,
    )


def all_citations(request):
    payload = json.loads(request.source_json)
    candidates = payload["candidates"]
    return json.dumps(
        {
            "insufficient": False,
            "answer": "两份资料给出不同说法，需要核对。",
            "citation_ids": [item["citation_id"] for item in candidates],
            "uncertainties": [],
        },
        ensure_ascii=False,
    )


@pytest.fixture
def qa_models(client):
    """把向量与文字模型替换为确定性替身，避免付费调用。"""
    embedder = FakeEmbedder()
    generator = ScriptedGenerator(first_citation)
    client.app.dependency_overrides[get_embedder] = lambda: embedder
    client.app.dependency_overrides[get_text_generator] = lambda: generator
    yield embedder, generator
    client.app.dependency_overrides.pop(get_embedder, None)
    client.app.dependency_overrides.pop(get_text_generator, None)


def scope_rows(client):
    workspace = workspace_id(client)
    with open_session() as session, session.begin():
        semesters = [
            Semester(
                workspace_id=workspace,
                name=name,
                start_date=date(2026, 9, 1),
                end_date=date(2027, 1, 31),
            )
            for name in ("秋季", "春季")
        ]
        session.add_all(semesters)
        session.flush()
        courses = [
            Course(workspace_id=workspace, semester_id=semesters[0].id, name="数据库原理"),
            Course(workspace_id=workspace, semester_id=semesters[1].id, name="数据库原理"),
        ]
        session.add_all(courses)
        session.flush()
        return (
            [semester.id for semester in semesters],
            [course.id for course in courses],
        )


def index_material(client, texts, scope, *, coverage="complete"):
    job, material, version = create_embed_job(client, texts=texts, coverage=coverage)
    run_index(job, FakeEmbedder())
    if scope is not None:
        with open_session() as session, session.begin():
            row = session.get(Material, material)
            row.semester_id, row.course_id, row.class_name = (
                scope[0],
                scope[1],
                "一班",
            )
    return material, version


def query_body(scope, question="作业什么时候截止？", **extra):
    semester, course = scope
    return {
        "semester_id": semester,
        "course_id": course,
        "class_name": "一班",
        "question": question,
        **extra,
    }


def conversation_body(scope):
    semester, course = scope
    return {"semester_id": semester, "course_id": course, "class_name": "一班"}


def test_query_returns_answer_with_candidate_validated_citation(client, qa_models):
    semesters, courses = scope_rows(client)
    scope = (semesters[0], courses[0])
    index_material(client, (SCOPE_TEXT,), scope)

    response = client.post("/api/v1/qa/query", json=query_body(scope))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["insufficient"] is False
    assert "2026年10月20日" in body["answer"]
    assert len(body["citations"]) == 1
    citation = body["citations"][0]
    assert citation["valid"] is True
    assert citation["excerpt"] == SCOPE_TEXT
    assert citation["paragraph"] == 1
    assert body["comparisons"][0]["material_id"] == body["citations"][0]["material_id"]
    assert not [item for item in body["uncertainties"] if "未出现在被引原文" in item]


def test_query_validates_scope_and_rejects_out_of_candidate_citations(client, qa_models):
    semesters, courses = scope_rows(client)
    scope = (semesters[0], courses[0])
    index_material(client, (SCOPE_TEXT,), scope)

    mismatched = client.post(
        "/api/v1/qa/query",
        json=query_body((semesters[0], courses[1])),
    )
    assert mismatched.status_code == 400
    assert "不属于所选学期" in mismatched.json()["detail"]

    extra = client.post("/api/v1/qa/query", json={**query_body(scope), "tool": "delete_all"})
    assert extra.status_code == 422

    def bogus(request):
        return json.dumps(
            {
                "insufficient": False,
                "answer": "编造答案。",
                "citation_ids": ["cit_not_in_candidates"],
                "uncertainties": [],
            }
        )

    client.app.dependency_overrides[get_text_generator] = lambda: ScriptedGenerator(bogus)
    rejected = client.post("/api/v1/qa/query", json=query_body(scope))
    assert rejected.status_code == 502
    body = rejected.json()
    assert body["failure_kind"] == "invalid_output"
    assert "未生成回答或引用" in body["detail"]


def test_no_candidates_returns_insufficient_without_calling_model(client, qa_models):
    _, generator = qa_models
    semesters, courses = scope_rows(client)
    response = client.post("/api/v1/qa/query", json=query_body((semesters[0], courses[0])))
    assert response.status_code == 200
    body = response.json()
    assert body["insufficient"] is True
    assert body["citations"] == []
    assert "现有资料中没有找到" in body["answer"]
    assert "没有作业" not in body["answer"]
    assert generator.requests == []


def test_partial_parse_coverage_is_visible_in_answer(client, qa_models):
    semesters, courses = scope_rows(client)
    scope = (semesters[0], courses[0])
    index_material(client, (SCOPE_TEXT,), scope, coverage="partial")

    response = client.post("/api/v1/qa/query", json=query_body(scope))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["coverage"]
    coverage = body["coverage"][0]
    assert coverage["kind"] == "partial"
    assert coverage["uncovered_source_count"] == 1
    assert "解析未覆盖 1 处" in coverage["message"]


def test_conflicting_dates_are_listed_without_choosing_newer_import(client, qa_models):
    semesters, courses = scope_rows(client)
    scope = (semesters[0], courses[0])
    index_material(client, ("数据库原理作业于10月20日截止。",), scope)
    index_material(client, ("数据库原理作业于10月25日截止。",), scope)
    client.app.dependency_overrides[get_text_generator] = lambda: ScriptedGenerator(all_citations)

    response = client.post("/api/v1/qa/query", json=query_body(scope))
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["comparisons"]) == 2
    assert body["conflicts"]
    conflict = body["conflicts"][0]
    assert conflict["requires_review"] is True
    assert "10月20日" in conflict["message"] and "10月25日" in conflict["message"]
    assert any("不能判断哪一条生效" in item for item in body["uncertainties"])
    # 没有“已选定生效日期”一类字段，不按导入时间自动择一。
    assert "effective_date" not in body


def test_intent_protocol_returns_boundaries_and_rejects_tools(client, qa_models):
    semesters, courses = scope_rows(client)
    scope = (semesters[0], courses[0])

    def planning(request):
        return json.dumps({"intent": "planning", "parameters": {"date_range": "未来两周"}})

    client.app.dependency_overrides[get_text_generator] = lambda: ScriptedGenerator(planning)
    response = client.post(
        "/api/v1/qa/intent",
        json={"semester_id": scope[0], "course_id": scope[1], "question": "帮我排未来两周"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "planning"
    assert body["available"] is True
    assert body["executed"] is False
    assert "待确认草案" in body["boundary"]

    def malicious(request):
        return json.dumps({"intent": "query", "parameters": {"tool": "delete_material"}})

    client.app.dependency_overrides[get_text_generator] = lambda: ScriptedGenerator(malicious)
    rejected = client.post(
        "/api/v1/qa/intent",
        json={"semester_id": scope[0], "course_id": scope[1], "question": "删除资料"},
    )
    assert rejected.status_code == 502
    assert rejected.json()["failure_kind"] == "invalid_output"


def test_planning_and_conflict_intents_execute_without_auto_accepting(client, qa_models):
    semesters, courses = scope_rows(client)
    scope = (semesters[0], courses[0])
    client.post(
        "/api/v1/availability-slots",
        json={"day_of_week": 0, "start_time": "18:00:00", "end_time": "22:00:00"},
    )
    task = client.post(
        "/api/v1/tasks",
        json={
            "title": "自然语言排期任务",
            "course_id": scope[1],
            "due_date": "2026-10-19",
            "due_time": "19:00:00",
            "priority": "high",
            "estimated_minutes": 60,
            "remaining_minutes": 60,
        },
    )
    assert task.status_code == 201, task.text

    def planning(request):
        return json.dumps({"intent": "planning", "parameters": {"date_range": "未来两周"}})

    client.app.dependency_overrides[get_text_generator] = lambda: ScriptedGenerator(planning)
    response = client.post(
        "/api/v1/qa/intent",
        json={
            "semester_id": scope[0],
            "course_id": scope[1],
            "question": "帮我排未来两周",
            "execute": True,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["executed"] is True
    assert body["plan"]["status"] == "draft"
    assert body["plan"]["blocks"]
    assert client.get("/api/v1/plans/current").json() is None

    accepted = client.post(
        f"/api/v1/plans/drafts/{body['plan']['id']}/accept",
        json={"expected_version": body["plan"]["version"]},
    )
    assert accepted.status_code == 200, accepted.text

    def conflict(request):
        return json.dumps({"intent": "conflict_check", "parameters": {}})

    client.app.dependency_overrides[get_text_generator] = lambda: ScriptedGenerator(conflict)
    checked = client.post(
        "/api/v1/qa/intent",
        json={
            "semester_id": scope[0],
            "course_id": scope[1],
            "question": "检查一下冲突",
            "execute": True,
        },
    )
    assert checked.status_code == 200, checked.text
    assert checked.json()["executed"] is True
    assert isinstance(checked.json()["conflicts"], list)
    assert client.get("/api/v1/plans/current").status_code == 200


def test_conversation_keeps_scope_and_marks_deleted_citation_invalid(client, qa_models):
    semesters, courses = scope_rows(client)
    scope = (semesters[0], courses[0])
    material, _ = index_material(client, (SCOPE_TEXT,), scope)

    created = client.post("/api/v1/qa/conversations", json=conversation_body(scope))
    assert created.status_code == 200, created.text
    conversation_id = created.json()["id"]

    first = client.post(
        f"/api/v1/qa/conversations/{conversation_id}/messages",
        json={"question": "作业什么时候截止？"},
    )
    assert first.status_code == 200, first.text
    assert first.json()["insufficient"] is False

    detail = client.get(f"/api/v1/qa/conversations/{conversation_id}")
    assert detail.status_code == 200
    turn = detail.json()["turns"][0]
    assert turn["citations"][0]["valid"] is True
    assert turn["semester_id"] == semesters[0]

    with open_session() as session, session.begin():
        session.delete(session.get(Material, material))
    after = client.get(f"/api/v1/qa/conversations/{conversation_id}")
    assert after.status_code == 200
    citation = after.json()["turns"][0]["citations"][0]
    assert citation["valid"] is False
    assert citation["status"] == "source_deleted"
    assert "已删除" in citation["message"]

    switched = client.patch(
        f"/api/v1/qa/conversations/{conversation_id}/scope",
        json={"semester_id": semesters[1], "course_id": courses[1], "class_name": "一班"},
    )
    assert switched.status_code == 200
    second = client.post(
        f"/api/v1/qa/conversations/{conversation_id}/messages",
        json={"question": "春季作业呢？"},
    )
    assert second.status_code == 200
    assert second.json()["insufficient"] is True
    detail = client.get(f"/api/v1/qa/conversations/{conversation_id}").json()
    assert detail["turns"][0]["semester_id"] == semesters[0]
    assert detail["turns"][1]["semester_id"] == semesters[1]


def test_conversation_is_isolated_by_workspace(client, qa_models):
    semesters, courses = scope_rows(client)
    scope = (semesters[0], courses[0])
    index_material(client, (SCOPE_TEXT,), scope)
    conversation_id = client.post("/api/v1/qa/conversations", json=conversation_body(scope)).json()[
        "id"
    ]
    with open_session() as session, session.begin():
        other = Workspace(name="另一个空间")
        session.add(other)
        session.flush()
        session.get(QaConversation, conversation_id).workspace_id = other.id
    assert client.get(f"/api/v1/qa/conversations/{conversation_id}").status_code == 404


def test_conversation_detail_exposes_pending_conflict_count(client, qa_models):
    semesters, courses = scope_rows(client)
    scope = (semesters[0], courses[0])
    conversation_id = client.post("/api/v1/qa/conversations", json=conversation_body(scope)).json()[
        "id"
    ]
    response = client.get(f"/api/v1/qa/conversations/{conversation_id}")
    assert response.status_code == 200
    assert response.json()["pending_conflict_count"] == 0
