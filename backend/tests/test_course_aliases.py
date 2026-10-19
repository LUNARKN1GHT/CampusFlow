"""C006：课程别名与唯一匹配规则（真实 PostgreSQL 测试库）。"""

from fastapi.testclient import TestClient


def _plan(client: TestClient) -> dict:
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


def _course(client: TestClient, plan_id: int, name: str) -> dict:
    response = client.post(f"/api/v1/curriculum-plans/{plan_id}/courses", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def _alias(client: TestClient, plan_id: int, course_id: int, alias: str, **scope) -> dict:
    response = client.post(
        f"/api/v1/curriculum-plans/{plan_id}/aliases",
        json={"curriculum_course_id": course_id, "alias": alias, **scope},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _resolve(client: TestClient, plan_id: int, name: str, **scope):
    return client.get(
        f"/api/v1/curriculum-plans/{plan_id}/aliases/resolve",
        params={"name": name, **scope},
    )


def test_alias_resolves_uniquely_to_curriculum_course(client: TestClient) -> None:
    plan = _plan(client)
    course = _course(client, plan["id"], "数据结构与算法")
    _alias(client, plan["id"], course["id"], "数据结构")

    result = _resolve(client, plan["id"], "数据结构")
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["status"] == "matched"
    assert body["matched_by"] == "alias"
    assert [item["id"] for item in body["matches"]] == [course["id"]]
    assert "不自动合并" in body["note"]


def test_alias_colliding_with_other_course_is_ambiguous(client: TestClient) -> None:
    plan = _plan(client)
    first = _course(client, plan["id"], "数据库系统")
    _course(client, plan["id"], "数据库")
    _alias(client, plan["id"], first["id"], "数据库")

    body = _resolve(client, plan["id"], "数据库").json()
    assert body["status"] == "ambiguous"
    assert len(body["matches"]) == 2


def test_scoped_alias_is_not_used_without_matching_scope(client: TestClient) -> None:
    plan = _plan(client)
    semester = client.post(
        "/api/v1/semesters",
        json={"name": "2026 秋", "start_date": "2026-09-01", "end_date": "2027-01-31"},
    ).json()
    current = _course(client, plan["id"], "数据库系统")
    other = _course(client, plan["id"], "数据库原理")
    _alias(
        client,
        plan["id"],
        current["id"],
        "数据库",
        class_name="1 班",
        semester_id=semester["id"],
    )

    unscoped = _resolve(client, plan["id"], "数据库").json()
    assert unscoped["status"] == "not_found"

    scoped = _resolve(
        client,
        plan["id"],
        "数据库",
        class_name="1 班",
        semester_id=semester["id"],
    ).json()
    assert scoped["status"] == "matched"
    assert scoped["matches"][0]["id"] == current["id"]
    assert scoped["matches"][0]["id"] != other["id"]


def test_alias_maintenance_does_not_merge_real_courses(client: TestClient) -> None:
    plan = _plan(client)
    semester = client.post(
        "/api/v1/semesters",
        json={"name": "2026 秋", "start_date": "2026-09-01", "end_date": "2027-01-31"},
    ).json()
    for name in ("数据库系统", "数据库原理"):
        client.post(
            "/api/v1/courses",
            json={"semester_id": semester["id"], "name": name, "class_name": "1 班"},
        )
    course = _course(client, plan["id"], "数据库系统")
    _alias(client, plan["id"], course["id"], "DB")
    _alias(client, plan["id"], course["id"], "数据库原理")

    courses = client.get("/api/v1/courses", params={"semester_id": semester["id"]}).json()
    assert len(courses) == 2
    assert {item["name"] for item in courses} == {"数据库系统", "数据库原理"}


def test_duplicate_alias_for_same_course_is_rejected(client: TestClient) -> None:
    plan = _plan(client)
    course = _course(client, plan["id"], "数据库系统")
    _alias(client, plan["id"], course["id"], "DB")
    duplicate = client.post(
        f"/api/v1/curriculum-plans/{plan['id']}/aliases",
        json={"curriculum_course_id": course["id"], "alias": "DB"},
    )
    assert duplicate.status_code == 400


def test_alias_cannot_target_course_from_other_plan(client: TestClient) -> None:
    first = _plan(client)
    other = client.post(
        "/api/v1/curriculum-plans",
        json={
            "title": "培养方案",
            "major": "软件工程",
            "grade_year": 2024,
            "version_label": "2024 版",
        },
    ).json()
    foreign = _course(client, other["id"], "数据库系统")
    response = client.post(
        f"/api/v1/curriculum-plans/{first['id']}/aliases",
        json={"curriculum_course_id": foreign["id"], "alias": "DB"},
    )
    assert response.status_code == 400


def test_alias_can_be_deleted(client: TestClient) -> None:
    plan = _plan(client)
    course = _course(client, plan["id"], "数据库系统")
    alias = _alias(client, plan["id"], course["id"], "DB")
    assert (
        client.delete(f"/api/v1/curriculum-plans/{plan['id']}/aliases/{alias['id']}").status_code
        == 204
    )
    assert _resolve(client, plan["id"], "DB").json()["status"] == "not_found"
