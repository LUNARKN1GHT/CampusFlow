"""学期与课程 API 集成测试（真实 PostgreSQL 测试库）。"""

from fastapi.testclient import TestClient


def _create_semester(client: TestClient, name: str = "2026 秋") -> dict:
    response = client.post(
        "/api/v1/semesters",
        json={"name": name, "start_date": "2026-09-01", "end_date": "2027-01-31"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_course(client: TestClient, semester_id: int, name: str = "数据库原理") -> dict:
    response = client.post(
        "/api/v1/courses",
        json={
            "semester_id": semester_id,
            "name": name,
            "code": "CS301",
            "teacher": "王老师",
            "class_name": "1 班",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_semester_create_list_archive(client: TestClient) -> None:
    semester = _create_semester(client)
    assert semester["name"] == "2026 秋"
    assert semester["archived"] is False

    listed = client.get("/api/v1/semesters").json()
    assert [s["id"] for s in listed] == [semester["id"]]

    archived = client.patch(f"/api/v1/semesters/{semester['id']}", json={"archived": True}).json()
    assert archived["archived"] is True

    # 归档后默认隐藏，include_archived 才可见
    assert client.get("/api/v1/semesters").json() == []
    shown = client.get("/api/v1/semesters", params={"include_archived": True}).json()
    assert [s["id"] for s in shown] == [semester["id"]]


def test_semester_rejects_invalid_range(client: TestClient) -> None:
    response = client.post(
        "/api/v1/semesters",
        json={"name": "倒着走", "start_date": "2026-09-01", "end_date": "2026-08-01"},
    )
    assert response.status_code == 400


def test_course_create_requires_semester(client: TestClient) -> None:
    response = client.post(
        "/api/v1/courses", json={"semester_id": 999, "name": "不存在的学期里的课"}
    )
    assert response.status_code == 404


def test_course_list_filter_and_update(client: TestClient) -> None:
    semester = _create_semester(client)
    other_semester = _create_semester(client, "2027 春")
    course = _create_course(client, semester["id"])
    _create_course(client, other_semester["id"], "编译原理")

    filtered = client.get("/api/v1/courses", params={"semester_id": semester["id"]}).json()
    assert [c["id"] for c in filtered] == [course["id"]]

    updated = client.patch(
        f"/api/v1/courses/{course['id']}", json={"name": "高级数据库", "teacher": None}
    ).json()
    assert updated["name"] == "高级数据库"
    assert updated["teacher"] is None
    # 未提交的字段保持不变
    assert updated["code"] == "CS301"


def test_course_update_missing_course(client: TestClient) -> None:
    response = client.patch("/api/v1/courses/999", json={"name": "x"})
    assert response.status_code == 404
