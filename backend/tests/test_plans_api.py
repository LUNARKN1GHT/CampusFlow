"""M5 计划草案 API 集成测试（P013）。"""

from datetime import date, datetime

from index_helpers import open_session

from campusflow.infrastructure.db.models import PlanDraft, Workspace


def _task(client, **overrides):
    payload = {
        "title": "论文初稿",
        "due_date": "2026-10-19",
        "due_time": "19:00:00",
        "priority": "high",
        "estimated_minutes": 60,
        "remaining_minutes": 60,
    }
    payload.update(overrides)
    response = client.post("/api/v1/tasks", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _availability(client):
    response = client.post(
        "/api/v1/availability-slots",
        json={"day_of_week": 0, "start_time": "18:00:00", "end_time": "22:00:00"},
    )
    assert response.status_code == 201, response.text


def _generate(client, **overrides):
    payload = {
        "start_date": "2026-10-19",
        "end_date": "2026-10-20",
        "assumptions": {"buffer_minutes": 0},
    }
    payload.update(overrides)
    return client.post("/api/v1/plans/drafts", json=payload)


def test_generate_and_accept_plan_without_changing_task_ddl(client) -> None:
    task = _task(client)
    _availability(client)

    response = _generate(client)
    assert response.status_code == 201, response.text
    draft = response.json()
    assert draft["status"] == "draft"
    assert draft["version"] == 1
    assert draft["range_start"] == "2026-10-19"
    assert draft["range_end"] == "2026-10-20"
    assert draft["blocks"]
    assert draft["blocks"][0]["task_id"] == task["id"]
    assert draft["coverage"]["tasks_scheduled"] == 1
    assert len(draft["input_fingerprint"]) == 64

    unchanged = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert unchanged["due_date"] == "2026-10-19"
    assert unchanged["due_time"] == "19:00:00"

    accepted = client.post(
        f"/api/v1/plans/drafts/{draft['id']}/accept",
        json={"expected_version": 1},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"
    assert accepted.json()["version"] == 2

    current = client.get("/api/v1/plans/current")
    assert current.status_code == 200
    assert current.json()["id"] == draft["id"]

    repeat = client.post(
        f"/api/v1/plans/drafts/{draft['id']}/accept",
        json={"expected_version": 2},
    )
    assert repeat.status_code == 409


def test_stale_version_and_changed_inputs_are_rejected(client) -> None:
    task = _task(client, title="变更输入")
    _availability(client)
    first = _generate(client).json()

    wrong_version = client.post(
        f"/api/v1/plans/drafts/{first['id']}/accept",
        json={"expected_version": 99},
    )
    assert wrong_version.status_code == 409

    update = client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={"remaining_minutes": 30},
    )
    assert update.status_code == 200, update.text

    changed = client.post(
        f"/api/v1/plans/drafts/{first['id']}/accept",
        json={"expected_version": 1},
    )
    assert changed.status_code == 409
    assert "输入已变化" in changed.json()["detail"]


def test_newer_accepted_plan_is_not_overwritten_by_old_draft(client) -> None:
    _task(client, title="旧草案")
    _availability(client)
    old = _generate(client).json()
    newer = _generate(client).json()

    accepted = client.post(
        f"/api/v1/plans/drafts/{newer['id']}/accept",
        json={"expected_version": 1},
    )
    assert accepted.status_code == 200, accepted.text

    stale = client.post(
        f"/api/v1/plans/drafts/{old['id']}/accept",
        json={"expected_version": 1},
    )
    assert stale.status_code == 409
    assert "较新的已接受计划" in stale.json()["detail"]


def test_plan_lists_and_reports_no_availability(client) -> None:
    _task(client, title="没有可用时间", due_time=None)
    response = _generate(client)
    assert response.status_code == 201, response.text
    draft = response.json()
    assert draft["blocks"] == []
    assert draft["unscheduled"][0]["reason_code"] == "no_available_time"
    assert draft["coverage"]["capacity_gap_minutes"] == 60
    assert draft["coverage"]["adjustment_options"]

    listing = client.get("/api/v1/plans/drafts")
    assert listing.status_code == 200
    assert len(listing.json()) == 1

    conflicts = client.get(f"/api/v1/plans/drafts/{draft['id']}/conflicts")
    assert conflicts.status_code == 200
    assert isinstance(conflicts.json(), list)


def test_unknown_duration_is_reported_without_scheduling(client) -> None:
    _availability(client)
    _task(
        client,
        title="缺耗时",
        due_time=None,
        estimated_minutes=None,
        remaining_minutes=None,
        due_date="2026-10-19",
    )
    response = _generate(client)
    assert response.status_code == 201, response.text
    draft = response.json()
    assert draft["blocks"] == []
    assert any(item["reason_code"] == "missing_duration" for item in draft["unscheduled"])
    assert any(item["code"] == "missing_duration" for item in draft["gaps"])


def test_explicit_assumptions_are_echoed(client) -> None:
    _availability(client)
    _task(
        client,
        title="按假设排期",
        due_time=None,
        estimated_minutes=None,
        remaining_minutes=None,
        due_date="2026-10-19",
    )
    response = _generate(
        client,
        assumptions={
            "default_duration_minutes": 60,
            "buffer_minutes": 0,
            "segment_minutes": 45,
        },
    )
    assert response.status_code == 201, response.text
    draft = response.json()
    assert draft["blocks"]
    kinds = {item["kind"] for item in draft["assumptions"]}
    assert {"duration", "segment", "buffer"} <= kinds
    assert all(block["minutes"] <= 45 for block in draft["blocks"])


def _accepted_plan(client, *, tasks: int = 1) -> dict:
    for index in range(tasks):
        _task(client, title=f"任务{index}", due_time=None)
    _availability(client)
    draft = _generate(client).json()
    accepted = client.post(
        f"/api/v1/plans/drafts/{draft['id']}/accept",
        json={"expected_version": draft["version"]},
    )
    assert accepted.status_code == 200, accepted.text
    return accepted.json()


def test_move_block_updates_schedule_but_not_task_ddl(client) -> None:
    task = _task(client)
    _availability(client)
    draft = _generate(client).json()
    accepted = client.post(
        f"/api/v1/plans/drafts/{draft['id']}/accept",
        json={"expected_version": 1},
    ).json()
    block = accepted["blocks"][0]

    moved = client.patch(
        f"/api/v1/plans/blocks/{block['id']}",
        json={
            "starts_at": "2026-10-19T20:00:00+08:00",
            "ends_at": "2026-10-19T21:00:00+08:00",
            "expected_version": accepted["version"],
        },
    )
    assert moved.status_code == 200, moved.text
    payload = moved.json()
    assert payload["version"] == accepted["version"] + 1
    moved_start = datetime.fromisoformat(payload["blocks"][0]["starts_at"].replace("Z", "+00:00"))
    assert moved_start == datetime.fromisoformat("2026-10-19T20:00:00+08:00")
    assert "用户手动移动" in payload["blocks"][0]["reason"]

    unchanged = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert unchanged["due_date"] == "2026-10-19"
    assert unchanged["due_time"] == "19:00:00"

    stale = client.patch(
        f"/api/v1/plans/blocks/{block['id']}",
        json={
            "starts_at": "2026-10-19T18:00:00+08:00",
            "ends_at": "2026-10-19T19:00:00+08:00",
            "expected_version": accepted["version"],
        },
    )
    assert stale.status_code == 409


def test_move_rejects_overlap_with_fixed_event_and_other_blocks(client) -> None:
    _task(client, title="甲", due_time=None)
    _task(client, title="乙", due_time=None)
    _availability(client)
    draft = _generate(client).json()
    accepted = client.post(
        f"/api/v1/plans/drafts/{draft['id']}/accept",
        json={"expected_version": 1},
    ).json()
    first, second = accepted["blocks"]

    event = client.post(
        "/api/v1/fixed-events",
        json={
            "title": "晚课",
            "starts_at": "2026-10-19T20:00:00+08:00",
            "ends_at": "2026-10-19T21:00:00+08:00",
            "recurrence": "none",
        },
    )
    assert event.status_code == 201, event.text
    overlap_event = client.patch(
        f"/api/v1/plans/blocks/{first['id']}",
        json={
            "starts_at": "2026-10-19T20:00:00+08:00",
            "ends_at": "2026-10-19T21:00:00+08:00",
            "expected_version": accepted["version"],
        },
    )
    assert overlap_event.status_code == 400

    overlap_block = client.patch(
        f"/api/v1/plans/blocks/{first['id']}",
        json={
            "starts_at": second["starts_at"],
            "ends_at": second["ends_at"],
            "expected_version": accepted["version"],
        },
    )
    assert overlap_block.status_code == 400


def test_lock_and_unlock_accepted_block(client) -> None:
    accepted = _accepted_plan(client)
    block = accepted["blocks"][0]
    locked = client.post(
        f"/api/v1/plans/blocks/{block['id']}/lock",
        json={"locked": True, "expected_version": accepted["version"]},
    )
    assert locked.status_code == 200, locked.text
    assert locked.json()["blocks"][0]["locked"] is True

    unlocked = client.post(
        f"/api/v1/plans/blocks/{block['id']}/lock",
        json={"locked": False, "expected_version": locked.json()["version"]},
    )
    assert unlocked.status_code == 200, unlocked.text
    assert unlocked.json()["blocks"][0]["locked"] is False


def test_draft_block_cannot_be_moved_before_accepting(client) -> None:
    _task(client)
    _availability(client)
    draft = _generate(client).json()
    block = draft["blocks"][0]
    response = client.patch(
        f"/api/v1/plans/blocks/{block['id']}",
        json={
            "starts_at": "2026-10-19T20:00:00+08:00",
            "ends_at": "2026-10-19T21:00:00+08:00",
            "expected_version": draft["version"],
        },
    )
    assert response.status_code == 400
    assert "已接受计划" in response.json()["detail"]


def test_overview_reports_today_plan_and_upcoming_tasks(client) -> None:
    before = client.get("/api/v1/plans/overview")
    assert before.status_code == 200
    assert before.json()["has_plan"] is False
    assert before.json()["today_blocks"] == []

    _task(client, title="即将到期", due_time=None)
    accepted = _accepted_plan(client)

    overview = client.get("/api/v1/plans/overview")
    assert overview.status_code == 200
    body = overview.json()
    assert body["has_plan"] is True
    assert body["plan_status"] == "accepted"
    assert body["plan_version"] == accepted["version"]
    titles = [task["title"] for task in body["upcoming_tasks"]]
    assert "即将到期" in titles
    assert all(task["due_date"] == "2026-10-19" for task in body["upcoming_tasks"])
    assert body["pending_review_count"] == 0
    assert body["unscheduled_count"] == 0


def test_plan_from_another_workspace_is_not_visible(client) -> None:
    with open_session() as session, session.begin():
        other = Workspace(name="另一个工作空间", timezone="Asia/Shanghai")
        session.add(other)
        session.flush()
        draft = PlanDraft(
            workspace_id=other.id,
            semester_id=None,
            range_start=date(2026, 10, 19),
            range_end=date(2026, 11, 1),
            input_snapshot={},
            input_fingerprint="0" * 64,
        )
        session.add(draft)
        session.flush()
        draft_id = draft.id

    response = client.get(f"/api/v1/plans/drafts/{draft_id}")
    assert response.status_code == 404
    assert client.get("/api/v1/plans/current").json() is None


def test_range_validation(client) -> None:
    response = client.post(
        "/api/v1/plans/drafts",
        json={"start_date": "2026-10-20", "end_date": "2026-10-19"},
    )
    assert response.status_code == 400
