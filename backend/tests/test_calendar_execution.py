"""M9/E91：外部日历执行可靠性、重试、对账与同步差异。"""

import pytest
from calendar_helpers import (
    _confirm,
    _connect,
    _difference_draft,
    _differences,
    _draft,
    _execute,
    _provider,
    _retry,
    _task,
)
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("client")


@pytest.fixture(autouse=True)
def _reset_provider(client: TestClient) -> None:
    client.app.state.calendar_provider.reset()


def test_date_only_task_writes_all_day_event(client: TestClient) -> None:
    connection = _connect(client)
    task = _task(client, due_time=None, due_date="2026-11-05")
    draft = _confirm(client, _draft(client, connection, [task["id"]]))
    result = _execute(client, draft)
    assert result.status_code == 200, result.text
    [event] = _provider(client).events.values()
    assert event["date_only"] is True
    assert event["starts_at"].date().isoformat() == "2026-11-05"
    assert event["timezone"] == "Asia/Shanghai"


def test_partial_failure_and_retry_only_failed_items(client: TestClient) -> None:
    connection = _connect(client)
    first = _task(client, "任务一")
    second = _task(client, "任务二")
    draft = _confirm(client, _draft(client, connection, [first["id"], second["id"]]))

    _provider(client).fail_next("transient")
    result = _execute(client, draft)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["success"] == 1
    assert body["failed"] == 1
    assert body["draft"]["status"] == "partial"
    assert len(_provider(client).events) == 1
    success_event = next(iter(_provider(client).events))

    retried = _retry(client, body["draft"])
    assert retried.status_code == 200, retried.text
    retry_body = retried.json()
    assert retry_body["success"] == 2
    assert retry_body["failed"] == 0
    assert retry_body["draft"]["status"] == "completed"
    assert len(_provider(client).events) == 2
    assert success_event in _provider(client).events

    none_left = _retry(client, retry_body["draft"])
    assert none_left.status_code == 409


def test_unknown_result_reconciled_without_duplicate(client: TestClient) -> None:
    connection = _connect(client)
    task = _task(client)
    draft = _confirm(client, _draft(client, connection, [task["id"]]))

    _provider(client).fail_next("unknown_after_write")
    result = _execute(client, draft)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["unknown"] == 1
    assert body["success"] == 0
    assert body["draft"]["status"] == "unknown"
    assert len(_provider(client).events) == 1
    assert body["items"][0]["operation"]["error_message"]

    retried = _retry(client, body["draft"])
    assert retried.status_code == 200, retried.text
    retry_body = retried.json()
    assert retry_body["success"] == 1
    assert retry_body["draft"]["status"] == "completed"
    assert len(_provider(client).events) == 1

    operations = client.get("/api/v1/calendar/operations", params={"draft_id": draft["id"]}).json()
    assert len(operations) == 1
    assert operations[0]["operation_type"] == "create"
    assert operations[0]["attempt"] == 2


def test_manual_reconcile_marks_unknown_success_without_duplicate(client: TestClient) -> None:
    connection = _connect(client)
    task = _task(client)
    draft = _confirm(client, _draft(client, connection, [task["id"]]))

    _provider(client).fail_next("unknown_after_write")
    body = _execute(client, draft).json()
    assert body["unknown"] == 1
    assert len(_provider(client).events) == 1
    operation_id = body["items"][0]["operation"]["id"]

    reconciled = client.post(f"/api/v1/calendar/operations/{operation_id}/reconcile")
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["status"] == "success"
    assert reconciled.json()["external_event_id"]
    assert len(_provider(client).events) == 1


def test_update_target_missing_is_reported_and_internal_task_kept(
    client: TestClient,
) -> None:
    connection = _connect(client)
    task = _task(client)
    draft = _confirm(client, _draft(client, connection, [task["id"]]))
    assert _execute(client, draft).status_code == 200

    _provider(client).events.clear()
    client.patch(f"/api/v1/tasks/{task['id']}", json={"title": "论文改题"})
    update_draft = _difference_draft(client, connection["id"])
    body = _execute(client, _confirm(client, update_draft)).json()

    assert body["failed"] == 1
    assert body["items"][0]["operation"]["error_category"] == "not_found"
    assert client.get(f"/api/v1/tasks/{task['id']}").status_code == 200
    assert _differences(client, connection["id"])["differences"] == []


def test_task_change_and_cancel_generate_separate_drafts(client: TestClient) -> None:
    connection = _connect(client)
    task = _task(client)
    draft = _confirm(client, _draft(client, connection, [task["id"]]))
    assert _execute(client, draft).status_code == 200

    client.patch(f"/api/v1/tasks/{task['id']}", json={"title": "论文改题"})
    differences = _differences(client, connection["id"])
    assert differences["can_create_draft"] is True
    [update] = differences["differences"]
    assert update["operation_type"] == "update"
    assert update["source_type"] == "task"
    assert update["external_event_id"]

    update_draft = _difference_draft(client, connection["id"])
    assert update_draft["items"][0]["operation_type"] == "update"
    updated = _execute(client, _confirm(client, update_draft))
    assert updated.status_code == 200, updated.text
    assert [event["title"] for event in _provider(client).events.values()] == ["论文改题"]
    assert _differences(client, connection["id"])["differences"] == []

    cancelled = client.patch(
        f"/api/v1/tasks/{task['id']}/progress",
        json={"progress": "cancelled", "reason": "不再需要"},
    )
    assert cancelled.status_code == 200, cancelled.text
    delete_difference = _differences(client, connection["id"])["differences"][0]
    assert delete_difference["operation_type"] == "delete"
    delete_draft = _difference_draft(client, connection["id"])
    assert delete_draft["items"][0]["operation_type"] == "delete"
    deleted = _execute(client, _confirm(client, delete_draft))
    assert deleted.status_code == 200, deleted.text
    assert _provider(client).events == {}
    assert client.get(f"/api/v1/tasks/{task['id']}").json()["progress"] == "cancelled"


def test_revoke_blocks_pending_operations_and_keeps_internal_tasks(
    client: TestClient,
) -> None:
    connection = _connect(client)
    task = _task(client)
    draft = _confirm(client, _draft(client, connection, [task["id"]]))

    revoked = client.delete(f"/api/v1/calendar/connections/{connection['id']}")
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "revoked"

    blocked = _execute(client, draft)
    assert blocked.status_code == 409
    assert "撤销" in blocked.json()["detail"]
    assert _provider(client).events == {}
    assert client.get(f"/api/v1/tasks/{task['id']}").status_code == 200

    differences = _differences(client, connection["id"])
    assert differences["can_create_draft"] is False
    create = client.post(
        "/api/v1/calendar/differences/draft", json={"connection_id": connection["id"]}
    )
    assert create.status_code == 409


def test_plan_mode_writes_accepted_plan_blocks(client: TestClient) -> None:
    connection = _connect(client)
    _task(client, "计划块任务", due_date="2026-10-20", due_time="20:00:00")
    slot = client.post(
        "/api/v1/availability-slots",
        json={"day_of_week": 1, "start_time": "18:00:00", "end_time": "22:00:00"},
    )
    assert slot.status_code == 201, slot.text
    generated = client.post(
        "/api/v1/plans/drafts",
        json={"start_date": "2026-10-19", "end_date": "2026-10-20", "assumptions": {}},
    )
    assert generated.status_code == 201, generated.text
    plan = generated.json()
    accepted = client.post(
        f"/api/v1/plans/drafts/{plan['id']}/accept",
        json={"expected_version": plan["version"]},
    )
    assert accepted.status_code == 200, accepted.text

    response = client.post(
        "/api/v1/calendar/drafts",
        json={"connection_id": connection["id"], "source": {"kind": "plan", "plan_id": plan["id"]}},
    )
    assert response.status_code == 201, response.text
    draft = response.json()
    assert draft["items"]
    assert all(item["source_type"] == "plan_block" for item in draft["items"])

    confirmed = _confirm(client, draft)
    result = _execute(client, confirmed)
    assert result.status_code == 200, result.text
    assert result.json()["success"] == len(draft["items"])
    assert len(_provider(client).events) == len(draft["items"])
