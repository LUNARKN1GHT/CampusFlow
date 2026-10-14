"""X005：完整规定、条件限制、范围生效原文及待核对持久化。"""

import json

import pytest
from sqlalchemy import func, select

from campusflow.application.candidate_generation import InvalidModelOutput
from campusflow.application.candidates import save_candidates
from campusflow.application.rule_extraction import extract_rule_drafts
from campusflow.domain.errors import DomainError, NotFoundError
from campusflow.domain.rule_extraction import require_complete_sentence, require_full_rule
from campusflow.domain.states import ReviewStatus
from campusflow.infrastructure.db.models import ExtractedItem, FixedEvent, Task
from campusflow.infrastructure.llm.text import FakeTextGenerator
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.storage.local import LocalFileStorage

RULE = "实验准入：通过安全培训后才能进入实验室；未通过者不得进入，教师陪同也不例外。"
SCOPE = "本规定仅适用于2026秋季化学实验A班。"
EFFECTIVE = "本规定自2026年10月20日起生效，学期结束时失效。"


@pytest.fixture
def source(client, tmp_path, monkeypatch):
    monkeypatch.setattr(client.app.state, "file_storage", LocalFileStorage(tmp_path))

    def create(content=None):
        if content is None:
            content = "\n\n".join((RULE, SCOPE, EFFECTIVE))
        result = client.post(
            "/api/v1/materials/text", json={"title": "课程规定", "content": content}
        )
        assert result.status_code == 201
        data = result.json()
        data["workspace_id"] = client.get("/api/v1/settings").json()["workspace_id"]
        return data

    return create


def response(data, **changes):
    values = {
        "title": "实验准入",
        "rule_text": RULE,
        "conditions_text": RULE,
        "scope_text": SCOPE,
        "effective_text": EFFECTIVE,
    }
    values.update(changes)
    fields = []
    for name, value in values.items():
        field = {"name": name, "value": value, "origin": "document"}
        if value is None:
            field["missing_reason"] = "原文未明确声明，需核对"
        else:
            chunk = next(c for c in data["chunks"] if value in c["text"])
            field["evidence"] = [{"chunk_id": chunk["id"], "quote": value}]
        fields.append(field)
    return {"items": [{"kind": "rule", "fields": fields}]}


def generate(client, data, raw):
    fake = FakeTextGenerator(json.dumps(raw, ensure_ascii=False))
    with client.app.state.session_factory() as session:
        result = extract_rule_drafts(
            build_repositories(session), data["workspace_id"], data["material"]["id"], 1, fake
        )
        assert session.scalar(select(func.count()).select_from(ExtractedItem)) == 0
    return result


def test_conditions_scope_and_effective_range_remain_verbatim_and_pending(client, source):
    data = source()
    drafts = generate(client, data, response(data))
    fields = {f.name: f for f in drafts[0].fields}
    assert fields["rule_text"].value == RULE
    assert fields["conditions_text"].value == RULE
    assert fields["scope_text"].value == SCOPE
    assert fields["effective_text"].value == EFFECTIVE
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        saved = save_candidates(repos, data["workspace_id"], data["material"]["id"], 1, drafts)
        loaded = repos.candidates.get(data["workspace_id"], saved[0].id)
        assert loaded.review_status == ReviewStatus.PENDING
        assert loaded.draft == drafts[0]
        assert session.scalar(select(func.count()).select_from(Task)) == 0
        assert session.scalar(select(func.count()).select_from(FixedEvent)) == 0


@pytest.mark.parametrize("name", ["conditions_text", "scope_text", "effective_text"])
def test_missing_information_is_unknown_not_unconditional(client, source, name):
    data = source()
    draft = generate(client, data, response(data, **{name: None}))[0]
    assert next(f.value for f in draft.fields if f.name == name) is None
    assert any(r.code == f"missing_{name}" and r.field == name for r in draft.review_reasons)


@pytest.mark.parametrize(
    ("name", "excerpt"),
    [
        ("rule_text", "进入实验室"),
        ("rule_text", "未通过者不得进入，教师陪同也不例外。"),
        ("conditions_text", "通过安全培训"),
        ("conditions_text", "未通过者不得进入"),
        ("scope_text", "2026秋季化学实验A班"),
        ("effective_text", "2026年10月20日"),
    ],
)
def test_shortened_excerpts_cannot_drop_limits_even_with_valid_citations(
    client, source, name, excerpt
):
    data = source()
    with pytest.raises(InvalidModelOutput, match="全文|完整语句"):
        generate(client, data, response(data, **{name: excerpt}))


@pytest.mark.parametrize(
    "case",
    [
        "invented-fact",
        "suggestion",
        "wrong-kind",
        "extra-boolean",
        "unknown-chunk",
        "wrong-title",
        "excess-reasons",
    ],
)
def test_untrusted_rule_output_is_rejected(client, source, case):
    data = source()
    raw = response(data)
    item = raw["items"][0]
    fields = {f["name"]: f for f in item["fields"]}
    if case == "invented-fact":
        fields["rule_text"]["value"] = "所有同学都可以进入实验室。"
    elif case == "suggestion":
        fields["rule_text"]["origin"] = "suggestion"
        fields["rule_text"]["explanation"] = "建议这样处理"
    elif case == "wrong-kind":
        item["kind"] = "event"
    elif case == "extra-boolean":
        item["unconditional"] = True
    elif case == "unknown-chunk":
        fields["scope_text"]["evidence"][0]["chunk_id"] = 999999
    elif case == "wrong-title":
        fields["title"]["value"] = SCOPE
        fields["title"]["evidence"] = fields["scope_text"]["evidence"]
    else:
        item["review_reasons"] = [{"code": f"r_{i}", "message": "待核对"} for i in range(64)]
    with pytest.raises(InvalidModelOutput):
        generate(client, data, raw)


def test_model_cannot_override_context_and_missing_flags(client, source):
    data = source()
    raw = response(data, conditions_text=None)
    raw["items"][0]["review_reasons"] = [
        {"code": "missing_conditions_text", "field": "conditions_text", "message": "无条件"},
        {"code": "rule_context_review", "field": "rule_text", "message": "只看标题即可"},
    ]
    draft = generate(client, data, raw)[0]
    messages = {r.code: r.message for r in draft.review_reasons}
    assert "需核对" in messages["missing_conditions_text"]
    assert "不能只使用标题" in messages["rule_context_review"]


def test_format_and_assessment_rules_keep_exceptions_and_weights(client, source):
    texts = [
        "格式要求：报告须为PDF，特殊需要经教师批准可改用纸质版。",
        "考核规则：平时成绩占30%，期末成绩占70%；缺考者按课程补考规定处理。",
    ]
    data = source("\n\n".join(texts))
    items = []
    for text in texts:
        items.extend(
            response(
                data,
                title=text.split("：")[0],
                rule_text=text,
                conditions_text=text,
                scope_text=None,
                effective_text=None,
            )["items"]
        )
    drafts = generate(client, data, {"items": items})
    assert len(drafts) == 2
    assert [next(f.value for f in d.fields if f.name == "rule_text") for d in drafts] == texts
    assert drafts[0].fields[1].evidence != drafts[1].fields[1].evidence


def test_empty_result_and_failed_batch_leave_no_records(client, source):
    data = source()
    assert generate(client, data, {"items": []}) == []
    raw = response(data)
    bad = response(data)["items"][0]
    bad["fields"][1]["value"] = "进入实验室"
    raw["items"].append(bad)
    with pytest.raises(InvalidModelOutput):
        generate(client, data, raw)
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ExtractedItem)) == 0


@pytest.mark.parametrize("case", ["foreign", "version", "archived"])
def test_unavailable_sources_fail_before_model_call(client, source, case):
    data = source()
    fake = FakeTextGenerator(json.dumps(response(data)))
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        if case == "archived":
            repos.materials.set_archived(data["material"]["id"], True)
            repos.uow.commit()
        with pytest.raises((NotFoundError, DomainError)):
            extract_rule_drafts(
                repos,
                data["workspace_id"] + (100 if case == "foreign" else 0),
                data["material"]["id"],
                99 if case == "version" else 1,
                fake,
            )
    assert fake.requests == []


@pytest.mark.parametrize(
    ("text", "original"),
    [
        ("通过培训可进入", "未通过培训可进入"),
        ("可进入", "仅通过培训者，可进入；其他人不得进入"),
        ("其他人不得进入", "可进入；其他人不得进入"),
        ("", ""),
    ],
)
def test_complete_sentence_guard_rejects_cut_conditions(text, original):
    with pytest.raises(DomainError):
        require_complete_sentence(text, [original])


@pytest.mark.parametrize(
    ("text", "original"),
    [
        ("完整规定。", "完整规定。另一句。"),
        ("另一句。", "完整规定。另一句。"),
        ("适用于A班", "前一行\n适用于A班\n后一行"),
        ("条件；例外。", "条件；例外。"),
    ],
)
def test_complete_sentence_guard_accepts_real_boundaries(text, original):
    require_complete_sentence(text, [original])


def test_full_rule_guard_never_drops_a_second_sentence_exception():
    with pytest.raises(DomainError):
        require_full_rule("允许进入。", ["允许进入。但未通过培训者除外。"])


@pytest.mark.parametrize("text", ["发布时间：2026年10月14日。", "导入时间为2026年10月14日。"])
def test_material_timestamp_cannot_be_promoted_to_effective_date(client, source, text):
    data = source("\n\n".join((RULE, SCOPE, text)))
    with pytest.raises(InvalidModelOutput, match="生效依据"):
        generate(client, data, response(data, effective_text=text))


def test_explicit_effective_on_publication_keeps_words_without_date_conversion(client, source):
    text = "本规定自发布日期起生效。"
    data = source("\n\n".join((RULE, SCOPE, text)))
    drafts = generate(client, data, response(data, effective_text=text))
    assert next(f.value for f in drafts[0].fields if f.name == "effective_text") == text
