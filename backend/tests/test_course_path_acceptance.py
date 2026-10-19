"""C017：用合成培养方案验收版本差异、三类关系、环、缺项与范围隔离。"""

import json
from pathlib import Path

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

SAMPLE = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "verification"
        / "samples"
        / "course-paths"
        / "sample-plan.json"
    ).read_text(encoding="utf-8")
)
TEXT = "高级编译优化先修课程：编译器构造（CS499）。"


def _version(label: str) -> dict:
    return next(item for item in SAMPLE["versions"] if item["version_label"] == label)


def _plan(client: TestClient, version: dict) -> dict:
    response = client.post(
        "/api/v1/curriculum-plans",
        json={
            "title": "计算机科学与技术培养方案",
            "major": SAMPLE["major"],
            "grade_year": SAMPLE["grade_year"],
            "version_label": version["version_label"],
            "credit_limit_per_term": version["credit_limit_per_term"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _build_courses(client: TestClient, plan_id: int, version: dict) -> dict[str, int]:
    ids: dict[str, int] = {}
    for course in version["courses"]:
        response = client.post(
            f"/api/v1/curriculum-plans/{plan_id}/courses",
            json={
                "name": course["name"],
                "code": course["code"],
                "credits": course["credits"],
                "offered_terms": course["offered_terms"],
            },
        )
        assert response.status_code == 201, response.text
        ids[course["code"]] = response.json()["id"]
    return ids


def _build_relations(client: TestClient, plan_id: int, version: dict, ids: dict[str, int]) -> None:
    for relation in version["relations"]:
        if relation["from"] not in ids or relation["to"] not in ids:
            # 方案外先修课程不得自动补成方案课程，单独以候选缺项验收。
            continue
        response = client.post(
            f"/api/v1/curriculum-plans/{plan_id}/relations",
            json={
                "from_course_id": ids[relation["from"]],
                "to_course_id": ids[relation["to"]],
                "relation_type": relation["type"],
                "condition_text": relation["condition"],
            },
        )
        assert response.status_code == 201, response.text


def _build_plan(client: TestClient, label: str) -> tuple[dict, dict[str, int]]:
    version = _version(label)
    plan = _plan(client, version)
    ids = _build_courses(client, plan["id"], version)
    _build_relations(client, plan["id"], version, ids)
    return plan, ids


def test_2024_legal_order_satisfies_every_prerequisite(client: TestClient) -> None:
    version = _version("2024 版")
    plan, ids = _build_plan(client, "2024 版")
    graph = client.get(f"/api/v1/curriculum-plans/{plan['id']}/graph").json()
    assert graph["complete"] is True
    assert graph["cycles"] == [] and graph["conflicts"] == []
    assert graph["missing"] == [] and graph["pending"] == []

    order = client.get(f"/api/v1/curriculum-plans/{plan['id']}/order").json()
    assert order["blocked"] is False and order["problems"] == []
    position = {}
    for level_index, level in enumerate(order["levels"]):
        for course_id in level:
            position[course_id] = level_index
    assert len(position) == len(version["courses"])
    for relation in version["relations"]:
        if relation["type"] == "prerequisite":
            assert position[ids[relation["from"]]] < position[ids[relation["to"]]]


def test_2025_cycles_self_loop_missing_and_conflict_all_block(client: TestClient) -> None:
    plan, ids = _build_plan(client, "2025 修订版")

    # R-2025-023 引用的 CS499 不在方案课程表：以未匹配候选呈现为缺项。
    material = client.post("/api/v1/materials/text", json={"title": "方案", "content": TEXT}).json()
    workspace_id = client.get("/api/v1/settings").json()["workspace_id"]
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        source_version = repos.materials.list_versions(material["material"]["id"])[0]
        chunk_id = material["chunks"][0]["id"]
        repos.candidates.create(
            workspace_id,
            source_version.id,
            CandidateDraft(
                CandidateKind.COURSE_RELATION,
                (
                    CandidateField(
                        "from_course_name",
                        "编译器构造",
                        FieldOrigin.DOCUMENT,
                        (FieldEvidence(chunk_id, TEXT),),
                    ),
                    CandidateField(
                        "to_course_name",
                        "高级编译优化",
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

    graph = client.get(f"/api/v1/curriculum-plans/{plan['id']}/graph").json()
    assert graph["complete"] is False
    cycle_paths = [item["path"] for item in graph["cycles"]]
    assert [ids["CS404"], ids["CS404"]] in cycle_paths
    three_way = next(path for path in cycle_paths if len(path) == 4)
    assert three_way[0] == three_way[-1]
    assert {three_way[0], ids["CS402"], ids["CS403"]}.issubset(set(three_way))
    assert graph["conflicts"] and graph["conflicts"][0]["code"] == "relation_conflict"
    assert [item["code"] for item in graph["missing"]] == ["relation_candidate_unmatched"]
    assert graph["missing"][0]["candidate_ids"]

    order = client.get(f"/api/v1/curriculum-plans/{plan['id']}/order").json()
    assert order["blocked"] is True and order["levels"] == []
    codes = {item["code"] for item in order["problems"]}
    assert {"prerequisite_cycle", "relation_conflict", "relation_candidate_unmatched"} <= codes

    semester = client.post(f"/api/v1/curriculum-plans/{plan['id']}/semester-plan", json={}).json()
    assert semester["blocked"] is True and semester["terms"] == []
    assert any("未生成学期方案" in note for note in semester["limitations"])


def test_plan_versions_stay_isolated_and_unknown_fields_do_not_reorder(
    client: TestClient,
) -> None:
    first, first_ids = _build_plan(client, "2024 版")
    second, second_ids = _build_plan(client, "2025 修订版")

    # 同名课程拥有各自编号，关系不会跨方案混接。
    assert first_ids["CS101"] != second_ids["CS101"]
    first_graph = client.get(f"/api/v1/curriculum-plans/{first['id']}/graph").json()
    second_graph = client.get(f"/api/v1/curriculum-plans/{second['id']}/graph").json()
    assert first_graph["complete"] is True
    assert second_graph["cycles"]
    first_edges = {(edge["from_course_id"], edge["to_course_id"]) for edge in first_graph["edges"]}
    second_edges = {
        (edge["from_course_id"], edge["to_course_id"]) for edge in second_graph["edges"]
    }
    assert first_edges.isdisjoint(second_edges)

    nodes = {node["code"]: node for node in first_graph["nodes"]}
    assert nodes["CS215"]["credits"] is None
    assert nodes["CS209"]["offered_terms"] == []
    order = client.get(f"/api/v1/curriculum-plans/{first['id']}/order").json()
    assert order["blocked"] is False
    semester = client.post(f"/api/v1/curriculum-plans/{first['id']}/semester-plan", json={}).json()
    assert semester["blocked"] is False
    assert any(term["credits_unknown"] for term in semester["terms"])
    assert semester["graduation_audit"] is False
    next_courses = client.get(f"/api/v1/curriculum-plans/{first['id']}/next").json()
    assert len(next_courses["courses"]) == len(_version("2024 版")["courses"])
