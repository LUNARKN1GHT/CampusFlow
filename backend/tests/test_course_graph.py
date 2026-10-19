"""C009：适用方案内的课程关系图（真实 PostgreSQL 测试库）。"""

from fastapi.testclient import TestClient

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

TEXT = "课程关系：学习数据库系统前须先修程序设计。"


def _plan(client: TestClient, version: str = "2024 版", major: str = "计算机科学与技术") -> dict:
    response = client.post(
        "/api/v1/curriculum-plans",
        json={
            "title": "培养方案",
            "major": major,
            "grade_year": 2024,
            "version_label": version,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _course(client: TestClient, plan_id: int, name: str, code: str | None = None) -> dict:
    response = client.post(
        f"/api/v1/curriculum-plans/{plan_id}/courses",
        json={"name": name, "code": code},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _relation(
    client: TestClient, plan_id: int, source: int, target: int, kind: str
) -> dict:
    response = client.post(
        f"/api/v1/curriculum-plans/{plan_id}/relations",
        json={"from_course_id": source, "to_course_id": target, "relation_type": kind},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_graph_distinguishes_three_edge_types_and_scope(client: TestClient) -> None:
    first = _plan(client, "2024 版")
    second = _plan(client, "2025 版")
    a = _course(client, first["id"], "程序设计")
    b = _course(client, first["id"], "数据结构")
    c = _course(client, first["id"], "数据库系统")
    _relation(client, first["id"], a["id"], b["id"], "prerequisite")
    _relation(client, first["id"], b["id"], c["id"], "corequisite")
    _relation(client, first["id"], a["id"], c["id"], "recommended")
    _course(client, second["id"], "程序设计")
    _course(client, second["id"], "数据结构")

    graph = client.get(f"/api/v1/curriculum-plans/{first['id']}/graph")
    assert graph.status_code == 200, graph.text
    body = graph.json()
    assert body["complete"] is True
    assert {node["name"] for node in body["nodes"]} == {"程序设计", "数据结构", "数据库系统"}
    assert {edge["relation_type"] for edge in body["edges"]} == {
        "prerequisite",
        "corequisite",
        "recommended",
    }
    assert all(node["study_status"] == "unknown" for node in body["nodes"])

    other = client.get(f"/api/v1/curriculum-plans/{second['id']}/graph").json()
    assert other["edges"] == []
    assert {node["name"] for node in other["nodes"]} == {"程序设计", "数据结构"}


def test_graph_edge_keeps_candidate_evidence(client, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(client.app.state, "file_storage", LocalFileStorage(tmp_path))
    material = client.post(
        "/api/v1/materials/text", json={"title": "方案", "content": TEXT}
    ).json()
    plan = _plan(client)
    _course(client, plan["id"], "程序设计")
    _course(client, plan["id"], "数据库系统")
    workspace_id = client.get("/api/v1/settings").json()["workspace_id"]
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        version = repos.materials.list_versions(material["material"]["id"])[0]
        chunk_id = material["chunks"][0]["id"]
        candidate = repos.candidates.create(
            workspace_id,
            version.id,
            CandidateDraft(
                CandidateKind.COURSE_RELATION,
                (
                    CandidateField(
                        "from_course_name",
                        "程序设计",
                        FieldOrigin.DOCUMENT,
                        (FieldEvidence(chunk_id, TEXT),),
                    ),
                    CandidateField(
                        "to_course_name",
                        "数据库系统",
                        FieldOrigin.DOCUMENT,
                        (FieldEvidence(chunk_id, TEXT),),
                    ),
                    CandidateField(
                        "relation_type",
                        "强制先修",
                        FieldOrigin.DOCUMENT,
                        (FieldEvidence(chunk_id, TEXT),),
                    ),
                ),
                CandidateScope(curriculum_plan_id=plan["id"]),
            ),
        )
        repos.uow.commit()
        candidate_id = candidate.id
    client.patch(
        f"/api/v1/candidates/{candidate_id}/review-status",
        json={"status": "confirmed"},
    )
    converted = client.post(f"/api/v1/candidates/{candidate_id}/convert")
    assert converted.status_code == 200, converted.text

    graph = client.get(f"/api/v1/curriculum-plans/{plan['id']}/graph").json()
    assert len(graph["edges"]) == 1
    edge = graph["edges"][0]
    assert edge["source_candidate_id"] == candidate_id
    assert edge["evidence"]
    assert edge["evidence"][0]["quote"] == TEXT
    assert edge["evidence"][0]["material_id"] == material["material"]["id"]


def test_graph_requires_plan_in_workspace(client: TestClient) -> None:
    response = client.get("/api/v1/curriculum-plans/999/graph")
    assert response.status_code == 404
