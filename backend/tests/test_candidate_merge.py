"""N016：候选合并、进度与来源保留、时间块迁移和幂等重试。"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime
from threading import Barrier

from conftest import TEST_DATABASE_URL
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from campusflow.application.candidate_merge import execute_merge, preview_merge
from campusflow.application.candidates import save_candidates
from campusflow.domain.candidates import (
    CandidateDraft,
    CandidateField,
    CandidateKind,
    FieldEvidence,
    FieldOrigin,
)
from campusflow.domain.errors import ConflictError
from campusflow.domain.states import DueSourceType, TaskPriority, TaskProgress
from campusflow.infrastructure.db.models import (
    CandidateMerge,
    Task,
    TaskProgressChange,
)
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


def merge(client, target, source, token, reason="合并重复事项"):
    return client.post(
        f"/api/v1/candidates/{target.id}/merge",
        json={
            "source_candidate_ids": [source.id],
            "preview_token": token,
            "reason": reason,
        },
    )


def setup_two_targets(client):
    """两个目标 + 一个来源，三个候选各带一个任务，用于跨目标合并。"""
    target_b = save(client, upload(client, "目标 B 的通知"))
    target_c = save(client, upload(client, "目标 C 的通知"))
    source = save(client, upload(client, "来源通知"))
    target_b_task = add_task(
        client, target_b.id, progress=TaskProgress.NOT_STARTED, title="B 目标任务"
    )
    target_c_task = add_task(
        client, target_c.id, progress=TaskProgress.NOT_STARTED, title="C 目标任务"
    )
    source_task = add_task(client, source.id, progress=TaskProgress.DONE, title="来源任务")
    return target_b, target_c, source, target_b_task, target_c_task, source_task


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


class _LaggingMergeRepository:
    """模拟并发请求在锁外读到的旧快照：前 skip 次查不到合并记录。"""

    def __init__(self, inner, skip: int) -> None:
        self.inner = inner
        self.skip = skip
        self.calls = 0

    def find_merge(self, *args):
        self.calls += 1
        if self.calls <= self.skip:
            return None
        return self.inner.find_merge(*args)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def test_concurrent_merge_unique_conflict_returns_existing_record(client):
    target, source, _, _, _ = setup_pair(client)
    body = preview(client, target, source)
    workspace_id = client.get("/api/v1/settings").json()["workspace_id"]
    # 并发请求已完成合并并提交；本请求的两次前置查询都看不到它。
    with client.app.state.session_factory() as other:
        winner = build_repositories(other).candidates.create_merge(
            workspace_id,
            target_candidate_id=target.id,
            source_candidate_id=source.id,
            target_formal_kind="task",
            target_formal_id=None,
            source_formal_id=None,
            preserved_progress={},
            source_snapshot={},
            reason="并发请求先合并",
            operator="student",
        )
        other.commit()
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        racing = replace(repos, candidates=_LaggingMergeRepository(repos.candidates, skip=2))
        result = execute_merge(
            racing,
            workspace_id,
            target_candidate_id=target.id,
            source_ids=(source.id,),
            preview_token=body["token"],
            reason="合并重复事项",
            operator="student",
        )
    assert result.created is False
    assert [record.id for record in result.records] == [winner.id]
    history = client.get(f"/api/v1/candidates/{target.id}/merges").json()
    assert len(history) == 1 and history[0]["id"] == winner.id


def test_source_cannot_be_merged_into_second_target(client):
    """#623：同一来源只能合并到一个目标；第二个目标冲突且不再迁移或取消。"""
    target_b, target_c, source, target_b_task, target_c_task, source_task = setup_two_targets(
        client
    )
    first = merge(client, target_b, source, preview(client, target_b, source)["token"])
    assert first.status_code == 200, first.text
    assert first.json()["created"] is True

    second = merge(
        client,
        target_c,
        source,
        preview(client, target_c, source)["token"],
        reason="再次合并到另一个目标",
    )
    assert second.status_code == 409, second.text
    assert "已合并到其它候选" in second.json()["detail"]

    # 第二个目标没有留下合并记录，来源任务也只被取消一次。
    assert client.get(f"/api/v1/candidates/{target_c.id}/merges").json() == []
    tasks = {item["id"]: item for item in client.get("/api/v1/tasks").json()}
    assert tasks[target_c_task]["progress"] == "not_started"
    assert tasks[target_b_task]["progress"] == "done"
    assert tasks[source_task]["progress"] == "cancelled"
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(CandidateMerge)) == 1
        cancelled = session.scalar(
            select(func.count())
            .select_from(TaskProgressChange)
            .where(
                TaskProgressChange.task_id == source_task,
                TaskProgressChange.to_progress == "cancelled",
            )
        )
        assert cancelled == 1


def test_concurrent_merges_to_different_targets_keep_single_record(client):
    """#623：并发把同一来源合并到两个目标，只有一个成功，另一个返回冲突。"""
    target_b, target_c, source, _, _, source_task = setup_two_targets(client)
    workspace = client.get("/api/v1/settings").json()["workspace_id"]
    engine = create_engine(TEST_DATABASE_URL)
    barrier = Barrier(2)

    def merge_into(target_id: int) -> tuple[str, str]:
        with Session(engine) as session:
            repos = build_repositories(session)
            token = preview_merge(repos, workspace, target_id, (source.id,)).token
            barrier.wait(timeout=10)
            try:
                execute_merge(
                    repos,
                    workspace,
                    target_candidate_id=target_id,
                    source_ids=(source.id,),
                    preview_token=token,
                    reason="并发合并同一来源",
                    operator="student",
                )
                return "merged", ""
            except ConflictError as error:
                session.rollback()
                return "conflict", str(error)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(merge_into, [target_b.id, target_c.id]))
    finally:
        engine.dispose()

    statuses = sorted(outcome for outcome, _ in outcomes)
    assert statuses == ["conflict", "merged"]
    message = next(detail for outcome, detail in outcomes if outcome == "conflict")
    # 工作空间锁生效后，落败方读到对方的合并记录，而不是先撞唯一约束。
    assert "已合并到其它候选" in message
    with client.app.state.session_factory() as session:
        records = session.scalars(select(CandidateMerge)).all()
        assert len(records) == 1
        assert records[0].source_candidate_id == source.id
        assert session.get(Task, source_task).progress == "cancelled"
        cancelled = session.scalar(
            select(func.count())
            .select_from(TaskProgressChange)
            .where(
                TaskProgressChange.task_id == source_task,
                TaskProgressChange.to_progress == "cancelled",
            )
        )
        assert cancelled == 1
