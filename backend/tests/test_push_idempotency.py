"""Push delivery idempotency and multi-connector orchestration via the API."""
from app.models.connector import Connector
from app.services.ocr.verify import open_checks
from tests.conftest import register_and_login, sample_image


def _approved_doc(client, headers, mock=True, marker=""):
    files = {"file": ("rx.png", sample_image(marker), "image/png")}
    doc_id = client.post("/v1/documents/", files=files, headers=headers).json()["document_id"]
    payload = client.get(f"/v1/documents/{doc_id}", headers=headers).json()["payload"]
    required = open_checks(payload["meta"]["verification"])
    approved = client.post(f"/v1/documents/{doc_id}/approve", headers=headers,
                           json={"acknowledged": [c["id"] for c in required]})
    assert approved.status_code == 200
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


def test_push_delivers_and_is_idempotent(client, mock_ocr, db_session, tmp_path):
    headers = register_and_login(client)
    shop_id = client.get("/v1/auth/me", headers=headers).json()["shop_id"]
    doc_id = _approved_doc(client, headers)
    _add_connector(db_session, shop_id, "file_export", {"formats": ["json"], "output_dir": str(tmp_path)})

    first = client.post(f"/v1/documents/{doc_id}/push", headers=headers)
    assert first.status_code == 200
    body = first.json()
    assert body["status"] == "pushed"
    assert len(body["deliveries"]) == 1
    first_delivery_id = body["deliveries"][0]["id"]
    assert body["deliveries"][0]["status"] == "success"
    assert body["deliveries"][0]["delivery_state"] == "file_written"
    assert body["deliveries"][0]["connector_name"] == "file_export"

    # Re-push must NOT create a second delivery (idempotent).
    second = client.post(f"/v1/documents/{doc_id}/push", headers=headers).json()
    assert len(second["deliveries"]) == 1
    assert second["deliveries"][0]["id"] == first_delivery_id


def test_changed_approved_invoice_cannot_reuse_an_old_delivery(client, mock_ocr, db_session, tmp_path):
    from app.models.push_delivery import PushDelivery

    headers = register_and_login(client)
    shop_id = client.get("/v1/auth/me", headers=headers).json()["shop_id"]
    doc_id = _approved_doc(client, headers, marker="INVOICE")
    _add_connector(db_session, shop_id, "file_export", {"formats": ["json"], "output_dir": str(tmp_path)})
    first = client.post(f"/v1/documents/{doc_id}/push", headers=headers)
    assert first.status_code == 200
    delivery_id = first.json()["deliveries"][0]["id"]

    url = f"/v1/documents/{doc_id}"
    fields = client.get(url, headers=headers).json()["payload"]["fields"]
    fields["line_items"][0]["description"]["value"] = "Corrected product from source"
    patched = client.patch(url, headers=headers, json={"fields": fields})
    assert patched.status_code == 200
    required = open_checks(patched.json()["payload"]["meta"]["verification"])
    assert client.post(url + "/approve", headers=headers,
                       json={"acknowledged": [c["id"] for c in required]}).status_code == 200

    blocked = client.post(url + "/push", headers=headers)
    assert blocked.status_code == 409
    assert "changed after" in blocked.json()["detail"].lower()
    db = db_session()
    try:
        deliveries = db.query(PushDelivery).filter(PushDelivery.document_id == doc_id).all()
        assert len(deliveries) == 1
        assert deliveries[0].id == delivery_id
        assert deliveries[0].request_payload["data"]["line_items"][0]["description"]["value"] != "Corrected product from source"
    finally:
        db.close()


def test_stale_delivery_blocks_new_connector_before_any_export(client, mock_ocr, db_session, tmp_path):
    from app.models.push_delivery import PushDelivery

    headers = register_and_login(client)
    shop_id = client.get("/v1/auth/me", headers=headers).json()["shop_id"]
    doc_id = _approved_doc(client, headers, marker="INVOICE")
    _add_connector(db_session, shop_id, "file_export", {"formats": ["json"], "output_dir": str(tmp_path)})
    url = f"/v1/documents/{doc_id}"
    assert client.post(url + "/push", headers=headers).status_code == 200

    # The new destination must not receive the corrected invoice while the
    # original destination still holds the old version.
    second_dir = tmp_path / "second"
    second_dir.mkdir()
    _add_connector(db_session, shop_id, "file_export", {"formats": ["json"], "output_dir": str(second_dir)})
    fields = client.get(url, headers=headers).json()["payload"]["fields"]
    fields["line_items"][0]["description"]["value"] = "New approved item name"
    patched = client.patch(url, headers=headers, json={"fields": fields})
    assert patched.status_code == 200
    required = open_checks(patched.json()["payload"]["meta"]["verification"])
    assert client.post(url + "/approve", headers=headers,
                       json={"acknowledged": [c["id"] for c in required]}).status_code == 200

    assert client.post(url + "/push", headers=headers).status_code == 409
    assert list(second_dir.iterdir()) == []
    db = db_session()
    try:
        assert db.query(PushDelivery).filter(PushDelivery.document_id == doc_id).count() == 1
    finally:
        db.close()


def test_push_to_agent_connector_stays_approved_while_queued(client, mock_ocr, db_session):
    headers = register_and_login(client)
    shop_id = client.get("/v1/auth/me", headers=headers).json()["shop_id"]
    doc_id = _approved_doc(client, headers)
    _add_connector(db_session, shop_id, "desktop_agent", {"paired": True})

    body = client.post(f"/v1/documents/{doc_id}/push", headers=headers).json()
    # A queued job has not reached the shop PC yet.
    assert body["deliveries"][0]["status"] == "pending"
    assert body["deliveries"][0]["delivery_state"] == "queued"
    assert body["status"] == "approved"


def test_push_without_output_folder_stays_approved(client, mock_ocr, db_session):
    headers = register_and_login(client)
    shop_id = client.get("/v1/auth/me", headers=headers).json()["shop_id"]
    doc_id = _approved_doc(client, headers)
    _add_connector(db_session, shop_id, "file_export", {"format": "json"})
    body = client.post(f"/v1/documents/{doc_id}/push", headers=headers).json()
    assert body["status"] == "approved"
    assert body["deliveries"][0]["status"] == "failed"


def test_push_without_connector_returns_400(client, mock_ocr):
    headers = register_and_login(client)
    doc_id = _approved_doc(client, headers)
    assert client.post(f"/v1/documents/{doc_id}/push", headers=headers).status_code == 400
