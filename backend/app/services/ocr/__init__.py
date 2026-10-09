"""OCR orchestration: provider selection, extraction, validation, post-processing.

`process_document` returns a full versioned ExtractionPayload dict, or raises
`OCRError` on any failure. It NEVER returns fabricated data.
"""
import logging
import re
import time
from typing import List

from app.config import settings
from app.schemas.extraction import (
    SCHEMA_VERSION,
    ExtractionMeta,
    _walk_fields,
    collect_low_confidence,
    validate_fields,
)
from app.services.ocr.base import OCRError, OCRProvider
from app.services.ocr.classify import classify_text
from app.services.ocr.invoice_checks import (
    complete_from_the_bill,
    drop_copied_pans,
    show_combined_utgst,
    dedupe_line_items,
    flag_invalid_gstins,
    mark_free_supplies,
    reconcile_invoice,
    resolve_billed_rate,
    validate_line_arithmetic,
)
from app.services.ocr.postprocess import postprocess_fields
from app.services.ocr.choices import apply_default, reference_choices, settle_checks, total_choice
from app.services.ocr.verify import flag_failed_fields, verify_invoice
from app.services.ocr.pdf_utils import (
    extract_text_pages,
    extract_text_sample,
    image_only_pdf,
    is_digital_pdf,
    is_scanned_pdf,
    page_count,
    split_pdf,
)

__all__ = ["process_document", "get_provider", "OCRError"]

log = logging.getLogger(__name__)

TYPE_UNSURE_WARNING = (
    "We could not tell whether this is an invoice or a prescription, so it was read as an "
    "invoice. If it is a prescription, scan it again with the type set to Prescription."
)

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


# Header figures printed at the FOOT of the bill: on a multi-page invoice the
# last page carries them, while an earlier page may print a carried-forward
# subtotal under the same label. The later chunk's value wins for these.
_FOOT_FIELDS = frozenset({
    "total_amount", "total_taxable_amount", "total_discount_amount", "total_gst_amount",
    "total_cgst_amount", "total_sgst_amount", "total_igst_amount", "total_utgst_amount",
})
# Chunks that read the same header field differently, kept on the merged
# fields until _read_with_ai turns them into warnings.
_CONFLICTS = "_chunk_conflicts"


def _merge_fields(doc_type: str, base: dict, incoming: dict) -> dict:
    """Merge a later chunk's fields into the accumulated fields.

    Header/single fields are taken from the first chunk that populated them -
    except the bill's totals, which come from the last (see _FOOT_FIELDS); the
    repeating list (line_items / medications) is concatenated. Two chunks that
    read the same header field differently are recorded, and the field marked
    for checking, rather than one silently winning.
    """
    if base is None:
        return incoming
    list_key = _LIST_KEY.get(doc_type)
    for key, val in incoming.items():
        if key == _CONFLICTS:
            base.setdefault(_CONFLICTS, []).extend(val or [])
        elif key == list_key:
            base[key] = (base.get(key) or []) + (val or [])
        elif key not in base or not base.get(key):
            base[key] = val
        elif isinstance(base.get(key), dict) and isinstance(val, dict):
            for k, v in val.items():
                cur = base[key].get(k)
                cur_value = cur.get("value") if isinstance(cur, dict) else cur
                new_value = v.get("value") if isinstance(v, dict) else v
                if not cur or (isinstance(cur, dict) and not cur_value):
                    base[key][k] = v            # fill a still-empty header field
                    continue
                if not new_value or not isinstance(cur, dict) or not isinstance(v, dict):
                    continue
                if _same_reading(cur_value, new_value):
                    continue
                if key == "invoice" and k in _FOOT_FIELDS:
                    # The last page's figure; reconciliation checks it.
                    base[key][k] = v
                    continue
                kept, other = cur, v
                kept["confidence"] = min(kept.get("confidence") or 0.3, 0.3)
                base.setdefault(_CONFLICTS, []).append(
                    {"path": f"{key}.{k}", "kept": str(kept.get("value")), "other": str(other.get("value"))})
    return base


def _same_reading(a, b) -> bool:
    """Two readings of one header field that differ only in formatting."""
    squash = lambda x: re.sub(r"[\s,₹\-/]", "", str(x)).upper()  # noqa: E731
    sa, sb = squash(a), squash(b)
    if sa == sb:
        return True
    try:
        return abs(float(sa) - float(sb)) < 0.005
    except ValueError:
        return False


def chunk_conflict_warnings(fields: dict) -> List[str]:
    """Take the chunks' disagreements off the fields, as warnings for review."""
    return [f"Pages of this bill give two readings of {c['path'].replace('_', ' ')}: "
            f"{c['kept']} (kept) and {c['other']} - please check it."
            for c in fields.pop(_CONFLICTS, None) or []]


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

    if is_scanned_pdf(file_bytes):
        # Read the picture, not the scanner's guess at it (see is_scanned_pdf).
        file_bytes = image_only_pdf(file_bytes)
    cs = settings.ocr_pdf_chunk_pages
    if total <= cs:
        return [(file_bytes, "application/pdf", total)], total, False
    return [(c, "application/pdf", cs) for c in split_pdf(file_bytes, cs)], total, True


def _resplit_unit(data: bytes, content_type: str):
    """Break a failed unit into single-page units for a finer retry."""
    if content_type == "text/plain":
        return [(p.encode("utf-8"), "text/plain") for p in data.decode("utf-8", "replace").split(_PAGE_SEP)]
    return [(c, "application/pdf") for c in split_pdf(data, 1)]


def _process_unit(provider, data, content_type, doc_type, n_pages=1):
    """Process one unit; on unusable output re-split to single pages.
    Returns (merged_fields_or_None, failed_pages, last_error_or_None).

    The error comes back rather than being dropped: when nothing could be read,
    it is the only explanation the pharmacist - and we - will get. Never raises:
    one unit's failure must not throw away the units that were read.
    """
    try:
        return _read_unit(provider, data, content_type, doc_type, n_pages)
    except Exception as exc:  # noqa: BLE001 - anything unforeseen costs this unit only
        log.exception("extraction crashed on a %d-byte %s unit", len(data), content_type)
        return None, n_pages, exc


def _read_unit(provider, data, content_type, doc_type, n_pages):
    try:
        return _extract_one(provider, data, content_type, doc_type), 0, None
    except (OCRError, ValueError) as exc:
        log.warning("extraction failed for a %d-byte %s unit: %s", len(data), content_type, exc)
        # Smaller pieces fix a truncated or malformed answer, nothing else. An
        # overloaded AI or a refused request would only fail once per page.
        if getattr(exc, "kind", None) in ("busy", "rejected"):
            return None, n_pages, exc
        pages = _resplit_unit(data, content_type)
        if len(pages) <= 1:
            return None, n_pages, exc
        merged, failed, last = None, 0, exc
        for pdata, pct in pages:
            try:
                merged = _merge_fields(doc_type, merged, _extract_one(provider, pdata, pct, doc_type))
            except Exception as page_exc:  # noqa: BLE001 - one page never sinks the rest
                log.warning("extraction failed for a single page: %s", page_exc)
                failed += 1
                last = page_exc
        return merged, failed, (last if merged is None else None)


def _unreadable(prefix: str, exc) -> OCRError:
    """The failure to report when nothing could be read, keeping the cause.

    An overloaded AI keeps its own message, so the app can tell the pharmacist
    to retry; anything else says what the AI actually objected to."""
    if isinstance(exc, OCRError) and exc.kind == "busy":
        return OCRError(str(exc), kind="busy")
    if exc is None:
        return OCRError(f"{prefix}.")
    return OCRError(f"{prefix}: {exc}", kind=getattr(exc, "kind", None))


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
        fields, failed, error = _process_unit(provider, data, ct, doc_type, _n)
        if fields is None:
            raise _unreadable("Could not read the document", error)
        return fields, failed, total_pages

    results: dict[int, tuple] = {}
    workers = max(1, min(settings.ocr_chunk_concurrency, len(units)))
    done_pages = 0
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_process_unit, provider, data, ct, doc_type, n): (i, n)
            for i, (data, ct, n) in enumerate(units)
        }
        for fut in cf.as_completed(futures):
            i, n = futures[fut]
            try:
                results[i] = fut.result()
            except Exception as exc:  # noqa: BLE001 - _process_unit never raises; belt and braces
                results[i] = (None, n, exc)
            done_pages = min(done_pages + n, total_pages)
            if on_progress:
                try:
                    on_progress(done_pages, total_pages)
                except Exception:  # noqa: BLE001 - progress must never break extraction
                    pass

    merged = None
    failed_pages = 0
    error = None
    for i in range(len(units)):
        fields, failed, unit_error = results.get(i, (None, 0, None))
        failed_pages += failed
        error = error or unit_error
        if fields is not None:
            merged = _merge_fields(doc_type, merged, fields)

    if merged is None:
        raise _unreadable("Could not read any page of the document", error)
    return merged, failed_pages, total_pages


# Checks the older warning list never carried. Their failures are also added
# there, so an app build that predates the verdict banner still shows them.
_NEW_CHECKS = frozenset({
    "cross_foot", "head_totals", "line_tax", "line_net", "line_discount",
    "price_ladder", "gst_rates", "dates", "hsn", "supplier_pan", "printed_not_read", "irn",
    "rates_give_tax",
}) | frozenset({"cross_read", "ai_review", "document_kind"})


def _finalize(resolved_type, fields, pipeline, pages, failed_pages=0, hints=None, extra_warnings=None):
    """Post-process, run integrity checks, and wrap the result.

    Invoice integrity (de-duplication, arithmetic, totals reconciliation) runs
    here rather than inside a single parser, so that BOTH the deterministic PDF
    parser and the AI pipeline are held to the same standard.
    """
    hints = hints or {}
    integrity = {"duplicates_removed": 0, "copies_detected": hints.get("copies_detected")}
    check_warnings = list(extra_warnings or [])

    if resolved_type == "invoice":
        # Supplier and buyer settled by the GSTINs printed - the buyer's under
        # whatever label the supplier uses, or none (parties.py).
        from app.services.ocr import parties

        check_warnings.extend(parties.resolve(
            fields, hints.get("document_text") or hints.get("party_text") or "",
            hints.get("stream_text") or ""))
        # Facts the bill fixes without printing them - a zero head the sale
        # cannot carry - for either reader. A PAN is never one of them: what
        # the bill does not print stays blank.
        complete_from_the_bill(fields)
        drop_copied_pans(fields, hints.get("document_text") or hints.get("party_text") or "")
        items = fields.get("line_items") or []
        before = len(items)
        # A bill may list the same item twice on purpose (Corona does, twice):
        # when the lines as printed already add up to the bill's own totals,
        # nothing is a reprint and nothing is removed.
        if reconcile_invoice(fields).get("total_reconciles") is True:
            removed = 0
        else:
            items, removed = dedupe_line_items(items)
        fields["line_items"] = items
        integrity["duplicates_removed"] = removed
        if removed:
            log.info(
                "invoice integrity: collapsed %d duplicate line(s) of %d (pipeline=%s)",
                removed, before, pipeline,
            )
            copies = integrity.get("copies_detected")
            if copies and copies > 1:
                check_warnings.append(
                    f"This PDF contains {copies} printed copies of the same invoice. "
                    f"Showing {len(items)} items once ({removed} repeated rows removed)."
                )
            else:
                check_warnings.append(
                    f"{removed} repeated line(s) were removed. Please confirm the item count."
                )

    if resolved_type == "invoice":
        # A line the supplier billed at zero reads as zero, not as a gap.
        free = mark_free_supplies(fields.get("line_items") or [])
        if free:
            integrity["free_supply_lines"] = free
        # Decide which printed price column the bill was actually charged on,
        # before the arithmetic check runs against it.
        billed = resolve_billed_rate(fields.get("line_items") or [], hints.get("price_labels"))
        if billed:
            integrity["billed_rate_column"] = billed

    fields = postprocess_fields(resolved_type, fields)

    if resolved_type == "invoice":
        flagged = validate_line_arithmetic(fields.get("line_items") or [])
        if flagged:
            check_warnings.append(
                f"{flagged} line(s) where quantity x rate does not match the amount - marked for checking."
            )
        check_warnings.extend(flag_invalid_gstins(fields))
        report = reconcile_invoice(
            fields, hints.get("stated_item_count"), hints.get("total_in_words"),
            page_text=hints.get("document_text") or hints.get("party_text"),
        )
        check_warnings.extend(report.pop("warnings", []))
        integrity.update(report)

        # The verification layer: every identity the bill states, checked. Its
        # failures flag their fields (so review highlights them) and must each
        # be acknowledged before approval - see verify.py.
        # The page's own text travels with the result, so the checks that read
        # it - fields printed but not read - also run after a reviewer's edit,
        # and corrections can be learned from it.
        if hints.get("document_text"):
            integrity["page_text"] = hints["document_text"]
            # A new supplier's layout can leave fields our reader missed. The
            # AI is asked for just those, from the text, and only answers
            # printed on the page word for word are kept (gap_fill.py). A
            # reading already complete never calls it.
            if pipeline in ("pdf_parser", "tesseract"):
                from app.services.ocr.gap_fill import fill as fill_gaps
                from app.services.ocr.missed_fields import find_missed

                filled = fill_gaps(fields, hints["document_text"],
                                   find_missed(fields, hints["document_text"]))
                if filled:
                    integrity["gap_filled"] = filled
                    complete_from_the_bill(fields)
        # A printed "SGST/UTGST" figure is shown under UTGST too - after the
        # total GST is fixed, and flagged so every sum counts it once.
        if show_combined_utgst(fields, hints.get("document_text") or hints.get("party_text") or ""):
            integrity["sgst_utgst_combined"] = True
        # A credit note or challan is not a purchase invoice (document_kind.py).
        from app.services.ocr import document_kind

        title = ((fields.get("invoice") or {}).get("document_title") or {})
        integrity["document_kind"] = document_kind.detect(
            title.get("value") if isinstance(title, dict) else None,
            hints.get("document_text") or hints.get("party_text") or "")
        verification = verify_invoice(fields, integrity, extra=hints.get("cross_read"))
        flag_failed_fields(fields, verification)
        integrity["verification"] = verification

        # Where the bill itself gives two answers, the reviewer decides
        # (choices.py) - offered with every option and a default meanwhile.
        offered = (reference_choices(hints.get("document_text") or "", fields)
                   + total_choice(integrity, fields))
        if offered:
            apply_default(fields, offered)
            integrity["choices"] = offered
        for check in verification["checks"]:
            if check["status"] == "fail" and check["id"] in _NEW_CHECKS:
                check_warnings.append(f"{check['label']}: {check['message']}")

    list_key = _LIST_KEY.get(resolved_type)
    item_count = len(fields.get(list_key, []) or []) if list_key else 0
    warnings = check_warnings + collect_low_confidence(fields, settings.low_confidence_threshold)
    if failed_pages:
        warnings.insert(0, f"{failed_pages} of {pages} page(s) could not be read; review may be incomplete.")
    meta = ExtractionMeta(
        overall_confidence=_overall_confidence(fields),
        language="en", pipeline=pipeline, processed_at=time.time(), warnings=warnings,
        **{k: v for k, v in integrity.items() if v is not None},
    ).model_dump()
    meta["pages"] = pages
    meta["item_count"] = item_count
    settle_checks(meta)
    meta["pages_failed"] = failed_pages
    return {"schema_version": SCHEMA_VERSION, "doc_type": resolved_type, "fields": fields, "meta": meta}


# At or above this many lines, a parse is kept on its row count alone, as it
# always was. Below it, the invoice's own total has to agree - see below.
_SHORT_INVOICE_LINES = 3


def _parse_is_trustworthy_enough(parsed: dict, document_id: str) -> bool:
    """Whether to keep the deterministic parse rather than call the AI.

    Row count was the old proxy for "did the table parse", and it was wrong in
    both directions. It rejected invoices that genuinely bill ONE product -
    Abbott bills a single kit, Overseas a single pack - sending bills whose
    table we read perfectly to the paid model, which then read the figures by
    eye and got the batch, PTR and PTS wrong. And it accepted any three rows of
    nonsense.

    So a short parse must reconcile against the total printed on the bill. That
    is a far stronger test than counting rows: a one-line invoice whose line
    equals its printed total is certainly read correctly.
    """
    items = parsed.get("line_items") or []
    if not items:
        return False

    def value(item, key):
        return str((item.get(key) or {}).get("value") or "").strip()

    # A reading that adds up is still useless without what stock is made of.
    # Lupin's lines summed exactly, every one with no quantity; Meher's two
    # products both read "SRN". The AI reads those - by eye, but whole.
    billed = [i for i in items if value(i, "amount") not in ("", "0", "0.00")]
    if billed and sum(1 for i in billed if value(i, "quantity")) * 2 < len(billed):
        log.info("document %s: most lines read without a quantity - handing to the AI", document_id)
        return False
    names = {value(i, "description").upper() for i in items}
    if len(items) >= 2 and len(names) == 1 and len(next(iter(names))) <= 5 \
            and len({value(i, "batch_no") for i in items}) > 1:
        log.info("document %s: every product read under one name - handing to the AI", document_id)
        return False
    if len(items) >= _SHORT_INVOICE_LINES:
        return True
    report = reconcile_invoice(parsed)
    if report.get("total_reconciles") is True:
        return True
    log.info(
        "document %s: deterministic parse found only %d line(s) and they do not "
        "reconcile - handing to the AI", document_id, len(items),
    )
    return False


def process_document(document_id: str, file_bytes: bytes, content_type: str, doc_type=None, on_progress=None,
                     own_gstins=None) -> dict:
    """Read one document. `own_gstins` are the shop's own GSTINs, when known:
    the shop is the buyer on its own purchase bills (parties.py)."""
    from app.services.ocr.parties import OWN_GSTINS

    # A mapping {GSTIN: the shop's name} also lets a cut-off buyer name be
    # finished; a plain list of GSTINs only settles who is who.
    own = dict(own_gstins) if isinstance(own_gstins, dict) else tuple(own_gstins or ())
    token = OWN_GSTINS.set(own)
    try:
        return _process_document(document_id, file_bytes, content_type, doc_type, on_progress)
    finally:
        OWN_GSTINS.reset(token)


def _process_document(document_id: str, file_bytes: bytes, content_type: str, doc_type=None,
                      on_progress=None) -> dict:
    unreconciled = None
    # --- Tier 1: deterministic parse of digital PDF invoices (free, exact, unlimited
    # pages). Real (not mock) — runs whenever the input is a digital PDF and the
    # document isn't explicitly a prescription. ---
    # Photographs come here too: a phone picture of a bill is exactly the case
    # the Tesseract tier below is for, and it was previously AI-only.
    if doc_type in (None, "invoice") and (
        content_type == "application/pdf" or content_type.startswith("image/")
    ):
        try:
            if content_type == "application/pdf" and is_digital_pdf(file_bytes):
                from app.services.ocr.invoice_parser import parse_invoice_pdf

                parsed = parse_invoice_pdf(file_bytes)
                if parsed and _parse_is_trustworthy_enough(parsed, document_id):
                    hints = parsed.pop("_hints", {})
                    fields = validate_fields("invoice", parsed)
                    result = _finalize(
                        "invoice", fields, "pdf_parser", page_count(file_bytes), hints=hints,
                        extra_warnings=_supplier_licences(fields, file_bytes),
                    )
                    # Safety net for the page-skipping: if we judged this file to
                    # hold repeated copies and the lines then do NOT add up to the
                    # printed total, the judgement may have been wrong and real
                    # pages skipped. Read every page and keep whichever result
                    # reconciles. Costs time only on an invoice already suspect.
                    meta = result["meta"]
                    if (meta.get("copies_detected") or 1) > 1 and meta.get("total_reconciles") is False:
                        log.warning(
                            "document %s: %d copies assumed but the total does not "
                            "reconcile - re-reading every page",
                            document_id, meta["copies_detected"],
                        )
                        retry = parse_invoice_pdf(file_bytes, read_every_page=True)
                        if retry and len(retry.get("line_items", [])) >= 3:
                            retry_hints = retry.pop("_hints", {})
                            retry_result = _finalize(
                                "invoice", validate_fields("invoice", retry), "pdf_parser",
                                page_count(file_bytes), hints=retry_hints,
                            )
                            if retry_result["meta"].get("total_reconciles") is True:
                                return retry_result
                    if meta.get("total_reconciles") is True or not settings.ocr_second_read_unreconciled:
                        return result
                    # The table was read, but its lines do not reach the bill's
                    # total. Kept as it is - flagged - unless the AI's reading
                    # of the same text adds up, which is then the better one.
                    unreconciled = result
            # --- Tier 1b: a SCANNED invoice read by Tesseract, free and local.
            # Tried before the paid model, and kept only when the lines add up
            # to the total printed on the bill. That check is the whole safety
            # argument: a misread digit shows up as a total that does not
            # reconcile, so a reading we keep is one the invoice itself agrees
            # with. Anything less goes to the AI exactly as before.
            if settings.ocr_tesseract_tables and (
                content_type.startswith("image/") or is_scanned_pdf(file_bytes)
            ):
                from app.services.ocr.invoice_parser import parse_scanned_invoice

                scanned = parse_scanned_invoice(file_bytes, content_type)
                if scanned and len(scanned.get("line_items", [])) >= 3:
                    hints = scanned.pop("_hints", {})
                    candidate = _finalize(
                        "invoice", validate_fields("invoice", scanned), "tesseract",
                        page_count(file_bytes) if content_type == "application/pdf" else 1,
                        hints=hints,
                    )
                    if candidate["meta"].get("total_reconciles") is True:
                        log.info(
                            "document %s: scanned invoice read by tesseract, %d items, reconciled",
                            document_id, candidate["meta"]["item_count"],
                        )
                        return candidate
                    log.info(
                        "document %s: tesseract read %s items but the total does not "
                        "reconcile - using the AI instead",
                        document_id, candidate["meta"]["item_count"],
                    )
        except Exception as exc:  # noqa: BLE001 - any failure -> fall back to the AI pipeline
            # Never silent: a Tier-1 regression is invisible otherwise, because
            # the AI fallback still returns a plausible-looking result.
            log.warning(
                "tier1 pdf_parser failed for document %s (%s); falling back to AI",
                document_id, exc, exc_info=True,
            )

    if unreconciled is not None:
        return _second_read(document_id, unreconciled, file_bytes, content_type, doc_type, on_progress)
    return _read_with_ai(document_id, file_bytes, content_type, doc_type, on_progress)


def _second_read(document_id, unreconciled, file_bytes, content_type, doc_type, on_progress):
    """The AI's reading of a digital invoice whose table reading did not add up,
    kept only if it does; otherwise the table reading, flagged as it was."""
    try:
        ai = _read_with_ai(document_id, file_bytes, content_type, doc_type, on_progress)
    except Exception as exc:  # noqa: BLE001 - a second opinion that fails changes nothing
        log.info("document %s: AI second read failed (%s); keeping the table reading", document_id, exc)
        return unreconciled
    if ai["meta"].get("total_reconciles") is True:
        log.info("document %s: the table reading did not add up; the AI's does - using it", document_id)
        ai["meta"].setdefault("warnings", []).insert(
            0, "The lines read from the PDF's table did not add up to its total; this reading "
               "by the AI does - please check it.")
        return ai
    log.info("document %s: neither reading adds up; keeping the table reading", document_id)
    return unreconciled


def _supplier_licences(fields: dict, file_bytes: bytes) -> List[str]:
    """The supplier's own drug licences, from beside its GSTIN on a digital PDF
    (licences.py). Never fails the reading."""
    from app.services.ocr.licences import apply_supplier_licences

    return _safely("the supplier licence check", lambda: apply_supplier_licences(fields, file_bytes), [])


def _safely(what: str, step, default=None):
    """Run a check that follows the AI's reading. A check that crashes is a
    check that could not run - never a reason to lose the reading itself."""
    try:
        return step()
    except Exception:  # noqa: BLE001
        log.exception("%s could not run; the reading is kept", what)
        return default


CHECKS_FAILED_WARNING = ("The checks could not run on this reading. Please check every value "
                         "against the invoice before approving.")


def _finalize_safely(resolved_type, fields, pipeline, pages, failed_pages=0, hints=None, extra_warnings=None):
    """_finalize, or - if the checks themselves crash - the reading as read,
    with one failed check saying nothing was verified. The AI's reading is
    never thrown away because a check had a bug, and never shown as checked
    when it was not."""
    import copy

    original = copy.deepcopy(fields)
    try:
        return _finalize(resolved_type, fields, pipeline, pages, failed_pages,
                         hints=hints, extra_warnings=extra_warnings)
    except Exception:  # noqa: BLE001
        log.exception("the checks crashed on a %s reading; keeping it, marked unchecked", resolved_type)
    fields = original
    warnings = [CHECKS_FAILED_WARNING] + list(extra_warnings or [])
    if failed_pages:
        warnings.insert(0, f"{failed_pages} of {pages} page(s) could not be read; review may be incomplete.")
    meta = ExtractionMeta(
        overall_confidence=_overall_confidence(fields), language="en", pipeline=pipeline,
        processed_at=time.time(), warnings=warnings,
    ).model_dump()
    if resolved_type == "invoice":
        meta["verification"] = {
            "checks": [{"id": "checks_ran", "label": "Every check ran", "status": "fail",
                        "message": CHECKS_FAILED_WARNING, "fields": []}],
            "passed": 0, "failed": 1, "skipped": 0, "verdict": "needs_check", "acknowledged": [],
        }
    list_key = _LIST_KEY.get(resolved_type)
    meta["pages"] = pages
    meta["item_count"] = len(fields.get(list_key, []) or []) if list_key else 0
    meta["pages_failed"] = failed_pages
    return {"schema_version": SCHEMA_VERSION, "doc_type": resolved_type, "fields": fields, "meta": meta}


def _read_with_ai(document_id, file_bytes, content_type, doc_type, on_progress):
    # --- Tier 2: AI vision/text pipeline (images, scanned PDFs, non-invoice PDFs) ---
    provider = get_provider()

    # A PDF that carries text usually says what it is; reading that costs
    # nothing, where asking the AI spends a call of the scan's quota.
    if not doc_type and content_type == "application/pdf":
        doc_type = classify_text((extract_text_sample(file_bytes, 1)[:1] or [""])[0])
        if doc_type:
            log.info("document %s classified as %s from its text", document_id, doc_type)

    # Otherwise ask the AI, on the first page only (cheaper for long PDFs).
    if not doc_type:
        classify_bytes, classify_ct = file_bytes, content_type
        if content_type == "application/pdf" and page_count(file_bytes) > 1:
            if is_digital_pdf(file_bytes):
                classify_bytes = (extract_text_pages(file_bytes)[0] or "").encode("utf-8")
                classify_ct = "text/plain"
            else:
                classify_bytes = split_pdf(file_bytes, 1)[0]
        doc_type = provider.classify(classify_bytes, classify_ct)
    # An unclear answer is never silently a prescription. It is read as an
    # invoice - what this app is mostly sent, and the form with more fields to
    # correct - and the pharmacist is told to check the type.
    unsure = doc_type not in ("prescription", "invoice")
    resolved_type = "invoice" if unsure else doc_type
    if unsure:
        log.warning("document %s: type unclear (%r); reading it as an invoice", document_id, doc_type)

    # A scan is read by the AI by eye. Read it a second time, independently,
    # with Tesseract - in parallel, so it costs no wall-clock time beside the AI
    # call - and compare the two (services/ocr/cross_read.py).
    import concurrent.futures as _cf

    from app.services.ocr import cross_read

    is_scan = resolved_type == "invoice" and (
        content_type.startswith("image/")
        or (content_type == "application/pdf" and not is_digital_pdf(file_bytes))
    )
    with _cf.ThreadPoolExecutor(max_workers=1) as pool:
        second = pool.submit(cross_read.second_reading, file_bytes, content_type) if is_scan else None
        fields, failed_pages, total_pages = _extract_chunked(
            provider, file_bytes, content_type, resolved_type, on_progress=on_progress
        )
        second_fields = _safely("the second reading", lambda: second.result() if second else None)
    conflict_warnings = _safely("chunk merge notes", lambda: chunk_conflict_warnings(fields), [])
    fields.pop(_CONFLICTS, None)
    hints = {}
    if second_fields:
        hints["cross_read"] = _safely("the cross-read", lambda: cross_read.compare(fields, second_fields),
                                      [dict(cross_read.SKIPPED)])
    elif is_scan:
        # Said, not left out: a check that silently vanishes reads as a pass.
        hints["cross_read"] = [dict(cross_read.SKIPPED)]
    party_warnings = []
    if resolved_type == "invoice" and settings.ocr_party_check_enabled:
        # A GSTIN the AI left blank or misread is caught, not silently lost
        # (party_check.py). cross_read only compares fields BOTH readers saw,
        # so a GSTIN the AI dropped would otherwise pass unnoticed.
        from app.services.ocr.party_check import cross_check, first_page_text

        hints["party_text"] = _safely("the page text", lambda: first_page_text(file_bytes, content_type), "")
        party_warnings = _safely("the party check", lambda: cross_check(fields, hints["party_text"]), [])
    if resolved_type == "invoice" and content_type == "application/pdf" and not is_scan:
        party_warnings = party_warnings + _supplier_licences(fields, file_bytes)
    if is_scan and settings.ocr_ai_review:
        # A second AI checks the reading against the page, value by value, as
        # a reviewer would (ai_review.py). After party_check, so a GSTIN filled
        # in from the page is reviewed too.
        from app.services.ocr import ai_review

        hints.setdefault("cross_read", []).append(
            ai_review.review(provider, fields, file_bytes, content_type))
    result = _finalize_safely(resolved_type, fields, provider.name, total_pages, failed_pages,
                              hints=hints, extra_warnings=conflict_warnings + party_warnings)
    calls = getattr(provider, "calls", None)
    calls = list(calls) if isinstance(calls, list) else []
    if calls:
        result["meta"]["ai_calls"] = {
            "count": len(calls),
            "seconds": round(sum(c["seconds"] for c in calls), 1),
            "calls": calls[-20:],
        }
        log.info("document %s: %d AI call(s), %.1fs in all", document_id, len(calls),
                 result["meta"]["ai_calls"]["seconds"])
    if unsure:
        result["meta"].setdefault("warnings", []).insert(0, TYPE_UNSURE_WARNING)
        result["meta"]["type_unsure"] = True
    return result
