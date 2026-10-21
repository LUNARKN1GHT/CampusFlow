"""D015 集成测试：扫描 PDF 逐页识别回退。

验收：文本页优先使用文本解析；单页失败不抹掉其他成功页面。
用 pypdf 构造含文字页与纯图片页的合成 PDF；视觉识别用假适配器。
"""

import io

import pytest
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)

from campusflow.application.ports.parsers import (
    ParsedFragment,
    ParseFailure,
    ParseOutcome,
    ParserLocator,
)
from campusflow.infrastructure.llm.errors import (
    VisionInvalidOutput,
    VisionRecognitionError,
    VisionRequestRejected,
)
from campusflow.infrastructure.llm.vision import FakeVisionParser
from campusflow.infrastructure.parsers.pdf import PdfDocumentParser
from campusflow.infrastructure.parsers.pdf_render import render_pdf_page
from campusflow.infrastructure.parsers.pdf_with_vision import PdfWithVisionFallbackParser

PAGE_WIDTH = 144  # 2 英寸；150 dpi 下恰好 300 像素
PAGE_HEIGHT = 72  # 1 英寸；150 dpi 下恰好 150 像素


def _add_text_page(writer: PdfWriter) -> None:
    page = writer.add_blank_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
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
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 12 Tf 10 40 Td (Page one has a text layer) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)


def _add_image_page(writer: PdfWriter) -> None:
    page = writer.add_blank_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    image = DecodedStreamObject()
    image.set_data(bytes([200, 200, 200]) * (PAGE_WIDTH * PAGE_HEIGHT))
    image[NameObject("/Type")] = NameObject("/XObject")
    image[NameObject("/Subtype")] = NameObject("/Image")
    image[NameObject("/Width")] = NumberObject(PAGE_WIDTH)
    image[NameObject("/Height")] = NumberObject(PAGE_HEIGHT)
    image[NameObject("/ColorSpace")] = NameObject("/DeviceRGB")
    image[NameObject("/BitsPerComponent")] = NumberObject(8)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): writer._add_object(image)})}
    )
    content = DecodedStreamObject()
    content.set_data(f"q {PAGE_WIDTH} 0 0 {PAGE_HEIGHT} 0 0 cm /Im0 Do Q".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(content)


def _make_mixed_pdf() -> bytes:
    """构造两页 PDF：第 1 页有文字层，第 2 页只有图片（扫描页）。"""
    writer = PdfWriter()
    _add_text_page(writer)
    _add_image_page(writer)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _make_blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _png_size(content: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(content)) as image:
        return image.size


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


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (VisionRecognitionError("接口返回错误：HTTP 429"), True),
        (VisionRequestRejected("接口返回错误：HTTP 401"), False),
        (VisionInvalidOutput("识别结果不是合法 JSON"), False),
    ],
    ids=["transient", "rejected", "invalid-output"],
)
def test_vision_failure_retryability_is_preserved(error, retryable) -> None:
    """Q009：单页失败的临时/永久语义随异常类型保留。"""

    class FailingVision:
        def parse(self, content: bytes, mime_type: str) -> ParseOutcome:
            raise error

    outcome = PdfWithVisionFallbackParser(PdfDocumentParser(), FailingVision()).parse(
        _make_mixed_pdf(), "application/pdf"
    )

    page2_failures = [f for f in outcome.failures if f.locator.page == 2]
    assert len(page2_failures) == 1
    assert page2_failures[0].retryable is retryable
    # 第 1 页文本结果不受影响
    assert any(f.locator.page == 1 and "text layer" in f.text for f in outcome.fragments)


def test_blank_page_stays_as_blank_failure() -> None:
    """空白页不走视觉回退，保持空白页失败项。"""
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)

    outcome = PdfWithVisionFallbackParser(PdfDocumentParser(), FakeVisionParser()).parse(
        buffer.getvalue(), "application/pdf"
    )
    assert outcome.fragments == []
    assert outcome.failures[0].reason == "空白页"


def test_render_pdf_page_produces_png() -> None:
    image = render_pdf_page(_make_mixed_pdf(), 2)
    assert image.startswith(b"\x89PNG")
    with pytest.raises(ValueError, match="越界"):
        render_pdf_page(_make_mixed_pdf(), 99)


def test_render_pdf_page_uses_150_dpi() -> None:
    """144×72 pt 的页面在 150 dpi 下应为 300×150 像素。"""
    assert _png_size(render_pdf_page(_make_blank_pdf(), 1)) == (300, 150)
    assert _png_size(render_pdf_page(_make_mixed_pdf(), 1)) == (300, 150)


def test_render_scanned_image_page() -> None:
    """纯图片页可渲染，且页面内容不是全白。"""
    image_bytes = render_pdf_page(_make_mixed_pdf(), 2)
    assert _png_size(image_bytes) == (300, 150)
    with Image.open(io.BytesIO(image_bytes)) as image:
        extrema = image.convert("RGB").getextrema()
    # 图片区域是 200/255 的灰色，说明不是只渲染了白底
    assert any(high < 255 for _, high in extrema)


@pytest.mark.parametrize("content", [b"", b"not a pdf"], ids=["empty", "corrupt"])
def test_render_corrupted_pdf_raises_value_error(content: bytes) -> None:
    with pytest.raises(ValueError, match="无法打开 PDF"):
        render_pdf_page(content, 1)


def test_fallback_handles_corrupted_render_error() -> None:
    """渲染损坏 PDF 抛出的 ValueError 由回退解析器按单页失败处理。"""

    class ScanOnlyParser:
        def parse(self, content: bytes, mime_type: str) -> ParseOutcome:
            return ParseOutcome(
                fragments=[],
                failures=[
                    ParseFailure(
                        locator=ParserLocator(kind="page", page=1),
                        reason="扫描页无文字层",
                    )
                ],
            )

    outcome = PdfWithVisionFallbackParser(ScanOnlyParser(), FakeVisionParser()).parse(
        b"not a pdf", "application/pdf"
    )
    assert len(outcome.failures) == 1
    assert "扫描页识别失败：无法打开 PDF" in outcome.failures[0].reason
