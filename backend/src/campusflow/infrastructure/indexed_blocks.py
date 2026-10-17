"""Q004 阶段存储：显式登记、原子保存结果；事务由调用方控制，尚未接 Worker。"""

import math
import struct
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from campusflow.domain.chunking import ChunkSource, RetrievalBlock, SourceLocation
from campusflow.domain.embeddings import EmbeddedText
from campusflow.domain.errors import DomainError, NotFoundError
from campusflow.domain.indexing import (
    ATTEMPTS_EXHAUSTED_CODE,
    BLOCK_FAILURE_CODES,
    DEFAULT_BLOCK_LEASE_SECONDS,
    MAX_BLOCK_ATTEMPTS,
    MAX_BLOCK_LEASE_SECONDS,
    BlockClaim,
    BlockIndexProgress,
    BlockIndexState,
)
from campusflow.infrastructure.db.models import IndexedBlock, Material, MaterialVersion, SourceChunk
from campusflow.infrastructure.embedding_profiles import SqlEmbeddingProfiles

# 兼容既有导入名；可写入的失败码集合定义在领域层，避免适配器与用例各写一份。
FAILURE_CODES = BLOCK_FAILURE_CODES


def _validate_lease(lease_seconds: int) -> None:
    if type(lease_seconds) is not int or not 1 <= lease_seconds <= MAX_BLOCK_LEASE_SECONDS:
        raise DomainError(f"领取有效期必须为 1 至 {MAX_BLOCK_LEASE_SECONDS} 秒")


def _validate_max_attempts(max_attempts: int) -> None:
    if type(max_attempts) is not int or not 1 <= max_attempts <= 10:
        raise DomainError("每块最大尝试次数必须为 1 至 10")


class SqlIndexedBlocks:
    def __init__(self, session: Session):
        self.session = session

    def _profile(self, profile_id: str):
        profile = SqlEmbeddingProfiles(self.session).get(profile_id)
        if profile is None or profile.dimensions != 1024:
            raise DomainError("索引必须使用已登记的 1024 维模型配置")
        return profile

    def _source(self, workspace_id: int, source_id: int) -> ChunkSource:
        material_id = self.session.scalar(
            select(MaterialVersion.material_id)
            .join(SourceChunk, SourceChunk.version_id == MaterialVersion.id)
            .where(SourceChunk.id == source_id)
        )
        # 先锁资料，再锁其版本/片段，再锁索引行；同份资料的不同块保持相同锁顺序。
        material = self.session.scalar(
            select(Material)
            .where(
                Material.id == material_id,
                Material.workspace_id == workspace_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if material is None or material.archived:
            raise NotFoundError("索引来源不存在或已归档")
        row = self.session.execute(
            select(SourceChunk, MaterialVersion.version_no)
            .join(MaterialVersion, SourceChunk.version_id == MaterialVersion.id)
            .join(Material, MaterialVersion.material_id == Material.id)
            .where(SourceChunk.id == source_id, Material.workspace_id == workspace_id)
            .with_for_update(of=[SourceChunk, MaterialVersion])
            .execution_options(populate_existing=True)
        ).one_or_none()
        if row is None:
            raise NotFoundError("索引来源不存在或已归档")
        latest = self.session.scalar(
            select(func.max(MaterialVersion.version_no)).where(
                MaterialVersion.material_id == material_id,
            )
        )
        if row[1] != latest:
            raise DomainError("来源已被新版本替代，旧结果不得写回")
        chunk = row[0]
        bbox = (
            None
            if chunk.bbox_x0 is None
            else (chunk.bbox_x0, chunk.bbox_y0, chunk.bbox_x1, chunk.bbox_y1)
        )
        return ChunkSource(
            chunk.id,
            chunk.version_id,
            chunk.seq,
            chunk.text,
            SourceLocation(
                chunk.locator_type,
                chunk.locator_value,
                chunk.page,
                chunk.paragraph,
                bbox,
                chunk.table_group,
            ),
        )

    def register(self, workspace_id: int, profile_id: str, block: RetrievalBlock) -> None:
        profile = self._profile(profile_id)
        block.validate_against(self._source(workspace_id, block.source_chunk_id))
        if len(block.text.encode("utf-8")) > profile.max_input_bytes:
            raise DomainError("分块超过当前向量配置输入上限")
        blank = not block.text.strip()
        self.session.execute(
            insert(IndexedBlock)
            .values(
                profile_id=profile_id,
                block_id=block.id,
                workspace_id=workspace_id,
                version_id=block.version_id,
                source_chunk_id=block.source_chunk_id,
                source_fingerprint=block.source_fingerprint,
                start_offset=block.start,
                end_offset=block.end,
                chunker_version=block.chunker_version,
                text=block.text,
                state="skipped" if blank else "pending",
                retryable=False,
            )
            .on_conflict_do_nothing(index_elements=["profile_id", "block_id"])
        )
        current = self._row(workspace_id, profile_id, block.id)
        self._validate_current(current, self._source(workspace_id, current.source_chunk_id))

    def _row(self, workspace_id: int, profile_id: str, block_id: str) -> IndexedBlock:
        row = self.session.scalar(
            select(IndexedBlock)
            .where(
                IndexedBlock.workspace_id == workspace_id,
                IndexedBlock.profile_id == profile_id,
                IndexedBlock.block_id == block_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise NotFoundError("索引块不存在")
        return row

    @staticmethod
    def _validate_current(row: IndexedBlock, source: ChunkSource) -> None:
        RetrievalBlock(
            row.block_id,
            row.source_chunk_id,
            row.version_id,
            row.source_fingerprint,
            source.location,
            row.start_offset,
            row.end_offset,
            row.text,
            row.chunker_version,
        ).validate_against(source)

    def save_success(
        self,
        workspace_id: int,
        profile_id: str,
        block_id: str,
        result: EmbeddedText,
        *,
        token: str,
    ) -> bool:
        profile = self._profile(profile_id)
        result.validate_for(profile)
        try:
            format_string = f"!{profile.dimensions}f"
            values = struct.unpack(format_string, struct.pack(format_string, *result.values))
        except (struct.error, OverflowError):
            raise DomainError("向量不能安全存入单精度字段") from None
        if not all(math.isfinite(value) for value in values) or not any(values):
            raise DomainError("向量存储转换后无效")
        row = self._locked_current(workspace_id, profile_id, block_id)
        if row.state == "skipped":
            raise DomainError("跳过的空白块不能保存向量")
        if not self._owns_claim(row, token):
            return False
        row.embedding = list(values)
        row.state, row.retryable, row.failure_code = "done", False, None
        row.claim_token, row.lease_until = None, None
        self.session.flush()
        return True

    def _locked_current(self, workspace_id: int, profile_id: str, block_id: str) -> IndexedBlock:
        # 与 register 保持来源 -> 索引块的加锁顺序，避免并发登记/保存相互反锁。
        source_id = self.session.scalar(
            select(IndexedBlock.source_chunk_id).where(
                IndexedBlock.workspace_id == workspace_id,
                IndexedBlock.profile_id == profile_id,
                IndexedBlock.block_id == block_id,
            )
        )
        if source_id is None:
            raise NotFoundError("索引块不存在")
        source = self._source(workspace_id, source_id)
        row = self._row(workspace_id, profile_id, block_id)
        self._validate_current(row, source)
        return row

    def _owns_claim(self, row: IndexedBlock, token: str) -> bool:
        # 取数据库实际时钟，不能用事务开始时间：等待锁时凭证可能已经过期。
        now = self.session.scalar(select(func.clock_timestamp()))
        return (
            row.state == "running"
            and isinstance(token, str)
            and bool(token)
            and row.claim_token == token
            and row.lease_until is not None
            and row.lease_until > now
        )

    def claim(
        self,
        workspace_id: int,
        profile_id: str,
        block_id: str,
        *,
        lease_seconds: int = DEFAULT_BLOCK_LEASE_SECONDS,
        max_attempts: int = MAX_BLOCK_ATTEMPTS,
    ) -> BlockClaim | None:
        """短事务原子领取，过期领取可替换；调用方须提交后才请求模型。"""
        _validate_lease(lease_seconds)
        _validate_max_attempts(max_attempts)
        row = self._locked_current(workspace_id, profile_id, block_id)
        now = self.session.scalar(select(func.clock_timestamp()))
        eligible = (
            row.state == "pending"
            or (row.state == "failed" and row.retryable)
            or (row.state == "running" and row.lease_until <= now)
        )
        if not eligible:
            return None
        # 次数按领取计数：Worker 领取后崩溃也算一次，保证崩溃循环有界。
        if row.attempts >= max_attempts:
            row.state, row.retryable = "failed", False
            row.failure_code = ATTEMPTS_EXHAUSTED_CODE
            row.claim_token, row.lease_until = None, None
            self.session.flush()
            return None
        row.state, row.retryable, row.failure_code = "running", False, None
        row.claim_token = uuid4().hex
        row.lease_until = now + timedelta(seconds=lease_seconds)
        row.attempts = row.attempts + 1
        self.session.flush()
        return BlockClaim(row.block_id, row.profile_id, row.claim_token, row.text, row.lease_until)

    def claim_next(
        self,
        workspace_id: int,
        profile_id: str,
        block_ids: tuple[str, ...] | list[str],
        *,
        lease_seconds: int = DEFAULT_BLOCK_LEASE_SECONDS,
        max_attempts: int = MAX_BLOCK_ATTEMPTS,
    ) -> BlockClaim | None:
        """一次查询挑出可领取块再原子领取，避免按清单逐块扫描。

        先做不加锁的候选查询，再交给 claim 按「来源 → 索引块」的固定顺序加锁，
        避免与 register/save 形成反向锁序。并发下候选被抢走时 claim 返回 None，
        继续尝试下一个候选。
        """
        _validate_lease(lease_seconds)
        _validate_max_attempts(max_attempts)
        if not block_ids:
            return None
        now = self.session.scalar(select(func.clock_timestamp()))
        candidates = self.session.scalars(
            select(IndexedBlock.block_id)
            .where(
                IndexedBlock.workspace_id == workspace_id,
                IndexedBlock.profile_id == profile_id,
                IndexedBlock.block_id.in_(list(block_ids)),
                or_(
                    IndexedBlock.state == "pending",
                    and_(IndexedBlock.state == "failed", IndexedBlock.retryable.is_(True)),
                    and_(IndexedBlock.state == "running", IndexedBlock.lease_until <= now),
                ),
            )
            .order_by(IndexedBlock.source_chunk_id, IndexedBlock.start_offset)
        ).all()
        for block_id in candidates:
            claim = self.claim(
                workspace_id,
                profile_id,
                block_id,
                lease_seconds=lease_seconds,
                max_attempts=max_attempts,
            )
            if claim is not None:
                return claim
        return None

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
        _validate_max_attempts(max_attempts)
        if code not in FAILURE_CODES:
            raise DomainError("失败原因必须使用安全代码，不能写入原始异常或密钥")
        row = self._locked_current(workspace_id, profile_id, block_id)
        if not self._owns_claim(row, token):
            return False
        row.state = "failed"
        # 次数用尽后统一记为终态失败：原始码不再重要，也不允许再自动重试。
        if row.attempts >= max_attempts:
            row.failure_code, row.retryable = ATTEMPTS_EXHAUSTED_CODE, False
        else:
            row.failure_code = code
            row.retryable = code == "upstream_transient"
        row.claim_token, row.lease_until = None, None
        self.session.flush()
        return True

    def progress(
        self, workspace_id: int, profile_id: str, version_id: int
    ) -> tuple[BlockIndexProgress, ...]:
        rows = self.session.scalars(
            select(IndexedBlock)
            .where(
                IndexedBlock.workspace_id == workspace_id,
                IndexedBlock.profile_id == profile_id,
                IndexedBlock.version_id == version_id,
            )
            .order_by(IndexedBlock.source_chunk_id, IndexedBlock.start_offset)
        )
        return tuple(
            BlockIndexProgress(
                r.block_id,
                BlockIndexState(r.state),
                r.retryable,
                r.attempts,
                r.failure_code,
            )
            for r in rows
        )
