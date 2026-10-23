"""Record whether an ERP received, imported, or only had a file written.

Revision ID: f6d8a4c2e901
Revises: e4f1c7a9b2d6
"""
from alembic import op
import sqlalchemy as sa


revision = "f6d8a4c2e901"
down_revision = "e4f1c7a9b2d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("push_deliveries") as batch_op:
        batch_op.add_column(sa.Column("delivery_state", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("external_reference", sa.String(length=255), nullable=True))

    # Reclassify old receipts conservatively. A webhook 2xx never proved ERP
    # import; an agent ack only proved a local file write. Older file-export
    # rows without a destination had reported success despite writing nothing.
    bind = op.get_bind()
    rows = bind.execute(sa.text("""
        SELECT p.id, p.status, p.response_body, c.type, c.config
        FROM push_deliveries p JOIN connectors c ON c.id = p.connector_id
    """)).mappings().all()
    for row in rows:
        state = "failed" if row["status"] == "failed" else "queued" if row["status"] == "pending" else "received"
        status = row["status"]
        response = row["response_body"]
        if status == "success" and row["type"] == "desktop_agent":
            state = "file_written"
        elif status == "success" and row["type"] == "file_export":
            config = row["config"] or {}
            if isinstance(config, str):
                import json
                config = json.loads(config)
            if config.get("output_dir"):
                state = "file_written"
            else:
                state = "failed"
                status = "failed"
                response = "Legacy export generated without an output folder; no file was delivered."
        bind.execute(sa.text("""
            UPDATE push_deliveries SET status = :status, delivery_state = :state,
                response_body = :response WHERE id = :id
        """), {"status": status, "state": state, "response": response, "id": row["id"]})

    bind.execute(sa.text("""
        UPDATE documents SET status = 'approved' WHERE status = 'pushed'
        AND EXISTS (
            SELECT 1 FROM push_deliveries p
            WHERE p.document_id = documents.id AND p.status IN ('pending', 'failed')
        )
    """))


def downgrade() -> None:
    with op.batch_alter_table("push_deliveries") as batch_op:
        batch_op.drop_column("external_reference")
        batch_op.drop_column("delivery_state")
