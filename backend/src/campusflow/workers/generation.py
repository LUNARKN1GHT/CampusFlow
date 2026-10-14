"""文字生成装配入口：不随 Worker 启动自动调用，不影响现有解析链。"""

from campusflow.application.ports.generation import TextGenerator
from campusflow.core.config import Settings
from campusflow.infrastructure.llm.text import ZhipuTextGenerator


def build_text_generator(settings: Settings) -> TextGenerator:
    if not settings.zhipu_api_key:
        raise ValueError("缺少 ZHIPU_API_KEY，不能调用真实文字模型")
    return ZhipuTextGenerator(
        settings.zhipu_api_key,
        model=settings.text_model,
        timeout_seconds=settings.text_timeout_seconds,
        max_tokens=settings.text_max_tokens,
    )
