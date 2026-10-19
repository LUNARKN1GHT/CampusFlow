"""Q004 数据库结构：HNSW 检索索引、领取次数列与唯一索引由迁移建立。"""

import pytest
from index_helpers import open_session
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


def _scalar(sql: str, **params):
    with open_session() as session:
        return session.scalar(text(sql), params)


def test_hnsw_index_exists_with_cosine_opclass():
    row = _scalar(
        """
        SELECT a.amname || '|' || opc.opcname
        FROM pg_index i
        JOIN pg_class c ON c.oid = i.indexrelid
        JOIN pg_am a ON a.oid = c.relam
        JOIN pg_opclass opc ON opc.oid = i.indclass[0]
        WHERE c.relname = 'ix_indexed_blocks_embedding_hnsw'
        """
    )
    assert row == "hnsw|vector_cosine_ops"

    definition = _scalar(
        "SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_indexed_blocks_embedding_hnsw'"
    )
    assert "USING hnsw" in definition
    assert "vector_cosine_ops" in definition


def test_attempts_column_has_default_and_check_constraint():
    row = _scalar(
        """
        SELECT is_nullable || '|' || coalesce(column_default, '')
        FROM information_schema.columns
        WHERE table_name = 'indexed_blocks' AND column_name = 'attempts'
        """
    )
    assert row == "NO|0"

    assert (
        _scalar("SELECT count(*) FROM pg_constraint WHERE conname = 'ck_indexed_blocks_attempts'")
        == 1
    )


def test_attempts_cannot_be_negative():
    with pytest.raises(IntegrityError):
        with open_session() as session, session.begin():
            session.execute(
                text(
                    """
                    INSERT INTO indexed_blocks (
                        profile_id, block_id, workspace_id, version_id, source_chunk_id,
                        source_fingerprint, start_offset, end_offset, chunker_version,
                        text, attempts
                    )
                    VALUES ('missing', 'b', 1, 1, 1, 'f', 0, 1, 'v', 't', -1)
                    """
                )
            )


def test_one_embed_job_per_version_is_enforced_by_database():
    definition = _scalar(
        "SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_processing_jobs_embed_version'"
    )
    assert "UNIQUE INDEX" in definition.upper()
    assert "WHERE" in definition and "'embed'::text" in definition


def test_manifest_and_heartbeat_columns_exist():
    for column in ("manifest_id", "heartbeat_at", "auto_retry_count"):
        assert (
            _scalar(
                """
                SELECT count(*) FROM information_schema.columns
                WHERE table_name = 'processing_jobs' AND column_name = :name
                """,
                name=column,
            )
            == 1
        )
