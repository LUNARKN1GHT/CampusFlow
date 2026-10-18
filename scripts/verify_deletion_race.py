"""V003：真实 PostgreSQL + 私有 Redis 容器 + Linux RQ Worker 的删除竞态复验。

从 backend/ 运行，显式指定空的 *_test 数据库；不读取 .env，不调用真实模型。
通过 Redis 握手停在解析/向量调用中，确认删除提交后才放行，不靠固定睡眠碰运气。
"""

import argparse
import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path

import redis
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from campusflow.core.config import Settings


def pause_at_gate(connection, key):
    connection.rpush(key + ":ready", "ready")
    connection.expire(key + ":ready", 90)
    if connection.blpop(key + ":release", timeout=60) is None:
        raise RuntimeError("V003 gate timed out; verification failed")


def worker(stage, gate):
    from campusflow.application.processing import ParserRegistry, set_parser_registry
    from campusflow.infrastructure.queue.rq_queue import INDEX_QUEUE_NAME, QUEUE_NAME
    from campusflow.workers.embeddings import build_demo_embedder, set_embedder_factory
    from campusflow.workers.main import build_parser_registry
    from campusflow.workers.safe_worker import SafeWorker

    settings = Settings(_env_file=None)
    if not (make_url(settings.database_url).database or "").endswith("_test"):
        raise RuntimeError("V003 worker only permits a test database")
    connection = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=3)
    registry = build_parser_registry(settings, demo=True)

    class PausedParser:
        def parse(self, content, mime_type):
            from campusflow.domain.states import MaterialSourceType

            pause_at_gate(connection, gate)
            return registry.for_type(MaterialSourceType.PDF).parse(content, mime_type)

    class PausedEmbedder:
        def __init__(self, config):
            self.base = build_demo_embedder(config)
            self.calls = 0

        @property
        def profile(self):
            return self.base.profile

        def embed(self, text):
            self.calls += 1
            if self.calls == 2:
                pause_at_gate(connection, gate)
            return self.base.embed(text)

    if stage == "parse":
        # 只替换耗时边界；领取、读文件、写回守卫和 RQ 分叉仍使用生产代码。
        from campusflow.domain.states import MaterialSourceType

        parser = PausedParser()
        set_parser_registry(
            ParserRegistry(
                text_parser=registry.for_type(MaterialSourceType.TEXT),
                pdf_parser=parser,
                vision_parser=registry.for_type(MaterialSourceType.IMAGE),
            )
        )
    else:
        set_parser_registry(registry)
    set_embedder_factory(PausedEmbedder if stage == "index" else build_demo_embedder)
    SafeWorker(
        [INDEX_QUEUE_NAME if stage == "index" else QUEUE_NAME],
        connection=connection,
        log_job_description=False,
    ).work(burst=True)


class EvidenceGenerator:
    """只回显首条证据，不测试真实模型的语义能力。"""

    def generate(self, request):
        candidate = json.loads(request.source_json)["candidates"][0]
        return json.dumps(
            {
                "answer": "Synthetic homework evidence.",
                "citation_ids": [candidate["citation_id"]],
                "uncertainties": [],
            }
        )


def docker(*args, env=None):
    result = subprocess.run(
        ["docker", *args],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=45,
        check=False,
    )
    if result.returncode:
        # 不回显命令环境或连接字符串。
        raise RuntimeError(f"Docker {args[0]} failed; inspect local container state")
    return result.stdout.strip()


def require_response(response, status=200):
    if response.status_code != status:
        raise AssertionError(f"HTTP {response.status_code}, expected {status}: {response.text}")
    return response.json() if status != 204 else None


def upload(client, label):
    import pymupdf

    with pymupdf.open() as pdf:
        for page in range(2):
            pdf.new_page().insert_text((72, 72), f"Synthetic homework {label}, page {page + 1}.")
        content = pdf.tobytes()
    result = require_response(
        client.post("/api/v1/materials/upload", files=[("files", (label + ".pdf", content))]), 201
    )["results"][0]
    assert result["error"] is None
    return result["material"]["id"], result["job_id"]


def confirm_task(client, engine, material):
    from campusflow.application.candidates import save_candidates
    from campusflow.domain.candidates import (
        CandidateDraft,
        CandidateField,
        CandidateKind,
        FieldEvidence,
        FieldOrigin,
    )
    from campusflow.infrastructure.repositories import build_repositories

    chunk = require_response(client.get(f"/api/v1/materials/{material}/versions/1/chunks"))[0]
    workspace = require_response(client.get("/api/v1/settings"))["workspace_id"]
    with Session(engine) as session:
        candidate = save_candidates(
            build_repositories(session),
            workspace,
            material,
            1,
            [
                CandidateDraft(
                    CandidateKind.TASK,
                    (
                        CandidateField(
                            "title",
                            "Synthetic homework",
                            FieldOrigin.DOCUMENT,
                            (FieldEvidence(chunk["id"], chunk["text"]),),
                        ),
                    ),
                ),
            ],
        )[0]
    require_response(
        client.patch(
            f"/api/v1/candidates/{candidate.id}/review-status", json={"status": "confirmed"}
        )
    )
    task = require_response(client.post(f"/api/v1/candidates/{candidate.id}/convert"))["object_id"]
    require_response(
        client.patch(
            f"/api/v1/tasks/{task}/progress",
            json={
                "progress": "in_progress",
                "reason": "V003 synthetic progress",
            },
        )
    )
    return task, candidate.id


def delete(client, material):
    impact = require_response(client.get(f"/api/v1/materials/{material}/delete-impact"))
    require_response(
        client.post(
            f"/api/v1/materials/{material}/delete",
            json={
                "confirm_token": impact["confirm_token"],
                "keep_tasks": True,
            },
        ),
        204,
    )


def assert_absent(client, engine, material, version, storage_key, storage):
    from campusflow.infrastructure.db.models import (
        ExtractedItem,
        IndexedBlock,
        IndexManifest,
        MaterialVersion,
        ProcessingJob,
        SourceChunk,
    )

    assert client.get(f"/api/v1/materials/{material}").status_code == 404
    assert client.get(f"/api/v1/materials/{material}/versions/1/file").status_code == 404
    assert not (storage / storage_key).exists()
    with Session(engine) as session:
        for model, column in (
            (MaterialVersion, MaterialVersion.id),
            (SourceChunk, SourceChunk.version_id),
            (IndexedBlock, IndexedBlock.version_id),
            (IndexManifest, IndexManifest.version_id),
            (ProcessingJob, ProcessingJob.version_id),
            (ExtractedItem, ExtractedItem.source_version_id),
        ):
            assert (
                session.scalar(select(func.count()).select_from(model).where(column == version))
                == 0
            ), model.__name__


def verify(image):
    from alembic import command
    from alembic.config import Config
    from fastapi.testclient import TestClient
    from rq import Queue
    from rq.job import Job

    from campusflow.api.deps import get_embedder, get_text_generator
    from campusflow.infrastructure.db.models import MaterialVersion, ProcessingJob, Workspace
    from campusflow.infrastructure.queue.rq_queue import INDEX_QUEUE_NAME, QUEUE_NAME
    from campusflow.main import create_app
    from campusflow.workers.embeddings import build_demo_embedder
    from campusflow.workers.jobs import index_version_job, parse_version_job

    if not os.environ.get("CAMPUSFLOW_DATABASE_URL"):
        raise RuntimeError("Set CAMPUSFLOW_DATABASE_URL to an empty *_test database")
    settings = Settings(_env_file=None, dispatch_on_startup=False, recovery_interval_seconds=0)
    database = make_url(settings.database_url)
    if not (database.database or "").endswith("_test"):
        raise RuntimeError("Only *_test databases are allowed")
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "backend/alembic.ini"))
    config.set_main_option("script_location", str(root / "backend/migrations"))
    command.upgrade(config, "head")
    engine = create_engine(settings.database_url)
    with Session(engine) as session:
        if session.scalar(select(func.count()).select_from(Workspace)):
            raise RuntimeError("Use an empty test database; this verifier never truncates data")
    label = "campusflow-v003-" + uuid.uuid4().hex[:12]
    network = label + "-net"
    redis_name = label + "-redis"
    workers = []
    evidence = []
    scratch = root / "backend/data"
    scratch.mkdir(exist_ok=True)
    docker("network", "create", network)
    try:
        docker(
            "run",
            "--rm",
            "-d",
            "--name",
            redis_name,
            "--network",
            network,
            "-p",
            "127.0.0.1::6379",
            "redis:7",
        )
        port = docker("port", redis_name, "6379/tcp").rsplit(":", 1)[1]
        settings.redis_url = f"redis://127.0.0.1:{port}/0"
        connection = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=3)
        connection.ping()
        with tempfile.TemporaryDirectory(prefix="v003-", dir=scratch) as temporary:
            storage = Path(temporary).resolve()
            assert storage.is_relative_to(scratch.resolve())
            settings.storage_dir = str(storage)
            child_env = os.environ.copy()
            child_env.update(
                CAMPUSFLOW_DATABASE_URL=database.set(host="host.docker.internal").render_as_string(
                    hide_password=False
                ),
                CAMPUSFLOW_REDIS_URL=f"redis://{redis_name}:6379/0",
                CAMPUSFLOW_STORAGE_DIR="/app/data/materials",
            )

            def start(stage, gate="unused"):
                name = label + "-" + str(len(workers))
                workers.append(name)
                docker(
                    "run",
                    "-d",
                    "--name",
                    name,
                    "--network",
                    network,
                    "--add-host",
                    "host.docker.internal:host-gateway",
                    "-e",
                    "CAMPUSFLOW_DATABASE_URL",
                    "-e",
                    "CAMPUSFLOW_REDIS_URL",
                    "-e",
                    "CAMPUSFLOW_STORAGE_DIR",
                    "--mount",
                    f"type=bind,source={storage},target=/app/data/materials",
                    "--mount",
                    f"type=bind,source={root / 'scripts'},target=/verification,readonly",
                    image,
                    "/app/.venv/bin/python",
                    "/verification/verify_deletion_race.py",
                    "--worker",
                    stage,
                    "--gate",
                    gate,
                    env=child_env,
                )
                return name

            def finished(name):
                assert docker("wait", name) == "0", "Worker exited unsuccessfully"
                # RQ 可以吞下作业异常后仍以 0 退出，必须另查失败作业。
                for queue in (QUEUE_NAME, INDEX_QUEUE_NAME):
                    assert not Queue(
                        queue, connection=connection
                    ).failed_job_registry.get_job_ids(), "RQ job failed"

            def material_version(material):
                with Session(engine) as session:
                    row = session.scalar(
                        select(MaterialVersion).where(MaterialVersion.material_id == material)
                    )
                    return row.id, row.storage_key

            app = create_app(settings)
            app.dependency_overrides[get_embedder] = lambda: build_demo_embedder(settings)
            app.dependency_overrides[get_text_generator] = EvidenceGenerator
            with TestClient(app) as client:
                require_response(
                    client.post(
                        "/api/v1/auth/login",
                        json={
                            "username": settings.local_username,
                            "password": settings.local_password,
                        },
                    )
                )
                # 场景一：原文件已读入，解析尚未返回时删除。
                material, parse_job = upload(client, "parse-race")
                version, key = material_version(material)
                gate = label + ":parse"
                name = start("parse", gate)
                assert connection.blpop(gate + ":ready", timeout=40), "Parser never reached gate"
                assert (
                    require_response(client.get(f"/api/v1/jobs/{parse_job}"))["status"] == "running"
                )
                delete(client, material)
                connection.rpush(gate + ":release", "release")
                finished(name)
                assert_absent(client, engine, material, version, key, storage)
                evidence.append({"scenario": "delete_during_parse", "passed": True})

                # 场景二：第一块已入库、第二块向量请求尚未返回时删除。
                material, parse_job = upload(client, "index-race")
                version, key = material_version(material)
                finished(start("plain"))
                assert require_response(client.get(f"/api/v1/jobs/{parse_job}"))["status"] == "done"
                task, candidate = confirm_task(client, engine, material)
                history_before = require_response(
                    client.get(f"/api/v1/tasks/{task}/progress-history")
                )
                with Session(engine) as session:
                    index_job = session.scalar(
                        select(ProcessingJob.id).where(
                            ProcessingJob.version_id == version,
                            ProcessingJob.stage == "embed",
                        )
                    )
                assert index_job is not None
                app.state.job_queue.enqueue_index(index_job)
                gate = label + ":index"
                name = start("index", gate)
                assert connection.blpop(gate + ":ready", timeout=40), (
                    "Embedder never reached second block"
                )
                assert (
                    require_response(client.get(f"/api/v1/jobs/{index_job}"))["status"] == "running"
                )
                progress = require_response(client.get(f"/api/v1/jobs/{index_job}/index-progress"))
                assert progress["summary"]["indexed"] == 1
                conversation = require_response(client.post("/api/v1/qa/conversations", json={}))
                url = f"/api/v1/qa/conversations/{conversation['id']}"
                answer = require_response(
                    client.post(url + "/messages", json={"question": "Synthetic homework?"})
                )
                assert answer["citations"] and all(c["valid"] for c in answer["citations"])
                delete(client, material)
                connection.rpush(gate + ":release", "release")
                finished(name)
                assert_absent(client, engine, material, version, key, storage)
                assert client.get(f"/api/v1/candidates/{candidate}").status_code == 404
                task_after = require_response(client.get(f"/api/v1/tasks/{task}"))
                assert task_after["progress"] == "in_progress"
                assert (
                    require_response(client.get(f"/api/v1/tasks/{task}/progress-history"))
                    == history_before
                )
                source = require_response(client.get(f"/api/v1/tasks/{task}/source"))
                assert source["source_state"] == "invalidated", source
                citations = require_response(client.get(url))["turns"][0]["citations"]
                assert citations and all(
                    not c["valid"] and c["status"] == "source_deleted" for c in citations
                )
                after = require_response(
                    client.post("/api/v1/qa/query", json={"question": "Synthetic homework?"})
                )
                assert after["insufficient"] and not after["citations"]

                # 迟到的重复消息也通过真实 RQ 执行，不能重新创建已删除内容。
                late = []
                for queue, function, job_id in (
                    (QUEUE_NAME, parse_version_job, parse_job),
                    (INDEX_QUEUE_NAME, index_version_job, index_job),
                ):
                    late.append(Queue(queue, connection=connection).enqueue(function, job_id))
                finished(start("plain"))
                finished(start("index"))
                assert all(Job.fetch(job.id, connection=connection).is_finished for job in late)
                assert_absent(client, engine, material, version, key, storage)
                evidence.append(
                    {
                        "scenario": "delete_during_index",
                        "passed": True,
                        "task_progress_preserved": True,
                        "old_citations_invalid": True,
                        "late_messages_finished_without_resurrection": True,
                    }
                )
        connection.close()
        print(
            json.dumps({"cases": evidence, "queue": "real Redis/RQ", "models": "fake, zero cost"})
        )
    finally:
        # 仅清理由本次 UUID 命名的临时容器和网络，不操作既有服务或数据库。
        for name in [*reversed(workers), redis_name]:
            subprocess.run(
                ["docker", "rm", "-f", name], capture_output=True, timeout=20, check=False
            )
        subprocess.run(
            ["docker", "network", "rm", network], capture_output=True, timeout=20, check=False
        )
        engine.dispose()


if __name__ == "__main__":
    if not __debug__:
        raise SystemExit("Verification requires assertions; do not use python -O")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", choices=("plain", "parse", "index"))
    parser.add_argument("--gate", default="unused")
    parser.add_argument("--image", default="campusflow-v003-worker:local")
    args = parser.parse_args()
    if args.worker:
        worker(args.worker, args.gate)
    else:
        verify(args.image)
