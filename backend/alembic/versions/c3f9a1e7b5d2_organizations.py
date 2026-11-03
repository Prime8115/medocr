"""Organisations, workspace access and licence plans.

Every existing shop becomes a workspace of an organisation of its own, named
after it, on the smallest plan that fits its active users; each user gets
access to their shop. No bill, lesson or setting moves.
(docs/ORG_USER_MANAGEMENT_PRD.md)

Revision ID: c3f9a1e7b5d2
Revises: a18c34d790f2
"""
import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "c3f9a1e7b5d2"
down_revision = "a18c34d790f2"
branch_labels = None
depends_on = None

# The plans as they stood when this ran (app/services/licensing.py), smallest first.
_PLANS = (("starter", 2), ("growth", 5), ("business", 15), ("enterprise", 50))


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("plan", sa.String(length=32), nullable=False, server_default="starter"),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "user_workspaces",
        sa.Column("user_id", sa.String(length=32), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("shop_id", sa.String(length=32), sa.ForeignKey("shops.id", ondelete="CASCADE"), primary_key=True),
    )
    with op.batch_alter_table("shops") as batch_op:
        batch_op.add_column(sa.Column("org_id", sa.String(length=32), nullable=True))
        batch_op.create_foreign_key("fk_shops_org_id", "organizations", ["org_id"], ["id"])
        batch_op.create_index("ix_shops_org_id", ["org_id"])
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("org_id", sa.String(length=32), nullable=True))
        batch_op.create_foreign_key("fk_users_org_id", "organizations", ["org_id"], ["id"])
        batch_op.create_index("ix_users_org_id", ["org_id"])

    bind = op.get_bind()
    orgs = sa.table("organizations", sa.column("id"), sa.column("name"), sa.column("plan"),
                    sa.column("is_active"), sa.column("created_at"), sa.column("updated_at"))
    links = sa.table("user_workspaces", sa.column("user_id"), sa.column("shop_id"))
    now = datetime.now(timezone.utc)
    for shop_id, name in bind.execute(sa.text("SELECT id, name FROM shops")).fetchall():
        active = bind.execute(sa.text("SELECT COUNT(*) FROM users WHERE shop_id = :s AND is_active = :t"),
                              {"s": shop_id, "t": True}).scalar() or 0
        plan = next((p for p, seats in _PLANS if seats >= active), "enterprise")
        org_id = uuid.uuid4().hex
        bind.execute(orgs.insert().values(id=org_id, name=name or "Organisation", plan=plan, is_active=True,
                                          created_at=now, updated_at=now))
        bind.execute(sa.text("UPDATE shops SET org_id = :o WHERE id = :s"), {"o": org_id, "s": shop_id})
        bind.execute(sa.text("UPDATE users SET org_id = :o WHERE shop_id = :s"), {"o": org_id, "s": shop_id})
        for (user_id,) in bind.execute(sa.text("SELECT id FROM users WHERE shop_id = :s"), {"s": shop_id}).fetchall():
            bind.execute(links.insert().values(user_id=user_id, shop_id=shop_id))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_index("ix_users_org_id")
        batch_op.drop_constraint("fk_users_org_id", type_="foreignkey")
        batch_op.drop_column("org_id")
    with op.batch_alter_table("shops") as batch_op:
        batch_op.drop_index("ix_shops_org_id")
        batch_op.drop_constraint("fk_shops_org_id", type_="foreignkey")
        batch_op.drop_column("org_id")
    op.drop_table("user_workspaces")
    op.drop_table("organizations")
