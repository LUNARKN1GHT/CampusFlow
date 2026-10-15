"""X008：同一适用范围内同一字段在多个来源取值不一致时，并列保留全部取值。

两份通知对同一门课的截止时间给出不同日期、且都没有声明"本通知作废前一份"时，
按版本新旧或导入时间挑一个值是错的：被丢掉的那个取值仍然可能是学生需要遵守的
要求，而且用户再也看不到它。这里只判断"取值是否不一致"，不判断"哪个对"。
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass

from campusflow.domain.candidates import CandidateKind, JsonValue, ReviewReason

SOURCE_CONFLICT = "source_conflict"

MAX_DISPLAY_VALUE = 120
MAX_LISTED_ENTRIES = 12
MAX_REASON_LENGTH = 1000


@dataclass(frozen=True)
class ConflictEntry:
    """一个来源（候选）为某个字段提供的取值。"""

    candidate_id: int
    origin_key: str
    kind: CandidateKind
    scope_key: tuple[int | None, int | None, str | None]
    field_name: str
    value: JsonValue


@dataclass(frozen=True)
class FieldConflict:
    """同一候选类型、同一适用范围内，同一字段出现多个不一致的取值。"""

    field_name: str
    entries: tuple[ConflictEntry, ...]


def _canonical(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _display(value: JsonValue) -> str:
    text = value if isinstance(value, str) else _canonical(value)
    if len(text) > MAX_DISPLAY_VALUE:
        text = text[:MAX_DISPLAY_VALUE] + "…"
    return text


def field_conflicts(entries: Sequence[ConflictEntry]) -> tuple[FieldConflict, ...]:
    """按（候选类型、适用范围、字段名）分组，两个及以上非空且不一致的取值即为冲突。

    缺失（null）不计入：缺失已由 missing_* 原因表达，不构成第二个取值。
    全部分组都一致时返回空，调用方据此清掉过期的冲突原因。
    """
    groups: dict[tuple[object, ...], list[ConflictEntry]] = {}
    for entry in entries:
        key = (entry.kind, entry.scope_key, entry.field_name)
        groups.setdefault(key, []).append(entry)
    conflicts = []
    for (_, _, field_name), group in groups.items():
        values = [_canonical(e.value) for e in group if e.value is not None]
        if len(values) >= 2 and len(set(values)) >= 2:
            conflicts.append(FieldConflict(str(field_name), tuple(group)))
    return tuple(conflicts)


def conflict_reason(conflict: FieldConflict) -> ReviewReason:
    """一条冲突原因同时列出全部取值与来源，挂在每个涉事候选上，不挑"正确"的那个。"""
    shown = conflict.entries[:MAX_LISTED_ENTRIES]
    detail = "；".join(f"{entry.origin_key} = {_display(entry.value)}" for entry in shown)
    if len(conflict.entries) > len(shown):
        detail += f"；…（共 {len(conflict.entries)} 个来源）"
    message = (
        f"字段 {conflict.field_name} 在不同来源中取值不一致：{detail}。"
        "没有作废或更正声明，因此不按版本新旧择一，全部取值与来源并列保留，请核对后再确认。"
    )
    return ReviewReason(SOURCE_CONFLICT, message[:MAX_REASON_LENGTH], conflict.field_name)
