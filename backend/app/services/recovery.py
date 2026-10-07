"""Resume documents left mid-processing (e.g. by a server restart or a deploy).

Extraction work is kept in the ocr_jobs table, so a restart no longer loses it:
on startup every job the dead process was running goes back to the queue, and
any document still marked queued/processing without a job - from before the
job table existed - gets one. The worker then finishes them; nobody has to
press "Try again". Only a document with no stored file to read is failed.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.document import Document
from app.services import jobs, lifecycle

_STUCK = (lifecycle.QUEUED, lifecycle.PROCESSING)
_MESSAGE = "Processing was interrupted. Please try again."


def recover_stuck_documents(db: Session, older_than_seconds: Optional[float] = None) -> int:
    """Give every stuck document a job. Returns how many will be resumed.

    At startup every such document is stuck. While running - the worker sweeps
    every minute - only one untouched for `older_than_seconds` is: an upload
    commits its document a moment before it queues the job, and a document in
    that moment must not be given a second one.
    """
    resumed = 0
    query = db.query(Document).filter(Document.status.in_(_STUCK))
    if older_than_seconds is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)
        query = query.filter(Document.updated_at < cutoff)
    for doc in query.all():
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
