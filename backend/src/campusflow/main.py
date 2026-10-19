"""API 装配入口：创建 FastAPI 应用、挂载路由与异常映射，并运行恢复扫描。

除健康检查与登录外，业务路由统一要求本机会话；启动时的补偿与回收尽力而为，
失败只记录日志，不阻塞服务启动，留待下一次扫描或重启后重试。
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager, suppress

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from campusflow.api import auth as auth_api
from campusflow.api import calendar as calendar_api
from campusflow.api import candidates as candidates_api
from campusflow.api import courses as courses_api
from campusflow.api import exports as exports_api
from campusflow.api import jobs as jobs_api
from campusflow.api import materials as materials_api
from campusflow.api import paths as paths_api
from campusflow.api import plans as plans_api
from campusflow.api import qa as qa_api
from campusflow.api import reminders as reminders_api
from campusflow.api import rules as rules_api
from campusflow.api import schedule as schedule_api
from campusflow.api import settings as settings_api
from campusflow.api import tasks as tasks_api
from campusflow.api import version_relations as version_relations_api
from campusflow.api.health import router as health_router
from campusflow.application.dispatch import dispatch_pending_jobs
from campusflow.application.jobs import (
    RecoveryResult,
    reclaim_stale_jobs,
    run_recovery_sweep,
)
from campusflow.application.ports.repositories import Repositories
from campusflow.core.config import Settings
from campusflow.domain.errors import ConflictError, DomainError, NotFoundError
from campusflow.infrastructure.calendar.factory import build_calendar_provider
from campusflow.infrastructure.calendar.tokens import TokenCipher
from campusflow.infrastructure.db.engine import create_db_engine
from campusflow.infrastructure.db.session import create_session_factory
from campusflow.infrastructure.queue.rq_queue import RQJobQueue
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.storage.local import LocalFileStorage

logger = logging.getLogger(__name__)


def compensate_pending_deliveries(app: FastAPI) -> int:
    """投递补偿扫描（J004）：重投待投递作业，返回成功投递数。

    独立入口（测试与手动调用）；启动流程统一走 run_recovery。
    尽力而为：任何异常都不影响服务启动。Redis 不可用时记录保持待投递，
    等下次启动或手动重投；没有待投递记录时也不访问 Redis。
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
        logger.warning("dispatch_failed: 待投递补偿扫描失败，保留记录等待下次重投")
        return 0


def reclaim_timed_out_jobs(app: FastAPI) -> int:
    """超时回收扫描（J005）：把长期停在"处理中"的作业标记为可重试失败。

    与投递补偿一样是独立入口，异常只记录日志，不影响服务启动；
    索引作业按心跳判断超时，且不会把资料解析状态改成失败。
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
        logger.warning("reclaim_failed: 超时回收扫描失败，作业保留处理中状态")
        return 0


@contextmanager
def _repositories(app: FastAPI) -> Iterator[Repositories]:
    """每次恢复步骤使用独立会话：一步失败回滚不会影响其他步骤。"""
    with app.state.session_factory() as session:
        yield build_repositories(session)


def run_recovery(app: FastAPI) -> RecoveryResult:
    """一次完整恢复扫描（Q004）：投递补偿 + 超时回收 + 索引自动重投。"""
    settings: Settings = app.state.settings
    result = run_recovery_sweep(
        lambda: _repositories(app),
        app.state.job_queue,
        dispatch_enabled=settings.dispatch_on_startup,
        timeout_seconds=settings.job_timeout_seconds,
        embed_timeout_seconds=settings.embed_job_timeout_seconds,
        max_auto_retries=settings.embed_job_max_auto_retries,
    )
    if result.delivered or result.reclaimed or result.auto_retried:
        logger.info(
            "恢复扫描：投递 %s 条，回收 %s 个，自动重投 %s 个",
            result.delivered,
            result.reclaimed,
            result.auto_retried,
        )
    return result


async def _recovery_loop(app: FastAPI, interval_seconds: int) -> None:
    """后台周期恢复。同步数据库操作放到线程里，不阻塞请求处理。"""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await asyncio.to_thread(run_recovery, app)
        except asyncio.CancelledError:
            # 关闭阶段必须让取消继续传播，不能当成普通失败吞掉。
            raise
        except Exception:  # 单轮失败不能让定时任务退出
            logger.warning("recovery_sweep_failed: 定期恢复扫描失败，下一轮重试")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        # 启动时先扫一次（等价于原来的启动补偿 + 超时回收）
        try:
            run_recovery(application)
        except Exception:  # 启动扫描尽力而为，绝不阻塞服务启动
            logger.warning("recovery_sweep_failed: 启动恢复扫描失败，等待下一轮")
        task: asyncio.Task[None] | None = None
        # 周期为 0 表示关闭周期扫描，只在启动时执行一次，便于测试与离线运行。
        if settings.recovery_interval_seconds > 0:
            task = asyncio.create_task(
                _recovery_loop(application, settings.recovery_interval_seconds)
            )
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

    engine = create_db_engine(settings)
    app.state.session_factory = create_session_factory(engine)
    app.state.settings = settings
    # 进程内随机会话集合：重启即全部失效，不等同于生产身份系统或持久会话。
    app.state.auth_tokens = set()
    app.state.file_storage = LocalFileStorage(settings.storage_dir)
    app.state.job_queue = RQJobQueue(settings.redis_url)
    app.state.calendar_provider = build_calendar_provider(settings)
    app.state.token_cipher = TokenCipher(settings.calendar_token_key)

    # 健康检查与登录不要求会话；其余业务路由统一挂 require_session 依赖。
    for router in (health_router, auth_api.router):
        app.include_router(router, prefix="/api/v1")
    for router in (
        courses_api.router,
        paths_api.router,
        tasks_api.router,
        schedule_api.router,
        settings_api.router,
        materials_api.router,
        jobs_api.router,
        candidates_api.router,
        rules_api.router,
        version_relations_api.router,
        qa_api.router,
        plans_api.router,
        reminders_api.router,
        exports_api.router,
        calendar_api.router,
    ):
        app.include_router(
            router,
            prefix="/api/v1",
            dependencies=[Depends(auth_api.require_session)],
        )

    # 领域异常统一映射：不存在 404、状态冲突 409、其余业务拒绝 400，
    # 只回传领域层写好的中文说明，不暴露异常类型、SQL 或外部响应细节。
    @app.exception_handler(NotFoundError)
    async def handle_not_found(request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ConflictError)
    async def handle_conflict_error(request: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(DomainError)
    async def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return app


app = create_app()
