"""D009 集成测试：确认后的资料删除与派生失效。

验收：
- 文件、片段、索引和候选结果均清理或失效；
- 保留的任务不会静默消失或仍显示来源可验证。

本文件覆盖已经存在的派生内容（原文件、版本、片段、处理作业与尝试历史）；
向量索引与候选结果分别属于 M3 与 R 系列，尚未建表，等它们落地时按同一条
级联规则挂在 materials / material_versions 下即可。
"""

import pytest
from conftest import TEST_DATABASE_URL
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from campusflow.application.processing import ParserRegistry, set_parser_registry
from campusflow.domain.materials import (
    MAX_SOURCE_NOTE_LENGTH,
    SOURCE_STATE_AVAILABLE,
    SOURCE_STATE_INVALIDATED,
    SOURCE_STATE_NONE,
    invalidated_source_note,
    source_state,
)
from campusflow.infrastructure.llm.vision import FakeVisionParser
from campusflow.infrastructure.parsers.pdf import PdfDocumentParser
from campusflow.infrastructure.parsers.text import TextDocumentParser
from campusflow.infrastructure.storage.local import LocalFileStorage
from campusflow.workers.jobs import parse_version_job

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"delete-invalidation"
TEXT = "删除测试内容。\n\n第二段。"

# 直接查表：确认派生内容真的从数据库里消失了
DERIVED_ROW_TABLES = (
    ("materials", "SELECT count(*) FROM materials WHERE id = :id"),
    ("material_versions", "SELECT count(*) FROM material_versions WHERE material_id = :id"),
    ("source_chunks", "SELECT count(*) FROM source_chunks"),
    ("processing_jobs", "SELECT count(*) FROM processing_jobs WHERE material_id = :id"),
    ("processing_job_attempts", "SELECT count(*) FROM processing_job_attempts"),
)


class RecordingQueue:
    """只记录投递，不连接 Redis。"""

    def __init__(self) -> None:
        self.enqueued: list[int] = []

    def enqueue_parse(self, job_id: int) -> str:
        self.enqueued.append(job_id)
        return f"parse-{job_id}"


@pytest.fixture(autouse=True)
def local_queue(client: TestClient):
    original = client.app.state.job_queue
    client.app.state.job_queue = RecordingQueue()
    set_parser_registry(
        ParserRegistry(
            text_parser=TextDocumentParser(),
            pdf_parser=PdfDocumentParser(),
            vision_parser=FakeVisionParser(),
        )
    )
    yield
    client.app.state.job_queue = original


@pytest.fixture
def shared_storage(client: TestClient, tmp_path, monkeypatch):
    storage = LocalFileStorage(tmp_path)
    client.app.state.file_storage = storage
    monkeypatch.setenv("CAMPUSFLOW_STORAGE_DIR", str(tmp_path))
    return storage


def _import_text(client: TestClient, title: str = "待删除资料") -> dict:
    response = client.post("/api/v1/materials/text", json={"title": title, "content": TEXT})
    assert response.status_code == 201, response.text
    return response.json()


def _upload_image(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/materials/upload",
        files=[("files", ("通知.png", PNG_BYTES))],
    )
    assert response.status_code == 201, response.text
    return response.json()["results"][0]


def _count(sql: str, **params) -> int:
    engine = create_engine(TEST_DATABASE_URL)
    with engine.connect() as connection:
        return int(connection.execute(text(sql), params).scalar_one())


def _link_task(client: TestClient, material_id: int, title: str = "关联任务") -> dict:
    """创建任务并直接关联到资料（模拟 R 系列将来写入的来源关系）。"""
    task = client.post("/api/v1/tasks", json={"title": title}).json()
    engine = create_engine(TEST_DATABASE_URL)
    with engine.connect() as connection:
        connection.execute(
            text("UPDATE tasks SET source_material_id = :m WHERE id = :t"),
            {"m": material_id, "t": task["id"]},
        )
        connection.commit()
    return task


def _task(client: TestClient, task_id: int) -> dict:
    response = client.get(f"/api/v1/tasks/{task_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _delete(client: TestClient, material_id: int, *, keep_tasks: bool) -> None:
    impact = client.get(f"/api/v1/materials/{material_id}/delete-impact")
    assert impact.status_code == 200, impact.text
    response = client.post(
        f"/api/v1/materials/{material_id}/delete",
        json={"confirm_token": impact.json()["confirm_token"], "keep_tasks": keep_tasks},
    )
    assert response.status_code == 204, response.text


# ------------------------------------------------------------------- 领域规则


def test_source_state_has_three_distinct_values() -> None:
    """来源状态三态互不混淆（D009）：有来源 / 来源已失效 / 本来就没有来源。"""
    assert source_state(7, None) == SOURCE_STATE_AVAILABLE
    assert source_state(None, "2026-10-12T10:00:00Z") == SOURCE_STATE_INVALIDATED
    assert source_state(None, None) == SOURCE_STATE_NONE
    # 有来源时即使带着旧的失效痕迹，也以"还有来源"为准
    assert source_state(7, "2026-10-12T10:00:00Z") == SOURCE_STATE_AVAILABLE


def test_invalidated_note_names_material_and_stays_within_column() -> None:
    """失效说明指出原资料，且不超出列宽（D009）。"""
    assert "数据库作业三" in invalidated_source_note("数据库作业三")
    assert "已删除" in invalidated_source_note("数据库作业三")
    long_note = invalidated_source_note("超长标题" * 200)
    assert len(long_note) <= MAX_SOURCE_NOTE_LENGTH
    # 空标题也不留空说明
    assert invalidated_source_note("   ").strip() != ""


# ------------------------------------------------------- 保留任务的来源标记


def test_kept_task_records_invalidated_source(client: TestClient) -> None:
    """保留个人任务：任务留下"来源已被删除"的记录（D009 验收）。"""
    imported = _import_text(client, title="数据库作业三")
    material_id = imported["material"]["id"]
    task = _link_task(client, material_id)

    before = _task(client, task["id"])
    assert before["source_state"] == SOURCE_STATE_AVAILABLE
    assert before["source_invalidated_at"] is None

    _delete(client, material_id, keep_tasks=True)

    after = _task(client, task["id"])
    assert after["title"] == "关联任务"  # 不静默消失
    assert after["source_state"] == SOURCE_STATE_INVALIDATED  # 也不再声称来源可验证
    assert after["source_invalidated_at"] is not None
    assert "数据库作业三" in after["source_invalidated_note"]

    # 未受影响的普通任务保持"没有来源"，不会被误标成失效
    plain = client.post("/api/v1/tasks", json={"title": "手工任务"}).json()
    assert plain["source_state"] == SOURCE_STATE_NONE
    assert _task(client, plain["id"])["source_invalidated_at"] is None


def test_archived_material_keeps_source_available(client: TestClient) -> None:
    """归档不是删除（D009 依据 README §6）：归档后来源仍然有效。"""
    imported = _import_text(client, title="归档资料")
    material_id = imported["material"]["id"]
    task = _link_task(client, material_id)

    response = client.post(f"/api/v1/materials/{material_id}/archive")
    assert response.status_code == 200, response.text

    after = _task(client, task["id"])
    assert after["source_state"] == SOURCE_STATE_AVAILABLE
    assert after["source_invalidated_at"] is None
    assert client.get(f"/api/v1/materials/{material_id}").status_code == 200


def test_delete_with_keep_tasks_false_leaves_no_trace(client: TestClient) -> None:
    """一并删除：任务随资料消失，不留失效痕迹（D009）。"""
    imported = _import_text(client)
    material_id = imported["material"]["id"]
    task = _link_task(client, material_id)

    _delete(client, material_id, keep_tasks=False)

    assert client.get(f"/api/v1/tasks/{task['id']}").status_code == 404
    assert _count("SELECT count(*) FROM tasks WHERE id = :id", id=task["id"]) == 0


# --------------------------------------------------------- 派生记录一并清理


def test_delete_removes_derived_records_and_original_file(
    client: TestClient, shared_storage, tmp_path
) -> None:
    """文件、版本、片段、作业与尝试历史全部清理（D009 验收）。"""
    uploaded = _upload_image(client)
    material_id = uploaded["material"]["id"]
    job_id = uploaded["job_id"]
    parse_version_job(job_id)  # 先让派生内容真的长出来

    assert any(path.is_file() for path in tmp_path.rglob("*"))
    assert _count("SELECT count(*) FROM source_chunks") == 1
    assert _count("SELECT count(*) FROM processing_job_attempts") == 1

    _delete(client, material_id, keep_tasks=True)

    for name, sql in DERIVED_ROW_TABLES:
        assert _count(sql, id=material_id) == 0, f"{name} 仍有残留"
    assert _count("SELECT count(*) FROM processing_jobs WHERE id = :id", id=job_id) == 0
    assert not any(path.is_file() for path in tmp_path.rglob("*"))
    assert client.get(f"/api/v1/materials/{material_id}").status_code == 404


def test_delete_while_job_still_pending_clears_job(
    client: TestClient, shared_storage, tmp_path
) -> None:
    """作业还没跑完就删除：作业与文件一并消失，不留指向已删资料的行（D009）。"""
    uploaded = _upload_image(client)
    material_id = uploaded["material"]["id"]
    job_id = uploaded["job_id"]
    assert _count("SELECT count(*) FROM processing_jobs WHERE id = :id", id=job_id) == 1

    _delete(client, material_id, keep_tasks=True)

    assert _count("SELECT count(*) FROM processing_jobs WHERE id = :id", id=job_id) == 0
    assert not any(path.is_file() for path in tmp_path.rglob("*"))


# ------------------------------------------------------- 预览里的未完成作业


def test_delete_impact_counts_unfinished_jobs(client: TestClient, shared_storage) -> None:
    """删除预览列出还没跑完的作业，跑完后归零（D009）。"""
    uploaded = _upload_image(client)
    material_id = uploaded["material"]["id"]
    job_id = uploaded["job_id"]

    pending = client.get(f"/api/v1/materials/{material_id}/delete-impact").json()
    assert pending["unfinished_job_count"] == 1  # 作业还没跑

    parse_version_job(job_id)
    finished = client.get(f"/api/v1/materials/{material_id}/delete-impact").json()
    assert finished["unfinished_job_count"] == 0  # 跑完了，不再提示
    # 已完成作业仍然会被删除连带清掉，只是不必再提示"还有活在跑"
    assert _count("SELECT count(*) FROM processing_jobs WHERE id = :id", id=job_id) == 1

    _delete(client, material_id, keep_tasks=True)
    assert _count("SELECT count(*) FROM processing_jobs WHERE id = :id", id=job_id) == 0
