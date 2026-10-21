"""PDF 页面渲染：把指定页转为 PNG 字节，供视觉识别回退使用（D015）。

只读取内存中的原文件、不写回临时文件；渲染失败或页码越界由调用方转为该页失败。
"""

from __future__ import annotations

import io

import pypdfium2 as pdfium

# PDF 坐标单位为 1/72 英寸；固定按 150 dpi 渲染。
RENDER_SCALE = 150 / 72


def render_pdf_page(content: bytes, page_no: int) -> bytes:
    """按固定 150 dpi 把第 page_no 页（从 1 开始）渲染为 PNG；失败抛 ValueError。"""
    try:
        document = pdfium.PdfDocument(content)
    except Exception as exc:
        # 损坏、空文件或非 PDF 统一转为 ValueError，调用方按单页失败处理。
        raise ValueError(f"无法打开 PDF：{exc}") from exc

    try:
        page_count = len(document)
        if not 1 <= page_no <= page_count:
            raise ValueError(f"页码越界：{page_no}（共 {page_count} 页）")
        try:
            page = document[page_no - 1]
            bitmap = page.render(scale=RENDER_SCALE)
            image = bitmap.to_pil()
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            return buffer.getvalue()
        except Exception as exc:
            raise ValueError(f"第 {page_no} 页渲染失败：{exc}") from exc
    finally:
        document.close()
