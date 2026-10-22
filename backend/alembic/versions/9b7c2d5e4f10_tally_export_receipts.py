"""Track manual Tally export and the customer's import report.

Revision ID: 9b7c2d5e4f10
Revises: e4f1c7a9b2d6
"""
from alembic import op
import sqlalchemy as sa


revision = "9b7c2d5e4f10"
down_revision = "e4f1c7a9b2d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tally_exports",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("shop_id", sa.String(length=32), sa.ForeignKey("shops.id"), nullable=False),
        sa.Column("document_id", sa.String(length=40), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("connector_id", sa.String(length=32), sa.ForeignKey("connectors.id"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("xml_sha256", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("download_count", sa.Integer(), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("total_amount", sa.String(length=32), nullable=False),
        sa.Column("exported_by", sa.String(length=32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reported_by", sa.String(length=32), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_reference", sa.String(length=128), nullable=True),
        sa.Column("report_reason", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_id", "connector_id", name="uq_tally_export_document_connector"),
    )
    for field in ("shop_id", "document_id", "connector_id"):
        op.create_index(f"ix_tally_exports_{field}", "tally_exports", [field])


def downgrade() -> None:
    op.drop_table("tally_exports")
