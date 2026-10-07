"""A second, independent reading of a scan - the check behind the AI's eyes.

A digital PDF is read exactly, off its own text. A scan or a photo is read by
the AI, by eye, and that is where wrong figures come from: a 6 read as an 8, a
digit dropped from a batch number. Reconciliation catches a misread that moves
the total; it cannot catch one that doesn't, or a misread header field.

So the same scan is read again by Tesseract - free, local, and wrong in
different ways from the AI - and the two readings are compared field by field:

    pass     both readers saw the field, and agree;
    fail     both saw it, and disagree - the field is flagged for the reviewer;
    skipped  Tesseract could not read it. It never votes on what it did not see.

Agreement between two independent readers is strong evidence the value is
right; a disagreement points the reviewer at exactly the field to check.
Each comparison becomes a check in the verification (see verify.py).
"""
import logging
import re
from typing import Dict, List, Optional, Tuple

from app.config import settings

log = logging.getLogger(__name__)

# Pages compared. Totals and the first pages' lines are what matter; reading a
# 30-page scan twice would add time for little extra assurance.
MAX_PAGES = 3

_HEADER_FIELDS = (
    ("invoice.invoice_no", "Invoice number", "text"),
    ("invoice.invoice_date", "Invoice date", "date"),
    ("invoice.total_amount", "Bill total", "money"),
    ("invoice.total_taxable_amount", "Taxable total", "money"),
    ("invoice.total_cgst_amount", "CGST total", "money"),
    ("invoice.total_sgst_amount", "SGST total", "money"),
    ("invoice.total_igst_amount", "IGST total", "money"),
    ("supplier.gstin", "Supplier GSTIN", "text"),
    ("bill_to.gstin", "Bill-to GSTIN", "text"),
)
_LINE_FIELDS = (
    ("quantity", "qty", "number"),
    ("amount", "amount", "money"),
    ("batch_no", "batch", "text"),
    ("expiry", "expiry", "date"),
    ("mrp", "MRP", "money"),
)


def _value(fields: dict, path: str) -> str:
    section, _, key = path.partition(".")
    leaf = (fields.get(section) or {}).get(key)
    return str((leaf or {}).get("value") or "").strip() if isinstance(leaf, dict) else ""


def _norm(kind: str, raw: str) -> Optional[str]:
    """A value in the form both readers can be compared in, or None if unreadable."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if kind in ("money", "number"):
        m = re.search(r"-?\d[\d,]*\.?\d*", raw.replace("₹", ""))
        if not m:
            return None
        try:
            return f"{float(m.group().replace(',', '')):.2f}"
        except ValueError:
            return None
    if kind == "date":
        from app.services.ocr.verify import parse_month
        day = re.match(r"\s*(\d{1,2})[\s./\-]", raw)
        month = parse_month(raw)
        if not month:
            return None
        return f"{month[0]:04d}-{month[1]:02d}" + (f"-{int(day.group(1)):02d}" if day else "")
    return re.sub(r"[\s.\-/]", "", raw).upper()


def _same(kind: str, a: str, b: str) -> bool:
    if kind == "date" and (len(a) == 7 or len(b) == 7):
        return a[:7] == b[:7]          # one reader printed no day: compare months
    return a == b


def compare(ai: dict, second: dict) -> List[dict]:
    """Every field both readings can speak to, as verification checks."""
    checks: List[dict] = []
    agree, disagree, implicated = 0, [], []

    def judge(path: str, label: str, kind: str, a_raw: str, b_raw: str) -> None:
        nonlocal agree
        a, b = _norm(kind, a_raw), _norm(kind, b_raw)
        if a is None or b is None:
            return
        if _same(kind, a, b):
            agree += 1
        else:
            disagree.append(f"{label}: AI read {a_raw!r}, second reading {b_raw!r}")
            implicated.append(path)

    for path, label, kind in _HEADER_FIELDS:
        judge(path, label, kind, _value(ai, path), _value(second, path))

    ai_items = ai.get("line_items") or []
    tx_items = second.get("line_items") or []
    if ai_items and tx_items:
        # A second reading of only the first pages of a long scan holds only
        # their lines: its count says nothing about the whole bill.
        if second.get("_partial"):
            pass
        elif len(ai_items) == len(tx_items):
            agree += 1
        elif len(tx_items) > 1:
            disagree.append(f"Line count: AI read {len(ai_items)}, second reading {len(tx_items)}")
        for i, ai_item in enumerate(ai_items):
            match = _matching_line(ai_item, tx_items, i)
            if match is None:
                continue
            for key, label, kind in _LINE_FIELDS:
                judge(f"line_items[{i}].{key}", f"line {i + 1} {label}", kind,
                      _leaf(ai_item, key), _leaf(match, key))

    if not agree and not disagree:
        checks.append({"id": "cross_read", "label": "A second reading of the scan agrees",
                       "status": "skipped", "message": "The second reading could not read this scan.",
                       "fields": []})
    elif disagree:
        checks.append({"id": "cross_read", "label": "A second reading of the scan agrees",
                       "status": "fail",
                       "message": f"{len(disagree)} value(s) read differently - "
                                  + "; ".join(disagree[:5]) + (" ..." if len(disagree) > 5 else ""),
                       "fields": sorted(set(implicated))})
    else:
        checks.append({"id": "cross_read", "label": "A second reading of the scan agrees",
                       "status": "pass",
                       "message": f"{agree} value(s) confirmed by an independent second reading.",
                       "fields": []})
    return checks


def _leaf(item: dict, key: str) -> str:
    leaf = item.get(key)
    return str((leaf or {}).get("value") or "").strip() if isinstance(leaf, dict) else ""


def _matching_line(ai_item: dict, others: List[dict], index: int) -> Optional[dict]:
    """The second reading's line for this one: same batch, else same position."""
    batch = _norm("text", _leaf(ai_item, "batch_no"))
    if batch:
        for other in others:
            if _norm("text", _leaf(other, "batch_no")) == batch:
                return other
    return others[index] if index < len(others) else None


def second_reading(data: bytes, content_type: str) -> Optional[dict]:
    """Tesseract's reading of the scan, or None where it cannot run.

    A scan whose TABLE Tesseract cannot rebuild still has its header read - the
    invoice number, date, total and GSTINs are what a misread hurts most, and
    MSV's scan, whose table it cannot rebuild, used to get no second reading
    at all. Only values that pass their own test are offered for comparison
    (see _trustworthy): a doubtful one is left out, never compared, so the
    second reading cannot raise a false alarm.
    """
    if not settings.ocr_cross_read:
        return None
    from app.services.ocr import tesseract_table
    from app.services.ocr.invoice_parser import parse_scanned_invoice

    if not tesseract_table.available():
        return None
    try:
        reading = parse_scanned_invoice(data, content_type, max_pages=MAX_PAGES, header_only_ok=True)
    except Exception as exc:  # noqa: BLE001 - a second opinion that fails is no opinion
        log.warning("cross_read: second reading failed (%s)", exc)
        return None
    if reading:
        _trustworthy(reading, (reading.get("_hints") or {}).get("document_text") or "")
        if content_type == "application/pdf":
            from app.services.ocr.pdf_utils import page_count

            reading["_partial"] = page_count(data) > MAX_PAGES
    return reading


def _trustworthy(reading: dict, text: str) -> None:
    """Keep only header values a reader can stand behind.

    * An invoice number holds a digit - a Tally bill's "Invoice No.   Dated"
      heading otherwise gives "Dated".
    * GSTINs are the ones whose check character is right, placed by where they
      sit relative to the buyer's heading (party_check) - never by the flat OCR
      text's order, which interleaves the party blocks.
    """
    from app.services.ocr.party_check import page_party_gstins

    invoice = reading.get("invoice") or {}
    if not re.search(r"\d", _value(reading, "invoice.invoice_no")):
        invoice.pop("invoice_no", None)
    supplier, buyer, _every = page_party_gstins(text)
    for party, gstin in (("supplier", supplier), ("bill_to", buyer)):
        block = reading.setdefault(party, {})
        if gstin:
            block["gstin"] = {"value": gstin, "confidence": 1.0}
        else:
            block.pop("gstin", None)


SKIPPED = {"id": "cross_read", "label": "A second reading of the scan agrees", "status": "skipped",
           "message": "The second reading could not read this scan.", "fields": []}
