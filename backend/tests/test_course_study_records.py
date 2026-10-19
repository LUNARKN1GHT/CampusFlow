"""C007：已修、在修与待修状态维护（真实 PostgreSQL 测试库）。"""

from fastapi.testclient import TestClient

from campusflow.infrastructure.db.models import (
    CourseStudyRecord,
    CurriculumCourse,
    CurriculumPlan,
    Workspace,
)


def _plan(client: TestClient, version: str = "2024 版") -> dict:
    response = client.post(
        "/api/v1/curriculum-plans",
        json={
            "title": "计算机科学与技术培养方案",
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


def _semester(
    client: TestClient,
    name: str,
    start_date: str = "2026-09-01",
    end_date: str = "2027-01-31",
) -> dict:
    response = client.post(
        "/api/v1/semesters",
        json={"name": name, "start_date": start_date, "end_date": end_date},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _record(
    client: TestClient,
    plan_id: int,
    course_id: int,
    *,
    status: str = "planned",
    semester_id: int | None = None,
    **values,
):
    return client.post(
        f"/api/v1/curriculum-plans/{plan_id}/study-records",
        json={
            "curriculum_course_id": course_id,
            "status": status,
            "semester_id": semester_id,
            **values,
        },
    )


def _summary(client: TestClient, plan_id: int) -> dict:
    response = client.get(f"/api/v1/curriculum-plans/{plan_id}/study-status")
    assert response.status_code == 200, response.text
    return response.json()


def _entry(summary: dict, course_id: int) -> dict:
    return next(
        entry for entry in summary["courses"] if entry["curriculum_course_id"] == course_id
    )


def test_status_change_does_not_fabricate_credits_or_grade(client: TestClient) -> None:
    plan = _plan(client)
    course = _course(client, plan["id"], "数据结构", credits=4)
    created = _record(client, plan["id"], course["id"], status="planned")
    assert created.status_code == 201, created.text
    assert created.json()["grade"] is None
    assert created.json()["credits_earned"] is None

    updated = client.patch(
        f"/api/v1/curriculum-plans/{plan['id']}/study-records/{created.json()['id']}",
        json={"status": "completed"},
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["status"] == "completed"
    # 课程自身有 4 学分，但修改状态不得据此推断已得学分或成绩。
    assert body["grade"] is None
    assert body["credits_earned"] is None

    entry = _entry(_summary(client, plan["id"]), course["id"])
    assert entry["status"] == "completed"
    assert entry["records"][0]["grade"] is None
    assert entry["records"][0]["credits_earned"] is None
    assert "credits_earned" not in entry
    assert "grade" not in entry


def test_status_change_keeps_user_provided_values(client: TestClient) -> None:
    plan = _plan(client)
    course = _course(client, plan["id"], "高等数学", credits=5)
    created = _record(
        client,
        plan["id"],
        course["id"],
        status="in_progress",
        grade="A-",
        credits_earned=2.5,
        note="用户自填",
    )
    assert created.status_code == 201, created.text

    updated = client.patch(
        f"/api/v1/curriculum-plans/{plan['id']}/study-records/{created.json()['id']}",
        json={"status": "completed"},
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["grade"] == "A-"
    assert body["credits_earned"] == 2.5
    assert body["note"] == "用户自填"


def test_repeat_study_across_semesters_is_kept_separately(client: TestClient) -> None:
    plan = _plan(client)
    course = _course(client, plan["id"], "大学物理", credits=3)
    first_term = _semester(client, "2025 秋")
    second_term = _semester(client, "2026 秋", "2026-09-01", "2027-01-31")

    first = _record(
        client,
        plan["id"],
        course["id"],
        status="completed",
        semester_id=first_term["id"],
        grade="D",
    )
    second = _record(
        client,
        plan["id"],
        course["id"],
        status="completed",
        semester_id=second_term["id"],
        grade="B+",
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text

    listed = client.get(f"/api/v1/curriculum-plans/{plan['id']}/study-records")
    assert listed.status_code == 200, listed.text
    assert len(listed.json()) == 2

    entry = _entry(_summary(client, plan["id"]), course["id"])
    assert entry["status"] == "completed"
    assert entry["record_count"] == 2
    assert entry["repeat_record_count"] == 1
    assert set(entry["completed_semester_ids"]) == {first_term["id"], second_term["id"]}
    assert "重复修读" in entry["note"]
    assert {record["grade"] for record in entry["records"]} == {"D", "B+"}


def test_duplicate_same_term_and_status_is_rejected(client: TestClient) -> None:
    plan = _plan(client)
    course = _course(client, plan["id"], "线性代数")
    term = _semester(client, "2026 秋")

    first = _record(client, plan["id"], course["id"], status="planned", semester_id=term["id"])
    assert first.status_code == 201, first.text

    duplicate = _record(
        client, plan["id"], course["id"], status="planned", semester_id=term["id"]
    )
    assert duplicate.status_code == 400
    assert "已有修读记录" in duplicate.text

    # 同一学期但状态不同是不同事实，允许保存。
    different_status = _record(
        client, plan["id"], course["id"], status="in_progress", semester_id=term["id"]
    )
    assert different_status.status_code == 201, different_status.text

    # 未填写学期时，同一课程与状态同样只允许一条，不能靠 NULL 绕过约束。
    other = _course(client, plan["id"], "概率论")
    no_term = _record(client, plan["id"], other["id"], status="planned")
    assert no_term.status_code == 201, no_term.text
    duplicate_no_term = _record(client, plan["id"], other["id"], status="planned")
    assert duplicate_no_term.status_code == 400
    assert "已有修读记录" in duplicate_no_term.text


def test_unknown_status_is_derived_from_no_records(client: TestClient) -> None:
    plan = _plan(client)
    planned_course = _course(client, plan["id"], "离散数学")
    empty_course = _course(client, plan["id"], "编译原理")
    _record(client, plan["id"], planned_course["id"], status="planned")

    summary = _summary(client, plan["id"])
    planned_entry = _entry(summary, planned_course["id"])
    unknown_entry = _entry(summary, empty_course["id"])
    assert planned_entry["status"] == "planned"
    assert planned_entry["record_count"] == 1
    assert unknown_entry["status"] == "unknown"
    assert unknown_entry["record_count"] == 0
    assert unknown_entry["records"] == []
    assert "没有修读记录" in unknown_entry["note"]
    assert "未知不代表未修或已修" in unknown_entry["note"]
    assert summary["planned_count"] == 1
    assert summary["unknown_count"] == 1
    assert "unknown 表示没有记录" in summary["note"]

    # unknown 只是派生展示值，不能写入数据库当作猜测状态。
    rejected = _record(client, plan["id"], empty_course["id"], status="unknown")
    assert rejected.status_code == 400
    assert "修读状态" in rejected.text


def test_cross_plan_course_is_rejected(client: TestClient) -> None:
    first = _plan(client, "2024 版")
    second = _plan(client, "2025 版")
    local = _course(client, first["id"], "数据结构")
    foreign = _course(client, second["id"], "操作系统")

    rejected = _record(client, first["id"], foreign["id"], status="planned")
    assert rejected.status_code == 400
    assert "不属于该培养方案" in rejected.text

    foreign_record = _record(client, second["id"], foreign["id"], status="completed")
    assert foreign_record.status_code == 201, foreign_record.text
    record_id = foreign_record.json()["id"]

    listed = client.get(f"/api/v1/curriculum-plans/{first['id']}/study-records")
    assert listed.status_code == 200
    assert listed.json() == []

    patched = client.patch(
        f"/api/v1/curriculum-plans/{first['id']}/study-records/{record_id}",
        json={"status": "in_progress"},
    )
    assert patched.status_code == 400
    assert "不属于该培养方案" in patched.text

    deleted = client.delete(
        f"/api/v1/curriculum-plans/{first['id']}/study-records/{record_id}"
    )
    assert deleted.status_code == 400
    # 另一种方案里的记录没有被误删。
    assert len(
        client.get(f"/api/v1/curriculum-plans/{second['id']}/study-records").json()
    ) == 1
    assert _entry(_summary(client, first["id"]), local["id"])["status"] == "unknown"


def test_aggregate_precedence_is_completed_in_progress_planned_unknown(
    client: TestClient,
) -> None:
    plan = _plan(client)
    course = _course(client, plan["id"], "数据库系统")
    first = _semester(client, "2025 秋")
    second = _semester(client, "2026 春", "2026-02-20", "2026-06-30")
    third = _semester(client, "2026 秋")

    planned = _record(client, plan["id"], course["id"], status="planned", semester_id=third["id"])
    _record(client, plan["id"], course["id"], status="in_progress", semester_id=second["id"])
    assert _entry(_summary(client, plan["id"]), course["id"])["status"] == "in_progress"

    completed = _record(
        client, plan["id"], course["id"], status="completed", semester_id=first["id"]
    )
    entry = _entry(_summary(client, plan["id"]), course["id"])
    assert entry["status"] == "completed"
    assert entry["record_count"] == 3
    assert entry["completed_semester_ids"] == [first["id"]]

    client.delete(
        f"/api/v1/curriculum-plans/{plan['id']}/study-records/{completed.json()['id']}"
    )
    assert _entry(_summary(client, plan["id"]), course["id"])["status"] == "in_progress"

    client.delete(
        f"/api/v1/curriculum-plans/{plan['id']}/study-records/"
        f"{_entry(_summary(client, plan['id']), course['id'])['records'][0]['id']}"
    )
    # 剩下 planned 记录时状态回落到待修，而不是被推断为未知。
    assert _entry(_summary(client, plan["id"]), course["id"])["status"] == "planned"

    client.delete(
        f"/api/v1/curriculum-plans/{plan['id']}/study-records/{planned.json()['id']}"
    )
    assert _entry(_summary(client, plan["id"]), course["id"])["status"] == "unknown"


def test_switch_preview_counts_study_records(client: TestClient) -> None:
    current = _plan(client, "2023 版")
    target = _plan(client, "2024 版")
    course = _course(client, current["id"], "计算机组成原理")
    created = _record(client, current["id"], course["id"], status="in_progress")
    assert created.status_code == 201, created.text
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
    assert preview.json()["study_record_count"] == 1


def test_records_are_isolated_by_workspace(client: TestClient) -> None:
    plan = _plan(client)
    course = _course(client, plan["id"], "软件工程")
    own = _record(client, plan["id"], course["id"], status="planned")
    assert own.status_code == 201, own.text

    with client.app.state.session_factory() as session:
        other = Workspace(name="他人空间")
        session.add(other)
        session.flush()
        other_plan = CurriculumPlan(
            workspace_id=other.id,
            title="软件工程培养方案",
            major="软件工程",
            grade_year=2024,
            version_label="2024 版",
        )
        session.add(other_plan)
        session.flush()
        other_course = CurriculumCourse(
            workspace_id=other.id,
            curriculum_plan_id=other_plan.id,
            name="软件测试",
            offered_terms=[],
        )
        session.add(other_course)
        session.flush()
        other_record = CourseStudyRecord(
            workspace_id=other.id,
            curriculum_course_id=other_course.id,
            status="completed",
            credits_earned=3,
        )
        session.add(other_record)
        session.commit()
        other_plan_id = other_plan.id
        other_record_id = other_record.id

    assert (
        client.get(f"/api/v1/curriculum-plans/{other_plan_id}/study-records").status_code
        == 404
    )
    assert (
        client.get(f"/api/v1/curriculum-plans/{other_plan_id}/study-status").status_code
        == 404
    )
    assert (
        client.patch(
            f"/api/v1/curriculum-plans/{plan['id']}/study-records/{other_record_id}",
            json={"status": "in_progress"},
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/api/v1/curriculum-plans/{plan['id']}/study-records/{other_record_id}"
        ).status_code
        == 404
    )

    summary = _summary(client, plan["id"])
    record_ids = [
        record["id"] for entry in summary["courses"] for record in entry["records"]
    ]
    assert record_ids == [own.json()["id"]]
    assert summary["completed_count"] == 0
    assert summary["planned_count"] == 1
