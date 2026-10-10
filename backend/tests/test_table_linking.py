"""D016 集成测试：表格和跨页续表的来源关联。

验收：跨页作业要求不会丢掉原页证据；无法可靠重建表格时显示限制。
使用 B010 跨页表格样本（表头在第 2、3 页重复，行跨页续表）。
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from campusflow.application.ports.parsers import ParsedFragment, ParseOutcome, ParserLocator
from campusflow.domain.tables import find_table_parts, group_key, missing_parts
from campusflow.infrastructure.parsers.pdf import PdfDocumentParser
from campusflow.infrastructure.parsers.table_aware import TableAwareParser

SAMPLE_PDF = (
    Path(__file__).resolve().parents[2] / "output" / "pdf" / "b010-text-and-cross-page-table.pdf"
)


def test_cross_page_table_grouped_with_page_evidence() -> None:
    """跨页表格的两个分部归入同一组，且各保留原页码（D016 验收）。"""
    outcome = TableAwareParser(PdfDocumentParser()).parse(
        SAMPLE_PDF.read_bytes(), "application/pdf"
    )
    table_fragments = [f for f in outcome.fragments if f.table_group]
    assert len(table_fragments) == 2
    assert {f.table_group for f in table_fragments} == {"课程事项表"}
    # 第 1 部分在第 2 页，续表在第 3 页，页码证据不丢
    assert sorted(f.locator.page for f in table_fragments) == [2, 3]


def test_incomplete_table_reports_limitation() -> None:
    """声明共 2 部分但只找到 1 部分：显示无法可靠重建（D016 验收）。"""

    class OnlyPartOne:
        def parse(self, content: bytes, mime_type: str) -> ParseOutcome:
            return ParseOutcome(
                fragments=[
                    ParsedFragment(
                        seq=0,
                        locator=ParserLocator(kind="page", page=1),
                        text="课程事项表（第 1 部分，共 2 部分）\n行号 周次 课程",
                    )
                ],
                failures=[],
            )

    outcome = TableAwareParser(OnlyPartOne()).parse(b"x", "application/pdf")
    assert any("无法可靠重建" in f.reason and "第 2 部分" in f.reason for f in outcome.failures)


def test_find_table_parts_and_grouping() -> None:
    text = "课程事项表（第 1 部分，共 2 部分）\n内容"
    parts = find_table_parts(text)
    assert len(parts) == 1
    assert parts[0].title == "课程事项表"
    assert parts[0].part == 1 and parts[0].total == 2
    assert group_key("课程 事项表") == group_key("课程事项表")
    assert missing_parts(parts) == [("课程事项表", 2)]


def test_non_table_fragments_have_no_group() -> None:
    outcome = TableAwareParser(PdfDocumentParser()).parse(
        SAMPLE_PDF.read_bytes(), "application/pdf"
    )
    page1 = [f for f in outcome.fragments if f.locator.page == 1]
    assert page1[0].table_group is None  # 第 1 页不是表格分部


@pytest.mark.integration
def test_table_group_persisted_to_chunks(client: TestClient) -> None:
    """table_group 落库到片段并随导入响应可见。"""
    response = client.post(
        "/api/v1/materials/text",
        json={"title": "表格文本", "content": "课程事项表（第 1 部分，共 2 部分）\n\n普通段落"},
    )
    assert response.status_code == 201
    chunks = response.json()["chunks"]
    assert all("table_group" in c for c in chunks)
