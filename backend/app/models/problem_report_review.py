"""Triage state for a customer report; the original audit event is immutable."""
from sqlalchemy import Column, DateTime, ForeignKey, String, Text

from app.database import Base
from app.models.base import TimestampMixin


class ProblemReportReview(Base, TimestampMixin):
    __tablename__ = "problem_report_reviews"

    report_id = Column(String(32), ForeignKey("audit_log.id"), primary_key=True)
    status = Column(String(20), nullable=False, default="open")
    resolution_note = Column(Text, nullable=True)
    updated_by = Column(String(32), ForeignKey("users.id"), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
