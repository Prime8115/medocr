from sqlalchemy import Column, ForeignKey, Integer, String, UniqueConstraint

from app.database import Base
from app.models.base import TimestampMixin, gen_uuid


class SupplierLabel(Base, TimestampMixin):
    """Where a supplier prints a field, learned from a reviewer's correction.

    A new supplier's bill leaves its LR number blank because it labels it
    "Docket No. & Date"; the reviewer types the number in; the words in front
    of it on the page are stored here, so that supplier's next bill is read
    there. Keyed by shop and the supplier's GSTIN, one row per field. See
    services/ocr/learned_labels.py for what is learned and how carefully.
    """
    __tablename__ = "supplier_labels"

    id = Column(String(32), primary_key=True, default=gen_uuid)
    shop_id = Column(String(32), ForeignKey("shops.id"), nullable=False, index=True)
    supplier_gstin = Column(String(15), nullable=False)
    field = Column(String(64), nullable=False)          # "invoice.lr_no"
    label = Column(String(128), nullable=False)         # "Docket No. & Date <num>"
    position = Column(String(8), nullable=False)        # "after" | "below"
    shape = Column(String(64), nullable=True)           # "A9" - what the value looks like
    words = Column(Integer, nullable=False, default=1)  # words in a text value
    tail = Column(Integer, nullable=True)  # below a label: words after it on its line
    # "blank" - the reader left it empty; "misread" - it read something else,
    # so a value at this label replaces what it reads, not only fills a gap.
    learned_from = Column(String(8), nullable=False, default="blank")
    learned_by = Column(String(32), nullable=True)
    times_used = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("shop_id", "supplier_gstin", "field", name="uq_supplier_label"),
    )
