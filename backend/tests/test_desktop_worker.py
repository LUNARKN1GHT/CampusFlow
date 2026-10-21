"""P005 桌面模式验收：不依赖 Redis 的数据库轮询 Worker。

覆盖：上传→解析→索引闭环、重复投递/双 Worker 幂等、重启恢复、
桌面模块不导入 redis/rq、服务端仍选择 RQ 队列。
"""

import io
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import TEST_DATABASE_URL
from fastapi.testclient import TestClient
from pydantic import ValidationError
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from campusflow.application.jobs import retry_job
from campusflow.application.processing import ParserRegistry, set_parser_registry
from campusflow.core.config import Settings
from campusflow.domain.states import JobStage, JobStatus, MaterialStatus
from campusflow.infrastructure.db.models import IndexedBlock
from campusflow.infrastructure.llm.vision import FakeVisionParser
from campusflow.infrastructure.parsers.pdf import PdfDocumentParser
from campusflow.infrastructure.parsers.text import TextDocumentParser
from campusflow.infrastructure.parsers.word import WordDocumentParser
from campusflow.infrastructure.queue.db_queue import DBJobQueue
from campusflow.infrastructure.queue.factory import build_job_queue
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.storage.local import LocalFileStorage
from campusflow.workers.desktop import run_desktop_worker
from campusflow.workers.embeddings import build_demo_embedder, set_embedder_factory
from campusflow.workers.jobs import index_version_job, parse_version_job

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"desktop-worker"


class CountingEmbedder:
    """线程安全的计数向量器：用来断言同一作业只调用一次模型。"""

    def __init__(self, settings: Settings) -> None:
        self._inner = build_demo_embedder(settings)
        self.calls: list[str] = []
        self._lock = threading.Lock()

    @property
    def profile(self):
        return self._inner.profile

    def embed(self, value: str):
        with self._lock:
            self.calls.append(value)
        return self._inner.embed(value)


@contextmanager
def _session() -> Iterator[Session]:
    """独立短连接读取，用完释放连接池，避免长套件耗尽数据库连接。"""
    engine = create_engine(TEST_DATABASE_URL)
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


def _job(job_id: int):
    with _session() as session:
        return build_repositories(session).jobs.get(job_id)


def _material_status(material_id: int) -> str:
    with _session() as session:
        return build_repositories(session).materials.get(material_id).status


def _backdate_running(job_id: int, seconds: int) -> None:
    """模拟 Worker 崩溃：作业停在 running，开始时间与心跳都已超时。"""
    started = datetime.now(UTC) - timedelta(seconds=seconds)
    engine = create_engine(TEST_DATABASE_URL)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE processing_jobs SET status = 'running', started_at = :started, "
                    "heartbeat_at = :started WHERE id = :job_id"
                ),
                {"started": started, "job_id": job_id},
            )
    finally:
        engine.dispose()


@pytest.fixture
def desktop_queue(client: TestClient) -> DBJobQueue:
    """上传投递走数据库队列，不连接 Redis。"""
    original = client.app.state.job_queue
    client.app.state.job_queue = DBJobQueue()
    yield client.app.state.job_queue
    client.app.state.job_queue = original


@pytest.fixture
def shared_storage(client: TestClient, tmp_path, monkeypatch):
    storage = LocalFileStorage(tmp_path)
    client.app.state.file_storage = storage
    monkeypatch.setenv("CAMPUSFLOW_STORAGE_DIR", str(tmp_path))
    return storage


@pytest.fixture(autouse=True)
def worker_assembly():
    """与桌面 Worker 启动时一致的假识别与假向量装配。"""
    set_parser_registry(
        ParserRegistry(
            text_parser=TextDocumentParser(),
            pdf_parser=PdfDocumentParser(),
            vision_parser=FakeVisionParser(),
            word_parser=WordDocumentParser(),
        )
    )
    set_embedder_factory(build_demo_embedder)
    yield
    set_embedder_factory(None)


def _upload_image(client: TestClient, name: str = "桌面截图.png") -> dict:
    response = client.post(
        "/api/v1/materials/upload",
        files=[("files", (name, PNG_BYTES))],
    )
    assert response.status_code == 201, response.text
    return response.json()["results"][0]


def _text_pdf_bytes(text: str) -> bytes:
    """生成最小文本 PDF，用于桌面 Worker 的多格式闭环验证。"""
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    content = DecodedStreamObject()
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content.set_data(f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(content)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


DOCX_BYTES = (
    Path(__file__).parents[2] / "samples/synthetic/n001/word-notice-table.docx"
).read_bytes()
PDF_BYTES = _text_pdf_bytes("Homework due 2026-11-01")


# ------------------------------------------------------------ 无 Redis 闭环


def test_upload_parse_and_index_without_redis(
    client: TestClient, desktop_queue: DBJobQueue, shared_storage
) -> None:
    """桌面模式：上传后只写数据库，轮询 Worker 完成解析与索引（P005 验收）。"""
    result = _upload_image(client)
    parse_job_id = result["job_id"]
    material_id = result["material"]["id"]

    # 投递是本地标识，不连接 Redis；dispatch 仍登记投递时间，避免反复补偿
    assert desktop_queue.enqueue_parse(parse_job_id) == f"desktop-parse-{parse_job_id}"
    assert desktop_queue.enqueue_index(parse_job_id) == f"desktop-index-{parse_job_id}"
    job = _job(parse_job_id)
    assert job.status == JobStatus.PENDING
    assert job.enqueued_at is not None
    assert job.delivery_attempts == 1

    processed = run_desktop_worker(client.app.state.settings, burst=True, poll_interval=0)
    assert processed == 2  # 解析作业 + 解析后自动生成的索引作业

    assert _job(parse_job_id).status == JobStatus.DONE
    assert _material_status(material_id) == MaterialStatus.DONE

    with _session() as session:
        jobs = build_repositories(session).jobs.list_by_material(material_id)
        embed_jobs = [item for item in jobs if item.stage == JobStage.EMBED]
        assert len(embed_jobs) == 1
        embed_job_id = embed_jobs[0].id
        version_id = embed_jobs[0].version_id
    assert _job(embed_job_id).status == JobStatus.DONE

    with _session() as session:
        rows = list(session.query(IndexedBlock).filter(IndexedBlock.version_id == version_id))
        assert rows
        assert all(row.state == "done" and row.attempts == 1 for row in rows)

    # 没有新作业时 burst 直接退出，不报错
    assert run_desktop_worker(client.app.state.settings, burst=True, poll_interval=0) == 0


@pytest.mark.parametrize(
    ("name", "payload"),
    [("桌面资料.pdf", PDF_BYTES), ("桌面通知.docx", DOCX_BYTES)],
)
def test_pdf_and_docx_parse_without_redis(
    client: TestClient,
    desktop_queue: DBJobQueue,
    shared_storage,
    name: str,
    payload: bytes,
) -> None:
    """P005 验收：PDF 与 DOCX 在桌面模式下同样由数据库 Worker 完成解析与索引。"""
    response = client.post("/api/v1/materials/upload", files=[("files", (name, payload))])
    assert response.status_code == 201, response.text
    material_id = response.json()["results"][0]["material"]["id"]

    assert run_desktop_worker(client.app.state.settings, burst=True, poll_interval=0) >= 2
    assert _material_status(material_id) == MaterialStatus.DONE

    with _session() as session:
        jobs = build_repositories(session).jobs.list_by_material(material_id)
        embed_jobs = [item for item in jobs if item.stage == JobStage.EMBED]
        assert len(embed_jobs) == 1
        version_id = embed_jobs[0].version_id
    with _session() as session:
        rows = list(session.query(IndexedBlock).filter(IndexedBlock.version_id == version_id))
        assert rows
        assert all(row.state == "done" for row in rows)


def test_duplicate_delivery_and_two_workers_run_job_once(client: TestClient) -> None:
    """重复投递 / 两个 Worker 同时领取：作业只执行一次，块不翻倍（P005 验收）。"""
    from index_helpers import create_embed_job

    job_id, _material_id, version_id = create_embed_job(client)
    settings = client.app.state.settings
    counting = CountingEmbedder(settings)
    set_embedder_factory(lambda _settings: counting)

    barrier = threading.Barrier(2)

    def synchronized_runner(stage: str, target_id: int) -> None:
        # 确保两个 Worker 都已经查到同一作业，再一起尝试领取
        barrier.wait(timeout=10)
        if stage == JobStage.PARSE:
            parse_version_job(target_id)
        else:
            index_version_job(target_id)

    errors: list[BaseException] = []

    def run_worker() -> None:
        try:
            run_desktop_worker(
                settings,
                burst=True,
                poll_interval=0,
                job_runner=synchronized_runner,
                recover_on_start=False,
            )
        except BaseException as exc:  # 线程内异常要带回主线程断言
            errors.append(exc)

    threads = [threading.Thread(target=run_worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert len(counting.calls) == 2  # 两个来源块各调用一次模型
    assert _job(job_id).status == JobStatus.DONE
    assert _job(job_id).attempts == 1

    with _session() as session:
        rows = list(session.query(IndexedBlock).filter(IndexedBlock.version_id == version_id))
        assert [row.attempts for row in rows] == [1, 1]

    # 重复投递：作业已完成，再次轮询不会调用模型
    calls_before = len(counting.calls)
    assert run_desktop_worker(settings, burst=True, poll_interval=0) == 0
    assert len(counting.calls) == calls_before


# -------------------------------------------------------------- 重启恢复


def test_restart_reclaims_stale_embed_job_and_processes_it(
    client: TestClient, shared_storage
) -> None:
    """重启恢复：超时的索引作业被既有恢复扫描回收并自动重投（P005 验收）。"""
    from index_helpers import create_embed_job

    job_id, _material_id, version_id = create_embed_job(client)
    _backdate_running(job_id, seconds=3600)

    processed = run_desktop_worker(client.app.state.settings, burst=True, poll_interval=0)

    assert processed == 1
    job = _job(job_id)
    assert job.status == JobStatus.DONE
    assert job.attempts == 2  # 崩溃前的领取 + 重跑
    with _session() as session:
        rows = list(session.query(IndexedBlock).filter(IndexedBlock.version_id == version_id))
        assert rows and all(row.state == "done" for row in rows)


def test_restart_reclaims_stale_parse_job_then_retry_processes(
    client: TestClient, desktop_queue: DBJobQueue, shared_storage
) -> None:
    """重启恢复：解析作业回收为可重试失败，显式重试后由轮询 Worker 完成。"""
    result = _upload_image(client)
    job_id = result["job_id"]
    material_id = result["material"]["id"]
    _backdate_running(job_id, seconds=3600)

    # 启动扫描只回收，不静默重跑解析；此时没有可领取的 pending 作业
    assert run_desktop_worker(client.app.state.settings, burst=True, poll_interval=0) == 0
    job = _job(job_id)
    assert job.status == JobStatus.FAILED
    assert job.retryable is True
    assert _material_status(material_id) == MaterialStatus.FAILED

    with _session() as session:
        retried = retry_job(build_repositories(session), DBJobQueue(), job_id)
        assert retried is not None
        assert retried.status == JobStatus.PENDING

    assert (
        run_desktop_worker(
            client.app.state.settings, burst=True, poll_interval=0, recover_on_start=False
        )
        == 2
    )
    assert _job(job_id).status == JobStatus.DONE
    assert _material_status(material_id) == MaterialStatus.DONE


# ------------------------------------------------------------ 候选查询语义


def test_claimable_ids_exclude_running_and_permanent_failures(client: TestClient) -> None:
    """候选查询只返回 pending 的解析/索引作业，按创建顺序且受 limit 限制。"""
    from index_helpers import create_source

    workspace = client.get("/api/v1/settings").json()["workspace_id"]
    created: dict[str, int] = {}
    for name in ("parse_pending", "embed_pending", "parse_running", "embed_failed"):
        material_id, version_id = create_source(client, texts=(f"{name} 段落。",))
        with _session() as session, session.begin():
            repos = build_repositories(session)
            stage = JobStage.PARSE if name.startswith("parse") else JobStage.EMBED
            created[name] = repos.jobs.create(workspace, material_id, version_id, stage).id

    with _session() as session, session.begin():
        repos = build_repositories(session)
        assert repos.jobs.claim(created["parse_running"]) is True
        repos.jobs.record_attempt(
            created["embed_failed"],
            status=JobStatus.FAILED,
            scope=None,
            error_reason="永久失败",
            retryable=False,
        )

    with _session() as session:
        claimable = build_repositories(session).jobs.list_claimable_ids(limit=10)
    assert claimable == [created["parse_pending"], created["embed_pending"]]

    with _session() as session:
        assert build_repositories(session).jobs.list_claimable_ids(limit=1) == [
            created["parse_pending"]
        ]


# -------------------------------------------------------- 依赖隔离与队列选择


def test_desktop_modules_do_not_import_redis_or_rq(tmp_path) -> None:
    """桌面模块与桌面队列装配不导入 redis/rq（P005 验收）。"""
    backend = Path(__file__).resolve().parent.parent
    code = "\n".join(
        (
            "import sys",
            "from campusflow.core.config import Settings",
            "from campusflow.infrastructure.queue.db_queue import DBJobQueue",
            "from campusflow.infrastructure.queue.factory import build_job_queue",
            "import campusflow.workers.desktop",
            "queue = build_job_queue(Settings(_env_file=None, deployment_mode='desktop'))",
            "assert isinstance(queue, DBJobQueue)",
            "import campusflow.main",
            "assert type(campusflow.main.app.state.job_queue).__name__ == 'DBJobQueue'",
            "assert 'redis' not in sys.modules and 'rq' not in sys.modules",
            "print('desktop-isolated')",
        )
    )
    environment = os.environ.copy()
    environment["CAMPUSFLOW_DEPLOYMENT_MODE"] = "desktop"
    environment["CAMPUSFLOW_DATABASE_URL"] = TEST_DATABASE_URL
    environment["CAMPUSFLOW_DATA_DIR"] = str(tmp_path / "data")
    environment["CAMPUSFLOW_STORAGE_DIR"] = str(tmp_path / "materials")

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=backend,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert "desktop-isolated" in result.stdout


def test_server_mode_still_selects_rq_queue(client: TestClient, monkeypatch) -> None:
    """服务端模式保持 RQ：不创建连接，只验证装配类型与连接串。"""
    from campusflow.infrastructure.queue.rq_queue import RQJobQueue

    captured: dict[str, str] = {}

    def fake_init(self, redis_url: str, **kwargs) -> None:
        captured["redis_url"] = redis_url

    monkeypatch.setattr(RQJobQueue, "__init__", fake_init)
    settings = Settings(
        _env_file=None,
        database_url=TEST_DATABASE_URL,
        deployment_mode="server",
        redis_url="redis://127.0.0.1:1/0",
    )
    assert isinstance(build_job_queue(settings), RQJobQueue)
    assert captured["redis_url"] == settings.redis_url

    # 桌面模式不因 redis_url 不可用而失败，也不会连接 Redis
    desktop_settings = settings.model_copy(update={"deployment_mode": "desktop"})
    assert isinstance(build_job_queue(desktop_settings), DBJobQueue)


def test_unknown_deployment_mode_is_rejected(client: TestClient) -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url=TEST_DATABASE_URL,
            deployment_mode="unknown",
        )
