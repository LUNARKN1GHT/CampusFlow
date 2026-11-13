"""A007：今天、本周与自定义规划区间。"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from campusflow.application.planning import collect_inputs
from campusflow.domain.errors import DomainError
from campusflow.domain.planning import resolve_planning_range
from campusflow.infrastructure.repositories import build_repositories


def _task(client: TestClient, title: str, **overrides) -> dict:
    payload = {
        "title": title,
        "due_date": "2026-10-20",
        "estimated_minutes": 60,
        "remaining_minutes": 60,
    }
    payload.update(overrides)
    response = client.post("/api/v1/tasks", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _availability(client: TestClient, day_of_week: int = 0) -> None:
    response = client.post(
        "/api/v1/availability-slots",
        json={"day_of_week": day_of_week, "start_time": "18:00:00", "end_time": "22:00:00"},
    )
    assert response.status_code == 201, response.text


def _today(client: TestClient) -> date:
    timezone = client.get("/api/v1/settings").json()["timezone"]
    return datetime.now(ZoneInfo(timezone)).date()


def test_this_week_range_crosses_month_boundary() -> None:
    # 2026-09-30 是周三，本周从 09-28 周一跨到 10-04 周日
    assert resolve_planning_range("this_week", date(2026, 9, 30)) == (
        date(2026, 9, 28),
        date(2026, 10, 4),
    )
    assert resolve_planning_range("today", date(2026, 9, 30)) == (
        date(2026, 9, 30),
        date(2026, 9, 30),
    )
    assert resolve_planning_range(
        "custom", date(2026, 9, 30), start=date(2026, 10, 1), end=date(2026, 10, 1)
    ) == (date(2026, 10, 1), date(2026, 10, 1))


def test_today_range_without_availability_returns_clear_empty_result(
    client: TestClient,
) -> None:
    task = _task(client, "今天无可用时间", due_date=None)
    today = _today(client)

    response = client.post(
        "/api/v1/plans/drafts",
        json={"range_type": "today", "assumptions": {"buffer_minutes": 0}},
    )
    assert response.status_code == 201, response.text
    draft = response.json()
    assert draft["range_start"] == today.isoformat()
    assert draft["range_end"] == today.isoformat()
    assert draft["blocks"] == []
    entry = next(item for item in draft["unscheduled"] if item["task_id"] == task["id"])
    assert entry["reason_code"] == "no_available_time"


def test_this_week_range_uses_monday_to_sunday(client: TestClient) -> None:
    _availability(client)
    today = _today(client)
    monday = today - timedelta(days=today.weekday())

    response = client.post("/api/v1/plans/drafts", json={"range_type": "this_week"})
    assert response.status_code == 201, response.text
    draft = response.json()
    assert draft["range_start"] == monday.isoformat()
    assert draft["range_end"] == (monday + timedelta(days=6)).isoformat()


def test_custom_range_requires_both_dates(client: TestClient) -> None:
    missing_end = client.post(
        "/api/v1/plans/drafts",
        json={"range_type": "custom", "start_date": "2026-10-19"},
    )
    assert missing_end.status_code == 400
    assert "同时提供" in missing_end.json()["detail"]

    preset_with_date = client.post(
        "/api/v1/plans/drafts",
        json={"range_type": "today", "start_date": "2026-10-19"},
    )
    assert preset_with_date.status_code == 400
    assert "不接受自定义" in preset_with_date.json()["detail"]


def test_new_range_draft_does_not_touch_current_plan(client: TestClient) -> None:
    _task(client, "任务一")
    _task(client, "任务二")
    _availability(client)
    created = client.post(
        "/api/v1/plans/drafts",
        json={
            "start_date": "2026-10-19",
            "end_date": "2026-10-20",
            "assumptions": {"buffer_minutes": 0},
        },
    ).json()
    accepted = client.post(
        f"/api/v1/plans/drafts/{created['id']}/accept",
        json={"expected_version": created["version"]},
    ).json()

    today_draft = client.post(
        "/api/v1/plans/drafts",
        json={"range_type": "today", "assumptions": {"buffer_minutes": 0}},
    )
    assert today_draft.status_code == 201

    current = client.get("/api/v1/plans/current").json()
    assert current["id"] == accepted["id"]
    assert len(current["blocks"]) == len(accepted["blocks"])


def test_reschedule_subrange_keeps_blocks_outside_range(
    client: TestClient, fixed_planning_clock
) -> None:
    first = _task(client, "周一任务", due_date="2026-10-19")
    second = _task(client, "周二任务", due_date="2026-10-20")
    _availability(client, day_of_week=0)
    _availability(client, day_of_week=1)
    created = client.post(
        "/api/v1/plans/drafts",
        json={
            "start_date": "2026-10-19",
            "end_date": "2026-10-20",
            "assumptions": {"buffer_minutes": 0},
        },
    ).json()
    accepted = client.post(
        f"/api/v1/plans/drafts/{created['id']}/accept",
        json={"expected_version": created["version"]},
    ).json()
    outside = next(block for block in accepted["blocks"] if block["task_id"] == second["id"])

    response = client.post(
        "/api/v1/plans/reschedule",
        json={"start_date": "2026-10-19", "end_date": "2026-10-19"},
    )
    assert response.status_code == 201, response.text
    draft = response.json()
    assert draft["range_start"] == "2026-10-19"
    assert draft["range_end"] == "2026-10-19"

    kept = next(block for block in draft["blocks"] if block["task_id"] == second["id"])

    def instant(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    assert instant(kept["starts_at"]) == instant(outside["starts_at"])
    assert instant(kept["ends_at"]) == instant(outside["ends_at"])
    assert len([block for block in draft["blocks"] if block["task_id"] == second["id"]]) == 1
    assert {block["task_id"] for block in draft["blocks"]} == {first["id"], second["id"]}

    accepted_new = client.post(
        f"/api/v1/plans/drafts/{draft['id']}/accept",
        json={"expected_version": draft["version"]},
    )
    assert accepted_new.status_code == 200, accepted_new.text


def test_planning_range_limit_is_inclusive_and_bounded() -> None:
    start = date(2026, 1, 1)
    # 含首尾 366 天允许，再增加一天即拒绝（#625）。
    assert resolve_planning_range(
        "custom", start, start=start, end=start + timedelta(days=365)
    ) == (start, start + timedelta(days=365))
    with pytest.raises(DomainError, match="366"):
        resolve_planning_range("custom", start, start=start, end=start + timedelta(days=366))


def test_occurrence_expansion_rejects_range_over_limit(client: TestClient) -> None:
    """固定日程 occurrences 在排期输入收集前就按同一上限拒绝，不展开数百万天。"""
    workspace_id = client.get("/api/v1/settings").json()["workspace_id"]
    with client.app.state.session_factory() as session:
        with pytest.raises(DomainError, match="366"):
            collect_inputs(
                build_repositories(session),
                workspace_id,
                semester_id=None,
                range_start=date(1, 1, 1),
                range_end=date(9999, 12, 31),
            )


def test_custom_range_longer_than_limit_returns_400_without_draft(client: TestClient) -> None:
    response = client.post(
        "/api/v1/plans/drafts",
        json={
            "range_type": "custom",
            "start_date": "0001-01-01",
            "end_date": "9999-12-31",
        },
    )
    assert response.status_code == 400
    assert "366" in response.json()["detail"]
    # 拒绝发生在展开可用时间与固定日程之前，不留下草案或时间块。
    assert client.get("/api/v1/plans/drafts").json() == []


def test_reschedule_range_longer_than_limit_returns_400(
    client: TestClient, fixed_planning_clock
) -> None:
    _task(client, "今天任务")
    _availability(client)
    created = client.post(
        "/api/v1/plans/drafts",
        json={
            "start_date": "2026-10-19",
            "end_date": "2026-10-20",
            "assumptions": {"buffer_minutes": 0},
        },
    ).json()
    accepted = client.post(
        f"/api/v1/plans/drafts/{created['id']}/accept",
        json={"expected_version": created["version"]},
    )
    assert accepted.status_code == 200, accepted.text

    response = client.post(
        "/api/v1/plans/reschedule",
        json={"start_date": "2026-10-19", "end_date": "2027-10-20"},
    )
    assert response.status_code == 400
    assert "366" in response.json()["detail"]
    assert len(client.get("/api/v1/plans/drafts").json()) == 1


def test_overview_blocks_match_frontend_contract(client: TestClient, fixed_planning_clock) -> None:
    _task(client, "今天任务")
    _availability(client)
    draft = client.post(
        "/api/v1/plans/drafts",
        json={
            "start_date": "2026-10-19",
            "end_date": "2026-10-20",
            "assumptions": {"buffer_minutes": 0},
        },
    ).json()
    accepted = client.post(
        f"/api/v1/plans/drafts/{draft['id']}/accept",
        json={"expected_version": draft["version"]},
    )
    assert accepted.status_code == 200, accepted.text

    body = client.get("/api/v1/plans/overview").json()
    assert body["today_blocks"], body
    # #629：总览时间块只含 OverviewBlock 字段，不含草案的 draft_id/kind/seq/constraint_source。
    expected = {
        "id",
        "task_id",
        "title",
        "starts_at",
        "ends_at",
        "minutes",
        "locked",
        "reason",
    }
    for item in body["today_blocks"]:
        assert set(item) == expected
    if body["next_block"] is not None:
        assert set(body["next_block"]) == expected
