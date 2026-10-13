"""N003：DOCX 正文按文档顺序解析，段落和表格单元定位不冒充页码。"""

import io
import zipfile

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from campusflow.application.ports.parsers import (
    ParsedFragment,
    ParseFailure,
    ParseOutcome,
    ParserLocator,
)
from campusflow.domain.errors import PermanentError
from campusflow.domain.word import DOCX_MIME, validate_docx


class WordDocumentParser:
    def parse(self, content: bytes, mime_type: str) -> ParseOutcome:
        if mime_type != DOCX_MIME:
            raise PermanentError("Word 解析器仅支持普通 .docx")
        error = validate_docx(content)
        if error:
            raise PermanentError(error)
        try:
            document = Document(io.BytesIO(content))
        except Exception:
            raise PermanentError("Word 文档关系损坏，请重新另存为 .docx") from None
        fragments: list[ParsedFragment] = []
        failures: list[ParseFailure] = []
        table_no = 0

        def paragraph(block: Paragraph, locator: ParserLocator, group: str | None = None):
            if block.text.strip():
                fragments.append(
                    ParsedFragment(len(fragments), locator, block.text.strip(), table_group=group)
                )
            if block._p.xpath(".//w:drawing | .//w:pict | .//w:object"):
                failures.append(
                    ParseFailure(locator, "该段含图片、文本框或嵌入对象，未识别其中内容")
                )

        def walk(container, depth=0, cell_location=None):
            nonlocal table_no
            if depth > 20:
                failures.append(
                    ParseFailure(ParserLocator("document"), "表格嵌套过深，未识别更深内容")
                )
                return
            paragraph_no = 0
            for block in container.iter_inner_content():
                if isinstance(block, Paragraph):
                    paragraph_no += 1
                    if cell_location is None:
                        locator = ParserLocator("paragraph", paragraph=paragraph_no)
                        group = None
                    else:
                        table, row, column = cell_location
                        locator = ParserLocator(
                            "table_cell",
                            paragraph=paragraph_no,
                            table=table,
                            row=row,
                            column=column,
                        )
                        group = f"word-table-{table}"
                    paragraph(block, locator, group)
                elif isinstance(block, Table):
                    table_no += 1
                    current_table = table_no
                    seen_cells = set()
                    for row_no, row in enumerate(block.rows, 1):
                        for column_no, cell in enumerate(row.cells, row.grid_cols_before + 1):
                            # 合并单元格可能占多个位置；只引用其首次出现的左上位置。
                            if cell._tc in seen_cells:
                                continue
                            seen_cells.add(cell._tc)
                            walk(cell, depth + 1, (current_table, row_no, column_no))

        walk(document)
        if document.element.body.xpath(".//w:ins | .//w:del | .//w:sdt | .//w:altChunk"):
            failures.append(
                ParseFailure(
                    ParserLocator("document"), "文档含修订、内容控件或外部内容，相关内容未完整解析"
                )
            )
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            extras = [
                name
                for name in archive.namelist()
                if name.startswith(
                    ("word/header", "word/footer", "word/footnotes", "word/endnotes")
                )
            ]
        if extras:
            failures.append(
                ParseFailure(
                    ParserLocator("document"), "页眉、页脚、脚注或尾注不在本次正文解析范围"
                )
            )
        if not fragments and not failures:
            failures.append(
                ParseFailure(ParserLocator("document"), "文档没有可提取的正文或表格文字")
            )
        return ParseOutcome(fragments, failures)
