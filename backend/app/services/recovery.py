"""Resume documents left mid-processing (e.g. by a server restart or a deploy).

Extraction work is kept in the ocr_jobs table, so a restart no longer loses it:
on startup every job the dead process was running goes back to the queue, and
any document still marked queued/processing without a job - from before the
job table existed - gets one. The worker then finishes them; nobody has to
press "Try again". Only a document with no stored file to read is failed.
"""
from sqlalchemy.orm import Session

from app.models.document import Document
from app.services import jobs, lifecycle

_STUCK = (lifecycle.QUEUED, lifecycle.PROCESSING)
_MESSAGE = "Processing was interrupted. Please try again."


def recover_stuck_documents(db: Session) -> int:
    """Re-queue interrupted work. Returns how many documents will be resumed.

    Assumes it runs in the only process serving the API (one uvicorn worker,
    as the Dockerfile starts it): every 'running' job then belongs to a process
    that no longer exists.
    """
    jobs.release_running(db)

    resumed = 0
    for doc in db.query(Document).filter(Document.status.in_(_STUCK)).all():
        if not jobs.active_job(db, doc.id):
            if not doc.image_ref:
                doc.status = lifecycle.FAILED
                doc.progress = None
                doc.error = doc.error or _MESSAGE
                continue
            jobs.enqueue(db, doc.id, jobs.infer_content_type(doc.image_ref), doc.requested_doc_type)
        if doc.status == lifecycle.PROCESSING:
            doc.status = lifecycle.QUEUED
            doc.progress = None
        resumed += 1
    db.commit()
    return resumed
