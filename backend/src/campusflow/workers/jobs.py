"""Worker 作业函数（J002）。

RQ 消息只携带 job_id；作业函数从数据库读取详情并调用应用用例。
"""

from campusflow.application.ports.repositories import Repositories
from campusflow.application.processing import parse_version
from campusflow.core.config import Settings
from campusflow.infrastructure.db.engine import create_db_engine
from campusflow.infrastructure.db.session import create_session_factory
from campusflow.infrastructure.repositories import (
    SqlAlchemyCourseRepository,
    SqlAlchemyJobRepository,
    SqlAlchemyMaterialRepository,
    SqlAlchemyScheduleRepository,
    SqlAlchemySemesterRepository,
    SqlAlchemyTaskRepository,
    SqlAlchemyUnitOfWork,
    SqlAlchemyWorkspaceRepository,
)
from campusflow.infrastructure.storage.local import LocalFileStorage


def parse_version_job(job_id: int) -> None:
    """Worker 入口：装配依赖并执行解析作业。"""
    settings = Settings()
    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        repos = Repositories(
            workspaces=SqlAlchemyWorkspaceRepository(session),
            semesters=SqlAlchemySemesterRepository(session),
            courses=SqlAlchemyCourseRepository(session),
            tasks=SqlAlchemyTaskRepository(session),
            schedule=SqlAlchemyScheduleRepository(session),
            materials=SqlAlchemyMaterialRepository(session),
            jobs=SqlAlchemyJobRepository(session),
            uow=SqlAlchemyUnitOfWork(session),
        )
        storage = LocalFileStorage(settings.storage_dir)
        parse_version(repos, storage, job_id)
