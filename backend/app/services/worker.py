"""Background worker: runs whatever extraction work is due in the ocr_jobs table.

An upload starts its own job at once; the worker is for everything after that
- a busy-AI retry whose wait is over, a scan whose process died mid-way, and
the work found at startup. One thread polls; a small pool does the reading.
Each pass also renews the leases this process holds (the heartbeat) and
returns to the queue any job whose holder stopped renewing - in this process
or any other. Each job is claimed atomically (services/jobs.py), so a job the
upload already started is skipped here rather than read twice.
"""
import concurrent.futures as cf
import logging
import threading
import time
from typing import Optional, Set

from app.config import settings

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, poll_seconds: float, lease_seconds: float, max_workers: int):
        if poll_seconds * 3 > lease_seconds:
            raise ValueError("the poll must renew a lease several times before it expires")
        self.poll_seconds = poll_seconds
        self.lease_seconds = lease_seconds
        self._pool = cf.ThreadPoolExecutor(max_workers=max(1, max_workers), thread_name_prefix="ocr-job")
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._inflight: Set[str] = set()
        self._lock = threading.Lock()
        self._last_sweep = time.monotonic()   # startup recovery has just swept

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="ocr-worker", daemon=True)
        self._thread.start()
        log.info("ocr worker started (poll %.0fs)", self.poll_seconds)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _loop(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - the loop must outlive any one bad pass
                log.exception("ocr worker pass failed")

    def tick(self) -> int:
        """One pass: renew our leases, free expired ones, start what is due."""
        from app.api import documents as documents_api  # runtime import: avoids a cycle
        from app.services import jobs

        db = documents_api.SessionLocal()
        try:
            jobs.heartbeat(db, jobs.held_job_ids())
            freed = jobs.release_expired(db, self.lease_seconds)
            if freed:
                log.warning("ocr worker: %d job(s) whose holder stopped renewing returned to the queue", freed)
            self._sweep_orphans(db)
            due = jobs.due_job_ids(db, limit=50)
        finally:
            db.close()

        # A read past its deadline goes to manual entry instead of spinning on.
        for job_id in jobs.overdue_job_ids(settings.ocr_job_deadline_seconds):
            jobs.let_go(job_id)   # no more heartbeats: this read is over
            # Its own thread: the pool may be full of exactly such stuck reads.
            threading.Thread(target=self._give_up, args=(job_id,), name="ocr-overdue", daemon=True).start()

        started = 0
        for job_id in due:
            with self._lock:
                if job_id in self._inflight:
                    continue
                self._inflight.add(job_id)
            self._pool.submit(self._run, job_id)
            started += 1
        return started

    @staticmethod
    def _give_up(job_id: str) -> None:
        from app.api import documents as documents_api

        try:
            documents_api.give_up_overdue(job_id)
        except Exception:  # noqa: BLE001
            log.exception("ocr job %s: could not hand the overdue scan to manual entry", job_id)

    def _sweep_orphans(self, db) -> None:
        """Documents left queued or processing with no job - an upload that
        died between saving the document and queueing it - are queued again.
        Startup did this only once; now nothing waits for a restart."""
        from app.services.recovery import recover_stuck_documents

        now = time.monotonic()
        if now - self._last_sweep < settings.ocr_orphan_sweep_seconds:
            return
        self._last_sweep = now
        try:
            recover_stuck_documents(db, older_than_seconds=settings.ocr_orphan_min_age_seconds)
        except Exception:  # noqa: BLE001 - a failed sweep waits for the next one
            db.rollback()
            log.exception("ocr worker: orphan sweep failed")

    def _run(self, job_id: str) -> None:
        from app.api import documents as documents_api

        try:
            documents_api.run_job(job_id)
        except Exception:  # noqa: BLE001
            log.exception("ocr job %s crashed", job_id)
        finally:
            with self._lock:
                self._inflight.discard(job_id)


def build_worker() -> Worker:
    return Worker(
        poll_seconds=settings.ocr_worker_poll_seconds,
        lease_seconds=settings.ocr_job_lease_seconds,
        max_workers=settings.ocr_max_concurrent_jobs,
    )
