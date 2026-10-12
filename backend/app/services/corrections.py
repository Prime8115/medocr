"""Recording what reviewers correct, value by value.

The first reading of every document is kept (meta.original_fields), and each
edit records a DocumentCorrection row per field that changed: the value the
reader produced, the value the reviewer put, the supplier, and - for a line
field - the printed column heading the reader took it from. These rows are
what supplier lessons are learned from (services/supplier_profile.py), and
what turns a client's correction into a test.
"""
import copy
from typing import Iterable, List, Optional

from sqlalchemy.orm import Session

from app.models.document_correction import DocumentCorrection

_SECTIONS = ("supplier", "bill_to", "ship_to", "invoice")
# Fields a reviewer never "corrects": labels the app derives for display.
_SKIP = {"rate_source", "extras"}


def _value(leaf) -> Optional[str]:
    if isinstance(leaf, dict):
        leaf = leaf.get("value")
    if leaf is None:
        return None
    text = str(leaf).strip()
    return text or None


def _same(a: Optional[str], b: Optional[str]) -> bool:
    if a == b:
        return True
    if a is None or b is None:
        return False
    try:   # "1,234.50" and "1234.5" are the same figure
        return abs(float(a.replace(",", "")) - float(b.replace(",", ""))) < 0.005
    except ValueError:
        return " ".join(a.split()).lower() == " ".join(b.split()).lower()


def diffs(old: dict, new: dict) -> List[dict]:
    """Every field whose value changed between two readings:
    {field, line, old, new}. Lines are compared position by position; a line
    added or removed is recorded once as field "line_items"."""
    out: List[dict] = []
    for section in _SECTIONS:
        a, b = (old or {}).get(section) or {}, (new or {}).get(section) or {}
        for key in sorted(set(a) | set(b)):
            if key in _SKIP:
                continue
            va, vb = _value(a.get(key)), _value(b.get(key))
            if not _same(va, vb):
                out.append({"field": f"{section}.{key}", "line": None, "old": va, "new": vb})
    la, lb = (old or {}).get("line_items") or [], (new or {}).get("line_items") or []
    for i, (ia, ib) in enumerate(zip(la, lb)):
        for key in sorted(set(ia) | set(ib)):
            if key in _SKIP:
                continue
            va, vb = _value(ia.get(key)), _value(ib.get(key))
            if not _same(va, vb):
                out.append({"field": f"line.{key}", "line": i, "old": va, "new": vb})
    if len(la) != len(lb):
        out.append({"field": "line_items", "line": None, "old": str(len(la)), "new": str(len(lb))})
    return out


def keep_original(result: dict) -> dict:
    """Store the reading as it arrived, once - the base every later correction
    is measured against."""
    meta = result.setdefault("meta", {})
    if "original_fields" not in meta:
        meta["original_fields"] = copy.deepcopy(result.get("fields") or {})
    return result


def record(db: Session, shop_id: str, document_id: str, payload: dict, old_fields: dict,
           new_fields: dict, user_id: Optional[str]) -> List[DocumentCorrection]:
    """Add a DocumentCorrection for each field this edit changed."""
    meta = (payload or {}).get("meta") or {}
    columns = meta.get("columns") or {}
    supplier = _value(((new_fields or {}).get("supplier") or {}).get("gstin")) \
        or _value(((old_fields or {}).get("supplier") or {}).get("gstin"))
    rows = []
    for d in diffs(old_fields, new_fields):
        source = columns.get(d["field"][5:]) if d["field"].startswith("line.") else None
        row = DocumentCorrection(
            shop_id=shop_id, document_id=document_id,
            supplier_gstin=(supplier or "")[:15] or None,
            field=d["field"][:64], line=d["line"],
            value_read=d["old"], value_corrected=d["new"],
            source=(source or None) and str(source)[:128],
            pipeline=(meta.get("pipeline") or None) and str(meta.get("pipeline"))[:32],
            corrected_by=user_id)
        db.add(row)
        rows.append(row)
    return rows


def for_supplier(db: Session, shop_id: str, supplier_gstin: str) -> Iterable[DocumentCorrection]:
    return (db.query(DocumentCorrection)
            .filter_by(shop_id=shop_id, supplier_gstin=supplier_gstin)
            .order_by(DocumentCorrection.created_at))
