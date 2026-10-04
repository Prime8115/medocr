"""Gemini's rate limits: honoured, remembered between scans, and - when the
day's quota is gone - not waited out for a day."""
from datetime import datetime, timezone

import pytest

import app.services.ocr.gemini as gem
from app.config import settings
from app.services.ocr.base import OCRError
from app.services.ocr.key_pool import KeyPool, QuotaWait, next_quota_reset, shared_pool


class FakeClock:
    def __init__(self, t=1_760_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class GeminiQuota(Exception):
    """Shaped like google.genai's ClientError for a 429."""

    code = 429

    def __init__(self, quota_id="GenerateRequestsPerMinutePerProjectPerModel-FreeTier", delay="37s"):
        self.details = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": [
            {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
             "violations": [{"quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
                             "quotaId": quota_id}]},
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay},
        ]}}
        super().__init__(f"429 RESOURCE_EXHAUSTED. {self.details}")


class FakeResp:
    def __init__(self, text):
        self.text = text


class ScriptedClient:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.models = self

    def generate_content(self, model, contents, config=None):
        self.calls += 1
        item = self.script.pop(0) if self.script else '{"patient": {"name": {"value": "Alice"}}}'
        if isinstance(item, Exception):
            raise item
        return FakeResp(item)


# --- reading Gemini's 429 ---------------------------------------------------

def test_the_wait_gemini_asks_for_is_read():
    assert gem._rate_limit_info(GeminiQuota()) == (37.0, False)


def test_the_daily_quota_is_recognised():
    exc = GeminiQuota("GenerateRequestsPerDayPerProjectPerModel-FreeTier", "8s")
    assert gem._rate_limit_info(exc) == (8.0, True)


def test_a_429_without_details_is_read_from_its_text():
    assert gem._rate_limit_info(Exception("429 Please retry in 12.5s.")) == (12.5, False)
    assert gem._rate_limit_info(Exception("429 quota exceeded")) == (None, False)


# --- the pool -----------------------------------------------------------------

def test_requests_are_paced_per_minute():
    clock = FakeClock()
    pool = KeyPool(["k"], client_factory=lambda k: k, rpm_per_key=2, clock=clock)
    pool.acquire("m")
    pool.acquire("m")
    with pytest.raises(QuotaWait) as wait:
        pool.acquire("m")
    assert 59 <= wait.value.retry_after <= 60 and not wait.value.daily
    # Another model has its own quota.
    assert pool.acquire("other")[1] == "k"
    # A short wait is simply waited.
    clock.t += 50
    assert pool.acquire("m", max_wait=15, sleep=clock.sleep)[1] == "k"


def test_a_cooldown_is_per_model_and_as_long_as_gemini_said():
    clock = FakeClock()
    pool = KeyPool(["a", "b"], client_factory=lambda k: k, clock=clock)
    pool.report_rate_limit("a", "m", 30, daily=False)
    assert {pool.acquire("m")[1] for _ in range(3)} == {"b"}
    assert pool.is_in_cooldown("a", "m") and not pool.is_in_cooldown("a", "other")
    clock.t += 31
    assert not pool.is_in_cooldown("a", "m")


def test_daily_quota_rests_the_key_until_the_pacific_midnight():
    clock = FakeClock()
    pool = KeyPool(["a"], client_factory=lambda k: k, clock=clock)
    pool.report_rate_limit("a", "m", 5, daily=True)
    with pytest.raises(QuotaWait) as wait:
        pool.acquire("m", max_wait=20)
    assert wait.value.daily
    assert wait.value.retry_after == pytest.approx(next_quota_reset(clock.t) - clock.t)


def test_next_quota_reset_is_midnight_in_california():
    from zoneinfo import ZoneInfo

    reset = datetime.fromtimestamp(next_quota_reset(1_760_000_000.0), ZoneInfo("America/Los_Angeles"))
    assert (reset.hour, reset.minute) == (0, 0)
    assert 0 < next_quota_reset(1_760_000_000.0) - 1_760_000_000.0 <= 25 * 3600


def test_the_pool_is_shared_between_scans():
    first = shared_pool(["shared-test-key"], 10)
    first.report_rate_limit("shared-test-key", "m", 30, daily=False)
    assert shared_pool([" shared-test-key "], 10) is first
    assert shared_pool(["shared-test-key"], 10).is_in_cooldown("shared-test-key", "m")


# --- the provider -------------------------------------------------------------

def _provider(monkeypatch, clients, rpm=0):
    monkeypatch.setattr(settings, "ocr_model", "primary")
    monkeypatch.setattr(settings, "ocr_fallback_model", "secondary")
    monkeypatch.setattr(settings, "ocr_max_retries", 2)
    monkeypatch.setattr(settings, "ocr_rate_wait_max_seconds", 0)
    pool = KeyPool(list(clients), client_factory=clients.__getitem__, rpm_per_key=rpm)
    return gem.GeminiProvider(sleep=lambda _s: None, key_pool=pool), pool


def test_busy_error_carries_geminis_wait(monkeypatch):
    client = ScriptedClient([GeminiQuota(delay="37s"), GeminiQuota(delay="50s")])
    p, _ = _provider(monkeypatch, {"k": client})
    with pytest.raises(OCRError) as err:
        p.extract(b"img", "image/jpeg", "prescription")
    assert err.value.kind == "busy"
    assert 36 <= err.value.retry_after <= 50
    assert not err.value.daily_quota
    assert client.calls == 2  # once per model; no hammering a key Gemini just refused


def test_daily_quota_on_both_models_is_reported(monkeypatch):
    day = "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
    client = ScriptedClient([GeminiQuota(day), GeminiQuota(day)])
    p, _ = _provider(monkeypatch, {"k": client})
    with pytest.raises(OCRError) as err:
        p.extract(b"img", "image/jpeg", "prescription")
    assert err.value.kind == "busy" and err.value.daily_quota


def test_the_fallback_model_serves_when_the_primarys_day_is_over(monkeypatch):
    client = ScriptedClient([GeminiQuota("GenerateRequestsPerDayPerProjectPerModel-FreeTier")])
    p, pool = _provider(monkeypatch, {"k": client})
    assert p.extract(b"img", "image/jpeg", "prescription")["patient"]["name"]["value"] == "Alice"
    # The next scan does not even try the exhausted model.
    assert p.extract(b"img", "image/jpeg", "prescription")["patient"]["name"]["value"] == "Alice"
    assert client.calls == 3


# --- the queue ----------------------------------------------------------------

def _upload(client, headers):
    from tests.test_manual_entry import _text_pdf

    r = client.post("/v1/documents/", headers=headers,
                    files={"file": ("x.pdf", _text_pdf(), "application/pdf")}, data={"doc_type": "invoice"})
    assert r.status_code == 200, r.text
    return r.json()["document_id"]


def _job(doc_id):
    from app.api import documents as documents_api
    from app.models.job import OcrJob

    db = documents_api.SessionLocal()  # the test database, as conftest points it
    try:
        return db.query(OcrJob).filter(OcrJob.document_id == doc_id).order_by(OcrJob.created_at.desc()).first()
    finally:
        db.close()


def _busy(monkeypatch, **kw):
    from app.api import documents as documents_api

    def busy(*_a, **_k):
        raise OCRError("429 RESOURCE_EXHAUSTED", kind="busy", **kw)

    monkeypatch.setattr(documents_api, "process_document", busy)


def _as_utc(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def test_the_queue_waits_as_long_as_gemini_said(client, monkeypatch):
    from tests.conftest import register_and_login

    _busy(monkeypatch, retry_after=240)
    doc_id = _upload(client, register_and_login(client))
    job = _job(doc_id)
    assert job.status == "pending"
    wait = (_as_utc(job.run_after) - datetime.now(timezone.utc)).total_seconds()
    assert wait > 200  # not the default 20s, which Gemini would refuse again


def test_daily_quota_goes_straight_to_manual_entry(client, monkeypatch):
    from tests.conftest import register_and_login

    monkeypatch.setattr(settings, "ocr_daily_quota_mode", "manual_entry")
    _busy(monkeypatch, retry_after=30, daily_quota=True)
    headers = register_and_login(client)
    doc = client.get(f"/v1/documents/{_upload(client, headers)}", headers=headers).json()
    assert doc["status"] == "needs_review"
    assert doc["payload"]["meta"]["needs_manual_entry"] is True


def test_daily_quota_can_be_waited_out_instead(client, monkeypatch):
    from tests.conftest import register_and_login

    monkeypatch.setattr(settings, "ocr_daily_quota_mode", "wait")
    _busy(monkeypatch, retry_after=30, daily_quota=True)
    headers = register_and_login(client)
    doc_id = _upload(client, headers)
    assert client.get(f"/v1/documents/{doc_id}", headers=headers).json()["status"] == "queued"
    due = _as_utc(_job(doc_id).run_after).timestamp()
    assert due >= next_quota_reset() - 5
