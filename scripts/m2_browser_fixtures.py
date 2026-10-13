"""M2 浏览器验收：合成输入与真实 RQ 子进程，视觉输出使用确定性替身。

只允许显式配置的 _test 数据库和非零 Redis 库；不调用付费模型。
"""

import argparse
import json
import os
import uuid
from pathlib import Path

import pymupdf
import redis
from campusflow.application.ports.parsers import (
    ParseFailure,
    ParseOutcome,
    ParserLocator,
)
from campusflow.application.processing import ParserRegistry, set_parser_registry
from campusflow.core.config import Settings
from campusflow.infrastructure.db.models import Material, Task
from campusflow.infrastructure.llm.errors import VisionRecognitionError
from campusflow.infrastructure.llm.vision import FakeVisionParser
from campusflow.infrastructure.parsers.pdf import PdfDocumentParser
from campusflow.infrastructure.parsers.pdf_with_vision import (
    PdfWithVisionFallbackParser,
)
from campusflow.infrastructure.parsers.table_aware import TableAwareParser
from campusflow.infrastructure.parsers.text import TextDocumentParser
from campusflow.infrastructure.queue.rq_queue import QUEUE_NAME
from campusflow.workers.safe_worker import SafeWorker
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session


class FixtureVision:
    def __init__(self, mode: str):
        self.mode = mode

    def parse(self, content: bytes, mime_type: str) -> ParseOutcome:
        if self.mode == "failed":
            raise VisionRecognitionError("合成验收：视觉服务暂时不可用")
        result = FakeVisionParser().parse(content, mime_type)
        if self.mode == "partial":
            return ParseOutcome(
                result.fragments,
                [
                    ParseFailure(
                        ParserLocator(kind="region", bbox=(0.1, 0.5, 0.9, 0.8)),
                        "合成验收：下半区暂时未识别",
                        retryable=True,
                    )
                ],
            )
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["fixtures", "worker", "link-task"])
    parser.add_argument("--material-id", type=int)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--mode", choices=["done", "partial", "failed"], default="done")
    args = parser.parse_args()
    if args.action == "fixtures":
        if args.directory is None:
            parser.error("fixtures 需要 --directory")
        args.directory.mkdir(parents=True, exist_ok=True)
        with pymupdf.open() as image:
            page = image.new_page(width=300, height=200)
            page.insert_text((25, 40), "M2 synthetic image " + uuid.uuid4().hex[:8])
            png = page.get_pixmap().tobytes("png")
            (args.directory / "m2-image.png").write_bytes(png)
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((72, 72), "M2 text page one")
            pdf.new_page().insert_text((72, 72), "M2 text page two")
            (args.directory / "m2-text.pdf").write_bytes(pdf.tobytes())
        return
    if not os.environ.get("CAMPUSFLOW_DATABASE_URL") or not os.environ.get(
        "CAMPUSFLOW_REDIS_URL"
    ):
        raise SystemExit("必须显式配置隔离测试环境")
    settings = Settings(_env_file=None)
    if not (make_url(settings.database_url).database or "").endswith("_test"):
        raise SystemExit("只允许 _test 数据库")
    redis_db = make_url(settings.redis_url).database or ""
    if not redis_db.isdigit() or int(redis_db) == 0:
        raise SystemExit("只允许非零的独立 Redis 库")
    if args.action == "link-task":
        with Session(create_engine(settings.database_url)) as session:
            material = session.get(Material, args.material_id)
            if material is None or not material.title.startswith("M2 text "):
                raise SystemExit("只允许关联本次 M2 合成资料")
            task = Task(
                workspace_id=material.workspace_id,
                source_material_id=material.id,
                title="M2 synthetic linked task " + uuid.uuid4().hex[:8],
            )
            session.add(task)
            session.commit()
            print(json.dumps({"id": task.id, "title": task.title}))
        return
    vision = FixtureVision(args.mode)
    set_parser_registry(
        ParserRegistry(
            text_parser=TextDocumentParser(),
            pdf_parser=TableAwareParser(
                PdfWithVisionFallbackParser(PdfDocumentParser(), vision)
            ),
            vision_parser=TableAwareParser(vision),
        )
    )
    with redis.Redis.from_url(settings.redis_url) as connection:
        SafeWorker([QUEUE_NAME], connection=connection, log_job_description=False).work(
            burst=True
        )


if __name__ == "__main__":
    main()
