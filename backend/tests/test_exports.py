"""A018／A019：任务清单 CSV 与日历草案 ICS 导出（真实 PostgreSQL）。

覆盖显式范围、日期精度、CSV 往返、取消任务排除、来源提示隐私、
范围指纹失效、ICS 全天／UTC 换算、特殊字符转义、已接受计划时间块、
固定日程可选展开和未支持组合；不依赖外部日历或服务端留存文件。
"""

import csv
import io
import re
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from index_helpers import open_session

from campusflow.infrastructure.db.models import PlanBlock

HEADER = [
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
    payload: dict = {"title": title, **extra}
    response = client.post("/api/v1/tasks", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _create_availability(client: TestClient) -> None:
    response = client.post(
        "/api/v1/availability-slots",
        json={"day_of_week": 0, "start_time": "18:00:00", "end_time": "22:00:00"},
    )
    assert response.status_code == 201, response.text


def _generate_plan(client: TestClient, *, start: str = "2026-10-19", end: str = "2026-10-20"):
    response = client.post(
        "/api/v1/plans/drafts",
        json={
            "start_date": start,
            "end_date": end,
            "assumptions": {"buffer_minutes": 0},
        },
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
    include_source_hints: bool = False,
    include_fixed_events: bool = False,
    mode: str = "tasks",
    export_format: str = "csv",
    plan_id: int | None = None,
):
    return client.post(
        "/api/v1/exports/preview",
        json={
            "mode": mode,
            "format": export_format,
            "task_ids": task_ids,
            "plan_id": plan_id,
            "options": {
                "include_source_hints": include_source_hints,
                "include_fixed_events": include_fixed_events,
            },
        },
    )


def _download(
    client: TestClient,
    task_ids: list[int] | None,
    fingerprint: str | None,
    *,
    include_source_hints: bool = False,
    include_fixed_events: bool = False,
    mode: str = "tasks",
    export_format: str = "csv",
    plan_id: int | None = None,
):
    return client.post(
        "/api/v1/exports",
        json={
            "mode": mode,
            "format": export_format,
            "task_ids": task_ids,
            "plan_id": plan_id,
            "options": {
                "include_source_hints": include_source_hints,
                "include_fixed_events": include_fixed_events,
            },
            "scope_fingerprint": fingerprint,
        },
    )


def _parse_csv(content: bytes) -> tuple[list[str], list[dict[str, str]]]:
    assert content.startswith(b"\xef\xbb\xbf"), "CSV 必须带 UTF-8 BOM"
    reader = csv.reader(io.StringIO(content.decode("utf-8-sig"), newline=""))
    rows = list(reader)
    header, body = rows[0], rows[1:]
    return header, [dict(zip(header, row, strict=True)) for row in body]


def _unfold(content: bytes) -> str:
    """ICS 必须是 UTF-8、CRLF，且不能有裸 LF；折行按空格续行还原。"""
    text = content.decode("utf-8")
    assert "\r\n" in text
    assert b"\n" not in content.replace(b"\r\n", b""), "ICS 只能使用 CRLF 换行"
    return text.replace("\r\n ", "")


def _ics_events(content: bytes) -> dict[str, dict[str, str]]:
    """按 UID 索引 VEVENT 属性；属性名保留 DTSTART;VALUE=DATE 这类参数写法。"""
    text = _unfold(content)
    assert text.startswith("BEGIN:VCALENDAR\r\n")
    assert text.endswith("END:VCALENDAR\r\n")
    events: dict[str, dict[str, str]] = {}
    for chunk in text.split("BEGIN:VEVENT")[1:]:
        body, _, _ = chunk.partition("END:VEVENT")
        properties: dict[str, str] = {}
        for line in body.strip("\r\n").split("\r\n"):
            name, _, value = line.partition(":")
            properties[name] = value
        events[properties["UID"]] = properties
    return events


def test_selected_tasks_only_and_scope_required(client: TestClient) -> None:
    selected = _create_task(client, "选中的任务")
    other = _create_task(client, "没有选中的任务")

    preview_response = _preview(client, [selected["id"]])
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["scope"]["task_ids"] == [selected["id"]]
    assert preview["item_count"] == 1
    assert preview["row_count"] == 1

    download = _download(client, [selected["id"]], preview["scope_fingerprint"])
    assert download.status_code == 200, download.text
    assert "选中的任务" in download.content.decode("utf-8-sig")
    assert "没有选中的任务" not in download.content.decode("utf-8-sig")
    assert str(other["id"]) not in {row["task_id"] for row in _parse_csv(download.content)[1]}

    empty = _preview(client, [])
    assert empty.status_code == 400
    assert "显式选择" in empty.json()["detail"]

    missing = _preview(client, [999_999])
    assert missing.status_code == 404
    assert "任务不存在" in missing.json()["detail"]


def test_duplicate_task_ids_are_deduplicated(client: TestClient) -> None:
    task = _create_task(client, "重复选择")
    preview = _preview(client, [task["id"], task["id"]]).json()
    assert preview["item_count"] == 1
    assert preview["row_count"] == 1
    assert preview["scope"]["task_ids"] == [task["id"]]


def test_date_only_and_missing_due_keep_precision(client: TestClient) -> None:
    date_only = _create_task(client, "只有日期", due_date="2026-10-15")
    timed = _create_task(client, "带时刻", due_date="2026-10-16", due_time="14:30")
    no_due = _create_task(client, "没有截止")

    preview_response = _preview(client, [date_only["id"], timed["id"], no_due["id"]])
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["counts"]["date_only"] == 1
    assert preview["counts"]["timed"] == 1
    assert preview["counts"]["no_due"] == 1
    assert preview["date_range"] == {
        "start": "2026-10-15",
        "end": "2026-10-16",
        "has_date_only": True,
    }
    warning_codes = {warning["code"] for warning in preview["warnings"]}
    assert {"date_only_ddl", "missing_due_date"} <= warning_codes

    download = _download(
        client,
        [date_only["id"], timed["id"], no_due["id"]],
        preview["scope_fingerprint"],
    )
    assert download.status_code == 200, download.text
    rows = {row["task_id"]: row for row in _parse_csv(download.content)[1]}

    only_date = rows[str(date_only["id"])]
    assert only_date["due_date"] == "2026-10-15"
    assert only_date["due_time"] == ""
    assert only_date["due_precision"] == "date"
    assert only_date["due_source"] == "user"

    with_time = rows[str(timed["id"])]
    assert with_time["due_time"] == "14:30"
    assert with_time["due_precision"] == "datetime"

    without_due = rows[str(no_due["id"])]
    assert without_due["due_date"] == ""
    assert without_due["due_time"] == ""
    assert without_due["due_precision"] == "none"

    text = download.content.decode("utf-8-sig")
    # 验收硬指标：不把只有日期的截止补成午夜或 23:59。
    assert "00:00" not in text
    assert "23:59" not in text


def test_special_characters_round_trip(client: TestClient) -> None:
    long_title = '中文,带逗号 "引号"\n第二行 ' + "长" * 200
    assert len(long_title) <= 300
    task = _create_task(client, long_title)

    preview = _preview(client, [task["id"]]).json()
    download = _download(client, [task["id"]], preview["scope_fingerprint"])
    assert download.status_code == 200, download.text

    header, rows = _parse_csv(download.content)
    assert header == HEADER
    assert len(rows) == 1
    assert rows[0]["title"] == long_title
    assert rows[0]["task_id"] == str(task["id"])


def test_cancelled_task_is_excluded_and_warned(client: TestClient) -> None:
    active = _create_task(client, "进行中的任务")
    cancelled = _create_task(client, "已取消的任务")
    response = client.patch(
        f"/api/v1/tasks/{cancelled['id']}/progress",
        json={"progress": "cancelled", "reason": "不再需要"},
    )
    assert response.status_code == 200, response.text

    preview_response = _preview(client, [active["id"], cancelled["id"]])
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["item_count"] == 2
    assert preview["row_count"] == 1
    assert preview["counts"]["cancelled"] == 1
    assert preview["can_download"] is True
    assert any(
        item["code"] == "cancelled_task" and item["count"] == 1 for item in preview["excluded"]
    )
    assert "cancelled_task" in {warning["code"] for warning in preview["warnings"]}
    cancelled_item = next(item for item in preview["items"] if item["task_id"] == cancelled["id"])
    assert cancelled_item["included"] is False
    assert cancelled_item["exclusion_reason"] == "cancelled_task"

    download = _download(client, [active["id"], cancelled["id"]], preview["scope_fingerprint"])
    assert download.status_code == 200, download.text
    assert "进行中的任务" in download.content.decode("utf-8-sig")
    assert "已取消的任务" not in download.content.decode("utf-8-sig")

    only_cancelled = _preview(client, [cancelled["id"]]).json()
    assert only_cancelled["can_download"] is False
    assert "empty_export" in {warning["code"] for warning in only_cancelled["warnings"]}
    refused = _download(client, [cancelled["id"]], only_cancelled["scope_fingerprint"])
    assert refused.status_code == 400
    assert "没有可导出" in refused.json()["detail"]


def test_source_hints_blank_and_private_fields_never_exported(client: TestClient) -> None:
    description = "这是只应留在应用内的描述"
    due_note = "这是内部截止备注"
    task = _create_task(
        client,
        "隐私检查任务",
        description=description,
        due_note=due_note,
        due_date="2026-11-01",
    )

    preview = _preview(client, [task["id"]]).json()
    assert preview["items"][0]["source_title"] is None
    assert preview["items"][0]["source_version_no"] is None
    assert preview["items"][0]["source_locator"] is None

    for include_hints in (False, True):
        scoped = _preview(client, [task["id"]], include_source_hints=include_hints).json()
        download = _download(
            client,
            [task["id"]],
            scoped["scope_fingerprint"],
            include_source_hints=include_hints,
        )
        assert download.status_code == 200, download.text
        text = download.content.decode("utf-8-sig")
        assert description not in text
        assert due_note not in text
        assert "null" not in text
        header, rows = _parse_csv(download.content)
        assert header == HEADER
        assert rows[0]["source_title"] == ""
        assert rows[0]["source_version_no"] == ""
        assert rows[0]["source_locator"] == ""


def test_stale_scope_fingerprint_requires_new_preview(client: TestClient) -> None:
    task = _create_task(client, "原始标题", due_date="2026-10-20")
    preview = _preview(client, [task["id"]]).json()
    assert preview["can_download"] is True
    assert (
        _preview(client, [task["id"]]).json()["scope_fingerprint"] == preview["scope_fingerprint"]
    )

    download = _download(client, [task["id"]], preview["scope_fingerprint"])
    assert download.status_code == 200, download.text
    assert download.headers["content-type"] == "text/csv; charset=utf-8"
    assert download.content.startswith(b"\xef\xbb\xbf")
    disposition = download.headers["content-disposition"]
    assert re.search(r'filename="campusflow-tasks-\d{8}\.csv"', disposition)

    updated = client.patch(f"/api/v1/tasks/{task['id']}", json={"title": "修改后的标题"})
    assert updated.status_code == 200, updated.text

    stale = _download(client, [task["id"]], preview["scope_fingerprint"])
    assert stale.status_code == 409
    assert "重新预览" in stale.json()["detail"]

    without_fingerprint = _download(client, [task["id"]], None)
    assert without_fingerprint.status_code == 400
    assert "指纹" in without_fingerprint.json()["detail"]


def test_plan_csv_is_still_rejected(client: TestClient) -> None:
    plan_csv = _preview(client, None, mode="plan", plan_id=1)
    assert plan_csv.status_code == 400
    assert "计划清单导出由后续任务提供" in plan_csv.json()["detail"]


def test_tasks_ics_all_day_and_timed_in_utc(client: TestClient) -> None:
    date_only = _create_task(client, "只有日期", due_date="2026-10-15")
    timed = _create_task(client, "带时刻", due_date="2026-10-16", due_time="14:30")
    no_due = _create_task(client, "没有截止")
    task_ids = [date_only["id"], timed["id"], no_due["id"]]

    preview_response = _preview(client, task_ids, export_format="ics")
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["format"] == "ics"
    assert preview["event_count"] == 2
    assert preview["counts"] == {
        "date_only": 1,
        "timed": 1,
        "no_due": 1,
        "scheduled": 0,
        "unscheduled": 0,
        "source_invalidated": 0,
        "cancelled": 0,
    }
    warning_codes = {warning["code"] for warning in preview["warnings"]}
    assert {"date_only_ddl", "missing_due_date"} <= warning_codes

    download = _download(client, task_ids, preview["scope_fingerprint"], export_format="ics")
    assert download.status_code == 200, download.text
    assert download.headers["content-type"] == "text/calendar; charset=utf-8"
    assert re.search(
        r'filename="campusflow-tasks-\d{8}\.ics"',
        download.headers["content-disposition"],
    )

    events = _ics_events(download.content)
    assert set(events) == {
        f"campusflow-task-{date_only['id']}-due@campusflow.local",
        f"campusflow-task-{timed['id']}-due@campusflow.local",
    }
    assert f"campusflow-task-{no_due['id']}-due@campusflow.local" not in events

    all_day = events[f"campusflow-task-{date_only['id']}-due@campusflow.local"]
    assert all_day["DTSTART;VALUE=DATE"] == "20261015"
    assert all_day["DTEND;VALUE=DATE"] == "20261016"
    assert "DTSTART" not in all_day, "只有日期的截止不得补具体时刻"
    assert all_day["X-CAMPUSFLOW-DUE-PRECISION"] == "DATE"
    assert all_day["X-CAMPUSFLOW-TASK-ID"] == str(date_only["id"])
    assert all_day["X-CAMPUSFLOW-DUE-SOURCE"] == "user"
    assert all_day["X-CAMPUSFLOW-PROGRESS"] == "not_started"
    assert all_day["X-CAMPUSFLOW-PRIORITY"] == "medium"
    assert all_day["X-CAMPUSFLOW-SOURCE-STATE"] == "none"
    assert "X-CAMPUSFLOW-SOURCE-HINT" not in all_day
    assert all_day["TRANSP"] == "TRANSPARENT"
    assert all_day["STATUS"] == "CONFIRMED"

    timed_event = events[f"campusflow-task-{timed['id']}-due@campusflow.local"]
    # 14:30 Asia/Shanghai = 06:30 UTC；15 分钟只是显示窗口。
    assert timed_event["DTSTART"] == "20261016T063000Z"
    assert timed_event["DTEND"] == "20261016T064500Z"
    assert timed_event["X-CAMPUSFLOW-DUE-PRECISION"] == "DATETIME"

    text = _unfold(download.content)
    assert "BEGIN:VCALENDAR" in text
    assert "VERSION:2.0" in text
    assert "CALSCALE:GREGORIAN" in text
    assert "METHOD:PUBLISH" in text
    assert "CLASS:PRIVATE" in text
    assert "PRODID:" in text
    assert "DESCRIPTION" not in text
    assert "VALARM" not in text


def test_tasks_ics_repeat_export_keeps_uid_and_times(client: TestClient) -> None:
    task = _create_task(client, "重复导出", due_date="2026-10-21", due_time="09:05")
    preview = _preview(client, [task["id"]], export_format="ics").json()

    first = _download(client, [task["id"]], preview["scope_fingerprint"], export_format="ics")
    second = _download(client, [task["id"]], preview["scope_fingerprint"], export_format="ics")
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text

    def without_timestamp(content: bytes) -> list[str]:
        return [
            line
            for line in content.decode("utf-8").split("\r\n")
            if not line.startswith("DTSTAMP:")
        ]

    assert without_timestamp(first.content) == without_timestamp(second.content)
    events = _ics_events(first.content)
    assert list(events) == [f"campusflow-task-{task['id']}-due@campusflow.local"]
    assert events[f"campusflow-task-{task['id']}-due@campusflow.local"]["DTSTART"] == (
        "20261021T010500Z"
    )


def test_ics_escapes_special_characters_and_folds_long_titles(client: TestClient) -> None:
    title = "报告,初稿;附\\录\n第二行 " + "长" * 80
    task = _create_task(client, title, due_date="2026-10-22")

    preview = _preview(client, [task["id"]], export_format="ics").json()
    download = _download(client, [task["id"]], preview["scope_fingerprint"], export_format="ics")
    assert download.status_code == 200, download.text

    text = _unfold(download.content)
    expected = "SUMMARY:报告\\,初稿\\;附\\\\录\\n第二行 " + "长" * 80
    assert expected in text

    # 折行后每行不超过 75 字节；续行以单个空格开头。
    for line in download.content.split(b"\r\n"):
        assert len(line) <= 75
    continuation = download.content.split(b"\r\n")[1:]
    assert any(line.startswith(b" ") for line in continuation)


def test_plan_ics_exports_accepted_blocks(client: TestClient, fixed_planning_clock) -> None:
    task = _create_task(
        client,
        "论文初稿",
        due_date="2026-10-19",
        due_time="19:00",
        priority="high",
        estimated_minutes=60,
        remaining_minutes=60,
    )
    _create_availability(client)
    draft = _generate_plan(client)
    accepted = _accept_plan(client, draft)
    assert accepted["status"] == "accepted"
    assert accepted["version"] == 2

    preview_response = _preview(client, None, mode="plan", export_format="ics", plan_id=draft["id"])
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["scope"]["plan_id"] == draft["id"]
    assert preview["scope"]["plan_version"] == 2
    assert preview["scope"]["range_start"] == "2026-10-19"
    assert preview["scope"]["range_end"] == "2026-10-20"
    assert preview["timezone"] == "Asia/Shanghai"
    block_events = [block for block in accepted["blocks"] if block["kind"] == "task"]
    assert preview["event_count"] == len(block_events)
    assert preview["counts"]["scheduled"] == len(block_events)
    assert preview["can_download"] is True

    download = _download(
        client,
        None,
        preview["scope_fingerprint"],
        mode="plan",
        export_format="ics",
        plan_id=draft["id"],
    )
    assert download.status_code == 200, download.text
    assert download.headers["content-type"] == "text/calendar; charset=utf-8"
    assert re.search(
        r'filename="campusflow-plan-\d{8}\.ics"',
        download.headers["content-disposition"],
    )

    events = _ics_events(download.content)
    assert len(events) == len(block_events)
    for block in block_events:
        event = events[f"campusflow-plan-{draft['id']}-block-{block['id']}@campusflow.local"]
        expected_start = (
            datetime.fromisoformat(block["starts_at"]).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        )
        expected_end = (
            datetime.fromisoformat(block["ends_at"]).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        )
        assert event["DTSTART"] == expected_start
        assert event["DTEND"] == expected_end
        assert event["SUMMARY"] == block["title"]
        assert event["SEQUENCE"] == "2"
        assert event["X-CAMPUSFLOW-PLAN-ID"] == str(draft["id"])
        assert event["X-CAMPUSFLOW-BLOCK-ID"] == str(block["id"])
        assert event["X-CAMPUSFLOW-LOCKED"] == "FALSE"
        assert event["X-CAMPUSFLOW-TASK-ID"] == str(task["id"])
        assert event["TRANSP"] == "OPAQUE"
        assert "DESCRIPTION" not in event

    # 时间块按 UTC 导出：18:00 Asia/Shanghai = 10:00 UTC。
    assert any(event["DTSTART"].endswith("T100000Z") for event in events.values())


def test_plan_ics_rejects_draft_superseded_and_missing(client: TestClient) -> None:
    _create_task(
        client,
        "草案任务",
        due_date="2026-10-19",
        due_time="19:00",
        estimated_minutes=60,
        remaining_minutes=60,
    )
    _create_availability(client)
    draft = _generate_plan(client)

    draft_preview = _preview(client, None, mode="plan", export_format="ics", plan_id=draft["id"])
    assert draft_preview.status_code == 400
    assert "接受" in draft_preview.json()["detail"]

    missing_plan = _preview(client, None, mode="plan", export_format="ics")
    assert missing_plan.status_code == 400
    assert "计划" in missing_plan.json()["detail"]

    unknown = _preview(client, None, mode="plan", export_format="ics", plan_id=999_999)
    assert unknown.status_code == 404

    with_tasks = _preview(client, [1], mode="plan", export_format="ics", plan_id=draft["id"])
    assert with_tasks.status_code == 400
    assert "任务标识" in with_tasks.json()["detail"]


def test_plan_ics_excludes_buffer_cancelled_and_warns_unscheduled(
    client: TestClient, fixed_planning_clock
) -> None:
    active = _create_task(
        client,
        "保留的任务",
        due_date="2026-10-19",
        due_time="19:00",
        estimated_minutes=60,
        remaining_minutes=60,
    )
    to_cancel = _create_task(
        client,
        "将被取消的任务",
        due_date="2026-10-20",
        due_time="19:00",
        estimated_minutes=30,
        remaining_minutes=30,
    )
    _create_task(client, "缺少耗时的任务", due_date="2026-10-20")
    _create_availability(client)
    draft = _generate_plan(client)
    _accept_plan(client, draft)

    with open_session() as session, session.begin():
        session.add(
            PlanBlock(
                draft_id=draft["id"],
                task_id=None,
                kind="buffer",
                title="提交缓冲",
                starts_at=datetime(2026, 10, 20, 10, 0, tzinfo=UTC),
                ends_at=datetime(2026, 10, 20, 10, 30, tzinfo=UTC),
                minutes=30,
                seq=99,
                locked=False,
                reason="提交缓冲不是用户活动",
                constraint_source=[],
            )
        )

    response = client.patch(
        f"/api/v1/tasks/{to_cancel['id']}/progress",
        json={"progress": "cancelled", "reason": "不再需要"},
    )
    assert response.status_code == 200, response.text

    preview_response = _preview(client, None, mode="plan", export_format="ics", plan_id=draft["id"])
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["event_count"] == 1
    assert preview["counts"]["scheduled"] == 1
    assert preview["counts"]["cancelled"] == 1
    assert preview["counts"]["unscheduled"] == 1
    excluded = {notice["code"]: notice["count"] for notice in preview["excluded"]}
    assert excluded["buffer_block"] == 1
    assert excluded["cancelled_task"] == 1
    assert excluded["unscheduled_task"] == 1
    warning_codes = {warning["code"] for warning in preview["warnings"]}
    assert {"unscheduled_task", "buffer_block", "cancelled_task"} <= warning_codes

    download = _download(
        client,
        None,
        preview["scope_fingerprint"],
        mode="plan",
        export_format="ics",
        plan_id=draft["id"],
    )
    assert download.status_code == 200, download.text
    events = _ics_events(download.content)
    assert len(events) == 1
    assert next(iter(events.values()))["X-CAMPUSFLOW-TASK-ID"] == str(active["id"])
    assert "提交缓冲" not in _unfold(download.content)
    assert "将被取消的任务" not in _unfold(download.content)
    assert "缺少耗时的任务" not in _unfold(download.content)


def test_plan_ics_fixed_events_are_opt_in(client: TestClient, fixed_planning_clock) -> None:
    _create_task(
        client,
        "计划任务",
        due_date="2026-10-19",
        due_time="19:00",
        estimated_minutes=60,
        remaining_minutes=60,
    )
    single = client.post(
        "/api/v1/fixed-events",
        json={
            "title": "操作系统实验课",
            "starts_at": "2026-10-20T14:00:00",
            "ends_at": "2026-10-20T16:00:00",
            "location": "实验楼 302",
        },
    )
    assert single.status_code == 201, single.text
    weekly = client.post(
        "/api/v1/fixed-events",
        json={
            "title": "每周组会",
            "starts_at": "2026-10-19T09:00:00",
            "ends_at": "2026-10-19T10:00:00",
            "recurrence": "weekly",
            "repeat_until": "2026-10-26",
        },
    )
    assert weekly.status_code == 201, weekly.text

    _create_availability(client)
    draft = _generate_plan(client)
    _accept_plan(client, draft)

    default_preview = _preview(
        client, None, mode="plan", export_format="ics", plan_id=draft["id"]
    ).json()
    default_download = _download(
        client,
        None,
        default_preview["scope_fingerprint"],
        mode="plan",
        export_format="ics",
        plan_id=draft["id"],
    )
    assert default_download.status_code == 200, default_download.text
    default_events = _ics_events(default_download.content)
    assert not any(uid.startswith("campusflow-fixed-") for uid in default_events)
    assert "fixed_event_disabled" in {warning["code"] for warning in default_preview["warnings"]}

    preview_response = _preview(
        client,
        None,
        mode="plan",
        export_format="ics",
        plan_id=draft["id"],
        include_fixed_events=True,
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["event_count"] == default_preview["event_count"] + 2

    download = _download(
        client,
        None,
        preview["scope_fingerprint"],
        mode="plan",
        export_format="ics",
        plan_id=draft["id"],
        include_fixed_events=True,
    )
    assert download.status_code == 200, download.text
    events = _ics_events(download.content)
    single_uid = f"campusflow-fixed-{single.json()['id']}@campusflow.local"
    weekly_uid = f"campusflow-fixed-{weekly.json()['id']}-20261019T010000Z@campusflow.local"
    assert single_uid in events
    assert weekly_uid in events
    assert events[single_uid]["LOCATION"] == "实验楼 302"
    assert events[single_uid]["DTSTART"] == "20261020T060000Z"
    assert events[single_uid]["DTEND"] == "20261020T080000Z"
    assert events[weekly_uid]["DTSTART"] == "20261019T010000Z"
    assert "RRULE" not in _unfold(download.content)
    assert "X-CAMPUSFLOW-SOURCE-HINT" not in events[single_uid]

    # 重复导出同一范围时固定日程 UID 保持稳定。
    repeat = _download(
        client,
        None,
        preview["scope_fingerprint"],
        mode="plan",
        export_format="ics",
        plan_id=draft["id"],
        include_fixed_events=True,
    )
    assert repeat.status_code == 200, repeat.text
    assert set(_ics_events(repeat.content)) == set(events)


def test_ics_fingerprint_mismatch_requires_new_preview(client: TestClient) -> None:
    task = _create_task(client, "日历原稿", due_date="2026-10-23")
    preview = _preview(client, [task["id"]], export_format="ics").json()

    updated = client.patch(f"/api/v1/tasks/{task['id']}", json={"title": "日历改稿"})
    assert updated.status_code == 200, updated.text

    stale = _download(client, [task["id"]], preview["scope_fingerprint"], export_format="ics")
    assert stale.status_code == 409
    assert "重新预览" in stale.json()["detail"]

    without_fingerprint = _download(client, [task["id"]], None, export_format="ics")
    assert without_fingerprint.status_code == 400
    assert "指纹" in without_fingerprint.json()["detail"]


def test_ics_never_contains_description_notes_or_raw_text(client: TestClient) -> None:
    description = "日历不得包含的描述原文"
    due_note = "日历不得包含的截止备注"
    task = _create_task(
        client,
        "隐私日历任务",
        description=description,
        due_note=due_note,
        due_date="2026-10-24",
        due_time="08:00",
    )

    for include_hints in (False, True):
        preview = _preview(
            client,
            [task["id"]],
            export_format="ics",
            include_source_hints=include_hints,
        ).json()
        download = _download(
            client,
            [task["id"]],
            preview["scope_fingerprint"],
            export_format="ics",
            include_source_hints=include_hints,
        )
        assert download.status_code == 200, download.text
        text = _unfold(download.content)
        assert description not in text
        assert due_note not in text
        assert "DESCRIPTION" not in text
        assert "VALARM" not in text
