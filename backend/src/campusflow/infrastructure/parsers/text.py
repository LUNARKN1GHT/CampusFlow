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
    """纯文本/Markdown 解析；段落号从 1 开始，与 split_paragraphs 的固定规则一致。"""

    def parse(self, content: bytes, mime_type: str) -> ParseOutcome:
        """按空行切分段落并输出 paragraph 定位；不做编码猜测，非 UTF-8 直接失败。"""

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
