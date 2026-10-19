"""A012：提醒幂等键与重复调度保护。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, time, timedelta, timezone

from conftest import TEST_DATABASE_URL
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select

from campusflow.application.reminders import sync_reminders
from campusflow.domain.reminders import (
    MAX_DEDUPE_KEY_LENGTH,
    deadline_dedupe_key,
    task_review_dedupe_key,
    workspace_review_dedupe_key,
)
from campusflow.infrastructure.db.models import ReminderTrigger
from campusflow.infrastructure.repositories import build_repositories

SHANGHAI = timezone(timedelta(hours=8))


def _task(client: TestClient, **overrides) -> dict:
    payload = {
        "title": "幂等任务",
        "due_date": "2026-11-10",
        "due_time": "18:00:00",
        "estimated_minutes": 60,
        "remaining_minutes": 60,
    }
    payload.update(overrides)
    response = client.post("/api/v1/tasks", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _rule(client: TestClient, **payload) -> dict:
    response = client.post("/api/v1/reminders/rules", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _reminders(client: TestClient) -> list[dict]:
    response = client.get("/api/v1/reminders")
    assert response.status_code == 200, response.text
    return response.json()


def test_deadline_key_is_stable_and_window_specific() -> None:
    window = datetime(2026, 11, 9, 18, 0, tzinfo=SHANGHAI)
    keyword = {
        "task_id": 7,
        "rule_id": 3,
        "rule_version": 2,
        "due_date": date(2026, 11, 10),
        "due_time": time(18, 0),
    }
    key = deadline_dedupe_key(**keyword, window_start=window)
    assert key == deadline_dedupe_key(**keyword, window_start=window)
    assert key != deadline_dedupe_key(**keyword, window_start=window + timedelta(minutes=30))
    assert key != deadline_dedupe_key(**{**keyword, "rule_version": 3}, window_start=window)
    assert key != deadline_dedupe_key(**{**keyword, "due_time": None}, window_start=window)


def test_review_keys_distinguish_task_and_day() -> None:
    ahead = date(2026, 11, 10)
    assert task_review_dedupe_key(
        task_id=1, due_date=ahead, rule_version=1
    ) == task_review_dedupe_key(task_id=1, due_date=ahead, rule_version=1)
    assert task_review_dedupe_key(
        task_id=1, due_date=ahead, rule_version=1
    ) != task_review_dedupe_key(task_id=1, due_date=ahead, rule_version=2)
    assert workspace_review_dedupe_key(day=ahead) != workspace_review_dedupe_key(
        day=ahead + timedelta(days=1)
    )


def test_dedupe_key_length_is_bounded() -> None:
    key = deadline_dedupe_key(
        task_id=10**18,
        rule_id=10**18,
        rule_version=10**9,
        due_date=date(2026, 11, 10),
        due_time=time(23, 59, 59, 999999),
        window_start=datetime(2026, 11, 9, tzinfo=SHANGHAI),
    )
    assert len(key) <= MAX_DEDUPE_KEY_LENGTH


def test_repeated_cycles_keep_one_trigger(client: TestClient) -> None:
    task = _task(client)
    _rule(client, task_id=task["id"], advance_minutes=60)

    first = _reminders(client)
    second = _reminders(client)
    assert [item["id"] for item in first] == [item["id"] for item in second]

    factory = client.app.state.session_factory
    with factory() as session:
        repos = build_repositories(session)
        workspace_id = repos.tasks.get(task["id"]).workspace_id
        sync_reminders(repos, workspace_id, now=datetime(2026, 11, 9, 10, tzinfo=UTC))
        sync_reminders(repos, workspace_id, now=datetime(2026, 11, 9, 12, tzinfo=UTC))
        session.commit()
        assert len(repos.reminders.list_triggers(workspace_id)) == 1


def test_concurrent_inserts_produce_one_row(client: TestClient) -> None:
    task = _task(client)
    factory = client.app.state.session_factory
    with factory() as session:
        repos = build_repositories(session)
        workspace_id = repos.tasks.get(task["id"]).workspace_id

    row = {
        "workspace_id": workspace_id,
        "rule_id": None,
        "task_id": task["id"],
        "kind": "deadline",
        "dedupe_key": "deadline:concurrency:test",
        "scheduled_for": datetime(2026, 11, 9, 18, tzinfo=SHANGHAI),
        "message": "并发写入测试",
        "payload": {"task_id": task["id"]},
    }

    def insert() -> None:
        with factory() as session:
            repos = build_repositories(session)
            repos.reminders.upsert_triggers([dict(row)])
            session.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: insert(), range(2)))

    engine = create_engine(TEST_DATABASE_URL)
    with engine.connect() as connection:
        count = connection.execute(
            select(func.count())
            .select_from(ReminderTrigger)
            .where(ReminderTrigger.dedupe_key == row["dedupe_key"])
        ).scalar_one()
    assert count == 1


def test_failed_sync_rolls_back_and_retry_succeeds(client: TestClient) -> None:
    task = _task(client)
    _rule(client, task_id=task["id"], advance_minutes=60)

    factory = client.app.state.session_factory
    with factory() as session:
        repos = build_repositories(session)
        workspace_id = repos.tasks.get(task["id"]).workspace_id
        original = repos.reminders.upsert_triggers

        def fail_once(rows):
            raise RuntimeError("模拟调度失败")

        repos.reminders.upsert_triggers = fail_once
        try:
            sync_reminders(repos, workspace_id)
        except RuntimeError:
            session.rollback()
        else:
            raise AssertionError("预期第一次同步失败")

        repos.reminders.upsert_triggers = original
        sync_reminders(repos, workspace_id)
        session.commit()
        triggers = repos.reminders.list_triggers(workspace_id)
        assert len(triggers) == 1
