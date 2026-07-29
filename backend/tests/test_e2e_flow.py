"""True end-to-end: real FastAPI app + a real HTTP webhook receiver.

Unlike test_push_idempotency (which uses fake connectors), this spins up an
actual HTTP server, registers a webhook connector pointing at it, runs the full
scan -> review -> correct -> approve -> push flow, and verifies the receiver got
a correctly HMAC-signed payload over the wire.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.config import settings
from app.services.connectors.signing import verify
from tests.conftest import register_and_login

RECEIVED: list[dict] = []
SECRET = "e2e-shared-secret"


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        RECEIVED.append(
            {
                "body": body,
                "signature": self.headers.get("X-MediScan-Signature", ""),
                "version": self.headers.get("X-MediScan-Payload-Version", ""),
            }
        )
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *args):
        pass  # silence


@pytest.fixture()
def webhook_server():
    RECEIVED.clear()
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def test_full_scan_to_webhook_delivery(client, db_session, webhook_server, monkeypatch):
    monkeypatch.setattr(settings, "allow_mock_ocr", True)
    headers = register_and_login(client)

    # 1. Configure a signed webhook connector pointing at the real receiver.
    created = client.post(
        "/v1/connectors/",
        json={"type": "webhook", "name": "E2E", "config": {"url": webhook_server}, "secret": SECRET},
        headers=headers,
    )
    assert created.status_code == 201

    # 2. Submit a document (mock OCR runs in the background task).
    files = {"file": ("rx.jpg", b"prescription bytes", "image/jpeg")}
    doc_id = client.post("/v1/documents/", files=files, headers=headers).json()["document_id"]

    # 3. It reaches needs_review with extracted fields.
    doc = client.get(f"/v1/documents/{doc_id}", headers=headers).json()
    assert doc["status"] == "needs_review"

    # 4. Correct a field.
    fields = doc["payload"]["fields"]
    fields["patient"]["name"]["value"] = "Verified Patient"
    patched = client.patch(f"/v1/documents/{doc_id}", json={"fields": fields}, headers=headers)
    assert patched.status_code == 200

    # 5. Approve, then push.
    client.post(f"/v1/documents/{doc_id}/approve", headers=headers)
    push = client.post(f"/v1/documents/{doc_id}/push", headers=headers).json()
    assert push["status"] == "pushed"
    assert push["deliveries"][0]["status"] == "success"
    assert push["deliveries"][0]["response_code"] == 200

    # 6. The external receiver actually got the payload, correctly signed.
    assert len(RECEIVED) == 1
    got = RECEIVED[0]
    assert verify(SECRET, got["body"], got["signature"])
    assert got["version"] == "1.0"
    payload = json.loads(got["body"])
    assert payload["document_id"] == doc_id
    assert payload["data"]["patient"]["name"]["value"] == "Verified Patient"
    assert payload["event"] == "document.approved"


def test_failed_extraction_never_delivers(client, webhook_server, monkeypatch):
    """No OCR configured -> document fails -> nothing is ever sent to the receiver."""
    monkeypatch.setattr(settings, "allow_mock_ocr", False)
    monkeypatch.setattr(settings, "gemini_api_key", None)
    headers = register_and_login(client)

    client.post(
        "/v1/connectors/",
        json={"type": "webhook", "name": "E2E", "config": {"url": webhook_server}, "secret": SECRET},
        headers=headers,
    )
    files = {"file": ("rx.jpg", b"x", "image/jpeg")}
    doc_id = client.post("/v1/documents/", files=files, headers=headers).json()["document_id"]

    doc = client.get(f"/v1/documents/{doc_id}", headers=headers).json()
    assert doc["status"] == "failed"
    # Cannot approve or push a failed document.
    assert client.post(f"/v1/documents/{doc_id}/approve", headers=headers).status_code == 409
    assert len(RECEIVED) == 0
