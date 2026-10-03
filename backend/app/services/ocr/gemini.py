"""Gemini OCR provider using the current `google-genai` SDK.

Handles the common "model overloaded" (503) and rate-limit (429) errors with
exponential-backoff retries, then falls back to a secondary model, before
finally failing. This keeps scans working when Gemini is briefly busy instead of
surfacing an error to the pharmacist on every hiccup.
"""
import json
import logging
import time

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
    return any(m in msg for m in _TRANSIENT_MARKERS)


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
        from app.services.ocr.key_pool import KeyPool

        self._key_pool = key_pool or KeyPool(keys) if keys else None
        self._client = None
        self._primary = settings.ocr_model
        self._fallback = settings.ocr_fallback_model
        self._max_retries = max(1, settings.ocr_max_retries)
        self._base_backoff = settings.ocr_base_backoff
        self._sleep = sleep

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
        """One or more attempts against a single model with key rotation and backoff on transient errors."""
        last_exc = None
        for attempt in range(1, self._max_retries + 1):
            if self._client is not None:
                client = self._client
                key = "default"
            elif self._key_pool is not None:
                client, key = self._key_pool.get_client()
            else:
                client = self._genai.Client(api_key=settings.gemini_api_key)
                key = "default"

            try:
                return client.models.generate_content(
                    model=model, contents=contents, config=config
                )
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if self._is_rate_limit(exc) and self._key_pool is not None:
                    self._key_pool.mark_rate_limited(key, cooldown_seconds=45.0)
                    if len(self._key_pool) > 1 and attempt < self._max_retries:
                        continue
                if not _is_transient(exc) or attempt == self._max_retries:
                    raise
                # Exponential backoff (capped at 60s), for rate-limit recovery.
                self._sleep(min(60.0, self._base_backoff * (2 ** (attempt - 1))))
        raise last_exc  # pragma: no cover

    def _generate_with_fallback(self, contents, config=None):
        """Try the primary model (with retries); on persistent transient failure,
        try the fallback model (with retries). Non-transient errors propagate."""
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
                    raise OCRError(
                        f"AI service is busy (both models overloaded). Please retry. [{_brief(exc2)}]",
                        kind="busy",
                    ) from exc2
            if _is_transient(exc):
                raise OCRError(
                    f"AI service is busy. Please retry in a moment. [{_brief(exc)}]", kind="busy"
                ) from exc
            raise OCRError(f"AI request was rejected: {_brief(exc)}", kind="rejected") from exc

    def classify(self, file_bytes: bytes, content_type: str) -> str:
        resp = self._generate_with_fallback(
            self._content_parts(CLASSIFY_PROMPT, file_bytes, content_type)
        )
        text = (getattr(resp, "text", "") or "").strip().lower()
        return "invoice" if "invoice" in text else "prescription"

    def extract(self, file_bytes: bytes, content_type: str, doc_type: str) -> dict:
        prompt = EXTRACTION_PROMPT.get(doc_type)
        model_cls = FIELDS_MODEL.get(doc_type)
        if prompt is None or model_cls is None:
            raise OCRError(f"Unsupported doc_type: {doc_type!r}")

        from google.genai import types

        contents = self._content_parts(prompt, file_bytes, content_type)
        try:
            resp = self._generate_with_fallback(
                contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_json_schema=compact_schema(model_cls),
                    max_output_tokens=settings.ocr_max_output_tokens,
                ),
            )
        except OCRError as exc:
            if not _is_schema_rejection(exc):
                raise
            # Still too complex for this model. The prompt spells out every
            # field, and validate_fields checks the answer's shape afterwards,
            # so ask for plain JSON rather than failing the scan.
            log.warning("Gemini rejected the %s response schema; retrying without it: %s", doc_type, exc)
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
