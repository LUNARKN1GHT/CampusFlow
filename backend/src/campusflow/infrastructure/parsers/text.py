"""粘贴文本文档解析器（D011 协议实现）。

复用 domain/materials.split_paragraphs 的固定切分规则，输出段落定位片段。
"""

from campusflow.application.ports.parsers import (
    ParsedFragment,
    ParseOutcome,
    ParserLocator,
)
from campusflow.domain.materials import split_paragraphs

TEXT_MIME_TYPES = frozenset({"text/plain", "text/markdown"})


class TextDocumentParser:
    def parse(self, content: bytes, mime_type: str) -> ParseOutcome:
        if mime_type not in TEXT_MIME_TYPES:
            raise ValueError(f"文本解析器不支持的类型：{mime_type}")
        paragraphs = split_paragraphs(content.decode("utf-8"))
        return ParseOutcome(
            fragments=[
                ParsedFragment(
                    seq=index,
                    locator=ParserLocator(kind="paragraph", paragraph=index + 1),
                    text=paragraph,
                )
                for index, paragraph in enumerate(paragraphs)
            ],
            failures=[],
        )
