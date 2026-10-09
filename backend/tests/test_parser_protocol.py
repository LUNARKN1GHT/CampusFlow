"""D011 测试：可替换解析器输出协议。

验收：PDF、文本、图片适配器均可遵守同一协议；
位置与原文版本绑定且错误有范围（部分失败按页/区域列出）。
"""

import pytest

from campusflow.application.materials import fragments_to_chunks
from campusflow.application.ports.parsers import (
    ParsedFragment,
    ParseFailure,
    ParseOutcome,
    ParserLocator,
)
from campusflow.infrastructure.parsers.text import TextDocumentParser


class FakePdfParser:
    """模拟 PDF 适配器：逐页解析，第 3 页识别失败。"""

    def parse(self, content: bytes, mime_type: str) -> ParseOutcome:
        return ParseOutcome(
            fragments=[
                ParsedFragment(
                    seq=0, locator=ParserLocator(kind="page", page=1), text="第 1 页内容"
                ),
                ParsedFragment(
                    seq=1, locator=ParserLocator(kind="page", page=2), text="第 2 页内容"
                ),
            ],
            failures=[
                ParseFailure(locator=ParserLocator(kind="page", page=3), reason="扫描页无文字层")
            ],
        )


class FakeImageParser:
    """模拟图片识别适配器：输出区域定位与置信度。"""

    def parse(self, content: bytes, mime_type: str) -> ParseOutcome:
        return ParseOutcome(
            fragments=[
                ParsedFragment(
                    seq=0,
                    locator=ParserLocator(kind="region", bbox=(0.1, 0.2, 0.9, 0.4)),
                    text="作业三下周交",
                    confidence=0.82,
                )
            ],
            failures=[],
        )


def test_protocol_covers_pdf_text_image_adapters() -> None:
    """三种适配器输出同一协议结构（D011 验收）。"""
    for parser, mime in (
        (FakePdfParser(), "application/pdf"),
        (TextDocumentParser(), "text/plain"),
        (FakeImageParser(), "image/png"),
    ):
        outcome = parser.parse(b"content", mime)
        assert isinstance(outcome, ParseOutcome)
        for fragment in outcome.fragments:
            locator_type, locator_value = fragment.locator.as_chunk_locator()
            assert locator_type in {"page", "paragraph", "region"}
            assert locator_value


def test_text_parser_produces_stable_paragraph_locators() -> None:
    parser = TextDocumentParser()
    outcome = parser.parse("第一段\n\n第二段".encode(), "text/plain")
    assert [f.locator.paragraph for f in outcome.fragments] == [1, 2]
    assert [f.text for f in outcome.fragments] == ["第一段", "第二段"]


def test_pdf_partial_failure_has_scope() -> None:
    """部分失败必须带范围信息（页码），不能只说"失败"（D011 验收）。"""
    outcome = FakePdfParser().parse(b"x", "application/pdf")
    assert outcome.is_partial
    failure = outcome.failures[0]
    assert failure.locator.page == 3
    assert failure.reason


def test_image_region_locator_and_confidence() -> None:
    outcome = FakeImageParser().parse(b"x", "image/png")
    fragment = outcome.fragments[0]
    assert fragment.confidence == 0.82
    locator_type, locator_value = fragment.locator.as_chunk_locator()
    assert locator_type == "region"
    assert locator_value == "0.1000,0.2000,0.9000,0.4000"


def test_fragments_bind_to_version_via_mapping() -> None:
    """位置与原文版本绑定：映射后的片段携带 version_id（D011 验收）。"""
    outcome = FakePdfParser().parse(b"x", "application/pdf")
    chunks = fragments_to_chunks(outcome, version_id=42)
    assert all(c.version_id == 42 for c in chunks)
    assert [(c.locator_type, c.locator_value) for c in chunks] == [("page", "1"), ("page", "2")]


def test_locator_missing_fields_rejected() -> None:
    with pytest.raises(ValueError):
        ParserLocator(kind="paragraph").as_chunk_locator()
    with pytest.raises(ValueError):
        ParserLocator(kind="page").as_chunk_locator()
    with pytest.raises(ValueError):
        ParserLocator(kind="region").as_chunk_locator()


def test_text_parser_rejects_unsupported_mime() -> None:
    with pytest.raises(ValueError):
        TextDocumentParser().parse(b"x", "application/pdf")
