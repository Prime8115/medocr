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
            due = jobs.due_job_ids(db, limit=50)
        finally:
            db.close()

        started = 0
        for job_id in due:
            with self._lock:
                if job_id in self._inflight:
                    continue
                self._inflight.add(job_id)
            self._pool.submit(self._run, job_id)
            started += 1
        return started

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
