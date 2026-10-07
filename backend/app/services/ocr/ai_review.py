"""A second AI, acting as the reviewer - the check a pharmacist would make.

The first AI reads a scan; a person checking its work would hold the reading
beside the paper and tick each value off. This does the same: a DIFFERENT
model (where two are configured) is shown the page and the list of values
read, and answers, for each, whether the page shows exactly that.

    pass     the reviewer saw every value it was shown, as read;
    fail     it sees some values differently - each is flagged on its field,
             with what the reviewer saw, for the pharmacist to settle;
    skipped  it could not run (no quota, timeout, unreadable answer).

It never changes a value: a second reader can misread too, so a disagreement
sends the pharmacist to that field rather than overruling either reader. And
it never votes on what it did not answer - a value it left out counts as
unreviewed, never as confirmed, so a lazy answer cannot pass a bill.

Digital PDFs are read exactly from their own text and are not reviewed.
"""
import json
import logging
from typing import Dict, List, Optional, Tuple

from app.config import settings

log = logging.getLogger(__name__)

LABEL = "An AI reviewer checked the values against the scan"

_HEADER = (
    ("invoice.invoice_no", "Invoice number", "text"),
    ("invoice.invoice_date", "Invoice date", "date"),
    ("invoice.total_amount", "Bill total", "money"),
    ("invoice.total_taxable_amount", "Taxable total", "money"),
    ("invoice.total_discount_amount", "Bill discount", "money"),
    ("invoice.total_cgst_amount", "CGST total", "money"),
    ("invoice.total_sgst_amount", "SGST total", "money"),
    ("invoice.total_igst_amount", "IGST total", "money"),
    ("invoice.total_utgst_amount", "UTGST total", "money"),
    ("invoice.total_gst_amount", "GST total", "money"),
    ("supplier.name", "Supplier name", "name"),
    ("supplier.gstin", "Supplier GSTIN", "text"),
    ("supplier.pan", "Supplier PAN", "text"),
    ("supplier.dl_no_1", "Supplier DL no. 1", "text"),
    ("supplier.dl_no_2", "Supplier DL no. 2", "text"),
    ("bill_to.name", "Bill-to name", "name"),
    ("bill_to.gstin", "Bill-to GSTIN", "text"),
    ("bill_to.pan", "Bill-to PAN", "text"),
)
_LINE = (
    ("description", "product", "name"),
    ("hsn", "HSN", "text"),
    ("batch_no", "batch", "text"),
    ("expiry", "expiry", "date"),
    ("quantity", "qty", "number"),
    ("free_quantity", "free qty", "number"),
    ("mrp", "MRP", "money"),
    ("rate", "rate", "money"),
    ("discount_percent", "discount %", "number"),
    ("gst_percent", "GST %", "number"),
    ("amount", "amount", "money"),
)

PROMPT = """You are a pharmacy accounts reviewer. Another reader typed the values below
off this invoice. Check each one against the invoice image, as a careful
person would before the bill is paid.

For every id, decide:
  "match"   - the invoice prints exactly this value for that field;
  "differs" - the invoice prints something else for that field; give what it
              prints, character for character, in "seen";
  "unclear" - you cannot find or read that field on the invoice.

Judge the value, not its formatting: 1,234.50 and 1234.5 match; 05/03/2026
and 5-Mar-2026 match. A batch number, GSTIN, PAN or DL number must match
character for character. Do not guess: if unsure, answer "unclear".

Answer with JSON only, every id exactly once:
{"match": ["H1", ...], "differs": [{"id": "L2.b", "seen": "..."}], "unclear": ["..."]}

VALUES:
"""


def _leaf_value(node: dict, key: str) -> str:
    leaf = (node or {}).get(key)
    return str((leaf or {}).get("value") or "").strip() if isinstance(leaf, dict) else ""


def values_to_review(fields: dict, max_lines: int) -> Tuple[Dict[str, Tuple[str, str, str, str]], int]:
    """{short id: (field path, label, kind, value read)} for every value read,
    and how many lines were left out beyond `max_lines`. Short ids keep the
    answer small: H3 for a header field, L2.b for line 2's batch."""
    out: Dict[str, Tuple[str, str, str, str]] = {}
    n = 0
    for path, label, kind in _HEADER:
        section, _, key = path.partition(".")
        value = _leaf_value(fields.get(section) or {}, key)
        n += 1
        if value:
            out[f"H{n}"] = (path, label, kind, value)
    items = fields.get("line_items") or []
    for i, item in enumerate(items[:max_lines]):
        for j, (key, label, kind) in enumerate(_LINE):
            value = _leaf_value(item, key)
            if value:
                out[f"L{i + 1}.{chr(ord('a') + j)}"] = (f"line_items[{i}].{key}", f"line {i + 1} {label}",
                                                        kind, value)
    return out, max(0, len(items) - max_lines)


def build_prompt(values: Dict[str, Tuple[str, str, str, str]]) -> str:
    lines = [f'{vid}  {label}: {json.dumps(value, ensure_ascii=False)}'
             for vid, (_path, label, _kind, value) in values.items()]
    return PROMPT + "\n".join(lines)


def _same(kind: str, read: str, seen: str) -> bool:
    """Whether the reviewer's "differs" is only a difference of formatting."""
    from app.services.ocr.cross_read import _norm, _same as same_norm

    if kind == "name":
        squash = lambda s: "".join(ch for ch in s.upper() if ch.isalnum())  # noqa: E731
        return squash(read) == squash(seen)
    a, b = _norm(kind, read), _norm(kind, seen)
    return a is not None and b is not None and same_norm(kind, a, b)


def judge(values: Dict[str, Tuple[str, str, str, str]], answer: Optional[dict], lines_left_out: int = 0) -> dict:
    """The verification check from the reviewer's answer."""
    if not isinstance(answer, dict):
        return skipped("The reviewer's answer could not be read.")
    matched = {str(x).strip() for x in answer.get("match") or [] if isinstance(x, (str, int))}
    differs: List[str] = []
    implicated: List[str] = []
    confirmed = 0
    disputed_ids = set()
    for entry in answer.get("differs") or []:
        if not isinstance(entry, dict):
            continue
        vid = str(entry.get("id") or "").strip()
        seen = str(entry.get("seen") or "").strip()
        if vid not in values:
            continue
        path, label, kind, read = values[vid]
        disputed_ids.add(vid)
        if not seen or _same(kind, read, seen):
            confirmed += 1          # formatting only: the reviewer agrees
            continue
        differs.append(f"{label}: read {read!r}, reviewer sees {seen!r}")
        implicated.append(path)
    confirmed += len(matched & set(values))
    unreviewed = len(values) - confirmed - len(differs)
    tail = []
    if unreviewed > 0:
        tail.append(f"{unreviewed} value(s) it could not confirm")
    if lines_left_out:
        tail.append(f"{lines_left_out} line(s) beyond the first {settings.ocr_ai_review_max_lines} not reviewed")
    note = (" (" + "; ".join(tail) + ")") if tail else ""
    if differs:
        return {"id": "ai_review", "label": LABEL, "status": "fail",
                "message": f"{len(differs)} value(s) the reviewer sees differently - "
                           + "; ".join(differs[:6]) + (" ..." if len(differs) > 6 else "") + note + ".",
                "fields": sorted(set(implicated))}
    if not confirmed:
        return skipped("The reviewer confirmed no values.")
    return {"id": "ai_review", "label": LABEL, "status": "pass",
            "message": f"{confirmed} value(s) confirmed against the scan{note}.", "fields": []}


def skipped(why: str) -> dict:
    return {"id": "ai_review", "label": LABEL, "status": "skipped", "message": why, "fields": []}


def _page_image(file_bytes: bytes, content_type: str) -> Tuple[bytes, str]:
    """What the reviewer is shown: the picture of the page only - never a
    scanner's hidden text layer - and at most the first few pages."""
    if content_type != "application/pdf":
        return file_bytes, content_type
    from app.services.ocr.pdf_utils import image_only_pdf, page_count, split_pdf

    data = file_bytes
    if page_count(data) > settings.ocr_ai_review_max_pages:
        data = split_pdf(data, settings.ocr_ai_review_max_pages)[0]
    return image_only_pdf(data), "application/pdf"


def review(provider, fields: dict, file_bytes: bytes, content_type: str) -> dict:
    """Run the reviewer and return its check. Never raises: a reviewer that
    cannot run leaves the reading as it is, with the check marked skipped."""
    values, left_out = values_to_review(fields, settings.ocr_ai_review_max_lines)
    if not values:
        return skipped("There were no values to review.")
    try:
        image, ct = _page_image(file_bytes, content_type)
        answer = provider.review_json(build_prompt(values), image, ct)
    except Exception as exc:  # noqa: BLE001 - a reviewer that fails is no review
        log.warning("ai_review: could not run (%s)", str(exc)[:200])
        return skipped("The AI reviewer could not run this time.")
    if answer is None:
        return skipped("The AI reviewer is not available.")
    return judge(values, answer, left_out)
