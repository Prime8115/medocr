"""supplier_column_lessons table

A supplier's line-item columns as reviewers' corrections showed them, applied
when that supplier's next bill is read.

Revision ID: b2e8f4a6c9d1
Revises: a9d3e7c2f1b8
Create Date: 2026-10-12 13:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'b2e8f4a6c9d1'
down_revision = 'a9d3e7c2f1b8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'supplier_column_lessons',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('shop_id', sa.String(length=32), nullable=False),
        sa.Column('supplier_gstin', sa.String(length=15), nullable=False),
        sa.Column('heading', sa.String(length=128), nullable=False),
        sa.Column('heading_printed', sa.String(length=128), nullable=True),
        sa.Column('field', sa.String(length=32), nullable=False),
        sa.Column('kind', sa.String(length=4), nullable=False),
        sa.Column('learned_by', sa.String(length=32), nullable=True),
        sa.Column('times_used', sa.Integer(), nullable=False),
        sa.Column('times_undone', sa.Integer(), nullable=False),
        sa.Column('times_failed', sa.Integer(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['shop_id'], ['shops.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('shop_id', 'supplier_gstin', 'heading', 'field', 'kind',
                            name='uq_supplier_column_lesson'),
    )
    with op.batch_alter_table('supplier_column_lessons', schema=None) as batch_op:
        batch_op.create_index('ix_supplier_column_lessons_shop_id', ['shop_id'])


def downgrade() -> None:
    with op.batch_alter_table('supplier_column_lessons', schema=None) as batch_op:
        batch_op.drop_index('ix_supplier_column_lessons_shop_id')
    op.drop_table('supplier_column_lessons')
