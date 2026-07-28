"""Shared model helpers: UUID ids and timestamp columns."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime


def gen_uuid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
