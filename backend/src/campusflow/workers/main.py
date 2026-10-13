"""独立 Worker 启动入口（J002）。

启动方式（backend/ 目录）：
    uv run --locked python -m campusflow.workers.main

Worker 与 API 是不同进程，共享应用层用例；业务状态以 PostgreSQL 为准。
"""

import argparse
import logging
import os

import redis
from sqlalchemy.engine import make_url

from campusflow.application.processing import ParserRegistry, set_parser_registry
from campusflow.core.config import Settings
from campusflow.infrastructure.llm.vision import FakeVisionParser, ZhipuVisionParser
from campusflow.infrastructure.parsers.pdf import PdfDocumentParser
from campusflow.infrastructure.parsers.pdf_with_vision import PdfWithVisionFallbackParser
from campusflow.infrastructure.parsers.table_aware import TableAwareParser
from campusflow.infrastructure.parsers.text import TextDocumentParser
from campusflow.infrastructure.queue.rq_queue import QUEUE_NAME
from campusflow.workers.safe_worker import SafeWorker


def build_parser_registry(settings: Settings, *, demo: bool = False) -> ParserRegistry:
    """与真实 Worker 共用装配，避免各解析器只在独立测试中生效。"""
    if demo:
        database = make_url(settings.database_url).database or ""
        if not database.endswith("_test"):
            raise ValueError("演示识别只允许使用名称以 _test 结尾的专用测试库")
        vision = FakeVisionParser()
    else:
        if not settings.zhipu_api_key:
            raise ValueError("缺少 ZHIPU_API_KEY，请在 backend/.env 配置后启动")
        vision = ZhipuVisionParser(settings.zhipu_api_key)
    return ParserRegistry(
        text_parser=TextDocumentParser(),
        pdf_parser=TableAwareParser(PdfWithVisionFallbackParser(PdfDocumentParser(), vision)),
        vision_parser=TableAwareParser(vision),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="CampusFlow 后台资料解析；Windows 请使用 Docker")
    parser.add_argument("--burst", action="store_true", help="处理完当前队列后退出")
    parser.add_argument("--demo", action="store_true", help="仅限测试库：假识别，不调用模型")
    args = parser.parse_args()
    if os.name == "nt":
        raise SystemExit(
            "此 Worker 需要 Linux；Windows 请按 docs/MATERIAL_PROCESSING.md 使用 Docker"
        )
    try:
        settings = Settings()
        set_parser_registry(build_parser_registry(settings, demo=args.demo))
        connection = redis.Redis.from_url(
            settings.redis_url, socket_connect_timeout=3, socket_timeout=5
        )
        connection.ping()
        SafeWorker([QUEUE_NAME], connection=connection, log_job_description=False).work(
            burst=args.burst
        )
    except ValueError:
        # 仅上面的受控配置校验信息；不输出连接对象或 Settings。
        raise SystemExit("Worker 配置无效，请运行诊断命令检查模型、测试库与连接设置") from None
    except Exception:
        logging.getLogger(__name__).error("worker_start_failed: 请运行诊断命令检查服务连接")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
