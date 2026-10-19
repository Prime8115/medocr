"""document upload parts

A PDF holding several invoices is split into one document each. The parts
were not linked, so the app opened the first and the rest were left for the
pharmacist to find in History: a 7-page Ajanta file showed "9 items · 1 pages"
and its 6-page, 165-line credit note looked lost.

Revision ID: d2f4a6c8e1b3
Revises: c7d9e2f4a6b8
Create Date: 2026-10-19 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'd2f4a6c8e1b3'
down_revision = 'c7d9e2f4a6b8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing rows stay NULL: one document per upload, or parts never linked.
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('upload_id', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('part_no', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('part_count', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('part_pages', sa.String(length=32), nullable=True))
        batch_op.create_index('ix_documents_upload_id', ['upload_id'])


def downgrade() -> None:
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.drop_index('ix_documents_upload_id')
        batch_op.drop_column('part_pages')
        batch_op.drop_column('part_count')
        batch_op.drop_column('part_no')
        batch_op.drop_column('upload_id')
