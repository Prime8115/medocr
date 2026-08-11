"""Stuck-document recovery on startup."""
from app.models.document import Document
from app.services.recovery import recover_stuck_documents
from tests.conftest import register_and_login


def test_recover_marks_stuck_docs_failed(client, db_session):
    headers = register_and_login(client)
    shop_id = client.get("/v1/auth/me", headers=headers).json()["shop_id"]

    s = db_session()
    try:
        s.add(Document(shop_id=shop_id, doc_type="invoice", status="processing", progress="4/33"))
        s.add(Document(shop_id=shop_id, doc_type="invoice", status="queued"))
        s.add(Document(shop_id=shop_id, doc_type="prescription", status="needs_review"))  # untouched
        s.commit()
    finally:
        s.close()

    s = db_session()
    try:
        n = recover_stuck_documents(s)
        assert n == 2
        statuses = sorted(d.status for d in s.query(Document).all())
        assert statuses == ["failed", "failed", "needs_review"]
        failed = s.query(Document).filter(Document.status == "failed").first()
        assert "interrupted" in (failed.error or "").lower()
        assert failed.progress is None
    finally:
        s.close()
