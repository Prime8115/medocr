"""Gemini provider retry/backoff/fallback on transient overload errors."""
import json

import pytest

import app.services.ocr.gemini as gem
from app.config import settings
from app.services.ocr.base import OCRError


class Overloaded(Exception):
    code = 503


class BadRequest(Exception):
    code = 400


class FakeResp:
    def __init__(self, text):
        self.text = text


class FakeModels:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []  # models called, in order

    def generate_content(self, model, contents, config=None):
        self.calls.append(model)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeResp(item)


class FakeClient:
    def __init__(self, script):
        self.models = FakeModels(script)


def _provider(monkeypatch, script, **over):
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(settings, "ocr_model", over.get("model", "gemini-2.5-flash"))
    monkeypatch.setattr(settings, "ocr_fallback_model", over.get("fallback", "gemini-2.0-flash"))
    monkeypatch.setattr(settings, "ocr_max_retries", over.get("retries", 3))
    p = gem.GeminiProvider(sleep=lambda _s: None)  # no real sleeping
    p._client = FakeClient(script)
    return p


def test_is_transient_detection():
    assert gem._is_transient(Overloaded())
    assert gem._is_transient(Exception("The model is overloaded, try again"))
    assert gem._is_transient(Exception("429 RESOURCE_EXHAUSTED"))
    assert not gem._is_transient(BadRequest())
    assert not gem._is_transient(Exception("invalid api key"))


def test_retries_then_succeeds_on_same_model(monkeypatch):
    payload = json.dumps({"patient": {"name": {"value": "X"}}})
    p = _provider(monkeypatch, [Overloaded(), Overloaded(), payload], retries=3)
    out = p.extract(b"img", "image/jpeg", "prescription")
    assert out["patient"]["name"]["value"] == "X"
    # Three attempts, all on the primary model.
    assert p._client.models.calls == ["gemini-2.5-flash"] * 3


def test_falls_back_to_secondary_model(monkeypatch):
    payload = json.dumps({"patient": {}})
    # Primary fails all retries (2), fallback succeeds on first try.
    script = [Overloaded(), Overloaded(), payload]
    p = _provider(monkeypatch, script, retries=2)
    p.extract(b"img", "image/jpeg", "prescription")
    assert p._client.models.calls == ["gemini-2.5-flash", "gemini-2.5-flash", "gemini-2.0-flash"]


def test_both_overloaded_raises_busy_error(monkeypatch):
    script = [Overloaded(), Overloaded(), Overloaded(), Overloaded()]
    p = _provider(monkeypatch, script, retries=2)
    with pytest.raises(OCRError) as ei:
        p.extract(b"img", "image/jpeg", "prescription")
    assert "busy" in str(ei.value).lower()


def test_non_transient_error_not_retried(monkeypatch):
    p = _provider(monkeypatch, [BadRequest()], retries=3)
    with pytest.raises(OCRError):
        p.extract(b"img", "image/jpeg", "prescription")
    # Only one call — no retries on a permanent error.
    assert len(p._client.models.calls) == 1
