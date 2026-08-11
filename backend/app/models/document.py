from sqlalchemy import Column, String, Float, JSON, ForeignKey, Text

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
    status = Column(String(32), default="queued", nullable=False, index=True)
    image_ref = Column(String(512), nullable=True)
    overall_confidence = Column(Float, nullable=True)
    payload = Column(JSON, nullable=True)  # extracted structured data
    progress = Column(String(16), nullable=True)  # e.g. "12/60" while processing
    error = Column(Text, nullable=True)
    created_by = Column(String(32), ForeignKey("users.id"), nullable=True)
