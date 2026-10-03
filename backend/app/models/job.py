from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.database import Base
from app.models.base import TimestampMixin, gen_uuid

# pending -> running -> done | failed; running -> pending when the AI is busy
# (with run_after in the future) or when the process holding it died.
JOB_PENDING = "pending"
JOB_RUNNING = "running"
JOB_DONE = "done"
JOB_FAILED = "failed"
JOB_ACTIVE = (JOB_PENDING, JOB_RUNNING)


class OcrJob(Base, TimestampMixin):
    """One unit of extraction work for a document, kept in the database.

    It used to live only in the web process's memory - a FastAPI background
    task, and a threading.Timer while the AI was busy - so a restart or deploy
    dropped every scan in flight. A row here survives both: whatever was not
    finished is picked up again by the worker (services/worker.py).
    """

    __tablename__ = "ocr_jobs"

    id = Column(String(32), primary_key=True, default=gen_uuid)
    document_id = Column(String(40), ForeignKey("documents.id"), nullable=False, index=True)
    status = Column(String(16), default=JOB_PENDING, nullable=False, index=True)
    content_type = Column(String(64), nullable=False)
    # The type the user asked for; NULL = Auto (detect it).
    doc_type = Column(String(32), nullable=True)
    # How many times the AI was too busy for this scan already.
    busy_attempts = Column(Integer, default=0, nullable=False)
    run_after = Column(DateTime(timezone=True), nullable=False, index=True)
    locked_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
