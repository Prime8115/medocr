"""End-to-end document lifecycle via the API using the mock OCR provider:
submit -> review -> correct (PATCH) -> approve -> push.
"""
from app.config import settings
from app.models.connector import Connector
from tests.conftest import register_and_login


def _shop_id(client, headers):
    return client.get("/v1/auth/me", headers=headers).json()["shop_id"]


def _submit(client, headers, content=b"fake-image", filename="rx.jpg", ctype="image/jpeg", doc_type=None):
    files = {"file": (filename, content, ctype)}
    data = {"doc_type": doc_type} if doc_type else {}
    return client.post("/v1/documents/", files=files, data=data, headers=headers)


def test_submit_prescription_reaches_needs_review(client, mock_ocr):
    headers = register_and_login(client)
    resp = _submit(client, headers)
    assert resp.status_code == 200
    doc_id = resp.json()["document_id"]

    # Background job runs before TestClient returns; status should be needs_review.
    doc = client.get(f"/v1/documents/{doc_id}", headers=headers).json()
    assert doc["status"] == "needs_review"
    assert doc["doc_type"] == "prescription"
    assert doc["payload"]["fields"]["patient"]["name"]["value"].endswith("(MOCK)")
    assert doc["overall_confidence"] is not None
    # Low-confidence fields flagged for review.
    assert any("registration_no" in w for w in doc["payload"]["meta"]["warnings"])


def test_submit_invoice_explicit_type(client, mock_ocr):
    headers = register_and_login(client)
    doc_id = _submit(client, headers, doc_type="invoice").json()["document_id"]
    doc = client.get(f"/v1/documents/{doc_id}", headers=headers).json()
    assert doc["doc_type"] == "invoice"
    assert doc["payload"]["fields"]["line_items"][0]["batch_no"]["value"] == "B12345"


def test_autodetect_invoice(client, mock_ocr):
    headers = register_and_login(client)
    # Mock classifier keys off an "INVOICE" marker in the bytes; no doc_type given.
    doc_id = _submit(client, headers, content=b"INVOICE supplier bill data").json()["document_id"]
    doc = client.get(f"/v1/documents/{doc_id}", headers=headers).json()
    assert doc["doc_type"] == "invoice"


def test_patch_corrections_then_approve_and_push(client, mock_ocr, db_session):
    headers = register_and_login(client)
    doc_id = _submit(client, headers).json()["document_id"]

    # Correct the patient name.
    corrected = {
        "patient": {"name": {"value": "Corrected Name", "confidence": 1.0}, "age": {"value": "50"}},
        "prescriber": {},
        "medications": [],
    }
    patched = client.patch(f"/v1/documents/{doc_id}", json={"fields": corrected}, headers=headers)
    assert patched.status_code == 200
    body = patched.json()
    assert body["status"] == "needs_review"
    assert body["payload"]["fields"]["patient"]["name"]["value"] == "Corrected Name"

    # Approve.
    approved = client.post(f"/v1/documents/{doc_id}/approve", headers=headers)
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    # Push with no connector -> 400.
    assert client.post(f"/v1/documents/{doc_id}/push", headers=headers).status_code == 400

    # Add a file-export connector (delivers without network), then push -> pushed.
    shop_id = _shop_id(client, headers)
    s = db_session()
    try:
        s.add(Connector(shop_id=shop_id, type="file_export", name="Test", config={"formats": ["json"]}, enabled=True))
        s.commit()
    finally:
        s.close()

    pushed = client.post(f"/v1/documents/{doc_id}/push", headers=headers)
    assert pushed.status_code == 200
    assert pushed.json()["status"] == "pushed"


def test_approve_before_review_rejected(client, mock_ocr, db_session):
    headers = register_and_login(client)
    # Seed a document stuck in 'queued' (approve not allowed from queued).
    from app.models.document import Document

    shop_id = _shop_id(client, headers)
    s = db_session()
    try:
        doc = Document(shop_id=shop_id, doc_type="prescription", status="queued")
        s.add(doc)
        s.commit()
        doc_id = doc.id
    finally:
        s.close()
    assert client.post(f"/v1/documents/{doc_id}/approve", headers=headers).status_code == 409


def test_retry_reprocesses_stored_image(client, mock_ocr):
    """A document can be re-run on its stored image without re-uploading."""
    headers = register_and_login(client)
    doc_id = _submit(client, headers).json()["document_id"]
    # First pass reaches needs_review with a stored image.
    assert client.get(f"/v1/documents/{doc_id}", headers=headers).json()["status"] == "needs_review"

    # Retry re-runs OCR on the stored image.
    r = client.post(f"/v1/documents/{doc_id}/retry", headers=headers)
    assert r.status_code == 200
    doc = client.get(f"/v1/documents/{doc_id}", headers=headers).json()
    assert doc["status"] == "needs_review"
    assert doc["payload"]["fields"]["patient"]["name"]["value"].endswith("(MOCK)")


def test_ocr_failure_marks_document_failed(client, monkeypatch):
    """No mock, no API key -> provider raises OCRError -> document ends 'failed' (never faked)."""
    # The genuine-failure path: the manual-entry fallback is off, or failed too.
    monkeypatch.setattr(settings, "ocr_fallback_enabled", False)
    monkeypatch.setattr(settings, "allow_mock_ocr", False)
    monkeypatch.setattr(settings, "gemini_api_key", None)
    headers = register_and_login(client)
    doc_id = _submit(client, headers).json()["document_id"]
    doc = client.get(f"/v1/documents/{doc_id}", headers=headers).json()
    assert doc["status"] == "failed"
    assert doc["error"]
    assert doc["payload"] is None  # nothing fabricated


def test_failure_shows_the_user_a_plain_message_and_keeps_the_cause(client, db_session, monkeypatch):
    """The pharmacist sees what to do, never the AI's own error text; the cause
    is kept in the audit log for whoever diagnoses it."""
    # The genuine-failure path: the manual-entry fallback is off, or failed too.
    monkeypatch.setattr(settings, "ocr_fallback_enabled", False)
    from app.api import documents as documents_api
    from app.models.audit_log import AuditLog
    from app.services.ocr import OCRError

    cause = "Could not read the document: AI request was rejected: 400 INVALID_ARGUMENT schema"

    def refuse(*_a, **_k):
        raise OCRError(cause, kind="rejected")

    monkeypatch.setattr(documents_api, "process_document", refuse)
    headers = register_and_login(client)
    doc_id = _submit(client, headers).json()["document_id"]
    doc = client.get(f"/v1/documents/{doc_id}", headers=headers).json()
    assert doc["status"] == "failed"
    assert doc["error"] == documents_api.FAILED_MESSAGE
    assert "INVALID_ARGUMENT" not in doc["error"]

    db = db_session()
    try:
        row = db.query(AuditLog).filter_by(action="document.failed", target=doc_id).one()
        assert row.detail["cause"] == cause
    finally:
        db.close()


def test_busy_failure_tells_the_user_to_try_again(client, monkeypatch):
    # The genuine-failure path: the manual-entry fallback is off, or failed too.
    monkeypatch.setattr(settings, "ocr_fallback_enabled", False)
    from app.api import documents as documents_api
    from app.services.ocr import OCRError

    def busy(*_a, **_k):
        raise OCRError("AI service is busy. Please retry in a moment. [503 UNAVAILABLE]", kind="busy")

    monkeypatch.setattr(documents_api, "process_document", busy)
    monkeypatch.setattr(settings, "ocr_busy_requeue_attempts", 0)  # no waiting allowed
    headers = register_and_login(client)
    doc_id = _submit(client, headers).json()["document_id"]
    doc = client.get(f"/v1/documents/{doc_id}", headers=headers).json()
    assert doc["error"] == documents_api.BUSY_MESSAGE
    assert "503" not in doc["error"]
    # The app recognises an overload by this word and retries on it.
    assert "busy" in doc["error"].lower()


def test_unexpected_crash_does_not_leak_to_the_user(client, monkeypatch):
    # The genuine-failure path: the manual-entry fallback is off, or failed too.
    monkeypatch.setattr(settings, "ocr_fallback_enabled", False)
    from app.api import documents as documents_api

    def crash(*_a, **_k):
        raise KeyError("line_items")

    monkeypatch.setattr(documents_api, "process_document", crash)
    headers = register_and_login(client)
    doc_id = _submit(client, headers).json()["document_id"]
    doc = client.get(f"/v1/documents/{doc_id}", headers=headers).json()
    assert doc["error"] == documents_api.FAILED_MESSAGE


def _busy_then(monkeypatch, outcomes):
    """process_document that is busy until `outcomes` runs out of busy entries."""
    from app.api import documents as documents_api
    from app.services.ocr import OCRError

    calls = []

    def process(*_a, **_k):
        calls.append(1)
        outcome = outcomes.pop(0)
        if outcome == "busy":
            raise OCRError("AI service is busy. [429 RESOURCE_EXHAUSTED]", kind="busy")
        return outcome

    monkeypatch.setattr(documents_api, "process_document", process)
    return calls


def _job(db_session, doc_id):
    from app.models.job import OcrJob

    db = db_session()
    try:
        job = db.query(OcrJob).filter_by(document_id=doc_id).one()
        db.expunge(job)
        return job
    finally:
        db.close()


def _let_time_pass():
    """Run every queued job as if its wait were over."""
    from datetime import datetime, timedelta, timezone

    from app.api import documents as documents_api

    return documents_api.run_due_jobs(now=datetime.now(timezone.utc) + timedelta(days=1))


def test_busy_ai_requeues_the_scan_instead_of_failing_it(client, db_session, monkeypatch):
    """The pharmacist sees "processing", not an error, while the AI is busy -
    and the scan finishes by itself once there is capacity again."""
    from app.api import documents as documents_api

    result = {"schema_version": "1.0", "doc_type": "invoice", "fields": {}, "meta": {}}
    calls = _busy_then(monkeypatch, ["busy", "busy", result])
    headers = register_and_login(client)
    doc_id = _submit(client, headers).json()["document_id"]

    doc = client.get(f"/v1/documents/{doc_id}", headers=headers).json()
    assert doc["status"] == "queued"
    assert doc["progress"] == documents_api.WAITING_PROGRESS
    assert doc["error"] is None
    job = _job(db_session, doc_id)
    assert job.status == "pending" and job.busy_attempts == 1

    # Not due yet: nothing runs before the wait is over.
    assert documents_api.run_due_jobs() == 0

    assert _let_time_pass() == 1   # busy again
    assert _job(db_session, doc_id).busy_attempts == 2
    assert _let_time_pass() == 1   # reads it

    doc = client.get(f"/v1/documents/{doc_id}", headers=headers).json()
    assert doc["status"] == "needs_review"
    assert doc["progress"] is None
    assert len(calls) == 3
    assert _job(db_session, doc_id).status == "done"


def test_a_scan_that_stays_busy_fails_after_the_last_wait(client, db_session, monkeypatch):
    # The genuine-failure path: the manual-entry fallback is off, or failed too.
    monkeypatch.setattr(settings, "ocr_fallback_enabled", False)
    from app.api import documents as documents_api

    monkeypatch.setattr(settings, "ocr_busy_requeue_attempts", 3)
    calls = _busy_then(monkeypatch, ["busy"] * 4)
    headers = register_and_login(client)
    doc_id = _submit(client, headers).json()["document_id"]
    while _let_time_pass():
        pass

    doc = client.get(f"/v1/documents/{doc_id}", headers=headers).json()
    assert len(calls) == 4  # the first try and three waits
    assert doc["status"] == "failed"
    assert doc["error"] == documents_api.BUSY_MESSAGE
    assert doc["progress"] is None
    assert _job(db_session, doc_id).status == "failed"


def test_requeue_waits_double_up_to_a_cap():
    from app.api import documents as documents_api

    delays = [documents_api._requeue_delay(n) for n in range(6)]
    assert delays == [20.0, 40.0, 80.0, 160.0, 300.0, 300.0]


def test_retrying_an_auto_detected_scan_detects_its_type_again(client, monkeypatch):
    """An Auto upload stores a placeholder type until it is read. Retrying a
    failed one must not treat that placeholder as the user's choice - it once
    forced a scanned invoice through the prescription reader, so every invoice
    field came back empty."""
    # The genuine-failure path: the manual-entry fallback is off, or failed too.
    monkeypatch.setattr(settings, "ocr_fallback_enabled", False)
    from app.api import documents as documents_api
    from app.services.ocr import OCRError

    seen = []

    def fail_then_record(document_id, data, content_type, doc_type, on_progress=None):
        seen.append(doc_type)
        if len(seen) == 1:
            raise OCRError("Could not read the document.")
        return {"schema_version": "1.0", "doc_type": "invoice", "fields": {}, "meta": {}}

    monkeypatch.setattr(documents_api, "process_document", fail_then_record)
    headers = register_and_login(client)
    doc_id = _submit(client, headers).json()["document_id"]  # Auto: no doc_type
    assert client.get(f"/v1/documents/{doc_id}", headers=headers).json()["status"] == "failed"

    assert client.post(f"/v1/documents/{doc_id}/retry", headers=headers).status_code == 200

    assert seen == [None, None]  # detected both times, never forced
    doc = client.get(f"/v1/documents/{doc_id}", headers=headers).json()
    assert doc["doc_type"] == "invoice"


def test_retrying_keeps_a_type_the_user_chose(client, monkeypatch):
    from app.api import documents as documents_api
    from app.services.ocr import OCRError

    seen = []

    def fail_then_record(document_id, data, content_type, doc_type, on_progress=None):
        seen.append(doc_type)
        if len(seen) == 1:
            raise OCRError("Could not read the document.")
        return {"schema_version": "1.0", "doc_type": doc_type, "fields": {}, "meta": {}}

    monkeypatch.setattr(documents_api, "process_document", fail_then_record)
    headers = register_and_login(client)
    doc_id = _submit(client, headers, doc_type="prescription").json()["document_id"]
    client.post(f"/v1/documents/{doc_id}/retry", headers=headers)

    assert seen == ["prescription", "prescription"]


def _failed_doc(client, monkeypatch, headers, cause):
    from app.api import documents as documents_api
    from app.services.ocr import OCRError

    def refuse(*_a, **_k):
        raise OCRError(cause, kind="rejected")

    monkeypatch.setattr(documents_api, "process_document", refuse)
    return _submit(client, headers).json()["document_id"]


def test_the_owner_can_read_why_a_scan_failed(client, monkeypatch):
    # The genuine-failure path: the manual-entry fallback is off, or failed too.
    monkeypatch.setattr(settings, "ocr_fallback_enabled", False)
    headers = register_and_login(client)
    cause = "Could not read the document: Invalid invoice fields: quantity"
    doc_id = _failed_doc(client, monkeypatch, headers, cause)

    r = client.get(f"/v1/documents/{doc_id}/diagnostics", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "failed"
    assert body["requested_doc_type"] is None
    assert body["failures"][0]["cause"] == cause
    # ...while the document itself still shows only the plain message.
    doc = client.get(f"/v1/documents/{doc_id}", headers=headers).json()
    assert "Invalid" not in doc["error"]


def test_diagnostics_are_owner_only(client, db_session, monkeypatch):
    from app.models.user import User

    headers = register_and_login(client)
    doc_id = _failed_doc(client, monkeypatch, headers, "x")
    db = db_session()
    try:
        db.query(User).update({User.role: "staff"})
        db.commit()
    finally:
        db.close()
    assert client.get(f"/v1/documents/{doc_id}/diagnostics", headers=headers).status_code == 403


def test_diagnostics_never_cross_shops(client, monkeypatch):
    headers_a = register_and_login(client)
    doc_id = _failed_doc(client, monkeypatch, headers_a, "secret cause")
    headers_b = register_and_login(client, email="b@shop.com", shop="Shop B")
    r = client.get(f"/v1/documents/{doc_id}/diagnostics", headers=headers_b)
    assert r.status_code == 404
