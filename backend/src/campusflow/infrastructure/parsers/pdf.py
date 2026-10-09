"""PDF 文本页解析器（D012，D011 协议实现）。

逐页提取文字并保留页码；空白页与"无文字层的扫描页"区分开：
后者标记为需要 OCR 回退（D015），前者仅为空白页。
"""

from __future__ import annotations

import io
from typing import Any

from pypdf import PdfReader

from campusflow.application.ports.parsers import (
    ParsedFragment,
    ParseFailure,
    ParseOutcome,
    ParserLocator,
)

PDF_MIME_TYPE = "application/pdf"


def _classify_empty_page(page: Any) -> str:
    """区分空白页与扫描页：页面含图片视为扫描件，否则视为空白页。"""
    try:
        images = page.images
    except Exception:
        images = []
    if images:
        return "扫描页无文字层，需要图像识别回退"
    return "空白页"


class PdfDocumentParser:
    """pypdf 文本层解析。扫描页不伪造文字，进入部分失败列表。"""

    def parse(self, content: bytes, mime_type: str) -> ParseOutcome:
        if mime_type != PDF_MIME_TYPE:
            raise ValueError(f"PDF 解析器不支持的类型：{mime_type}")
        try:
            reader = PdfReader(io.BytesIO(content))
        except Exception as exc:
            raise ValueError(f"无法解析 PDF 文件：{exc}") from exc
        if reader.is_encrypted:
            raise ValueError("PDF 已加密，无法解析")

        fragments: list[ParsedFragment] = []
        failures: list[ParseFailure] = []
        seq = 0
        for index, page in enumerate(reader.pages):
            page_no = index + 1  # 页码从 1 开始
            text = (page.extract_text() or "").strip()
            if not text:
                failures.append(
                    ParseFailure(
                        locator=ParserLocator(kind="page", page=page_no),
                        reason=_classify_empty_page(page),
                    )
                )
                continue
            fragments.append(
                ParsedFragment(
                    seq=seq,
                    locator=ParserLocator(kind="page", page=page_no),
                    text=text,
                )
            )
            seq += 1

        if not fragments and not failures:
            raise ValueError("PDF 不含任何页面")
        return ParseOutcome(fragments=fragments, failures=failures)
