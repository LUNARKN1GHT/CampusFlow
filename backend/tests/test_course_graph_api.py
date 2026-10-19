"""C010–C014：课程成环、缺项、拓扑顺序、下一步与学期建议（真实 PostgreSQL）。"""

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


def _plan(client: TestClient, *, credit_limit: int | None = None, version: str = "2024 版") -> dict:
    response = client.post(
        "/api/v1/curriculum-plans",
        json={
            "title": "培养方案",
            "major": "计算机科学与技术",
            "grade_year": 2024,
            "version_label": version,
            "credit_limit_per_term": credit_limit,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _course(
    client: TestClient,
    plan_id: int,
    name: str,
    *,
    credits: float | None = None,
    terms: tuple[str, ...] = (),
) -> dict:
    response = client.post(
        f"/api/v1/curriculum-plans/{plan_id}/courses",
        json={"name": name, "credits": credits, "offered_terms": list(terms)},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _relation(client: TestClient, plan_id: int, source: int, target: int, kind: str) -> dict:
    response = client.post(
        f"/api/v1/curriculum-plans/{plan_id}/relations",
        json={"from_course_id": source, "to_course_id": target, "relation_type": kind},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _study(client: TestClient, plan_id: int, course_id: int, status: str) -> None:
    response = client.post(
        f"/api/v1/curriculum-plans/{plan_id}/study-records",
        json={"curriculum_course_id": course_id, "status": status},
    )
    assert response.status_code == 201, response.text


def test_order_only_uses_prerequisites_and_reports_alternatives(client: TestClient) -> None:
    plan = _plan(client)
    a = _course(client, plan["id"], "程序设计")
    b = _course(client, plan["id"], "高等数学")
    c = _course(client, plan["id"], "数据结构")
    d = _course(client, plan["id"], "数据库系统")
    _relation(client, plan["id"], a["id"], c["id"], "prerequisite")
    _relation(client, plan["id"], b["id"], c["id"], "prerequisite")
    _relation(client, plan["id"], c["id"], d["id"], "corequisite")
    _relation(client, plan["id"], a["id"], d["id"], "recommended")

    response = client.get(f"/api/v1/curriculum-plans/{plan['id']}/order")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["complete"] is True
    assert body["blocked"] is False
    assert body["problems"] == []
    # 并修与建议先学不产生先后顺序，D 仍与 A/B 同层。
    assert body["levels"] == [[a["id"], b["id"], d["id"]], [c["id"]]]
    assert body["alternatives"] is True


def test_cycle_and_self_loop_block_order_with_concrete_path(client: TestClient) -> None:
    plan = _plan(client)
    a = _course(client, plan["id"], "甲")
    b = _course(client, plan["id"], "乙")
    c = _course(client, plan["id"], "丙")
    _relation(client, plan["id"], a["id"], b["id"], "prerequisite")
    _relation(client, plan["id"], b["id"], c["id"], "prerequisite")
    _relation(client, plan["id"], c["id"], a["id"], "prerequisite")

    response = client.get(f"/api/v1/curriculum-plans/{plan['id']}/order")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["complete"] is False and body["blocked"] is True
    assert body["levels"] == []
    cycle = next(item for item in body["problems"] if item["code"] == "prerequisite_cycle")
    assert cycle["path"][0] == cycle["path"][-1]
    assert set(cycle["path"]) == {a["id"], b["id"], c["id"]}
    assert len(cycle["relation_ids"]) == 3

    looped = _plan(client, version="2025 版")
    d = _course(client, looped["id"], "毕业设计")
    _relation(client, looped["id"], d["id"], d["id"], "prerequisite")
    body = client.get(f"/api/v1/curriculum-plans/{looped['id']}/order").json()
    assert body["blocked"] is True and body["levels"] == []
    self_loop = next(item for item in body["problems"] if item["code"] == "prerequisite_cycle")
    assert self_loop["path"] == [d["id"], d["id"]]


def test_unmatched_relation_candidate_stays_missing_and_blocks_completeness(
    client, monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(client.app.state, "file_storage", LocalFileStorage(tmp_path))
    material = client.post("/api/v1/materials/text", json={"title": "方案", "content": TEXT}).json()
    plan = _plan(client)
    _course(client, plan["id"], "程序设计")
    workspace_id = client.get("/api/v1/settings").json()["workspace_id"]
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        version = repos.materials.list_versions(material["material"]["id"])[0]
        chunk_id = material["chunks"][0]["id"]
        repos.candidates.create(
            workspace_id,
            version.id,
            CandidateDraft(
                CandidateKind.COURSE_RELATION,
                (
                    CandidateField(
                        "from_course_name",
                        "方案外课程",
                        FieldOrigin.DOCUMENT,
                        (FieldEvidence(chunk_id, TEXT),),
                    ),
                    CandidateField(
                        "to_course_name",
                        "程序设计",
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
    assert [item["code"] for item in graph["missing"]] == ["relation_candidate_unmatched"]
    assert graph["missing"][0]["candidate_ids"]

    order = client.get(f"/api/v1/curriculum-plans/{plan['id']}/order").json()
    assert order["blocked"] is True and order["levels"] == []
    assert any(item["code"] == "relation_candidate_unmatched" for item in order["problems"])


def test_next_courses_separates_unmet_prerequisites_from_recommendations(
    client: TestClient,
) -> None:
    plan = _plan(client)
    a = _course(client, plan["id"], "程序设计")
    b = _course(client, plan["id"], "数据结构")
    c = _course(client, plan["id"], "数据库系统")
    d = _course(client, plan["id"], "数据可视化")
    _relation(client, plan["id"], a["id"], b["id"], "prerequisite")
    _relation(client, plan["id"], b["id"], c["id"], "prerequisite")
    _relation(client, plan["id"], b["id"], d["id"], "recommended")
    _study(client, plan["id"], a["id"], "completed")
    _study(client, plan["id"], b["id"], "in_progress")

    body = client.get(f"/api/v1/curriculum-plans/{plan['id']}/next").json()
    items = {item["course_id"]: item for item in body["courses"]}
    assert items[a["id"]]["study_status"] == "completed"
    assert items[a["id"]]["available"] is False
    assert items[b["id"]]["available"] is True and items[b["id"]]["unmet"] == []
    assert items[c["id"]]["available"] is False
    assert any("数据结构" in note for note in items[c["id"]]["unmet"])
    # 建议先学单独展示，不把课程标记为不可学。
    assert items[d["id"]]["available"] is True
    assert any("数据结构" in note for note in items[d["id"]]["recommended"])


def test_semester_plan_uses_known_terms_and_credit_limit(client: TestClient) -> None:
    plan = _plan(client, credit_limit=5)
    a = _course(client, plan["id"], "高等数学", credits=3, terms=("fall",))
    b = _course(client, plan["id"], "数据结构", credits=3, terms=("spring",))
    c = _course(client, plan["id"], "超重课程", credits=6, terms=("fall",))
    d = _course(client, plan["id"], "学分未知课程")
    _relation(client, plan["id"], a["id"], b["id"], "prerequisite")

    response = client.post(f"/api/v1/curriculum-plans/{plan['id']}/semester-plan", json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["blocked"] is False
    assert body["credit_limit"] == 5
    assert body["graduation_audit"] is False
    assert any("不能替代选课或毕业资格审核" in note for note in body["limitations"])
    assert body["terms"][0]["index"] == 0 and body["terms"][0]["season"] == "fall"
    assert a["id"] in body["terms"][0]["course_ids"]
    assert d["id"] in body["terms"][0]["course_ids"]
    assert body["terms"][0]["credits_unknown"] == 1
    assert any("学分未知" in warning for warning in body["terms"][0]["warnings"])
    spring = next(term for term in body["terms"] if term["season"] == "spring")
    assert b["id"] in spring["course_ids"]
    unplaced = {item["course_id"]: item for item in body["unplaced"]}
    assert c["id"] in unplaced and "开课学期" in unplaced[c["id"]]["reason"]


def test_semester_plan_stays_blocked_by_cycle(client: TestClient) -> None:
    plan = _plan(client)
    a = _course(client, plan["id"], "甲")
    b = _course(client, plan["id"], "乙")
    _relation(client, plan["id"], a["id"], b["id"], "prerequisite")
    _relation(client, plan["id"], b["id"], a["id"], "prerequisite")

    body = client.post(f"/api/v1/curriculum-plans/{plan['id']}/semester-plan", json={}).json()
    assert body["blocked"] is True
    assert body["terms"] == [] and body["unplaced"] == []
    assert any("未生成学期方案" in note for note in body["limitations"])


def test_analysis_endpoints_require_plan_in_workspace(client: TestClient) -> None:
    assert client.get("/api/v1/curriculum-plans/999/order").status_code == 404
    assert client.get("/api/v1/curriculum-plans/999/next").status_code == 404
    assert client.post("/api/v1/curriculum-plans/999/semester-plan", json={}).status_code == 404
