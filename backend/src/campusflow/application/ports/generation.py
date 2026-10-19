"""X002：文字生成端口，只交换文本，不暴露供应商 SDK 或执行工具。"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class GenerationRequest:
    """一次文字生成的全部输入；不携带密钥、会话状态或工具定义。"""

    system_prompt: str
    source_json: str


class TextGenerator(Protocol):
    def generate(self, request: GenerationRequest) -> str:
        """返回模型正文；调用失败抛领域错误，不能用空结果冒充成功。

        截断、工具调用或结构不完整的响应按 ModelOutputError 拒绝；适配器
        不自动重试或切换供应方，由上层按固定文案处理失败。
        """
        ...
