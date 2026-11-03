"""A complaint remains visible to platform admins until it is resolved."""
from app.models.audit_log import AuditLog
from app.models.problem_report_review import ProblemReportReview
from app.models.user import User
from tests.conftest import register_and_login, sample_image


def _submit(client, headers):
    files = {"file": ("invoice.png", sample_image("INVOICE"), "image/png")}
    return client.post("/v1/documents/", files=files, data={"doc_type": "invoice"}, headers=headers).json()["document_id"]


def _grant(db_session, email):
    with db_session() as db:
        user = db.query(User).filter(User.email == email).one()
        user.is_platform_admin = True
        db.commit()


def test_recent_and_legacy_reports_are_in_platform_queue(client, db_session, mock_ocr):
    customer = register_and_login(client, email="recent@example.com", shop="Customer A")
    doc_id = _submit(client, customer)
    client.post(f"/v1/documents/{doc_id}/report", json={"note": "Rate wrong on second phone"}, headers=customer)
    with db_session() as db:
        legacy = AuditLog(shop_id=None, actor_id=None, action="document.reported", target="deleted-document",
                          detail={"note": "Older complaint"})
        db.add(legacy)
        db.commit()
        legacy_id = legacy.id
    admin = register_and_login(client, email="admin@example.com", shop="Platform")
    outsider = register_and_login(client, email="outsider@example.com", shop="Other")
    assert client.get("/v1/admin/reports", headers=customer).status_code == 403
    assert client.get("/v1/admin/reports", headers=outsider).status_code == 403
    assert client.get("/v1/admin/reports").status_code == 401
    _grant(db_session, "admin@example.com")
    response = client.get("/v1/admin/reports", headers=admin)
    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert {item["status"] for item in response.json()["items"]} == {"open"}
    filtered = client.get("/v1/admin/reports", params={"q": "recent@example.com"}, headers=admin).json()
    assert filtered["total"] == 1
    assert filtered["items"][0]["document_id"] == doc_id
    assert client.get(f"/v1/admin/reports/{legacy_id}", headers=admin).json()["document"] is None


def test_report_resolution_is_required_and_audited(client, db_session, mock_ocr):
    customer = register_and_login(client, email="customer@example.com")
    doc_id = _submit(client, customer)
    client.post(f"/v1/documents/{doc_id}/report", json={"note": "Missing line"}, headers=customer)
    admin = register_and_login(client, email="admin@example.com", shop="Platform")
    _grant(db_session, "admin@example.com")
    report = client.get("/v1/admin/reports", headers=admin).json()["items"][0]
    rid = report["id"]
    assert client.patch(f"/v1/admin/reports/{rid}", json={"status": "closed"}, headers=admin).status_code == 422
    assert client.patch(f"/v1/admin/reports/{rid}", json={"status": "closed", "resolution_note": "fixed"}, headers=customer).status_code == 403
    started = client.patch(f"/v1/admin/reports/{rid}", json={"status": "in_progress"}, headers=admin)
    assert started.status_code == 200
    closed = client.patch(f"/v1/admin/reports/{rid}", json={"status": "closed", "resolution_note": "Verified against source"}, headers=admin)
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"
    assert closed.json()["closed_at"]
    assert len(closed.json()["history"]) == 2
    assert client.get("/v1/admin/reports", params={"status": "open"}, headers=admin).json()["total"] == 0
    assert client.get("/v1/admin/reports", params={"status": "closed"}, headers=admin).json()["total"] == 1
    reopened = client.patch(f"/v1/admin/reports/{rid}", json={"status": "open"}, headers=admin)
    assert reopened.json()["closed_at"] is None
    with db_session() as db:
        assert db.get(ProblemReportReview, rid).status == "open"
        assert db.query(AuditLog).filter(AuditLog.action == "problem_report.updated").count() == 3
        assert db.query(AuditLog).filter(AuditLog.action == "document.reported").one().detail["note"] == "Missing line"


def test_source_access_requires_platform_admin(client, db_session, mock_ocr):
    customer = register_and_login(client, email="customer@example.com")
    doc_id = _submit(client, customer)
    client.post(f"/v1/documents/{doc_id}/report", json={}, headers=customer)
    admin = register_and_login(client, email="admin@example.com", shop="Platform")
    _grant(db_session, "admin@example.com")
    rid = client.get("/v1/admin/reports", headers=admin).json()["items"][0]["id"]
    assert client.get(f"/v1/admin/reports/{rid}/source", headers=customer).status_code == 403
    source = client.get(f"/v1/admin/reports/{rid}/source", headers=admin)
    assert source.status_code == 200
    assert source.json()["image"].startswith("data:image/")
    assert source.headers["cache-control"] == "private, no-store"
