from sqlalchemy import Column, String, Boolean, ForeignKey
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.base import TimestampMixin, gen_uuid


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id = Column(String(32), primary_key=True, default=gen_uuid)
    # Login-default workspace. Each signed session selects its workspace in JWT.
    shop_id = Column(String(32), ForeignKey("shops.id"), nullable=False, index=True)
    # The organisation the user belongs to; "owner" is its admin, "staff" a member.
    org_id = Column(String(32), ForeignKey("organizations.id"), nullable=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(32), default="staff", nullable=False)  # "owner" | "staff"
    is_active = Column(Boolean, default=True, nullable=False)
    is_platform_admin = Column(Boolean, default=False, nullable=False)

    shop = relationship("Shop", back_populates="users")
