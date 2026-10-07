"""The last resort when the AI cannot read a document: never a dead end.

Whatever stops Gemini - it refuses the request, its answer is unusable, it is
down, its quota is gone, the key is missing - a scan used to end as "Failed",
and the pharmacist could only try again later. Now the document still reaches
review, flagged for manual entry:

1. We get its text ourselves: the PDF's own text layer when it has one,
   otherwise Tesseract OCR on each page or photo. Free, local, no rate limit.
2. Header fields are filled by the same patterns the deterministic parser uses
   (invoice number and date, total, GSTINs, PAN, e-mail, drug licences,
   references, tax totals). Each gets a low confidence so the review screen
   flags it for checking - a pattern match is a suggestion, not a reading.
3. Line items are left for the pharmacist to enter; the text we read is kept
   with the document so they can copy from it instead of retyping from paper.

Nothing is invented: a field the patterns cannot find stays blank.
"""
import io
import logging
import re
import shutil
import subprocess
from typing import List, Optional, Tuple

from app.config import settings
from app.schemas.extraction import validate_fields
from app.services.ocr.classify import classify_text
from app.services.ocr.invoice_header import (
    _DATE,
    _EMAIL,
    _GSTIN_SHAPE,
    _PAN_SHAPE,
    drug_licences,
    extract_references,
    extract_totals,
    gstin_is_valid,
)
from app.services.ocr.pdf_utils import extract_text_pages, is_digital_pdf, page_count

log = logging.getLogger(__name__)

PIPELINE = "manual_entry"
# Below the review threshold (0.6), so every value we guessed is highlighted.
_GUESS = 0.5
_MAX_RAW_TEXT = 20_000

MANUAL_ENTRY_WARNING = (
    "This document could not be read automatically. Details found in its text have been "
    "filled in and marked for checking - please complete the rest, including the line items, by hand."
)

_MONTH_DATE = r"(\d{1,2}[\s\-/.](?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[\s\-/.,]*\d{2,4})"
# Scanner OCR confuses I, l and 1 - "lnvoice" on the MSV bill.
_INVOICE_NO = re.compile(
    r"[il1|]nv[o0][il1]ce\s*(?:no|number|#)\b\.?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]{1,24})", re.I)
_INVOICE_DATE = re.compile(
    r"(?:[il1]nv[o0][il1]ce\s*date|bill\s*date|dated|date)\s*[:\-]?\s*(?:" + _MONTH_DATE + "|" + _DATE + ")", re.I)


# ------------------------------------------------------------------ text --
def _tesseract_available() -> bool:
    return shutil.which(settings.tesseract_cmd) is not None


def _ocr_image(image) -> str:
    """Tesseract on one PIL image. Empty if Tesseract is missing or fails -
    the fallback must never be the thing that crashes."""
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    try:
        result = subprocess.run(
            [settings.tesseract_cmd, "stdin", "stdout", "-l", settings.tesseract_lang, "--psm", "3"],
            input=buf.getvalue(), capture_output=True, timeout=settings.tesseract_timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("tesseract failed: %s", exc)
        return ""
    if result.returncode != 0:
        log.warning("tesseract exited %s: %s", result.returncode, result.stderr[:300])
    return result.stdout.decode("utf-8", "replace")


def _prepare(image):
    """Upright and greyscale - phone photos carry their rotation in EXIF."""
    from PIL import ImageOps

    return ImageOps.exif_transpose(image).convert("L")


def _pdf_page_images(data: bytes, max_pages: int):
    from app.services.ocr.pdfium_safe import render_pages

    # ~300 dpi: what Tesseract reads printed invoices best at.
    yield from render_pages(data, 300 / 72, max_pages=max_pages)


def document_text(data: bytes, content_type: str) -> Tuple[str, str, int]:
    """(text, where it came from, pages read). Never raises."""
    max_pages = settings.ocr_fallback_max_pages
    try:
        if content_type == "application/pdf":
            pages = page_count(data)
            if is_digital_pdf(data):
                return "\n\n".join(extract_text_pages(data)[:max_pages]), "pdf_text", pages
            if not _tesseract_available():
                return "", "none", pages
            texts = [_ocr_image(_prepare(img)) for img in _pdf_page_images(data, max_pages)]
            return "\n\n".join(texts), "ocr", pages
        if not _tesseract_available():
            return "", "none", 1
        from PIL import Image

        return _ocr_image(_prepare(Image.open(io.BytesIO(data)))), "ocr", 1
    except Exception as exc:  # noqa: BLE001 - a broken file still reaches manual entry
        log.warning("fallback could not read text: %s", exc)
        return "", "none", 1


# ---------------------------------------------------------------- fields --
def _leaf(value: Optional[str]) -> dict:
    return {"value": value, "confidence": _GUESS if value else None}


def _first(pattern, text: str) -> Optional[str]:
    m = pattern.search(text or "")
    if not m:
        return None
    return next((g for g in m.groups() if g), None)


def _party_gstins(text: str) -> Tuple[Optional[str], Optional[str]]:
    """(supplier GSTIN, buyer GSTIN), each blank unless it is readable.

    Which party a GSTIN belongs to is decided by WHERE it sits - before or after
    the buyer's heading ("Bill to", "Buyer", "Ship to", "Consignee") - never by
    which ones happened to be legible. Otherwise a supplier GSTIN the scanner
    garbled would let the buyer's slide into the supplier's place, filing tax
    against the wrong party. Without a heading, the bill's order decides: the
    supplier's block comes first.

    A GSTIN whose check character is wrong (OCR misreads a character often
    enough) is left blank rather than shown.
    """
    from app.services.ocr.invoice_parser import _BUYER_HEADING

    heading = _BUYER_HEADING.search(text or "")
    matches = list(_GSTIN_SHAPE.finditer(text or ""))
    if heading:
        before = [m.group(1) for m in matches if m.start() < heading.start()]
        after = [m.group(1) for m in matches if m.start() >= heading.start()]
        supplier = before[0] if before else None
        buyer = after[0] if after else None
    else:
        ordered = list(dict.fromkeys(m.group(1) for m in matches))
        supplier = ordered[0] if ordered else None
        buyer = ordered[1] if len(ordered) > 1 else None
    return (supplier if gstin_is_valid(supplier) else None,
            buyer if gstin_is_valid(buyer) else None)


def _standalone_pan(text: str) -> Optional[str]:
    """The first PAN printed as itself, not as the middle of a GSTIN."""
    for m in _PAN_SHAPE.finditer(text or ""):
        around = (text or "")[max(0, m.start() - 2):m.end() + 3]
        if not _GSTIN_SHAPE.search(around):
            return m.group(1)
    return None


def _invoice_fields(text: str) -> dict:
    from app.services.ocr.invoice_parser import _extract_total

    supplier_gstin, buyer_gstin = _party_gstins(text)
    # Only a PAN the bill prints on its own - never characters 3-12 of a GSTIN:
    # a field the bill does not print stays blank.
    supplier_pan = _standalone_pan(text)

    licences = drug_licences(text)[:3]
    supplier = {
        "gstin": _leaf(supplier_gstin),
        "pan": _leaf(supplier_pan),
        "email": _leaf(_first(_EMAIL, text)),
    }
    for i, (number, date) in enumerate(licences, start=1):
        supplier[f"dl_no_{i}"] = _leaf(number)
        supplier[f"dl_date_{i}"] = _leaf(date)

    invoice = {
        "invoice_no": _leaf(_first(_INVOICE_NO, text)),
        "invoice_date": _leaf(_first(_INVOICE_DATE, text)),
        "total_amount": _leaf(_extract_total(text)),
    }
    for key, value in {**extract_references(text), **extract_totals(text)}.items():
        invoice[key] = _leaf(value)

    return {
        "supplier": supplier,
        "bill_to": {"gstin": _leaf(buyer_gstin), "pan": _leaf(None)},
        "invoice": invoice,
        "line_items": [],
    }


def fallback_payload(document_id: str, data: bytes, content_type: str,
                     doc_type: Optional[str], reason: str) -> dict:
    """A reviewable document for something the AI could not read. Never raises
    for an unreadable file - the worst case is an empty form to fill in."""
    from app.services.ocr import _finalize

    text, source, pages = document_text(data, content_type)
    resolved = doc_type if doc_type in ("invoice", "prescription") else (classify_text(text) or "invoice")
    raw_fields = _invoice_fields(text) if resolved == "invoice" else {}
    fields = validate_fields(resolved, raw_fields)

    result = _finalize(resolved, fields, PIPELINE, pages)
    meta = result["meta"]
    meta["warnings"] = [MANUAL_ENTRY_WARNING] + [w for w in meta.get("warnings", []) if w != MANUAL_ENTRY_WARNING]
    meta["needs_manual_entry"] = True
    meta["text_source"] = source
    meta["raw_text"] = (text or "")[:_MAX_RAW_TEXT] or None
    log.warning(
        "document %s: AI could not read it (%s); %s text, %d chars - sent for manual entry",
        document_id, reason[:200], source, len(text or ""),
    )
    return result
