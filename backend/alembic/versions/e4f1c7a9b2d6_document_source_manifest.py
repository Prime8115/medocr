"""Keep an optional encrypted original and a page lineage manifest.

Existing documents remain NULL and continue to use image_ref.

Revision ID: e4f1c7a9b2d6
Revises: d2f4a6c8e1b3
"""
from alembic import op
import sqlalchemy as sa


revision = "e4f1c7a9b2d6"
down_revision = "d2f4a6c8e1b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch_op:
        batch_op.add_column(sa.Column("original_ref", sa.String(length=512), nullable=True))
        batch_op.add_column(sa.Column("source_manifest", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_column("source_manifest")
        batch_op.drop_column("original_ref")
