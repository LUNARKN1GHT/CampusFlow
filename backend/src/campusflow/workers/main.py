"""独立 Worker 启动入口（J002）。

启动方式（backend/ 目录）：
    uv run --locked python -m campusflow.workers.main

Worker 与 API 是不同进程，共享应用层用例；业务状态以 PostgreSQL 为准。
"""

import argparse
import logging
import os

import redis

from campusflow.core.config import Settings
from campusflow.infrastructure.queue.rq_queue import INDEX_QUEUE_NAME, QUEUE_NAME
from campusflow.workers.assembly import build_parser_registry, configure_worker
from campusflow.workers.safe_worker import SafeWorker

# 保留旧导入路径（测试与外部脚本从 workers.main 取注册表构造函数）。
__all__ = ["build_parser_registry", "main"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CampusFlow 后台解析与向量索引；Windows 请使用 Docker"
    )
    parser.add_argument("--burst", action="store_true", help="处理完当前队列后退出")
    parser.add_argument(
        "--demo", action="store_true", help="仅限测试库：假识别与假向量，不调用模型"
    )
    args = parser.parse_args()
    if os.name == "nt":
        raise SystemExit(
            "此 Worker 需要 Linux；Windows 请按 docs/MATERIAL_PROCESSING.md 使用 Docker"
        )
    try:
        settings = Settings()
        # 启动顺序：先注入模型与解析器装配，再连通 Redis，最后开始消费；
        # 解析器或连接配置不成立时直接退出，不进入消费循环。
        configure_worker(settings, demo=args.demo)
        connection = redis.Redis.from_url(
            settings.redis_url, socket_connect_timeout=3, socket_timeout=5
        )
        connection.ping()
        # 同时消费解析与索引队列，按列表顺序取任务；burst 模式清空当前队列后退出。
        SafeWorker(
            [QUEUE_NAME, INDEX_QUEUE_NAME], connection=connection, log_job_description=False
        ).work(burst=args.burst)
    except ValueError:
        # 仅上面的受控配置校验信息；不输出连接对象或 Settings。
        raise SystemExit("Worker 配置无效，请运行诊断命令检查模型、测试库与连接设置") from None
    except Exception:
        logging.getLogger(__name__).error("worker_start_failed: 请运行诊断命令检查服务连接")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
