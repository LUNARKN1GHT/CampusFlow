"""Q004 测试辅助：确定性假向量器与合成索引资料。

假向量器不联网、不花钱；同一段文字永远得到同一个非零向量，
需要注入失败时按调用顺序消费预设异常。
"""

import hashlib
from collections.abc import Callable, Sequence

from conftest import TEST_DATABASE_URL
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from campusflow.application.indexing import index_version
from campusflow.application.ports.repositories import ProcessingJobData, Repositories
from campusflow.domain.embeddings import EmbeddedText, EmbeddingProfile
from campusflow.domain.states import JobStage
from campusflow.infrastructure.db.models import Material, MaterialVersion, SourceChunk
from campusflow.infrastructure.embedding_profiles import SqlEmbeddingProfiles
from campusflow.infrastructure.index_manifests import SqlIndexManifests
from campusflow.infrastructure.repositories import build_repositories

DEFAULT_TEXTS = ("第一段合成内容。", "第二段合成内容。")


def open_session() -> Session:
    return Session(create_engine(TEST_DATABASE_URL))


class FakeEmbedder:
    """确定性假向量器；可按调用顺序注入异常，并可挂接运行中变更钩子。"""

    def __init__(self, profile: EmbeddingProfile | None = None) -> None:
        self._profile = profile or EmbeddingProfile()
        self.calls: list[str] = []
        self.failures: list[BaseException | None] = []
        self.on_embed: Callable[[str, int], None] | None = None

    @property
    def profile(self) -> EmbeddingProfile:
        return self._profile

    def fail_next(self, error: BaseException) -> None:
        self.failures.append(error)

    def embed(self, text: str) -> EmbeddedText:
        self.calls.append(text)
        if self.on_embed is not None:
            self.on_embed(text, len(self.calls))
        if self.failures:
            error = self.failures.pop(0)
            if error is not None:
                raise error
        self._profile.validate_text(text)
        digest = hashlib.sha256(text.encode()).digest()
        values = tuple(digest[index % len(digest)] + 1 for index in range(self._profile.dimensions))
        return EmbeddedText(self._profile.id, values, self._profile.model)


def workspace_id(client) -> int:
    return client.get("/api/v1/settings").json()["workspace_id"]


def coverage_for(texts: Sequence[str], *, mode: str = "complete") -> dict | None:
    """按模式构造解析覆盖元数据；unknown 返回 None（快照里不写该键）。"""
    if mode == "unknown":
        return None
    failures = []
    if mode == "partial":
        failures = [
            {
                "locator_type": "page",
                "locator_value": "2",
                "reason": "第 2 页未识别",
                "retryable": False,
            }
        ]
    return {"fragment_count": len(texts), "failures": failures}


def create_source(
    client,
    *,
    texts: Sequence[str] = DEFAULT_TEXTS,
    coverage: str = "complete",
    archived: bool = False,
) -> tuple[int, int]:
    """合成一份已解析资料：返回 (material_id, version_id)。"""
    workspace = workspace_id(client)
    with open_session() as session, session.begin():
        material = Material(
            workspace_id=workspace,
            title="合成索引资料",
            source_type="text",
            archived=archived,
        )
        session.add(material)
        session.flush()
        metadata: dict = {}
        raw = coverage_for(texts, mode=coverage)
        if raw is not None:
            metadata["coverage"] = raw
        version = MaterialVersion(material_id=material.id, version_no=1, source_metadata=metadata)
        session.add(version)
        session.flush()
        for seq, text in enumerate(texts):
            session.add(
                SourceChunk(
                    version_id=version.id,
                    seq=seq,
                    text=text,
                    locator_type="paragraph",
                    locator_value=str(seq + 1),
                    paragraph=seq + 1,
                )
            )
        return material.id, version.id


def create_embed_job(client, *, texts: Sequence[str] = DEFAULT_TEXTS, coverage: str = "complete"):
    """合成资料 + 待处理索引作业；返回 (job_id, material_id, version_id)。"""
    material_id, version_id = create_source(client, texts=texts, coverage=coverage)
    with open_session() as session, session.begin():
        repos = build_repositories(session)
        job = repos.jobs.create(workspace_id(client), material_id, version_id, JobStage.EMBED)
        return job.id, material_id, version_id


def get_job(job_id: int) -> ProcessingJobData | None:
    with open_session() as session:
        return build_repositories(session).jobs.get(job_id)


def run_index(job_id: int, embedder: FakeEmbedder, **kwargs):
    """在独立会话中执行一次索引用例（与 Worker 装配一致）。"""
    with open_session() as session:
        repos: Repositories = build_repositories(session)
        return index_version(
            repos,
            SqlIndexManifests(session),
            SqlEmbeddingProfiles(session),
            embedder,
            job_id,
            **kwargs,
        )


def block_rows(job_id: int) -> list[dict]:
    """读取作业当前清单下每个块的直接状态，便于断言。"""
    from sqlalchemy import select

    from campusflow.infrastructure.db.models import IndexedBlock, ProcessingJob

    with open_session() as session:
        job = session.get(ProcessingJob, job_id)
        if job is None or job.manifest_id is None:
            return []
        from campusflow.infrastructure.db.models import IndexManifest

        manifest = session.get(IndexManifest, job.manifest_id)
        rows = session.scalars(
            select(IndexedBlock)
            .where(
                IndexedBlock.profile_id == manifest.profile_id,
                IndexedBlock.version_id == manifest.version_id,
            )
            .order_by(IndexedBlock.source_chunk_id, IndexedBlock.start_offset)
        )
        return [
            {
                "block_id": row.block_id,
                "state": row.state,
                "attempts": row.attempts,
                "retryable": row.retryable,
                "failure_code": row.failure_code,
                "has_embedding": row.embedding is not None,
            }
            for row in rows
        ]
