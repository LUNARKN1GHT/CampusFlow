"""索引存储端口（Q004）：应用层只依赖这些行为，不接触数据库类型。

事务由调用方控制：领取与保存各自使用短事务，调用模型时不得持有事务。
"""

from typing import Protocol

from campusflow.domain.chunking import ChunkingConfig, RetrievalBlock
from campusflow.domain.embeddings import EmbeddedText
from campusflow.domain.indexing import (
    DEFAULT_BLOCK_LEASE_SECONDS,
    MAX_BLOCK_ATTEMPTS,
    BlockClaim,
    BlockIndexProgress,
    IndexProgressSummary,
)


class IndexedBlocks(Protocol):
    def register(self, workspace_id: int, profile_id: str, block: RetrievalBlock) -> None: ...

    def claim(
        self,
        workspace_id: int,
        profile_id: str,
        block_id: str,
        *,
        lease_seconds: int = DEFAULT_BLOCK_LEASE_SECONDS,
        max_attempts: int = MAX_BLOCK_ATTEMPTS,
    ) -> BlockClaim | None: ...

    def claim_next(
        self,
        workspace_id: int,
        profile_id: str,
        block_ids: tuple[str, ...] | list[str],
        *,
        lease_seconds: int = DEFAULT_BLOCK_LEASE_SECONDS,
        max_attempts: int = MAX_BLOCK_ATTEMPTS,
    ) -> BlockClaim | None: ...

    def save_success(
        self,
        workspace_id: int,
        profile_id: str,
        block_id: str,
        result: EmbeddedText,
        *,
        token: str,
    ) -> bool: ...

    def save_failure(
        self,
        workspace_id: int,
        profile_id: str,
        block_id: str,
        code: str,
        *,
        token: str,
        max_attempts: int = MAX_BLOCK_ATTEMPTS,
    ) -> bool: ...

    def progress(
        self, workspace_id: int, profile_id: str, version_id: int
    ) -> tuple[BlockIndexProgress, ...]: ...


class IndexManifests(Protocol):
    blocks: IndexedBlocks

    def prepare(
        self,
        workspace_id: int,
        version_id: int,
        profile_id: str,
        *,
        config: ChunkingConfig,
    ) -> str: ...

    def summary(
        self, workspace_id: int, manifest_id: str, *, verify_snapshot: bool = True
    ) -> IndexProgressSummary: ...

    def blocks_progress(
        self, workspace_id: int, manifest_id: str, *, verify_snapshot: bool = False
    ) -> tuple[BlockIndexProgress, ...]: ...

    def claim_next(
        self,
        workspace_id: int,
        manifest_id: str,
        *,
        lease_seconds: int = DEFAULT_BLOCK_LEASE_SECONDS,
        max_attempts: int = MAX_BLOCK_ATTEMPTS,
        verify_snapshot: bool = True,
    ) -> BlockClaim | None: ...

    def save_success(
        self,
        workspace_id: int,
        manifest_id: str,
        claim: BlockClaim,
        result: EmbeddedText,
        *,
        verify_snapshot: bool = True,
    ) -> bool: ...

    def save_failure(
        self,
        workspace_id: int,
        manifest_id: str,
        claim: BlockClaim,
        code: str,
        *,
        max_attempts: int = MAX_BLOCK_ATTEMPTS,
        verify_snapshot: bool = True,
    ) -> bool: ...
