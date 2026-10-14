"""X004：活动/截止要求区分、缺失起止、原文证据与候选保存。"""

import json

import pytest
from sqlalchemy import func, select

from campusflow.application.candidate_generation import InvalidModelOutput
from campusflow.application.candidates import save_candidates
from campusflow.application.event_extraction import extract_event_drafts
from campusflow.domain.errors import DomainError, NotFoundError
from campusflow.domain.event_extraction import event_time_reasons, require_event_statement
from campusflow.domain.states import ReviewStatus
from campusflow.infrastructure.db.models import ExtractedItem, FixedEvent
from campusflow.infrastructure.llm.text import FakeTextGenerator
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.storage.local import LocalFileStorage

STATEMENT = "学术讲座将于2026年10月20日14:00开始，16:00结束。"


@pytest.fixture
def source(client, tmp_path, monkeypatch):
    monkeypatch.setattr(client.app.state, "file_storage", LocalFileStorage(tmp_path))

    def create(content=STATEMENT + "\n\n地点：东区报告厅。\n\n仅限已修基础课的同学参加。"):
        response = client.post(
            "/api/v1/materials/text", json={"title": "活动通知", "content": content}
        )
        assert response.status_code == 201
        data = response.json()
        data["workspace_id"] = client.get("/api/v1/settings").json()["workspace_id"]
        return data

    return create


def response(data, **overrides):
    values = {
        "title": "学术讲座",
        "activity_text": STATEMENT,
        "start_text": "2026年10月20日14:00",
        "end_text": "16:00",
        "location": "东区报告厅",
        "participation_conditions": "仅限已修基础课的同学参加",
    }
    values.update(overrides)
    fields = []
    for name, value in values.items():
        field = {"name": name, "value": value, "origin": "document"}
        if value is None:
            field["missing_reason"] = "原文未明确，待核对"
        else:
            chunk = next(c for c in data["chunks"] if value in c["text"])
            field["evidence"] = [{"chunk_id": chunk["id"], "quote": value}]
        fields.append(field)
    return {"items": [{"kind": "event", "fields": fields}]}


def generate(client, data, raw):
    fake = FakeTextGenerator(json.dumps(raw, ensure_ascii=False))
    with client.app.state.session_factory() as session:
        drafts = extract_event_drafts(
            build_repositories(session), data["workspace_id"], data["material"]["id"], 1, fake
        )
        assert session.scalar(select(func.count()).select_from(ExtractedItem)) == 0
        assert session.scalar(select(func.count()).select_from(FixedEvent)) == 0
    return drafts


def test_fixed_activity_preserves_conditions_and_saves_only_pending_candidates(client, source):
    data = source()
    drafts = generate(client, data, response(data))
    fields = {f.name: f for f in drafts[0].fields}
    assert fields["start_text"].value == "2026年10月20日14:00"
    assert fields["end_text"].value == "16:00"
    assert fields["participation_conditions"].value == "仅限已修基础课的同学参加"
    assert any(
        r.code == "event_date_context" and r.field == "end_text" for r in drafts[0].review_reasons
    )
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        saved = save_candidates(repos, data["workspace_id"], data["material"]["id"], 1, drafts)
        loaded = repos.candidates.get(data["workspace_id"], saved[0].id)
        assert loaded.review_status == ReviewStatus.PENDING
        assert loaded.draft == drafts[0]
        assert session.scalar(select(func.count()).select_from(FixedEvent)) == 0


@pytest.mark.parametrize(
    "field", ["start_text", "end_text", "location", "participation_conditions"]
)
def test_missing_fields_are_visible_even_when_model_omits_review_reasons(client, source, field):
    data = source()
    drafts = generate(client, data, response(data, **{field: None}))
    assert any(r.code == f"missing_{field}" and r.field == field for r in drafts[0].review_reasons)
    assert next(f.value for f in drafts[0].fields if f.name == field) is None


def test_missing_both_endpoints_does_not_guess_event_length(client, source):
    text = "学术讲座时间待定。"
    data = source(text)
    raw = response(
        data,
        activity_text=text,
        start_text=None,
        end_text=None,
        location=None,
        participation_conditions=None,
    )
    raw["items"][0]["review_reasons"] = [
        {"code": "missing_end_text", "field": "end_text", "message": "默认两小时即可"}
    ]
    draft = generate(client, data, raw)[0]
    assert {"missing_start_text", "missing_end_text"} <= {r.code for r in draft.review_reasons}
    assert all("默认" not in r.message for r in draft.review_reasons)


@pytest.mark.parametrize(
    "text",
    [
        "作业请在周五前提交。",
        "讲座报告请在周五前完成。",
        "考试报名截至10月15日。",
        "答辩材料10月15日前交。",
        "学术讲座14:00开始，作业16:00前提交。",
    ],
)
def test_misclassified_deadline_or_mixed_statement_is_rejected(client, source, text):
    data = source(text)
    raw = response(
        data,
        title=text,
        activity_text=text,
        start_text=None,
        end_text=None,
        location=None,
        participation_conditions=None,
    )
    with pytest.raises(InvalidModelOutput, match="截止|提交|完成|报名"):
        generate(client, data, raw)


def test_shortened_quote_cannot_hide_deadline_context(client, source):
    data = source("学术讲座报告周五前提交。")
    raw = response(
        data,
        activity_text="学术讲座",
        start_text=None,
        end_text=None,
        location=None,
        participation_conditions=None,
    )
    with pytest.raises(InvalidModelOutput, match="完整语句"):
        generate(client, data, raw)


@pytest.mark.parametrize(
    "content", ["作业周五前提交。" + STATEMENT, STATEMENT + "作业周五前提交。"]
)
def test_deadline_in_separate_sentence_does_not_hide_actual_activity(client, source, content):
    data = source(content)
    raw = response(data, location=None, participation_conditions=None)
    assert len(generate(client, data, raw)) == 1


def test_multiple_activities_remain_separate_with_distinct_times(client, source):
    second = "课程答辩将于2026年10月21日09:00开始，11:00结束。"
    data = source(STATEMENT + "\n\n" + second)
    raw = response(data, location=None, participation_conditions=None)
    raw["items"].extend(
        response(
            data,
            title="课程答辩",
            activity_text=second,
            start_text="2026年10月21日09:00",
            end_text="11:00",
            location=None,
            participation_conditions=None,
        )["items"]
    )
    drafts = generate(client, data, raw)
    assert len(drafts) == 2
    fields = [{f.name: f for f in draft.fields} for draft in drafts]
    assert fields[0]["end_text"].value == "16:00"
    assert fields[1]["end_text"].value == "11:00"
    assert fields[0]["activity_text"].evidence != fields[1]["activity_text"].evidence


def test_archived_or_missing_version_never_calls_model(client, source):
    data = source()
    fake = FakeTextGenerator(json.dumps(response(data)))
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        with pytest.raises(NotFoundError):
            extract_event_drafts(repos, data["workspace_id"], data["material"]["id"], 99, fake)
        repos.materials.set_archived(data["material"]["id"], True)
        repos.uow.commit()
        with pytest.raises(DomainError, match="归档"):
            extract_event_drafts(repos, data["workspace_id"], data["material"]["id"], 1, fake)
    assert fake.requests == []


def test_other_sentence_deadline_cannot_be_used_as_event_end(client, source):
    data = source(STATEMENT + "\n\n作业18:00前提交。")
    raw = response(data, end_text="18:00", location=None, participation_conditions=None)
    with pytest.raises(InvalidModelOutput, match="当前活动"):
        generate(client, data, raw)


@pytest.mark.parametrize(
    "case", ["invented-time", "task-kind", "extra-ddl", "wrong-title", "unknown-id"]
)
def test_invalid_event_output_is_rejected(client, source, case):
    data = source()
    raw = response(data)
    item = raw["items"][0]
    fields = {f["name"]: f for f in item["fields"]}
    if case == "invented-time":
        fields["end_text"]["value"] = "18:00"
    elif case == "task-kind":
        item["kind"] = "task"
    elif case == "extra-ddl":
        item["due_date"] = "2026-10-20"
    elif case == "wrong-title":
        fields["title"]["value"] = "东区报告厅"
        fields["title"]["evidence"] = fields["location"]["evidence"]
    else:
        fields["start_text"]["evidence"][0]["chunk_id"] = 999999
    with pytest.raises(InvalidModelOutput):
        generate(client, data, raw)


def test_empty_result_and_invalid_second_event_do_not_write_anything(client, source):
    data = source()
    assert generate(client, data, {"items": []}) == []
    raw = response(data)
    bad = response(data)["items"][0]
    bad["fields"][0]["value"] = "不存在的活动"
    raw["items"].append(bad)
    with pytest.raises(InvalidModelOutput):
        generate(client, data, raw)
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ExtractedItem)) == 0


def test_foreign_workspace_is_rejected_before_model_call(client, source):
    data = source()
    fake = FakeTextGenerator(json.dumps(response(data)))
    with client.app.state.session_factory() as session:
        with pytest.raises(NotFoundError):
            extract_event_drafts(
                build_repositories(session),
                data["workspace_id"] + 100,
                data["material"]["id"],
                1,
                fake,
            )
    assert fake.requests == []


@pytest.mark.parametrize("text", ["周五交报告", "普通通知", "提交考试报告", "比赛报名截止"])
def test_domain_guard_refuses_non_event_descriptions(text):
    with pytest.raises(DomainError):
        require_event_statement(text)


@pytest.mark.parametrize("text", ["下午", "25:00", "14:99", "14:00-16:00"])
def test_ambiguous_or_invalid_endpoint_stays_reviewable(text):
    reasons = event_time_reasons(text, "start_text")
    assert any(r.code == "uncertain_event_time" for r in reasons)
    assert all(r.field == "start_text" for r in reasons)
