"""The /report and /stats endpoints — turning complaints into evidence.

Every invoice defect so far arrived as a WhatsApp message and had to be
reproduced from a description. A report records what the pipeline actually
produced alongside the stored file, so it can become a fixture.
"""
from app.models.audit_log import AuditLog
from tests.conftest import register_and_login


def _submit(client, headers, doc_type="invoice"):
    files = {"file": ("inv.jpg", b"INVOICE fake", "image/jpeg")}
    return client.post("/v1/documents/", files=files, data={"doc_type": doc_type}, headers=headers)


# --------------------------------- reporting ---------------------------------
def test_report_records_what_the_pipeline_produced(client, db_session, mock_ocr):
    headers = register_and_login(client)
    doc_id = _submit(client, headers).json()["document_id"]

    resp = client.post(
        f"/v1/documents/{doc_id}/report",
        json={"note": "Zydus bill: 143 items but the app shows 429"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["document_id"] == doc_id
    assert resp.json()["reported"] is True

    with db_session() as session:
        entry = session.query(AuditLog).filter(AuditLog.action == "document.reported").one()
    assert entry.target == doc_id
    detail = entry.detail
    assert detail["note"].startswith("Zydus bill")
    assert detail["doc_type"] == "invoice"
    assert detail["pipeline"] == "mock"
    assert detail["image_ref"]                       # the file we need to reproduce it
    assert "item_count" in detail
    assert "total_reconciles" in detail
    assert detail["printed_total"] == "1250.00"      # what we read off the invoice


def test_report_works_without_a_note(client, db_session, mock_ocr):
    headers = register_and_login(client)
    doc_id = _submit(client, headers).json()["document_id"]
    assert client.post(f"/v1/documents/{doc_id}/report", json={}, headers=headers).status_code == 200
    with db_session() as session:
        assert session.query(AuditLog).filter(AuditLog.action == "document.reported").one().detail["note"] is None


def test_report_truncates_a_very_long_note(client, db_session, mock_ocr):
    headers = register_and_login(client)
    doc_id = _submit(client, headers).json()["document_id"]
    client.post(f"/v1/documents/{doc_id}/report", json={"note": "x" * 5000}, headers=headers)
    with db_session() as session:
        note = session.query(AuditLog).filter(AuditLog.action == "document.reported").one().detail["note"]
    assert len(note) == 2000


def test_cannot_report_another_shops_document(client, mock_ocr):
    owner_a = register_and_login(client, email="a@shop.com", shop="A")
    doc_id = _submit(client, owner_a).json()["document_id"]

    owner_b = register_and_login(client, email="b@shop.com", shop="B")
    assert client.post(f"/v1/documents/{doc_id}/report", json={}, headers=owner_b).status_code == 404


def test_report_requires_auth(client, mock_ocr):
    headers = register_and_login(client)
    doc_id = _submit(client, headers).json()["document_id"]
    assert client.post(f"/v1/documents/{doc_id}/report", json={}).status_code == 401


def test_report_unknown_document_is_404(client):
    headers = register_and_login(client)
    assert client.post("/v1/documents/doc_missing/report", json={}, headers=headers).status_code == 404


# ----------------------------------- stats -----------------------------------
def test_stats_summarises_this_shops_extractions(client, mock_ocr):
    headers = register_and_login(client)
    _submit(client, headers, doc_type="invoice")
    _submit(client, headers, doc_type="prescription")

    stats = client.get("/v1/documents/stats", headers=headers).json()
    assert stats["documents"] == 2
    assert stats["by_doc_type"] == {"invoice": 1, "prescription": 1}
    assert stats["by_pipeline"] == {"mock": 2}
    assert stats["invoices"]["total"] == 1
    assert stats["window_days"] == 30


def test_stats_counts_reported_documents(client, mock_ocr):
    headers = register_and_login(client)
    doc_id = _submit(client, headers).json()["document_id"]
    client.post(f"/v1/documents/{doc_id}/report", json={"note": "wrong"}, headers=headers)

    assert client.get("/v1/documents/stats", headers=headers).json()["reported_by_users"] == 1


def test_stats_is_scoped_to_the_callers_shop(client, mock_ocr):
    owner_a = register_and_login(client, email="a@shop.com", shop="A")
    _submit(client, owner_a)
    _submit(client, owner_a)

    owner_b = register_and_login(client, email="b@shop.com", shop="B")
    assert client.get("/v1/documents/stats", headers=owner_b).json()["documents"] == 0


def test_stats_route_is_not_shadowed_by_the_document_route(client, mock_ocr):
    """`/stats` must win over `/{document_id}` — otherwise it 404s as a doc id."""
    headers = register_and_login(client)
    resp = client.get("/v1/documents/stats", headers=headers)
    assert resp.status_code == 200
    assert "invoices" in resp.json()


def test_stats_requires_auth(client):
    assert client.get("/v1/documents/stats").status_code == 401


def test_stats_rejects_an_absurd_window(client):
    headers = register_and_login(client)
    assert client.get("/v1/documents/stats?days=0", headers=headers).status_code == 422
    assert client.get("/v1/documents/stats?days=9999", headers=headers).status_code == 422
