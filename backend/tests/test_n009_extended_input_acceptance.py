"""N009：Word、网页与失败样本的定位和回答依据验收。"""

import json
from datetime import date
from pathlib import Path
from unittest.mock import Mock

from index_helpers import FakeEmbedder, open_session, run_index, workspace_id

from campusflow.api.deps import get_embedder, get_text_generator
from campusflow.api.materials import get_web_fetcher
from campusflow.application import processing
from campusflow.application.ports.web_fetch import FetchedWebPage
from campusflow.infrastructure.db.models import Course, Semester
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.storage.local import LocalFileStorage
from campusflow.workers.main import build_parser_registry

SAMPLES = Path(__file__).parents[2] / "samples/synthetic/n001"
URL = "https://example.com/notice"


class ScriptedGenerator:
    def __init__(self, answer_builder):
        self.answer_builder = answer_builder

    def generate(self, request):
        candidates = json.loads(request.source_json)["candidates"]
        return json.dumps(self.answer_builder(candidates), ensure_ascii=False)


def citation_for(candidates, needle):
    return next(item["citation_id"] for item in candidates if needle in item["text"])


def prepare(client, tmp_path, monkeypatch):
    monkeypatch.setattr(client.app.state, "file_storage", LocalFileStorage(tmp_path))
    monkeypatch.setattr(
        client.app.state, "job_queue", Mock(enqueue_parse=Mock(return_value="n009-job"))
    )
    monkeypatch.setattr(
        processing, "_PARSER_REGISTRY", build_parser_registry(client.app.state.settings, demo=True)
    )
    workspace = workspace_id(client)
    with open_session() as session, session.begin():
        semester = Semester(
            workspace_id=workspace,
            name="秋季",
            start_date=date(2026, 9, 1),
            end_date=date(2027, 1, 31),
        )
        session.add(semester)
        session.flush()
        course = Course(workspace_id=workspace, semester_id=semester.id, name="数据库原理")
        session.add(course)
        session.flush()
        return semester.id, course.id


def parse(client, job_id):
    with client.app.state.session_factory() as session:
        processing.parse_version(build_repositories(session), client.app.state.file_storage, job_id)


def index(client, material_id: int, version_id: int) -> None:
    jobs = client.get(f"/api/v1/jobs?material_id={material_id}").json()
    embed = next(job for job in jobs if job["stage"] == "embed" and job["version_id"] == version_id)
    run_index(embed["id"], FakeEmbedder())


def ask(client, semester_id, course_id, builder):
    client.app.dependency_overrides[get_embedder] = lambda: FakeEmbedder()
    client.app.dependency_overrides[get_text_generator] = lambda: ScriptedGenerator(builder)
    try:
        return client.post(
            "/api/v1/qa/query",
            json={
                "semester_id": semester_id,
                "course_id": course_id,
                "class_name": "CS2026-A" if course_id else None,
                "question": "作业或考试什么时候？",
            },
        )
    finally:
        client.app.dependency_overrides.pop(get_embedder, None)
        client.app.dependency_overrides.pop(get_text_generator, None)


def test_word_locator_and_answer_evidence_do_not_invent_page_or_publication(
    client, tmp_path, monkeypatch
):
    semester_id, course_id = prepare(client, tmp_path, monkeypatch)
    content = (SAMPLES / "word-notice-paragraph.docx").read_bytes()
    response = client.post(
        "/api/v1/materials/upload",
        params={
            "semester_id": semester_id,
            "course_id": course_id,
            "class_name": "CS2026-A",
        },
        files=[("files", ("word-notice-paragraph.docx", content))],
    )
    assert response.status_code == 201, response.text
    item = response.json()["results"][0]
    parse(client, item["job_id"])
    mid = item["material"]["id"]
    material = client.get(f"/api/v1/materials/{mid}").json()
    assert material["published_at"] is None
    chunk = next(
        c
        for c in client.get(f"/api/v1/materials/{mid}/versions/1/chunks").json()
        if "截止时间" in c["text"]
    )
    assert chunk["page"] is None and chunk["paragraph"] == 7
    assert "2026-11-15 18:00" in chunk["text"]
    assert client.get(f"/api/v1/materials/{mid}/versions/1/file").content == content

    version_id = client.get(f"/api/v1/materials/{mid}/versions").json()[0]["id"]
    index(client, mid, version_id)
    answer = ask(
        client,
        semester_id,
        course_id,
        lambda candidates: {
            "insufficient": False,
            "answer": "截止时间为 2026-11-15 18:00。",
            "citation_ids": [citation_for(candidates, "2026-11-15")],
            "uncertainties": [],
        },
    )
    assert answer.status_code == 200, answer.text
    body = answer.json()
    citation = body["citations"][0]
    assert citation["valid"] is True
    assert citation["page"] is None and citation["paragraph"] == 7
    assert "2026-11-15 18:00" in citation["excerpt"]
    assert not [item for item in body["uncertainties"] if "未出现在被引原文" in item]


def test_saved_web_snapshot_remains_verifiable_after_site_changes(client, tmp_path, monkeypatch):
    semester_id, course_id = prepare(client, tmp_path, monkeypatch)
    body = (SAMPLES / "public-notice.html").read_bytes()
    fetcher = Mock()
    fetcher.fetch.return_value = FetchedWebPage(URL, URL, "text/html; charset=utf-8", body)
    client.app.dependency_overrides[get_web_fetcher] = lambda: fetcher
    try:
        first = client.post(
            "/api/v1/materials/web",
            json={
                "url": URL,
                "title": "考试安排",
                "semester_id": semester_id,
                "course_id": course_id,
                "class_name": "CS2026-A",
            },
        )
        assert first.status_code == 201, first.text
        first_data = first.json()
        parse(client, first_data["job_id"])
        first_id = first_data["material"]["id"]
        material = client.get(f"/api/v1/materials/{first_id}").json()
        assert material["published_at"] is None
        chunks = client.get(f"/api/v1/materials/{first_id}/versions/1/chunks").json()
        exam = next(c for c in chunks if "12 月 19 日至 12 月 27 日" in c["text"])
        assert exam["locator_type"] == "web" and exam["locator_value"] == "p:3"
        assert exam["page"] is None
        index(client, first_id, first_data["version"]["id"])
        first_answer = ask(
            client,
            semester_id,
            course_id,
            lambda candidates: {
                "insufficient": False,
                "answer": "考试时间为 12 月 19 日至 12 月 27 日。",
                "citation_ids": [citation_for(candidates, "12 月 19 日")],
                "uncertainties": [],
            },
        )
        assert first_answer.status_code == 200, first_answer.text
        first_body = first_answer.json()
        assert first_body["citations"][0]["valid"] is True
        assert first_body["citations"][0]["page"] is None
        assert "12 月 19 日至 12 月 27 日" in first_body["citations"][0]["excerpt"]

        fetcher.fetch.return_value = FetchedWebPage(
            URL, URL, "text/html; charset=utf-8", "<main><p>考试改期</p></main>".encode()
        )
        second = client.post(
            "/api/v1/materials/web",
            json={"url": URL, "title": "考试安排（新）"},
        )
        assert second.status_code == 201, second.text
        parse(client, second.json()["job_id"])

        assert client.get(f"/api/v1/materials/{first_id}/versions/1/chunks").json() == chunks
        assert client.get(f"/api/v1/materials/{first_id}/versions/1/file").content == body
        again = ask(
            client,
            semester_id,
            course_id,
            lambda candidates: {
                "insufficient": False,
                "answer": "考试时间为 12 月 19 日至 12 月 27 日。",
                "citation_ids": [citation_for(candidates, "12 月 19 日")],
                "uncertainties": [],
            },
        )
        assert again.status_code == 200, again.text
        assert again.json()["citations"][0]["valid"] is True
    finally:
        client.app.dependency_overrides.pop(get_web_fetcher, None)


def test_login_page_failure_does_not_create_basis_or_publication_time(
    client, tmp_path, monkeypatch
):
    prepare(client, tmp_path, monkeypatch)
    fetcher = Mock()
    fetcher.fetch.return_value = FetchedWebPage(
        URL, URL, "text/html", b"<form><input type='password'></form>"
    )
    client.app.dependency_overrides[get_web_fetcher] = lambda: fetcher
    try:
        response = client.post("/api/v1/materials/web", json={"url": URL, "title": "登录页"})
        assert response.status_code == 201, response.text
        data = response.json()
        parse(client, data["job_id"])
        mid = data["material"]["id"]
        material = client.get(f"/api/v1/materials/{mid}").json()
        assert material["status"] == "failed"
        assert material["published_at"] is None
        assert client.get(f"/api/v1/materials/{mid}/versions/1/chunks").json() == []
    finally:
        client.app.dependency_overrides.pop(get_web_fetcher, None)
