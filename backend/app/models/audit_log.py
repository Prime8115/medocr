from sqlalchemy import Column, String, JSON

from app.database import Base
from app.models.base import TimestampMixin, gen_uuid


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_log"

    id = Column(String(32), primary_key=True, default=gen_uuid)
    shop_id = Column(String(32), index=True, nullable=True)
    actor_id = Column(String(32), nullable=True)
    action = Column(String(64), nullable=False)  # e.g. document.approved, connector.created
    target = Column(String(128), nullable=True)
    detail = Column(JSON, nullable=True)
