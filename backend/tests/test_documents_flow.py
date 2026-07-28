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


def test_ocr_failure_marks_document_failed(client, monkeypatch):
    """No mock, no API key -> provider raises OCRError -> document ends 'failed' (never faked)."""
    monkeypatch.setattr(settings, "allow_mock_ocr", False)
    monkeypatch.setattr(settings, "gemini_api_key", None)
    headers = register_and_login(client)
    doc_id = _submit(client, headers).json()["document_id"]
    doc = client.get(f"/v1/documents/{doc_id}", headers=headers).json()
    assert doc["status"] == "failed"
    assert doc["error"]
    assert doc["payload"] is None  # nothing fabricated
