"""D004 集成测试：粘贴文本导入。

验收：粘贴内容保存为稳定原文版本并关联来源及课程范围；
段落位置在重复读取时稳定；空文本和超限输入返回明确错误。
"""

import pytest
from fastapi.testclient import TestClient

from campusflow.domain.materials import MAX_PASTED_TEXT_CHARS, split_paragraphs

NOTICE_TEXT = (
    "各位同学：\n"
    "\n"
    "数据库原理作业三已于本周发布，请在课程平台完成提交。\n"
    "\n"
    "截止时间：2026-10-08 18:00，逾期不再接收。\n"
    "\n"
    "如有疑问请联系助教。\n"
)


def test_import_pasted_text_creates_stable_version(client: TestClient) -> None:
    response = client.post(
        "/api/v1/materials/text",
        json={
            "title": "作业三通知",
            "content": NOTICE_TEXT,
            "publisher": "课程群",
            "published_at": "2026-09-30T12:00:00",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()

    material = body["material"]
    assert material["title"] == "作业三通知"
    assert material["source_type"] == "text"
    assert material["status"] == "pending"
    # 发布时间与导入时间分开（D001 语义在导入路径上保持）
    assert material["published_at"] is not None
    assert material["imported_at"] is not None
    assert material["published_at"] != material["imported_at"]

    chunks = body["chunks"]
    assert len(chunks) == 4
    assert [c["locator_value"] for c in chunks] == ["1", "2", "3", "4"]
    assert all(c["locator_type"] == "paragraph" for c in chunks)
    assert chunks[1]["text"].startswith("数据库原理作业三")


def test_paragraph_positions_stable_across_reads(client: TestClient) -> None:
    """同一份文本重复导入与重复读取，段落号与内容完全一致（D004 验收）。"""
    first = client.post(
        "/api/v1/materials/text", json={"title": "样本一", "content": NOTICE_TEXT}
    ).json()
    second = client.post(
        "/api/v1/materials/text", json={"title": "样本二", "content": NOTICE_TEXT}
    ).json()
    assert [(c["seq"], c["locator_value"], c["text"]) for c in first["chunks"]] == [
        (c["seq"], c["locator_value"], c["text"]) for c in second["chunks"]
    ]


def test_empty_text_rejected(client: TestClient) -> None:
    response = client.post("/api/v1/materials/text", json={"title": "空白", "content": "   \n  \n"})
    assert response.status_code == 400
    assert "不能为空" in response.json()["detail"]


def test_oversized_text_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/materials/text",
        json={"title": "超长", "content": "字" * (MAX_PASTED_TEXT_CHARS + 1)},
    )
    assert response.status_code == 400
    assert "超出上限" in response.json()["detail"]


def test_import_with_course_scope(client: TestClient) -> None:
    semester = client.post(
        "/api/v1/semesters",
        json={"name": "2026 秋", "start_date": "2026-09-01", "end_date": "2027-01-31"},
    ).json()
    course = client.post(
        "/api/v1/courses",
        json={"semester_id": semester["id"], "name": "数据库原理"},
    ).json()

    response = client.post(
        "/api/v1/materials/text",
        json={
            "title": "作业三通知",
            "content": NOTICE_TEXT,
            "semester_id": semester["id"],
            "course_id": course["id"],
            "class_name": "1 班",
        },
    )
    assert response.status_code == 201
    material = response.json()["material"]
    assert material["semester_id"] == semester["id"]
    assert material["course_id"] == course["id"]
    assert material["class_name"] == "1 班"


def test_import_rejects_unknown_semester_and_course(client: TestClient) -> None:
    response = client.post(
        "/api/v1/materials/text",
        json={"title": "挂错地方", "content": NOTICE_TEXT, "semester_id": 999},
    )
    assert response.status_code == 404
    response = client.post(
        "/api/v1/materials/text",
        json={"title": "挂错地方", "content": NOTICE_TEXT, "course_id": 999},
    )
    assert response.status_code == 404


def test_split_paragraphs_normalizes_line_endings() -> None:
    assert split_paragraphs("第一段\r\n\r\n第二段\n\n\n第三段\n") == ["第一段", "第二段", "第三段"]
    assert split_paragraphs("  只有一段  ") == ["只有一段"]
    assert split_paragraphs("\n\n\n") == []


def test_commit_failure_cleans_up_file_and_rolls_back(
    client: TestClient, tmp_path, monkeypatch
) -> None:
    """提交失败时：数据库回滚且已保存的原文件被删除，不留孤立文件（评审回归）。"""
    from conftest import TEST_DATABASE_URL
    from sqlalchemy import select

    from campusflow.infrastructure.db.models import Material
    from campusflow.infrastructure.repositories import SqlAlchemyUnitOfWork
    from campusflow.infrastructure.storage.local import LocalFileStorage

    # 先触发默认空间创建，再打补丁，避免空间初始化被模拟失败波及
    assert client.get("/api/v1/settings").status_code == 200

    # 用独立临时存储目录观察文件是否被清理
    storage = LocalFileStorage(tmp_path)
    client.app.state.file_storage = storage

    real_commit = SqlAlchemyUnitOfWork.commit

    def failing_commit(self) -> None:
        raise RuntimeError("模拟数据库提交失败")

    monkeypatch.setattr(SqlAlchemyUnitOfWork, "commit", failing_commit)
    try:
        with pytest.raises(RuntimeError, match="模拟数据库提交失败"):
            client.post(
                "/api/v1/materials/text", json={"title": "会失败的导入", "content": NOTICE_TEXT}
            )
    finally:
        monkeypatch.setattr(SqlAlchemyUnitOfWork, "commit", real_commit)
    # 不留下任何文件
    assert list(tmp_path.rglob("*")) == [] or not any(
        path.is_file() for path in tmp_path.rglob("*")
    )
    # 不留下任何资料记录
    engine = __import__("sqlalchemy").create_engine(TEST_DATABASE_URL)
    with engine.connect() as connection:
        count = connection.execute(select(Material)).all()
    assert count == []


def test_semester_course_mismatch_rejected(client: TestClient) -> None:
    """课程与学期不一致时拒绝导入（评审回归：不混用不同学期要求）。"""
    semester_a = client.post(
        "/api/v1/semesters",
        json={"name": "2026 秋", "start_date": "2026-09-01", "end_date": "2027-01-31"},
    ).json()
    semester_b = client.post(
        "/api/v1/semesters",
        json={"name": "2027 春", "start_date": "2027-02-15", "end_date": "2027-07-15"},
    ).json()
    course = client.post(
        "/api/v1/courses", json={"semester_id": semester_a["id"], "name": "数据库原理"}
    ).json()

    response = client.post(
        "/api/v1/materials/text",
        json={
            "title": "矛盾范围",
            "content": NOTICE_TEXT,
            "semester_id": semester_b["id"],  # 课程在 A 学期，资料却标 B 学期
            "course_id": course["id"],
        },
    )
    assert response.status_code == 400
    assert "学期" in response.json()["detail"]

    # 一致的组合应正常通过
    ok = client.post(
        "/api/v1/materials/text",
        json={
            "title": "一致范围",
            "content": NOTICE_TEXT,
            "semester_id": semester_a["id"],
            "course_id": course["id"],
        },
    )
    assert ok.status_code == 201
