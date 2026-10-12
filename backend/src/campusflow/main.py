import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from campusflow.api import auth as auth_api
from campusflow.api import courses as courses_api
from campusflow.api import jobs as jobs_api
from campusflow.api import materials as materials_api
from campusflow.api import schedule as schedule_api
from campusflow.api import settings as settings_api
from campusflow.api import tasks as tasks_api
from campusflow.api.health import router as health_router
from campusflow.application.dispatch import dispatch_pending_jobs
from campusflow.application.jobs import reclaim_stale_jobs
from campusflow.core.config import Settings
from campusflow.domain.errors import DomainError, NotFoundError
from campusflow.infrastructure.db.engine import create_db_engine
from campusflow.infrastructure.db.session import create_session_factory
from campusflow.infrastructure.queue.rq_queue import RQJobQueue
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.storage.local import LocalFileStorage

logger = logging.getLogger(__name__)


def compensate_pending_deliveries(app: FastAPI) -> int:
    """投递补偿扫描（J004）：重投待投递作业，返回成功投递数。

    尽力而为：任何异常都不影响服务启动。Redis 不可用时记录保持待投递，
    等下次启动或手动重投；没有待投递记录时不访问 Redis。
    """
    settings: Settings = app.state.settings
    if not settings.dispatch_on_startup:
        return 0
    try:
        with app.state.session_factory() as session:
            outcome = dispatch_pending_jobs(build_repositories(session), app.state.job_queue)
        if outcome.delivered or outcome.failed:
            logger.info("待投递补偿：成功 %s 条，失败 %s 条", outcome.delivered, outcome.failed)
        return outcome.delivered
    except Exception:  # 补偿失败绝不阻塞启动
        logger.warning("待投递补偿扫描失败，保留记录等待下次重投", exc_info=True)
        return 0


def reclaim_timed_out_jobs(app: FastAPI) -> int:
    """超时回收扫描（J005）：把长期停在"处理中"的作业标记为可重试失败。

    与投递补偿一样尽力而为：异常只记录日志，不影响服务启动。
    """
    settings: Settings = app.state.settings
    try:
        with app.state.session_factory() as session:
            reclaimed = reclaim_stale_jobs(
                build_repositories(session), timeout_seconds=settings.job_timeout_seconds
            )
        if reclaimed:
            logger.info("超时回收：%s 个作业已标记为可重试失败", len(reclaimed))
        return len(reclaimed)
    except Exception:  # 回收失败不能阻塞启动，作业下次启动再回收
        logger.warning("超时回收扫描失败，作业保留处理中状态", exc_info=True)
        return 0


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        compensate_pending_deliveries(application)
        reclaim_timed_out_jobs(application)
        yield

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

    engine = create_db_engine(settings)
    app.state.session_factory = create_session_factory(engine)
    app.state.settings = settings
    app.state.auth_tokens = set()
    app.state.file_storage = LocalFileStorage(settings.storage_dir)
    app.state.job_queue = RQJobQueue(settings.redis_url)

    for router in (health_router, auth_api.router):
        app.include_router(router, prefix="/api/v1")
    for router in (
        courses_api.router,
        tasks_api.router,
        schedule_api.router,
        settings_api.router,
        materials_api.router,
        jobs_api.router,
    ):
        app.include_router(
            router,
            prefix="/api/v1",
            dependencies=[Depends(auth_api.require_session)],
        )

    @app.exception_handler(NotFoundError)
    async def handle_not_found(request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(DomainError)
    async def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return app


app = create_app()
