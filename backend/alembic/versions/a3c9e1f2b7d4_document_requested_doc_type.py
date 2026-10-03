"""document requested_doc_type column

The type the user picked at upload (NULL = Auto), so a retry re-detects an
Auto upload instead of reusing doc_type's "prescription" placeholder.

Revision ID: a3c9e1f2b7d4
Revises: 06e9ecb4e9b3
Create Date: 2026-10-03 10:45:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'a3c9e1f2b7d4'
down_revision = '06e9ecb4e9b3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing rows stay NULL: their original choice was never recorded, so a
    # retry detects the type again - which is what fixes the scans already
    # stuck as "prescription".
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('requested_doc_type', sa.String(length=32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.drop_column('requested_doc_type')
