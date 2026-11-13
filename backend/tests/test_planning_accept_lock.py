"""#599 计划接受并发回归：同一学期两个草案并发接受只允许一个成为当前计划。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from conftest import TEST_DATABASE_URL
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from campusflow.application.planning import accept_draft
from campusflow.domain.errors import ConflictError
from campusflow.infrastructure.db.models import PlanDraft
from campusflow.infrastructure.repositories import build_repositories


def _task(client) -> None:
    response = client.post(
        "/api/v1/tasks",
        json={
            "title": "并发接受草案",
            "due_date": "2026-10-19",
            "due_time": "19:00:00",
            "priority": "high",
            "estimated_minutes": 60,
            "remaining_minutes": 60,
        },
    )
    assert response.status_code == 201, response.text


def _availability(client) -> None:
    response = client.post(
        "/api/v1/availability-slots",
        json={"day_of_week": 0, "start_time": "18:00:00", "end_time": "22:00:00"},
    )
    assert response.status_code == 201, response.text


def _generate(client) -> dict:
    response = client.post(
        "/api/v1/plans/drafts",
        json={
            "start_date": "2026-10-19",
            "end_date": "2026-10-20",
            "assumptions": {"buffer_minutes": 0},
        },
    )
    assert response.status_code == 201, response.text
    draft = response.json()
    assert draft["status"] == "draft"
    return draft


def test_concurrent_accept_keeps_single_current_plan(client) -> None:
    _task(client)
    _availability(client)
    first = _generate(client)
    second = _generate(client)
    assert first["id"] != second["id"]
    workspace = client.get("/api/v1/settings").json()["workspace_id"]

    engine = create_engine(TEST_DATABASE_URL)
    barrier = Barrier(2)

    def accept(draft_id: int) -> str:
        with Session(engine) as session:
            barrier.wait(timeout=5)
            try:
                accept_draft(
                    build_repositories(session),
                    workspace,
                    draft_id,
                    expected_version=1,
                )
                return "accepted"
            except ConflictError:
                session.rollback()
                return "conflict"

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = sorted(pool.map(accept, [first["id"], second["id"]]))
        assert outcomes == ["accepted", "conflict"]
        with Session(engine) as session:
            total = session.scalar(select(func.count()).select_from(PlanDraft))
            accepted = session.scalar(
                select(func.count()).select_from(PlanDraft).where(PlanDraft.status == "accepted")
            )
        assert total == 2
        assert accepted == 1
    finally:
        engine.dispose()
