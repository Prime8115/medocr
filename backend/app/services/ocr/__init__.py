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
from app.services.ocr.pdf_utils import (
    extract_text_pages,
    is_digital_pdf,
    page_count,
    split_pdf,
)

__all__ = ["process_document", "get_provider", "OCRError"]

# List keys per doc type that accumulate across PDF page-chunks.
_LIST_KEY = {"invoice": "line_items", "prescription": "medications"}

_PAGE_SEP = "\n\n----- PAGE BREAK -----\n\n"


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


def _extract_one(provider: OCRProvider, data: bytes, content_type: str, doc_type: str) -> dict:
    raw = provider.extract(data, content_type, doc_type)
    return validate_fields(doc_type, raw)


def _build_units(file_bytes: bytes, content_type: str):
    """Split the document into work units [(bytes, content_type, n_pages), ...].

    Digital PDFs -> compact TEXT units (many pages/call, reliable, rate-friendly).
    Scanned PDFs -> image PDF chunks (vision). Non-PDF -> a single unit.
    Returns (units, total_pages, resplittable).
    """
    if content_type != "application/pdf":
        return [(file_bytes, content_type, 1)], 1, False

    total = page_count(file_bytes)
    if is_digital_pdf(file_bytes):
        texts = extract_text_pages(file_bytes)
        cs = settings.ocr_text_chunk_pages
        units = []
        for i in range(0, len(texts), cs):
            group = texts[i:i + cs]
            units.append((_PAGE_SEP.join(group).encode("utf-8"), "text/plain", len(group)))
        return (units or [(b"", "text/plain", total)]), total, True

    cs = settings.ocr_pdf_chunk_pages
    if total <= cs:
        return [(file_bytes, "application/pdf", total)], total, False
    return [(c, "application/pdf", cs) for c in split_pdf(file_bytes, cs)], total, True


def _resplit_unit(data: bytes, content_type: str):
    """Break a failed unit into single-page units for a finer retry."""
    if content_type == "text/plain":
        return [(p.encode("utf-8"), "text/plain") for p in data.decode("utf-8", "replace").split(_PAGE_SEP)]
    return [(c, "application/pdf") for c in split_pdf(data, 1)]


def _process_unit(provider, data, content_type, doc_type):
    """Process one unit; on truncation re-split to single pages.
    Returns (merged_fields_or_None, failed_pages)."""
    try:
        return _extract_one(provider, data, content_type, doc_type), 0
    except (OCRError, ValueError):
        pages = _resplit_unit(data, content_type)
        if len(pages) <= 1:
            return None, 1
        merged, failed = None, 0
        for pdata, pct in pages:
            try:
                merged = _merge_fields(doc_type, merged, _extract_one(provider, pdata, pct, doc_type))
            except (OCRError, ValueError):
                failed += 1
        return merged, failed


def _extract_chunked(provider, file_bytes, content_type, doc_type, on_progress=None):
    """Extract a (possibly long) document: build work units, process them in
    parallel (bounded concurrency), and merge results in page order.

    Scales to 60+ page invoices. Digital PDFs go through the compact text path.
    A truncated unit is adaptively re-split to single pages; a page that still
    can't be read is skipped rather than failing the whole document.
    Returns (fields, failed_pages, total_pages).
    """
    import concurrent.futures as cf

    units, total_pages, _ = _build_units(file_bytes, content_type)

    # Fast path: a single unit — run inline and surface errors.
    if len(units) == 1:
        data, ct, _n = units[0]
        fields, failed = _process_unit(provider, data, ct, doc_type)
        if fields is None:
            raise OCRError("Could not read the document.")
        return fields, failed, total_pages

    results: dict[int, tuple] = {}
    workers = max(1, min(settings.ocr_chunk_concurrency, len(units)))
    done_pages = 0
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_process_unit, provider, data, ct, doc_type): (i, n)
            for i, (data, ct, n) in enumerate(units)
        }
        for fut in cf.as_completed(futures):
            i, n = futures[fut]
            results[i] = fut.result()
            done_pages = min(done_pages + n, total_pages)
            if on_progress:
                try:
                    on_progress(done_pages, total_pages)
                except Exception:  # noqa: BLE001 - progress must never break extraction
                    pass

    merged = None
    failed_pages = 0
    for i in range(len(units)):
        fields, failed = results.get(i, (None, 0))
        failed_pages += failed
        if fields is not None:
            merged = _merge_fields(doc_type, merged, fields)

    if merged is None:
        raise OCRError("Could not read any page of the document.")
    return merged, failed_pages, total_pages


def _finalize(resolved_type, fields, pipeline, pages, failed_pages=0):
    fields = postprocess_fields(resolved_type, fields)
    list_key = _LIST_KEY.get(resolved_type)
    item_count = len(fields.get(list_key, []) or []) if list_key else 0
    warnings = collect_low_confidence(fields, settings.low_confidence_threshold)
    if failed_pages:
        warnings.insert(0, f"{failed_pages} of {pages} page(s) could not be read; review may be incomplete.")
    meta = ExtractionMeta(
        overall_confidence=_overall_confidence(fields),
        language="en", pipeline=pipeline, processed_at=time.time(), warnings=warnings,
    ).model_dump()
    meta["pages"] = pages
    meta["item_count"] = item_count
    meta["pages_failed"] = failed_pages
    return {"schema_version": SCHEMA_VERSION, "doc_type": resolved_type, "fields": fields, "meta": meta}


def process_document(document_id: str, file_bytes: bytes, content_type: str, doc_type=None, on_progress=None) -> dict:
    # --- Tier 1: deterministic parse of digital PDF invoices (free, exact, unlimited
    # pages). Real (not mock) — runs whenever the input is a digital PDF and the
    # document isn't explicitly a prescription. ---
    if content_type == "application/pdf" and doc_type in (None, "invoice"):
        try:
            if is_digital_pdf(file_bytes):
                from app.services.ocr.invoice_parser import parse_invoice_pdf

                parsed = parse_invoice_pdf(file_bytes)
                if parsed and len(parsed.get("line_items", [])) >= 3:
                    fields = validate_fields("invoice", parsed)
                    return _finalize("invoice", fields, "pdf_parser", page_count(file_bytes))
        except Exception:  # noqa: BLE001 - any failure -> fall back to the AI pipeline
            pass

    # --- Tier 2: AI vision/text pipeline (images, scanned PDFs, non-invoice PDFs) ---
    provider = get_provider()

    # Classify on the first page only (cheaper for long PDFs); use text when digital.
    if not doc_type:
        classify_bytes, classify_ct = file_bytes, content_type
        if content_type == "application/pdf" and page_count(file_bytes) > 1:
            if is_digital_pdf(file_bytes):
                classify_bytes = (extract_text_pages(file_bytes)[0] or "").encode("utf-8")
                classify_ct = "text/plain"
            else:
                classify_bytes = split_pdf(file_bytes, 1)[0]
        doc_type = provider.classify(classify_bytes, classify_ct)
    resolved_type = doc_type if doc_type in ("prescription", "invoice") else "prescription"

    fields, failed_pages, total_pages = _extract_chunked(
        provider, file_bytes, content_type, resolved_type, on_progress=on_progress
    )
    return _finalize(resolved_type, fields, provider.name, total_pages, failed_pages)
