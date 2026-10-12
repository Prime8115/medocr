from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, UniqueConstraint

from app.database import Base
from app.models.base import TimestampMixin, gen_uuid


class SupplierColumnLesson(Base, TimestampMixin):
    """A supplier's line-item column, as a reviewer's corrections showed it.

    "is": the column headed `heading` holds `field` (Lupin's "Trade
    Price/Unit" is the PTR). "not": it does not, whatever its wording suggests
    (Lupin's "TP/ PTR Value" is the line's value, never the PTR). Keyed by
    shop, supplier GSTIN, heading and field. A lesson a reviewer has to undo
    twice, or that twice leaves a bill not adding up, is retired.
    See services/supplier_profile.py.
    """
    __tablename__ = "supplier_column_lessons"

    id = Column(String(32), primary_key=True, default=gen_uuid)
    shop_id = Column(String(32), ForeignKey("shops.id"), nullable=False, index=True)
    supplier_gstin = Column(String(15), nullable=False)
    heading = Column(String(128), nullable=False)       # as compared (lessons.norm)
    heading_printed = Column(String(128), nullable=True)  # as printed, for showing
    field = Column(String(32), nullable=False)          # "ptr"
    kind = Column(String(4), nullable=False)            # "is" | "not"
    learned_by = Column(String(32), nullable=True)
    times_used = Column(Integer, nullable=False, default=0)
    times_undone = Column(Integer, nullable=False, default=0)
    times_failed = Column(Integer, nullable=False, default=0)
    active = Column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("shop_id", "supplier_gstin", "heading", "field", "kind", name="uq_supplier_column_lesson"),
    )
