"""Q004 search acceleration: HNSW index on block embeddings.

Revision ID: q004_index_hnsw
Revises: q004_index_pipeline
"""

from alembic import op

revision = "q004_index_hnsw"
down_revision = "q004_index_pipeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 余弦距离与 domain/embeddings.py::cosine_similarity 一致，不依赖向量是否已归一化。
    # 当前表为空或很小，普通 CREATE INDEX 在迁移事务内可以接受；等数据量大到
    # 建索引会明显锁表时，再改成 CONCURRENTLY（需要在事务外执行）。
    op.execute(
        "CREATE INDEX ix_indexed_blocks_embedding_hnsw "
        "ON indexed_blocks USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    # 只删除本迁移创建的索引；共享的 vector 扩展由更早的迁移管理，不在这里动。
    op.execute("DROP INDEX IF EXISTS ix_indexed_blocks_embedding_hnsw")
