"""R004 并发兜底：同一候选转换请求并发时只有一条正式任务（#623）。"""

from datetime import date

from sqlalchemy import func, select

from campusflow.application.candidates import save_candidates, set_candidate_review_status
from campusflow.application.conversion import convert_candidate, convert_candidate_to_task
from campusflow.domain.candidates import (
    CandidateDraft,
    CandidateField,
    CandidateKind,
    FieldEvidence,
    FieldOrigin,
)
from campusflow.domain.states import DueSourceType, ReviewStatus, TaskPriority
from campusflow.infrastructure.db.models import FixedEvent, Task
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.storage.local import LocalFileStorage

CONTENT = "2026-10-16 提交实验报告。"
QUOTE = "实验报告"

EVENT_CONTENT = "学术讲座 2026-10-20 14:00 至 2026-10-20 16:00 在三教 301 举行。"
EVENT_QUOTE_TITLE = "学术讲座"
EVENT_QUOTE_START = "2026-10-20 14:00"
EVENT_QUOTE_END = "2026-10-20 16:00"
EVENT_QUOTE_LOCATION = "三教 301"


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


def _event_draft(chunk_id: int) -> CandidateDraft:
    return CandidateDraft(
        CandidateKind.EVENT,
        (
            CandidateField(
                "title",
                EVENT_QUOTE_TITLE,
                FieldOrigin.DOCUMENT,
                (FieldEvidence(chunk_id, EVENT_QUOTE_TITLE),),
            ),
            CandidateField(
                "activity_text",
                f"{EVENT_QUOTE_TITLE} {EVENT_QUOTE_START} 至 {EVENT_QUOTE_END}",
                FieldOrigin.DOCUMENT,
                (FieldEvidence(chunk_id, EVENT_QUOTE_START),),
            ),
            CandidateField(
                "start_text",
                EVENT_QUOTE_START,
                FieldOrigin.DOCUMENT,
                (FieldEvidence(chunk_id, EVENT_QUOTE_START),),
            ),
            CandidateField(
                "end_text",
                EVENT_QUOTE_END,
                FieldOrigin.DOCUMENT,
                (FieldEvidence(chunk_id, EVENT_QUOTE_END),),
            ),
            CandidateField(
                "location",
                EVENT_QUOTE_LOCATION,
                FieldOrigin.DOCUMENT,
                (FieldEvidence(chunk_id, EVENT_QUOTE_LOCATION),),
            ),
        ),
    )


def _confirmed_candidate(
    client, draft_factory, *, content=CONTENT, title="课程通知"
) -> tuple[int, int]:
    """上传资料并保存一个已确认候选，返回 (workspace_id, candidate_id)。"""
    response = client.post("/api/v1/materials/text", json={"title": title, "content": content})
    assert response.status_code == 201, response.text
    payload = response.json()
    workspace_id = client.get("/api/v1/settings").json()["workspace_id"]
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        candidate = save_candidates(
            repos,
            workspace_id,
            payload["material"]["id"],
            1,
            [draft_factory(payload["chunks"][0]["id"])],
        )[0]
        set_candidate_review_status(
            repos, workspace_id, candidate.id, ReviewStatus.CONFIRMED, operator="student"
        )
    return workspace_id, candidate.id


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


def test_convert_candidate_reports_created_once_for_task(client, tmp_path, monkeypatch):
    """created 只在真正新建时为 True：重复转换必须报 False 且复用同一任务。"""
    monkeypatch.setattr(client.app.state, "file_storage", LocalFileStorage(tmp_path))
    workspace_id, candidate_id = _confirmed_candidate(client, _task_draft)

    with client.app.state.session_factory() as session:
        first, created_first = convert_candidate(
            build_repositories(session), workspace_id, candidate_id
        )
    with client.app.state.session_factory() as session:
        second, created_second = convert_candidate(
            build_repositories(session), workspace_id, candidate_id
        )

    assert created_first is True
    assert created_second is False
    assert first.object_id == second.object_id
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Task)) == 1


def test_convert_candidate_created_false_when_unique_constraint_wins(client, tmp_path, monkeypatch):
    """并发兜底命中既有任务时外层仍是 created=False，不能报告新建成功。"""
    monkeypatch.setattr(client.app.state, "file_storage", LocalFileStorage(tmp_path))
    workspace_id, candidate_id = _confirmed_candidate(client, _task_draft)

    # 并发请求已为同一候选建好任务并提交；本请求前两次查询都看不到它。
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
            source_candidate_id=candidate_id,
        )
        other.commit()

    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        original_get = repos.tasks.get_by_source_candidate
        calls = {"count": 0}

        def lagging_get(candidate):
            calls["count"] += 1
            if calls["count"] <= 2:
                return None
            return original_get(candidate)

        monkeypatch.setattr(repos.tasks, "get_by_source_candidate", lagging_get)
        conversion, created = convert_candidate(repos, workspace_id, candidate_id)

    assert created is False
    assert conversion.object_id == winner.id
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Task)) == 1


def test_convert_candidate_reports_created_once_for_event(client, tmp_path, monkeypatch):
    """日程候选同样按是否真正新建返回 created，重复转换复用同一固定日程。"""
    monkeypatch.setattr(client.app.state, "file_storage", LocalFileStorage(tmp_path))
    workspace_id, candidate_id = _confirmed_candidate(
        client, _event_draft, content=EVENT_CONTENT, title="讲座通知"
    )

    with client.app.state.session_factory() as session:
        first, created_first = convert_candidate(
            build_repositories(session), workspace_id, candidate_id
        )
    with client.app.state.session_factory() as session:
        second, created_second = convert_candidate(
            build_repositories(session), workspace_id, candidate_id
        )

    assert created_first is True
    assert created_second is False
    assert first.object_id == second.object_id
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(FixedEvent)) == 1
