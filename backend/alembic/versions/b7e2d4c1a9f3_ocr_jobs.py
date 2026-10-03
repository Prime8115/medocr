"""ocr_jobs table

Extraction work moves from the web process's memory into the database, so a
restart or deploy can no longer lose a scan in flight.

Revision ID: b7e2d4c1a9f3
Revises: a3c9e1f2b7d4
Create Date: 2026-10-03 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'b7e2d4c1a9f3'
down_revision = 'a3c9e1f2b7d4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'ocr_jobs',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('document_id', sa.String(length=40), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('content_type', sa.String(length=64), nullable=False),
        sa.Column('doc_type', sa.String(length=32), nullable=True),
        sa.Column('busy_attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('run_after', sa.DateTime(timezone=True), nullable=False),
        sa.Column('locked_by', sa.String(length=64), nullable=True),
        sa.Column('locked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_ocr_jobs_document_id', 'ocr_jobs', ['document_id'])
    op.create_index('ix_ocr_jobs_status', 'ocr_jobs', ['status'])
    op.create_index('ix_ocr_jobs_run_after', 'ocr_jobs', ['run_after'])
    # At most one queued-or-running job per document, enforced by the database
    # rather than trusted to the application.
    op.create_index(
        'uq_ocr_jobs_one_active_per_document', 'ocr_jobs', ['document_id'], unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
        sqlite_where=sa.text("status IN ('pending', 'running')"),
    )


def downgrade() -> None:
    op.drop_index('uq_ocr_jobs_one_active_per_document', table_name='ocr_jobs')
    op.drop_index('ix_ocr_jobs_run_after', table_name='ocr_jobs')
    op.drop_index('ix_ocr_jobs_status', table_name='ocr_jobs')
    op.drop_index('ix_ocr_jobs_document_id', table_name='ocr_jobs')
    op.drop_table('ocr_jobs')
