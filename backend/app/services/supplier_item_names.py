"""Reuse approved product-name corrections on another bill from the supplier.

This is narrower than a general product catalogue: only a name the reviewer
actually corrected can be reused, and only when the supplier and the OCR's old
name match. A numeric row/date prefix may be ignored because some tables feed
that adjacent column into the product name on a later scan.
"""
import copy
import re

from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.document_correction import DocumentCorrection
from app.services import lifecycle
from app.services.ocr.verify import reverify


def _value(fields: dict, section: str, key: str) -> str:
    leaf = ((fields or {}).get(section) or {}).get(key)
    return str((leaf or {}).get("value") or "").strip() if isinstance(leaf, dict) else ""


def _normal(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def _supplier_matches(source: dict, target: dict) -> bool:
    old_gstin = _normal(_value(source, "supplier", "gstin"))
    new_gstin = _normal(_value(target, "supplier", "gstin"))
    if old_gstin and new_gstin:
        return old_gstin == new_gstin
    old_name = re.sub(r"[^a-z0-9]", "", _normal(_value(source, "supplier", "name")))
    new_name = re.sub(r"[^a-z0-9]", "", _normal(_value(target, "supplier", "name")))
    return len(old_name) >= 6 and old_name == new_name


def _same_product_context(old: dict, new: dict) -> bool:
    for key in ("product_code", "hsn", "pack"):
        a = _normal(str((old.get(key) or {}).get("value") or ""))
        b = _normal(str((new.get(key) or {}).get("value") or ""))
        if a and b and a != b:
            return False
    return True


_NUMERIC_PREFIX = re.compile(r"[\d\s,./()\-]+")


def _name_matches(raw: str, old: str, corrected: str) -> bool:
    raw, old, corrected = _normal(raw), _normal(old), _normal(corrected)
    if len(old) < 5 or len(corrected) < 5 or old == corrected:
        return False
    if raw == old:
        return True
    # A nearby expiry range and row number can be read into the name:
    # "12-25, 26-36 2 [RM 1454] MEDICINE". Only an entirely numeric/punctuation
    # prefix may be stripped; a strength or a different product word cannot.
    for suffix in (old, corrected):
        if len(suffix) >= 10 and raw.endswith(" " + suffix):
            prefix = raw[:-(len(suffix) + 1)]
            if _NUMERIC_PREFIX.fullmatch(prefix) and any(ch.isdigit() for ch in prefix):
                return True
    return False


def apply(db: Session, target: Document, payload: dict) -> dict:
    if payload.get("doc_type") != "invoice":
        return payload
    fields = payload.get("fields") or {}
    lines = fields.get("line_items") or []
    if not lines:
        return payload
    candidates = (db.query(DocumentCorrection, Document)
                  .join(Document, DocumentCorrection.document_id == Document.id)
                  .filter(DocumentCorrection.shop_id == target.shop_id,
                          Document.shop_id == target.shop_id,
                          Document.id != target.id,
                          Document.status.in_((lifecycle.APPROVED, lifecycle.PUSHED)),
                          Document.created_at <= target.created_at,
                          DocumentCorrection.field.in_(("line.description", "line.product_name")),
                          DocumentCorrection.created_at <= target.created_at)
                  .order_by(DocumentCorrection.created_at.desc(), DocumentCorrection.id.desc())
                  .all())
    proposals: dict[tuple[int, str], set[str]] = {}
    for correction, source in candidates:
        index = correction.line
        if index is None or index < 0:
            continue
        source_payload = source.payload or {}
        source_fields = source_payload.get("fields") or {}
        if not _supplier_matches(source_fields, fields):
            continue
        source_rows = source_fields.get("line_items") or []
        original_rows = (((source_payload.get("meta") or {}).get("original_fields") or {})
                         .get("line_items") or [])
        if index >= len(source_rows):
            continue
        key = correction.field[5:]
        old_row = original_rows[index] if index < len(original_rows) else source_rows[index]
        old_name = str(correction.value_read or (old_row.get(key) or {}).get("value") or "")
        corrected = str((source_rows[index].get(key) or {}).get("value") or "").strip()
        if not corrected or _normal(old_name) == _normal(corrected):
            continue
        for current_index, row in enumerate(lines):
            if not _same_product_context(old_row, row):
                continue
            raw = str((row.get(key) or {}).get("value") or "")
            if _name_matches(raw, old_name, corrected):
                proposals.setdefault((current_index, key), set()).add(corrected)

    updated = copy.deepcopy(fields)
    applied = []
    for (index, key), names in proposals.items():
        # Two reviewers taught different answers for the same raw name. Leave
        # this occurrence for review instead of guessing which one is right.
        if len({_normal(name) for name in names}) != 1:
            continue
        name = next(iter(names))
        old = updated["line_items"][index].get(key) or {}
        if _normal(old.get("value")) == _normal(name):
            continue
        updated["line_items"][index][key] = {**old, "value": name, "confidence": 0.8}
        applied.append(f"line_items[{index}].{key}")
    if not applied:
        return payload
    meta = reverify("invoice", payload, updated)
    meta["supplier_item_names_applied"] = sorted(applied)
    return {**payload, "fields": updated, "meta": meta}
