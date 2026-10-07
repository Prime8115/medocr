"""Gemini OCR provider using the current `google-genai` SDK.

Handles the common "model overloaded" (503) and rate-limit (429) errors with
exponential-backoff retries, then falls back to a secondary model, before
finally failing. This keeps scans working when Gemini is briefly busy instead of
surfacing an error to the pharmacist on every hiccup.
"""
import json
import logging
import re
import time
from typing import Optional, Tuple

from app.config import settings
from app.schemas.extraction import FIELDS_MODEL
from app.services.ocr.base import OCRError, OCRProvider
from app.services.ocr.prompts import CLASSIFY_PROMPT, EXTRACTION_PROMPT

log = logging.getLogger(__name__)

# Substrings/codes that indicate a transient, retryable condition.
_TRANSIENT_MARKERS = (
    "503", "overloaded", "unavailable", "429", "resource_exhausted",
    "rate limit", "try again", "timeout", "deadline", "500", "internal",
)


def _is_transient(exc: Exception) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code in (429, 500, 503):
        return True
    msg = str(exc).lower()
    # httpx's ReadTimeout says "timed out", not "timeout"; a request that ran
    # past its limit is as transient as a 503.
    return any(m in msg for m in _TRANSIENT_MARKERS) or _is_timeout(exc)


def _rate_limit_info(exc: Exception) -> Tuple[Optional[float], bool]:
    """(seconds Gemini says to wait, whether the DAILY quota ran out).

    A 429 from Gemini carries a RetryInfo ("retryDelay": "37s") and a
    QuotaFailure naming the quota - "...PerDay..." when it is the day's quota,
    which no amount of short retrying will bring back. Read from the structured
    error when there is one, else from its text.
    """
    retry_after: Optional[float] = None
    daily = False
    details = getattr(exc, "details", None)
    entries = []
    if isinstance(details, dict):
        entries = (details.get("error") or details).get("details") or []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        delay = entry.get("retryDelay")
        if delay:
            try:
                retry_after = float(str(delay).rstrip("s"))
            except ValueError:
                pass
        for violation in entry.get("violations") or []:
            if "perday" in str(violation.get("quotaId", "")).lower():
                daily = True
    text = str(exc)
    if retry_after is None:
        m = re.search(r"retry(?:Delay|\s+in)['\":\s]+([\d.]+)\s*s", text, re.I)
        if m:
            retry_after = float(m.group(1))
    if not daily and re.search(r"per\s*day", text, re.I):
        daily = True
    return retry_after, daily


def make_client(api_key: str):
    """A Gemini client whose every request gives up after
    OCR_REQUEST_TIMEOUT_SECONDS instead of waiting on an overloaded model."""
    from google import genai
    from google.genai import types

    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=int(settings.ocr_request_timeout_seconds * 1000)),
    )


def _is_timeout(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    return "timeout" in name or "timed out" in msg or "deadline" in msg


def _brief(exc: Exception, limit: int = 300) -> str:
    """The error as one short line. Gemini errors embed the whole JSON response,
    which is unreadable on a phone screen and bloats the stored document."""
    text = " ".join(str(exc).split()) or type(exc).__name__
    return text if len(text) <= limit else text[: limit - 1] + "…"


def compact_schema(model_cls) -> dict:
    """The response schema in the smallest form Gemini will accept.

    Gemini rejects a structured-output schema it judges too complex to serve
    ("too many states"). Pydantic's own schema is far larger than the shape it
    describes: a title on every property, docstrings as descriptions, the full
    default object repeated on every nested field, and every nullable value
    spelled as an anyOf. Doubling the invoice fields for an integrating client
    doubled all of that.

    None of it constrains the output, so it is dropped: title, description and
    default removed, and `anyOf: [X, null]` becomes `type: [X, "null"]`. The
    shared models stay under $defs, so the {value, confidence} leaf is described
    once rather than 78 times, and that leaf requires both keys - a fixed shape
    is cheaper for the model than an optional one. Every field is kept: a
    scanned invoice must return the same fields as a digital one.
    """

    def walk(node):
        if isinstance(node, list):
            return [walk(n) for n in node]
        if not isinstance(node, dict):
            return node
        branches = node.get("anyOf")
        if branches and len(branches) == 2 and {"type": "null"} in branches:
            other = walk(next(b for b in branches if b != {"type": "null"}))
            if set(other) == {"type"} and isinstance(other["type"], str):
                return {"type": [other["type"], "null"]}
        out = {}
        for k, v in node.items():
            if k in ("title", "description", "default"):
                continue
            # Under "properties"/"$defs" the keys are names, not keywords.
            out[k] = {n: walk(d) for n, d in v.items()} if k in ("properties", "$defs") else walk(v)
        if set(out.get("properties", {})) == {"value", "confidence"}:
            out["required"] = ["value", "confidence"]
        return out

    return walk(model_cls.model_json_schema())


# (model, doc_type) pairs whose response schema the model has refused in this
# process. gemini-flash-lite-latest refuses the invoice schema every time, so
# without this every invoice paid for a request that could only fail first.
# Per process, not persisted: a model that starts accepting it is noticed on
# the next restart (and each deploy restarts the worker).
_SCHEMA_REFUSED: set = set()


def _is_schema_rejection(exc: OCRError) -> bool:
    """Gemini refused a request that carried a response schema.

    It does not always say so: production showed the invoice extraction failing
    on every attempt with nothing but "400 INVALID_ARGUMENT. Request contains an
    invalid argument." - while the very same text, sent with the (smaller)
    prescription schema, was read fine. A bare INVALID_ARGUMENT on a schema'd
    request is therefore treated as the schema being refused, and the request
    is retried once without it. A bad key or model reads differently ("API key
    not valid", "not found") and is not retried.
    """
    msg = str(exc).lower()
    if exc.kind != "rejected":
        return False
    return (
        "schema" in msg
        or "too many states" in msg
        or "invalid_argument" in msg
        or "invalid argument" in msg
    )


def parse_classification(answer: str) -> Optional[str]:
    """'invoice' or 'prescription' when the AI's answer names exactly one of
    them; None when it names neither or both - the caller decides what an
    unclear answer means, rather than this quietly calling it a prescription."""
    words = set(re.findall(r"[a-z]+", answer.lower()))
    is_invoice = bool(words & {"invoice", "bill"})
    is_rx = "prescription" in words
    if is_invoice != is_rx:
        return "invoice" if is_invoice else "prescription"
    return None


class GeminiProvider(OCRProvider):
    name = "gemini"

    def __init__(self, sleep=time.sleep, key_pool=None):
        keys = settings.gemini_keys_list
        if not keys and not key_pool:
            raise OCRError("OCR not configured: GEMINI_API_KEY is missing.")
        try:
            from google import genai  # imported lazily
        except ImportError as exc:  # pragma: no cover
            raise OCRError("google-genai is not installed.") from exc
        self._genai = genai
        from app.services.ocr.key_pool import shared_pool

        # Shared across scans, so a cooldown or the day's quota learned on one
        # scan is not rediscovered - by being refused - on every next one.
        self._key_pool = key_pool or (shared_pool(keys, settings.ocr_rpm_per_key) if keys else None)
        self._client = None
        self._primary = settings.ocr_model
        self._fallback = settings.ocr_fallback_model
        self._max_retries = max(1, settings.ocr_max_retries)
        self._base_backoff = settings.ocr_base_backoff
        self._sleep = sleep
        # How many extractions Gemini only answered once the response schema
        # was dropped. Zero in a healthy system; the nightly live test reports it.
        self.schema_fallbacks = 0
        # Every request made, with its model, duration and outcome - recorded in
        # the document's meta so a slow scan can be explained afterwards.
        self.calls: list = []

    def _content_parts(self, prompt: str, file_bytes: bytes, content_type: str) -> list:
        """Build the model input: raw text for digital PDFs (compact, reliable),
        otherwise the media (image/PDF) part for the vision model."""
        if content_type == "text/plain":
            text = file_bytes.decode("utf-8", "replace")
            return [prompt, "DOCUMENT TEXT (extract structured data from this):\n\n" + text]
        mime = content_type if content_type == "application/pdf" or content_type.startswith(
            "image/"
        ) else "image/jpeg"
        return [prompt, {"inline_data": {"data": file_bytes, "mimeType": mime}}]

    def _is_rate_limit(self, exc: Exception) -> bool:
        code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        if code == 429:
            return True
        msg = str(exc).lower()
        return "429" in msg or "resource_exhausted" in msg or "rate limit" in msg

    def _generate(self, model: str, contents, config=None):
        """Attempts against one model: through the shared key pool (which rotates
        keys, paces requests and remembers cooldowns), else a single client with
        backoff. Raises QuotaWait when every key is out for this model."""
        from app.services.ocr.key_pool import QuotaWait

        last_exc = None
        for attempt in range(1, self._max_retries + 1):
            if self._client is not None:
                client, key = self._client, "default"
            elif self._key_pool is not None:
                client, key = self._key_pool.acquire(
                    model, max_wait=settings.ocr_rate_wait_max_seconds, sleep=self._sleep
                )
            else:
                client = make_client(settings.gemini_api_key)
                key = "default"

            started = time.monotonic()
            try:
                resp = client.models.generate_content(
                    model=model, contents=contents, config=config
                )
                self.calls.append({"model": model, "seconds": round(time.monotonic() - started, 1),
                                   "outcome": "ok"})
                return resp
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                self.calls.append({"model": model, "seconds": round(time.monotonic() - started, 1),
                                   "outcome": "timeout" if _is_timeout(exc) else _brief(exc, 80)})
                if _is_timeout(exc):
                    # A model that let one request hang will most likely hang
                    # the next: go to the fallback now, not after another wait.
                    raise
                if self._is_rate_limit(exc):
                    retry_after, daily = _rate_limit_info(exc)
                    if self._key_pool is not None:
                        # The pool now knows; the next acquire() picks another
                        # key, waits briefly, or raises QuotaWait.
                        self._key_pool.report_rate_limit(key, model, retry_after, daily)
                        if attempt < self._max_retries:
                            continue
                    raise QuotaWait(model, retry_after or 60.0, daily) from exc
                if not _is_transient(exc) or attempt == self._max_retries:
                    raise
                # Exponential backoff (capped at 60s) on an overloaded model.
                self._sleep(min(60.0, self._base_backoff * (2 ** (attempt - 1))))
        raise last_exc  # pragma: no cover

    def _generate_with_fallback(self, contents, config=None):
        """Try the primary model (with retries); on persistent transient failure,
        try the fallback model, which has a quota of its own. Non-transient errors
        propagate. A busy outcome says how long to wait, and whether it is the
        day's quota on both models."""
        from app.services.ocr.key_pool import QuotaWait

        def busy(exc: Exception, others: Tuple[Exception, ...] = ()) -> OCRError:
            waits = [e for e in (exc,) + others if isinstance(e, QuotaWait)]
            retry_after = min((w.retry_after for w in waits), default=None)
            daily = bool(waits) and len(waits) == 1 + len(others) and all(w.daily for w in waits)
            return OCRError(
                f"AI service is busy. Please retry in a moment. [{_brief(exc)}]",
                kind="busy", retry_after=retry_after, daily_quota=daily,
            )

        try:
            return self._generate(self._primary, contents, config)
        except Exception as exc:  # noqa: BLE001
            if self._fallback and self._fallback != self._primary and _is_transient(exc):
                try:
                    return self._generate(self._fallback, contents, config)
                except Exception as exc2:  # noqa: BLE001
                    if not _is_transient(exc2):
                        raise OCRError(
                            f"AI request was rejected: {_brief(exc2)}", kind="rejected"
                        ) from exc2
                    raise busy(exc2, (exc,)) from exc2
            if _is_transient(exc):
                raise busy(exc) from exc
            raise OCRError(f"AI request was rejected: {_brief(exc)}", kind="rejected") from exc

    def classify(self, file_bytes: bytes, content_type: str) -> str:
        from google.genai import types

        resp = self._generate_with_fallback(
            self._content_parts(CLASSIFY_PROMPT, file_bytes, content_type),
            config=types.GenerateContentConfig(temperature=settings.ocr_temperature),
        )
        return parse_classification(getattr(resp, "text", "") or "")

    def complete_json(self, prompt: str) -> dict:
        """A JSON answer to a plain-text prompt - for filling named gaps in a
        reading (gap_fill.py). The whole document is never re-sent as an image;
        only its text and the few fields wanted."""
        from google.genai import types

        resp = self._generate_with_fallback(
            [prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                max_output_tokens=2048,
            ),
        )
        raw = (getattr(resp, "text", "") or "").strip()
        try:
            out = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OCRError(f"Model returned unreadable output: {exc}", kind="output") from exc
        return out if isinstance(out, dict) else {}

    def extract(self, file_bytes: bytes, content_type: str, doc_type: str) -> dict:
        prompt = EXTRACTION_PROMPT.get(doc_type)
        model_cls = FIELDS_MODEL.get(doc_type)
        if prompt is None or model_cls is None:
            raise OCRError(f"Unsupported doc_type: {doc_type!r}")

        from google.genai import types

        contents = self._content_parts(prompt, file_bytes, content_type)
        refused_key = (self._primary, doc_type)
        if refused_key in _SCHEMA_REFUSED:
            # This model has already refused this schema in this process; the
            # schema'd request would only fail again, costing a call and its
            # latency on every single invoice.
            self.schema_fallbacks += 1
            resp = self._generate_with_fallback(
                contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    max_output_tokens=settings.ocr_max_output_tokens,
                ),
            )
            raw = (getattr(resp, "text", "") or "").strip()
            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                raise OCRError(f"Model returned unreadable output: {exc}", kind="output") from exc
        try:
            resp = self._generate_with_fallback(
                contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_json_schema=compact_schema(model_cls),
                    max_output_tokens=settings.ocr_max_output_tokens,
                    temperature=settings.ocr_temperature,
                ),
            )
        except OCRError as exc:
            if not _is_schema_rejection(exc):
                raise
            # Still too complex for this model. The prompt spells out every
            # field, and validate_fields checks the answer's shape afterwards,
            # so ask for plain JSON rather than failing the scan.
            log.warning("Gemini rejected the %s response schema; retrying without it: %s", doc_type, exc)
            self.schema_fallbacks += 1
            _SCHEMA_REFUSED.add(refused_key)
            resp = self._generate_with_fallback(
                contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    max_output_tokens=settings.ocr_max_output_tokens,
                    temperature=settings.ocr_temperature,
                ),
            )

        raw = (getattr(resp, "text", "") or "").strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OCRError(f"Model returned unreadable output: {exc}", kind="output") from exc
