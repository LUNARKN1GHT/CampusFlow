"""N005：网络替身 + 真实 PostgreSQL 和私有存储验证快照及授权。"""

from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from campusflow.api.materials import get_web_fetcher
from campusflow.application.ports.web_fetch import FetchedWebPage, WebFetchError
from campusflow.infrastructure.db.models import Course, Semester, Workspace
from campusflow.infrastructure.storage.local import LocalFileStorage

BODY = (Path(__file__).parents[2] / "samples/synthetic/n001/public-notice.html").read_bytes()
URL = "https://example.com/notice"


@pytest.fixture
def web_client(client, tmp_path, monkeypatch):
    fetcher = Mock()
    fetcher.fetch.return_value = FetchedWebPage(
        URL, URL + "?final=1", "text/html; charset=utf-8", BODY
    )
    monkeypatch.setattr(client.app.state, "file_storage", LocalFileStorage(tmp_path))
    monkeypatch.setattr(
        client.app.state, "job_queue", Mock(enqueue_parse=Mock(return_value="web-job"))
    )
    client.app.dependency_overrides[get_web_fetcher] = lambda: fetcher
    yield client, fetcher, tmp_path
    client.app.dependency_overrides.pop(get_web_fetcher, None)


def imported(client, **fields):
    response = client.post(
        "/api/v1/materials/web", json={"url": URL, "title": "考试通知", **fields}
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_snapshot_keeps_urls_times_and_unknown_publication_distinct(web_client):
    client, fetcher, _ = web_client
    result = imported(client)
    fetcher.fetch.assert_called_once_with(URL)
    metadata = result["version"]["source_metadata"]
    assert metadata["requested_url"] == URL and metadata["final_url"].endswith("final=1")
    assert metadata["fetched_at"]
    assert metadata["published_at"] is None
    assert result["material"]["published_at"] is None
    assert result["material"]["source_type"] == "web"
    assert result["material"]["status"] == "pending"
    mid = result["material"]["id"]
    download = client.get(f"/api/v1/materials/{mid}/versions/1/file")
    assert download.content == BODY
    assert "attachment" in download.headers["content-disposition"]
    assert "sandbox" in download.headers["content-security-policy"]
    assert download.headers["x-content-type-options"] == "nosniff"


def test_date_only_publication_is_not_invented_as_midnight(web_client):
    client, _, _ = web_client
    result = imported(client, publisher="教务处", published_text="2026-11-20")
    metadata = result["version"]["source_metadata"]
    assert metadata["published_text"] == "2026-11-20" and metadata["published_at"] is None
    assert metadata["publication_basis"] == "user_provided"
    bad = client.post(
        "/api/v1/materials/web",
        json={"url": URL, "title": "通知", "published_at": "2026-11-20T00:00:00"},
    )
    assert bad.status_code == 422


def test_changed_website_and_edited_metadata_do_not_replace_saved_source(web_client):
    client, fetcher, _ = web_client
    first = imported(client, publisher="旧发布方")
    fetcher.fetch.return_value = FetchedWebPage(URL, URL, "text/html", b"<p>new notice</p>")
    second = imported(client, publisher="新发布方")
    first_id, second_id = first["material"]["id"], second["material"]["id"]
    assert first_id != second_id
    client.patch(
        f"/api/v1/materials/{first_id}",
        json={"publisher": "修正展示名称", "source_url": "https://example.com/new"},
    )
    assert client.get(f"/api/v1/materials/{first_id}/versions/1/file").content == BODY
    version = client.get(f"/api/v1/materials/{first_id}/versions").json()[0]
    assert version["source_metadata"]["publisher"] == "旧发布方"
    assert version["source_metadata"]["requested_url"] == URL


def test_network_failure_leaves_no_partial_record_or_file(web_client):
    client, fetcher, root = web_client
    fetcher.fetch.side_effect = WebFetchError("access_denied", "网页需要登录，请粘贴原文")
    response = client.post("/api/v1/materials/web", json={"url": URL, "title": "通知"})
    assert response.status_code == 400 and "需要登录" in response.json()["detail"]
    assert client.get("/api/v1/materials").json() == []
    assert not [p for p in root.rglob("*") if p.is_file()]


def test_unauthenticated_request_never_fetches(web_client):
    client, fetcher, _ = web_client
    anonymous = TestClient(client.app)
    response = anonymous.post("/api/v1/materials/web", json={"url": URL, "title": "通知"})
    assert response.status_code == 401
    fetcher.fetch.assert_not_called()
    anonymous.close()


def test_foreign_course_rejected_before_network(web_client):
    client, fetcher, _ = web_client
    with client.app.state.session_factory() as session:
        workspace = Workspace(name="another", timezone="Asia/Shanghai")
        session.add(workspace)
        session.flush()
        semester = Semester(
            workspace_id=workspace.id,
            name="term",
            start_date=date(2026, 9, 1),
            end_date=date(2027, 1, 1),
        )
        session.add(semester)
        session.flush()
        course = Course(workspace_id=workspace.id, semester_id=semester.id, name="foreign")
        session.add(course)
        session.commit()
        course_id = course.id
    response = client.post(
        "/api/v1/materials/web", json={"url": URL, "title": "通知", "course_id": course_id}
    )
    assert response.status_code == 404
    fetcher.fetch.assert_not_called()


def test_database_failure_removes_snapshot(web_client, monkeypatch):
    client, _, root = web_client

    def fail(*args, **kwargs):
        raise RuntimeError("database failure")

    monkeypatch.setattr(
        "campusflow.infrastructure.repositories.SqlAlchemyMaterialRepository.create_version", fail
    )
    with pytest.raises(RuntimeError, match="database failure"):
        imported(client)
    assert client.get("/api/v1/materials").json() == []
    assert not [p for p in root.rglob("*") if p.is_file()]


def test_queue_failure_preserves_snapshot_and_pending_dispatch(web_client, monkeypatch):
    client, _, _ = web_client
    queue = Mock(enqueue_parse=Mock(side_effect=ConnectionError("offline")))
    monkeypatch.setattr(client.app.state, "job_queue", queue)
    result = imported(client)
    mid = result["material"]["id"]
    assert client.get(f"/api/v1/materials/{mid}/versions/1/file").content == BODY
    assert result["job_id"]
