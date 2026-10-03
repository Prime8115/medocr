"""A restart, crash or deploy never loses a scan: interrupted work resumes."""
from app.models.document import Document
from app.models.job import OcrJob
from app.services import jobs
from app.services.recovery import recover_stuck_documents
from tests.conftest import register_and_login

RESULT = {"schema_version": "1.0", "doc_type": "invoice", "fields": {}, "meta": {}}


def _shop_id(client, headers):
    return client.get("/v1/auth/me", headers=headers).json()["shop_id"]


def test_a_scan_killed_mid_way_finishes_after_a_restart(client, db_session, monkeypatch):
    """The process dies while reading: on restart the scan is picked up and
    finished, with no "Try again" from the pharmacist."""
    from app.api import documents as documents_api

    def start_then_die(job_id):
        # Exactly what a killed process leaves behind: the job claimed and the
        # document marked processing, then nothing.
        db = documents_api.SessionLocal()
        try:
            assert jobs.claim(db, job_id)
            job = db.get(OcrJob, job_id)
            db.get(Document, job.document_id).status = "processing"
            db.commit()
        finally:
            db.close()

    monkeypatch.setattr(documents_api, "run_job", start_then_die)
    headers = register_and_login(client)
    files = {"file": ("inv.pdf", b"%PDF-fake", "application/pdf")}
    assert client.post("/v1/documents/", headers=headers, files=files).status_code == 200

    s = db_session()
    try:
        doc = s.query(Document).one()
        doc_id = doc.id
        assert doc.status == "processing"
        assert s.query(OcrJob).one().status == "running"  # held by the "dead" process

        # Restart.
        assert recover_stuck_documents(s) == 1
        assert s.get(Document, doc_id).status == "queued"
        assert s.query(OcrJob).one().status == "pending"
    finally:
        s.close()

    monkeypatch.setattr(documents_api, "process_document", lambda *_a, **_k: RESULT)
    assert documents_api.run_due_jobs() == 1

    doc = client.get(f"/v1/documents/{doc_id}", headers=headers).json()
    assert doc["status"] == "needs_review"


def test_documents_from_before_the_job_table_get_a_job(client, db_session):
    headers = register_and_login(client)
    shop_id = _shop_id(client, headers)
    s = db_session()
    try:
        s.add(Document(shop_id=shop_id, doc_type="invoice", status="processing",
                       progress="4/33", image_ref="local://a.pdf"))
        s.add(Document(shop_id=shop_id, doc_type="invoice", status="queued",
                       image_ref="local://b.jpg", requested_doc_type="invoice"))
        s.add(Document(shop_id=shop_id, doc_type="prescription", status="needs_review"))  # untouched
        s.commit()

        assert recover_stuck_documents(s) == 2
        assert sorted(d.status for d in s.query(Document).all()) == ["needs_review", "queued", "queued"]
        by_type = {j.content_type: j for j in s.query(OcrJob).all()}
        assert set(by_type) == {"application/pdf", "image/jpeg"}
        assert by_type["image/jpeg"].doc_type == "invoice"   # the user's choice survives
        assert by_type["application/pdf"].doc_type is None   # Auto stays Auto
        assert all(j.status == "pending" for j in by_type.values())
    finally:
        s.close()


def test_a_document_with_no_stored_file_fails_cleanly(client, db_session):
    headers = register_and_login(client)
    s = db_session()
    try:
        s.add(Document(shop_id=_shop_id(client, headers), doc_type="invoice", status="queued"))
        s.commit()
        assert recover_stuck_documents(s) == 0
        doc = s.query(Document).one()
        assert doc.status == "failed"
        assert "interrupted" in doc.error.lower()
    finally:
        s.close()


def test_recovery_never_queues_a_second_job(client, db_session):
    headers = register_and_login(client)
    s = db_session()
    try:
        doc = Document(shop_id=_shop_id(client, headers), doc_type="invoice", status="queued",
                       image_ref="local://a.pdf")
        s.add(doc)
        s.commit()
        jobs.enqueue(s, doc.id, "application/pdf", None)
        s.commit()
        recover_stuck_documents(s)
        recover_stuck_documents(s)
        assert s.query(OcrJob).count() == 1
    finally:
        s.close()
