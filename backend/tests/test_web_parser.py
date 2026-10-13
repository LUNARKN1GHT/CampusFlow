"""N006：合成网页的正文、原始位置和真实资料作业链路。"""

from pathlib import Path
from unittest.mock import Mock

import pytest
from bs4 import BeautifulSoup

from campusflow.api.materials import get_web_fetcher
from campusflow.application import processing
from campusflow.application.materials import fragments_to_chunks
from campusflow.application.ports.web_fetch import FetchedWebPage
from campusflow.domain.errors import PermanentError
from campusflow.infrastructure.parsers.web import WebDocumentParser
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.storage.local import LocalFileStorage
from campusflow.workers.main import build_parser_registry

BODY = (Path(__file__).parents[2] / "samples/synthetic/n001/public-notice.html").read_bytes()
URL = "https://example.com/notice"


def parse(text):
    return WebDocumentParser().parse(text.encode("utf-8"), "text/html; charset=utf-8")


def test_golden_evidence_retains_main_paragraph_and_publisher():
    result = WebDocumentParser().parse(BODY, "text/html")
    assert not result.failures
    exam = next(f for f in result.fragments if "12 月 19 日至 12 月 27 日" in f.text)
    assert exam.locator.as_chunk_locator() == ("web", "p:3")
    metadata = next(f for f in result.fragments if "发布单位" in f.text)
    assert "教务处" in metadata.text and "2026-11-20" in metadata.text
    assert metadata.locator.anchor == "p:1"
    raw = BeautifulSoup(BODY.decode("utf-8"), "html.parser")
    for fragment in result.fragments:
        tag, number = fragment.locator.anchor.split(":")
        original = raw.find_all(tag)[int(number) - 1]
        assert original.get_text(" ", strip=True) == fragment.text
    assert result == WebDocumentParser().parse(BODY, "text/html")
    assert {c.version_id for c in fragments_to_chunks(result, 81)} == {81}


def test_noise_removed_without_renumbering_source_or_executing_scripts():
    result = parse("""<nav><p>菜单</p></nav><main><h1>考试</h1>
        <div class='ads'><p>广告</p></div><p>正文<strong>截止日期</strong></p>
        <p hidden>隐藏</p><p aria-hidden='true'>隐藏2</p><script>danger()</script>
        <aside>相关推荐</aside><!-- 注释 --></main><footer>页脚</footer>""")
    assert [f.text for f in result.fragments] == ["考试", "正文 截止日期"]
    assert result.fragments[1].locator.anchor == "p:3"


def test_table_cells_and_nested_tables_are_not_duplicated():
    result = parse("""<article><p>安排</p><table><tr><th>课程</th><th>时间</th></tr>
      <tr><td><p>操作系统</p></td><td>周五<table><tr><td>备注</td></tr></table></td></tr>
      </table><ul><li>带证件</li><li>准时</li></ul></article>""")
    assert [f.text for f in result.fragments] == [
        "安排",
        "课程",
        "时间",
        "操作系统",
        "周五",
        "备注",
        "带证件",
        "准时",
    ]
    cells = [f for f in result.fragments if f.table_group]
    assert [f.table_group for f in cells] == ["web-table-1"] * 4 + ["web-table-2"]
    assert len({f.locator.anchor for f in cells}) == len(cells)


def test_nonstandard_markup_keeps_plain_container_text():
    result = parse("<div>通知：<span>周五提交</span><div>附件另交</div></div>")
    assert [f.text for f in result.fragments] == ["通知： 周五提交", "附件另交"]
    assert not result.failures


@pytest.mark.parametrize(
    "html",
    [
        "<form><input type='password'><button>登录</button></form>",
        "<form><button>Sign in with school account</button></form>",
    ],
    ids=["password", "single-sign-on"],
)
def test_login_page_is_not_notice_text(html):
    result = parse(html)
    assert not result.fragments and "登录" in result.failures[0].reason


@pytest.mark.parametrize(
    "html",
    ["", "<script>loadContent()</script>", "<nav>菜单</nav>"],
    ids=["empty", "script-only", "navigation-only"],
)
def test_empty_or_dynamic_page_reports_no_body(html):
    result = parse(html)
    assert not result.fragments and result.failures


def test_media_content_reports_partial_or_failed_without_loading_resources():
    result = parse(
        "<main><p>安排</p><img src='http://127.0.0.1/secret'><iframe src='x'>旧文字</iframe></main>"
    )
    assert result.is_partial and [f.text for f in result.fragments] == ["安排"]
    assert [f.locator.anchor for f in result.failures] == ["img:1", "iframe:1"]
    image_only = parse("<main><img src='x'></main>")
    assert not image_only.fragments and image_only.failures


@pytest.mark.parametrize(
    "mime,html",
    [
        ("text/html; charset=gb18030", "<p>中文考试通知</p>"),
        ("text/html", "<meta charset='gb18030'><p>中文考试通知</p>"),
        (
            "text/html",
            '<meta http-equiv="Content-Type" content="text/html; charset=gb18030">'
            "<p>中文考试通知</p>",
        ),
    ],
    ids=["http-header", "meta-charset", "meta-http-equiv"],
)
def test_declared_chinese_encoding_is_respected(mime, html):
    result = WebDocumentParser().parse(html.encode("gb18030"), mime)
    assert [f.text for f in result.fragments] == ["中文考试通知"]


@pytest.mark.parametrize(
    "body,mime",
    [
        (b"\xff", "text/html"),
        (b"text", "text/html; charset=unknown-charset"),
        (b"x", "application/pdf"),
        (b"x" * (2 * 1024 * 1024 + 1), "text/html"),
    ],
    ids=["invalid-utf8", "unknown-charset", "unsupported-type", "oversized"],
)
def test_unreliable_or_oversized_source_fails_explicitly(body, mime):
    with pytest.raises(PermanentError):
        WebDocumentParser().parse(body, mime)


def test_plain_text_snapshot_has_stable_paragraphs():
    result = WebDocumentParser().parse(
        "考试通知\n\n周五交作业".encode(), "text/plain; charset=utf-8"
    )
    assert [f.text for f in result.fragments] == ["考试通知", "周五交作业"]
    assert [f.locator.as_chunk_locator() for f in result.fragments] == [
        ("paragraph", "1"),
        ("paragraph", "2"),
    ]
    assert WebDocumentParser().parse(b"  ", "text/plain").failures


def test_header_alone_does_not_claim_dynamic_body_was_read():
    result = parse("<header><h1>考试通知</h1></header><main><script>load()</script></main>")
    assert result.is_partial and "没有可读取正文" in result.failures[0].reason


def test_vector_image_is_reported_as_uncovered():
    result = parse("<main><svg><text>图中的通知</text></svg></main>")
    assert not result.fragments and result.failures[0].locator.anchor == "svg:1"


@pytest.fixture
def imported_web(client, tmp_path, monkeypatch):
    fetcher = Mock()
    fetcher.fetch.return_value = FetchedWebPage(URL, URL, "text/html; charset=utf-8", BODY)
    monkeypatch.setattr(client.app.state, "file_storage", LocalFileStorage(tmp_path))
    monkeypatch.setattr(
        client.app.state, "job_queue", Mock(enqueue_parse=Mock(return_value="web-job"))
    )
    monkeypatch.setattr(
        processing, "_PARSER_REGISTRY", build_parser_registry(client.app.state.settings, demo=True)
    )
    client.app.dependency_overrides[get_web_fetcher] = lambda: fetcher
    yield client, fetcher
    client.app.dependency_overrides.pop(get_web_fetcher, None)


def run_import(client):
    response = client.post("/api/v1/materials/web", json={"url": URL, "title": "考试安排"})
    assert response.status_code == 201, response.text
    data = response.json()
    with client.app.state.session_factory() as session:
        processing.parse_version(
            build_repositories(session), client.app.state.file_storage, data["job_id"]
        )
    mid = data["material"]["id"]
    return mid, client.get(f"/api/v1/materials/{mid}/versions/1/chunks").json()


def test_saved_snapshot_to_worker_chunks_preserves_old_version(imported_web):
    client, fetcher = imported_web
    first_id, first_chunks = run_import(client)
    assert client.get(f"/api/v1/materials/{first_id}").json()["status"] == "done"
    exam = next(c for c in first_chunks if "12 月 19 日至 12 月 27 日" in c["text"])
    assert exam["locator_type"] == "web" and exam["locator_value"] == "p:3"
    assert exam["page"] is None
    newer = "<main><p>考试改期</p></main>".encode()
    fetcher.fetch.return_value = FetchedWebPage(URL, URL, "text/html; charset=utf-8", newer)
    second_id, second_chunks = run_import(client)
    assert first_id != second_id and second_chunks[0]["version_id"] != exam["version_id"]
    assert client.get(f"/api/v1/materials/{first_id}/versions/1/chunks").json() == first_chunks
    assert client.get(f"/api/v1/materials/{first_id}/versions/1/file").content == BODY
    assert client.get(f"/api/v1/materials/{second_id}/versions/1/file").content == newer


def test_http_success_login_page_processing_fails(imported_web):
    client, fetcher = imported_web
    fetcher.fetch.return_value = FetchedWebPage(
        URL, URL, "text/html", b"<form><input type='password'></form>"
    )
    mid, chunks = run_import(client)
    assert chunks == []
    assert client.get(f"/api/v1/materials/{mid}").json()["status"] == "failed"


def test_worker_uses_saved_content_type_for_encoding(imported_web):
    client, fetcher = imported_web
    fetcher.fetch.return_value = FetchedWebPage(
        URL, URL, "text/html; charset=gb18030", "<p>中文通知</p>".encode("gb18030")
    )
    mid, chunks = run_import(client)
    assert [c["text"] for c in chunks] == ["中文通知"]
    assert client.get(f"/api/v1/materials/{mid}").json()["status"] == "done"
