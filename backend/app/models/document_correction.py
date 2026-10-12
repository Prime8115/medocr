from sqlalchemy import Column, ForeignKey, Integer, String, Text

from app.database import Base
from app.models.base import TimestampMixin, gen_uuid


class DocumentCorrection(Base, TimestampMixin):
    """One field a reviewer changed on a reading: what was read, what they put.

    The audit log kept only WHICH fields changed, so a correction could never
    be turned into a lesson (or a test). This keeps the values, the supplier,
    and where on the bill the reader took the value from - the printed column
    heading for a line field ("Trade Price/Unit") - which is what a supplier's
    lesson is built from (services/supplier_profile.py).
    """
    __tablename__ = "document_corrections"

    id = Column(String(32), primary_key=True, default=gen_uuid)
    shop_id = Column(String(32), ForeignKey("shops.id"), nullable=False, index=True)
    document_id = Column(String(32), ForeignKey("documents.id"), nullable=False, index=True)
    supplier_gstin = Column(String(15), nullable=True, index=True)
    # "invoice.po_no", or a line field as "line.ptr" with its index in `line`.
    field = Column(String(64), nullable=False)
    line = Column(Integer, nullable=True)
    value_read = Column(Text, nullable=True)
    value_corrected = Column(Text, nullable=True)
    # The heading the reader took a line field from ("Trade Price/Unit").
    source = Column(String(128), nullable=True)
    pipeline = Column(String(32), nullable=True)
    corrected_by = Column(String(32), nullable=True)
