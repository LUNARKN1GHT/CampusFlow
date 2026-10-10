"""可替换解析器输出协议（D011）。

统一 PDF、文本、图片适配器的输出表示：片段携带定位与置信信息，
部分失败按页面/区域逐项列出。位置信息绑定到具体原文版本——
适配器只产出定位，持久化时由应用层关联版本（见 fragments_to_chunks）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

LocatorKind = Literal["page", "paragraph", "region"]


@dataclass(frozen=True)
class ParserLocator:
    """片段定位：段落号、页码或图片区域（bbox 为 0-1 归一化坐标）。"""

    kind: LocatorKind
    paragraph: int | None = None  # 段落号，从 1 开始
    page: int | None = None  # 页码，从 1 开始
    bbox: tuple[float, float, float, float] | None = None  # (x0, y0, x1, y1) 归一化

    def as_chunk_locator(self) -> tuple[str, str]:
        """转换为 source_chunks 的 (locator_type, locator_value)。"""
        if self.kind == "paragraph":
            if self.paragraph is None:
                raise ValueError("段落定位缺少段落号")
            return "paragraph", str(self.paragraph)
        if self.kind == "page":
            if self.page is None:
                raise ValueError("页码定位缺少页码")
            return "page", str(self.page)
        if self.bbox is None:
            raise ValueError("区域定位缺少坐标")
        return "region", ",".join(f"{v:.4f}" for v in self.bbox)


@dataclass(frozen=True)
class ParsedFragment:
    """解析出的一个片段：文本 + 定位 + 置信度（None 表示解析器不提供）。
    table_group 标识跨页表格分组（D016），同一表格各分部共享同一组键。"""

    seq: int
    locator: ParserLocator
    text: str
    confidence: float | None = None
    table_group: str | None = None


@dataclass(frozen=True)
class ParseFailure:
    """部分失败：某一页/某个区域无法识别，附原因。错误必须有范围（D011 验收）。"""

    locator: ParserLocator
    reason: str


@dataclass(frozen=True)
class ParseOutcome:
    """解析结果：片段列表 + 部分失败列表。两者可同时非空（部分成功）。"""

    fragments: list[ParsedFragment]
    failures: list[ParseFailure]

    @property
    def is_partial(self) -> bool:
        return bool(self.fragments) and bool(self.failures)


class DocumentParser(Protocol):
    """文档解析适配器接口。PDF（pypdf）、粘贴文本、图片 OCR 均实现本协议。"""

    def parse(self, content: bytes, mime_type: str) -> ParseOutcome:
        """解析文件内容为片段序列；无法识别的页/区域进入 failures 而不是静默丢弃。"""
        ...
