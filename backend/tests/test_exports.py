"""A018：任务清单 CSV 导出（真实 PostgreSQL）。

覆盖显式范围、日期精度、CSV 往返、取消任务排除、来源提示隐私、
范围指纹失效和未支持模式；不依赖外部日历或服务端留存文件。
"""

import csv
import io
import re

from fastapi.testclient import TestClient

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


def _preview(
    client: TestClient,
    task_ids: list[int] | None,
    *,
    include_source_hints: bool = False,
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
                "include_fixed_events": False,
            },
        },
    )


def _download(
    client: TestClient,
    task_ids: list[int],
    fingerprint: str | None,
    *,
    include_source_hints: bool = False,
):
    return client.post(
        "/api/v1/exports",
        json={
            "mode": "tasks",
            "format": "csv",
            "task_ids": task_ids,
            "plan_id": None,
            "options": {
                "include_source_hints": include_source_hints,
                "include_fixed_events": False,
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


def test_plan_mode_and_ics_are_rejected(client: TestClient) -> None:
    task = _create_task(client, "占位任务")

    plan_csv = _preview(client, None, mode="plan", plan_id=1)
    assert plan_csv.status_code == 400
    assert "计划导出" in plan_csv.json()["detail"]

    tasks_ics = _preview(client, [task["id"]], export_format="ics")
    assert tasks_ics.status_code == 400
    assert "ICS" in tasks_ics.json()["detail"]

    plan_ics = _preview(client, None, mode="plan", export_format="ics", plan_id=1)
    assert plan_ics.status_code == 400
    assert "计划导出" in plan_ics.json()["detail"]
