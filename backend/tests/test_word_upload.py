"""N002：Word 校验、上传和失败无孤立文件。"""

import io
import zipfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from campusflow.domain.upload import validate_upload
from campusflow.domain.word import DOCX_MIME
from campusflow.infrastructure.storage.local import LocalFileStorage

SAMPLE = Path(__file__).parents[2] / "samples/synthetic/n001/word-notice-paragraph.docx"


def changed_package(changes):
    output = io.BytesIO()
    with zipfile.ZipFile(SAMPLE) as original, zipfile.ZipFile(output, "w") as result:
        for item in original.infolist():
            if item.filename not in changes:
                result.writestr(item.filename, original.read(item))
        for name, content in changes.items():
            if content is not None:
                result.writestr(name, content)
    return output.getvalue()


def test_valid_sample_is_recognized_as_word():
    validation, error = validate_upload("通知.DOCX", SAMPLE.read_bytes(), 1_000_000)
    assert error is None
    assert validation.mime_type == DOCX_MIME


@pytest.mark.parametrize("extension", ["doc", "docm", "dotx"])
def test_unsupported_word_format_has_conversion_guidance(extension):
    validation, error = validate_upload(f"通知.{extension}", b"document", 100)
    assert validation is None
    assert "另存" in error and ".docx" in error


@pytest.mark.parametrize(
    "content",
    [
        b"text renamed docx",
        b"PK\x03\x04bad",
        b"",
        changed_package({"word/document.xml": None}),
        changed_package({"word/document.xml": b"<not-word/>"}),
        changed_package({"word/document.xml": b"<!DOCTYPE foo><foo/>"}),
        changed_package({"word/vbaProject.bin": b"macro"}),
        changed_package({"../escape.xml": b"<x/>"}),
    ],
    ids=[
        "renamed",
        "broken-zip",
        "empty",
        "missing-part",
        "wrong-root",
        "doctype",
        "macro",
        "path",
    ],
)
def test_invalid_word_is_not_accepted(content):
    validation, error = validate_upload("通知.docx", content, 1_000_000)
    assert validation is None and error


def test_expansion_limit(monkeypatch):
    monkeypatch.setattr("campusflow.domain.word.MAX_UNPACKED_BYTES", 100)
    assert "大小上限" in validate_upload("通知.docx", SAMPLE.read_bytes(), 1_000_000)[1]


def test_word_size_limit():
    assert "大小上限" in validate_upload("通知.docx", SAMPLE.read_bytes(), 10)[1]


@pytest.fixture
def isolated_upload(client, tmp_path):
    storage, queue = client.app.state.file_storage, client.app.state.job_queue
    client.app.state.file_storage = LocalFileStorage(tmp_path)
    client.app.state.job_queue = Mock(enqueue_parse=Mock(return_value="test-job"))
    yield client, tmp_path
    client.app.state.file_storage, client.app.state.job_queue = storage, queue


def test_upload_creates_word_record_and_rejects_invalid_without_files(isolated_upload):
    client, root = isolated_upload
    response = client.post(
        "/api/v1/materials/upload",
        files=[
            ("files", ("bad.docx", b"fake")),
            ("files", ("good.docx", SAMPLE.read_bytes())),
        ],
    )
    assert response.status_code == 201
    bad, good = response.json()["results"]
    assert bad["error"] and bad["material"] is None and bad["job_id"] is None
    assert good["material"]["source_type"] == "word" and good["job_id"]
    assert len(client.get("/api/v1/materials").json()) == 1
    assert len([p for p in root.rglob("*") if p.is_file()]) == 1


def test_failed_transaction_cleans_word_file(isolated_upload, monkeypatch):
    client, root = isolated_upload

    def fail(*args, **kwargs):
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(
        "campusflow.infrastructure.repositories.SqlAlchemyMaterialRepository.create", fail
    )
    with pytest.raises(RuntimeError, match="simulated database failure"):
        client.post(
            "/api/v1/materials/upload", files=[("files", ("good.docx", SAMPLE.read_bytes()))]
        )
    assert not [p for p in root.rglob("*") if p.is_file()]
    assert client.get("/api/v1/materials").json() == []
