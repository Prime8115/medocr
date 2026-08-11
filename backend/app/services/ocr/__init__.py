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
from app.services.ocr.pdf_utils import page_count, split_pdf

__all__ = ["process_document", "get_provider", "OCRError"]

# List keys per doc type that accumulate across PDF page-chunks.
_LIST_KEY = {"invoice": "line_items", "prescription": "medications"}


def get_provider() -> OCRProvider:
    if settings.allow_mock_ocr:
        from app.services.ocr.mock import MockProvider

        return MockProvider()
    from app.services.ocr.gemini import GeminiProvider

    return GeminiProvider()


def _overall_confidence(fields: dict):
    confs = [c for _, v, c in _walk_fields(fields) if v not in (None, "") and c is not None]
    return round(sum(confs) / len(confs), 3) if confs else None


def _merge_fields(doc_type: str, base: dict, incoming: dict) -> dict:
    """Merge a later chunk's fields into the accumulated fields.

    Header/single fields are taken from the first chunk that populated them;
    the repeating list (line_items / medications) is concatenated.
    """
    if base is None:
        return incoming
    list_key = _LIST_KEY.get(doc_type)
    for key, val in incoming.items():
        if key == list_key:
            base[key] = (base.get(key) or []) + (val or [])
        elif key not in base or not base.get(key):
            base[key] = val
        elif isinstance(base.get(key), dict) and isinstance(val, dict):
            # Fill any still-empty header sub-fields from this chunk.
            for k, v in val.items():
                cur = base[key].get(k)
                if not cur or (isinstance(cur, dict) and not cur.get("value")):
                    base[key][k] = v
    return base


def _extract_one(provider: OCRProvider, chunk: bytes, content_type: str, doc_type: str) -> dict:
    raw = provider.extract(chunk, content_type, doc_type)
    return validate_fields(doc_type, raw)


def _process_chunk(provider, chunk, content_type, doc_type, is_pdf):
    """Process one chunk. On truncation, adaptively re-split into single pages.
    Returns (merged_fields_or_None, failed_pages)."""
    try:
        return _extract_one(provider, chunk, content_type, doc_type), 0
    except (OCRError, ValueError):
        pages = split_pdf(chunk, 1) if is_pdf else [chunk]
        if len(pages) <= 1:
            return None, 1
        merged, failed = None, 0
        for page in pages:
            try:
                merged = _merge_fields(doc_type, merged, _extract_one(provider, page, content_type, doc_type))
            except (OCRError, ValueError):
                failed += 1
        return merged, failed


def _extract_chunked(provider, file_bytes, content_type, doc_type, on_progress=None):
    """Extract from a (possibly long) document by splitting a PDF into page-chunks,
    processing them **in parallel**, and merging results in page order.

    Scales to large invoices (60+ pages): concurrency and chunk size are
    configurable, a truncated chunk is adaptively re-split to single pages, and a
    page that still can't be read is skipped rather than failing the whole doc.
    Returns (fields, failed_pages, total_pages).
    """
    import concurrent.futures as cf

    is_pdf = content_type == "application/pdf"
    total_pages = page_count(file_bytes) if is_pdf else 1
    chunks = [file_bytes]
    if is_pdf and total_pages > settings.ocr_pdf_chunk_pages:
        chunks = split_pdf(file_bytes, settings.ocr_pdf_chunk_pages)

    single_input = len(chunks) == 1

    # Fast path: a single chunk (small doc) — run inline and surface errors.
    if single_input:
        fields, failed = _process_chunk(provider, chunks[0], content_type, doc_type, is_pdf)
        if fields is None:
            raise OCRError("Could not read the document.")
        return fields, failed, total_pages

    # Parallel path: process chunks concurrently, merge in original page order.
    results: dict[int, tuple] = {}
    workers = max(1, min(settings.ocr_chunk_concurrency, len(chunks)))
    done = 0
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_process_chunk, provider, ch, content_type, doc_type, is_pdf): i
            for i, ch in enumerate(chunks)
        }
        for fut in cf.as_completed(futures):
            i = futures[fut]
            results[i] = fut.result()
            done += 1
            if on_progress:
                try:
                    on_progress(min(done * settings.ocr_pdf_chunk_pages, total_pages), total_pages)
                except Exception:  # noqa: BLE001 - progress must never break extraction
                    pass

    merged = None
    failed_pages = 0
    for i in range(len(chunks)):
        fields, failed = results.get(i, (None, 0))
        failed_pages += failed
        if fields is not None:
            merged = _merge_fields(doc_type, merged, fields)

    if merged is None:
        raise OCRError("Could not read any page of the document.")
    return merged, failed_pages, total_pages


def process_document(document_id: str, file_bytes: bytes, content_type: str, doc_type=None, on_progress=None) -> dict:
    provider = get_provider()

    # Classify on the first page/chunk only (cheaper for long PDFs).
    if not doc_type:
        classify_bytes = file_bytes
        if content_type == "application/pdf" and page_count(file_bytes) > 1:
            classify_bytes = split_pdf(file_bytes, 1)[0]
        doc_type = provider.classify(classify_bytes, content_type)
    resolved_type = doc_type if doc_type in ("prescription", "invoice") else "prescription"

    fields, failed_pages, total_pages = _extract_chunked(
        provider, file_bytes, content_type, resolved_type, on_progress=on_progress
    )
    fields = postprocess_fields(resolved_type, fields)

    pages = page_count(file_bytes) if content_type == "application/pdf" else 1
    list_key = _LIST_KEY.get(resolved_type)
    item_count = len(fields.get(list_key, []) or []) if list_key else 0

    warnings = collect_low_confidence(fields, settings.low_confidence_threshold)
    if failed_pages:
        warnings.insert(
            0, f"{failed_pages} of {total_pages} page(s) could not be read; review may be incomplete."
        )

    meta = ExtractionMeta(
        overall_confidence=_overall_confidence(fields),
        language="en",
        pipeline=provider.name,
        processed_at=time.time(),
        warnings=warnings,
    ).model_dump()
    meta["pages"] = pages
    meta["item_count"] = item_count
    meta["pages_failed"] = failed_pages

    return {
        "schema_version": SCHEMA_VERSION,
        "doc_type": resolved_type,
        "fields": fields,
        "meta": meta,
    }
