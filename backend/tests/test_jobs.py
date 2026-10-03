"""The database job queue: exactly-once claiming, due times, stalled jobs, worker."""
import time
from datetime import datetime, timedelta, timezone

from app.models.document import Document
from app.models.job import OcrJob
from app.services import jobs
from app.services.worker import Worker
from tests.conftest import register_and_login

RESULT = {"schema_version": "1.0", "doc_type": "invoice", "fields": {}, "meta": {}}


def _doc(client, db_session, **kw):
    headers = register_and_login(client)
    shop_id = client.get("/v1/auth/me", headers=headers).json()["shop_id"]
    s = db_session()
    try:
        doc = Document(shop_id=shop_id, doc_type="invoice", status="queued", image_ref="local://x.pdf", **kw)
        s.add(doc)
        s.commit()
        return doc.id
    finally:
        s.close()


def test_only_one_claim_wins(client, db_session):
    doc_id = _doc(client, db_session)
    s = db_session()
    try:
        job = jobs.enqueue(s, doc_id, "application/pdf", None)
        s.commit()
        assert jobs.claim(s, job.id) is True
        assert jobs.claim(s, job.id) is False  # already running
    finally:
        s.close()


def test_a_deferred_job_is_not_due_until_its_wait_is_over(client, db_session):
    doc_id = _doc(client, db_session)
    s = db_session()
    try:
        job = jobs.enqueue(s, doc_id, "application/pdf", None)
        jobs.defer(s, job, 60, "busy")
        s.commit()
        assert jobs.due_job_ids(s, 10) == []
        later = datetime.now(timezone.utc) + timedelta(seconds=61)
        assert jobs.due_job_ids(s, 10, now=later) == [job.id]
        assert jobs.claim(s, job.id) is False          # not yet
        assert jobs.claim(s, job.id, now=later) is True
    finally:
        s.close()


def test_a_stalled_job_goes_back_to_the_queue_but_a_live_one_does_not(client, db_session):
    doc_id = _doc(client, db_session)
    s = db_session()
    try:
        job = jobs.enqueue(s, doc_id, "application/pdf", None)
        s.commit()
        jobs.claim(s, job.id)
        # Locked just now: still alive.
        assert jobs.release_running(s, stale_before=datetime.now(timezone.utc) - timedelta(minutes=15)) == 0
        # Locked before the cutoff: presumed dead.
        assert jobs.release_running(s, stale_before=datetime.now(timezone.utc) + timedelta(seconds=1)) == 1
        s.expire_all()
        assert s.get(OcrJob, job.id).status == "pending"
    finally:
        s.close()


def test_the_worker_finishes_due_work_on_its_own(client, db_session, monkeypatch):
    from app.api import documents as documents_api

    monkeypatch.setattr(documents_api, "process_document", lambda *_a, **_k: RESULT)
    monkeypatch.setattr(documents_api.storage, "load", lambda _ref: b"%PDF")
    doc_id = _doc(client, db_session)
    s = db_session()
    try:
        jobs.enqueue(s, doc_id, "application/pdf", None)
        s.commit()
    finally:
        s.close()

    worker = Worker(poll_seconds=60, stale_seconds=900, max_workers=2)
    try:
        assert worker.tick() == 1
        for _ in range(100):  # the pool runs it in the background
            s = db_session()
            try:
                if s.get(Document, doc_id).status == "needs_review":
                    break
            finally:
                s.close()
            time.sleep(0.05)
        s = db_session()
        try:
            assert s.get(Document, doc_id).status == "needs_review"
            assert s.query(OcrJob).one().status == "done"
        finally:
            s.close()
    finally:
        worker.stop()


def test_a_missing_stored_file_fails_the_scan_cleanly(client, db_session, monkeypatch):
    from app.api import documents as documents_api

    def gone(_ref):
        raise FileNotFoundError("x.pdf")

    monkeypatch.setattr(documents_api.storage, "load", gone)
    doc_id = _doc(client, db_session)
    s = db_session()
    try:
        jobs.enqueue(s, doc_id, "application/pdf", None)
        s.commit()
    finally:
        s.close()
    assert documents_api.run_due_jobs() == 1
    s = db_session()
    try:
        doc = s.get(Document, doc_id)
        assert doc.status == "failed"
        assert doc.error == documents_api.FAILED_MESSAGE
        assert s.query(OcrJob).one().status == "failed"
    finally:
        s.close()
