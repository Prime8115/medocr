"""A good reading is never thrown away, and no scan stays stuck."""
import time
from datetime import datetime, timedelta, timezone

import pytest

import app.services.ocr as ocr
from app.models.document import Document
from app.models.job import OcrJob
from app.schemas.extraction import validate_fields
from app.services import jobs
from app.services.recovery import recover_stuck_documents
from tests.test_jobs import RESULT, doc_id, job_id  # noqa: F401 - fixtures


def _f(v):
    return {"value": v, "confidence": 0.9}


READING = {"invoice": {"invoice_no": _f("INV-1"), "total_amount": _f("100.00")},
           "line_items": [{"description": _f("X"), "quantity": _f("1"), "rate": _f("100.00"),
                           "amount": _f("100.00")}]}


# --- what the AI hands back --------------------------------------------------------

def test_one_answer_wrapped_in_a_list_is_read():
    assert validate_fields("invoice", [READING])["invoice"]["invoice_no"]["value"] == "INV-1"


@pytest.mark.parametrize("answer", [[READING, READING], ["x"], "text", 42])
def test_anything_else_that_is_not_one_object_is_refused_cleanly(answer):
    with pytest.raises(ValueError):
        validate_fields("invoice", answer)


class _Reader:
    name = "gemini"

    def __init__(self, crash_on=()):
        self.crash_on, self.n = set(crash_on), 0

    def classify(self, *_a):
        return "invoice"

    def extract(self, *_a):
        self.n += 1
        if self.n in self.crash_on:
            raise RuntimeError("something nobody foresaw")
        return {**READING, "line_items": [dict(READING["line_items"][0], description=_f(f"X{self.n}"))]}


def test_a_unit_that_crashes_never_raises():
    fields, failed, error = ocr._process_unit(_Reader(crash_on={1}), b"img", "image/jpeg", "invoice", 2)
    assert fields is None and failed == 2 and isinstance(error, RuntimeError)


def test_one_crashing_chunk_does_not_lose_the_others(monkeypatch):
    units = [(b"a", "image/jpeg", 2), (b"b", "image/jpeg", 2), (b"c", "image/jpeg", 2)]
    monkeypatch.setattr(ocr, "_build_units", lambda *_a: (units, 6, None))
    monkeypatch.setattr(ocr.settings, "ocr_chunk_concurrency", 1)
    fields, failed, total = ocr._extract_chunked(_Reader(crash_on={2}), b"pdf", "application/pdf", "invoice")
    assert len(fields["line_items"]) == 2 and failed == 2 and total == 6


# --- the checks after the reading ---------------------------------------------------

def _photo(monkeypatch, reader=None):
    from app.services.ocr import cross_read

    monkeypatch.setattr(ocr, "get_provider", lambda: reader or _Reader())
    monkeypatch.setattr(cross_read, "second_reading", lambda *_a: None)
    monkeypatch.setattr(ocr.settings, "ocr_ai_review", False)
    return ocr.process_document("d", b"\xff\xd8photo", "image/jpeg", "invoice")


def test_a_crashing_check_keeps_the_reading_marked_unchecked(monkeypatch):
    def broken(*_a, **_k):
        raise KeyError("a bug in a check")

    monkeypatch.setattr(ocr, "verify_invoice", broken)
    result = _photo(monkeypatch)
    assert result["fields"]["invoice"]["invoice_no"]["value"] == "INV-1"
    [check] = result["meta"]["verification"]["checks"]
    assert check["id"] == "checks_ran" and check["status"] == "fail"
    assert result["meta"]["verification"]["verdict"] == "needs_check"
    assert ocr.CHECKS_FAILED_WARNING in result["meta"]["warnings"]


def test_a_crashing_party_check_costs_only_itself(monkeypatch):
    from app.services.ocr import party_check

    def broken(*_a, **_k):
        raise RuntimeError("party check bug")

    monkeypatch.setattr(party_check, "cross_check", broken)
    result = _photo(monkeypatch)
    assert result["fields"]["invoice"]["invoice_no"]["value"] == "INV-1"
    assert any(c["id"] == "total_reconciles" for c in result["meta"]["verification"]["checks"])


# --- saving the reading -------------------------------------------------------------

def _status(db_session, doc_id):
    s = db_session()
    try:
        return s.get(Document, doc_id).status, s.get(Document, doc_id).payload
    finally:
        s.close()


def test_a_lesson_that_cannot_be_applied_never_fails_the_scan(client, db_session, doc_id, job_id, monkeypatch):
    from app.api import documents as documents_api

    def broken(*_a, **_k):
        raise RuntimeError("learned-label bug")

    monkeypatch.setattr(documents_api, "process_document", lambda *_a, **_k: dict(RESULT))
    monkeypatch.setattr(documents_api.storage, "load", lambda _ref: b"%PDF")
    monkeypatch.setattr(documents_api, "apply_learned", broken)
    monkeypatch.setattr(documents_api, "apply_remembered", broken)
    assert documents_api.run_due_jobs() == 1
    status, payload = _status(db_session, doc_id)
    assert status == "needs_review" and payload["doc_type"] == "invoice"


def test_a_read_past_its_deadline_goes_to_manual_entry(client, db_session, doc_id, job_id, monkeypatch):
    from app.api import documents as documents_api

    s = db_session()
    try:
        assert jobs.claim(s, job_id)
    finally:
        s.close()
    assert job_id in jobs.overdue_job_ids(60, now=time.monotonic() + 61)
    assert job_id not in jobs.overdue_job_ids(60)

    monkeypatch.setattr(documents_api.storage, "load", lambda _ref: b"%PDF")
    monkeypatch.setattr(documents_api, "_manual_entry",
                        lambda *_a: {**RESULT, "meta": {"needs_manual_entry": True}})
    documents_api.give_up_overdue(job_id)
    jobs.let_go(job_id)
    status, payload = _status(db_session, doc_id)
    assert status == "needs_review" and payload["meta"]["needs_manual_entry"]
    s = db_session()
    try:
        job = s.get(OcrJob, job_id)
        assert job.status == "done" and "longer than" in job.last_error
    finally:
        s.close()


# --- nothing waits for a restart ----------------------------------------------------

def test_the_sweep_queues_an_orphan_but_never_an_upload_in_flight(client, db_session, doc_id):
    s = db_session()
    try:
        assert recover_stuck_documents(s, older_than_seconds=120) == 0       # just made: leave it
        assert s.query(OcrJob).count() == 0
        doc = s.get(Document, doc_id)
        doc.updated_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        s.commit()
        assert recover_stuck_documents(s, older_than_seconds=120) == 1       # an orphan: queued
        assert s.query(OcrJob).filter(OcrJob.document_id == doc_id).count() == 1
        assert recover_stuck_documents(s, older_than_seconds=120) == 1       # has a job now: no second
        assert s.query(OcrJob).filter(OcrJob.document_id == doc_id).count() == 1
    finally:
        s.close()
