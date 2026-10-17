"""Q004 完整清单和领取入口；每次调用使用短事务，不在事务中请求外部模型。"""

import hashlib
import json
from dataclasses import asdict

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from campusflow.application.source_uncertainty import COVERAGE_KEY
from campusflow.domain.chunking import (
    CHUNKER_VERSION,
    DEFAULT_CHUNKING,
    ChunkingConfig,
    RetrievalBlock,
    split_sources,
)
from campusflow.domain.embeddings import EmbeddedText
from campusflow.domain.errors import DomainError, NotFoundError
from campusflow.domain.indexing import (
    BlockClaim,
    BlockIndexProgress,
    BlockIndexState,
    IndexProgressSummary,
    summarize_index_progress,
    validated_index_coverage,
)
from campusflow.infrastructure.db.models import (
    IndexedBlock,
    IndexManifest,
    Material,
    MaterialVersion,
    SourceChunk,
)
from campusflow.infrastructure.indexed_blocks import SqlIndexedBlocks


def _identity(snapshot: dict) -> str:
    return hashlib.sha256(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class SqlIndexManifests:
    def __init__(self, session: Session):
        self.session = session
        self.blocks = SqlIndexedBlocks(session)

    def _snapshot(
        self, workspace_id: int, version_id: int, profile_id: str, config: ChunkingConfig
    ) -> tuple[dict, list[RetrievalBlock]]:
        profile = self.blocks._profile(profile_id)
        if config.max_bytes > profile.max_input_bytes:
            raise DomainError("分块设置超过模型配置输入上限")
        material_id = self.session.scalar(
            select(MaterialVersion.material_id).where(MaterialVersion.id == version_id)
        )
        material = self.session.scalar(
            select(Material)
            .where(Material.id == material_id, Material.workspace_id == workspace_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if material is None or material.archived:
            raise NotFoundError("索引来源不存在或已归档")
        # 锁版本阻止同时增加片段（外键检查），再按固定顺序锁既有片段。
        version = self.session.scalar(
            select(MaterialVersion)
            .where(MaterialVersion.id == version_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if version is None:
            raise NotFoundError("资料版本不存在")
        latest = self.session.scalar(
            select(func.max(MaterialVersion.version_no)).where(
                MaterialVersion.material_id == material_id
            )
        )
        if version.version_no != latest:
            raise DomainError("来源已被新版本替代，旧清单不得继续处理")
        ids = list(
            self.session.scalars(
                select(SourceChunk.id)
                .where(SourceChunk.version_id == version_id)
                .order_by(SourceChunk.seq)
                .with_for_update()
            )
        )
        sources = [self.blocks._source(workspace_id, key) for key in ids]
        blocks = split_sources(sources, config)
        metadata = version.source_metadata
        coverage = metadata.get(COVERAGE_KEY) if isinstance(metadata, dict) else None
        snapshot = {
            "schema": 1,
            "workspace_id": workspace_id,
            "version_id": version_id,
            "profile_id": profile_id,
            "chunker": CHUNKER_VERSION,
            "config": asdict(config),
            "sources": [{"id": source.id, "fingerprint": source.fingerprint} for source in sources],
            "block_ids": [block.id for block in blocks],
            "coverage": coverage,
            "empty_source_count": sum(not source.text for source in sources),
        }
        return snapshot, blocks

    def prepare(
        self,
        workspace_id: int,
        version_id: int,
        profile_id: str,
        *,
        config: ChunkingConfig = DEFAULT_CHUNKING,
    ) -> str:
        """从数据库完整来源生成快照；清单和所有初始块同事务提交。"""
        snapshot, blocks = self._snapshot(workspace_id, version_id, profile_id, config)
        key = _identity(snapshot)
        for block in blocks:
            self.blocks.register(workspace_id, profile_id, block)
        self.session.execute(
            insert(IndexManifest)
            .values(
                id=key,
                workspace_id=workspace_id,
                version_id=version_id,
                profile_id=profile_id,
                snapshot=snapshot,
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )
        return key

    def _current(self, workspace_id: int, manifest_id: str) -> IndexManifest:
        row = self.session.scalar(
            select(IndexManifest)
            .where(
                IndexManifest.id == manifest_id,
                IndexManifest.workspace_id == workspace_id,
            )
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise NotFoundError("索引清单不存在")
        if _identity(row.snapshot) != row.id:
            raise DomainError("索引清单快照已改变，必须重新生成")
        snapshot, _ = self._snapshot(
            workspace_id, row.version_id, row.profile_id, ChunkingConfig(**row.snapshot["config"])
        )
        if snapshot != row.snapshot:
            raise DomainError("索引来源或解析覆盖已变化，必须重新生成清单")
        return row

    def summary(self, workspace_id: int, manifest_id: str) -> IndexProgressSummary:
        manifest = self._current(workspace_id, manifest_id)
        snapshot = manifest.snapshot
        ids = snapshot["block_ids"]
        records = self.session.scalars(
            select(IndexedBlock)
            .where(
                IndexedBlock.workspace_id == workspace_id,
                IndexedBlock.profile_id == manifest.profile_id,
                IndexedBlock.version_id == manifest.version_id,
                IndexedBlock.block_id.in_(ids),
            )
            .execution_options(populate_existing=True)
        )
        known, uncovered = validated_index_coverage(snapshot["coverage"], len(snapshot["sources"]))
        return summarize_index_progress(
            ids,
            [
                BlockIndexProgress(r.block_id, BlockIndexState(r.state), r.retryable)
                for r in records
            ],
            source_coverage_known=known,
            uncovered_source_count=uncovered + snapshot["empty_source_count"],
        )

    def claim_next(
        self, workspace_id: int, manifest_id: str, *, lease_seconds: int = 900
    ) -> BlockClaim | None:
        manifest = self._current(workspace_id, manifest_id)
        for key in manifest.snapshot["block_ids"]:
            claim = self.blocks.claim(
                workspace_id, manifest.profile_id, key, lease_seconds=lease_seconds
            )
            if claim is not None:
                return claim
        return None

    def _check_claim(self, workspace_id: int, manifest_id: str, claim: BlockClaim) -> None:
        manifest = self._current(workspace_id, manifest_id)
        if (
            claim.profile_id != manifest.profile_id
            or claim.block_id not in manifest.snapshot["block_ids"]
        ):
            raise DomainError("领取凭证不属于当前清单")

    def save_success(
        self, workspace_id: int, manifest_id: str, claim: BlockClaim, result: EmbeddedText
    ) -> bool:
        self._check_claim(workspace_id, manifest_id, claim)
        return self.blocks.save_success(
            workspace_id, claim.profile_id, claim.block_id, result, token=claim.token
        )

    def save_failure(
        self, workspace_id: int, manifest_id: str, claim: BlockClaim, code: str
    ) -> bool:
        self._check_claim(workspace_id, manifest_id, claim)
        return self.blocks.save_failure(
            workspace_id, claim.profile_id, claim.block_id, code, token=claim.token
        )
