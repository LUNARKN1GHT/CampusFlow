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
    """单块索引状态存储；方法只 flush，提交与回滚由用例控制。

    领取必须短事务：先提交领取结果，再在事务之外调用模型，最后凭 token 写回。
    """

    def register(self, workspace_id: int, profile_id: str, block: RetrievalBlock) -> None:
        """登记块为待索引；重复登记幂等，空白块直接记为跳过。"""
        ...

    def claim(
        self,
        workspace_id: int,
        profile_id: str,
        block_id: str,
        *,
        lease_seconds: int = DEFAULT_BLOCK_LEASE_SECONDS,
        max_attempts: int = MAX_BLOCK_ATTEMPTS,
    ) -> BlockClaim | None:
        """原子领取一块并返回凭证；None 表示已完成、被占用、不可重试或次数用尽。

        过期租约可被重新领取；尝试次数按领取计数，Worker 崩溃也计入上限。
        """
        ...

    def claim_next(
        self,
        workspace_id: int,
        profile_id: str,
        block_ids: tuple[str, ...] | list[str],
        *,
        lease_seconds: int = DEFAULT_BLOCK_LEASE_SECONDS,
        max_attempts: int = MAX_BLOCK_ATTEMPTS,
    ) -> BlockClaim | None:
        """在给定块范围内挑一块领取；并发被抢时继续尝试下一个候选。"""
        ...

    def save_success(
        self,
        workspace_id: int,
        profile_id: str,
        block_id: str,
        result: EmbeddedText,
        *,
        token: str,
    ) -> bool:
        """凭领取凭证保存向量；False 表示凭证已过期或不属于当前持有者，不得重写。"""
        ...

    def save_failure(
        self,
        workspace_id: int,
        profile_id: str,
        block_id: str,
        code: str,
        *,
        token: str,
        max_attempts: int = MAX_BLOCK_ATTEMPTS,
    ) -> bool:
        """记录安全失败码；次数用尽改为终态失败，False 同样表示凭证失效。"""
        ...

    def progress(
        self, workspace_id: int, profile_id: str, version_id: int
    ) -> tuple[BlockIndexProgress, ...]:
        """只读进度；不重算来源快照，也不触发任何写入。"""
        ...


class IndexManifests(Protocol):
    """一次索引作业的不可变清单与来源快照，方法同样由调用方控制事务。"""

    blocks: IndexedBlocks

    def prepare(
        self,
        workspace_id: int,
        version_id: int,
        profile_id: str,
        *,
        config: ChunkingConfig,
    ) -> str:
        """按完整来源生成快照并登记全部块，返回清单 ID；重复调用幂等。

        来源已归档、被新版本替代或分块超过模型输入上限时拒绝生成。
        """
        ...

    def summary(
        self, workspace_id: int, manifest_id: str, *, verify_snapshot: bool = True
    ) -> IndexProgressSummary:
        """汇总进度；默认重算快照，来源或覆盖变化时抛错要求重新生成清单。"""
        ...

    def blocks_progress(
        self, workspace_id: int, manifest_id: str, *, verify_snapshot: bool = False
    ) -> tuple[BlockIndexProgress, ...]:
        """逐块进度；只读场景跳过全量快照校验，避免长时间持有行锁。"""
        ...

    def claim_next(
        self,
        workspace_id: int,
        manifest_id: str,
        *,
        lease_seconds: int = DEFAULT_BLOCK_LEASE_SECONDS,
        max_attempts: int = MAX_BLOCK_ATTEMPTS,
        verify_snapshot: bool = True,
    ) -> BlockClaim | None:
        """在清单范围内领取下一块；快照变化时拒绝，不能索引旧清单。"""
        ...

    def save_success(
        self,
        workspace_id: int,
        manifest_id: str,
        claim: BlockClaim,
        result: EmbeddedText,
        *,
        verify_snapshot: bool = True,
    ) -> bool:
        """校验凭证属于当前清单后写回；False 表示凭证已失效。"""
        ...

    def save_failure(
        self,
        workspace_id: int,
        manifest_id: str,
        claim: BlockClaim,
        code: str,
        *,
        max_attempts: int = MAX_BLOCK_ATTEMPTS,
        verify_snapshot: bool = True,
    ) -> bool:
        """记录失败码：码必须在安全白名单内，凭证失效时返回 False 且不写回。"""
        ...
