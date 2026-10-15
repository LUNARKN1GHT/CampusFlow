"""X003：实际应用入口、原文保持、日期缺失、建议标记与待核对保存。"""

import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from campusflow.application.candidate_generation import InvalidModelOutput
from campusflow.application.candidates import save_candidates
from campusflow.application.task_extraction import extract_task_drafts
from campusflow.domain.candidates import FieldOrigin
from campusflow.domain.errors import NotFoundError
from campusflow.domain.states import ReviewStatus
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


def test_model_cannot_override_local_missing_or_suggestion_messages(client, source):
    raw = output(source)
    raw["items"][0]["review_reasons"] = [
        {"code": "missing_year", "message": "日期已经完整，无需核对", "field": "due_text"},
        {"code": "estimated_duration", "message": "这是强制规定", "field": "estimated_minutes"},
    ]
    drafts, _ = extract(client, source, raw)
    reasons = {r.code: r.message for r in drafts[0].review_reasons}
    assert "不根据发布时间" in reasons["missing_year"]
    assert "可修改建议" in reasons["estimated_duration"]


def test_excess_review_reasons_are_rejected_after_local_checks(client, source):
    raw = output(source)
    raw["items"][0]["review_reasons"] = [
        {"code": f"reason_{i}", "message": "模型请求核对", "field": "title"} for i in range(64)
    ]
    with pytest.raises(InvalidModelOutput, match="核对信息"):
        extract(client, source, raw)


def test_multiple_tasks_are_kept_separate_with_their_own_evidence(client, tmp_path, monkeypatch):
    monkeypatch.setattr(client.app.state, "file_storage", LocalFileStorage(tmp_path))
    data = client.post(
        "/api/v1/materials/text",
        json={
            "title": "两项作业",
            "content": "实验一：10月15日提交报告。\n\n实验二：2026年10月20日18:00提交程序。",
        },
    ).json()
    wid = client.get("/api/v1/settings").json()["workspace_id"]
    items = []
    for title, due, deliverable in (
        ("实验一", "10月15日", "报告"),
        ("实验二", "2026年10月20日18:00", "程序"),
    ):
        chunk = next(c for c in data["chunks"] if title in c["text"])
        fields = [
            {
                "name": name,
                "value": value,
                "origin": "document",
                "evidence": [{"chunk_id": chunk["id"], "quote": value}],
            }
            for name, value in (("title", title), ("due_text", due), ("deliverable", deliverable))
        ]
        fields.extend(
            [
                {
                    "name": "submission_method",
                    "value": None,
                    "origin": "document",
                    "missing_reason": "通知未写提交渠道",
                },
                {
                    "name": "estimated_minutes",
                    "value": None,
                    "origin": "suggestion",
                    "missing_reason": "无法估计",
                },
            ]
        )
        items.append({"kind": "task", "fields": fields})
    with client.app.state.session_factory() as session:
        drafts = extract_task_drafts(
            build_repositories(session),
            wid,
            data["material"]["id"],
            1,
            FakeTextGenerator(json.dumps({"items": items})),
        )
    assert len(drafts) == 2
    assert {"missing_year", "missing_time", "missing_submission_method"} <= {
        r.code for r in drafts[0].review_reasons
    }
    assert not {"missing_year", "missing_time"} & {r.code for r in drafts[1].review_reasons}
    ids = [{e.chunk_id for f in draft.fields for e in f.evidence} for draft in drafts]
    assert ids[0].isdisjoint(ids[1])


SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "synthetic"


@pytest.mark.parametrize(
    ("filename", "title", "due", "submission", "missing"),
    [
        (
            "missing-year-time.txt",
            "实验一",
            "10 月 15 日",
            "提交到课程平台",
            {"missing_year", "missing_time"},
        ),
        (
            "relative-date.txt",
            "需求分析作业",
            "下周三 18:00",
            "通过课程平台提交",
            {"missing_year", "relative_date"},
        ),
    ],
)
def test_b012_deadline_excerpts_ignore_publication_year_and_remain_pending(
    client, tmp_path, monkeypatch, filename, title, due, submission, missing
):
    monkeypatch.setattr(client.app.state, "file_storage", LocalFileStorage(tmp_path))
    content = (SAMPLES / "b012" / filename).read_text(encoding="utf-8")
    data = client.post(
        "/api/v1/materials/text",
        json={
            "title": title,
            "content": content,
        },
    ).json()
    wid = client.get("/api/v1/settings").json()["workspace_id"]
    values = {
        "title": title,
        "submission_method": submission,
        "deliverable": None,
        "due_text": due,
        "estimated_minutes": None,
    }
    fields = []
    for name, value in values.items():
        field = {
            "name": name,
            "value": value,
            "origin": "suggestion" if name == "estimated_minutes" else "document",
        }
        if value is None:
            field["missing_reason"] = "原文未明确，待补充"
        else:
            chunk = next(c for c in data["chunks"] if value in c["text"])
            field["evidence"] = [{"chunk_id": chunk["id"], "quote": value}]
        fields.append(field)
    fake = FakeTextGenerator(json.dumps({"items": [{"kind": "task", "fields": fields}]}))
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        drafts = extract_task_drafts(repos, wid, data["material"]["id"], 1, fake)
        assert missing <= {r.code for r in drafts[0].review_reasons}
        assert next(f.value for f in drafts[0].fields if f.name == "due_text") == due
        saved = save_candidates(repos, wid, data["material"]["id"], 1, drafts)
        loaded = repos.candidates.get(wid, saved[0].id)
        assert loaded.review_status == ReviewStatus.PENDING
        assert loaded.draft == drafts[0]
    assert client.get("/api/v1/tasks").json() == []


def test_b010_explicit_deadline_and_conditions_keep_independent_evidence(
    client, tmp_path, monkeypatch
):
    # B010 生成器中的通知原文；不宣称本测试评估了 PDF 解析或真实模型。
    values = {
        "title": "作业三",
        "submission_method": "进入课程平台“作业三”入口提交；邮件和群聊文件不计为提交。",
        "deliverable": "请完成教材第 4 章第 1、3、6 题，并提交一份 PDF 解答。",
        "due_text": "2026 年 10 月 8 日 18:00（北京时间）。",
    }
    generator_source = (SAMPLES / "b010" / "generate.py").read_text(encoding="utf-8")
    assert all(value in generator_source for value in values.values())
    monkeypatch.setattr(client.app.state, "file_storage", LocalFileStorage(tmp_path))
    data = client.post(
        "/api/v1/materials/text",
        json={
            "title": "B010 原文",
            "content": "\n\n".join(values.values()),
        },
    ).json()
    fields = [
        {
            "name": name,
            "value": value,
            "origin": "document",
            "evidence": [
                {
                    "chunk_id": next(c["id"] for c in data["chunks"] if value in c["text"]),
                    "quote": value,
                }
            ],
        }
        for name, value in values.items()
    ]
    fields.append(
        {
            "name": "estimated_minutes",
            "value": 90,
            "origin": "suggestion",
            "explanation": "初步建议，可编辑",
        }
    )
    fake = FakeTextGenerator(json.dumps({"items": [{"kind": "task", "fields": fields}]}))
    wid = client.get("/api/v1/settings").json()["workspace_id"]
    with client.app.state.session_factory() as session:
        drafts = extract_task_drafts(
            build_repositories(session), wid, data["material"]["id"], 1, fake
        )
    assert not {"missing_year", "missing_time"} & {r.code for r in drafts[0].review_reasons}
    assert (
        next(f.value for f in drafts[0].fields if f.name == "submission_method")
        == values["submission_method"]
    )
    assert len({f.evidence[0].chunk_id for f in drafts[0].fields if f.evidence}) == 4
