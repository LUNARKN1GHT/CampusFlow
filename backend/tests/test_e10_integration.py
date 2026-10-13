"""E10：单用户空间、设置、时间、归档与重启读取的汇总 API 演示。"""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from campusflow.main import create_app


def test_e10_workspace_settings_archive_restart_and_restore(client: TestClient) -> None:
    settings = client.get("/api/v1/settings").json()
    workspace_id = settings.pop("workspace_id")
    settings.update(timezone="America/New_York", weekly_capacity_minutes=900)
    assert client.put("/api/v1/settings", json=settings).status_code == 200
    semester = client.post(
        "/api/v1/semesters",
        json={
            "name": "E10 集成学期",
            "start_date": "2026-09-01",
            "end_date": "2027-01-31",
        },
    ).json()
    course = client.post(
        "/api/v1/courses", json={"semester_id": semester["id"], "name": "集成课程"}
    ).json()
    task = client.post(
        "/api/v1/tasks",
        json={
            "course_id": course["id"],
            "title": "课程作业",
            "due_date": "2026-10-15",
            "estimated_minutes": 90,
        },
    ).json()
    event = client.post(
        "/api/v1/fixed-events",
        json={
            "course_id": course["id"],
            "title": "跨日实验",
            "starts_at": "2026-10-08T23:30:00",
            "ends_at": "2026-10-09T00:30:00",
        },
    ).json()
    assert datetime.fromisoformat(event["starts_at"]) == datetime(2026, 10, 9, 3, 30, tzinfo=UTC)
    assert (
        client.patch(f"/api/v1/semesters/{semester['id']}", json={"archived": True}).status_code
        == 200
    )
    assert client.get("/api/v1/semesters").json() == []
    configuration = client.app.state.settings
    with TestClient(create_app(configuration)) as restarted:
        assert restarted.get("/api/v1/tasks").status_code == 401
        assert (
            restarted.post(
                "/api/v1/auth/login",
                json={
                    "username": configuration.local_username,
                    "password": configuration.local_password,
                },
            ).status_code
            == 200
        )
        saved = restarted.get("/api/v1/settings").json()
        assert saved["workspace_id"] == workspace_id
        assert saved["weekly_capacity_minutes"] == 900
        assert saved["timezone"] == "America/New_York"
        assert restarted.get("/api/v1/tasks", params={"semester_id": semester["id"]}).json() == [
            task
        ]
        assert task["due_time"] is None  # 日期精度 DDL 不能补造成确定时刻
        occurrences = restarted.get(
            "/api/v1/fixed-events/occurrences",
            params={
                "semester_id": semester["id"],
                "start_date": "2026-10-08",
                "end_date": "2026-10-08",
            },
        ).json()
        assert len(occurrences) == 1
        assert occurrences[0]["starts_at"] == "2026-10-08T23:30:00-04:00"
        assert (
            restarted.patch(
                f"/api/v1/semesters/{semester['id']}", json={"archived": False}
            ).status_code
            == 200
        )
        assert restarted.get("/api/v1/semesters").json()[0]["id"] == semester["id"]
        assert restarted.get(f"/api/v1/tasks/{task['id']}").json() == task
        assert restarted.post("/api/v1/auth/logout").status_code == 204
        assert restarted.get("/api/v1/settings").status_code == 401
