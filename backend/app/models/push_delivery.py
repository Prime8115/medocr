from sqlalchemy import Column, String, Integer, JSON, ForeignKey, Text

from app.database import Base
from app.models.base import TimestampMixin, gen_uuid


class PushDelivery(Base, TimestampMixin):
    __tablename__ = "push_deliveries"

    id = Column(String(32), primary_key=True, default=gen_uuid)
    document_id = Column(String(40), ForeignKey("documents.id"), nullable=False, index=True)
    connector_id = Column(String(32), ForeignKey("connectors.id"), nullable=False, index=True)
    status = Column(String(32), default="pending", nullable=False)  # pending | success | failed
    request_payload = Column(JSON, nullable=True)
    response_body = Column(Text, nullable=True)
    response_code = Column(Integer, nullable=True)
    attempts = Column(Integer, default=0, nullable=False)
    idempotency_key = Column(String(64), index=True, nullable=True)
