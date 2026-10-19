"""资料版本、来源失效与粘贴文本切分的领域规则（D001、D004、J007、D009）。纯函数。"""

import re

# 粘贴文本上限：20 万字符，超出返回明确错误（D004 验收）
MAX_PASTED_TEXT_CHARS = 200_000

# 结果不再写回的原因（J007）：晚到的作业既不能复活已删除的资料，
# 也不能把旧版本的结果写进新版本
DELETED_MATERIAL_REASON = "资料已删除，本作业结果不再写回"
SUPERSEDED_VERSION_REASON = "资料已有更新的版本，本作业结果不再写回"

# 任务来源的三种状态（D009）。资料删除后任务必须留在"来源已失效"这一档，
# 而不是与从未有过来源的普通任务混在一起：用户在任务页要能看出这条引用
# 已经不可用，不能让它悄悄变成"看起来一直就是手工建的"。
SOURCE_STATE_AVAILABLE = "available"
SOURCE_STATE_INVALIDATED = "invalidated"
SOURCE_STATE_NONE = "none"

# 失效说明长度上限，与 tasks.source_invalidated_note 列宽一致
MAX_SOURCE_NOTE_LENGTH = 300

# 空行（可含空白）作为段落边界；单个换行仍属于同一段，保留段内换行。
_BLANK_LINE = re.compile(r"\n\s*\n")


def source_state(source_material_id: int | None, invalidated_at: object | None) -> str:
    """任务来源的当前状态（D009）。

    还有来源资料 → available；来源资料已删除并留过痕 → invalidated；
    两者都没有 → none（手工创建的任务，本来就没有来源）。
    """
    if source_material_id is not None:
        return SOURCE_STATE_AVAILABLE
    if invalidated_at is not None:
        return SOURCE_STATE_INVALIDATED
    return SOURCE_STATE_NONE


def invalidated_source_note(material_title: str) -> str:
    """来源资料被删除时写进任务的失效说明（D009）。

    带上原资料标题，用户才知道这条引用原来指向什么；标题过长时截断，
    说明本身不写原文内容，只写"哪份资料、怎么了"。
    """
    title = material_title.strip() or "未命名资料"
    prefix = "来源资料《"
    suffix = "》已删除，引用不可用"
    room = MAX_SOURCE_NOTE_LENGTH - len(prefix) - len(suffix)
    if len(title) > room:
        title = title[: max(room - 1, 1)] + "…"
    return f"{prefix}{title}{suffix}"


def next_version_no(existing: list[int]) -> int:
    """下一版本号：已有版本最大号 + 1，无版本时从 1 开始。"""
    return max(existing, default=0) + 1


def is_superseded(version_no: int, latest_version_no: int) -> bool:
    """该版本是否已被更新的版本取代（J007）。

    版本号更大的版本一经建立，旧版本的作业就是"迟到了"：它的结果属于旧内容，
    不得写回资料的当前状态，否则新版本会收到旧版本的结果。
    """
    return version_no < latest_version_no


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
    candidate_ids: list[int] | None = None,
    linked_fixed_event_ids: list[int] | None = None,
    linked_rule_ids: list[int] | None = None,
    version_relation_ids: list[int] | None = None,
) -> str:
    """删除确认指纹：影响内容的稳定哈希（D008）。

    预览与执行必须基于同一份影响内容；任何一项变化（版本、片段数、
    关联任务、候选、日程、规则与版本关联）都会使旧确认失效，防止用过期的确认执行新的删除。
    截断哈希只用于比对影响集合是否变化，不作为安全凭证。
    """
    import hashlib
    import json

    canonical = json.dumps(
        {
            "material_id": material_id,
            "version_ids": sorted(version_ids),
            "chunk_count": chunk_count,
            "linked_task_ids": sorted(linked_task_ids),
            "candidate_ids": sorted(candidate_ids or []),
            "linked_fixed_event_ids": sorted(linked_fixed_event_ids or []),
            "linked_rule_ids": sorted(linked_rule_ids or []),
            "version_relation_ids": sorted(version_relation_ids or []),
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]
