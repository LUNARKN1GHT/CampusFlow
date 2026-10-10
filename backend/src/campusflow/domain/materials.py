"""资料领域规则（D001、D004）。"""

import re

# 粘贴文本上限：20 万字符，超出返回明确错误（D004 验收）
MAX_PASTED_TEXT_CHARS = 200_000

_BLANK_LINE = re.compile(r"\n\s*\n")


def next_version_no(existing: list[int]) -> int:
    """下一版本号：已有版本最大号 + 1，无版本时从 1 开始。"""
    return max(existing, default=0) + 1


def validate_pasted_text(content: str) -> str | None:
    """校验粘贴文本，合法返回 None，否则返回中文错误信息。"""
    if not content.strip():
        return "粘贴内容不能为空"
    if len(content) > MAX_PASTED_TEXT_CHARS:
        return f"粘贴内容超出上限（{MAX_PASTED_TEXT_CHARS} 字符）"
    return None


def split_paragraphs(content: str) -> list[str]:
    """把粘贴文本切分为段落列表。

    规则固定（按空行切分、去除首尾空白、丢弃空段），保证同一份文本
    重复读取时段落顺序与段落号完全一致（D004 验收：段落位置稳定）。
    """
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = [part.strip() for part in _BLANK_LINE.split(normalized)]
    return [part for part in paragraphs if part]


def deletion_confirmation_token(
    material_id: int,
    version_ids: list[int],
    chunk_count: int,
    linked_task_ids: list[int],
) -> str:
    """删除确认指纹：影响内容的稳定哈希（D008）。

    预览与执行必须基于同一份影响内容；任何一项变化（版本、片段数、
    关联任务）都会使旧确认失效，防止用过期的确认执行新的删除。
    """
    import hashlib
    import json

    canonical = json.dumps(
        {
            "material_id": material_id,
            "version_ids": sorted(version_ids),
            "chunk_count": chunk_count,
            "linked_task_ids": sorted(linked_task_ids),
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]
