"""A010：提醒规则、触发生成、幂等与操作接口。"""

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _instant(value: str) -> datetime:
    """数据库可能以 UTC 或 +08:00 回显同一时间点，只比较时刻本身。"""
    return datetime.fromisoformat(value)


def _local(*args: int) -> datetime:
    return datetime(*args, tzinfo=SHANGHAI)


def _task(client: TestClient, title: str = "提醒任务", **overrides) -> dict:
    payload = {
        "title": title,
        "due_date": "2026-11-02",
        "due_time": "10:00:00",
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


def _reminders(client: TestClient, **params) -> list[dict]:
    response = client.get("/api/v1/reminders", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_builtin_default_rule_and_single_default(client: TestClient) -> None:
    rules = client.get("/api/v1/reminders/rules").json()
    assert len(rules) == 1
    assert rules[0]["builtin"] is True
    assert rules[0]["id"] is None
    assert rules[0]["advance_minutes"] == 1440

    created = _rule(client, advance_minutes=2880)
    assert created["is_default"] is True
    assert created["advance_minutes"] == 2880

    duplicate = client.post("/api/v1/reminders/rules", json={"advance_minutes": 60})
    assert duplicate.status_code == 409

    rules = client.get("/api/v1/reminders/rules").json()
    assert [item["builtin"] for item in rules] == [False]


def test_rule_validation_and_task_scope(client: TestClient) -> None:
    invalid = client.post("/api/v1/reminders/rules", json={"advance_minutes": 20000})
    assert invalid.status_code == 422
    missing_task = client.post(
        "/api/v1/reminders/rules", json={"task_id": 999, "advance_minutes": 60}
    )
    assert missing_task.status_code == 404

    task = _task(client)
    created = _rule(client, task_id=task["id"], advance_minutes=30)
    assert created["task_id"] == task["id"]


def test_repeat_windows_generated_and_idempotent(client: TestClient) -> None:
    task = _task(client)
    _rule(client, task_id=task["id"], advance_minutes=60, repeat_minutes=30)

    first = _reminders(client, status="pending")
    assert len(first) == 3
    assert sorted(_instant(item["scheduled_for"]) for item in first) == [
        _local(2026, 11, 2, 9, 0),
        _local(2026, 11, 2, 9, 30),
        _local(2026, 11, 2, 10, 0),
    ]
    assert all(item["status"] == "pending" for item in first)
    assert all("10:00" in item["message"] for item in first)

    second = _reminders(client, status="pending")
    assert [item["id"] for item in second] == [item["id"] for item in first]


def test_date_only_ddl_never_claims_a_time(client: TestClient) -> None:
    _task(client, due_time=None, due_date="2026-11-03")

    reminders = _reminders(client)
    assert len(reminders) == 1
    item = reminders[0]
    assert item["payload"]["date_only"] is True
    assert "2026-11-03" in item["message"]
    assert "未指定具体时刻" in item["message"]
    assert ":" not in item["message"]


def test_read_and_dismiss_actions(client: TestClient) -> None:
    _task(client)
    item = _reminders(client)[0]

    read = client.post(f"/api/v1/reminders/{item['id']}/action", json={"action": "read"})
    assert read.status_code == 200
    assert read.json()["status"] == "sent"

    summary = client.get("/api/v1/reminders/summary").json()
    assert summary["unread"] == 0
    assert summary["sent"] == 1

    dismissed = client.post(f"/api/v1/reminders/{item['id']}/action", json={"action": "dismiss"})
    assert dismissed.json()["status"] == "dismissed"

    # 重复同步不会把已忽略的提醒重新激活
    assert _reminders(client)[0]["status"] == "dismissed"
    invalid = client.post(f"/api/v1/reminders/{item['id']}/action", json={"action": "archive"})
    assert invalid.status_code == 422


def test_paused_rule_cancels_pending(client: TestClient) -> None:
    _task(client)
    rule = _rule(client, advance_minutes=60)
    assert len(_reminders(client, status="pending")) == 1

    paused = client.patch(
        f"/api/v1/reminders/rules/{rule['id']}",
        json={"advance_minutes": 60, "paused": True},
    )
    assert paused.status_code == 200
    assert paused.json()["version"] == 2
    assert _reminders(client, status="pending") == []

    resumed = client.patch(
        f"/api/v1/reminders/rules/{rule['id']}",
        json={"advance_minutes": 60, "paused": False},
    )
    assert resumed.status_code == 200
    assert len(_reminders(client, status="pending")) == 1


def test_done_cancels_and_reopen_restores(client: TestClient) -> None:
    task = _task(client)
    _rule(client, task_id=task["id"], advance_minutes=60)
    [item] = _reminders(client, status="pending")

    done = client.patch(
        f"/api/v1/tasks/{task['id']}/progress",
        json={"progress": "done", "reason": "提前完成"},
    )
    assert done.status_code == 200
    cancelled = _reminders(client, status="cancelled")
    assert item["id"] in [row["id"] for row in cancelled]
    assert _reminders(client, status="pending") == []

    reopened = client.patch(
        f"/api/v1/tasks/{task['id']}/progress",
        json={"progress": "in_progress", "reason": "需要补充"},
    )
    assert reopened.status_code == 200
    pending = _reminders(client, status="pending")
    assert [row["id"] for row in pending] == [item["id"]]


def test_delete_task_rule_falls_back_to_default(client: TestClient) -> None:
    task = _task(client)
    rule = _rule(client, task_id=task["id"], advance_minutes=60)
    assert len(_reminders(client, status="pending")) == 1

    deleted = client.delete(f"/api/v1/reminders/rules/{rule['id']}")
    assert deleted.status_code == 204
    pending = _reminders(client, status="pending")
    assert len(pending) == 1
    assert _instant(pending[0]["scheduled_for"]) == _local(2026, 11, 1, 10, 0)
