"""Platform report triage without losing older audit-log complaints.

Revision ID: a18c34d790f2
Revises: 9b7c2d5e4f10
"""
from alembic import op
import sqlalchemy as sa

revision = "a18c34d790f2"
down_revision = "9b7c2d5e4f10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("is_platform_admin", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "problem_report_reviews",
        sa.Column("report_id", sa.String(length=32), sa.ForeignKey("audit_log.id"), primary_key=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.String(length=32), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_log_action_created_at", "audit_log", ["action", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_action_created_at", table_name="audit_log")
    op.drop_table("problem_report_reviews")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("is_platform_admin")
