"""BUG-554：粘贴文本的显式识别入口、幂等与状态展示接口。

保存与识别分开：导入文本不创建作业、不调用模型；只有用户显式启动才准备
问答索引并提取任务候选。缺模型密钥、模型失败与确实无事项必须是三种可区分结果。
"""

import json

import pytest
from index_helpers import FakeEmbedder, open_session, run_index

from campusflow.api.deps import RecognitionModels, get_recognition_models
from campusflow.domain.errors import TransientError
from campusflow.infrastructure.db.models import (
    Material,
    MaterialVersion,
    ProcessingJob,
    SourceChunk,
    Workspace,
)
from campusflow.infrastructure.storage.local import LocalFileStorage
from campusflow.workers.embeddings import set_embedder_factory

TEXT = (
    "【测试】数据库原理，测试1班：实验报告请于 2026 年 11 月 10 日 18:00 前提交，"
    "提交方式为课程平台上传 PDF。"
)


class RecordingQueue:
    """记录投递调用；测试不依赖 Redis。"""

    def __init__(self) -> None:
        self.parse_jobs: list[int] = []
        self.index_jobs: list[int] = []

    def enqueue_parse(self, job_id: int) -> str:
        self.parse_jobs.append(job_id)
        return f"parse-{job_id}"

    def enqueue_index(self, job_id: int) -> str:
        self.index_jobs.append(job_id)
        return f"index-{job_id}"


class TaskGenerator:
    """按 TASK_SPEC 生成一条有原文依据的任务候选。"""

    def __init__(self, due_text: str = "2026 年 11 月 10 日 18:00 前提交") -> None:
        self.due_text = due_text
        self.calls = 0

    def generate(self, request) -> str:
        self.calls += 1
        sources = json.loads(request.source_json)["source_chunks"]
        chunk_id = sources[0]["chunk_id"]
        fields = [
            {
                "name": "title",
                "value": "实验报告",
                "origin": "document",
                "evidence": [{"chunk_id": chunk_id, "quote": "实验报告"}],
            },
            {
                "name": "submission_method",
                "value": "课程平台上传 PDF",
                "origin": "document",
                "evidence": [{"chunk_id": chunk_id, "quote": "课程平台上传 PDF"}],
            },
            {
                "name": "deliverable",
                "value": "实验报告",
                "origin": "document",
                "evidence": [{"chunk_id": chunk_id, "quote": "实验报告"}],
            },
            {
                "name": "due_text",
                "value": self.due_text,
                "origin": "document",
                "evidence": [{"chunk_id": chunk_id, "quote": self.due_text}],
            },
            {
                "name": "estimated_minutes",
                "value": 90,
                "origin": "suggestion",
                "explanation": "按阅读与写作估计，可修改",
            },
        ]
        return json.dumps({"items": [{"kind": "task", "fields": fields}]}, ensure_ascii=False)


class RelativeTaskGenerator:
    """只提供原文中存在的字段；缺失字段保留 null 与原因，不补造。"""

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request) -> str:
        self.calls += 1
        sources = json.loads(request.source_json)["source_chunks"]
        chunk_id = sources[0]["chunk_id"]
        fields = [
            {
                "name": "title",
                "value": "实验报告",
                "origin": "document",
                "evidence": [{"chunk_id": chunk_id, "quote": "实验报告"}],
            },
            {
                "name": "submission_method",
                "value": None,
                "origin": "document",
                "missing_reason": "原文未说明提交方式",
            },
            {
                "name": "deliverable",
                "value": None,
                "origin": "document",
                "missing_reason": "原文未单独说明产出物",
            },
            {
                "name": "due_text",
                "value": "下周五提交",
                "origin": "document",
                "evidence": [{"chunk_id": chunk_id, "quote": "下周五提交"}],
            },
            {
                "name": "estimated_minutes",
                "value": None,
                "origin": "suggestion",
                "missing_reason": "没有合理依据估计耗时",
            },
        ]
        return json.dumps({"items": [{"kind": "task", "fields": fields}]}, ensure_ascii=False)


class EmptyGenerator:
    """模型明确返回没有任务；必须与调用失败区分。"""

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request) -> str:
        self.calls += 1
        return '{"items": []}'


class BrokenGenerator:
    """模拟上游临时故障，测试可见失败与可重试状态。"""

    def generate(self, request) -> str:
        raise TransientError("upstream timeout")


@pytest.fixture
def material(client, monkeypatch, tmp_path):
    """粘贴一份合成通知，并记录识别入口的队列投递。"""
    monkeypatch.setattr(client.app.state, "file_storage", LocalFileStorage(tmp_path))
    queue = RecordingQueue()
    original_queue = client.app.state.job_queue
    client.app.state.job_queue = queue
    response = client.post(
        "/api/v1/materials/text",
        json={"title": "数据库原理通知", "content": TEXT},
    )
    assert response.status_code == 201, response.text
    data = response.json()
    try:
        yield data, queue
    finally:
        client.app.state.job_queue = original_queue


def _override_models(client, generator) -> None:
    client.app.dependency_overrides[get_recognition_models] = lambda: RecognitionModels(
        embedder=FakeEmbedder(), generator=generator
    )


def _clear_models(client) -> None:
    client.app.dependency_overrides.pop(get_recognition_models, None)


def _job_rows(material_id: int) -> list[ProcessingJob]:
    with open_session() as session:
        return list(
            session.query(ProcessingJob)
            .filter(ProcessingJob.material_id == material_id)
            .order_by(ProcessingJob.id)
        )


def test_paste_text_only_saves_and_reports_next_steps(client, material) -> None:
    data, queue = material
    material_id = data["material"]["id"]
    assert data["material"]["status"] == "done"
    assert data["chunks"]
    # 仅保存原文：不自动创建作业、不自动调用模型、不自动生成候选。
    assert queue.index_jobs == []
    assert _job_rows(material_id) == []
    assert client.get("/api/v1/jobs", params={"material_id": material_id}).json() == []
    assert client.get("/api/v1/candidates").json() == []

    status = client.get(f"/api/v1/materials/{material_id}/recognition").json()
    assert status["version_no"] == 1
    assert status["chunk_count"] == len(data["chunks"])
    assert status["index"]["state"] == "not_started"
    assert status["index"]["searchable"] is False
    assert status["extraction"]["state"] == "not_started"
    assert status["extraction"]["candidate_count"] == 0
    assert status["can_start"] is True
    assert status["blocked_reason"] is None
    assert "尚未准备问答索引" in status["index"]["reason"]
    assert "尚未提取事项" in status["extraction"]["reason"]


def test_start_creates_index_job_and_pending_candidates(client, material) -> None:
    data, queue = material
    material_id = data["material"]["id"]
    generator = TaskGenerator()
    _override_models(client, generator)
    try:
        response = client.post(f"/api/v1/materials/{material_id}/recognition")
    finally:
        _clear_models(client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["started"] is True
    assert body["extraction"]["state"] == "done"
    assert body["extraction"]["candidate_count"] == 1
    assert body["extraction"]["pending_count"] == 1
    assert body["index"]["state"] == "queued"
    assert body["index"]["job_id"] is not None
    assert queue.index_jobs == [body["index"]["job_id"]]

    # 候选是待核对状态，带原文证据；不自动成为正式任务。
    candidates = client.get("/api/v1/candidates").json()
    assert len(candidates) == 1
    assert candidates[0]["review_status"] == "pending"
    assert candidates[0]["title"] == "实验报告"
    detail = client.get(f"/api/v1/candidates/{candidates[0]['id']}").json()
    fields = {item["name"]: item for item in detail["fields"]}
    assert fields["due_text"]["value"] == "2026 年 11 月 10 日 18:00 前提交"
    assert fields["due_text"]["verifiable"] is True
    assert fields["estimated_minutes"]["origin"] == "suggestion"
    assert client.get("/api/v1/tasks").json() == []

    # 索引由现有 Worker 执行；完成后状态显示可检索，而不是假装已可问答。
    run_index(body["index"]["job_id"], FakeEmbedder())
    status = client.get(f"/api/v1/materials/{material_id}/recognition").json()
    assert status["index"]["state"] == "done"
    assert status["index"]["searchable"] is True
    assert status["can_start"] is False


def test_repeat_start_does_not_duplicate_job_or_candidates(client, material) -> None:
    data, queue = material
    material_id = data["material"]["id"]
    generator = TaskGenerator()
    _override_models(client, generator)
    try:
        first = client.post(f"/api/v1/materials/{material_id}/recognition")
        second = client.post(f"/api/v1/materials/{material_id}/recognition")
    finally:
        _clear_models(client)
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["started"] is True
    assert second.json()["started"] is False
    assert generator.calls == 1
    assert len(queue.index_jobs) == 1
    assert len(_job_rows(material_id)) == 1
    assert len(client.get("/api/v1/candidates").json()) == 1


def test_missing_model_configuration_is_a_clear_business_error(client, material) -> None:
    data, queue = material
    material_id = data["material"]["id"]
    # 测试环境没有模型密钥；全局工厂必须复位，避免受其它用例影响。
    set_embedder_factory(None)
    response = client.post(f"/api/v1/materials/{material_id}/recognition")
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert "ZHIPU_API_KEY" in detail
    assert "配置" in detail
    # 未配置时没有任何副作用：不建作业、不投递、不生成候选。
    assert queue.index_jobs == []
    assert _job_rows(material_id) == []
    assert client.get("/api/v1/candidates").json() == []


def test_model_failure_is_visible_and_can_retry(client, material) -> None:
    data, _ = material
    material_id = data["material"]["id"]
    _override_models(client, BrokenGenerator())
    try:
        failed = client.post(f"/api/v1/materials/{material_id}/recognition")
    finally:
        _clear_models(client)
    assert failed.status_code == 503, failed.text
    assert "未保存任何候选事项" in failed.json()["detail"]
    status = client.get(f"/api/v1/materials/{material_id}/recognition").json()
    assert status["extraction"]["state"] == "failed"
    assert "可重新开始" in status["extraction"]["reason"]
    assert status["can_start"] is True

    generator = TaskGenerator()
    _override_models(client, generator)
    try:
        retried = client.post(f"/api/v1/materials/{material_id}/recognition")
    finally:
        _clear_models(client)
    assert retried.status_code == 200, retried.text
    assert retried.json()["started"] is True
    assert len(client.get("/api/v1/candidates").json()) == 1


def test_index_failure_is_reported_separately_from_extraction(client, material) -> None:
    """索引失败不能抹掉已经提取的候选，也不能显示成“识别全部完成”。"""
    data, _ = material
    material_id = data["material"]["id"]
    _override_models(client, TaskGenerator())
    try:
        started = client.post(f"/api/v1/materials/{material_id}/recognition")
    finally:
        _clear_models(client)
    assert started.status_code == 200, started.text
    job_id = started.json()["index"]["job_id"]

    class BrokenEmbedder(FakeEmbedder):
        def embed(self, text: str):
            raise TransientError("upstream 503")

    run_index(job_id, BrokenEmbedder())
    status = client.get(f"/api/v1/materials/{material_id}/recognition").json()
    assert status["index"]["state"] == "failed"
    assert status["extraction"]["state"] == "done"
    assert status["extraction"]["candidate_count"] == 1
    # 索引可重试，且不会重复生成候选。
    assert status["can_start"] is True


def test_empty_extraction_is_distinct_from_failure(client, material) -> None:
    data, _ = material
    material_id = data["material"]["id"]
    generator = EmptyGenerator()
    _override_models(client, generator)
    try:
        response = client.post(f"/api/v1/materials/{material_id}/recognition")
        repeated = client.post(f"/api/v1/materials/{material_id}/recognition")
    finally:
        _clear_models(client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["extraction"]["state"] == "empty"
    assert body["extraction"]["candidate_count"] == 0
    assert "不等于没有作业要求" in body["extraction"]["reason"]
    assert repeated.json()["started"] is False
    assert generator.calls == 1
    assert client.get("/api/v1/candidates").json() == []


def test_relative_deadline_stays_uncertain(client, material) -> None:
    created = client.post(
        "/api/v1/materials/text",
        json={"title": "相对日期通知", "content": "实验报告：下周五提交。"},
    )
    assert created.status_code == 201, created.text
    material_id = created.json()["material"]["id"]
    generator = RelativeTaskGenerator()
    _override_models(client, generator)
    try:
        response = client.post(f"/api/v1/materials/{material_id}/recognition")
    finally:
        _clear_models(client)
    assert response.status_code == 200, response.text
    candidate = client.get("/api/v1/candidates").json()[0]
    detail = client.get(f"/api/v1/candidates/{candidate['id']}").json()
    fields = {item["name"]: item for item in detail["fields"]}
    assert fields["due_text"]["value"] == "下周五提交"
    codes = {reason["code"] for reason in detail["review_reasons"]}
    assert {"missing_year", "relative_date", "missing_time"} <= codes
    # 不确定日期不自动补年份或时刻，也没有生成正式任务。
    assert client.get("/api/v1/tasks").json() == []


def test_archived_material_cannot_start(client, material) -> None:
    data, _ = material
    material_id = data["material"]["id"]
    assert client.post(f"/api/v1/materials/{material_id}/archive").status_code == 200
    status = client.get(f"/api/v1/materials/{material_id}/recognition").json()
    assert status["archived"] is True
    assert status["can_start"] is False
    assert "已归档" in status["blocked_reason"]

    generator = TaskGenerator()
    _override_models(client, generator)
    try:
        response = client.post(f"/api/v1/materials/{material_id}/recognition")
    finally:
        _clear_models(client)
    assert response.status_code == 400
    assert "已归档" in response.json()["detail"]
    assert generator.calls == 0


def test_foreign_material_is_not_readable_or_startable(client, material) -> None:
    """跨空间与不存在同样返回 404，不泄露其它空间资料的识别状态。"""
    with open_session() as session, session.begin():
        other = Workspace(name="另一个空间")
        session.add(other)
        session.flush()
        foreign = Material(
            workspace_id=other.id,
            title="他人的文本",
            source_type="text",
            status="done",
        )
        session.add(foreign)
        session.flush()
        version = MaterialVersion(material_id=foreign.id, version_no=1)
        session.add(version)
        session.flush()
        session.add(
            SourceChunk(
                version_id=version.id,
                seq=0,
                text="他人的原文",
                locator_type="paragraph",
                locator_value="1",
                paragraph=1,
            )
        )
        foreign_id = foreign.id

    assert client.get(f"/api/v1/materials/{foreign_id}/recognition").status_code == 404
    _override_models(client, TaskGenerator())
    try:
        response = client.post(f"/api/v1/materials/{foreign_id}/recognition")
    finally:
        _clear_models(client)
    assert response.status_code == 404
