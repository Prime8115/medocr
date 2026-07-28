"""Push delivery idempotency and multi-connector orchestration via the API."""
from app.models.connector import Connector
from tests.conftest import register_and_login


def _approved_doc(client, headers, mock=True):
    files = {"file": ("rx.jpg", b"fake", "image/jpeg")}
    doc_id = client.post("/v1/documents/", files=files, headers=headers).json()["document_id"]
    client.post(f"/v1/documents/{doc_id}/approve", headers=headers)
    return doc_id


def _add_connector(db_session, shop_id, ctype="file_export", config=None):
    s = db_session()
    try:
        c = Connector(shop_id=shop_id, type=ctype, name=ctype, config=config or {}, enabled=True)
        s.add(c)
        s.commit()
        return c.id
    finally:
        s.close()


def test_push_delivers_and_is_idempotent(client, mock_ocr, db_session):
    headers = register_and_login(client)
    shop_id = client.get("/v1/auth/me", headers=headers).json()["shop_id"]
    doc_id = _approved_doc(client, headers)
    _add_connector(db_session, shop_id, "file_export", {"formats": ["json"]})

    first = client.post(f"/v1/documents/{doc_id}/push", headers=headers)
    assert first.status_code == 200
    body = first.json()
    assert body["status"] == "pushed"
    assert len(body["deliveries"]) == 1
    first_delivery_id = body["deliveries"][0]["id"]
    assert body["deliveries"][0]["status"] == "success"

    # Re-push must NOT create a second delivery (idempotent).
    second = client.post(f"/v1/documents/{doc_id}/push", headers=headers).json()
    assert len(second["deliveries"]) == 1
    assert second["deliveries"][0]["id"] == first_delivery_id


def test_push_to_agent_connector_is_pending_then_pushed(client, mock_ocr, db_session):
    headers = register_and_login(client)
    shop_id = client.get("/v1/auth/me", headers=headers).json()["shop_id"]
    doc_id = _approved_doc(client, headers)
    _add_connector(db_session, shop_id, "desktop_agent", {"paired": True})

    body = client.post(f"/v1/documents/{doc_id}/push", headers=headers).json()
    # Queued for the agent counts as an accepted push.
    assert body["deliveries"][0]["status"] == "pending"
    assert body["status"] == "pushed"


def test_push_without_connector_returns_400(client, mock_ocr):
    headers = register_and_login(client)
    doc_id = _approved_doc(client, headers)
    assert client.post(f"/v1/documents/{doc_id}/push", headers=headers).status_code == 400
