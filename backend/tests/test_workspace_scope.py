"""W006 集成测试：对象读取、修改及关联的空间授权。

API 始终解析当前（默认）空间；第二个空间的对象通过 SQLAlchemy 直接写入测试库，
模拟"猜 ID"访问其他空间资源的场景。所有跨空间操作都必须以 404 拒绝，
且错误内容不得包含其他空间对象的信息。
"""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from conftest import TEST_DATABASE_URL
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from campusflow.infrastructure.db.models import (
    AvailabilitySlot,
    Course,
    FixedEvent,
    Semester,
    Task,
    Workspace,
)

TZ = ZoneInfo("Asia/Shanghai")


def _default_workspace_id(client: TestClient) -> int:
    # 通过业务接口触发默认空间创建并读取其 ID
    settings = client.get("/api/v1/settings").json()
    return settings["workspace_id"]


def _create_other_workspace_objects() -> dict:
    """直接在数据库中创建第二个空间及其课程/任务/日程/可用时段/学期。"""
    engine = create_engine(TEST_DATABASE_URL)
    with Session(engine) as session:
        other = Workspace(name="另一个空间")
        session.add(other)
        session.flush()
        semester = Semester(
            workspace_id=other.id,
            name="他人的 2026 秋",
            start_date=date(2026, 9, 1),
            end_date=date(2027, 1, 31),
        )
        session.add(semester)
        session.flush()
        course = Course(
            workspace_id=other.id,
            semester_id=semester.id,
            name="他人的数据库原理",
            code="CS999",
        )
        session.add(course)
        session.flush()
        task = Task(workspace_id=other.id, course_id=course.id, title="他人的任务")
        session.add(task)
        event = FixedEvent(
            workspace_id=other.id,
            course_id=course.id,
            title="他人的实验课",
            starts_at=datetime(2026, 10, 12, 14, 0, tzinfo=TZ),
            ends_at=datetime(2026, 10, 12, 16, 0, tzinfo=TZ),
        )
        session.add(event)
        slot = AvailabilitySlot(
            workspace_id=other.id, day_of_week=1, start_time=time(19, 0), end_time=time(21, 0)
        )
        session.add(slot)
        session.commit()
        return {
            "workspace_id": other.id,
            "semester_id": semester.id,
            "course_id": course.id,
            "task_id": task.id,
            "event_id": event.id,
            "slot_id": slot.id,
        }


@pytest.fixture
def other_ids(client: TestClient) -> dict:
    _default_workspace_id(client)  # 确保默认空间存在
    return _create_other_workspace_objects()


def test_cannot_read_course_from_other_workspace(client: TestClient, other_ids: dict) -> None:
    response = client.patch(f"/api/v1/courses/{other_ids['course_id']}", json={"name": "改名"})
    assert response.status_code == 404
    assert response.json() == {"detail": "课程不存在"}


def test_cannot_archive_semester_from_other_workspace(client: TestClient, other_ids: dict) -> None:
    response = client.patch(
        f"/api/v1/semesters/{other_ids['semester_id']}", json={"archived": True}
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "学期不存在"}


def test_cannot_create_course_under_other_workspace_semester(
    client: TestClient, other_ids: dict
) -> None:
    response = client.post(
        "/api/v1/courses",
        json={"semester_id": other_ids["semester_id"], "name": "寄生课程"},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "学期不存在"}


def test_cannot_read_or_modify_task_from_other_workspace(
    client: TestClient, other_ids: dict
) -> None:
    assert client.get(f"/api/v1/tasks/{other_ids['task_id']}").status_code == 404
    patched = client.patch(f"/api/v1/tasks/{other_ids['task_id']}", json={"title": "篡改"})
    assert patched.status_code == 404
    progress = client.patch(
        f"/api/v1/tasks/{other_ids['task_id']}/progress", json={"progress": "done"}
    )
    assert progress.status_code == 404
    history = client.get(f"/api/v1/tasks/{other_ids['task_id']}/progress-history")
    assert history.status_code == 404
    for response in (patched, progress, history):
        assert response.json() == {"detail": "任务不存在"}


def test_cannot_attach_task_to_other_workspace_course(client: TestClient, other_ids: dict) -> None:
    response = client.post(
        "/api/v1/tasks", json={"title": "寄生任务", "course_id": other_ids["course_id"]}
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "课程不存在"}


def test_cannot_manage_event_from_other_workspace(client: TestClient, other_ids: dict) -> None:
    patched = client.patch(
        f"/api/v1/fixed-events/{other_ids['event_id']}", json={"location": "篡改地点"}
    )
    assert patched.status_code == 404
    assert patched.json() == {"detail": "日程不存在"}
    assert client.delete(f"/api/v1/fixed-events/{other_ids['event_id']}").status_code == 404


def test_cannot_create_event_for_other_workspace_course(
    client: TestClient, other_ids: dict
) -> None:
    response = client.post(
        "/api/v1/fixed-events",
        json={
            "course_id": other_ids["course_id"],
            "title": "寄生日程",
            "starts_at": "2026-10-12T10:00:00",
            "ends_at": "2026-10-12T12:00:00",
        },
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "课程不存在"}


def test_cannot_delete_slot_from_other_workspace(client: TestClient, other_ids: dict) -> None:
    response = client.delete(f"/api/v1/availability-slots/{other_ids['slot_id']}")
    assert response.status_code == 404
    assert response.json() == {"detail": "可用时间段不存在"}


def test_lists_only_show_current_workspace(client: TestClient, other_ids: dict) -> None:
    # 本空间的正常数据
    client.post(
        "/api/v1/semesters",
        json={"name": "我的 2026 秋", "start_date": "2026-09-01", "end_date": "2027-01-31"},
    )
    semesters = client.get("/api/v1/semesters").json()
    assert all(s["name"] != "他人的 2026 秋" for s in semesters)
    courses = client.get("/api/v1/courses").json()
    assert all(c["name"] != "他人的数据库原理" for c in courses)
    tasks = client.get("/api/v1/tasks").json()
    assert all(t["title"] != "他人的任务" for t in tasks)
    events = client.get("/api/v1/fixed-events").json()
    assert all(e["title"] != "他人的实验课" for e in events)
    slots = client.get("/api/v1/availability-slots").json()
    assert slots == []


def test_error_responses_do_not_leak_other_workspace_info(
    client: TestClient, other_ids: dict
) -> None:
    """错误内容不得包含其他空间对象的名称、编号等任何信息。"""
    responses = [
        client.get(f"/api/v1/tasks/{other_ids['task_id']}"),
        client.patch(f"/api/v1/courses/{other_ids['course_id']}", json={"name": "x"}),
        client.patch(f"/api/v1/semesters/{other_ids['semester_id']}", json={"archived": True}),
    ]
    for response in responses:
        assert response.status_code == 404
        text = response.text
        assert "他人" not in text
        assert str(other_ids["workspace_id"]) not in text
        assert "CS999" not in text
