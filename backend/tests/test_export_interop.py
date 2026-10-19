"""A021：导出文件的日期、时区与隐私互操作（真实 PostgreSQL）。

覆盖：仅日期 DDL 在正负时区保持同一天全天、带时刻 DDL 在另一时区仍是同一时间点、
计划块跨日不裁剪、每周固定日程展开不写 RRULE 且 UID 稳定、CSV 不泄漏隐私字段、
计划时区不可读时拒绝导出。
"""

import csv
import io
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from index_helpers import open_session

from campusflow.infrastructure.db.models import PlanBlock, PlanDraft

TASK_HEADER = [
    "export_version",
    "task_id",
    "course_id",
    "course_name",
    "title",
    "due_date",
    "due_time",
    "due_precision",
    "due_source",
    "target_date",
    "priority",
    "progress",
    "estimated_minutes",
    "remaining_minutes",
    "source_state",
    "source_title",
    "source_version_no",
    "source_locator",
]


def _create_task(client: TestClient, title: str, **extra: object) -> dict:
    response = client.post("/api/v1/tasks", json={"title": title, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def _create_availability(client: TestClient, day: int = 0) -> None:
    response = client.post(
        "/api/v1/availability-slots",
        json={"day_of_week": day, "start_time": "18:00:00", "end_time": "22:00:00"},
    )
    assert response.status_code == 201, response.text


def _set_timezone(client: TestClient, timezone: str) -> None:
    current = client.get("/api/v1/settings").json()
    response = client.put(
        "/api/v1/settings",
        json={
            "timezone": timezone,
            "daily_capacity_minutes": current["daily_capacity_minutes"],
            "break_minutes": current["break_minutes"],
            "buffer_minutes": current["buffer_minutes"],
            "weekly_capacity_minutes": current["weekly_capacity_minutes"],
        },
    )
    assert response.status_code == 200, response.text


def _generate_plan(client: TestClient, start: str, end: str) -> dict:
    response = client.post(
        "/api/v1/plans/drafts",
        json={"start_date": start, "end_date": end, "assumptions": {"buffer_minutes": 0}},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _accept_plan(client: TestClient, draft: dict) -> dict:
    response = client.post(
        f"/api/v1/plans/drafts/{draft['id']}/accept",
        json={"expected_version": draft["version"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _preview(
    client: TestClient,
    task_ids: list[int] | None,
    *,
    mode: str = "tasks",
    export_format: str = "ics",
    plan_id: int | None = None,
    include_fixed_events: bool = False,
):
    return client.post(
        "/api/v1/exports/preview",
        json={
            "mode": mode,
            "format": export_format,
            "task_ids": task_ids,
            "plan_id": plan_id,
            "options": {
                "include_source_hints": False,
                "include_fixed_events": include_fixed_events,
            },
        },
    )


def _download(
    client: TestClient,
    task_ids: list[int] | None,
    fingerprint: str,
    *,
    mode: str = "tasks",
    export_format: str = "ics",
    plan_id: int | None = None,
    include_fixed_events: bool = False,
):
    return client.post(
        "/api/v1/exports",
        json={
            "mode": mode,
            "format": export_format,
            "task_ids": task_ids,
            "plan_id": plan_id,
            "options": {
                "include_source_hints": False,
                "include_fixed_events": include_fixed_events,
            },
            "scope_fingerprint": fingerprint,
        },
    )


def _unfold(content: bytes) -> str:
    text = content.decode("utf-8")
    assert "\r\n" in text
    assert b"\n" not in content.replace(b"\r\n", b""), "ICS 只能使用 CRLF 换行"
    return text.replace("\r\n ", "")


def _ics_events(content: bytes) -> dict[str, dict[str, str]]:
    text = _unfold(content)
    events: dict[str, dict[str, str]] = {}
    for chunk in text.split("BEGIN:VEVENT")[1:]:
        body, _, _ = chunk.partition("END:VEVENT")
        properties: dict[str, str] = {}
        for line in body.strip("\r\n").split("\r\n"):
            name, _, value = line.partition(":")
            properties[name] = value
        events[properties["UID"]] = properties
    return events


def _utc(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)


def _parse_csv(content: bytes) -> tuple[list[str], list[dict[str, str]]]:
    assert content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(content.decode("utf-8-sig"), newline="")))
    header, body = rows[0], rows[1:]
    return header, [dict(zip(header, row, strict=True)) for row in body]


def test_date_only_ddl_stays_all_day_across_timezones(client: TestClient) -> None:
    task = _create_task(client, "仅日期作业", due_date="2026-11-02")

    for timezone in ("Asia/Shanghai", "America/Los_Angeles"):
        _set_timezone(client, timezone)
        preview = _preview(client, [task["id"]]).json()
        assert preview["timezone"] == timezone
        assert preview["date_range"] == {
            "start": "2026-11-02",
            "end": "2026-11-02",
            "has_date_only": True,
        }

        download = _download(client, [task["id"]], preview["scope_fingerprint"])
        assert download.status_code == 200, download.text
        events = _ics_events(download.content)
        (event,) = events.values()
        assert event["DTSTART;VALUE=DATE"] == "20261102"
        assert event["DTEND;VALUE=DATE"] == "20261103"
        assert "DTSTART" not in event, "仅日期截止不能补造具体时刻"
        assert event["X-CAMPUSFLOW-DUE-PRECISION"] == "DATE"


def test_timed_ddl_is_one_instant_in_any_timezone(client: TestClient) -> None:
    _set_timezone(client, "Asia/Shanghai")
    task = _create_task(
        client,
        "带时刻截止",
        due_date="2026-11-02",
        due_time="23:30:00",
    )
    preview = _preview(client, [task["id"]]).json()
    download = _download(client, [task["id"]], preview["scope_fingerprint"])
    assert download.status_code == 200, download.text
    (event,) = _ics_events(download.content).values()

    start = _utc(event["DTSTART"])
    assert event["DTSTART"] == "20261102T153000Z"
    assert _utc(event["DTEND"]) - start == timedelta(minutes=15)

    # 同一 UTC 时间点在不同客户端时区只是本地显示不同，不是另一条事件。
    assert start.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M") == (
        "2026-11-02 10:30"
    )
    assert start.astimezone(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M") == (
        "2026-11-03 00:30"
    )


def test_plan_block_across_midnight_is_not_clamped(client: TestClient) -> None:
    _set_timezone(client, "Asia/Shanghai")
    task = _create_task(
        client,
        "跨日任务",
        due_date="2026-10-20",
        due_time="10:00",
        estimated_minutes=60,
        remaining_minutes=60,
    )
    _create_availability(client)
    draft = _generate_plan(client, "2026-10-19", "2026-10-20")
    _accept_plan(client, draft)

    with open_session() as session, session.begin():
        block = PlanBlock(
            draft_id=draft["id"],
            task_id=task["id"],
            kind="task",
            title="跨日写作",
            starts_at=datetime(2026, 10, 19, 23, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
            ends_at=datetime(2026, 10, 20, 1, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            minutes=90,
            seq=98,
            locked=False,
            reason="跨日样本",
            constraint_source=[],
        )
        session.add(block)
        session.flush()
        block_id = block.id

    preview = _preview(client, None, mode="plan", plan_id=draft["id"]).json()
    download = _download(
        client, None, preview["scope_fingerprint"], mode="plan", plan_id=draft["id"]
    )
    assert download.status_code == 200, download.text
    events = _ics_events(download.content)
    event = next(
        item for uid, item in events.items() if item.get("X-CAMPUSFLOW-BLOCK-ID") == str(block_id)
    )
    assert event["DTSTART"] == "20261019T153000Z"
    assert event["DTEND"] == "20261019T170000Z"
    assert _utc(event["DTEND"]) - _utc(event["DTSTART"]) == timedelta(minutes=90)
    assert (
        _utc(event["DTEND"]).astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")
        == "2026-10-20 01:00"
    )


def test_weekly_fixed_event_expands_without_rrule(client: TestClient) -> None:
    _set_timezone(client, "Asia/Shanghai")
    fixed = client.post(
        "/api/v1/fixed-events",
        json={
            "title": "每周组会",
            "starts_at": "2026-10-19T23:30:00",
            "ends_at": "2026-10-20T00:30:00",
            "recurrence": "weekly",
            "repeat_until": "2026-10-27",
        },
    )
    assert fixed.status_code == 201, fixed.text
    fixed_id = fixed.json()["id"]

    _create_availability(client)
    draft = _generate_plan(client, "2026-10-19", "2026-10-27")
    _accept_plan(client, draft)

    preview = _preview(
        client,
        None,
        mode="plan",
        plan_id=draft["id"],
        include_fixed_events=True,
    ).json()
    download = _download(
        client,
        None,
        preview["scope_fingerprint"],
        mode="plan",
        plan_id=draft["id"],
        include_fixed_events=True,
    )
    assert download.status_code == 200, download.text
    events = _ics_events(download.content)
    fixed_events = {
        uid: event
        for uid, event in events.items()
        if uid.startswith(f"campusflow-fixed-{fixed_id}")
    }
    assert len(fixed_events) == 2
    starts = sorted(_utc(event["DTSTART"]) for event in fixed_events.values())
    assert [
        value.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M") for value in starts
    ] == [
        "2026-10-19 23:30",
        "2026-10-26 23:30",
    ]
    for event in fixed_events.values():
        assert _utc(event["DTEND"]) - _utc(event["DTSTART"]) == timedelta(minutes=60)
        # 跨日发生项保持实际次日结束，不被裁剪到当天。
        assert (
            _utc(event["DTEND"])
            .astimezone(ZoneInfo("Asia/Shanghai"))
            .strftime("%Y-%m-%d %H:%M")
            .endswith("00:30")
        )
    assert "RRULE" not in _unfold(download.content)

    repeat = _download(
        client,
        None,
        preview["scope_fingerprint"],
        mode="plan",
        plan_id=draft["id"],
        include_fixed_events=True,
    )
    assert repeat.status_code == 200, repeat.text
    assert set(_ics_events(repeat.content)) == set(events), "重复导出 UID 必须稳定"


def test_csv_has_no_extra_private_fields(client: TestClient) -> None:
    semester = client.post(
        "/api/v1/semesters",
        json={"name": "2026 秋", "start_date": "2026-09-01", "end_date": "2027-01-15"},
    )
    assert semester.status_code == 201, semester.text
    course = client.post(
        "/api/v1/courses",
        json={
            "semester_id": semester.json()["id"],
            "name": "隐私课程",
            "teacher": "王老师",
            "class_name": "教学班 A",
        },
    )
    assert course.status_code == 201, course.text
    task = _create_task(
        client,
        "普通标题",
        course_id=course.json()["id"],
        description="私密任务描述",
        due_note="私密截止备注",
        due_date="2026-11-02",
        due_time="10:00",
    )
    preview = _preview(client, [task["id"]], export_format="csv").json()
    assert preview["can_download"] is True
    download = _download(client, [task["id"]], preview["scope_fingerprint"], export_format="csv")
    assert download.status_code == 200, download.text

    header, rows = _parse_csv(download.content)
    assert header == TASK_HEADER
    for forbidden in (
        "description",
        "due_note",
        "teacher",
        "class_name",
        "quote",
        "publisher",
        "url",
    ):
        assert forbidden not in header, f"CSV 不应包含 {forbidden} 列"
    (row,) = rows
    assert row["title"] == "普通标题"
    assert row["course_name"] == "隐私课程"
    text = download.content.decode("utf-8-sig")
    for secret in ("私密任务描述", "私密截止备注", "王老师", "教学班 A"):
        assert secret not in text, f"CSV 不应包含 {secret}"


def test_unreadable_plan_timezone_is_rejected(client: TestClient) -> None:
    _create_task(
        client,
        "计划任务",
        due_date="2026-10-19",
        due_time="19:00",
        estimated_minutes=60,
        remaining_minutes=60,
    )
    _create_availability(client)
    draft = _generate_plan(client, "2026-10-19", "2026-10-20")
    _accept_plan(client, draft)

    with open_session() as session, session.begin():
        stored = session.get(PlanDraft, draft["id"])
        assert stored is not None
        snapshot = dict(stored.input_snapshot)
        snapshot["timezone"] = "Not/AZone"
        stored.input_snapshot = snapshot

    response = _preview(client, None, mode="plan", plan_id=draft["id"])
    assert response.status_code == 400
    assert "时区" in response.json()["detail"]
