from sqlalchemy import Column, ForeignKey, String, UniqueConstraint

from app.database import Base
from app.models.base import TimestampMixin, gen_uuid


class SupplierChoice(Base, TimestampMixin):
    """A reviewer's decision about one of a supplier's ambiguous fields,
    remembered so that supplier's next bill arrives already decided.

    Keyed by the supplier's GSTIN: Zydus prints both a "PO Number" and an
    "Order No"; once a shop has said which one is its PO, every later Zydus
    bill for that shop uses the same one. `option_label` is where on the bill
    the chosen value is printed ("PO Number"), not the value itself - the next
    bill's value will differ.
    """
    __tablename__ = "supplier_choices"

    id = Column(String(32), primary_key=True, default=gen_uuid)
    shop_id = Column(String(32), ForeignKey("shops.id"), nullable=False, index=True)
    supplier_gstin = Column(String(15), nullable=False)
    choice_id = Column(String(32), nullable=False)
    option_label = Column(String(128), nullable=False)
    decided_by = Column(String(32), nullable=True)

    __table_args__ = (
        UniqueConstraint("shop_id", "supplier_gstin", "choice_id", name="uq_supplier_choice"),
    )
