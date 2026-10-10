"""D006 集成测试：完全相同文件的重复导入检测。

验收：完全重复可提示已有资料；不同版本或不同空间不被静默合并。
"""

from conftest import TEST_DATABASE_URL
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from campusflow.infrastructure.db.models import Material, MaterialVersion, Workspace

PDF_A = "%PDF-1.4 内容甲".encode()
PDF_A_COPY = "%PDF-1.4 内容甲".encode()  # 与 A 完全相同的字节
PDF_B = "%PDF-1.4 内容乙".encode()  # 不同内容


def _upload(client: TestClient, name: str, content: bytes, **params) -> dict:
    response = client.post(
        "/api/v1/materials/upload",
        params=params,
        files=[("files", (name, content))],
    )
    assert response.status_code == 201, response.text
    return response.json()["results"][0]


def test_exact_duplicate_prompts_existing_material(client: TestClient) -> None:
    """同一文件传两次：第二次提示已有资料且不新建（D006 验收）。"""
    first = _upload(client, "通知.pdf", PDF_A)
    assert first["material"] is not None
    assert first["duplicate_of"] is None

    second = _upload(client, "改了个文件名.pdf", PDF_A_COPY)
    assert second["material"] is None
    assert second["duplicate_of"] is not None
    assert second["duplicate_of"]["id"] == first["material"]["id"]
    assert second["duplicate_of"]["title"] == "通知.pdf"

    # 数据库中只有一条资料
    listed = client.get("/api/v1/materials").json()
    assert len(listed) == 1


def test_different_version_creates_new_material(client: TestClient) -> None:
    """内容不同的文件（新版本）正常建档，不被合并（D006 验收）。"""
    first = _upload(client, "通知 v1.pdf", PDF_A)
    second = _upload(client, "通知 v2.pdf", PDF_B)
    assert second["material"] is not None
    assert second["duplicate_of"] is None
    assert second["material"]["id"] != first["material"]["id"]

    listed = client.get("/api/v1/materials").json()
    assert len(listed) == 2


def test_allow_duplicate_creates_anyway(client: TestClient) -> None:
    """用户明确选择保留两份时允许重复建档（复用选择由用户掌控）。"""
    _upload(client, "通知.pdf", PDF_A)
    second = _upload(client, "通知.pdf", PDF_A_COPY, allow_duplicate=True)
    assert second["material"] is not None
    assert second["duplicate_of"] is None

    listed = client.get("/api/v1/materials").json()
    assert len(listed) == 2


def test_other_workspace_file_not_matched(client: TestClient) -> None:
    """其他空间已有相同文件，不影响当前空间建档（不跨空间合并）。"""
    engine = create_engine(TEST_DATABASE_URL)
    with Session(engine) as session:
        other = Workspace(name="另一个空间")
        session.add(other)
        session.flush()
        material = Material(
            workspace_id=other.id,
            title="他人的通知.pdf",
            source_type="pdf",
        )
        session.add(material)
        session.flush()
        import hashlib

        session.add(
            MaterialVersion(
                material_id=material.id,
                version_no=1,
                checksum=hashlib.sha256(PDF_A).hexdigest(),
            )
        )
        session.commit()

    result = _upload(client, "我的通知.pdf", PDF_A)
    assert result["material"] is not None  # 当前空间正常建档
    assert result["duplicate_of"] is None
