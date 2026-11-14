"""R004 并发兜底：同一候选转换请求并发时只有一条正式任务（#623）。"""

from datetime import date

from sqlalchemy import func, select

from campusflow.application.candidates import save_candidates, set_candidate_review_status
from campusflow.application.conversion import convert_candidate_to_task
from campusflow.domain.candidates import (
    CandidateDraft,
    CandidateField,
    CandidateKind,
    FieldEvidence,
    FieldOrigin,
)
from campusflow.domain.states import DueSourceType, ReviewStatus, TaskPriority
from campusflow.infrastructure.db.models import Task
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.storage.local import LocalFileStorage

CONTENT = "2026-10-16 提交实验报告。"
QUOTE = "实验报告"


def _task_draft(chunk_id: int) -> CandidateDraft:
    return CandidateDraft(
        CandidateKind.TASK,
        (
            CandidateField("title", QUOTE, FieldOrigin.DOCUMENT, (FieldEvidence(chunk_id, QUOTE),)),
            CandidateField(
                "due_text", "2026-10-16", FieldOrigin.DOCUMENT, (FieldEvidence(chunk_id, QUOTE),)
            ),
        ),
    )


def test_concurrent_conversion_unique_conflict_returns_existing_task(client, tmp_path, monkeypatch):
    monkeypatch.setattr(client.app.state, "file_storage", LocalFileStorage(tmp_path))
    response = client.post("/api/v1/materials/text", json={"title": "课程通知", "content": CONTENT})
    assert response.status_code == 201, response.text
    payload = response.json()
    workspace_id = client.get("/api/v1/settings").json()["workspace_id"]
    material_id = payload["material"]["id"]
    chunk_id = payload["chunks"][0]["id"]
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        candidate = save_candidates(repos, workspace_id, material_id, 1, [_task_draft(chunk_id)])[0]
        set_candidate_review_status(
            repos, workspace_id, candidate.id, ReviewStatus.CONFIRMED, operator="student"
        )

    # 并发请求已为同一候选建好任务并提交；本请求先查时仍看不到它。
    with client.app.state.session_factory() as other:
        winner = build_repositories(other).tasks.create(
            workspace_id,
            None,
            "实验报告",
            None,
            date(2026, 10, 16),
            None,
            None,
            DueSourceType.DOCUMENT,
            None,
            TaskPriority.MEDIUM,
            90,
            90,
            source_candidate_id=candidate.id,
        )
        other.commit()

    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        original_get = repos.tasks.get_by_source_candidate
        calls = {"count": 0}

        def lagging_get(candidate_id):
            calls["count"] += 1
            if calls["count"] == 1:
                return None
            return original_get(candidate_id)

        monkeypatch.setattr(repos.tasks, "get_by_source_candidate", lagging_get)
        task = convert_candidate_to_task(repos, workspace_id, candidate.id)

    assert task.id == winner.id
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Task)) == 1
