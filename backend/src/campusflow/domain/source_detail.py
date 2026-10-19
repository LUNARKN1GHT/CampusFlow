"""R007：任务与日程的来源详情规则；区分手动/提取来源并判断引用是否仍可验证。"""

SOURCE_TYPE_EXTRACTED = "extracted"
SOURCE_TYPE_MANUAL = "manual"

OBJECT_TYPE_TASK = "task"
OBJECT_TYPE_EVENT = "fixed_event"


def source_type(
    *,
    candidate_id: int | None,
    material_id: int | None,
    invalidated_at: object | None,
) -> str:
    """区分手动事项与提取事项；来源已失效但留过痕仍算提取过。

    只按来源关联本身判定，不看字段内容，避免把用户手填的字段误当成提取来源。
    """
    if candidate_id is not None or material_id is not None or invalidated_at is not None:
        return SOURCE_TYPE_EXTRACTED
    return SOURCE_TYPE_MANUAL


def citation_is_verifiable(chunk_text: str | None, quote: str) -> bool:
    """引用可验证的前提是原文片段仍然存在，且摘录仍能在其中逐字找到。"""
    return chunk_text is not None and quote in chunk_text
