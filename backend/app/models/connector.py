from sqlalchemy import Column, String, Boolean, JSON, ForeignKey

from app.database import Base
from app.models.base import TimestampMixin, gen_uuid


class Connector(Base, TimestampMixin):
    __tablename__ = "connectors"

    id = Column(String(32), primary_key=True, default=gen_uuid)
    shop_id = Column(String(32), ForeignKey("shops.id"), nullable=False, index=True)
    type = Column(String(32), nullable=False)  # webhook | file_export | desktop_agent
    name = Column(String(255), nullable=False)
    config = Column(JSON, default=dict)
    secret_ref = Column(String(255), nullable=True)  # HMAC secret (encrypt at rest in prod)
    enabled = Column(Boolean, default=True, nullable=False)
