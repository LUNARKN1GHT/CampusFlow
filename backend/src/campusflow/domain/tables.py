"""表格与跨页续表识别规则（D016）。

按"第 X 部分，共 Y 部分 / 续表"标记识别跨页表格分部，
同一表格的各分部归入同一组；声明的分部缺失时给出限制说明，
不强行拼出不完整的表格。每个分部保留自己的原始页码作为证据。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 表格分部标题模式："课程事项表（第 1 部分，共 2 部分）"或"（续表，第 2 部分，共 2 部分）"
_PART_PATTERN = re.compile(
    r"^(?P<title>.+?)（(?:(?:续表，)?第\s*(?P<part>\d+)\s*部分，共\s*(?P<total>\d+)\s*部分)）",
    re.MULTILINE,
)


@dataclass(frozen=True)
class TablePart:
    """一个表格分部：标题、第几部分、总部分数、所在页码。"""

    title: str
    part: int
    total: int
    page: int


def find_table_parts(text: str) -> list[TablePart]:
    """在片段文本中找出表格分部标记。page 由调用方补充。"""
    return [
        TablePart(
            title=match.group("title").strip(),
            part=int(match.group("part")),
            total=int(match.group("total")),
            page=0,
        )
        for match in _PART_PATTERN.finditer(text)
    ]


def group_key(title: str) -> str:
    """同一表格的分组键：去掉空白的标题。"""
    return re.sub(r"\s+", "", title)


def missing_parts(parts: list[TablePart]) -> list[tuple[str, int]]:
    """返回（表格分组键, 缺失的分部号）列表：声明共 N 部分但只找到少于 N 个。"""
    by_group: dict[str, list[TablePart]] = {}
    for part in parts:
        by_group.setdefault(group_key(part.title), []).append(part)
    missing: list[tuple[str, int]] = []
    for key, group in by_group.items():
        total = group[0].total
        found = {part.part for part in group}
        for expected in range(1, total + 1):
            if expected not in found:
                missing.append((key, expected))
    return missing
