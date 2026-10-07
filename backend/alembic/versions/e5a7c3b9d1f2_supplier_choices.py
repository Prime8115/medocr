"""supplier_choices table

A reviewer's decision about a supplier's ambiguous field - which of the bill's
two order references is the PO, say - remembered per shop and supplier GSTIN,
so the supplier's next bill arrives already decided.

Revision ID: e5a7c3b9d1f2
Revises: c4f1a8e2d6b5
Create Date: 2026-10-06 18:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'e5a7c3b9d1f2'
down_revision = 'c4f1a8e2d6b5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'supplier_choices',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('shop_id', sa.String(length=32), nullable=False),
        sa.Column('supplier_gstin', sa.String(length=15), nullable=False),
        sa.Column('choice_id', sa.String(length=32), nullable=False),
        sa.Column('option_label', sa.String(length=128), nullable=False),
        sa.Column('decided_by', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['shop_id'], ['shops.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('shop_id', 'supplier_gstin', 'choice_id', name='uq_supplier_choice'),
    )
    with op.batch_alter_table('supplier_choices', schema=None) as batch_op:
        batch_op.create_index('ix_supplier_choices_shop_id', ['shop_id'])


def downgrade() -> None:
    with op.batch_alter_table('supplier_choices', schema=None) as batch_op:
        batch_op.drop_index('ix_supplier_choices_shop_id')
    op.drop_table('supplier_choices')
