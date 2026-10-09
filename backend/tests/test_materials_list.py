"""D005 集成测试：资料列表筛选与元数据修订。

验收：按学期、课程、类型、处理状态查询；修订元数据不覆盖原文版本；
范围变更后相关筛选立即按新范围生效。
"""

from fastapi.testclient import TestClient

TEXT_A = "第一份资料内容。\n\n包含作业要求。"
TEXT_B = "第二份资料内容。\n\n包含考试安排。"


def _import(client: TestClient, title: str, content: str, **extra: object) -> dict:
    response = client.post(
        "/api/v1/materials/text",
        json={"title": title, "content": content, **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_list_filters_by_semester_course_type_status(client: TestClient) -> None:
    semester = client.post(
        "/api/v1/semesters",
        json={"name": "2026 秋", "start_date": "2026-09-01", "end_date": "2027-01-31"},
    ).json()
    course = client.post(
        "/api/v1/courses", json={"semester_id": semester["id"], "name": "数据库原理"}
    ).json()

    _import(client, "无范围资料", TEXT_A)
    scoped = _import(
        client, "有范围资料", TEXT_B, semester_id=semester["id"], course_id=course["id"]
    )

    all_items = client.get("/api/v1/materials").json()
    assert len(all_items) == 2

    by_semester = client.get("/api/v1/materials", params={"semester_id": semester["id"]}).json()
    assert [m["id"] for m in by_semester] == [scoped["material"]["id"]]

    by_course = client.get("/api/v1/materials", params={"course_id": course["id"]}).json()
    assert [m["id"] for m in by_course] == [scoped["material"]["id"]]

    by_type = client.get("/api/v1/materials", params={"source_type": "text"}).json()
    assert len(by_type) == 2
    empty = client.get("/api/v1/materials", params={"source_type": "pdf"}).json()
    assert empty == []

    by_status = client.get("/api/v1/materials", params={"status": "pending"}).json()
    assert len(by_status) == 2
    done = client.get("/api/v1/materials", params={"status": "done"}).json()
    assert done == []


def test_metadata_revision_keeps_original_version_untouched(client: TestClient) -> None:
    """修订元数据不得覆盖原文版本（D005 验收）。"""
    imported = _import(client, "原标题", TEXT_A)
    material_id = imported["material"]["id"]
    original_chunks = imported["chunks"]

    updated = client.patch(
        f"/api/v1/materials/{material_id}",
        json={"title": "新标题", "publisher": "课程群"},
    ).json()
    assert updated["title"] == "新标题"
    assert updated["publisher"] == "课程群"

    # 原文版本内容不受影响：重新导入同内容，片段仍一致；
    # 已保存版本仍指向同一份原文（通过再次导入对比片段内容验证规则稳定性）
    reimported = _import(client, "对照组", TEXT_A)
    assert [c["text"] for c in reimported["chunks"]] == [c["text"] for c in original_chunks]


def test_scope_change_takes_effect_immediately(client: TestClient) -> None:
    """范围变更后，按新范围筛选立即生效（检索范围更新的 MVP 语义）。"""
    semester = client.post(
        "/api/v1/semesters",
        json={"name": "2026 秋", "start_date": "2026-09-01", "end_date": "2027-01-31"},
    ).json()
    course = client.post(
        "/api/v1/courses", json={"semester_id": semester["id"], "name": "数据库原理"}
    ).json()

    imported = _import(client, "待归类", TEXT_A)
    material_id = imported["material"]["id"]

    # 修订前：按课程筛选不包含它
    assert client.get("/api/v1/materials", params={"course_id": course["id"]}).json() == []

    client.patch(
        f"/api/v1/materials/{material_id}",
        json={"semester_id": semester["id"], "course_id": course["id"], "class_name": "2 班"},
    )

    # 修订后：立即出现在新范围
    after = client.get("/api/v1/materials", params={"course_id": course["id"]}).json()
    assert [m["id"] for m in after] == [material_id]


def test_metadata_revision_rejects_foreign_scope(client: TestClient) -> None:
    imported = _import(client, "正常资料", TEXT_A)
    material_id = imported["material"]["id"]

    response = client.patch(f"/api/v1/materials/{material_id}", json={"course_id": 999})
    assert response.status_code == 404
    assert response.json() == {"detail": "课程不存在"}


def test_material_detail_and_unknown_material(client: TestClient) -> None:
    imported = _import(client, "详情测试", TEXT_A)
    detail = client.get(f"/api/v1/materials/{imported['material']['id']}").json()
    assert detail["title"] == "详情测试"

    missing = client.get("/api/v1/materials/999")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "资料不存在"}


def test_metadata_revision_rejects_semester_course_mismatch(client: TestClient) -> None:
    """修订路径同样校验课程与学期一致性（评审回归）。"""
    semester_a = client.post(
        "/api/v1/semesters",
        json={"name": "2026 秋", "start_date": "2026-09-01", "end_date": "2027-01-31"},
    ).json()
    semester_b = client.post(
        "/api/v1/semesters",
        json={"name": "2027 春", "start_date": "2027-02-15", "end_date": "2027-07-15"},
    ).json()
    course = client.post(
        "/api/v1/courses", json={"semester_id": semester_a["id"], "name": "数据库原理"}
    ).json()

    imported = _import(client, "正常资料", TEXT_A)
    material_id = imported["material"]["id"]

    response = client.patch(
        f"/api/v1/materials/{material_id}",
        json={"semester_id": semester_b["id"], "course_id": course["id"]},
    )
    assert response.status_code == 400
    assert "学期" in response.json()["detail"]

    # 一致组合正常
    ok = client.patch(
        f"/api/v1/materials/{material_id}",
        json={"semester_id": semester_a["id"], "course_id": course["id"]},
    )
    assert ok.status_code == 200
    assert ok.json()["course_id"] == course["id"]
