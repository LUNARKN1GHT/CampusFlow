"""独立 Worker 启动入口（J002）。

启动方式（backend/ 目录）：
    uv run --locked python -m campusflow.workers.main

Worker 与 API 是不同进程，共享应用层用例；业务状态以 PostgreSQL 为准。
"""

import redis
from rq import Worker

from campusflow.application.processing import ParserRegistry, set_parser_registry
from campusflow.core.config import Settings
from campusflow.infrastructure.llm.vision import ZhipuVisionParser
from campusflow.infrastructure.parsers.pdf import PdfDocumentParser
from campusflow.infrastructure.parsers.text import TextDocumentParser
from campusflow.infrastructure.queue.rq_queue import QUEUE_NAME


def main() -> None:
    settings = Settings()
    if not settings.zhipu_api_key:
        raise SystemExit("缺少 ZHIPU_API_KEY，无法启动 Worker（视觉识别需要密钥）")
    set_parser_registry(
        ParserRegistry(
            text_parser=TextDocumentParser(),
            pdf_parser=PdfDocumentParser(),
            vision_parser=ZhipuVisionParser(settings.zhipu_api_key),
        )
    )
    connection = redis.Redis.from_url(settings.redis_url)
    Worker([QUEUE_NAME], connection=connection).work()


if __name__ == "__main__":
    main()
