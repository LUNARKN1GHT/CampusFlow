"""C004：培养方案课程与三类课程关系（真实 PostgreSQL 测试库）。"""

from fastapi.testclient import TestClient


def _plan(client: TestClient, version: str = "2024 版") -> dict:
    response = client.post(
        "/api/v1/curriculum-plans",
        json={
            "title": "培养方案",
            "major": "计算机科学与技术",
            "grade_year": 2024,
            "version_label": version,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _course(client: TestClient, plan_id: int, name: str, **values) -> dict:
    response = client.post(
        f"/api/v1/curriculum-plans/{plan_id}/courses",
        json={"name": name, **values},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _relation(
    client: TestClient,
    plan_id: int,
    source: int,
    target: int,
    relation_type: str = "prerequisite",
) -> dict:
    response = client.post(
        f"/api/v1/curriculum-plans/{plan_id}/relations",
        json={
            "from_course_id": source,
            "to_course_id": target,
            "relation_type": relation_type,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_three_relation_types_are_queryable_separately(client: TestClient) -> None:
    plan = _plan(client)
    calculus = _course(client, plan["id"], "高等数学", credits=5, offered_terms=["fall"])
    programming = _course(client, plan["id"], "程序设计", credits=3)
    data_structures = _course(client, plan["id"], "数据结构", credits=4)

    _relation(client, plan["id"], calculus["id"], programming["id"], "recommended")
    _relation(client, plan["id"], programming["id"], data_structures["id"], "prerequisite")
    _relation(client, plan["id"], data_structures["id"], programming["id"], "corequisite")

    all_relations = client.get(f"/api/v1/curriculum-plans/{plan['id']}/relations").json()
    assert len(all_relations) == 3
    assert {item["relation_type"] for item in all_relations} == {
        "prerequisite",
        "corequisite",
        "recommended",
    }

    prerequisites = client.get(
        f"/api/v1/curriculum-plans/{plan['id']}/relations",
        params={"relation_type": "prerequisite"},
    ).json()
    assert len(prerequisites) == 1
    assert prerequisites[0]["from_course_id"] == programming["id"]
    assert prerequisites[0]["to_course_id"] == data_structures["id"]

    recommended = client.get(
        f"/api/v1/curriculum-plans/{plan['id']}/relations",
        params={"relation_type": "recommended"},
    ).json()
    assert len(recommended) == 1


def test_relation_rejects_cross_plan_course(client: TestClient) -> None:
    first = _plan(client, "2024 版")
    second = _plan(client, "2025 版")
    local = _course(client, first["id"], "数据结构")
    foreign = _course(client, second["id"], "数据结构")

    response = client.post(
        f"/api/v1/curriculum-plans/{first['id']}/relations",
        json={
            "from_course_id": foreign["id"],
            "to_course_id": local["id"],
            "relation_type": "prerequisite",
        },
    )
    assert response.status_code == 400
    assert "不属于" in response.text


def test_relation_is_idempotent_only_by_type_and_direction(client: TestClient) -> None:
    plan = _plan(client)
    a = _course(client, plan["id"], "A")
    b = _course(client, plan["id"], "B")
    _relation(client, plan["id"], a["id"], b["id"], "prerequisite")

    duplicate = client.post(
        f"/api/v1/curriculum-plans/{plan['id']}/relations",
        json={
            "from_course_id": a["id"],
            "to_course_id": b["id"],
            "relation_type": "prerequisite",
        },
    )
    assert duplicate.status_code == 400
    assert "已存在" in duplicate.text

    # 反向关系是不同事实，允许保存，由环检测判断是否矛盾。
    reverse = client.post(
        f"/api/v1/curriculum-plans/{plan['id']}/relations",
        json={
            "from_course_id": b["id"],
            "to_course_id": a["id"],
            "relation_type": "prerequisite",
        },
    )
    assert reverse.status_code == 201, reverse.text


def test_self_loop_is_kept_for_analysis(client: TestClient) -> None:
    plan = _plan(client)
    course = _course(client, plan["id"], "自环课程")
    relation = _relation(client, plan["id"], course["id"], course["id"], "prerequisite")
    assert relation["from_course_id"] == relation["to_course_id"]


def test_referenced_course_cannot_be_deleted_silently(client: TestClient) -> None:
    plan = _plan(client)
    a = _course(client, plan["id"], "A")
    b = _course(client, plan["id"], "B")
    _relation(client, plan["id"], a["id"], b["id"])

    blocked = client.delete(f"/api/v1/curriculum-plans/{plan['id']}/courses/{a['id']}")
    assert blocked.status_code == 400
    assert "关系" in blocked.text

    relation_id = _relation_id(client, plan["id"])
    client.delete(f"/api/v1/curriculum-plans/{plan['id']}/relations/{relation_id}")
    deleted = client.delete(f"/api/v1/curriculum-plans/{plan['id']}/courses/{a['id']}")
    assert deleted.status_code == 204


def _relation_id(client: TestClient, plan_id: int) -> int:
    return client.get(f"/api/v1/curriculum-plans/{plan_id}/relations").json()[0]["id"]


def test_invalid_relation_type_and_offered_terms_are_rejected(client: TestClient) -> None:
    plan = _plan(client)
    a = _course(client, plan["id"], "A")
    b = _course(client, plan["id"], "B")
    bad_type = client.post(
        f"/api/v1/curriculum-plans/{plan['id']}/relations",
        json={"from_course_id": a["id"], "to_course_id": b["id"], "relation_type": "hard"},
    )
    assert bad_type.status_code == 400

    bad_term = client.post(
        f"/api/v1/curriculum-plans/{plan['id']}/courses",
        json={"name": "C", "offered_terms": ["autumn"]},
    )
    assert bad_term.status_code == 400
