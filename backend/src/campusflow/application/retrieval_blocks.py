"""Q003：授权读取已保存的原文片段，返回纯计算的检索块。"""

from campusflow.application.materials import list_version_chunks
from campusflow.application.ports.repositories import Repositories
from campusflow.domain.chunking import (
    DEFAULT_CHUNKING,
    ChunkingConfig,
    ChunkSource,
    RetrievalBlock,
    SourceLocation,
    split_sources,
)


def build_version_blocks(
    repos: Repositories,
    workspace_id: int,
    material_id: int,
    version_no: int,
    *,
    config: ChunkingConfig = DEFAULT_CHUNKING,
) -> list[RetrievalBlock]:
    """授权读取指定版本的原文片段并切分为检索块；不写库、不调用模型。

    切分沿用保存时的片段边界与定位信息，同一输入重复调用结果一致；
    不合并段落或表格单元，避免引用无法回到原文。
    """
    chunks = list_version_chunks(repos, workspace_id, material_id, version_no)
    return split_sources(
        [
            ChunkSource(
                id=chunk.id,
                version_id=chunk.version_id,
                seq=chunk.seq,
                text=chunk.text,
                location=SourceLocation(
                    kind=chunk.locator_type,
                    value=chunk.locator_value,
                    page=chunk.page,
                    paragraph=chunk.paragraph,
                    bbox=chunk.bbox,
                    table_group=chunk.table_group,
                ),
            )
            for chunk in chunks
        ],
        config,
    )
