"""W009：非删除归档、历史查询、恢复和空间隔离。"""

from datetime import date

import pytest
from conftest import TEST_DATABASE_URL
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from campusflow.infrastructure.db.models import Semester, Workspace


def _create(client: TestClient, path: str, payload: dict) -> dict:
    response = client.post(f"/api/v1/{path}", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _semester(client: TestClient, name: str) -> dict:
    return _create(
        client,
        "semesters",
        {
            "name": name,
            "start_date": "2026-09-01",
            "end_date": "2027-01-31",
        },
    )


def test_archive_restore_preserves_records_and_scoped_queries(client: TestClient) -> None:
    old = _semester(client, "历史学期")
    current = _semester(client, "当前学期")
    ids = {}
    for semester in (old, current):
        course = _create(
            client, "courses", {"semester_id": semester["id"], "name": semester["name"]}
        )
        task = _create(
            client,
            "tasks",
            {
                "course_id": course["id"],
                "title": "未完成作业",
                "due_date": "2026-10-20",
                "estimated_minutes": 90,
                "remaining_minutes": 60,
            },
        )
        event = _create(
            client,
            "fixed-events",
            {
                "course_id": course["id"],
                "title": "每周讨论",
                "starts_at": "2026-10-08T10:00:00",
                "ends_at": "2026-10-08T11:00:00",
                "recurrence": "weekly",
                "repeat_until": "2026-10-22",
            },
        )
        ids[semester["id"]] = {
            "courses": course["id"],
            "tasks": task["id"],
            "fixed-events": event["id"],
        }
    old_task_id = ids[old["id"]]["tasks"]
    response = client.patch(
        f"/api/v1/tasks/{old_task_id}/progress",
        json={
            "progress": "blocked",
            "reason": "等待老师确认",
        },
    )
    assert response.status_code == 200
    personal = _create(client, "tasks", {"title": "个人待办"})
    _create(
        client,
        "fixed-events",
        {"title": "个人安排", "starts_at": "2026-10-08T12:00:00", "ends_at": "2026-10-08T13:00:00"},
    )
    _create(
        client,
        "availability-slots",
        {"day_of_week": 0, "start_time": "19:00:00", "end_time": "21:00:00"},
    )
    paths = [
        "courses",
        "tasks",
        "fixed-events",
        "availability-slots",
        f"tasks/{old_task_id}",
        f"tasks/{old_task_id}/progress-history",
    ]
    before = {path: client.get(f"/api/v1/{path}").json() for path in paths}
    for archived in (True, True, False):
        assert (
            client.patch(f"/api/v1/semesters/{old['id']}", json={"archived": archived}).status_code
            == 200
        )
        active = client.get("/api/v1/semesters").json()
        assert {item["id"] for item in active} == (
            {current["id"]} if archived else {old["id"], current["id"]}
        )
        all_semesters = client.get("/api/v1/semesters", params={"include_archived": True}).json()
        assert len(all_semesters) == 2
        assert (
            next(item for item in all_semesters if item["id"] == old["id"])["archived"] is archived
        )
        for path in paths:
            assert client.get(f"/api/v1/{path}").json() == before[path]
        for semester in (old, current):
            for path in ("courses", "tasks", "fixed-events"):
                scoped = client.get(f"/api/v1/{path}", params={"semester_id": semester["id"]})
                assert scoped.status_code == 200, scoped.text
                assert [item["id"] for item in scoped.json()] == [ids[semester["id"]][path]]
            instances = client.get(
                "/api/v1/fixed-events/occurrences",
                params={
                    "semester_id": semester["id"],
                    "start_date": "2026-10-08",
                    "end_date": "2026-10-22",
                },
            )
            assert instances.status_code == 200
            assert len(instances.json()) == 3
            assert {item["source_event_id"] for item in instances.json()} == {
                ids[semester["id"]]["fixed-events"]
            }
        assert (
            client.get(
                "/api/v1/tasks", params={"semester_id": old["id"], "progress": "not_started"}
            ).json()
            == []
        )
        assert (
            client.get(
                "/api/v1/tasks",
                params={"semester_id": old["id"], "course_id": ids[current["id"]]["courses"]},
            ).json()
            == []
        )
        assert personal["id"] in {item["id"] for item in client.get("/api/v1/tasks").json()}


@pytest.mark.parametrize("other_space", [False, True])
def test_semester_query_does_not_leak_other_space(client: TestClient, other_space: bool) -> None:
    client.get("/api/v1/settings")
    semester_id = 99999
    if other_space:
        with Session(create_engine(TEST_DATABASE_URL)) as session:
            workspace = Workspace(name="另一空间")
            session.add(workspace)
            session.flush()
            semester = Semester(
                workspace_id=workspace.id,
                name="私密学期",
                start_date=date(2026, 9, 1),
                end_date=date(2027, 1, 31),
                archived=True,
            )
            session.add(semester)
            session.commit()
            semester_id = semester.id
    for path in ("courses", "tasks", "fixed-events", "fixed-events/occurrences"):
        params = {"semester_id": semester_id}
        if path.endswith("occurrences"):
            params.update(start_date="2026-10-08", end_date="2026-10-22")
        response = client.get(f"/api/v1/{path}", params=params)
        assert response.status_code == 404
        assert response.json() == {"detail": "学期不存在"}
