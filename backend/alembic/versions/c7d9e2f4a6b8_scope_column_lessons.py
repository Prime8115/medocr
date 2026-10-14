"""Scope learned columns to the ordered table headings.

Revision ID: c7d9e2f4a6b8
Revises: b2e8f4a6c9d1
"""
from alembic import op
import sqlalchemy as sa

revision = "c7d9e2f4a6b8"
down_revision = "b2e8f4a6c9d1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("supplier_column_lessons") as batch:
        batch.add_column(sa.Column("layout_signature", sa.String(64), nullable=False, server_default=""))
        batch.drop_constraint("uq_supplier_column_lesson", type_="unique")
        batch.create_unique_constraint("uq_supplier_column_lesson_layout",
                                       ["shop_id", "supplier_gstin", "layout_signature", "heading", "field", "kind"])
    # Preserve old lessons for inspection. Their source layout is unknown;
    # they must be taught again on a document carrying layout evidence.
    op.execute(sa.text("UPDATE supplier_column_lessons SET active = false WHERE layout_signature = ''"))


def downgrade():
    # Multiple layouts can have the same old key. Refuse a lossy rollback.
    connection = op.get_bind()
    duplicates = connection.execute(sa.text(
        "SELECT 1 FROM supplier_column_lessons "
        "GROUP BY shop_id, supplier_gstin, heading, field, kind HAVING COUNT(*) > 1"
    )).first()
    if duplicates:
        raise RuntimeError("Cannot remove layout scope while multiple layouts share a lesson key.")
    with op.batch_alter_table("supplier_column_lessons") as batch:
        batch.drop_constraint("uq_supplier_column_lesson_layout", type_="unique")
        batch.drop_column("layout_signature")
        batch.create_unique_constraint("uq_supplier_column_lesson",
                                       ["shop_id", "supplier_gstin", "heading", "field", "kind"])
