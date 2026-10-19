"""M9/E90：外部日历连接、授权、草案与确认一致性。"""

from datetime import UTC, datetime, timedelta

import pytest
from calendar_helpers import (
    _confirm,
    _connect,
    _draft,
    _execute,
    _provider,
    _start,
    _state_from,
    _task,
)
from fastapi.testclient import TestClient
from index_helpers import open_session

from campusflow.infrastructure.db.models import CalendarConnection, CalendarDraft

pytestmark = pytest.mark.usefixtures("client")


@pytest.fixture(autouse=True)
def _reset_provider(client: TestClient) -> None:
    client.app.state.calendar_provider.reset()


def test_oauth_connection_callback_error_and_secret_boundary(client: TestClient) -> None:
    start = _start(client)
    state = _state_from(start["authorization_url"])

    wrong = client.get(
        "/api/v1/calendar/oauth/callback",
        params={"state": "not-the-state", "code": "student@example.com"},
    )
    assert wrong.status_code == 400
    assert client.get("/api/v1/calendar/connections").json() == []

    denied = client.get(
        "/api/v1/calendar/oauth/callback",
        params={"state": state, "error": "access_denied"},
    )
    assert denied.status_code == 400
    assert "未建立" in denied.json()["detail"]
    assert client.get("/api/v1/calendar/connections").json() == []

    connection = _connect(client)
    assert connection["status"] == "active"
    assert connection["account_email"] == "student@example.com"
    assert "local-access" not in str(connection)
    assert "local-refresh" not in str(connection)

    reused = client.get(
        "/api/v1/calendar/oauth/callback",
        params={"state": state, "code": "student@example.com"},
    )
    assert reused.status_code == 400
    assert len(client.get("/api/v1/calendar/connections").json()) == 1

    with open_session() as session:
        row = session.query(CalendarConnection).one()
        assert row.access_token_ciphertext
        assert "local-access" not in row.access_token_ciphertext
        assert row.refresh_token_ciphertext
        assert "local-refresh" not in row.refresh_token_ciphertext


def test_preview_does_not_write_and_confirmation_is_required(client: TestClient) -> None:
    connection = _connect(client)
    first = _task(client, "写课程论文")
    _task(client, "未选择的任务", due_date="2026-11-03")
    draft = _draft(client, connection, [first["id"]])

    assert draft["status"] == "draft"
    assert len(draft["items"]) == 1
    assert draft["items"][0]["operation_type"] == "create"
    assert draft["items"][0]["source_id"] == first["id"]
    assert _provider(client).events == {}

    confirmed = _confirm(client, draft)
    assert confirmed["status"] == "confirmed"
    assert confirmed["confirmed_item_ids"] == [draft["items"][0]["id"]]
    assert confirmed["confirmed_fingerprint"]
    assert _provider(client).events == {}

    wrong = client.post(
        f"/api/v1/calendar/drafts/{draft['id']}/execute",
        json={"confirmation_fingerprint": "0" * 64},
    )
    assert wrong.status_code == 409
    assert _provider(client).events == {}

    result = _execute(client, confirmed)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["success"] == 1
    assert body["failed"] == 0
    assert body["unknown"] == 0
    assert body["draft"]["status"] == "completed"
    assert len(_provider(client).events) == 1

    again = _execute(client, body["draft"])
    assert again.status_code == 200
    assert again.json()["success"] == 1
    assert len(_provider(client).events) == 1
    task = client.get(f"/api/v1/tasks/{first['id']}").json()
    assert task["due_date"] == "2026-11-02"
    assert task["due_time"] == "10:00:00"


def test_content_change_invalidates_confirmation(client: TestClient) -> None:
    connection = _connect(client)
    task = _task(client)
    draft = _confirm(client, _draft(client, connection, [task["id"]]))

    changed = client.patch(f"/api/v1/tasks/{task['id']}", json={"title": "写课程论文（改名）"})
    assert changed.status_code == 200, changed.text

    blocked = _execute(client, draft)
    assert blocked.status_code == 409
    assert "已变化" in blocked.json()["detail"]
    assert _provider(client).events == {}

    reconfirm = client.post(
        f"/api/v1/calendar/drafts/{draft['id']}/confirm",
        json={
            "content_fingerprint": draft["content_fingerprint"],
            "item_ids": [draft["items"][0]["id"]],
        },
    )
    assert reconfirm.status_code == 409


def test_expired_confirmation_cannot_execute(client: TestClient) -> None:
    connection = _connect(client)
    task = _task(client)
    draft = _confirm(client, _draft(client, connection, [task["id"]]))

    with open_session() as session:
        row = session.get(CalendarDraft, draft["id"])
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        session.commit()

    blocked = _execute(client, draft)
    assert blocked.status_code == 409
    current = client.get(f"/api/v1/calendar/drafts/{draft['id']}").json()
    assert current["status"] == "expired"
    assert _provider(client).events == {}


def test_calendar_change_invalidates_confirmation(client: TestClient) -> None:
    connection = _connect(client)
    task = _task(client)
    draft = _confirm(client, _draft(client, connection, [task["id"]]))

    with open_session() as session:
        row = session.get(CalendarConnection, connection["id"])
        assert row is not None
        row.calendar_id = "another-calendar"
        session.commit()

    blocked = _execute(client, draft)
    assert blocked.status_code == 409
    assert "目标日历" in blocked.json()["detail"]
    assert _provider(client).events == {}


def test_connection_is_bound_to_workspace(client: TestClient) -> None:
    from campusflow.infrastructure.db.models import Workspace

    with open_session() as session:
        other = Workspace(name="另一个空间", timezone="UTC")
        session.add(other)
        session.flush()
        connection = CalendarConnection(
            workspace_id=other.id,
            provider="local",
            account_email="other@example.com",
            calendar_id="campusflow-local",
            status="active",
            scopes="calendar.app.created",
        )
        session.add(connection)
        session.commit()
        other_id = connection.id

    assert client.delete(f"/api/v1/calendar/connections/{other_id}").status_code == 404
    response = client.get("/api/v1/calendar/differences", params={"connection_id": other_id})
    assert response.status_code == 404
