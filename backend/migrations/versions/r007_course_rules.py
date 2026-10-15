"""R006 course rules table

Revision ID: r007_course_rules
Revises: r006_event_source_invalidation
Create Date: 2026-10-26 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "r007_course_rules"
down_revision: str | None = "r006_event_source_invalidation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 由已确认规则候选转换而来的课程规则；条件、适用范围与生效声明只保存原文文本
    op.create_table(
        "course_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=True),
        sa.Column("semester_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("rule_text", sa.String(), nullable=False),
        sa.Column("conditions_text", sa.String(), nullable=True),
        sa.Column("scope_text", sa.String(), nullable=True),
        sa.Column("effective_text", sa.String(), nullable=True),
        sa.Column("source_material_id", sa.Integer(), nullable=True),
        sa.Column("source_candidate_id", sa.Integer(), nullable=True),
        # 来源失效留痕：记住"曾经有来源、来源已被删除"
        sa.Column("source_invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_invalidated_note", sa.String(length=300), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"]),
        sa.ForeignKeyConstraint(["semester_id"], ["semesters.id"]),
        # 删除资料时置空，失效由上面的两列标记
        sa.ForeignKeyConstraint(["source_material_id"], ["materials.id"], ondelete="SET NULL"),
        # 唯一索引保证一个候选只建一条规则
        sa.ForeignKeyConstraint(
            ["source_candidate_id"], ["extracted_items.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_course_rules_workspace_id"), "course_rules", ["workspace_id"])
    op.create_index(op.f("ix_course_rules_course_id"), "course_rules", ["course_id"])
    op.create_index(op.f("ix_course_rules_semester_id"), "course_rules", ["semester_id"])
    op.create_index(
        op.f("ix_course_rules_source_material_id"), "course_rules", ["source_material_id"]
    )
    op.create_index(
        op.f("ix_course_rules_source_candidate_id"),
        "course_rules",
        ["source_candidate_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_course_rules_source_candidate_id"), table_name="course_rules")
    op.drop_index(op.f("ix_course_rules_source_material_id"), table_name="course_rules")
    op.drop_index(op.f("ix_course_rules_semester_id"), table_name="course_rules")
    op.drop_index(op.f("ix_course_rules_course_id"), table_name="course_rules")
    op.drop_index(op.f("ix_course_rules_workspace_id"), table_name="course_rules")
    op.drop_table("course_rules")
