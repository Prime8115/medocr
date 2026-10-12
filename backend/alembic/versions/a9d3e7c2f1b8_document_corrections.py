"""document_corrections table

Every field a reviewer changes, with the value read and the value corrected,
so corrections can be learned from per supplier.

Revision ID: a9d3e7c2f1b8
Revises: f6b8d4e2a7c1
Create Date: 2026-10-12 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'a9d3e7c2f1b8'
down_revision = 'f6b8d4e2a7c1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'document_corrections',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('shop_id', sa.String(length=32), nullable=False),
        sa.Column('document_id', sa.String(length=32), nullable=False),
        sa.Column('supplier_gstin', sa.String(length=15), nullable=True),
        sa.Column('field', sa.String(length=64), nullable=False),
        sa.Column('line', sa.Integer(), nullable=True),
        sa.Column('value_read', sa.Text(), nullable=True),
        sa.Column('value_corrected', sa.Text(), nullable=True),
        sa.Column('source', sa.String(length=128), nullable=True),
        sa.Column('pipeline', sa.String(length=32), nullable=True),
        sa.Column('corrected_by', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['shop_id'], ['shops.id']),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('document_corrections', schema=None) as batch_op:
        batch_op.create_index('ix_document_corrections_shop_id', ['shop_id'])
        batch_op.create_index('ix_document_corrections_document_id', ['document_id'])
        batch_op.create_index('ix_document_corrections_supplier_gstin', ['supplier_gstin'])


def downgrade() -> None:
    with op.batch_alter_table('document_corrections', schema=None) as batch_op:
        batch_op.drop_index('ix_document_corrections_supplier_gstin')
        batch_op.drop_index('ix_document_corrections_document_id')
        batch_op.drop_index('ix_document_corrections_shop_id')
    op.drop_table('document_corrections')
