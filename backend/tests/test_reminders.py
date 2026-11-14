"""A010：提醒规则、触发生成、幂等与操作接口。"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from campusflow.application.reminders import MAX_DEADLINE_WINDOWS
from campusflow.infrastructure.repositories import SqlAlchemyReminderRepository

SHANGHAI = ZoneInfo("Asia/Shanghai")
# 截止日期必须相对测试运行日生成：固定过去日期会让提醒窗口永远不生成。
DUE_DAY = date.today() + timedelta(days=30)
DUE_ONLY_DAY = DUE_DAY + timedelta(days=1)


def _instant(value: str) -> datetime:
    """数据库可能以 UTC 或 +08:00 回显同一时间点，只比较时刻本身。"""
    return datetime.fromisoformat(value)


def _local(*args: int) -> datetime:
    return datetime(*args, tzinfo=SHANGHAI)


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=SHANGHAI)


def _task(client: TestClient, title: str = "提醒任务", **overrides) -> dict:
    payload = {
        "title": title,
        "due_date": DUE_DAY.isoformat(),
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
        _at(DUE_DAY, 9, 0),
        _at(DUE_DAY, 9, 30),
        _at(DUE_DAY, 10, 0),
    ]
    assert all(item["status"] == "pending" for item in first)
    assert all("10:00" in item["message"] for item in first)

    second = _reminders(client, status="pending")
    assert [item["id"] for item in second] == [item["id"] for item in first]


def test_date_only_ddl_never_claims_a_time(client: TestClient) -> None:
    _task(client, due_time=None, due_date=DUE_ONLY_DAY.isoformat())

    reminders = _reminders(client)
    assert len(reminders) == 1
    item = reminders[0]
    assert item["payload"]["date_only"] is True
    assert DUE_ONLY_DAY.isoformat() in item["message"]
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
    assert _instant(pending[0]["scheduled_for"]) == _at(DUE_DAY - timedelta(days=1), 10, 0)


def test_high_frequency_repeat_is_capped(client: TestClient) -> None:
    """#626：repeat_minutes=1 也只生成有界数量的未来窗口。"""
    task = _task(client)
    _rule(client, task_id=task["id"], advance_minutes=1440, repeat_minutes=1)

    pending = _reminders(client, status="pending")
    assert len(pending) == MAX_DEADLINE_WINDOWS
    scheduled = sorted(_instant(item["scheduled_for"]) for item in pending)
    due_at = _at(DUE_DAY, 10, 0)
    assert scheduled[-1] == due_at
    assert scheduled[0] == due_at - timedelta(minutes=MAX_DEADLINE_WINDOWS - 1)
    assert len({item["id"] for item in pending}) == MAX_DEADLINE_WINDOWS

    # 重复读取不会新增窗口，记录数保持有界。
    again = _reminders(client, status="pending")
    assert len(again) == MAX_DEADLINE_WINDOWS
    assert [item["id"] for item in again] == [item["id"] for item in pending]


def test_read_path_skips_unchanged_upserts(client: TestClient, monkeypatch) -> None:
    """#626：读取路径在状态未变化时不重复 upsert 提醒。"""
    task = _task(client)
    _rule(client, task_id=task["id"], advance_minutes=1440, repeat_minutes=1)
    first = _reminders(client, status="pending")
    assert len(first) == MAX_DEADLINE_WINDOWS

    def forbidden_upsert(self, rows):
        raise AssertionError("读取路径不应重复 upsert 未变化的提醒")

    monkeypatch.setattr(SqlAlchemyReminderRepository, "upsert_triggers", forbidden_upsert)
    second = _reminders(client, status="pending")
    assert [item["id"] for item in second] == [item["id"] for item in first]

    summary = client.get("/api/v1/reminders/summary")
    assert summary.status_code == 200, summary.text
    assert summary.json()["pending"] == MAX_DEADLINE_WINDOWS
