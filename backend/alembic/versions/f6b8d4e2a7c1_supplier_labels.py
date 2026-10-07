"""supplier_labels table

Where a supplier prints a field, learned from a reviewer's correction, per
shop and supplier GSTIN - so the supplier's next bill is read there.

Revision ID: f6b8d4e2a7c1
Revises: e5a7c3b9d1f2
Create Date: 2026-10-06 21:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'f6b8d4e2a7c1'
down_revision = 'e5a7c3b9d1f2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'supplier_labels',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('shop_id', sa.String(length=32), nullable=False),
        sa.Column('supplier_gstin', sa.String(length=15), nullable=False),
        sa.Column('field', sa.String(length=64), nullable=False),
        sa.Column('label', sa.String(length=128), nullable=False),
        sa.Column('position', sa.String(length=8), nullable=False),
        sa.Column('shape', sa.String(length=64), nullable=True),
        sa.Column('words', sa.Integer(), nullable=False),
        sa.Column('tail', sa.Integer(), nullable=True),
        sa.Column('learned_from', sa.String(length=8), nullable=False),
        sa.Column('learned_by', sa.String(length=32), nullable=True),
        sa.Column('times_used', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['shop_id'], ['shops.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('shop_id', 'supplier_gstin', 'field', name='uq_supplier_label'),
    )
    with op.batch_alter_table('supplier_labels', schema=None) as batch_op:
        batch_op.create_index('ix_supplier_labels_shop_id', ['shop_id'])


def downgrade() -> None:
    with op.batch_alter_table('supplier_labels', schema=None) as batch_op:
        batch_op.drop_index('ix_supplier_labels_shop_id')
    op.drop_table('supplier_labels')
