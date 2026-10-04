from sqlalchemy import Column, String, Float, JSON, ForeignKey, Index, Text

from app.database import Base
from app.models.base import TimestampMixin, gen_uuid


def gen_doc_id() -> str:
    return "doc_" + gen_uuid()[:12]


# Lifecycle: queued -> processing -> needs_review -> approved -> pushed
#            (any stage) -> failed
class Document(Base, TimestampMixin):
    __tablename__ = "documents"

    id = Column(String(40), primary_key=True, default=gen_doc_id)
    shop_id = Column(String(32), ForeignKey("shops.id"), nullable=False, index=True)
    doc_type = Column(String(32), default="prescription", nullable=False)  # prescription | invoice
    # The type the user picked at upload; NULL means "Auto". doc_type above holds
    # a placeholder until the scan is read, so it must never stand in for this:
    # a retry that did so forced Auto-uploaded invoices through the prescription
    # reader.
    requested_doc_type = Column(String(32), nullable=True)
    status = Column(String(32), default="queued", nullable=False, index=True)
    # SHA-256 of the file as uploaded: the same file sent twice opens the
    # earlier scan.
    content_hash = Column(String(64), nullable=True)
    image_ref = Column(String(512), nullable=True)
    overall_confidence = Column(Float, nullable=True)
    payload = Column(JSON, nullable=True)  # extracted structured data
    progress = Column(String(16), nullable=True)  # e.g. "12/60" while processing
    error = Column(Text, nullable=True)
    created_by = Column(String(32), ForeignKey("users.id"), nullable=True)

    __table_args__ = (Index("ix_documents_shop_content_hash", "shop_id", "content_hash"),)
