"""Worker 共享装配（P005）：解析器注册表与模型工厂。

服务端 RQ Worker 与桌面轮询 Worker 共用同一套装配，避免两种入口出现
解析器或向量模型不一致。本模块不导入 Redis/RQ，桌面模式可直接使用。
"""

from sqlalchemy.engine import make_url

from campusflow.application.processing import ParserRegistry, set_parser_registry
from campusflow.core.config import Settings
from campusflow.infrastructure.llm.vision import FakeVisionParser, ZhipuVisionParser
from campusflow.infrastructure.parsers.pdf import PdfDocumentParser
from campusflow.infrastructure.parsers.pdf_with_vision import PdfWithVisionFallbackParser
from campusflow.infrastructure.parsers.table_aware import TableAwareParser
from campusflow.infrastructure.parsers.text import TextDocumentParser
from campusflow.infrastructure.parsers.web import WebDocumentParser
from campusflow.infrastructure.parsers.word import WordDocumentParser
from campusflow.workers.embeddings import build_demo_embedder, build_embedder, set_embedder_factory


def build_parser_registry(settings: Settings, *, demo: bool = False) -> ParserRegistry:
    """与真实 Worker 共用装配，避免各解析器只在独立测试中生效。

    演示模式只允许测试库并使用假视觉解析；真实模式缺密钥直接拒绝启动，
    不会在没有模型配置时悄悄退回假识别。
    """
    if demo:
        require_test_database(settings)
        vision = FakeVisionParser()
    else:
        if not settings.zhipu_api_key:
            raise ValueError("缺少 ZHIPU_API_KEY，请在 backend/.env 配置后启动")
        vision = ZhipuVisionParser(
            settings.zhipu_api_key,
            timeout_seconds=settings.vision_timeout_seconds,
            max_retries=settings.vision_max_retries,
            backoff_seconds=settings.vision_retry_backoff_seconds,
        )
    return ParserRegistry(
        text_parser=TextDocumentParser(),
        pdf_parser=TableAwareParser(PdfWithVisionFallbackParser(PdfDocumentParser(), vision)),
        vision_parser=TableAwareParser(vision),
        word_parser=WordDocumentParser(),
        web_parser=WebDocumentParser(),
    )


def require_test_database(settings: Settings) -> None:
    """演示识别会写入假结果，只允许名称以 _test 结尾的专用测试库。"""
    database = make_url(settings.database_url).database or ""
    if not database.endswith("_test"):
        raise ValueError("演示识别只允许使用名称以 _test 结尾的专用测试库")


def configure_worker(settings: Settings, *, demo: bool) -> None:
    """按演示/真实模式注入解析器与向量模型工厂。

    真实模式缺密钥时在这里直接失败，不进入消费或轮询循环。
    """
    if demo:
        require_test_database(settings)
        set_embedder_factory(build_demo_embedder)
    else:
        set_embedder_factory(build_embedder)
    set_parser_registry(build_parser_registry(settings, demo=demo))
