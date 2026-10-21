"""桌面模式数据库轮询 Worker（P005）。

桌面部署不安装 Redis/RQ：Worker 直接轮询 processing_jobs 中待处理的解析与
索引作业，复用 `workers.jobs` 的作业函数和数据库幂等领取（claim）。重复投递
由 claim 兜底；崩溃或重启留下的"处理中"作业由既有恢复扫描回收后重跑。
Windows 原生可运行，不需要 Docker、fork 或 Linux 专用队列。

启动方式（backend/ 目录）：
    uv run --locked python -m campusflow.workers.desktop
    uv run --locked python -m campusflow.workers.desktop --burst --demo
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session, sessionmaker

from campusflow.application.jobs import RecoveryResult, run_recovery_sweep
from campusflow.application.ports.repositories import Repositories
from campusflow.core.config import Settings
from campusflow.domain.states import JobStage
from campusflow.infrastructure.db.engine import create_db_engine
from campusflow.infrastructure.db.session import create_session_factory
from campusflow.infrastructure.queue.db_queue import DBJobQueue
from campusflow.infrastructure.repositories import build_repositories
from campusflow.workers.assembly import configure_worker
from campusflow.workers.jobs import index_version_job, parse_version_job

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_BATCH_SIZE = 10

# 作业执行器只接收阶段与作业编号；默认实现复用 RQ Worker 同一套作业函数。
JobRunner = Callable[[str, int], None]


def run_claimed_job(stage: str, job_id: int) -> None:
    """按阶段执行一个作业；领取与状态写回由作业函数自己完成。"""
    if stage == JobStage.PARSE:
        parse_version_job(job_id)
    elif stage == JobStage.EMBED:
        index_version_job(job_id)
    else:
        # 查询已限定阶段；出现其它值说明调用方传错，不尝试执行未知入口。
        raise ValueError("作业阶段没有对应的处理入口")


@contextmanager
def _open_repositories(
    session_factory: sessionmaker[Session],
) -> Iterator[Repositories]:
    """每次恢复步骤使用独立会话，一步失败回滚不影响其它步骤。"""
    with session_factory() as session:
        yield build_repositories(session)


def recover_stale_jobs(
    settings: Settings, session_factory: sessionmaker[Session]
) -> RecoveryResult:
    """启动时执行一次既有恢复扫描（P005 重启恢复）。

    补投未投递作业、回收超过阈值的处理中作业，并按额度自动重投索引作业。
    解析作业被回收后保持"可重试失败"，由用户或运维显式重试，不静默重跑。
    """
    return run_recovery_sweep(
        lambda: _open_repositories(session_factory),
        DBJobQueue(),
        dispatch_enabled=settings.dispatch_on_startup,
        timeout_seconds=settings.job_timeout_seconds,
        embed_timeout_seconds=settings.embed_job_timeout_seconds,
        max_auto_retries=settings.embed_job_max_auto_retries,
    )


def _next_batch(
    session_factory: sessionmaker[Session], *, batch_size: int
) -> list[tuple[str, int]]:
    """读取一批候选作业的阶段与编号；只为仍存在的作业返回条目。"""
    with session_factory() as session:
        repos = build_repositories(session)
        ids = repos.jobs.list_claimable_ids(limit=batch_size)
        jobs = [repos.jobs.get(job_id) for job_id in ids]
    return [(job.stage, job.id) for job in jobs if job is not None]


def drain_once(
    session_factory: sessionmaker[Session],
    job_runner: JobRunner,
    *,
    batch_size: int,
    stop_event: threading.Event,
) -> int:
    """处理到没有新的可领取作业为止，返回本次执行的作业数。

    本轮已尝试过的作业会被跳过：即使作业函数因未预期异常没有写回终态，
    也不会在同一次轮询里空转。单个作业失败只记录日志，不终止轮询。
    """
    attempted: set[int] = set()
    processed = 0
    while not stop_event.is_set():
        batch = _next_batch(session_factory, batch_size=batch_size)
        fresh = [(stage, job_id) for stage, job_id in batch if job_id not in attempted]
        if not fresh:
            break
        for stage, job_id in fresh:
            if stop_event.is_set():
                break
            attempted.add(job_id)
            try:
                job_runner(stage, job_id)
            except Exception:  # 作业函数已写回失败状态，单个失败不能终止 Worker
                logger.warning("desktop_job_failed: job_id=%s error=processing_failed", job_id)
            processed += 1
    return processed


def run_desktop_worker(
    settings: Settings,
    *,
    burst: bool = False,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    stop_event: threading.Event | None = None,
    job_runner: JobRunner | None = None,
    recover_on_start: bool = True,
) -> int:
    """运行轮询循环，返回本次执行的作业数。

    burst 模式处理完当前可领取作业后退出；常驻模式在空闲时等待
    `poll_interval` 秒，收到停止信号后完成当前作业再退出。数据库暂时不可用
    时只记录日志并等待下一轮，便于桌面首次启动时等待迁移完成。
    """
    if poll_interval < 0:
        raise ValueError("poll_interval 不能为负数")
    if batch_size < 1:
        raise ValueError("batch_size 至少为 1")
    stop = stop_event if stop_event is not None else threading.Event()
    runner = job_runner if job_runner is not None else run_claimed_job
    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)
    processed = 0
    try:
        if recover_on_start:
            try:
                result = recover_stale_jobs(settings, session_factory)
                if result.delivered or result.reclaimed or result.auto_retried:
                    logger.info(
                        "desktop_recovery: 投递 %s 条，回收 %s 个，自动重投 %s 个",
                        result.delivered,
                        result.reclaimed,
                        result.auto_retried,
                    )
            except Exception:  # 恢复失败不阻塞轮询；下一轮仍可直接领取 pending 作业
                logger.warning("desktop_recovery_failed: 启动恢复扫描失败，继续轮询")

        while not stop.is_set():
            try:
                processed += drain_once(
                    session_factory,
                    runner,
                    batch_size=batch_size,
                    stop_event=stop,
                )
            except Exception:
                if burst:
                    raise
                logger.warning("desktop_poll_failed: 无法读取待处理作业，等待下一轮")
            if burst:
                break
            stop.wait(poll_interval)
    finally:
        engine.dispose()
    return processed


def install_stop_handlers(stop_event: threading.Event) -> None:
    """把 SIGINT/SIGTERM 转成停止标记；Windows 与 Unix 都适用。"""

    def _request_stop(signum: int, _frame) -> None:
        logger.info("desktop_worker_stopping: signal=%s", signum)
        stop_event.set()

    for name in ("SIGINT", "SIGTERM"):
        handler = getattr(signal, name, None)
        if handler is None:
            continue
        try:
            signal.signal(handler, _request_stop)
        except (OSError, ValueError):  # 非主线程或平台不支持时保持默认行为
            continue


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="CampusFlow 桌面模式作业 Worker：只轮询 PostgreSQL，不依赖 Redis"
    )
    parser.add_argument("--burst", action="store_true", help="处理完当前可领取作业后退出")
    parser.add_argument(
        "--demo", action="store_true", help="仅限测试库：假识别与假向量，不调用模型"
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        help=f"空闲轮询间隔秒数（默认 {DEFAULT_POLL_INTERVAL_SECONDS}）",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"每批读取的作业数（默认 {DEFAULT_BATCH_SIZE}）",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        settings = Settings()
        configure_worker(settings, demo=args.demo)
    except ValueError:
        raise SystemExit("桌面 Worker 配置无效，请检查数据库与模型配置") from None

    stop_event = threading.Event()
    install_stop_handlers(stop_event)
    try:
        processed = run_desktop_worker(
            settings,
            burst=args.burst,
            poll_interval=args.poll_interval,
            batch_size=args.batch_size,
            stop_event=stop_event,
        )
    except KeyboardInterrupt:  # 信号处理器未安装时的兜底
        processed = 0
    except Exception:
        logger.error("desktop_worker_failed: 请检查数据库连接与迁移状态")
        raise SystemExit(1) from None
    logger.info("desktop_worker_stopped: processed=%s", processed)


if __name__ == "__main__":
    main()
