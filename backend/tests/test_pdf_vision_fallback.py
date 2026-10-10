"""D015 集成测试：扫描 PDF 逐页识别回退。

验收：文本页优先使用文本解析；单页失败不抹掉其他成功页面。
用 PyMuPDF 构造含文字页与纯图片页的合成 PDF；视觉识别用假适配器。
"""

import io

import pymupdf
import pytest

from campusflow.application.ports.parsers import (
    ParsedFragment,
    ParseOutcome,
    ParserLocator,
)
from campusflow.infrastructure.llm.errors import VisionRecognitionError
from campusflow.infrastructure.llm.vision import FakeVisionParser
from campusflow.infrastructure.parsers.pdf import PdfDocumentParser
from campusflow.infrastructure.parsers.pdf_with_vision import PdfWithVisionFallbackParser


def _make_mixed_pdf() -> bytes:
    """构造两页 PDF：第 1 页有文字层，第 2 页只有图片（扫描页）。"""
    doc = pymupdf.open()
    page1 = doc.new_page()
    page1.insert_text((72, 72), "Page one has a text layer")
    page2 = doc.new_page()
    # 用 PyMuPDF 渲染一张纯色 PNG，避免测试依赖 Pillow
    img_doc = pymupdf.open()
    img_page = img_doc.new_page(width=200, height=100)
    img_page.draw_rect(pymupdf.Rect(0, 0, 200, 100), fill=(1, 1, 1))
    png_bytes = img_page.get_pixmap().tobytes("png")
    img_doc.close()
    page2.insert_image(pymupdf.Rect(50, 50, 250, 150), stream=png_bytes)
    output = io.BytesIO()
    doc.save(output)
    return output.getvalue()


def test_text_pages_use_text_parser_first() -> None:
    """文本页走文本解析，不调用视觉识别（D015 验收）。"""
    outcome = PdfWithVisionFallbackParser(PdfDocumentParser(), FakeVisionParser()).parse(
        _make_mixed_pdf(), "application/pdf"
    )

    page1 = [f for f in outcome.fragments if f.locator.page == 1]
    assert len(page1) == 1
    assert "text layer" in page1[0].text
    assert page1[0].confidence is None  # 文本层解析不来自视觉模型


def test_scanned_page_falls_back_to_vision_with_page_binding() -> None:
    """扫描页回退到视觉识别，结果绑定实际页码。"""
    vision = FakeVisionParser(
        ParseOutcome(
            fragments=[
                ParsedFragment(
                    seq=0,
                    locator=ParserLocator(kind="region", bbox=(0.1, 0.1, 0.9, 0.3)),
                    text="扫描页上的文字",
                    confidence=0.8,
                )
            ],
            failures=[],
        )
    )
    outcome = PdfWithVisionFallbackParser(PdfDocumentParser(), vision).parse(
        _make_mixed_pdf(), "application/pdf"
    )

    fallback = [f for f in outcome.fragments if f.text == "扫描页上的文字"]
    assert len(fallback) == 1
    assert fallback[0].locator.page == 2
    assert fallback[0].locator.bbox == (0.1, 0.1, 0.9, 0.3)
    assert fallback[0].confidence == 0.8
    # 文本页仍在
    assert any(f.locator.page == 1 for f in outcome.fragments)


def test_single_page_vision_failure_does_not_wipe_others() -> None:
    """单页识别失败只影响该页，其他页结果保留（D015 验收）。"""

    class FailingVision:
        def parse(self, content: bytes, mime_type: str) -> ParseOutcome:
            raise VisionRecognitionError("接口返回错误：HTTP 429")

    outcome = PdfWithVisionFallbackParser(PdfDocumentParser(), FailingVision()).parse(
        _make_mixed_pdf(), "application/pdf"
    )

    # 第 1 页文本结果保留
    assert any(f.locator.page == 1 and "text layer" in f.text for f in outcome.fragments)
    # 第 2 页失败带页码范围
    page2_failures = [f for f in outcome.failures if f.locator.page == 2]
    assert len(page2_failures) == 1
    assert "识别失败" in page2_failures[0].reason


def test_blank_page_stays_as_blank_failure() -> None:
    """空白页不走视觉回退，保持空白页失败项。"""
    import pypdf

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)

    outcome = PdfWithVisionFallbackParser(PdfDocumentParser(), FakeVisionParser()).parse(
        buffer.getvalue(), "application/pdf"
    )
    assert outcome.fragments == []
    assert outcome.failures[0].reason == "空白页"


def test_render_pdf_page_produces_png() -> None:
    from campusflow.infrastructure.parsers.pdf_render import render_pdf_page

    image = render_pdf_page(_make_mixed_pdf(), 2)
    assert image.startswith(b"\x89PNG")
    with pytest.raises(ValueError, match="越界"):
        render_pdf_page(_make_mixed_pdf(), 99)
