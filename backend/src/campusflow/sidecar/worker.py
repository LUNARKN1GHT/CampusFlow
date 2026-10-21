"""`worker`：按部署模式委托给既有 Worker 入口，不复制作业逻辑。

- desktop：`workers.desktop` 的数据库轮询循环（不需要 Redis）；
- server：`workers.main` 的 RQ 消费循环（需要 Redis，Windows 不支持）。

两者共用 `workers.assembly` 的解析器与向量模型装配，命令只负责参数、
日志与退出码。
"""

from __future__ import annotations

import os
import threading

from campusflow.core.config import Settings
from campusflow.sidecar.errors import SidecarError, UsageError
from campusflow.sidecar.logs import EventLogger

DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_BATCH_SIZE = 10
MODES = ("auto", "desktop", "server")


def resolve_mode(settings: Settings, requested: str) -> str:
    """`auto` 跟随 CAMPUSFLOW_DEPLOYMENT_MODE；显式取值可覆盖以便本地排查。"""
    if requested not in MODES:
        raise UsageError(f"--mode 只接受 {'/'.join(MODES)}，当前为 {requested}")
    return settings.deployment_mode if requested == "auto" else requested


def _run_desktop(
    settings: Settings,
    logger: EventLogger,
    *,
    burst: bool,
    demo: bool,
    poll_interval: float,
    batch_size: int,
) -> int:
    from campusflow.workers.assembly import configure_worker
    from campusflow.workers.desktop import install_stop_handlers, run_desktop_worker

    if poll_interval < 0:
        raise UsageError("--poll-interval 不能为负数")
    if batch_size < 1:
        raise UsageError("--batch-size 至少为 1")
    try:
        configure_worker(settings, demo=demo)
    except ValueError as exc:
        raise SidecarError(f"Worker 配置无效：{exc}") from None
    stop_event = threading.Event()
    install_stop_handlers(stop_event)
    logger.info(
        "worker_starting",
        mode="desktop",
        burst=burst,
        demo=demo,
        poll_interval=poll_interval,
        batch_size=batch_size,
    )
    try:
        processed = run_desktop_worker(
            settings,
            burst=burst,
            poll_interval=poll_interval,
            batch_size=batch_size,
            stop_event=stop_event,
        )
    except KeyboardInterrupt:  # 信号处理器未安装时的兜底，不视为失败
        processed = 0
    except Exception:
        logger.error("worker_failed", mode="desktop")
        raise SidecarError("Worker 无法读取待处理作业，请运行 doctor 检查数据库") from None
    logger.info("worker_stopped", mode="desktop", processed=processed)
    return 0


def _run_server(
    settings: Settings,
    logger: EventLogger,
    *,
    burst: bool,
    demo: bool,
) -> int:
    if os.name == "nt":
        raise SidecarError("服务端 RQ Worker 需要 Linux；Windows 桌面模式请使用 --mode desktop")
    from campusflow.workers.main import run_server_worker

    logger.info("worker_starting", mode="server", burst=burst, demo=demo)
    try:
        run_server_worker(settings, burst=burst, demo=demo)
    except ValueError as exc:
        raise SidecarError(f"Worker 配置无效：{exc}") from None
    except KeyboardInterrupt:
        pass
    except Exception:
        logger.error("worker_failed", mode="server")
        raise SidecarError("Worker 无法连接 Redis 或数据库，请运行 doctor 检查服务") from None
    logger.info("worker_stopped", mode="server")
    return 0


def run_worker(
    settings: Settings,
    logger: EventLogger,
    *,
    mode: str = "auto",
    burst: bool = False,
    demo: bool = False,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    resolved = resolve_mode(settings, mode)
    if resolved == "desktop":
        return _run_desktop(
            settings,
            logger,
            burst=burst,
            demo=demo,
            poll_interval=poll_interval,
            batch_size=batch_size,
        )
    return _run_server(settings, logger, burst=burst, demo=demo)
