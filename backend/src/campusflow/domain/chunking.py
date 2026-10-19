"""Q003：按已有来源边界分块；所有文本和位置都由原文切片得到。"""

import hashlib
import json
from bisect import bisect_right
from dataclasses import asdict, dataclass

from campusflow.domain.errors import DomainError

CHUNKER_VERSION = "source-boundary-v1"
SENTENCE_ENDINGS = frozenset("。！？；.!?;\n\r")


def _digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class SourceLocation:
    """原文定位；只描述解析器实际给出的位置，不推断页码或合并单元格。"""

    kind: str
    value: str
    page: int | None = None
    paragraph: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    table_group: str | None = None


@dataclass(frozen=True)
class ChunkSource:
    """已保存的片段快照；fingerprint 绑定编号、文本与定位，任一项被改写都会换身份。"""

    id: int
    version_id: int
    seq: int
    text: str
    location: SourceLocation

    def validate(self) -> None:
        if any(type(v) is not int or v < 1 for v in (self.id, self.version_id)):
            raise DomainError("分块来源必须有已保存的片段和版本编号")
        if type(self.seq) is not int or self.seq < 0 or not isinstance(self.text, str):
            raise DomainError("分块来源顺序或文本无效")
        if not isinstance(self.location, SourceLocation) or not self.location.value:
            raise DomainError("分块来源缺少原文定位")
        if self.location.kind not in {
            "page",
            "paragraph",
            "region",
            "table_cell",
            "document",
            "web",
        }:
            raise DomainError("分块来源定位类型无效")
        try:
            self.text.encode("utf-8")
        except UnicodeError:
            raise DomainError("分块来源不是有效 UTF-8 文本") from None

    @property
    def fingerprint(self) -> str:
        # 同一个数据库编号若被错误改写，不能继续复用旧块身份。
        return _digest(asdict(self))


@dataclass(frozen=True)
class ChunkingConfig:
    """分块上限（UTF-8 字节）与重叠字符数；重叠上限取字节上限的 1/8，避免碎块与重复内容。"""

    max_bytes: int = 3000
    overlap_chars: int = 0

    def __post_init__(self):
        if type(self.max_bytes) is not int or not 4 <= self.max_bytes <= 3000:
            raise DomainError("分块上限必须为 4 至 3000 UTF-8 字节")
        if (
            type(self.overlap_chars) is not int
            or not 0 <= self.overlap_chars <= self.max_bytes // 8
        ):
            raise DomainError("分块重叠长度无效")


DEFAULT_CHUNKING = ChunkingConfig()


@dataclass(frozen=True)
class RetrievalBlock:
    """原片段的一段字符切片；ID 由来源指纹与范围确定，可重复生成且不变。"""

    id: str
    source_chunk_id: int
    version_id: int
    source_fingerprint: str
    location: SourceLocation
    start: int
    end: int
    text: str
    chunker_version: str = CHUNKER_VERSION

    def validate_against(self, source: ChunkSource) -> None:
        source.validate()
        if (
            self.source_chunk_id != source.id
            or self.version_id != source.version_id
            or self.location != source.location
            or self.source_fingerprint != source.fingerprint
            or type(self.start) is not int
            or type(self.end) is not int
            or not 0 <= self.start < self.end <= len(source.text)
            or self.text != source.text[self.start : self.end]
            or self.chunker_version != CHUNKER_VERSION
            or self.id != _block_id(source.fingerprint, self.start, self.end)
        ):
            raise DomainError("检索块与原文版本、范围或定位不一致")


def _block_id(fingerprint: str, start: int, end: int) -> str:
    """块身份 = 来源指纹 + 字符范围 + 算法版本；换原文或改范围都会得到新 ID。"""
    return _digest(
        {"source": fingerprint, "start": start, "end": end, "algorithm": CHUNKER_VERSION}
    )


def split_source(
    source: ChunkSource, config: ChunkingConfig = DEFAULT_CHUNKING
) -> list[RetrievalBlock]:
    """把单个片段切成不超过字节上限的块，过长时优先在句末或换行处结束。

    块只是原文切片：不清理、不改写、不合并相邻段落，所有坐标都指回同一原片段。
    """
    source.validate()
    if not source.text:
        return []
    fingerprint = source.fingerprint
    # 字符位置用于精确回到原文；字节累计只用于模型输入长度限制。
    byte_offsets = [0]
    boundaries = []
    for i, character in enumerate(source.text):
        byte_offsets.append(byte_offsets[-1] + len(character.encode("utf-8")))
        if character in SENTENCE_ENDINGS:
            boundaries.append(i + 1)
    blocks = []
    start = 0
    while start < len(source.text):
        limit = bisect_right(byte_offsets, byte_offsets[start] + config.max_bytes) - 1
        end = limit
        # 原片段能放下就完整保留；过长时优先在最近的句末/换行切开。
        if limit < len(source.text):
            boundary_index = bisect_right(boundaries, limit) - 1
            if boundary_index >= 0 and boundaries[boundary_index] > start + config.overlap_chars:
                end = boundaries[boundary_index]
        blocks.append(
            RetrievalBlock(
                id=_block_id(fingerprint, start, end),
                source_chunk_id=source.id,
                version_id=source.version_id,
                source_fingerprint=fingerprint,
                location=source.location,
                start=start,
                end=end,
                text=source.text[start:end],
            )
        )
        if end == len(source.text):
            break
        # 重叠始终引用同一原片段和坐标，不创造新的原文身份；保证每轮前进。
        start = max(start + 1, end - config.overlap_chars)
    return blocks


def split_sources(
    sources: list[ChunkSource], config: ChunkingConfig = DEFAULT_CHUNKING
) -> list[RetrievalBlock]:
    """一次只处理同一资料版本：重复编号必须完全一致，片段顺序不得指向不同片段。"""
    unique: dict[int, ChunkSource] = {}
    sequences: dict[int, int] = {}
    version_id = None
    for source in sources:
        source.validate()
        if version_id is not None and source.version_id != version_id:
            raise DomainError("一次分块不能混合不同资料版本")
        version_id = source.version_id
        if source.id in unique and source != unique[source.id]:
            raise DomainError("同一来源编号出现互相矛盾的文本或定位")
        if source.seq in sequences and sequences[source.seq] != source.id:
            raise DomainError("同一版本内的片段顺序发生冲突")
        unique[source.id] = source
        sequences[source.seq] = source.id
    return [
        block
        for source in sorted(unique.values(), key=lambda item: item.seq)
        for block in split_source(source, config)
    ]
