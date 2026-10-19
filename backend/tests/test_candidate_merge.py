"""N016：候选合并、进度与来源保留、时间块迁移和幂等重试。"""

from datetime import UTC, date, datetime

from campusflow.application.candidates import save_candidates
from campusflow.domain.candidates import (
    CandidateDraft,
    CandidateField,
    CandidateKind,
    FieldEvidence,
    FieldOrigin,
)
from campusflow.domain.states import DueSourceType, TaskPriority, TaskProgress
from campusflow.infrastructure.repositories import build_repositories

CONTENT = "实验报告下周五前提交。"


def upload(client, title):
    response = client.post("/api/v1/materials/text", json={"title": title, "content": CONTENT})
    assert response.status_code == 201, response.text
    return response.json()


def save(client, material, title="实验报告"):
    workspace_id = client.get("/api/v1/settings").json()["workspace_id"]
    chunk_id = material["chunks"][0]["id"]
    draft = CandidateDraft(
        CandidateKind.TASK,
        (
            CandidateField("title", title, FieldOrigin.DOCUMENT, (FieldEvidence(chunk_id, title),)),
            CandidateField(
                "due_text", "下周五", FieldOrigin.DOCUMENT, (FieldEvidence(chunk_id, "下周五"),)
            ),
        ),
    )
    with client.app.state.session_factory() as session:
        return save_candidates(
            build_repositories(session), workspace_id, material["material"]["id"], 1, [draft]
        )[0]


def add_task(client, candidate_id, *, progress, title):
    workspace_id = client.get("/api/v1/settings").json()["workspace_id"]
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        task = repos.tasks.create(
            workspace_id,
            None,
            title,
            None,
            date(2026, 11, 15),
            None,
            None,
            DueSourceType.DOCUMENT,
            None,
            TaskPriority.HIGH,
            120,
            60,
            source_candidate_id=candidate_id,
        )
        if progress is not TaskProgress.NOT_STARTED:
            repos.tasks.set_progress(task.id, progress, "测试初始进度")
        repos.uow.commit()
        return task.id


def add_block(client, task_id):
    workspace_id = client.get("/api/v1/settings").json()["workspace_id"]
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        draft = repos.plans.create_draft(
            workspace_id,
            semester_id=None,
            range_start=date(2026, 11, 1),
            range_end=date(2026, 11, 15),
            input_snapshot={},
            input_fingerprint="n016",
            assumptions=[],
            gaps=[],
            unscheduled=[],
            conflicts=[],
            coverage={},
        )
        block = repos.plans.create_blocks(
            draft.id,
            [
                {
                    "task_id": task_id,
                    "kind": "task",
                    "title": "实验报告时间块",
                    "starts_at": datetime(2026, 11, 14, 9, 0, tzinfo=UTC),
                    "ends_at": datetime(2026, 11, 14, 10, 0, tzinfo=UTC),
                    "minutes": 60,
                    "seq": 0,
                    "locked": True,
                    "reason": "用户锁定",
                    "constraint_source": [],
                }
            ],
        )[0]
        repos.uow.commit()
        return block.id


def setup_pair(client):
    target = save(client, upload(client, "目标通知"))
    source = save(client, upload(client, "来源通知"))
    target_task = add_task(client, target.id, progress=TaskProgress.NOT_STARTED, title="目标任务")
    source_task = add_task(client, source.id, progress=TaskProgress.DONE, title="来源任务")
    block_id = add_block(client, source_task)
    return target, source, target_task, source_task, block_id


def preview(client, target, source):
    response = client.get(
        f"/api/v1/candidates/{target.id}/merge-preview",
        params={"source_ids": [source.id]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_preview_and_merge_preserve_progress_sources_and_blocks(client):
    target, source, target_task, source_task, block_id = setup_pair(client)
    body = preview(client, target, source)
    assert body["auto_merge"] is False
    assert body["sources"][0]["progress"] == "done"
    assert body["sources"][0]["plan_block_count"] == 1
    response = client.post(
        f"/api/v1/candidates/{target.id}/merge",
        json={
            "source_candidate_ids": [source.id],
            "preview_token": body["token"],
            "reason": "两条通知是同一份作业",
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["created"] is True
    record = result["records"][0]
    assert record["preserved_progress"] == {
        "target_progress": "not_started",
        "source_progress": "done",
    }
    assert record["source_snapshot"]["formal_id"] == source_task
    assert client.get(f"/api/v1/candidates/{source.id}").json()["review_status"] == "superseded"
    tasks = {item["id"]: item for item in client.get("/api/v1/tasks").json()}
    assert tasks[target_task]["progress"] == "done"
    assert tasks[source_task]["progress"] == "cancelled"
    with client.app.state.session_factory() as session:
        from campusflow.infrastructure.db.models import PlanBlock

        assert session.get(PlanBlock, block_id).task_id == target_task
    history = client.get(f"/api/v1/candidates/{target.id}/merges").json()
    assert history[0]["id"] == record["id"]


def test_retry_is_idempotent_and_stale_preview_is_rejected(client):
    target, source, _, source_task, _ = setup_pair(client)
    body = preview(client, target, source)
    payload = {
        "source_candidate_ids": [source.id],
        "preview_token": body["token"],
        "reason": "合并重复事项",
    }
    first = client.post(
        f"/api/v1/candidates/{target.id}/merge",
        json={**payload, "preview_token": "stale-token"},
    )
    assert first.status_code == 409
    assert "重新预览" in first.json()["detail"]
    merged = client.post(f"/api/v1/candidates/{target.id}/merge", json=payload)
    second = client.post(f"/api/v1/candidates/{target.id}/merge", json=payload)
    assert merged.status_code == 200 and second.status_code == 200
    assert second.json()["created"] is False
    assert len(client.get(f"/api/v1/candidates/{target.id}/merges").json()) == 1
    with client.app.state.session_factory() as session:
        from campusflow.infrastructure.db.models import Task

        assert session.get(Task, source_task).progress == "cancelled"
