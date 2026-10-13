"""跨页表格关联解析器（D016，包装任意 DocumentParser）。

在内层解析结果上标注表格分组：同一跨页表格的各分部归入同一 table_group，
各分部保留原始页码（证据不丢）；声明分部缺失时给出带范围的限制说明。
"""

from __future__ import annotations

from campusflow.application.ports.parsers import (
    DocumentParser,
    ParsedFragment,
    ParseFailure,
    ParseOutcome,
    ParserLocator,
)
from campusflow.domain.tables import find_table_parts, group_key, missing_parts


class TableAwareParser:
    def __init__(self, inner: DocumentParser) -> None:
        self._inner = inner

    def parse(self, content: bytes, mime_type: str) -> ParseOutcome:
        outcome = self._inner.parse(content, mime_type)

        # 找出所有分部并记录所在片段
        fragment_groups: dict[int, str] = {}  # 片段 seq → 分组键
        all_parts = []
        for fragment in outcome.fragments:
            parts = find_table_parts(fragment.text)
            for part in parts:
                part = type(part)(
                    title=part.title,
                    part=part.part,
                    total=part.total,
                    page=fragment.locator.page or 0,
                )
                all_parts.append(part)
                fragment_groups[fragment.seq] = group_key(part.title)

        fragments = [
            ParsedFragment(
                seq=fragment.seq,
                locator=fragment.locator,
                text=fragment.text,
                confidence=fragment.confidence,
                table_group=fragment_groups.get(fragment.seq),
            )
            for fragment in outcome.fragments
        ]

        failures = list(outcome.failures)
        for key, part_no in missing_parts(all_parts):
            failures.append(
                ParseFailure(
                    locator=ParserLocator(kind="page", page=0),
                    reason=f"跨页表格「{key}」第 {part_no} 部分缺失，无法可靠重建完整表格",
                )
            )
        return ParseOutcome(fragments=fragments, failures=failures)
