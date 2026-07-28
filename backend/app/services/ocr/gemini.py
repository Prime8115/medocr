"""Gemini OCR provider using the current `google-genai` SDK."""
import json

from app.config import settings
from app.schemas.extraction import FIELDS_MODEL
from app.services.ocr.base import OCRError, OCRProvider
from app.services.ocr.prompts import CLASSIFY_PROMPT, EXTRACTION_PROMPT


class GeminiProvider(OCRProvider):
    name = "gemini"

    def __init__(self):
        if not settings.gemini_api_key:
            raise OCRError("OCR not configured: GEMINI_API_KEY is missing.")
        try:
            from google import genai  # imported lazily
        except ImportError as exc:  # pragma: no cover
            raise OCRError("google-genai is not installed.") from exc
        self._genai = genai
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._model = settings.ocr_model

    def _media_part(self, file_bytes: bytes, content_type: str) -> dict:
        mime = content_type if content_type in ("application/pdf",) or content_type.startswith(
            "image/"
        ) else "image/jpeg"
        return {"inline_data": {"data": file_bytes, "mimeType": mime}}

    def classify(self, file_bytes: bytes, content_type: str) -> str:
        try:
            resp = self._client.models.generate_content(
                model=self._model,
                contents=[CLASSIFY_PROMPT, self._media_part(file_bytes, content_type)],
            )
            text = (resp.text or "").strip().lower()
        except Exception as exc:  # noqa: BLE001
            raise OCRError(f"Classification failed: {exc}") from exc
        return "invoice" if "invoice" in text else "prescription"

    def extract(self, file_bytes: bytes, content_type: str, doc_type: str) -> dict:
        prompt = EXTRACTION_PROMPT.get(doc_type)
        model_cls = FIELDS_MODEL.get(doc_type)
        if prompt is None or model_cls is None:
            raise OCRError(f"Unsupported doc_type: {doc_type!r}")

        from google.genai import types

        try:
            resp = self._client.models.generate_content(
                model=self._model,
                contents=[prompt, self._media_part(file_bytes, content_type)],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_json_schema=model_cls.model_json_schema(),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            raise OCRError(f"Extraction failed: {exc}") from exc

        raw = (resp.text or "").strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OCRError(f"Model returned non-JSON output: {exc}") from exc
