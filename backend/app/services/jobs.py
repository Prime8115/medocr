"""The extraction job queue, kept in the database.

Ownership is a lease. A process claims a job with one conditional UPDATE, so
however many threads, processes or servers race for it, exactly one wins - on
Postgres in production and SQLite in the tests alike. The holder refreshes
its lease while it works (heartbeat); a lease left unrefreshed for
OCR_JOB_LEASE_SECONDS means the holder is gone, and the job goes back to the
queue for anyone to take. Every write the holder makes is fenced on still
holding the lease, so a holder that stalled and lost its job can never
overwrite the result of the process that took it over.
"""
import os
import socket
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.job import JOB_ACTIVE, JOB_DONE, JOB_FAILED, JOB_PENDING, JOB_RUNNING, OcrJob

# Who this process is, for the lease. Unique per process start, so a restarted
# server never mistakes its predecessor's leases for its own.
WORKER_ID = f"{socket.gethostname()[:30]}:{os.getpid()}:{uuid.uuid4().hex[:8]}"

# Jobs this process currently holds - the heartbeat keeps their leases alive.
_held: set = set()
_held_lock = threading.Lock()


def held_job_ids() -> List[str]:
    with _held_lock:
        return list(_held)


def _hold(job_id: str) -> None:
    with _held_lock:
        _held.add(job_id)


def let_go(job_id: str) -> None:
    with _held_lock:
        _held.discard(job_id)


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


def _update(*where):
    # The database alone decides which rows match - that is what makes claims
    # and fenced writes atomic. Re-checking in Python against already-loaded
    # objects is pointless and, on SQLite, a naive-vs-aware datetime crash.
    return update(OcrJob).where(*where).execution_options(synchronize_session=False)


def active_job(db: Session, document_id: str) -> Optional[OcrJob]:
    return (
        db.query(OcrJob)
        .filter(OcrJob.document_id == document_id, OcrJob.status.in_(JOB_ACTIVE))
        .first()
    )


def enqueue(db: Session, document_id: str, content_type: str, doc_type: Optional[str]) -> OcrJob:
    """Queue extraction for a document, due now. A document never has two
    active jobs - the database refuses a second one - so asking again returns
    the job already queued or running."""
    existing = active_job(db, document_id)
    if existing:
        return existing
    job = OcrJob(
        document_id=document_id,
        status=JOB_PENDING,
        content_type=content_type,
        doc_type=doc_type,
        run_after=_now(),
        busy_attempts=0,
        attempts=0,
    )
    try:
        with db.begin_nested():
            db.add(job)
    except IntegrityError:
        # Another request queued it between our check and our insert.
        return active_job(db, document_id)
    return job


def claim(db: Session, job_id: str, now: Optional[datetime] = None) -> bool:
    """Take a due job. True only for the one caller that won it."""
    now = now or _now()
    result = db.execute(
        _update(OcrJob.id == job_id, OcrJob.status == JOB_PENDING, OcrJob.run_after <= now)
        .values(status=JOB_RUNNING, locked_by=WORKER_ID, locked_at=now,
                attempts=OcrJob.attempts + 1, updated_at=now)
    )
    db.commit()
    if result.rowcount == 1:
        _hold(job_id)
        return True
    return False


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


def _fenced(job_id: str):
    return (OcrJob.id == job_id, OcrJob.status == JOB_RUNNING, OcrJob.locked_by == WORKER_ID)


def finish(db: Session, job_id: str, ok: bool, error: Optional[str] = None) -> bool:
    """Close a job this process holds. False - and nothing written - if the
    lease was lost meanwhile; the caller must then discard its own result."""
    result = db.execute(
        _update(*_fenced(job_id)).values(
            status=JOB_DONE if ok else JOB_FAILED, locked_by=None, locked_at=None,
            attempts=0, last_error=(error or "")[:2000] or None, updated_at=_now(),
        )
    )
    let_go(job_id)
    return result.rowcount == 1


def defer(db: Session, job_id: str, delay_seconds: float, error: str) -> bool:
    """Back to the queue, due again after `delay_seconds` (the AI was busy).
    Fenced like finish(); a deferral is a clean exit, so attempts reset."""
    now = _now()
    result = db.execute(
        _update(*_fenced(job_id)).values(
            status=JOB_PENDING, locked_by=None, locked_at=None, attempts=0,
            busy_attempts=OcrJob.busy_attempts + 1,
            run_after=now + timedelta(seconds=delay_seconds),
            last_error=error[:2000], updated_at=now,
        )
    )
    let_go(job_id)
    return result.rowcount == 1


def heartbeat(db: Session, job_ids: Iterable[str]) -> int:
    """Renew the leases this process holds on `job_ids`."""
    ids = list(job_ids)
    if not ids:
        return 0
    now = _now()
    result = db.execute(
        _update(OcrJob.id.in_(ids), OcrJob.status == JOB_RUNNING, OcrJob.locked_by == WORKER_ID)
        .values(locked_at=now)
    )
    db.commit()
    return result.rowcount


def release_expired(db: Session, lease_seconds: float, now: Optional[datetime] = None) -> int:
    """Return to the queue every running job whose holder stopped renewing its
    lease - a process that crashed, was killed, or was replaced by a deploy."""
    now = now or _now()
    result = db.execute(
        _update(OcrJob.status == JOB_RUNNING, OcrJob.locked_at < now - timedelta(seconds=lease_seconds))
        .values(status=JOB_PENDING, locked_by=None, locked_at=None, run_after=now, updated_at=now)
    )
    db.commit()
    return result.rowcount
