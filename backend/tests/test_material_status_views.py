"""#555 资料状态视图：原文读取与问答索引分开，索引失败不被“完成”掩盖。

覆盖：解析成功 + 索引成功、解析成功 + 索引失败、索引部分成功、索引未开始、
索引等待/排队/进行中、无向量配置失败、旧版本失败不覆盖新版本成功。
使用合成资料与确定性假向量，不调用真实模型。
"""

import pytest
from index_helpers import (
    DEFAULT_TEXTS,
    FakeEmbedder,
    create_source,
    open_session,
    run_index,
    workspace_id,
)

from campusflow.application import indexing as indexing_use_case
from campusflow.application.ports.repositories import NewSourceChunk
from campusflow.domain.errors import DomainError, PermanentError
from campusflow.domain.states import JobStage, JobStatus, MaterialStatus
from campusflow.infrastructure.repositories import build_repositories

_PARSE_STATUS_TO_MATERIAL = {
    JobStatus.DONE: MaterialStatus.DONE,
    JobStatus.PARTIAL: MaterialStatus.PARTIAL,
    JobStatus.FAILED: MaterialStatus.FAILED,
    JobStatus.PENDING: MaterialStatus.PENDING,
    JobStatus.RUNNING: MaterialStatus.PENDING,
}


class _NoopQueue:
    """只返回投递键，不连接 Redis。"""

    def enqueue_parse(self, job_id: int) -> str:
        return f"parse-{job_id}"

    def enqueue_index(self, job_id: int) -> str:
        return f"index-{job_id}"


@pytest.fixture
def noop_queue(client):
    original = client.app.state.job_queue
    client.app.state.job_queue = _NoopQueue()
    yield
    client.app.state.job_queue = original


def _parsed_source(
    client,
    *,
    texts=DEFAULT_TEXTS,
    coverage: str = "complete",
    status: JobStatus = JobStatus.DONE,
):
    """合成一份“已解析”资料：片段、解析作业与资料状态一起给出。"""
    material_id, version_id = create_source(client, texts=texts, coverage=coverage)
    with open_session() as session, session.begin():
        repos = build_repositories(session)
        job = repos.jobs.create(workspace_id(client), material_id, version_id, JobStage.PARSE)
        repos.jobs.record_attempt(
            job.id,
            status=status,
            scope=f"完成 {len(texts)} 个片段",
            error_reason="部分范围未识别" if status is JobStatus.PARTIAL else None,
            retryable=status is JobStatus.PARTIAL,
        )
        repos.materials.set_status(material_id, _PARSE_STATUS_TO_MATERIAL[status])
    return material_id, version_id


def _create_job(client, material_id: int, version_id: int, stage: str) -> int:
    with open_session() as session, session.begin():
        return (
            build_repositories(session)
            .jobs.create(workspace_id(client), material_id, version_id, stage)
            .id
        )


def _detail(client, material_id: int) -> dict:
    response = client.get(f"/api/v1/materials/{material_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _list_item(client, material_id: int) -> dict:
    items = client.get("/api/v1/materials").json()
    return next(item for item in items if item["id"] == material_id)


def _failed_embed_job(client, material_id: int, version_id: int) -> int:
    job_id = _create_job(client, material_id, version_id, JobStage.EMBED)
    embedder = FakeEmbedder()
    embedder.fail_next(PermanentError("模型拒绝"))
    run_index(job_id, embedder)
    return job_id


def test_parse_success_and_index_success_reports_ready(client) -> None:
    material_id, version_id = _parsed_source(client)
    job_id = _create_job(client, material_id, version_id, JobStage.EMBED)
    run_index(job_id, FakeEmbedder())

    detail = _detail(client, material_id)
    assert detail["status"] == "done"  # 兼容字段仍是原文解析状态
    processing = detail["processing"]
    assert processing["text_state"] == "ready"
    index = processing["index"]
    assert index["state"] == "ready"
    assert index["reason_code"] == "index_ready"
    assert index["job_id"] == job_id
    assert index["retryable"] is False
    assert "可以基于这份资料进行问答" in index["message"]

    # 列表与详情使用同一份派生结果。
    assert _list_item(client, material_id)["processing"] == processing


def test_parse_success_but_index_failure_is_not_ready(client) -> None:
    material_id, version_id = _parsed_source(client, texts=("唯一段落。",))
    _failed_embed_job(client, material_id, version_id)

    detail = _detail(client, material_id)
    # 原文读取成功仍如实保留；不能因为索引失败把原文状态改成失败。
    assert detail["status"] == "done"
    assert detail["processing"]["text_state"] == "ready"

    index = detail["processing"]["index"]
    assert index["state"] == "failed"
    assert index["reason_code"] == "index_failed_all_blocks"
    assert index["error"] == "索引全部块失败"
    assert index["detail"] and "预期 1 块" in index["detail"]
    assert index["retryable"] is False
    assert "不能用于问答" in index["message"]
    assert "原文已读取并保留" in index["message"]

    # 列表同样不能显示为“可问答”。
    assert _list_item(client, material_id)["processing"]["index"]["state"] == "failed"


def test_partial_index_reports_partial_and_coverage(client) -> None:
    material_id, version_id = _parsed_source(client)
    _failed_embed_job(client, material_id, version_id)

    index = _detail(client, material_id)["processing"]["index"]
    assert index["state"] == "partial"
    assert index["reason_code"] == "index_partial_blocks_failed"
    assert index["detail"] and "失败 1" in index["detail"]
    assert "未覆盖或未索引内容不会被当作没有要求" in index["message"]


def test_index_not_started_is_never_reported_as_ready(client) -> None:
    material_id, _ = _parsed_source(client)

    index = _detail(client, material_id)["processing"]["index"]
    assert index["state"] == "not_started"
    assert index["reason_code"] == "index_not_started"
    assert index["job_id"] is None
    assert index["retryable"] is False
    assert "还不能用于问答" in index["message"]


def test_pasted_text_is_ready_for_reading_but_not_for_qa(client) -> None:
    response = client.post(
        "/api/v1/materials/text",
        json={"title": "粘贴通知", "content": "第一段。\n\n第二段。"},
    )
    assert response.status_code == 201, response.text
    material = response.json()["material"]

    assert material["status"] == "done"
    assert material["processing"]["text_state"] == "ready"
    index = material["processing"]["index"]
    assert index["state"] == "not_started"
    assert index["reason_code"] == "index_not_started"
    assert "粘贴文本目前不会自动建立问答索引" in index["message"]
    assert _detail(client, material["id"])["processing"] == material["processing"]


def test_waiting_delivery_queued_and_running_are_distinguished(client) -> None:
    material_id, version_id = _parsed_source(client)
    job_id = _create_job(client, material_id, version_id, JobStage.EMBED)

    waiting = _detail(client, material_id)["processing"]["index"]
    assert waiting["state"] == "preparing"
    assert waiting["reason_code"] == "index_waiting_delivery"

    with open_session() as session, session.begin():
        build_repositories(session).jobs.record_delivery(job_id, delivered=True)
    queued = _detail(client, material_id)["processing"]["index"]
    assert queued["state"] == "preparing"
    assert queued["reason_code"] == "index_queued"

    with open_session() as session, session.begin():
        assert build_repositories(session).jobs.claim(job_id) is True
    running = _detail(client, material_id)["processing"]["index"]
    assert running["state"] == "preparing"
    assert running["reason_code"] == "index_running"
    assert "不能用于问答" in running["message"]


def test_parse_queued_and_reading_are_distinguished(client) -> None:
    material_id, version_id = create_source(client)
    parse_job_id = _create_job(client, material_id, version_id, JobStage.PARSE)

    assert _detail(client, material_id)["processing"]["text_state"] == "queued"

    with open_session() as session, session.begin():
        assert build_repositories(session).jobs.claim(parse_job_id) is True
    detail = _detail(client, material_id)
    assert detail["processing"]["text_state"] == "reading"
    # 原文还没读完时，问答索引同样不能声称可用。
    assert detail["processing"]["index"]["state"] == "not_started"


def test_partial_parse_keeps_text_state_partial(client) -> None:
    material_id, _ = _parsed_source(client, coverage="partial", status=JobStatus.PARTIAL)

    detail = _detail(client, material_id)
    assert detail["status"] == "partial"
    assert detail["processing"]["text_state"] == "partial"
    assert detail["processing"]["index"]["state"] == "not_started"


def test_failed_parse_is_visible_and_index_not_started(client) -> None:
    material_id, _ = _parsed_source(client, status=JobStatus.FAILED)

    detail = _detail(client, material_id)
    assert detail["status"] == "failed"
    assert detail["processing"]["text_state"] == "failed"
    index = detail["processing"]["index"]
    assert index["state"] == "not_started"
    # 原文没读出来时不能声称“原文已保存，等待索引”。
    assert "原文未读取成功" in index["message"]


def test_missing_embedding_profile_failure_has_stable_code(client, monkeypatch) -> None:
    material_id, version_id = _parsed_source(client, texts=("唯一段落。",))
    job_id = _create_job(client, material_id, version_id, JobStage.EMBED)

    def reject_profile(*_args, **_kwargs):
        raise DomainError("向量配置未登记或与调用模型不一致")

    # 模拟索引入口在登记向量配置时失败（缺少可用向量模型配置的桌面场景）。
    monkeypatch.setattr(indexing_use_case, "register_embedder_profile", reject_profile)
    run_index(job_id, FakeEmbedder())

    index = _detail(client, material_id)["processing"]["index"]
    assert index["state"] == "failed"
    assert index["reason_code"] == "index_model_profile_mismatch"
    assert index["error"] == "向量配置未登记或与调用模型不一致"
    assert index["retryable"] is False
    assert "向量模型配置" in index["message"]


def test_new_version_success_is_not_overridden_by_old_failure(client) -> None:
    material_id, old_version_id = _parsed_source(client, texts=("旧版本段落。",))
    _failed_embed_job(client, material_id, old_version_id)
    assert _detail(client, material_id)["processing"]["index"]["state"] == "failed"

    with open_session() as session, session.begin():
        repos = build_repositories(session)
        version = repos.materials.create_version(
            material_id,
            2,
            "新版本",
            None,
            source_metadata={"coverage": {"fragment_count": 1, "failures": []}},
        )
        repos.materials.add_chunks(
            [
                NewSourceChunk(
                    version_id=version.id,
                    seq=0,
                    locator_type="paragraph",
                    locator_value="1",
                    text="新版本段落。",
                )
            ]
        )
        parse_job = repos.jobs.create(workspace_id(client), material_id, version.id, JobStage.PARSE)
        repos.jobs.record_attempt(
            parse_job.id, status=JobStatus.DONE, scope="完成 1 个片段", error_reason=None
        )
        repos.materials.set_status(material_id, MaterialStatus.DONE)
        new_embed_job_id = repos.jobs.create(
            workspace_id(client), material_id, version.id, JobStage.EMBED
        ).id

    run_index(new_embed_job_id, FakeEmbedder())
    index = _detail(client, material_id)["processing"]["index"]
    assert index["state"] == "ready"
    assert index["job_id"] == new_embed_job_id


def test_retryable_index_failure_is_retried_to_ready(client, noop_queue) -> None:
    material_id, version_id = _parsed_source(client, texts=("唯一段落。",))
    job_id = _create_job(client, material_id, version_id, JobStage.EMBED)
    with open_session() as session, session.begin():
        build_repositories(session).jobs.record_attempt(
            job_id,
            status=JobStatus.FAILED,
            scope=None,
            error_reason="处理超时：超过 5 分钟未完成，已标记为可重试失败",
            retryable=True,
        )

    failed = _detail(client, material_id)["processing"]["index"]
    assert failed["state"] == "failed"
    assert failed["reason_code"] == "index_timeout"
    assert failed["retryable"] is True

    response = client.post(f"/api/v1/jobs/{job_id}/retry")
    assert response.status_code == 200, response.text
    retried = _detail(client, material_id)["processing"]["index"]
    assert retried["state"] == "preparing"
    assert retried["reason_code"] == "index_queued"  # 已投递到队列，尚未开始处理

    # 重试执行成功后，列表与详情按当前作业记录更新为可用。
    run_index(job_id, FakeEmbedder())
    ready = _detail(client, material_id)["processing"]["index"]
    assert ready["state"] == "ready"
    assert ready["retryable"] is False


def test_archived_material_keeps_index_state_but_notes_scope(client) -> None:
    material_id, version_id = _parsed_source(client, texts=("唯一段落。",))
    job_id = _create_job(client, material_id, version_id, JobStage.EMBED)
    run_index(job_id, FakeEmbedder())

    response = client.post(f"/api/v1/materials/{material_id}/archive")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["archived"] is True
    # 归档不删除索引记录，但必须在说明里明确当前不参与问答。
    index = body["processing"]["index"]
    assert index["state"] == "ready"
    assert "资料已归档" in index["message"]
    assert all(item["id"] != material_id for item in client.get("/api/v1/materials").json())


def test_batch_job_read_returns_latest_jobs_for_all_materials(client) -> None:
    first_material, first_version = _parsed_source(client)
    second_material, second_version = _parsed_source(client)
    _create_job(client, second_material, second_version, JobStage.EMBED)

    with open_session() as session:
        jobs = build_repositories(session).jobs.latest_stage_jobs_by_materials(
            [first_material, second_material],
            stages=(JobStage.PARSE, JobStage.EMBED),
        )

    assert jobs[(first_material, JobStage.PARSE)].status == JobStatus.DONE
    assert (first_material, JobStage.EMBED) not in jobs
    assert jobs[(second_material, JobStage.EMBED)].version_id == second_version
