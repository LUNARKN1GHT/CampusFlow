"""N013：通知变更影响的任务、日程、规则与计划时间块。"""

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


def upload(client, title="旧通知"):
    response = client.post(
        "/api/v1/materials/text",
        json={"title": title, "content": CONTENT},
    )
    assert response.status_code == 201, response.text
    return response.json()


def save_candidate(client, material):
    workspace_id = client.get("/api/v1/settings").json()["workspace_id"]
    chunk_id = material["chunks"][0]["id"]
    draft = CandidateDraft(
        CandidateKind.TASK,
        (
            CandidateField(
                "title", "实验报告", FieldOrigin.DOCUMENT, (FieldEvidence(chunk_id, "实验报告"),)
            ),
            CandidateField(
                "due_text", "下周五", FieldOrigin.DOCUMENT, (FieldEvidence(chunk_id, "下周五"),)
            ),
        ),
    )
    with client.app.state.session_factory() as session:
        return save_candidates(
            build_repositories(session), workspace_id, material["material"]["id"], 1, [draft]
        )[0]


def add_formal_links(client, candidate_id):
    workspace_id = client.get("/api/v1/settings").json()["workspace_id"]
    with client.app.state.session_factory() as session, session.begin():
        repos = build_repositories(session)
        task = repos.tasks.create(
            workspace_id,
            None,
            "提交实验报告",
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
        draft = repos.plans.create_draft(
            workspace_id,
            semester_id=None,
            range_start=date(2026, 11, 1),
            range_end=date(2026, 11, 15),
            input_snapshot={"source": "n013"},
            input_fingerprint="n013-fingerprint",
            assumptions=[],
            gaps=[],
            unscheduled=[],
            conflicts=[],
            coverage={},
        )
        repos.plans.create_blocks(
            draft.id,
            [
                {
                    "task_id": task.id,
                    "kind": "task",
                    "title": "提交实验报告",
                    "starts_at": datetime(2026, 11, 14, 9, 0, tzinfo=UTC),
                    "ends_at": datetime(2026, 11, 14, 10, 0, tzinfo=UTC),
                    "minutes": 60,
                    "seq": 0,
                    "locked": True,
                    "reason": "用户锁定",
                    "constraint_source": [],
                }
            ],
        )
        return task.id


def test_completed_task_and_locked_block_are_listed_with_unsupported_boundaries(client):
    old = upload(client)
    new = upload(client, "新通知")
    left = save_candidate(client, old)
    right = save_candidate(client, new)
    task_id = add_formal_links(client, left.id)
    with client.app.state.session_factory() as session, session.begin():
        from campusflow.infrastructure.db.models import Task

        session.get(Task, task_id).progress = TaskProgress.DONE

    response = client.get(f"/api/v1/candidates/{left.id}/diff/{right.id}/impact")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"]["task_count"] == 1
    assert body["summary"]["completed_task_count"] == 1
    assert body["summary"]["locked_block_count"] == 1
    task = body["tasks"][0]
    assert task["id"] == task_id
    assert task["progress"] == "done" and task["completed"] is True
    assert task["plan_blocks"][0]["locked"] is True
    assert task["plan_blocks"][0]["draft_status"] == "draft"
    assert {item["code"] for item in body["unsupported"]} == {
        "reminders",
        "external_calendar",
    }
    assert client.get(f"/api/v1/candidates/{left.id}").json()["review_status"] == "pending"


def test_impact_does_not_include_unconverted_or_other_workspace_items(client):
    old = upload(client)
    new = upload(client, "新通知")
    left = save_candidate(client, old)
    right = save_candidate(client, new)
    response = client.get(f"/api/v1/candidates/{left.id}/diff/{right.id}/impact")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tasks"] == [] and body["fixed_events"] == [] and body["rules"] == []
    assert body["summary"]["task_count"] == 0
