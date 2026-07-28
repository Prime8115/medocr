"""Deterministic post-processing of extracted fields.

Adds normalized companions WITHOUT destroying the original OCR values:
  - dates      -> ISO 'YYYY-MM-DD' in <field>.normalized
  - quantities -> integer/float in <field>.normalized
  - strengths  -> {amount, unit} in <field>.normalized
  - medication names -> normalized_id via a pluggable hook (stub for now)
"""
import re
from typing import Optional

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
        _set_norm(inv.get("invoice_date"), normalize_date((inv.get("invoice_date") or {}).get("value")))
        for item in fields.get("line_items", []) or []:
            _set_norm(item.get("expiry"), normalize_date((item.get("expiry") or {}).get("value")))
            _set_norm(item.get("quantity"), normalize_quantity((item.get("quantity") or {}).get("value")))
    return fields
