"""本地冒烟脚本的合成 PDF/PNG 工具。

PyMuPDF 已从运行时依赖移除（P001b），这里用 pypdf 写最小 PDF、用 Pillow 画 PNG，
只服务脚本演示，不进入产品代码。
"""

from __future__ import annotations

import io
from collections.abc import Sequence

from PIL import Image, ImageDraw
from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)

DEFAULT_WIDTH = 612
DEFAULT_HEIGHT = 792


def _writer_bytes(writer: PdfWriter) -> bytes:
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _add_text_page(writer: PdfWriter, text: str, width: int, height: int) -> None:
    page = writer.add_blank_page(width=width, height=height)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = DecodedStreamObject()
    content.set_data(
        f"BT /F1 12 Tf 72 {height - 72} Td ({escaped}) Tj ET".encode("latin-1")
    )
    page[NameObject("/Contents")] = writer._add_object(content)


def _add_image_page(writer: PdfWriter, width: int, height: int, rgb: tuple[int, int, int]) -> None:
    page = writer.add_blank_page(width=width, height=height)
    image = DecodedStreamObject()
    image.set_data(bytes(rgb) * (width * height))
    image[NameObject("/Type")] = NameObject("/XObject")
    image[NameObject("/Subtype")] = NameObject("/Image")
    image[NameObject("/Width")] = NumberObject(width)
    image[NameObject("/Height")] = NumberObject(height)
    image[NameObject("/ColorSpace")] = NameObject("/DeviceRGB")
    image[NameObject("/BitsPerComponent")] = NumberObject(8)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): writer._add_object(image)})}
    )
    content = DecodedStreamObject()
    content.set_data(f"q {width} 0 0 {height} 0 0 cm /Im0 Do Q".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(content)


def make_text_pdf(
    pages: Sequence[str],
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> bytes:
    """生成只含文本页的 PDF。"""
    writer = PdfWriter()
    for text in pages:
        _add_text_page(writer, text, width, height)
    return _writer_bytes(writer)


def make_mixed_pdf(
    text: str,
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    image_rgb: tuple[int, int, int] = (200, 200, 200),
) -> bytes:
    """生成两页 PDF：第 1 页文本，第 2 页纯图片（供扫描页回退演示）。"""
    writer = PdfWriter()
    _add_text_page(writer, text, width, height)
    _add_image_page(writer, width, height, image_rgb)
    return _writer_bytes(writer)


def make_png(text: str, width: int, height: int) -> bytes:
    """生成带一行文字的 PNG 图片。"""
    image = Image.new("RGB", (width, height), "white")
    ImageDraw.Draw(image).text((15, 40), text, fill="black")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
