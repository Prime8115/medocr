"""OCR orchestration: provider selection, extraction, validation, post-processing.

`process_document` returns a full versioned ExtractionPayload dict, or raises
`OCRError` on any failure. It NEVER returns fabricated data.
"""
import time

from app.config import settings
from app.schemas.extraction import (
    SCHEMA_VERSION,
    ExtractionMeta,
    _walk_fields,
    collect_low_confidence,
    validate_fields,
)
from app.services.ocr.base import OCRError, OCRProvider
from app.services.ocr.postprocess import postprocess_fields

__all__ = ["process_document", "get_provider", "OCRError"]


def get_provider() -> OCRProvider:
    if settings.allow_mock_ocr:
        from app.services.ocr.mock import MockProvider

        return MockProvider()
    from app.services.ocr.gemini import GeminiProvider

    return GeminiProvider()


def _overall_confidence(fields: dict):
    confs = [c for _, v, c in _walk_fields(fields) if v not in (None, "") and c is not None]
    return round(sum(confs) / len(confs), 3) if confs else None


def process_document(document_id: str, file_bytes: bytes, content_type: str, doc_type=None) -> dict:
    provider = get_provider()

    resolved_type = doc_type or provider.classify(file_bytes, content_type)
    if resolved_type not in ("prescription", "invoice"):
        resolved_type = "prescription"

    raw_fields = provider.extract(file_bytes, content_type, resolved_type)

    try:
        fields = validate_fields(resolved_type, raw_fields)
    except ValueError as exc:
        raise OCRError(str(exc)) from exc

    fields = postprocess_fields(resolved_type, fields)

    meta = ExtractionMeta(
        overall_confidence=_overall_confidence(fields),
        language="en",
        pipeline=provider.name,
        processed_at=time.time(),
        warnings=collect_low_confidence(fields, settings.low_confidence_threshold),
    ).model_dump()

    return {
        "schema_version": SCHEMA_VERSION,
        "doc_type": resolved_type,
        "fields": fields,
        "meta": meta,
    }
