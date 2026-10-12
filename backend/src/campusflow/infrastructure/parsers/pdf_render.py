"""PDF 页面渲染：把指定页转为 PNG 字节，供视觉识别回退使用（D015）。"""

from __future__ import annotations

import pymupdf


def render_pdf_page(content: bytes, page_no: int) -> bytes:
    """渲染第 page_no 页（从 1 开始）为 PNG 字节。"""
    with pymupdf.open(stream=content, filetype="pdf") as doc:
        if not 1 <= page_no <= len(doc):
            raise ValueError(f"页码越界：{page_no}（共 {len(doc)} 页）")
        page = doc[page_no - 1]
        return page.get_pixmap(dpi=150).tobytes("png")
