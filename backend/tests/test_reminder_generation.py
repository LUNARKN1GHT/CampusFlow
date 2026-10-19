"""A011：按截止精度与核对状态生成提醒。"""

from datetime import date, timedelta

from conftest import TEST_DATABASE_URL
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

TEXT = "实验报告于 2026-11-20 18:00 前提交。"


class RecordingQueue:
    def __init__(self) -> None:
        self.enqueued: list[int] = []

    def enqueue_parse(self, job_id: int) -> str:
        self.enqueued.append(job_id)
        return f"parse-{job_id}"

    def enqueue_index(self, job_id: int) -> str:
        return f"index-{job_id}"


def _use_local_queue(client: TestClient) -> None:
    client.app.state.job_queue = RecordingQueue()


def _task(client: TestClient, **overrides) -> dict:
    payload = {
        "title": "提醒任务",
        "due_date": (date.today() + timedelta(days=20)).isoformat(),
        "due_source": "document",
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


def _import_text(client: TestClient) -> dict:
    _use_local_queue(client)
    response = client.post("/api/v1/materials/text", json={"title": "通知原文", "content": TEXT})
    assert response.status_code == 201, response.text
    return response.json()["material"]


def _source_ids(material_id: int) -> tuple[int, int]:
    engine = create_engine(TEST_DATABASE_URL)
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT m.workspace_id, v.id FROM materials m "
                "JOIN material_versions v ON v.material_id = m.id "
                "WHERE m.id = :id ORDER BY v.id LIMIT 1"
            ),
            {"id": material_id},
        ).one()
        return int(row[0]), int(row[1])


def _link_task(task_id: int, material_id: int) -> None:
    engine = create_engine(TEST_DATABASE_URL)
    with engine.connect() as connection:
        connection.execute(
            text("UPDATE tasks SET source_material_id = :m WHERE id = :t"),
            {"m": material_id, "t": task_id},
        )
        connection.commit()


def _insert_candidate(workspace_id: int, version_id: int) -> int:
    engine = create_engine(TEST_DATABASE_URL)
    with engine.connect() as connection:
        candidate_id = connection.execute(
            text(
                "INSERT INTO extracted_items "
                "(workspace_id, source_version_id, kind, review_status, review_reasons) "
                "VALUES (:w, :v, 'task', 'pending', '[]'::jsonb) RETURNING id"
            ),
            {"w": workspace_id, "v": version_id},
        ).scalar_one()
        connection.commit()
        return int(candidate_id)


def _delete_material(client: TestClient, material_id: int) -> None:
    impact = client.get(f"/api/v1/materials/{material_id}/delete-impact")
    assert impact.status_code == 200, impact.text
    response = client.post(
        f"/api/v1/materials/{material_id}/delete",
        json={"confirm_token": impact.json()["confirm_token"], "keep_tasks": True},
    )
    assert response.status_code == 204, response.text


def test_date_only_ddl_uses_no_precise_time(client: TestClient) -> None:
    due = (date.today() + timedelta(days=10)).isoformat()
    _task(client, due_date=due, due_time=None)
    _rule(client, advance_minutes=1440)

    [item] = _reminders(client, status="pending")
    assert item["kind"] == "deadline"
    assert item["payload"]["date_only"] is True
    assert item["payload"]["due_time"] is None
    assert due in item["message"]
    assert "未指定具体时刻" in item["message"]
    assert "00:00" not in item["message"]
    assert "23:59" not in item["message"]
    assert ":" not in item["message"]


def test_invalidated_source_replaces_definite_deadline(client: TestClient) -> None:
    material = _import_text(client)
    _source_ids(material["id"])
    due = (date.today() + timedelta(days=15)).isoformat()
    task = _task(client, due_date=due, due_time="18:00:00")
    _link_task(task["id"], material["id"])
    _rule(client, advance_minutes=60)

    [deadline] = _reminders(client, status="pending")
    assert deadline["kind"] == "deadline"
    assert "18:00" in deadline["message"]

    _delete_material(client, material["id"])

    review = [item for item in _reminders(client) if item["kind"] == "review"]
    assert len(review) == 1
    assert review[0]["status"] == "sent"
    assert review[0]["payload"]["source_invalidated"] is True
    assert review[0]["payload"]["uncertain"] is True
    assert "来源资料已失效" in review[0]["message"]
    assert "待核对" in review[0]["message"]
    assert "将在" not in review[0]["message"]

    cancelled = _reminders(client, status="cancelled")
    assert [item["id"] for item in cancelled] == [deadline["id"]]
    assert _reminders(client, status="pending") == []


def test_pending_review_reminder_tracks_review_state(client: TestClient) -> None:
    material = _import_text(client)
    workspace_id, version_id = _source_ids(material["id"])
    candidate_id = _insert_candidate(workspace_id, version_id)

    [review] = _reminders(client)
    assert review["kind"] == "review"
    assert review["task_id"] is None
    assert review["status"] == "sent"
    assert review["payload"]["pending_review_count"] == 1
    assert "待核对" in review["message"]
    assert "确认前不会作为确定约束参与排期" in review["message"]
    assert len(_reminders(client)) == 1

    engine = create_engine(TEST_DATABASE_URL)
    with engine.connect() as connection:
        connection.execute(
            text("UPDATE extracted_items SET review_status = 'confirmed' WHERE id = :id"),
            {"id": candidate_id},
        )
        connection.commit()

    assert _reminders(client, status="pending") == []
    # 已发送的提醒保留为历史记录，只撤消尚未发送的过期提醒
    [history] = _reminders(client)
    assert history["id"] == review["id"]
    assert history["status"] == "sent"
