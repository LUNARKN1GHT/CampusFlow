"""X003 首段：实际应用入口、原文保持、建议标记与失败不写入。"""

import json

import pytest
from sqlalchemy import func, select

from campusflow.application.candidate_generation import InvalidModelOutput
from campusflow.application.task_extraction import extract_task_drafts
from campusflow.domain.candidates import FieldOrigin
from campusflow.domain.errors import NotFoundError
from campusflow.infrastructure.db.models import ExtractedItem
from campusflow.infrastructure.llm.text import FakeTextGenerator
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.storage.local import LocalFileStorage

CONTENT = "实验报告：下周五提交，提交到课程平台，产出物为 PDF 报告。"


@pytest.fixture
def source(client, tmp_path, monkeypatch):
    monkeypatch.setattr(client.app.state, "file_storage", LocalFileStorage(tmp_path))
    response = client.post("/api/v1/materials/text", json={"title": "实验通知", "content": CONTENT})
    assert response.status_code == 201
    data = response.json()
    data["workspace_id"] = client.get("/api/v1/settings").json()["workspace_id"]
    return data


def output(source):
    values = {
        "title": "实验报告",
        "submission_method": "提交到课程平台",
        "deliverable": "PDF 报告",
        "due_text": "下周五提交",
    }
    fields = [
        {
            "name": name,
            "value": value,
            "origin": "document",
            "evidence": [{"chunk_id": source["chunks"][0]["id"], "quote": value}],
        }
        for name, value in values.items()
    ]
    fields.append(
        {
            "name": "estimated_minutes",
            "value": 60,
            "origin": "suggestion",
            "explanation": "按阅读与写作估计，可修改",
        }
    )
    return {"items": [{"kind": "task", "fields": fields}]}


def extract(client, source, raw, *, foreign=False):
    generator = FakeTextGenerator(json.dumps(raw, ensure_ascii=False))
    with client.app.state.session_factory() as session:
        drafts = extract_task_drafts(
            build_repositories(session),
            source["workspace_id"] + (100 if foreign else 0),
            source["material"]["id"],
            1,
            generator,
        )
        assert session.scalar(select(func.count()).select_from(ExtractedItem)) == 0
    return drafts, generator


def test_task_fields_preserve_original_deadline_and_suggestion(client, source):
    drafts, generator = extract(client, source, output(source))
    fields = {field.name: field for field in drafts[0].fields}
    assert fields["due_text"].value == "下周五提交"
    assert fields["submission_method"].value == "提交到课程平台"
    assert fields["deliverable"].value == "PDF 报告"
    assert fields["estimated_minutes"].origin == FieldOrigin.SUGGESTION
    assert any(r.code == "estimated_duration" for r in drafts[0].review_reasons)
    assert len(generator.requests) == 1
    assert client.get("/api/v1/tasks").json() == []


@pytest.mark.parametrize(
    "name",
    [
        "submission_method",
        "deliverable",
        "due_text",
        "estimated_minutes",
    ],
)
def test_missing_fields_are_retained_with_visible_review_reasons(client, source, name):
    raw = output(source)
    field = next(f for f in raw["items"][0]["fields"] if f["name"] == name)
    field.update(value=None, missing_reason="资料未明确说明")
    field.pop("evidence", None)
    drafts, _ = extract(client, source, raw)
    assert next(f for f in drafts[0].fields if f.name == name).value is None
    assert any(r.code == f"missing_{name}" and r.field == name for r in drafts[0].review_reasons)


@pytest.mark.parametrize(
    "case",
    [
        "invented-date",
        "invented-method",
        "zero-estimate",
        "negative-estimate",
        "factual-estimate",
    ],
)
def test_task_specific_invalid_results_are_rejected(client, source, case):
    raw = output(source)
    fields = {f["name"]: f for f in raw["items"][0]["fields"]}
    if case == "invented-date":
        fields["due_text"]["value"] = "2026-10-16 23:59"
    elif case == "invented-method":
        fields["submission_method"]["value"] = "发送到教师邮箱"
    elif case == "factual-estimate":
        fields["estimated_minutes"]["origin"] = "document"
    else:
        fields["estimated_minutes"]["value"] = 0 if case == "zero-estimate" else -10
    with pytest.raises(InvalidModelOutput):
        extract(client, source, raw)


def test_empty_result_and_batch_failure_leave_no_candidates(client, source):
    assert extract(client, source, {"items": []})[0] == []
    raw = output(source)
    second = output(source)["items"][0]
    second["fields"][3]["value"] = "伪造日期"
    raw["items"].append(second)
    with pytest.raises(InvalidModelOutput):
        extract(client, source, raw)
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ExtractedItem)) == 0


def test_foreign_material_is_rejected_before_generation(client, source):
    generator = FakeTextGenerator(json.dumps(output(source)))
    with client.app.state.session_factory() as session:
        with pytest.raises(NotFoundError):
            extract_task_drafts(
                build_repositories(session),
                source["workspace_id"] + 100,
                source["material"]["id"],
                1,
                generator,
            )
    assert generator.requests == []
