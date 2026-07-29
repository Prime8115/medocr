from sqlalchemy import Column, String, Float, Integer, ForeignKey, Index

from app.database import Base
from app.models.base import TimestampMixin, gen_uuid


class InventoryItem(Base, TimestampMixin):
    """A single stock item in a shop's inventory catalog.

    Populated by CSV upload or (future) a synced connection to the shop's
    software. `normalized_name` is precomputed for fast fuzzy matching.
    """
    __tablename__ = "inventory_items"

    id = Column(String(32), primary_key=True, default=gen_uuid)
    shop_id = Column(String(32), ForeignKey("shops.id"), nullable=False, index=True)
    sku = Column(String(64), nullable=True)          # shop's own item code, if any
    name = Column(String(255), nullable=False)       # as it appears in their software
    normalized_name = Column(String(255), nullable=False, index=True)
    composition = Column(String(255), nullable=True, index=True)  # salt/generic, e.g. "paracetamol 500"
    strength = Column(String(64), nullable=True)
    pack = Column(String(64), nullable=True)
    mrp = Column(Float, nullable=True)
    stock_qty = Column(Float, nullable=True, default=0)

    __table_args__ = (Index("ix_inventory_shop_norm", "shop_id", "normalized_name"),)
