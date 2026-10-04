"""The database job queue: claiming, leases, fencing, poison scans, the worker."""
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.models.document import Document
from app.models.job import OcrJob
from app.services import jobs
from app.services.worker import Worker
from tests.conftest import register_and_login

RESULT = {"schema_version": "1.0", "doc_type": "invoice", "fields": {}, "meta": {}}
LEASE = 90


def _later(seconds):
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


@pytest.fixture
def doc_id(client, db_session):
    headers = register_and_login(client)
    shop_id = client.get("/v1/auth/me", headers=headers).json()["shop_id"]
    s = db_session()
    try:
        doc = Document(shop_id=shop_id, doc_type="invoice", status="queued", image_ref="local://x.pdf")
        s.add(doc)
        s.commit()
        return doc.id
    finally:
        s.close()


@pytest.fixture
def job_id(db_session, doc_id):
    s = db_session()
    try:
        job = jobs.enqueue(s, doc_id, "application/pdf", None)
        s.commit()
        return job.id
    finally:
        s.close()


def _job(db_session, job_id):
    s = db_session()
    try:
        job = s.get(OcrJob, job_id)
        s.expunge(job)
        return job
    finally:
        s.close()


def test_only_one_claim_wins(db_session, job_id):
    s = db_session()
    try:
        assert jobs.claim(s, job_id) is True
        assert jobs.claim(s, job_id) is False  # already running
        job = _job(db_session, job_id)
        assert job.locked_by == jobs.WORKER_ID and job.attempts == 1
    finally:
        jobs.let_go(job_id)
        s.close()


def test_the_database_refuses_a_second_active_job(db_session, doc_id, job_id):
    s = db_session()
    try:
        # Through the service: the existing job comes back.
        assert jobs.enqueue(s, doc_id, "application/pdf", None).id == job_id
        # Behind the service's back: the unique index still says no.
        s.add(OcrJob(document_id=doc_id, status="pending", content_type="x",
                     run_after=datetime.now(timezone.utc), busy_attempts=0, attempts=0))
        with pytest.raises(IntegrityError):
            s.commit()
    finally:
        s.close()


def test_a_finished_job_frees_the_document_for_a_new_one(db_session, doc_id, job_id):
    s = db_session()
    try:
        jobs.claim(s, job_id)
        assert jobs.finish(s, job_id, ok=True)
        s.commit()
        assert jobs.enqueue(s, doc_id, "application/pdf", None).id != job_id
    finally:
        s.close()


def test_a_deferred_job_is_not_due_until_its_wait_is_over(db_session, job_id):
    s = db_session()
    try:
        jobs.claim(s, job_id)
        assert jobs.defer(s, job_id, 60, "busy")
        s.commit()
        assert jobs.due_job_ids(s, 10) == []
        assert jobs.due_job_ids(s, 10, now=_later(61)) == [job_id]
        assert jobs.claim(s, job_id) is False           # not yet
        assert jobs.claim(s, job_id, now=_later(61)) is True
        job = _job(db_session, job_id)
        assert job.busy_attempts == 1
        assert job.attempts == 1  # the deferral was a clean exit
    finally:
        jobs.let_go(job_id)
        s.close()


def test_an_expired_lease_goes_back_to_the_queue_a_live_one_does_not(db_session, job_id):
    s = db_session()
    try:
        jobs.claim(s, job_id)
        assert jobs.release_expired(s, LEASE) == 0                      # renewed just now
        assert jobs.release_expired(s, LEASE, now=_later(LEASE + 1)) == 1
        job = _job(db_session, job_id)
        assert job.status == "pending" and job.locked_by is None
    finally:
        jobs.let_go(job_id)
        s.close()


def test_the_heartbeat_keeps_a_long_scan_alive(db_session, job_id):
    s = db_session()
    try:
        jobs.claim(s, job_id)
        # A long AI call: the lease is 5 seconds from expiring.
        s.execute(jobs._update(OcrJob.id == job_id).values(
            locked_at=datetime.now(timezone.utc) - timedelta(seconds=LEASE - 5)))
        s.commit()
        assert jobs.heartbeat(s, jobs.held_job_ids()) == 1
        assert jobs.release_expired(s, LEASE, now=_later(10)) == 0  # still ours
    finally:
        jobs.let_go(job_id)
        s.close()


def test_a_holder_that_lost_its_lease_writes_nothing(db_session, job_id):
    """Fencing: A stalls, its lease expires, B takes over. A's late result must
    not land on top of B's."""
    s = db_session()
    try:
        jobs.claim(s, job_id)
        jobs.release_expired(s, LEASE, now=_later(LEASE + 1))
        s.execute(  # B, another process, takes it
            jobs._update(OcrJob.id == job_id).values(status="running", locked_by="other:1:b")
        )
        s.commit()

        assert jobs.finish(s, job_id, ok=True) is False
        assert jobs.defer(s, job_id, 60, "busy") is False
        s.commit()
        job = _job(db_session, job_id)
        assert job.status == "running" and job.locked_by == "other:1:b"
    finally:
        s.close()


def test_a_lost_lease_discards_the_result(client, db_session, doc_id, job_id, monkeypatch):
    from app.api import documents as documents_api

    def read_slowly(*_a, **_k):
        # While "we" read, our lease expires and another process takes the job.
        s = db_session()
        try:
            s.execute(jobs._update(OcrJob.id == job_id).values(locked_by="other:1:b"))
            s.commit()
        finally:
            s.close()
        return RESULT

    monkeypatch.setattr(documents_api, "process_document", read_slowly)
    monkeypatch.setattr(documents_api.storage, "load", lambda _ref: b"%PDF")
    assert documents_api.run_due_jobs() == 1

    s = db_session()
    try:
        assert s.get(Document, doc_id).status != "needs_review"  # our result was dropped
        assert s.get(OcrJob, job_id).locked_by == "other:1:b"
    finally:
        s.close()


def test_a_scan_that_keeps_killing_its_process_is_given_up(client, db_session, doc_id, job_id, monkeypatch):
    from app.api import documents as documents_api

    called = []
    monkeypatch.setattr(documents_api, "process_document", lambda *a, **k: called.append(1))
    s = db_session()
    try:
        # Its process died on it max_attempts times: claimed, never finished.
        s.execute(jobs._update(OcrJob.id == job_id).values(attempts=settings.ocr_job_max_attempts))
        s.commit()
    finally:
        s.close()

    assert documents_api.run_due_jobs() == 1
    s = db_session()
    try:
        doc = s.get(Document, doc_id)
        job = s.get(OcrJob, job_id)
        assert called == []  # not tried again
        assert doc.status == "failed" and doc.error == documents_api.FAILED_MESSAGE
        assert job.status == "failed" and "Gave up" in job.last_error
    finally:
        s.close()


def test_the_worker_finishes_due_work_on_its_own(client, db_session, doc_id, job_id, monkeypatch):
    from app.api import documents as documents_api

    monkeypatch.setattr(documents_api, "process_document", lambda *_a, **_k: RESULT)
    monkeypatch.setattr(documents_api.storage, "load", lambda _ref: b"%PDF")
    worker = Worker(poll_seconds=30, lease_seconds=LEASE, max_workers=2)
    try:
        assert worker.tick() == 1
        # The pool runs it in the background. Wait for it to finish before
        # looking: the in-memory test database is ONE connection shared by every
        # thread, so reading while the job writes could roll its transaction back.
        for _ in range(200):
            with worker._lock:
                if not worker._inflight:
                    break
            time.sleep(0.05)
        assert _job(db_session, job_id).status == "done"
        s = db_session()
        try:
            assert s.get(Document, doc_id).status == "needs_review"
        finally:
            s.close()
    finally:
        worker.stop()


def test_a_lease_must_outlive_several_heartbeats():
    with pytest.raises(ValueError):
        Worker(poll_seconds=60, lease_seconds=90, max_workers=1)


def test_a_missing_stored_file_fails_the_scan_cleanly(client, db_session, doc_id, job_id, monkeypatch):
    from app.api import documents as documents_api

    def gone(_ref):
        raise FileNotFoundError("x.pdf")

    monkeypatch.setattr(documents_api.storage, "load", gone)
    assert documents_api.run_due_jobs() == 1
    s = db_session()
    try:
        doc = s.get(Document, doc_id)
        assert doc.status == "failed" and doc.error == documents_api.FAILED_MESSAGE
        assert s.get(OcrJob, job_id).status == "failed"
    finally:
        s.close()
