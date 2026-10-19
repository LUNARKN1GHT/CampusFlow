"""C002：培养方案版本与个人方案信息（真实 PostgreSQL 测试库）。"""

from fastapi.testclient import TestClient


def _plan(client: TestClient, version: str = "2024 版") -> dict:
    response = client.post(
        "/api/v1/curriculum-plans",
        json={
            "title": "计算机科学与技术培养方案",
            "major": "计算机科学与技术",
            "grade_year": 2024,
            "version_label": version,
            "credit_limit_per_term": 25,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_plan_versions_are_not_mixed(client: TestClient) -> None:
    older = _plan(client, "2023 版")
    newer = _plan(client, "2024 版")
    plans = client.get("/api/v1/curriculum-plans").json()
    assert {plan["version_label"] for plan in plans} == {"2023 版", "2024 版"}
    assert older["id"] != newer["id"]

    duplicate = client.post(
        "/api/v1/curriculum-plans",
        json={
            "title": "重复方案",
            "major": "计算机科学与技术",
            "grade_year": 2024,
            "version_label": "2024 版",
        },
    )
    assert duplicate.status_code == 400
    assert "已存在" in duplicate.text


def test_profile_reports_missing_fields_until_complete(client: TestClient) -> None:
    initial = client.get("/api/v1/learning-profile")
    assert initial.status_code == 200
    body = initial.json()
    assert body["missing_fields"] == ["major", "grade_year", "curriculum_plan_id"]
    assert body["current_plan"] is None

    plan = _plan(client)
    updated = client.put(
        "/api/v1/learning-profile",
        json={
            "major": "计算机科学与技术",
            "grade_year": 2024,
            "curriculum_plan_id": plan["id"],
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["missing_fields"] == []
    assert updated.json()["current_plan"]["version_label"] == "2024 版"


def test_profile_rejects_mixing_grade_or_major(client: TestClient) -> None:
    plan = _plan(client)
    wrong_grade = client.put(
        "/api/v1/learning-profile",
        json={"major": "计算机科学与技术", "grade_year": 2023, "curriculum_plan_id": plan["id"]},
    )
    assert wrong_grade.status_code == 400
    assert "年级" in wrong_grade.text

    wrong_major = client.put(
        "/api/v1/learning-profile",
        json={"major": "软件工程", "grade_year": 2024, "curriculum_plan_id": plan["id"]},
    )
    assert wrong_major.status_code == 400
    assert "专业" in wrong_major.text


def test_switch_preview_keeps_old_plan_data(client: TestClient) -> None:
    current = _plan(client, "2023 版")
    target = _plan(client, "2024 版")
    saved = client.put(
        "/api/v1/learning-profile",
        json={
            "major": "计算机科学与技术",
            "grade_year": 2024,
            "curriculum_plan_id": current["id"],
        },
    )
    assert saved.status_code == 200, saved.text
    preview = client.get(
        "/api/v1/learning-profile/switch-preview", params={"plan_id": target["id"]}
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["current_plan"]["id"] == current["id"]
    assert body["target_plan"]["id"] == target["id"]
    assert body["relation_count"] == 0
    assert "不会被删除" in body["note"]

    # 预览不改变当前方案
    profile = client.get("/api/v1/learning-profile").json()
    assert profile["curriculum_plan_id"] == current["id"]


def test_plan_update_rejects_invalid_credit_limit(client: TestClient) -> None:
    plan = _plan(client)
    response = client.put(
        f"/api/v1/curriculum-plans/{plan['id']}",
        json={
            "title": plan["title"],
            "major": plan["major"],
            "grade_year": plan["grade_year"],
            "version_label": plan["version_label"],
            "credit_limit_per_term": 0,
        },
    )
    assert response.status_code == 422
