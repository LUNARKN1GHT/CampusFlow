"""X008 测试：解析未覆盖与来源冲突都要形成明确的候选核对原因。

验收：
- 被截断的来源不视为已确认，候选带"来源不完整"核对原因且仍为待核对；
- 两个来源对同一字段取值不一致时，两个取值与各自来源并列保留，不静默覆盖。
"""

import json

import pytest
from fastapi.testclient import TestClient

from campusflow.application.candidates import save_candidates
from campusflow.application.ports.parsers import (
    ParsedFragment,
    ParseFailure,
    ParseOutcome,
    ParserLocator,
)
from campusflow.application.processing import ParserRegistry, set_parser_registry
from campusflow.application.source_uncertainty import refresh_conflict_reasons, scope_key
from campusflow.application.task_extraction import extract_task_drafts
from campusflow.domain.candidates import CandidateKind
from campusflow.domain.source_conflicts import (
    SOURCE_CONFLICT,
    ConflictEntry,
    conflict_reason,
    field_conflicts,
)
from campusflow.domain.source_coverage import (
    SOURCE_INCOMPLETE,
    UncoveredRange,
    coverage_review_reasons,
)
from campusflow.domain.states import ReviewStatus
from campusflow.infrastructure.llm.text import FakeTextGenerator
from campusflow.infrastructure.parsers.pdf import PdfDocumentParser
from campusflow.infrastructure.parsers.text import TextDocumentParser
from campusflow.infrastructure.repositories import SqlAlchemyMaterialRepository, build_repositories
from campusflow.infrastructure.storage.local import LocalFileStorage
from campusflow.workers.jobs import parse_version_job

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"x008-image"
TRUNCATED_TEXT = "周五前提交实验报告，提交到课程平台"
CUT_REASON = "last message is cut by screenshot boundary"


# --------------------------------------------------------------- 领域规则


def test_covered_source_adds_no_reason() -> None:
    """没有未覆盖范围时不产生核对原因（也不因此把候选标为已确认）。"""
    assert coverage_review_reasons(()) == ()


def test_uncovered_range_becomes_candidate_level_reason() -> None:
    """未覆盖范围产生一条候选级原因，写明位置与解析器给出的原因。"""
    reasons = coverage_review_reasons(
        [UncoveredRange("region", "0.0540,0.8450,0.8460,1.0000", CUT_REASON)]
    )
    assert len(reasons) == 1
    assert reasons[0].code == SOURCE_INCOMPLETE
    assert reasons[0].field is None  # 未覆盖范围没有可归属的字段证据
    assert "0.0540,0.8450,0.8460,1.0000" in reasons[0].message
    assert CUT_REASON in reasons[0].message


def test_duplicate_ranges_collapse_and_message_stays_bounded() -> None:
    """重复范围去重；范围很多时说明文字仍不超过核对原因的长度上限。"""
    item = UncoveredRange("page", "3", "扫描页无可提取文本")
    assert len(coverage_review_reasons([item, item])) == 1

    many = [UncoveredRange("page", str(i), "扫描页无可提取文本" * 8) for i in range(1, 500)]
    message = coverage_review_reasons(many)[0].message
    assert len(message) <= 1000
    assert "共 499 处未覆盖范围" in message


def _entry(candidate_id: int, version_id: int, field: str, value, scope=(None, None, None)):
    return ConflictEntry(
        candidate_id,
        f"来源版本 #{version_id}",
        CandidateKind.TASK,
        scope,
        field,
        value,
    )


def test_equal_values_are_not_a_conflict() -> None:
    entries = [
        _entry(1, 10, "due_text", "2026-10-18T18:00"),
        _entry(2, 11, "due_text", "2026-10-18T18:00"),
    ]
    assert field_conflicts(entries) == ()


def test_conflicting_values_list_every_source() -> None:
    """冲突原因同时给出两个取值与各自来源，不挑选"正确"的一个。"""
    conflict = field_conflicts(
        [
            _entry(1, 10, "due_text", "2026-10-18T18:00"),
            _entry(2, 11, "due_text", "2026-10-20T20:00"),
        ]
    )[0]
    reason = conflict_reason(conflict)
    assert reason.code == SOURCE_CONFLICT
    assert reason.field == "due_text"
    assert "2026-10-18T18:00" in reason.message
    assert "2026-10-20T20:00" in reason.message
    assert "#10" in reason.message
    assert "#11" in reason.message


def test_missing_value_is_not_a_conflict() -> None:
    """缺失已由 missing_* 原因表达，不构成第二个取值。"""
    assert (
        field_conflicts([_entry(1, 10, "due_text", "2026-10-18"), _entry(2, 11, "due_text", None)])
        == ()
    )


def test_different_scopes_are_not_compared() -> None:
    """不同适用范围的同名课程不算冲突（不跨课程比对）。"""
    entries = [
        _entry(1, 10, "due_text", "2026-10-18", scope=(None, None, "A班")),
        _entry(2, 11, "due_text", "2026-10-20", scope=(None, None, "B班")),
    ]
    assert field_conflicts(entries) == ()


# --------------------------------------------------------------- 集成路径


class CroppedScreenshotParser:
    """模拟被截断的群聊截图：正文可读，最后一条消息被截图边界切掉。"""

    def parse(self, content: bytes, mime_type: str) -> ParseOutcome:
        return ParseOutcome(
            fragments=[
                ParsedFragment(
                    seq=0,
                    locator=ParserLocator(kind="region", bbox=(0.0, 0.0, 1.0, 0.84)),
                    text=TRUNCATED_TEXT,
                )
            ],
            failures=[
                ParseFailure(
                    locator=ParserLocator(kind="region", bbox=(0.054, 0.845, 0.846, 1.0)),
                    reason=CUT_REASON,
                )
            ],
        )


@pytest.fixture
def storage(client: TestClient, tmp_path, monkeypatch):
    storage = LocalFileStorage(tmp_path)
    client.app.state.file_storage = storage
    monkeypatch.setenv("CAMPUSFLOW_STORAGE_DIR", str(tmp_path))
    return storage


def _use_vision_parser(parser) -> None:
    set_parser_registry(
        ParserRegistry(
            text_parser=TextDocumentParser(),
            pdf_parser=PdfDocumentParser(),
            vision_parser=parser,
        )
    )


def _upload(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/materials/upload",
        files=[("files", ("群聊截图.png", PNG_BYTES))],
    )
    assert response.status_code == 201, response.text
    result = response.json()["results"][0]
    assert result["job_id"] is not None, result
    result["workspace_id"] = client.get("/api/v1/settings").json()["workspace_id"]
    return result


def _chunks(client: TestClient, material_id: int) -> list[dict]:
    """片段在解析完成后才存在，只能在解析之后读取。"""
    response = client.get(f"/api/v1/materials/{material_id}/versions/1/chunks")
    assert response.status_code == 200, response.text
    return response.json()


def _import_text(client: TestClient, title: str, content: str) -> dict:
    response = client.post("/api/v1/materials/text", json={"title": title, "content": content})
    assert response.status_code == 201, response.text
    data = response.json()
    data["workspace_id"] = client.get("/api/v1/settings").json()["workspace_id"]
    return data


def _task_raw(chunk_id: int, due_text: str) -> dict:
    """一个任务候选：标题与截止取自原文，其余字段按缺失上报。"""

    def document(name: str, value: str) -> dict:
        return {
            "name": name,
            "value": value,
            "origin": "document",
            "evidence": [{"chunk_id": chunk_id, "quote": value}],
        }

    def missing(name: str) -> dict:
        # 耗时字段只接受"规划建议"出处，缺失时也要符合该字段的出处约定
        origin = "suggestion" if name == "estimated_minutes" else "document"
        return {
            "name": name,
            "value": None,
            "origin": origin,
            "missing_reason": "资料未明确说明",
        }

    return {
        "items": [
            {
                "kind": "task",
                "fields": [
                    document("title", "实验报告"),
                    missing("submission_method"),
                    missing("deliverable"),
                    document("due_text", due_text),
                    missing("estimated_minutes"),
                ],
            }
        ]
    }


def _extract_and_save(client: TestClient, source: dict, due_text: str):
    """把一份来源提取成一条任务候选并保存，返回保存后的候选。"""
    raw = _task_raw(source["chunks"][0]["id"], due_text)
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        drafts = extract_task_drafts(
            repos,
            source["workspace_id"],
            source["material"]["id"],
            1,
            FakeTextGenerator(json.dumps(raw, ensure_ascii=False)),
        )
        return save_candidates(repos, source["workspace_id"], source["material"]["id"], 1, drafts)[
            0
        ]


def _value(candidate, name: str):
    return next(field.value for field in candidate.draft.fields if field.name == name)


def test_truncated_parse_is_stored_structurally(client: TestClient, storage) -> None:
    """覆盖范围按固定结构存回版本元数据，程序不必反解给人看的说明文字。"""
    _use_vision_parser(CroppedScreenshotParser())
    result = _upload(client)
    parse_version_job(result["job_id"])

    with client.app.state.session_factory() as session:
        version = SqlAlchemyMaterialRepository(session).list_versions(result["material"]["id"])[0]

    coverage = version.source_metadata["coverage"]
    assert coverage["fragment_count"] == 1
    assert coverage["failures"] == [
        {
            "locator_type": "region",
            "locator_value": "0.0540,0.8450,0.8460,1.0000",
            "reason": CUT_REASON,
            "retryable": False,
        }
    ]


def test_truncated_source_candidate_is_not_treated_as_confirmed(
    client: TestClient, storage
) -> None:
    """截断来源的候选必须带"来源不完整"原因，并以待核对状态落库。"""
    _use_vision_parser(CroppedScreenshotParser())
    result = _upload(client)
    parse_version_job(result["job_id"])

    raw = _task_raw(_chunks(client, result["material"]["id"])[0]["id"], "周五前提交实验报告")
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        drafts = extract_task_drafts(
            repos,
            result["workspace_id"],
            result["material"]["id"],
            1,
            FakeTextGenerator(json.dumps(raw, ensure_ascii=False)),
        )
        assert any(r.code == SOURCE_INCOMPLETE for r in drafts[0].review_reasons)
        saved = save_candidates(repos, result["workspace_id"], result["material"]["id"], 1, drafts)

    candidate = saved[0]
    assert candidate.review_status is ReviewStatus.PENDING
    assert candidate.review_status is not ReviewStatus.CONFIRMED
    assert any(r.code == SOURCE_INCOMPLETE for r in candidate.draft.review_reasons)


def test_plain_text_source_without_failures_adds_no_coverage_reason(
    client: TestClient, storage
) -> None:
    """没有报告未覆盖范围的来源不加这层原因（不制造无根据的核对提示）。"""
    source = _import_text(client, "纯文本通知", "实验报告请于10月18日提交")
    candidate = _extract_and_save(client, source, "10月18日提交")
    assert all(r.code != SOURCE_INCOMPLETE for r in candidate.draft.review_reasons)


CONFLICTING = (
    ("通知一", "实验报告请于10月18日提交", "10月18日提交"),
    ("通知二", "实验报告请于10月20日提交", "10月20日提交"),
)


def test_conflicting_sources_keep_both_values_and_flag_both_sides(
    client: TestClient, storage
) -> None:
    """同一字段在两个来源取值不同：两个值都保留，两侧都带冲突原因（B012 验收）。"""
    saved = [
        _extract_and_save(client, _import_text(client, t, c), due) for t, c, due in CONFLICTING
    ]
    assert len(saved) == 2

    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        candidates = [repos.candidates.get(item.workspace_id, item.id) for item in saved]

    first, second = candidates
    assert first is not None and second is not None
    # 两个取值并列保留，没有任何一侧被覆盖或作废
    assert _value(first, "due_text") == "10月18日提交"
    assert _value(second, "due_text") == "10月20日提交"
    assert first.review_status is ReviewStatus.PENDING
    assert second.review_status is ReviewStatus.PENDING
    # 两侧都能看到完整的冲突说明：两个取值 + 两个来源
    for candidate in (first, second):
        conflicts = [r for r in candidate.draft.review_reasons if r.code == SOURCE_CONFLICT]
        assert len(conflicts) == 1
        assert conflicts[0].field == "due_text"
        assert "10月18日提交" in conflicts[0].message
        assert "10月20日提交" in conflicts[0].message
        assert "#" in conflicts[0].message


def test_conflict_refresh_is_idempotent(client: TestClient, storage) -> None:
    """重复刷新不重复添加冲突原因，也不再产生无谓写入。"""
    saved = [
        _extract_and_save(client, _import_text(client, t, c), due) for t, c, due in CONFLICTING
    ]
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        scopes = [scope_key(item.draft.scope) for item in saved]
        assert refresh_conflict_reasons(repos, saved[0].workspace_id, scopes) == 0
        reasons = repos.candidates.get(saved[0].workspace_id, saved[0].id).draft.review_reasons
        assert [r.code for r in reasons].count(SOURCE_CONFLICT) == 1
