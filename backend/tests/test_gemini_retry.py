"""Gemini provider retry/backoff/fallback on transient overload errors."""
import json

import pytest

import app.services.ocr.gemini as gem
from app.config import settings
from app.services.ocr.base import OCRError


@pytest.fixture(autouse=True)
def _fresh_schema_memory():
    """Each test starts as a fresh worker that has seen no schema refusals."""
    gem._SCHEMA_REFUSED.clear()
    yield
    gem._SCHEMA_REFUSED.clear()


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
        self.configs = []  # the config sent with each call

    def generate_content(self, model, contents, config=None):
        self.calls.append(model)
        self.configs.append(config)
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


class RateLimited(Exception):
    code = 429


def test_multi_key_rotation_on_429(monkeypatch):
    from app.services.ocr.key_pool import KeyPool

    monkeypatch.setattr(settings, "gemini_api_key", "keyA")
    monkeypatch.setattr(settings, "ocr_max_retries", 3)

    client_a = FakeClient([RateLimited()])
    client_b = FakeClient([json.dumps({"patient": {"name": {"value": "Alice"}}})])

    def client_factory(k):
        return client_a if k == "keyA" else client_b

    pool = KeyPool(["keyA", "keyB"], client_factory=client_factory)
    p = gem.GeminiProvider(sleep=lambda _s: None, key_pool=pool)

    out = p.extract(b"img", "image/jpeg", "prescription")
    assert out["patient"]["name"]["value"] == "Alice"
    assert pool.is_in_cooldown("keyA")
    assert pool.is_in_cooldown("keyA", settings.ocr_model)
    assert not pool.is_in_cooldown("keyB")


# --- error kinds, and the schema Gemini is asked to fill ---------------------


class SchemaTooComplex(Exception):
    code = 400

    def __str__(self):
        return (
            "400 INVALID_ARGUMENT. The specified schema produces a constraint that has "
            "too many states for serving."
        )


def test_errors_carry_their_kind(monkeypatch):
    p = _provider(monkeypatch, [Overloaded()] * 4, retries=2)
    with pytest.raises(OCRError) as busy:
        p.extract(b"img", "image/jpeg", "prescription")
    assert busy.value.kind == "busy"

    p = _provider(monkeypatch, [BadRequest("API key not valid")], retries=2)
    with pytest.raises(OCRError) as rejected:
        p.extract(b"img", "image/jpeg", "prescription")
    assert rejected.value.kind == "rejected"
    # The cause reaches the pharmacist, not a generic "busy".
    assert "API key not valid" in str(rejected.value)
    assert "busy" not in str(rejected.value).lower()


def test_fallback_model_rejecting_is_not_reported_as_busy(monkeypatch):
    # Primary overloaded, fallback refuses the request outright (e.g. a model
    # name the key has no access to): that is not "busy", and must not say so.
    p = _provider(monkeypatch, [Overloaded(), Overloaded(), BadRequest("model not found")], retries=2)
    with pytest.raises(OCRError) as ei:
        p.extract(b"img", "image/jpeg", "prescription")
    assert ei.value.kind == "rejected"
    assert "model not found" in str(ei.value)


def test_long_gemini_errors_are_shortened(monkeypatch):
    p = _provider(monkeypatch, [BadRequest("x" * 5000)], retries=1)
    with pytest.raises(OCRError) as ei:
        p.extract(b"img", "image/jpeg", "prescription")
    assert len(str(ei.value)) < 400


def test_schema_rejection_retries_without_the_schema(monkeypatch):
    payload = json.dumps({"invoice": {"invoice_no": {"value": "M-544", "confidence": 0.9}}})
    p = _provider(monkeypatch, [SchemaTooComplex(), payload], retries=2)
    out = p.extract(b"text", "text/plain", "invoice")
    assert out["invoice"]["invoice_no"]["value"] == "M-544"
    first, second = p._client.models.configs
    assert first.response_json_schema is not None
    assert second.response_json_schema is None
    assert second.response_mime_type == "application/json"


def test_unreadable_output_is_kind_output(monkeypatch):
    p = _provider(monkeypatch, ['{"invoice": {'], retries=1)
    with pytest.raises(OCRError) as ei:
        p.extract(b"text", "text/plain", "invoice")
    assert ei.value.kind == "output"


def _leaf_paths(schema, node=None, path=""):
    """Every {value, confidence} leaf the schema describes, as dotted paths."""
    node = schema if node is None else node
    if "$ref" in node:
        node = schema["$defs"][node["$ref"].rsplit("/", 1)[-1]]
    if node.get("type") == "array":
        return _leaf_paths(schema, node["items"], path + "[]")
    props = node.get("properties", {})
    if set(props) == {"value", "confidence"}:
        return {path}
    out = set()
    for name, sub in props.items():
        out |= _leaf_paths(schema, sub, f"{path}.{name}" if path else name)
    return out


@pytest.mark.parametrize("doc_type", ["invoice", "prescription"])
def test_compact_schema_keeps_every_field(doc_type):
    from app.schemas.extraction import FIELDS_MODEL

    model = FIELDS_MODEL[doc_type]
    full, compact = model.model_json_schema(), gem.compact_schema(model)
    # Nothing a scanned document can return is lost by shrinking the schema.
    assert _leaf_paths(compact) == _leaf_paths(full)
    for name, definition in full["$defs"].items():
        assert set(compact["$defs"][name].get("properties", {})) == set(definition.get("properties", {}))


def test_compact_schema_drops_what_does_not_constrain_output():
    from app.schemas.extraction import FIELDS_MODEL

    model = FIELDS_MODEL["invoice"]
    text = json.dumps(gem.compact_schema(model))
    for noise in ('"title"', '"default"', '"anyOf"'):
        assert noise not in text
    # A property NAMED description is a field, not the keyword - it must stay.
    assert '"description"' in text
    # Well under the size that was rejected after the client fields were added.
    assert len(text) < len(json.dumps(model.model_json_schema())) / 2
    leaf = gem.compact_schema(model)["$defs"]["Field"]
    assert leaf["required"] == ["value", "confidence"]
    assert leaf["properties"]["value"] == {"type": ["string", "null"]}


class GenericInvalidArgument(Exception):
    code = 400

    def __str__(self):
        # Exactly what production recorded for the MSV Lifesciences invoice.
        return (
            "400 INVALID_ARGUMENT. {'error': {'code': 400, 'message': "
            "'Request contains an invalid argument.', 'status': 'INVALID_ARGUMENT'}}"
        )


def test_a_bare_invalid_argument_on_a_schema_request_retries_without_it(monkeypatch):
    payload = json.dumps({"invoice": {"invoice_no": {"value": "M-544", "confidence": 0.9}}})
    p = _provider(monkeypatch, [GenericInvalidArgument(), payload], retries=2)
    out = p.extract(b"text", "text/plain", "invoice")
    assert out["invoice"]["invoice_no"]["value"] == "M-544"
    first, second = p._client.models.configs
    assert first.response_json_schema is not None
    assert second.response_json_schema is None


def test_a_bad_key_is_not_retried_without_the_schema(monkeypatch):
    p = _provider(monkeypatch, [BadRequest("API key not valid. Please pass a valid API key.")], retries=2)
    with pytest.raises(OCRError):
        p.extract(b"text", "text/plain", "invoice")
    assert len(p._client.models.calls) == 1


def test_a_refused_schema_is_not_sent_again_by_the_same_worker(monkeypatch):
    """Once the model refuses the invoice schema, later invoices skip straight
    to the request that works - instead of paying for a call that can only
    fail first, on every invoice."""
    payload = json.dumps({"invoice": {"invoice_no": {"value": "M-544", "confidence": 0.9}}})
    p = _provider(monkeypatch, [SchemaTooComplex(), payload, payload], retries=2)
    p.extract(b"text", "text/plain", "invoice")
    p.extract(b"text", "text/plain", "invoice")
    configs = p._client.models.configs
    assert len(configs) == 3          # refused, retried - then one call only
    assert configs[2].response_json_schema is None


# --- a hung request: a time limit, and straight to the fallback --------------

class TimedOut(Exception):
    """What the HTTP client raises when a request runs past its time limit."""

    def __str__(self):
        return "The read operation timed out"


def test_a_timed_out_request_goes_straight_to_the_fallback(monkeypatch):
    # The primary hung once; it is not tried again before the fallback.
    p = _provider(monkeypatch, [TimedOut(), json.dumps({"patient": {"name": {"value": "Alice"}}})],
                  retries=3)
    out = p.extract(b"img", "image/jpeg", "prescription")
    assert out["patient"]["name"]["value"] == "Alice"
    assert p._client.models.calls == ["gemini-2.5-flash", "gemini-2.0-flash"]
    assert [c["outcome"] for c in p.calls] == ["timeout", "ok"]
    assert all(isinstance(c["seconds"], float) for c in p.calls)


def test_every_client_carries_the_time_limit(monkeypatch):
    monkeypatch.setattr(settings, "ocr_request_timeout_seconds", 45)
    client = gem.make_client("test-key")
    assert client._api_client._http_options.timeout == 45000


def test_the_calls_reach_the_documents_meta(monkeypatch):
    import app.services.ocr as ocr

    class Recorded(gem.GeminiProvider):
        def __init__(self):
            self.calls = [{"model": "m", "seconds": 1.5, "outcome": "timeout"},
                          {"model": "f", "seconds": 4.0, "outcome": "ok"}]
            self.name = "gemini"

        def classify(self, *_a):
            return "invoice"

        def extract(self, *_a):
            return {}

    monkeypatch.setattr(ocr, "get_provider", lambda: Recorded())
    monkeypatch.setattr(settings, "ocr_party_check_enabled", False)
    meta = ocr.process_document("d", b"img", "image/jpeg", "invoice")["meta"]
    assert meta["ai_calls"]["count"] == 2
    assert meta["ai_calls"]["seconds"] == 5.5


# --- a model that hung or is overloaded is tried last for a while -------------

def test_after_a_timeout_the_next_scan_starts_on_the_fallback(monkeypatch):
    ok = json.dumps({"patient": {"name": {"value": "Alice"}}})
    p = _provider(monkeypatch, [TimedOut(), ok, ok], retries=2)
    p.extract(b"img", "image/jpeg", "prescription")
    # The next scan does not wait on the primary again.
    p.extract(b"img", "image/jpeg", "prescription")
    assert p._client.models.calls == ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash"]


def test_an_overloaded_primary_rests_too(monkeypatch):
    ok = json.dumps({"patient": {"name": {"value": "Alice"}}})
    p = _provider(monkeypatch, [Overloaded("503 overloaded"), Overloaded("503 overloaded"), ok, ok],
                  retries=2)
    p.extract(b"img", "image/jpeg", "prescription")
    p.extract(b"img", "image/jpeg", "prescription")
    assert p._client.models.calls[-1] == "gemini-2.0-flash"
    assert p._client.models.calls.count("gemini-2.5-flash") == 2  # not tried on the second scan


def test_a_rested_model_is_used_again_once_its_rest_is_over(monkeypatch):
    monkeypatch.setattr(settings, "ocr_model_rest_seconds", 0)
    ok = json.dumps({"patient": {"name": {"value": "Alice"}}})
    p = _provider(monkeypatch, [TimedOut(), ok, ok], retries=2)
    p.extract(b"img", "image/jpeg", "prescription")
    p.extract(b"img", "image/jpeg", "prescription")
    assert p._client.models.calls[-1] == "gemini-2.5-flash"


def test_when_both_are_resting_the_primary_still_goes_first(monkeypatch):
    gem.rest_model("gemini-2.5-flash")
    gem.rest_model("gemini-2.0-flash")
    ok = json.dumps({"patient": {"name": {"value": "Alice"}}})
    p = _provider(monkeypatch, [ok], retries=2)
    p.extract(b"img", "image/jpeg", "prescription")
    assert p._client.models.calls == ["gemini-2.5-flash"]


def test_the_reviewer_asks_the_model_that_did_not_read(monkeypatch):
    p = _provider(monkeypatch, [json.dumps({"supplier": {}}), json.dumps({"match": ["H1"]})])
    p.extract(b"%PDF", "application/pdf", "invoice")
    answer = p.review_json("check", b"img", "image/jpeg")
    assert answer == {"match": ["H1"]}
    assert p._client.models.calls == ["gemini-2.5-flash", "gemini-2.0-flash"]


def test_the_reviewer_falls_back_to_the_reader_when_the_other_is_busy(monkeypatch):
    p = _provider(monkeypatch, [json.dumps({"supplier": {}}), Overloaded("503"), Overloaded("503"),
                                Overloaded("503"), json.dumps({"match": []})])
    p.extract(b"%PDF", "application/pdf", "invoice")
    assert p.review_json("check", b"img", "image/jpeg") == {"match": []}
    assert p._client.models.calls[-1] == "gemini-2.5-flash"


def test_a_reviewer_answer_that_is_not_an_object_is_none(monkeypatch):
    p = _provider(monkeypatch, [json.dumps(["H1"])])
    assert p.review_json("check", b"img", "image/jpeg") is None


def test_each_call_records_which_key_served_it_never_the_key(monkeypatch):
    from app.services.ocr.key_pool import KeyPool

    clients = {}

    def factory(key):
        clients[key] = FakeClient([json.dumps({"supplier": {}})] * 4)
        return clients[key]

    monkeypatch.setattr(settings, "ocr_model", "gemini-2.5-flash")
    monkeypatch.setattr(settings, "ocr_fallback_model", "gemini-2.0-flash")
    pool = KeyPool(["key-one", "key-two"], client_factory=factory)
    p = gem.GeminiProvider(sleep=lambda _s: None, key_pool=pool)
    p.extract(b"%PDF", "application/pdf", "invoice")
    p.extract(b"%PDF", "application/pdf", "invoice")
    assert [c["key"] for c in p.calls] == [1, 2]          # the keys take turns
    assert "key-one" not in json.dumps(p.calls)


def test_health_says_how_many_keys_are_loaded(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(settings, "gemini_api_keys", "a,b")
    monkeypatch.setattr(settings, "gemini_api_key", None)
    body = TestClient(app).get("/health").json()
    assert body == {"status": "ok", "ai_keys": 2}
