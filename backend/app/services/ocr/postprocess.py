"""Deterministic post-processing of extracted fields.

Adds normalized companions WITHOUT destroying the original OCR values:
  - dates      -> ISO 'YYYY-MM-DD' in <field>.normalized
  - quantities -> integer/float in <field>.normalized
  - strengths  -> {amount, unit} in <field>.normalized
  - medication names -> normalized_id via a pluggable hook (stub for now)
"""
import re
from typing import Optional, Tuple

from dateutil import parser as date_parser


def normalize_date(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        # dayfirst=True: Indian pharmacy docs use DD/MM/YYYY.
        return date_parser.parse(value, dayfirst=True, fuzzy=True).date().isoformat()
    except (ValueError, OverflowError, TypeError):
        return None


_NUM_RE = re.compile(r"[-+]?\d*\.?\d+")


def normalize_quantity(value: Optional[str]):
    if not value:
        return None
    m = _NUM_RE.search(value)
    if not m:
        return None
    num = m.group()
    return float(num) if "." in num else int(num)


_STRENGTH_RE = re.compile(r"(?P<amount>\d*\.?\d+)\s*(?P<unit>mg|mcg|g|ml|iu|%)", re.IGNORECASE)


def normalize_strength(value: Optional[str]):
    if not value:
        return None
    m = _STRENGTH_RE.search(value)
    if not m:
        return None
    amount = m.group("amount")
    return {
        "amount": float(amount) if "." in amount else int(amount),
        "unit": m.group("unit").lower(),
    }


def normalize_medication_name(value: Optional[str]) -> Optional[str]:
    """Hook for mapping a raw medicine name to a catalog id.

    Real drug-database lookup lands later; for now returns None (no match).
    """
    return None


def _set_norm(field: dict, normalized) -> None:
    if isinstance(field, dict) and normalized is not None:
        field["normalized"] = normalized


_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_DASHES = re.compile("[\u2010-\u2015\u2212]")


def _month_year(token: str) -> Optional[Tuple[int, int]]:
    """(year, month) of an expiry-shaped token - "08/26", "08-2026", "Aug-26"."""
    m = (re.fullmatch(r"(\d{1,2})[/\-.](\d{2}|\d{4})", token)
         or re.fullmatch(r"([A-Za-z]{3})[-/' ]?(\d{2}|\d{4})", token))
    if not m:
        return None
    month = _MONTHS.get(m.group(1).lower()) if m.group(1).isalpha() else int(m.group(1))
    return (int(m.group(2)) % 100, month) if month and 1 <= month <= 12 else None


def _two_dates(value) -> Optional[Tuple[str, str]]:
    """(earlier, later) when a cell holds exactly two different month-year
    dates side by side, else None."""
    tokens = str(value or "").split()
    if len(tokens) != 2:
        return None
    when = [_month_year(t) for t in tokens]
    if None in when or when[0] == when[1]:
        return None
    return (tokens[0], tokens[1]) if when[0] < when[1] else (tokens[1], tokens[0])


def tidy_line(item: dict) -> None:
    """Values one cell printed in a shape the stock software cannot take."""
    # A batch is keyed in as printed, with a plain hyphen: MAX RELIEF draws
    # "TPG — 928" with a long dash.
    batch = item.get("batch_no")
    if isinstance(batch, dict) and isinstance(batch.get("value"), str) and _DASHES.search(batch["value"]):
        batch["value"] = _DASHES.sub("-", batch["value"])
    # Two dates in the expiry cell, side by side: IPCA heads the column "MFG.
    # Expiry" and prints "03/25 08/26". A medicine expires after it is made, so
    # the later date is the expiry and the earlier the manufacturing date.
    expiry = item.get("expiry")
    pair = _two_dates(expiry.get("value")) if isinstance(expiry, dict) else None
    if pair:
        expiry["value"] = pair[1]
        if not (item.get("mfg_date") or {}).get("value"):
            item["mfg_date"] = {"value": pair[0], "confidence": expiry.get("confidence")}


def postprocess_fields(doc_type: str, fields: dict) -> dict:
    """Enrich fields in place and return them."""
    if doc_type == "prescription":
        for med in fields.get("medications", []) or []:
            _set_norm(med.get("strength"), normalize_strength((med.get("strength") or {}).get("value")))
            _set_norm(med.get("duration"), normalize_quantity((med.get("duration") or {}).get("value")))
            nid = normalize_medication_name((med.get("name") or {}).get("value"))
            if isinstance(med.get("name"), dict) and nid:
                med["name"]["normalized_id"] = nid
    elif doc_type == "invoice":
        inv = fields.get("invoice") or {}
        for d_key in ("invoice_date", "due_date", "lr_date", "po_date"):
            _set_norm(inv.get(d_key), normalize_date((inv.get(d_key) or {}).get("value")))
        sup = fields.get("supplier") or {}
        for dl_d_key in ("dl_date_1", "dl_date_2", "dl_date_3"):
            _set_norm(sup.get(dl_d_key), normalize_date((sup.get(dl_d_key) or {}).get("value")))
        for item in fields.get("line_items", []) or []:
            if isinstance(item, dict):
                tidy_line(item)
            _set_norm(item.get("expiry"), normalize_date((item.get("expiry") or {}).get("value")))
            _set_norm(item.get("quantity"), normalize_quantity((item.get("quantity") or {}).get("value")))
    return fields
