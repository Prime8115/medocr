from sqlalchemy import Column, String, JSON
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.base import TimestampMixin, gen_uuid


class Shop(Base, TimestampMixin):
    __tablename__ = "shops"

    id = Column(String(32), primary_key=True, default=gen_uuid)
    name = Column(String(255), nullable=False)
    settings = Column(JSON, default=dict)

    users = relationship("User", back_populates="shop", cascade="all, delete-orphan")
