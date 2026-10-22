"""One guarded manual Tally import session per document and connector.

This records what was offered for import, not a claim that Tally accepted it.
"""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from app.database import Base
from app.models.base import TimestampMixin, gen_uuid


class TallyExport(Base, TimestampMixin):
    __tablename__ = "tally_exports"

    id = Column(String(32), primary_key=True, default=gen_uuid)
    shop_id = Column(String(32), ForeignKey("shops.id"), nullable=False, index=True)
    document_id = Column(String(40), ForeignKey("documents.id"), nullable=False, index=True)
    connector_id = Column(String(32), ForeignKey("connectors.id"), nullable=False, index=True)
    status = Column(String(32), nullable=False)  # downloaded | not_imported | reported_imported
    xml_sha256 = Column(String(64), nullable=False)
    idempotency_key = Column(String(64), nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    download_count = Column(Integer, nullable=False, default=1)
    line_count = Column(Integer, nullable=False)
    total_amount = Column(String(32), nullable=False)
    exported_by = Column(String(32), ForeignKey("users.id"), nullable=False)
    reported_by = Column(String(32), ForeignKey("users.id"), nullable=True)
    reported_at = Column(DateTime(timezone=True), nullable=True)
    external_reference = Column(String(128), nullable=True)
    report_reason = Column(String(500), nullable=True)

    __table_args__ = (UniqueConstraint("document_id", "connector_id", name="uq_tally_export_document_connector"),)
