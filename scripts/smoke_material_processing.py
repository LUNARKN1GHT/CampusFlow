"""J009 可复现冒烟：真实 PostgreSQL/Redis + 独立 Linux Worker，视觉用假适配器。

从 backend/ 运行。必须显式设置以 _test 结尾的数据库和非 0 Redis 库号。
只新增本次合成资料，不清库；使用临时文件目录，完成后随目录清理。
"""

import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path

import pymupdf
import redis
from campusflow.core.config import Settings
from campusflow.main import create_app
from fastapi.testclient import TestClient
from rq import Queue
from sqlalchemy.engine import make_url


def main() -> None:
    if not os.environ.get("CAMPUSFLOW_DATABASE_URL") or not os.environ.get(
        "CAMPUSFLOW_REDIS_URL"
    ):
        raise SystemExit("先设置专用测试数据库和独立 Redis 库号，脚本不读取 .env")
    settings = Settings(_env_file=None, dispatch_on_startup=False)
    database = make_url(settings.database_url)
    redis_url = make_url(settings.redis_url)
    if not (database.database or "").endswith("_test"):
        raise SystemExit("只允许专用 _test 数据库")
    if not (redis_url.database or "").isdigit() or int(redis_url.database) == 0:
        raise SystemExit("必须显式选择非 0 的专用 Redis 库")
    connection = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=3)
    if Queue("campusflow-parse", connection=connection).count:
        raise SystemExit("专用队列非空：先核对旧作业，脚本不会删除或消费不明作业")

    root = Path(__file__).resolve().parents[1]
    scratch = root / "backend" / "data"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="j009-smoke-", dir=scratch) as temporary:
        assert Path(temporary).resolve().is_relative_to(scratch.resolve())
        settings.storage_dir = temporary
        app = create_app(settings)
        with TestClient(app) as client:
            login = client.post(
                "/api/v1/auth/login",
                json={
                    "username": settings.local_username,
                    "password": settings.local_password,
                },
            )
            assert login.status_code == 200
            label = uuid.uuid4().hex
            response = client.post(
                "/api/v1/materials/text",
                json={
                    "title": "J009 synthetic " + label,
                    "content": "first paragraph\n\nsecond",
                },
            )
            assert response.status_code == 201
            assert len(response.json()["chunks"]) == 2

            with pymupdf.open() as image_document:
                image_page = image_document.new_page(width=200, height=100)
                image_page.insert_text((15, 40), "synthetic image " + label[:8])
                png = image_page.get_pixmap().tobytes("png")
            with pymupdf.open() as pdf:
                pdf.new_page().insert_text((72, 72), "synthetic PDF " + label)
                pdf.new_page().insert_image(pymupdf.Rect(20, 20, 220, 120), stream=png)
                pdf_bytes = pdf.tobytes()

            uploads = []
            for filename, content in (("sample.pdf", pdf_bytes), ("sample.png", png)):
                result = client.post(
                    "/api/v1/materials/upload", files=[("files", (filename, content))]
                )
                assert result.status_code == 201
                upload = result.json()["results"][0]
                assert upload["job_id"] is not None and upload["error"] is None
                uploads.append(upload)

            child_env = os.environ.copy()
            child_env.update(
                CAMPUSFLOW_DATABASE_URL=database.set(
                    host="host.docker.internal"
                ).render_as_string(hide_password=False),
                CAMPUSFLOW_REDIS_URL=redis_url.set(
                    host="host.docker.internal"
                ).render_as_string(hide_password=False),
                CAMPUSFLOW_STORAGE_DIR="/app/data/materials",
            )
            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--add-host",
                    "host.docker.internal:host-gateway",
                    "-e",
                    "CAMPUSFLOW_DATABASE_URL",
                    "-e",
                    "CAMPUSFLOW_REDIS_URL",
                    "-e",
                    "CAMPUSFLOW_STORAGE_DIR",
                    "--mount",
                    f"type=bind,source={temporary},target=/app/data/materials",
                    "campusflow-worker-worker",
                    "uv",
                    "run",
                    "--locked",
                    "--no-dev",
                    "python",
                    "-m",
                    "campusflow.workers.main",
                    "--demo",
                    "--burst",
                ],
                env=child_env,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if result.returncode:
                raise RuntimeError("独立 Worker 退出失败：" + result.stderr[-1500:])

            evidence = []
            for upload in uploads:
                job = client.get(f"/api/v1/jobs/{upload['job_id']}").json()
                assert job["status"] == "done", job
                material_id = upload["material"]["id"]
                chunks = client.get(
                    f"/api/v1/materials/{material_id}/versions/1/chunks"
                ).json()
                assert chunks
                if upload is uploads[0]:
                    assert {chunk["page"] for chunk in chunks} == {1, 2}
                original = client.get(
                    f"/api/v1/materials/{material_id}/versions/1/file"
                )
                assert original.status_code == 200
                assert original.content == (pdf_bytes if upload is uploads[0] else png)
                evidence.append(
                    {
                        "job_id": job["id"],
                        "status": job["status"],
                        "chunks": len(chunks),
                    }
                )
            print(
                json.dumps(
                    {"text": "passed", "pdf_and_image": evidence, "vision": "fake"}
                )
            )
    connection.close()


if __name__ == "__main__":
    main()
