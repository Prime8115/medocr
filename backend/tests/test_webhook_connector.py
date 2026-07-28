"""Webhook connector: signing header, success, retry/backoff on failure."""
import json

import requests

from app.services.connectors.base import FAILED, SUCCESS
from app.services.connectors.signing import SIGNATURE_HEADER, verify
from app.services.connectors.webhook import WebhookConnector


class FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


def test_webhook_success_sends_valid_signature():
    captured = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        captured["url"] = url
        captured["data"] = data
        captured["headers"] = headers
        return FakeResponse(200, "ok")

    conn = WebhookConnector(
        config={"url": "https://example.com/hook"},
        secret="s3cr3t",
        http_post=fake_post,
        sleep=lambda _s: None,
    )
    result = conn.deliver({"payload_version": "1.0", "document_id": "doc_1"})

    assert result.status == SUCCESS
    assert result.attempts == 1
    # Signature header is present and valid over the exact body sent.
    sig = captured["headers"][SIGNATURE_HEADER]
    assert verify("s3cr3t", captured["data"], sig)
    assert json.loads(captured["data"])["document_id"] == "doc_1"


def test_webhook_retries_then_fails():
    calls = {"n": 0}
    sleeps = []

    def fake_post(url, data=None, headers=None, timeout=None):
        calls["n"] += 1
        return FakeResponse(500, "server error")

    conn = WebhookConnector(
        config={"url": "https://example.com/hook"},
        http_post=fake_post,
        sleep=lambda s: sleeps.append(s),
        max_attempts=3,
        base_backoff=0.5,
    )
    result = conn.deliver({"payload_version": "1.0"})

    assert result.status == FAILED
    assert calls["n"] == 3
    assert result.attempts == 3
    # Exponential backoff between the 3 attempts: 0.5, 1.0 (no sleep after last).
    assert sleeps == [0.5, 1.0]


def test_webhook_handles_connection_error():
    def fake_post(url, data=None, headers=None, timeout=None):
        raise requests.RequestException("connection refused")

    conn = WebhookConnector(
        config={"url": "https://x"}, http_post=fake_post, sleep=lambda _s: None, max_attempts=2
    )
    result = conn.deliver({"payload_version": "1.0"})
    assert result.status == FAILED
    assert "connection refused" in (result.response_body or "")


def test_webhook_no_url_fails_fast():
    conn = WebhookConnector(config={})
    result = conn.deliver({"payload_version": "1.0"})
    assert result.status == FAILED
