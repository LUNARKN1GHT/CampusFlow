"""文字生成装配入口：不随 Worker 启动自动调用，不影响现有解析链。

只在显式生成请求时构造适配器；未知供应方或缺密钥直接失败，不静默降级，
也不在导入本模块时读取密钥或发起网络请求。
"""

from campusflow.application.ports.generation import TextGenerator
from campusflow.core.config import Settings
from campusflow.infrastructure.llm.text import DeepSeekTextGenerator, ZhipuTextGenerator


def build_text_generator(settings: Settings) -> TextGenerator:
    """按 CAMPUSFLOW_TEXT_PROVIDER 选择适配器，缺少对应密钥时拒绝装配。"""
    provider = settings.text_provider
    if provider == "deepseek":
        if not settings.deepseek_api_key:
            raise ValueError("缺少 DEEPSEEK_API_KEY，不能调用真实文字模型")
        return DeepSeekTextGenerator(
            settings.deepseek_api_key,
            model=settings.text_model,
            timeout_seconds=settings.text_timeout_seconds,
            max_tokens=settings.text_max_tokens,
        )
    if provider != "zhipu":
        raise ValueError(f"未知的文字模型供应方：{provider}")
    if not settings.zhipu_api_key:
        raise ValueError("缺少 ZHIPU_API_KEY，不能调用真实文字模型")
    return ZhipuTextGenerator(
        settings.zhipu_api_key,
        model=settings.text_model,
        timeout_seconds=settings.text_timeout_seconds,
        max_tokens=settings.text_max_tokens,
    )
