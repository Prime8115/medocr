"""Recover documents left mid-processing (e.g. by a server restart or crash).

Background OCR jobs run in-process, so a restart loses any in-flight job and the
document would otherwise be stuck in 'queued'/'processing' forever. On startup we
mark those as 'failed' with a clear, retryable message; the app's auto-retry (or
the Retry button) then re-runs OCR on the stored image.
"""
from sqlalchemy.orm import Session

from app.models.document import Document
from app.services import lifecycle

_STUCK = (lifecycle.QUEUED, lifecycle.PROCESSING)
_MESSAGE = "Processing was interrupted (server restart). Please retry."


def recover_stuck_documents(db: Session) -> int:
    """Fail any documents stuck in queued/processing. Returns how many were reset."""
    stuck = db.query(Document).filter(Document.status.in_(_STUCK)).all()
    for doc in stuck:
        doc.status = lifecycle.FAILED
        doc.progress = None
        if not doc.error:
            doc.error = _MESSAGE
    if stuck:
        db.commit()
    return len(stuck)
