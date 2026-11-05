"""提取 Worker 的文字模型装配；演示模式绝不调用真实服务。"""

from sqlalchemy.engine import make_url

from campusflow.core.config import Settings
from campusflow.domain.errors import PermanentError
from campusflow.infrastructure.llm.text import FakeTextGenerator
from campusflow.workers.generation import build_text_generator

_DEMO = False


def set_extraction_demo(enabled: bool) -> None:
    global _DEMO
    _DEMO = enabled


def make_extraction_generator(settings: Settings):
    if _DEMO:
        if not (make_url(settings.database_url).database or "").endswith("_test"):
            raise ValueError("演示提取只允许专用测试库")
        return FakeTextGenerator('{"items": []}')

    class Generator:
        def generate(self, request):
            try:
                generator = build_text_generator(settings)
            except ValueError:
                raise PermanentError("文字模型配置不可用") from None
            return generator.generate(request)

    return Generator()
