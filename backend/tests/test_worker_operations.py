"""J009：运行装配、诊断只读性，以及实际日志/队列异常数据不泄露原文。"""

import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import fakeredis
import pytest
from conftest import TEST_DATABASE_URL
from rq import Queue, Worker

from campusflow.core.config import Settings
from campusflow.domain.states import MaterialSourceType
from campusflow.main import compensate_pending_deliveries, reclaim_timed_out_jobs
from campusflow.workers import diagnostics
from campusflow.workers.main import build_parser_registry
from campusflow.workers.safe_worker import SafeWorker


def test_real_worker_registry_includes_scanned_pdf_fallback():
    from test_pdf_vision_fallback import _make_mixed_pdf

    settings = Settings(_env_file=None, database_url=TEST_DATABASE_URL)
    registry = build_parser_registry(settings, demo=True)
    outcome = registry.for_type(MaterialSourceType.PDF).parse(_make_mixed_pdf(), "application/pdf")
    assert outcome.failures == []
    assert {fragment.locator.page for fragment in outcome.fragments} == {1, 2}


def test_demo_cannot_write_fake_recognition_to_business_database():
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://localhost/campusflow")
    with pytest.raises(ValueError, match="专用测试库"):
        build_parser_registry(settings, demo=True)


def test_real_worker_requires_key(monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ZHIPU_API_KEY"):
        build_parser_registry(Settings(_env_file=None))


def test_real_worker_passes_vision_limits(monkeypatch):
    """Q009：环境变量里的视觉超时/重试配置必须真正传到适配器。"""
    from campusflow.workers import main as worker_main

    parser_class = Mock(return_value=Mock())
    monkeypatch.setattr(worker_main, "ZhipuVisionParser", parser_class)
    settings = Settings(
        _env_file=None,
        database_url=TEST_DATABASE_URL,
        zhipu_api_key="test-key",
        vision_timeout_seconds=30,
        vision_max_retries=1,
        vision_retry_backoff_seconds=2,
    )
    build_parser_registry(settings)
    parser_class.assert_called_once_with(
        "test-key", timeout_seconds=30, max_retries=1, backoff_seconds=2
    )


def test_worker_exception_logs_and_redis_record_exclude_raw_error(monkeypatch, caplog):
    # Alembic 测试夹具的 fileConfig 会禁用先前创建的 logger；恢复本次观察对象。
    monkeypatch.setattr(logging.getLogger("rq.worker"), "disabled", False)
    monkeypatch.setattr(logging.getLogger("rq.worker"), "propagate", True)
    connection = fakeredis.FakeRedis()
    queue = Queue("privacy-test", connection=connection)
    worker = SafeWorker([queue], connection=connection)
    job = SimpleNamespace(id="parse-123")
    secret = "Bearer private-test-token 13812345678 private-original-text"
    error = RuntimeError(secret)
    persist = Mock()
    monkeypatch.setattr(Worker, "handle_job_failure", persist)
    with caplog.at_level(logging.ERROR):
        worker.handle_exception(job, type(error), error, None)
    worker.handle_job_failure(job, queue, exc_string=secret)
    assert "parse-123" in caplog.text
    assert "processing_failed" in caplog.text
    assert secret not in caplog.text
    assert "private-original-text" not in str(persist.call_args)
    assert "private-test-token" not in str(persist.call_args)


def test_startup_failures_do_not_log_database_exception(caplog, monkeypatch):
    monkeypatch.setattr(logging.getLogger("campusflow.main"), "disabled", False)
    monkeypatch.setattr(logging.getLogger("campusflow.main"), "propagate", True)
    app = SimpleNamespace(state=SimpleNamespace(settings=Settings(_env_file=None)))
    app.state.settings.dispatch_on_startup = True
    app.state.session_factory = Mock(side_effect=RuntimeError("private-original-text"))
    with caplog.at_level(logging.WARNING):
        assert compensate_pending_deliveries(app) == 0
        assert reclaim_timed_out_jobs(app) == 0
    assert "private-original-text" not in caplog.text
    assert "dispatch_failed" in caplog.text
    assert "reclaim_failed" in caplog.text


def test_diagnostics_is_read_only_and_has_no_credentials(client, tmp_path, monkeypatch):
    connection = fakeredis.FakeRedis()
    queue = Queue("campusflow-parse", connection=connection)
    queue.enqueue("campusflow.workers.jobs.parse_version_job", 123)
    before = {key: connection.dump(key) for key in connection.keys()}
    monkeypatch.setattr(diagnostics.redis.Redis, "from_url", lambda *a, **kw: connection)
    settings = Settings(
        _env_file=None,
        database_url=TEST_DATABASE_URL,
        storage_dir=str(tmp_path),
        ZHIPU_API_KEY="private-model-key",
    )
    report = diagnostics.collect_diagnostics(settings)
    assert report["database"] == {"ok": True, "schema_current": True}
    assert report["redis"]["queued"] == 1
    assert report["ready"] is True
    assert "private-model-key" not in json.dumps(report)
    assert TEST_DATABASE_URL not in json.dumps(report)
    after = {key: connection.dump(key) for key in connection.keys()}
    assert before == after


def test_diagnostics_reports_failure_without_raw_exception(tmp_path, monkeypatch):
    engine = Mock()
    engine.connect.side_effect = RuntimeError("private-database-password")
    monkeypatch.setattr(diagnostics, "create_db_engine", lambda settings: engine)
    monkeypatch.setattr(
        diagnostics.redis.Redis, "from_url", Mock(side_effect=ValueError("private-redis-password"))
    )
    report = diagnostics.collect_diagnostics(Settings(_env_file=None, storage_dir=str(tmp_path)))
    assert report["ready"] is False
    assert "private-" not in json.dumps(report)
    engine.dispose.assert_called_once()
