"""D003 集成测试：PDF 与图片上传校验。

验收：伪造扩展名与超限文件被拒绝；错误说明可理解且不留下孤立文件。
"""

from fastapi.testclient import TestClient

REAL_PDF = b"%PDF-1.4 minimal"
REAL_PNG = b"\x89PNG\r\n\x1a\n" + b"fake-png-content"


def _upload(client: TestClient, files: list[tuple[str, bytes]], **params) -> dict:
    response = client.post(
        "/api/v1/materials/upload",
        params=params,
        files=[("files", (name, content)) for name, content in files],
    )
    assert response.status_code in (201, 400, 404), response.text
    return response


def test_valid_pdf_and_image_upload(client: TestClient) -> None:
    response = _upload(
        client,
        [("作业通知.pdf", REAL_PDF), ("群聊截图.png", REAL_PNG)],
    )
    assert response.status_code == 201
    results = response.json()["results"]
    assert len(results) == 2
    by_name = {r["filename"]: r for r in results}
    assert by_name["作业通知.pdf"]["material"]["source_type"] == "pdf"
    assert by_name["群聊截图.png"]["material"]["source_type"] == "image"
    assert all(r["error"] is None for r in results)
    # 材料出现在列表中
    listed = client.get("/api/v1/materials").json()
    assert len(listed) == 2


def test_forged_extension_rejected(client: TestClient, tmp_path) -> None:
    """伪造扩展名（文本内容改名为 .pdf）被拒绝且不留文件（D003 验收）。"""
    client.app.state.file_storage = __import__(
        "campusflow.infrastructure.storage.local", fromlist=["LocalFileStorage"]
    ).LocalFileStorage(tmp_path)

    response = _upload(client, [("伪装.pdf", "这不是 PDF 内容".encode())])
    assert response.status_code == 201  # 批量接口：逐文件报告
    result = response.json()["results"][0]
    assert result["material"] is None
    assert "不符" in result["error"]
    # 没有留下任何文件
    assert not any(path.is_file() for path in tmp_path.rglob("*"))


def test_unsupported_type_rejected(client: TestClient) -> None:
    response = _upload(client, [("病毒.exe", b"MZ...")])
    result = response.json()["results"][0]
    assert result["material"] is None
    assert "不支持" in result["error"]


def test_oversized_file_rejected(client: TestClient) -> None:
    big = REAL_PDF + b"x" * (20 * 1024 * 1024)  # 超过默认 20MB 上限
    response = _upload(client, [("超大.pdf", big)])
    result = response.json()["results"][0]
    assert result["material"] is None
    assert "上限" in result["error"]


def test_too_many_files_rejected(client: TestClient) -> None:
    files = [(f"第{i}.png", REAL_PNG) for i in range(6)]  # 超过默认 5 个上限
    response = _upload(client, files)
    assert response.status_code == 400
    assert "最多" in response.json()["detail"]


def test_empty_file_rejected(client: TestClient) -> None:
    response = _upload(client, [("空.pdf", b"")])
    result = response.json()["results"][0]
    assert result["material"] is None
    assert "为空" in result["error"]


def test_upload_with_course_scope(client: TestClient) -> None:
    semester = client.post(
        "/api/v1/semesters",
        json={"name": "2026 秋", "start_date": "2026-09-01", "end_date": "2027-01-31"},
    ).json()
    course = client.post(
        "/api/v1/courses", json={"semester_id": semester["id"], "name": "数据库原理"}
    ).json()
    response = _upload(
        client,
        [("作业.pdf", REAL_PDF)],
        semester_id=semester["id"],
        course_id=course["id"],
    )
    material = response.json()["results"][0]["material"]
    assert material["course_id"] == course["id"]


def test_upload_rejects_foreign_course(client: TestClient) -> None:
    response = _upload(client, [("作业.pdf", REAL_PDF)], course_id=999)
    assert response.status_code == 404
