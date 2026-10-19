"""Worker 作业函数（J002、Q004）。

RQ 消息只携带 job_id；作业函数从数据库读取详情并调用应用用例。
"""

from campusflow.application.indexing import index_version
from campusflow.application.processing import parse_version
from campusflow.core.config import Settings
from campusflow.infrastructure.db.engine import create_db_engine
from campusflow.infrastructure.db.session import create_session_factory
from campusflow.infrastructure.embedding_profiles import SqlEmbeddingProfiles
from campusflow.infrastructure.index_manifests import SqlIndexManifests
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.storage.local import LocalFileStorage
from campusflow.workers.embeddings import make_embedder


def parse_version_job(job_id: int) -> None:
    """Worker 入口：装配依赖并执行解析作业。"""
    settings = Settings()
    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        repos = build_repositories(session)
        storage = LocalFileStorage(settings.storage_dir)
        parse_version(repos, storage, job_id)


def index_version_job(job_id: int) -> None:
    """Worker 入口：装配依赖并执行向量索引作业（Q004）。"""
    settings = Settings()
    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        repos = build_repositories(session)
        manifests = SqlIndexManifests(session)
        profiles = SqlEmbeddingProfiles(session)
        embedder = make_embedder(settings)
        index_version(repos, manifests, profiles, embedder, job_id)
