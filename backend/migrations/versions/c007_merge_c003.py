"""Merge C003 curriculum candidate scope with C007 study records.

Revision ID: c007_merge_c003
Revises: c007_course_study_records, c003_curriculum_candidates
"""

revision = "c007_merge_c003"
down_revision = ("c007_course_study_records", "c003_curriculum_candidates")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
