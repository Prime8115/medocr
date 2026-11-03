"""The customer company (organisation) and which workspaces each user may open.

An organisation holds the licence (app/services/licensing.py) and owns one or
more workspaces - the `shops` table, each with its own bills, suppliers and
settings. A user belongs to one organisation; `users.shop_id` is the workspace
they are working in now, so every existing tenancy check is unchanged.
See docs/ORG_USER_MANAGEMENT_PRD.md.
"""
from sqlalchemy import Boolean, Column, Date, ForeignKey, String
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.base import TimestampMixin, gen_uuid


class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"

    id = Column(String(32), primary_key=True, default=gen_uuid)
    name = Column(String(255), nullable=False)
    plan = Column(String(32), nullable=False, default="starter")
    valid_until = Column(Date, nullable=True)        # none = no expiry
    is_active = Column(Boolean, nullable=False, default=True)

    workspaces = relationship("Shop", back_populates="organization")


class UserWorkspace(Base):
    """A member's access to a workspace. Org admins open every workspace of
    their organisation without a row here."""
    __tablename__ = "user_workspaces"

    user_id = Column(String(32), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    shop_id = Column(String(32), ForeignKey("shops.id", ondelete="CASCADE"), primary_key=True)
