"""D012 集成测试：pypdf 文本页解析。

验收：多页样本的引用能回到对应页；空白页与无文本扫描页区分。
使用仓库内合成样本 output/pdf/b010-text-and-cross-page-table.pdf（B010）。
"""

from pathlib import Path

import pytest
from pypdf import PdfWriter

from campusflow.infrastructure.parsers.pdf import PdfDocumentParser, _classify_empty_page

SAMPLE_PDF = (
    Path(__file__).resolve().parents[2] / "output" / "pdf" / "b010-text-and-cross-page-table.pdf"
)


@pytest.fixture(scope="module")
def sample_pdf_bytes() -> bytes:
    assert SAMPLE_PDF.exists(), f"样本不存在：{SAMPLE_PDF}"
    return SAMPLE_PDF.read_bytes()


def test_multipage_sample_citations_return_to_pages(sample_pdf_bytes: bytes) -> None:
    """三页样本逐页解析，每页内容可回到对应页码（D012 验收）。"""
    outcome = PdfDocumentParser().parse(sample_pdf_bytes, "application/pdf")

    assert [f.locator.page for f in outcome.fragments] == [1, 2, 3]
    assert outcome.failures == []
    page1 = outcome.fragments[0].text
    assert "作业三" in page1
    page2 = outcome.fragments[1].text
    assert "实验" in page2 or "截止" in page2  # 跨页表格起始页


def test_fragments_have_stable_seq_and_page_locators(sample_pdf_bytes: bytes) -> None:
    outcome = PdfDocumentParser().parse(sample_pdf_bytes, "application/pdf")
    assert [f.seq for f in outcome.fragments] == list(range(len(outcome.fragments)))
    for fragment in outcome.fragments:
        locator_type, locator_value = fragment.locator.as_chunk_locator()
        assert locator_type == "page"
        assert locator_value == str(fragment.locator.page)


def test_blank_page_and_scanned_page_are_distinguished() -> None:
    """空白页与扫描页给出不同原因（D012 验收）。"""

    class FakeBlankPage:
        images = []

        def extract_text(self) -> str:
            return ""

    class FakeScannedPage:
        images = ["img1.png"]  # 有图片但无文字层

        def extract_text(self) -> str:
            return ""

    assert _classify_empty_page(FakeBlankPage()) == "空白页"
    assert "扫描页" in _classify_empty_page(FakeScannedPage())


def test_blank_page_pdf_reports_failure_with_page_scope() -> None:
    """纯空白页 PDF：进入部分失败并带页码范围，不产生伪造片段。"""
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = __import__("io").BytesIO()
    writer.write(buffer)

    outcome = PdfDocumentParser().parse(buffer.getvalue(), "application/pdf")
    assert outcome.fragments == []
    assert len(outcome.failures) == 1
    assert outcome.failures[0].locator.page == 1
    assert outcome.failures[0].reason == "空白页"


def test_invalid_pdf_and_wrong_mime_rejected() -> None:
    parser = PdfDocumentParser()
    with pytest.raises(ValueError, match="PDF"):
        parser.parse(b"not a pdf", "application/pdf")
    with pytest.raises(ValueError, match="不支持"):
        parser.parse(b"%PDF-1.4 fake", "image/png")
