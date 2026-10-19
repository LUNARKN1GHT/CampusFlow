"""Q018：DDL 回答与引用语义一致验收（具体日期、适用对象与支持原文）。"""

import json
from datetime import date

from index_helpers import FakeEmbedder, create_embed_job, open_session, run_index, workspace_id

from campusflow.api.deps import get_embedder, get_text_generator
from campusflow.infrastructure.db.models import Course, Material, Semester


class ScriptedGenerator:
    def __init__(self, answer_builder):
        self.answer_builder = answer_builder

    def generate(self, request):
        candidates = json.loads(request.source_json)["candidates"]
        return json.dumps(self.answer_builder(candidates), ensure_ascii=False)


def setup_material(client, text, *, class_name="一班"):
    workspace = workspace_id(client)
    with open_session() as session, session.begin():
        semester = Semester(
            workspace_id=workspace,
            name="秋季",
            start_date=date(2026, 9, 1),
            end_date=date(2027, 1, 31),
        )
        session.add(semester)
        session.flush()
        course = Course(workspace_id=workspace, semester_id=semester.id, name="数据库原理")
        session.add(course)
        session.flush()
        semester_id, course_id = semester.id, course.id
    job, material, _ = create_embed_job(client, texts=(text,))
    run_index(job, FakeEmbedder())
    with open_session() as session, session.begin():
        row = session.get(Material, material)
        row.semester_id, row.course_id, row.class_name = semester_id, course_id, class_name
    return semester_id, course_id, class_name


def ask(client, semester_id, course_id, class_name, builder):
    client.app.dependency_overrides[get_embedder] = lambda: FakeEmbedder()
    client.app.dependency_overrides[get_text_generator] = lambda: ScriptedGenerator(builder)
    try:
        return client.post(
            "/api/v1/qa/query",
            json={
                "semester_id": semester_id,
                "course_id": course_id,
                "class_name": class_name,
                "question": "作业什么时候截止？",
            },
        )
    finally:
        client.app.dependency_overrides.pop(get_embedder, None)
        client.app.dependency_overrides.pop(get_text_generator, None)


def test_full_date_answer_matches_cited_text_and_scope(client):
    text = "数据库原理（CS101）第三次作业于2026年10月20日23:59前提交。"
    semester_id, course_id, class_name = setup_material(client, text)
    response = ask(
        client,
        semester_id,
        course_id,
        class_name,
        lambda candidates: {
            "insufficient": False,
            "answer": "第三次作业于2026年10月20日23:59前提交。",
            "citation_ids": [candidates[0]["citation_id"]],
            "uncertainties": [],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    citation = body["citations"][0]
    assert "2026年10月20日23:59" in citation["excerpt"]
    assert citation["valid"] is True
    assert body["comparisons"][0]["course_id"] == course_id
    assert body["comparisons"][0]["class_name"] == class_name
    assert not [item for item in body["uncertainties"] if "未出现在被引原文" in item]


def test_incomplete_date_is_explicitly_uncertain(client):
    text = "第三次作业于10月20日截止。"
    semester_id, course_id, class_name = setup_material(client, text)
    response = ask(
        client,
        semester_id,
        course_id,
        class_name,
        lambda candidates: {
            "insufficient": False,
            "answer": "第三次作业于10月20日截止。",
            "citation_ids": [candidates[0]["citation_id"]],
            "uncertainties": [],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert any("没有年份" in item for item in body["uncertainties"])


def test_valid_citation_id_does_not_prove_semantic_support(client):
    text = "第三次作业于2026年10月20日截止。"
    semester_id, course_id, class_name = setup_material(client, text)
    response = ask(
        client,
        semester_id,
        course_id,
        class_name,
        lambda candidates: {
            "insufficient": False,
            "answer": "第三次作业于2026年12月31日截止。",
            "citation_ids": [candidates[0]["citation_id"]],
            "uncertainties": [],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["citations"][0]["valid"] is True
    assert any("2026年12月31日" in item for item in body["uncertainties"])
    assert any("请核对依据" in item for item in body["uncertainties"])


def test_conflict_scenario_requires_review_and_lists_both_dates(client):
    semester_id, course_id, class_name = setup_material(client, "数据库原理作业于10月20日截止。")
    job, material, _ = create_embed_job(client, texts=("数据库原理作业于10月25日截止。",))
    run_index(job, FakeEmbedder())
    with open_session() as session, session.begin():
        row = session.get(Material, material)
        row.semester_id, row.course_id, row.class_name = semester_id, course_id, class_name

    response = ask(
        client,
        semester_id,
        course_id,
        class_name,
        lambda candidates: {
            "insufficient": False,
            "answer": "现有资料对截止日期说法不一致。",
            "citation_ids": [item["citation_id"] for item in candidates],
            "uncertainties": [],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["conflicts"][0]["requires_review"] is True
    assert "10月20日" in body["conflicts"][0]["message"]
    assert "10月25日" in body["conflicts"][0]["message"]
    assert any("不能判断哪一条生效" in item for item in body["uncertainties"])
