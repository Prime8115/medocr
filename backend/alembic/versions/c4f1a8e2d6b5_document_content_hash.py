"""document content_hash column

SHA-256 of the file as uploaded, so the same file sent twice (a double tap,
the offline queue retrying after a dropped reply) opens the earlier scan
instead of being read - and charged against the AI quota - again.

Revision ID: c4f1a8e2d6b5
Revises: b7e2d4c1a9f3
Create Date: 2026-10-04 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'c4f1a8e2d6b5'
down_revision = 'b7e2d4c1a9f3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing rows stay NULL: they were uploaded before files were hashed.
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('content_hash', sa.String(length=64), nullable=True))
        batch_op.create_index('ix_documents_shop_content_hash', ['shop_id', 'content_hash'])


def downgrade() -> None:
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.drop_index('ix_documents_shop_content_hash')
        batch_op.drop_column('content_hash')
