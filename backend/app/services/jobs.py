"""The extraction job queue, kept in the database.

Claiming is one conditional UPDATE (`... WHERE id = :id AND status = 'pending'`),
so however many threads or processes race for a job, exactly one gets it - on
Postgres in production and on SQLite in the tests alike, with no locking
features either has to support.
"""
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.job import JOB_ACTIVE, JOB_DONE, JOB_FAILED, JOB_PENDING, JOB_RUNNING, OcrJob


def infer_content_type(ref: str) -> str:
    """The stored file's type, from its name - all a retry has to go on."""
    r = (ref or "").lower()
    if r.endswith(".pdf"):
        return "application/pdf"
    if r.endswith(".png"):
        return "image/png"
    if r.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def active_job(db: Session, document_id: str) -> Optional[OcrJob]:
    return (
        db.query(OcrJob)
        .filter(OcrJob.document_id == document_id, OcrJob.status.in_(JOB_ACTIVE))
        .first()
    )


def enqueue(db: Session, document_id: str, content_type: str, doc_type: Optional[str]) -> OcrJob:
    """Queue extraction for a document, due now. A document never has two
    active jobs: asking again returns the one already queued or running."""
    existing = active_job(db, document_id)
    if existing:
        return existing
    job = OcrJob(
        document_id=document_id,
        status=JOB_PENDING,
        content_type=content_type,
        doc_type=doc_type,
        run_after=_now(),
    )
    db.add(job)
    db.flush()
    return job


def claim(db: Session, job_id: str, now: Optional[datetime] = None) -> bool:
    """Take a due job for this worker. True only for the one caller that won."""
    now = now or _now()
    result = db.execute(
        update(OcrJob)
        .where(OcrJob.id == job_id, OcrJob.status == JOB_PENDING, OcrJob.run_after <= now)
        .values(status=JOB_RUNNING, locked_at=now, updated_at=now)
        # The database alone decides which rows match - that is what makes the
        # claim atomic. Re-checking in Python against already-loaded objects is
        # both pointless and, on SQLite, a naive-vs-aware datetime crash.
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return result.rowcount == 1


def due_job_ids(db: Session, limit: int, now: Optional[datetime] = None) -> List[str]:
    now = now or _now()
    rows = (
        db.query(OcrJob.id)
        .filter(OcrJob.status == JOB_PENDING, OcrJob.run_after <= now)
        .order_by(OcrJob.run_after)
        .limit(limit)
        .all()
    )
    return [r[0] for r in rows]


def defer(db: Session, job: OcrJob, delay_seconds: float, error: str) -> None:
    """Back to the queue, due again after `delay_seconds` (the AI was busy)."""
    now = _now()
    job.status = JOB_PENDING
    job.busy_attempts = (job.busy_attempts or 0) + 1
    job.run_after = now + timedelta(seconds=delay_seconds)
    job.locked_at = None
    job.last_error = error[:2000]


def finish(db: Session, job: OcrJob, ok: bool, error: Optional[str] = None) -> None:
    job.status = JOB_DONE if ok else JOB_FAILED
    job.locked_at = None
    job.last_error = (error or "")[:2000] or None


def release_running(db: Session, stale_before: Optional[datetime] = None) -> int:
    """Return running jobs to the queue: all of them at startup (the process
    that held them is gone), or those locked before `stale_before` while
    running (a worker that died without the whole process going)."""
    q = update(OcrJob).where(OcrJob.status == JOB_RUNNING)
    if stale_before is not None:
        q = q.where(OcrJob.locked_at < stale_before)
    result = db.execute(
        q.values(status=JOB_PENDING, locked_at=None, run_after=_now())
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return result.rowcount
