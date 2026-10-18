"""Q019：Q018 语义复核的真实模型补跑（DeepSeek 生成 + 确定性假向量）。

默认跳过；仅在显式设置 ``CAMPUSFLOW_Q018_LIVE=1`` 时运行，避免 CI 产生外部费用。
费用护栏：整个模块最多 10 次生成调用，按最坏输入输出估算仍远低于 10 元预算。

只允许使用合成样本：不得把真实校务资料发往该接口（见 AGENTS.md 中的 DeepSeek 条款说明）。
运行方式（在 backend/ 目录）：

    CAMPUSFLOW_Q018_LIVE=1 CAMPUSFLOW_TEXT_PROVIDER=deepseek \
    CAMPUSFLOW_TEXT_MODEL=deepseek-chat uv run --locked pytest \
    tests/test_qa_acceptance_live.py -q -s
"""

import json
import os
from datetime import date

import pytest
from conftest import BACKEND_DIR
from index_helpers import FakeEmbedder, create_embed_job, open_session, run_index, workspace_id

from campusflow.api.deps import get_embedder, get_text_generator
from campusflow.core.config import Settings
from campusflow.domain.model_failures import INSUFFICIENT_MATERIALS_MESSAGE
from campusflow.infrastructure.db.models import Course, Material, Semester
from campusflow.workers.generation import build_text_generator

pytestmark = pytest.mark.skipif(
    os.environ.get("CAMPUSFLOW_Q018_LIVE") != "1",
    reason="真实模型复核需显式开启 CAMPUSFLOW_Q018_LIVE=1（会产生外部调用费用）",
)

MAX_LIVE_CALLS = 10


class BudgetedGenerator:
    """调用次数护栏与记录：超限直接失败，原文一并留存供证据复核。"""

    def __init__(self, inner, limit: int = MAX_LIVE_CALLS) -> None:
        self.inner = inner
        self.limit = limit
        self.calls = 0
        self.transcripts: list[dict] = []

    def generate(self, request) -> str:
        if self.calls >= self.limit:
            raise AssertionError(f"超出本次复核的调用上限（{self.limit} 次）")
        self.calls += 1
        answer = self.inner.generate(request)
        self.transcripts.append({"source_json": request.source_json, "answer": answer})
        return answer


@pytest.fixture
def live_generator(client):
    settings = Settings(_env_file=BACKEND_DIR / ".env")
    assert settings.text_provider == "deepseek", "运行前需设置 CAMPUSFLOW_TEXT_PROVIDER=deepseek"
    assert settings.deepseek_api_key, "运行前需在 backend/.env 配置 DEEPSEEK_API_KEY"
    assert settings.text_model.startswith("deepseek"), "DeepSeek 需搭配 deepseek-chat 等型号"
    return BudgetedGenerator(build_text_generator(settings))


def record(name: str, **fields) -> None:
    print("LIVE-RECORD " + json.dumps({"scenario": name, **fields}, ensure_ascii=False))


def setup_material(client, text, *, class_name="一班"):
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
        semester_id, course_id = semester.id, course.id
    job, material, _ = create_embed_job(client, texts=(text,))
    run_index(job, FakeEmbedder())
    with open_session() as session, session.begin():
        row = session.get(Material, material)
        row.semester_id, row.course_id, row.class_name = semester_id, course_id, class_name
    return semester_id, course_id, class_name


def bind_scope(client, material_id, semester_id, course_id, *, class_name="一班"):
    with open_session() as session, session.begin():
        row = session.get(Material, material_id)
        row.semester_id, row.course_id, row.class_name = semester_id, course_id, class_name


def ask(client, generator, scope, question):
    client.app.dependency_overrides[get_embedder] = lambda: FakeEmbedder()
    client.app.dependency_overrides[get_text_generator] = lambda: generator
    try:
        response = client.post(
            "/api/v1/qa/query",
            json={
                "semester_id": scope[0],
                "course_id": scope[1],
                "class_name": scope[2],
                "question": question,
            },
        )
    finally:
        client.app.dependency_overrides.pop(get_embedder, None)
        client.app.dependency_overrides.pop(get_text_generator, None)
    return response


def test_live_full_date_answer_matches_cited_text(client, live_generator):
    scope = setup_material(client, "数据库原理（CS101）第三次作业于2026年10月20日23:59前提交。")
    response = ask(client, live_generator, scope, "作业什么时候截止？")
    assert response.status_code == 200, response.text
    body = response.json()
    record("完整日期", body=body)
    assert body["insufficient"] is False
    assert "2026" in body["answer"] and "10月20日" in body["answer"] and "23:59" in body["answer"]
    citation = body["citations"][0]
    assert citation["valid"] is True
    assert "2026年10月20日23:59" in citation["excerpt"]
    assert not [item for item in body["uncertainties"] if "未出现在被引原文" in item]


def test_live_incomplete_date_is_flagged(client, live_generator):
    scope = setup_material(client, "第三次作业于10月20日截止。")
    response = ask(client, live_generator, scope, "作业什么时候截止？")
    assert response.status_code == 200, response.text
    body = response.json()
    record("不完整日期", body=body)
    assert body["insufficient"] is False
    assert any("没有年份" in item for item in body["uncertainties"])


def test_live_conflict_lists_both_dates_without_picking(client, live_generator):
    scope = setup_material(client, "数据库原理作业于10月20日截止。")
    job, material, _ = create_embed_job(client, texts=("数据库原理作业于10月25日截止。",))
    run_index(job, FakeEmbedder())
    bind_scope(client, material, scope[0], scope[1])
    response = ask(client, live_generator, scope, "作业什么时候截止？")
    assert response.status_code == 200, response.text
    body = response.json()
    record("多资料冲突", body=body)
    assert body["insufficient"] is False
    assert body["conflicts"][0]["requires_review"] is True
    message = body["conflicts"][0]["message"]
    assert "10月20日" in message and "10月25日" in message
    assert any("不能判断哪一条生效" in item for item in body["uncertainties"])


def test_live_partial_coverage_is_visible(client, live_generator):
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
        semester_id, course_id = semester.id, course.id
    job, material_id, _ = create_embed_job(
        client, texts=("数据库原理作业于2026年10月20日截止。",), coverage="partial"
    )
    run_index(job, FakeEmbedder())
    bind_scope(client, material_id, semester_id, course_id)
    response = ask(client, live_generator, (semester_id, course_id, "一班"), "作业什么时候截止？")
    assert response.status_code == 200, response.text
    body = response.json()
    record("部分覆盖", body=body)
    assert any(item["kind"] == "partial" for item in body["coverage"])
    assert body["insufficient"] is False
    assert body["citations"][0]["valid"] is True


def test_live_no_candidates_skips_model(client, live_generator):
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
        scope = (semester.id, course.id, "一班")
    before = live_generator.calls
    response = ask(client, live_generator, scope, "作业什么时候截止？")
    assert response.status_code == 200, response.text
    body = response.json()
    record("无候选", body=body)
    assert body["insufficient"] is True
    assert body["answer"] == INSUFFICIENT_MATERIALS_MESSAGE
    assert body["citations"] == []
    assert live_generator.calls == before, "无候选时不得调用生成模型"


def test_live_deleted_source_no_longer_cited(client, live_generator):
    scope = setup_material(client, "数据库原理作业于2026年10月20日截止。")
    with open_session() as session:
        from sqlalchemy import select

        material_id = session.scalars(select(Material.id)).one()
    impact = client.get(f"/api/v1/materials/{material_id}/delete-impact")
    assert impact.status_code == 200, impact.text
    deleted = client.post(
        f"/api/v1/materials/{material_id}/delete",
        json={"confirm_token": impact.json()["confirm_token"], "keep_tasks": True},
    )
    assert deleted.status_code == 204, deleted.text
    before = live_generator.calls
    response = ask(client, live_generator, scope, "作业什么时候截止？")
    assert response.status_code == 200, response.text
    body = response.json()
    record("来源删除", body=body)
    assert body["insufficient"] is True
    assert body["citations"] == []
    assert live_generator.calls == before, "来源删除后不得再调用生成模型"
