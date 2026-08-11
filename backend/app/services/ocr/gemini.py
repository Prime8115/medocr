"""Gemini OCR provider using the current `google-genai` SDK.

Handles the common "model overloaded" (503) and rate-limit (429) errors with
exponential-backoff retries, then falls back to a secondary model, before
finally failing. This keeps scans working when Gemini is briefly busy instead of
surfacing an error to the pharmacist on every hiccup.
"""
import json
import time

from app.config import settings
from app.schemas.extraction import FIELDS_MODEL
from app.services.ocr.base import OCRError, OCRProvider
from app.services.ocr.prompts import CLASSIFY_PROMPT, EXTRACTION_PROMPT

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


class GeminiProvider(OCRProvider):
    name = "gemini"

    def __init__(self, sleep=time.sleep):
        if not settings.gemini_api_key:
            raise OCRError("OCR not configured: GEMINI_API_KEY is missing.")
        try:
            from google import genai  # imported lazily
        except ImportError as exc:  # pragma: no cover
            raise OCRError("google-genai is not installed.") from exc
        self._genai = genai
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._primary = settings.ocr_model
        self._fallback = settings.ocr_fallback_model
        self._max_retries = max(1, settings.ocr_max_retries)
        self._base_backoff = settings.ocr_base_backoff
        self._sleep = sleep

    def _media_part(self, file_bytes: bytes, content_type: str) -> dict:
        mime = content_type if content_type == "application/pdf" or content_type.startswith(
            "image/"
        ) else "image/jpeg"
        return {"inline_data": {"data": file_bytes, "mimeType": mime}}

    def _generate(self, model: str, contents, config=None):
        """One or more attempts against a single model with backoff on transient errors."""
        last_exc = None
        for attempt in range(1, self._max_retries + 1):
            try:
                return self._client.models.generate_content(
                    model=model, contents=contents, config=config
                )
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if not _is_transient(exc) or attempt == self._max_retries:
                    raise
                # Exponential backoff: base, 2x, 4x, ...
                self._sleep(self._base_backoff * (2 ** (attempt - 1)))
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
                    raise OCRError(
                        f"AI service is busy (both models overloaded). Please retry. [{exc2}]"
                    ) from exc2
            if _is_transient(exc):
                raise OCRError(f"AI service is busy. Please retry in a moment. [{exc}]") from exc
            raise OCRError(f"Extraction failed: {exc}") from exc

    def classify(self, file_bytes: bytes, content_type: str) -> str:
        resp = self._generate_with_fallback(
            [CLASSIFY_PROMPT, self._media_part(file_bytes, content_type)]
        )
        text = (getattr(resp, "text", "") or "").strip().lower()
        return "invoice" if "invoice" in text else "prescription"

    def extract(self, file_bytes: bytes, content_type: str, doc_type: str) -> dict:
        prompt = EXTRACTION_PROMPT.get(doc_type)
        model_cls = FIELDS_MODEL.get(doc_type)
        if prompt is None or model_cls is None:
            raise OCRError(f"Unsupported doc_type: {doc_type!r}")

        from google.genai import types

        resp = self._generate_with_fallback(
            [prompt, self._media_part(file_bytes, content_type)],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=model_cls.model_json_schema(),
                max_output_tokens=settings.ocr_max_output_tokens,
            ),
        )

        raw = (getattr(resp, "text", "") or "").strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OCRError(f"Model returned unreadable output: {exc}") from exc
