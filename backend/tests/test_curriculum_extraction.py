"""C003：培养方案课程与关系候选提取（真实数据库，假模型输出）。"""

import json

import pytest
from sqlalchemy import func, select

from campusflow.api.deps import get_text_generator
from campusflow.infrastructure.db.models import CourseRelation, CurriculumCourse, ExtractedItem
from campusflow.infrastructure.llm.text import FakeTextGenerator
from campusflow.infrastructure.storage.local import LocalFileStorage

TEXT = "数据库原理（CS301，3 学分，秋季学期）。学习数据库系统前须先修程序设计。"


def _response(kind: str, chunk_id: int) -> dict:
    if kind == "curriculum_course":
        return {
            "items": [
                {
                    "kind": "curriculum_course",
                    "fields": [
                        {
                            "name": "course_name",
                            "value": "数据库原理",
                            "origin": "document",
                            "evidence": [{"chunk_id": chunk_id, "quote": "数据库原理"}],
                        },
                        {
                            "name": "course_code",
                            "value": "CS301",
                            "origin": "document",
                            "evidence": [{"chunk_id": chunk_id, "quote": "CS301"}],
                        },
                        {
                            "name": "credits",
                            "value": 3,
                            "origin": "document",
                            "evidence": [{"chunk_id": chunk_id, "quote": "3 学分"}],
                        },
                        {
                            "name": "offered_terms_text",
                            "value": "秋季学期",
                            "origin": "document",
                            "evidence": [{"chunk_id": chunk_id, "quote": "秋季学期"}],
                        },
                    ],
                }
            ]
        }
    return {
        "items": [
            {
                "kind": "course_relation",
                "fields": [
                    {
                        "name": "from_course_name",
                        "value": "程序设计",
                        "origin": "document",
                        "evidence": [
                            {"chunk_id": chunk_id, "quote": "学习数据库系统前须先修程序设计"}
                        ],
                    },
                    {
                        "name": "to_course_name",
                        "value": "数据库系统",
                        "origin": "document",
                        "evidence": [
                            {"chunk_id": chunk_id, "quote": "学习数据库系统前须先修程序设计"}
                        ],
                    },
                    {
                        "name": "relation_type",
                        "value": "强制先修",
                        "origin": "document",
                        "evidence": [
                            {"chunk_id": chunk_id, "quote": "学习数据库系统前须先修程序设计"}
                        ],
                    },
                    {
                        "name": "condition_text",
                        "value": None,
                        "origin": "document",
                        "missing_reason": "原文未单独说明条件",
                    },
                ],
            }
        ]
    }


class CurriculumGenerator:
    def __init__(self) -> None:
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        kind = (
            "curriculum_course"
            if '"kind": "curriculum_course"' in request.system_prompt
            else "course_relation"
        )
        sources = json.loads(request.source_json)["source_chunks"]
        needle = "数据库原理" if kind == "curriculum_course" else "学习数据库系统前须先修程序设计"
        source = next(item for item in sources if needle in item["text"])
        return json.dumps(_response(kind, source["chunk_id"]), ensure_ascii=False)


@pytest.fixture
def material(client, monkeypatch, tmp_path):
    monkeypatch.setattr(client.app.state, "file_storage", LocalFileStorage(tmp_path))
    response = client.post(
        "/api/v1/materials/text",
        json={"title": "培养方案", "content": TEXT},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _plan(client) -> dict:
    response = client.post(
        "/api/v1/curriculum-plans",
        json={
            "title": "培养方案",
            "major": "计算机科学与技术",
            "grade_year": 2024,
            "version_label": "2024 版",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_extraction_saves_pending_candidates_without_formal_relations(client, material) -> None:
    plan = _plan(client)
    generator = CurriculumGenerator()
    client.app.dependency_overrides[get_text_generator] = lambda: generator
    try:
        response = client.post(
            f"/api/v1/curriculum-plans/{plan['id']}/extract",
            json={"material_id": material["material"]["id"], "version_no": 1},
        )
    finally:
        client.app.dependency_overrides.pop(get_text_generator, None)
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["candidates"]) == 2
    assert {item["kind"] for item in body["candidates"]} == {
        "curriculum_course",
        "course_relation",
    }
    assert all(item["review_status"] == "pending" for item in body["candidates"])
    assert body["pending_review"] is True

    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ExtractedItem)) == 2
        assert session.scalar(select(func.count()).select_from(CurriculumCourse)) == 0
        assert session.scalar(select(func.count()).select_from(CourseRelation)) == 0

    listed = client.get(
        "/api/v1/candidates", params={"kind": ["curriculum_course", "course_relation"]}
    ).json()
    assert len(listed) == 2
    assert {item["curriculum_plan_id"] for item in listed} == {plan["id"]}
    assert all(item["title"] for item in listed)


def test_relation_candidate_keeps_unmatched_course_as_review_reason(client, material) -> None:
    plan = _plan(client)
    generator = CurriculumGenerator()
    client.app.dependency_overrides[get_text_generator] = lambda: generator
    try:
        response = client.post(
            f"/api/v1/curriculum-plans/{plan['id']}/extract",
            json={"material_id": material["material"]["id"], "version_no": 1},
        )
    finally:
        client.app.dependency_overrides.pop(get_text_generator, None)
    assert response.status_code == 200, response.text
    listed = client.get(
        "/api/v1/candidates", params={"kind": ["course_relation"]}
    ).json()
    assert listed[0]["review_reasons"]
    codes = {reason["code"] for reason in listed[0]["review_reasons"]}
    assert "course_relation_review" in codes
    assert "relation_course_unmatched" in codes


class BadRelationGenerator(CurriculumGenerator):
    def generate(self, request):
        payload = json.loads(super().generate(request))
        if payload["items"][0]["kind"] == "course_relation":
            payload["items"][0]["fields"][2]["value"] = "同时开课"
        return json.dumps(payload, ensure_ascii=False)


def test_unknown_relation_type_is_rejected_without_saving(client, material) -> None:
    plan = _plan(client)
    generator = BadRelationGenerator()
    client.app.dependency_overrides[get_text_generator] = lambda: generator
    try:
        response = client.post(
            f"/api/v1/curriculum-plans/{plan['id']}/extract",
            json={"material_id": material["material"]["id"], "version_no": 1},
        )
    finally:
        client.app.dependency_overrides.pop(get_text_generator, None)
    assert response.status_code == 422 or response.status_code == 400
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ExtractedItem)) == 0


def test_extraction_requires_plan_in_workspace(client, material) -> None:
    generator = FakeTextGenerator("{}")
    client.app.dependency_overrides[get_text_generator] = lambda: generator
    try:
        response = client.post(
            "/api/v1/curriculum-plans/999/extract",
            json={"material_id": material["material"]["id"], "version_no": 1},
        )
    finally:
        client.app.dependency_overrides.pop(get_text_generator, None)
    assert response.status_code == 404
    assert generator.requests == []
