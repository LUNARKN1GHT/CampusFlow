"""C005：课程与关系候选的修正、确认、忽略和转换（真实数据库）。"""

import pytest

from campusflow.application.ports.repositories import Repositories
from campusflow.domain.candidates import (
    CandidateDraft,
    CandidateField,
    CandidateKind,
    CandidateScope,
    FieldEvidence,
    FieldOrigin,
)
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.storage.local import LocalFileStorage

TEXT = "数据库原理与程序设计课程；学习数据库系统前须先修程序设计。"


@pytest.fixture
def source(client, monkeypatch, tmp_path):
    monkeypatch.setattr(client.app.state, "file_storage", LocalFileStorage(tmp_path))
    response = client.post("/api/v1/materials/text", json={"title": "培养方案", "content": TEXT})
    assert response.status_code == 201, response.text
    data = response.json()
    workspace_id = client.get("/api/v1/settings").json()["workspace_id"]
    return data["material"]["id"], data["chunks"][0]["id"], workspace_id


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


def _course(client, plan_id: int, name: str) -> dict:
    response = client.post(f"/api/v1/curriculum-plans/{plan_id}/courses", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def _save_candidate(
    client,
    material_id: int,
    chunk_id: int,
    plan_id: int,
    kind: CandidateKind,
    fields: tuple[CandidateField, ...],
) -> int:
    with client.app.state.session_factory() as session:
        repos: Repositories = build_repositories(session)
        version = repos.materials.list_versions(material_id)[0]
        draft = CandidateDraft(
            kind,
            fields,
            CandidateScope(curriculum_plan_id=plan_id),
        )
        candidate = repos.candidates.create(
            client.get("/api/v1/settings").json()["workspace_id"], version.id, draft
        )
        repos.uow.commit()
        return candidate.id


def _course_fields(chunk_id: int) -> tuple[CandidateField, ...]:
    return (
        CandidateField(
            "course_name",
            "数据库原理",
            FieldOrigin.DOCUMENT,
            (FieldEvidence(chunk_id, "数据库原理"),),
        ),
        CandidateField(
            "credits",
            3,
            FieldOrigin.DOCUMENT,
            (FieldEvidence(chunk_id, "数据库原理与程序设计课程"),),
        ),
        CandidateField(
            "offered_terms",
            ["fall"],
            FieldOrigin.INFERRED,
            (FieldEvidence(chunk_id, "数据库原理与程序设计课程"),),
            explanation="原文未单列季节，示例按秋季核对",
        ),
    )


def _relation_fields(
    chunk_id: int, source_name: str = "程序设计", target_name: str = "数据库系统"
) -> tuple[CandidateField, ...]:
    return (
        CandidateField(
            "from_course_name",
            source_name,
            FieldOrigin.DOCUMENT,
            (FieldEvidence(chunk_id, "学习数据库系统前须先修程序设计"),),
        ),
        CandidateField(
            "to_course_name",
            target_name,
            FieldOrigin.DOCUMENT,
            (FieldEvidence(chunk_id, "学习数据库系统前须先修程序设计"),),
        ),
        CandidateField(
            "relation_type",
            "强制先修",
            FieldOrigin.DOCUMENT,
            (FieldEvidence(chunk_id, "须先修"),),
        ),
    )


def _confirm(client, candidate_id: int) -> None:
    response = client.patch(
        f"/api/v1/candidates/{candidate_id}/review-status",
        json={"status": "confirmed"},
    )
    assert response.status_code == 200, response.text


def test_course_candidate_conversion_preserves_plan_and_is_idempotent(client, source) -> None:
    material_id, chunk_id, _ = source
    plan = _plan(client)
    candidate_id = _save_candidate(
        client,
        material_id,
        chunk_id,
        plan["id"],
        CandidateKind.CURRICULUM_COURSE,
        _course_fields(chunk_id),
    )
    _confirm(client, candidate_id)

    converted = client.post(f"/api/v1/candidates/{candidate_id}/convert")
    assert converted.status_code == 200, converted.text
    body = converted.json()
    assert body["created"] is True
    assert body["kind"] == "curriculum_course"

    courses = client.get(f"/api/v1/curriculum-plans/{plan['id']}/courses").json()
    assert len(courses) == 1
    assert courses[0]["name"] == "数据库原理"
    assert courses[0]["curriculum_plan_id"] == plan["id"]
    assert courses[0]["source_candidate_id"] == candidate_id
    assert courses[0]["credits"] == 3

    repeated = client.post(f"/api/v1/candidates/{candidate_id}/convert").json()
    assert repeated["created"] is False
    assert repeated["object_id"] == body["object_id"]


def test_relation_candidate_conversion_keeps_type_and_plan(client, source) -> None:
    material_id, chunk_id, _ = source
    plan = _plan(client)
    programming = _course(client, plan["id"], "程序设计")
    database = _course(client, plan["id"], "数据库系统")
    candidate_id = _save_candidate(
        client,
        material_id,
        chunk_id,
        plan["id"],
        CandidateKind.COURSE_RELATION,
        _relation_fields(chunk_id),
    )
    _confirm(client, candidate_id)
    converted = client.post(f"/api/v1/candidates/{candidate_id}/convert")
    assert converted.status_code == 200, converted.text
    assert converted.json()["kind"] == "course_relation"

    relations = client.get(f"/api/v1/curriculum-plans/{plan['id']}/relations").json()
    assert len(relations) == 1
    assert relations[0]["from_course_id"] == programming["id"]
    assert relations[0]["to_course_id"] == database["id"]
    assert relations[0]["relation_type"] == "prerequisite"
    assert relations[0]["curriculum_plan_id"] == plan["id"]
    assert relations[0]["source_candidate_id"] == candidate_id


def test_missing_course_stays_reviewable_and_can_be_corrected(client, source) -> None:
    material_id, chunk_id, _ = source
    plan = _plan(client)
    _course(client, plan["id"], "程序设计")
    database = _course(client, plan["id"], "数据库系统")
    candidate_id = _save_candidate(
        client,
        material_id,
        chunk_id,
        plan["id"],
        CandidateKind.COURSE_RELATION,
        _relation_fields(chunk_id, target_name="不存在的课程"),
    )
    _confirm(client, candidate_id)
    failed = client.post(f"/api/v1/candidates/{candidate_id}/convert")
    assert failed.status_code == 400
    assert "无法唯一匹配" in failed.text
    assert client.get(f"/api/v1/curriculum-plans/{plan['id']}/relations").json() == []

    revised = client.patch(
        f"/api/v1/candidates/{candidate_id}/fields/to_course_name",
        json={
            "value": "数据库系统",
            "origin": "user",
            "note": "按课程目录核对后修正",
        },
    )
    assert revised.status_code == 200, revised.text
    retried = client.post(f"/api/v1/candidates/{candidate_id}/convert")
    assert retried.status_code == 200, retried.text
    relations = client.get(f"/api/v1/curriculum-plans/{plan['id']}/relations").json()
    assert relations[0]["to_course_id"] == database["id"]


def test_ambiguous_alias_blocks_relation_conversion(client, source) -> None:
    material_id, chunk_id, _ = source
    plan = _plan(client)
    first = _course(client, plan["id"], "数据库系统")
    second = _course(client, plan["id"], "数据库原理")
    for course in (first, second):
        response = client.post(
            f"/api/v1/curriculum-plans/{plan['id']}/aliases",
            json={"curriculum_course_id": course["id"], "alias": "数据库"},
        )
        assert response.status_code == 201
    _course(client, plan["id"], "程序设计")
    candidate_id = _save_candidate(
        client,
        material_id,
        chunk_id,
        plan["id"],
        CandidateKind.COURSE_RELATION,
        _relation_fields(chunk_id, target_name="数据库"),
    )
    _confirm(client, candidate_id)
    response = client.post(f"/api/v1/candidates/{candidate_id}/convert")
    assert response.status_code == 400
    assert "ambiguous" in response.text
    assert client.get(f"/api/v1/curriculum-plans/{plan['id']}/relations").json() == []


def test_ignored_candidate_cannot_be_converted(client, source) -> None:
    material_id, chunk_id, _ = source
    plan = _plan(client)
    candidate_id = _save_candidate(
        client,
        material_id,
        chunk_id,
        plan["id"],
        CandidateKind.CURRICULUM_COURSE,
        _course_fields(chunk_id),
    )
    ignored = client.patch(
        f"/api/v1/candidates/{candidate_id}/review-status",
        json={"status": "ignored", "reason": "不是本人培养方案"},
    )
    assert ignored.status_code == 200
    response = client.post(f"/api/v1/candidates/{candidate_id}/convert")
    assert response.status_code == 400
    assert client.get(f"/api/v1/curriculum-plans/{plan['id']}/courses").json() == []


def test_relation_candidate_detail_exposes_plan_scope(client, source) -> None:
    material_id, chunk_id, _ = source
    plan = _plan(client)
    candidate_id = _save_candidate(
        client,
        material_id,
        chunk_id,
        plan["id"],
        CandidateKind.COURSE_RELATION,
        _relation_fields(chunk_id),
    )
    detail = client.get(f"/api/v1/candidates/{candidate_id}")
    assert detail.status_code == 200
    assert detail.json()["scope"]["curriculum_plan_id"] == plan["id"]
    assert detail.json()["allowed_transitions"]
