"""Filling the gaps a new supplier's layout leaves - with the AI, on a short leash.

The deterministic reader is exact but literal: a supplier who labels a field in
a way it has not seen leaves that field blank. The AI reads any wording - and
can also invent a value. So it is used only for what is missing, and nothing it
says is trusted on its word:

* It is asked only when something IS missing - a field the bill prints that we
  did not read (missed_fields.py), or a field no invoice can do without. A bill
  read completely never reaches it, so it costs nothing on the suppliers we
  already know.
* It is sent the page's TEXT, not its image, and only the names of the fields
  wanted.
* An answer is accepted only if it appears on the page word for word, and only
  if it passes that field's own test - a GSTIN its check digit, a date a real
  date, an IRN its shape. Anything else is dropped, and the field stays blank
  and flagged, exactly as before.
* What it fills is marked: lower confidence, so review highlights it, and
  listed in `meta.gap_filled`.
"""
import json
import logging
import re
from typing import Dict, List, Optional

from app.config import settings
from app.services.ocr.invoice_header import gstin_is_valid

log = logging.getLogger(__name__)

# Fields worth asking for, and what a real value of each looks like.
_ASKABLE: Dict[str, str] = {
    "invoice.invoice_no": "reference", "invoice.invoice_date": "date",
    "invoice.due_date": "date", "invoice.lr_no": "reference", "invoice.lr_date": "date",
    "invoice.po_no": "reference", "invoice.po_date": "date", "invoice.irn": "irn",
    "invoice.eway_bill_no": "eway", "invoice.transport": "text", "invoice.total_amount": "money",
    "supplier.name": "text", "supplier.gstin": "gstin", "supplier.email": "email",
    "supplier.dl_no_1": "reference", "supplier.dl_no_2": "reference",
    "bill_to.name": "text", "bill_to.gstin": "gstin",
    "ship_to.name": "text", "ship_to.gstin": "gstin",
}
# Blank, any one of these alone is reason to ask.
_ESSENTIAL = ("invoice.invoice_no", "invoice.invoice_date", "invoice.total_amount",
              "supplier.name", "supplier.gstin", "bill_to.gstin")
# Held below a value read directly, so the review screen marks it for a glance.
GAP_FILL_CONFIDENCE = 0.6
_MAX_TEXT = 24000

_PROMPT = """You are reading the text of an Indian GST tax invoice from a pharmaceutical supplier.

Return a JSON object with exactly these keys:
{keys}

Rules:
- Copy each value EXACTLY as it is printed in the text below - same spelling, digits and punctuation.
- If the invoice does not print a value for a key, use null. Never guess or compute a value.
- "supplier" is the company issuing the invoice; "bill_to" and "ship_to" are the buyer.

Invoice text:
<<<
{text}
>>>"""


def _get(fields: dict, path: str) -> str:
    section, _, key = path.partition(".")
    leaf = (fields.get(section) or {}).get(key)
    return str((leaf or {}).get("value") or "").strip() if isinstance(leaf, dict) else ""


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def _acceptable(path: str, value: str, page: str, fields: dict) -> bool:
    """On the page word for word, and the right shape for its field."""
    kind = _ASKABLE[path]
    if kind == "money":
        digits = value.replace(",", "")
        if not re.fullmatch(r"\d+(?:\.\d{1,2})?", digits):
            return False
        return digits in page.replace(",", "")
    if kind == "irn":
        # Printed wrapped over lines - compare with the line breaks taken out.
        return (bool(re.fullmatch(r"[A-Fa-f0-9]{64}", value))
                and value.lower() in re.sub(r"\s+", "", page).lower())
    if _squash(value) not in _squash(page):
        return False
    if kind == "gstin":
        if not gstin_is_valid(value):
            return False
        # The supplier's GSTIN is never the buyer's, and vice versa.
        other = "bill_to.gstin" if path == "supplier.gstin" else "supplier.gstin"
        return value.upper() != _get(fields, other).upper()
    if kind == "date":
        from app.services.ocr.verify import parse_month
        return parse_month(value) is not None
    if kind == "eway":
        return bool(re.fullmatch(r"\d{10,16}", value))
    if kind == "email":
        return bool(re.fullmatch(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+", value))
    if kind == "reference":
        return 2 <= len(value) <= 64 and bool(re.search(r"[A-Za-z0-9]", value))
    return 2 <= len(value) <= 120


def wanted(fields: dict, missed: List[dict]) -> List[str]:
    """The fields to ask for, or [] when nothing calls for asking."""
    missed_paths = [m["path"] for m in missed if m["path"] in _ASKABLE]
    essential_blank = [p for p in _ESSENTIAL if not _get(fields, p)]
    if not missed_paths and not essential_blank:
        return []
    return [p for p in _ASKABLE if not _get(fields, p)]


def fill(fields: dict, page_text: str, missed: List[dict], provider=None) -> List[dict]:
    """Ask for the gaps and keep only the answers that prove themselves.

    Returns what was filled, as [{"path", "value"}]. Never raises: a gap-fill
    that fails leaves the reading exactly as it was.
    """
    if not settings.ocr_gap_fill or not page_text:
        return []
    paths = wanted(fields, missed)
    if not paths:
        return []
    try:
        if provider is None:
            from app.services.ocr import get_provider
            provider = get_provider()
        prompt = _PROMPT.format(keys=json.dumps(paths), text=page_text[:_MAX_TEXT])
        answer = provider.complete_json(prompt) or {}
    except Exception as exc:  # noqa: BLE001 - a missing gap-fill is never a failure
        log.info("gap_fill: not available (%s)", exc)
        return []

    filled: List[dict] = []
    for path in paths:
        value = answer.get(path)
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            continue
        value = str(value).strip()
        if not value or not _acceptable(path, value, page_text, fields):
            continue
        section, _, key = path.partition(".")
        fields.setdefault(section, {})[key] = {"value": value, "confidence": GAP_FILL_CONFIDENCE}
        filled.append({"path": path, "value": value})
    if filled:
        log.info("gap_fill: filled %s", [f["path"] for f in filled])
    return filled
