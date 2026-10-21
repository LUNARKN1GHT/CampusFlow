"""`serve`：以 uvicorn 运行 API；默认只绑定回环地址，便于桌面内置进程使用。

退出码来自 uvicorn 的优雅关闭：收到 SIGINT/SIGTERM 后完成在途请求再退出。
这里不额外安装信号处理器，避免与 uvicorn 自己的关闭流程冲突。
"""

from __future__ import annotations

import signal

from campusflow.core.config import Settings
from campusflow.sidecar.errors import SidecarError, UsageError
from campusflow.sidecar.logs import EventLogger

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def validate_bind(host: str, port: int) -> None:
    """端口与主机的使用错误在启动前就以退出码 2 报出，不进入 uvicorn。"""
    if not 1 <= port <= 65535:
        raise UsageError(f"端口必须在 1–65535 之间，当前为 {port}")
    if not host.strip():
        raise UsageError("监听地址不能为空")


def _swallow_after_shutdown(signum: int, _frame) -> None:
    """uvicorn 优雅关闭后会重新抛出同一个信号，这里让它以退出码 0 结束。"""
    return None


def _install_shutdown_handlers() -> None:
    """在 uvicorn 接管信号之前安装空处理器，保留"优雅关闭＝成功"的约定。"""
    for name in ("SIGINT", "SIGTERM"):
        handler = getattr(signal, name, None)
        if handler is None:
            continue
        try:
            signal.signal(handler, _swallow_after_shutdown)
        except (OSError, ValueError):  # 非主线程或平台不支持时保持默认行为
            continue


def run_serve(settings: Settings, logger: EventLogger, *, host: str, port: int) -> int:
    """阻塞运行 API，直到收到退出信号；启动失败返回 1。"""
    validate_bind(host, port)
    # uvicorn 与 app 工厂按需导入：migrate／backup 等命令不加载 Web 依赖。
    import uvicorn

    from campusflow.main import create_app

    app = create_app(settings)
    # log_config=None 保留调用方的日志配置，不让 uvicorn 重置根 logger。
    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_config=None,
        access_log=False,
    )
    server = uvicorn.Server(config)
    logger.info(
        "serve_starting",
        host=host,
        port=port,
        deployment_mode=settings.deployment_mode,
    )
    _install_shutdown_handlers()
    try:
        server.run()
    except OSError as exc:
        raise SidecarError(f"无法监听 {host}:{port}：地址被占用或没有权限") from exc
    finally:
        logger.info("serve_stopped", started=server.started)
    if not server.started:
        raise SidecarError("服务未能启动，请先运行 doctor 检查数据库与目录")
    return 0
