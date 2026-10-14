"""X002：文字生成端口，只交换文本，不暴露供应商 SDK 或执行工具。"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class GenerationRequest:
    system_prompt: str
    source_json: str


class TextGenerator(Protocol):
    def generate(self, request: GenerationRequest) -> str:
        """返回模型正文；调用失败抛领域错误，不能用空结果冒充成功。"""
        ...
