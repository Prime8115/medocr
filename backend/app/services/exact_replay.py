"""Replay human edits only when the very same uploaded invoice is read again.

Supplier rules need a printed locator and can transfer to a different invoice.
These edits cannot: a content hash and PDF part match confines them to the
identical source file, while the new scan's checks still run after replay.
"""
import copy
import re

from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.document_correction import DocumentCorrection
from app.services.ocr.verify import reverify


def _value(fields, section, key):
    leaf = ((fields or {}).get(section) or {}).get(key)
    return str((leaf or {}).get("value") or "").strip() if isinstance(leaf, dict) else ""


_FOREIGN_ADDRESS_LABEL = re.compile(
    r"\b(?:DELIVERY\s+AT|GR\s*/\s*PR\s+(?:NO|DATE)|E\s*-?\s*WAY\s+BILL|"
    r"GSTIN|INVOICE\s+(?:NO|DATE)|TRANSPORT(?:ER)?\s*:)", re.I)


def _safe_edit(path: str, value) -> bool:
    # A review draft may itself contain the adjacent column. Do not turn an
    # experimental edit with a delivery/reference label into the saved answer.
    return not (path.endswith(".address") and value and _FOREIGN_ADDRESS_LABEL.search(str(value)))


def _same_invoice(source: Document, target: Document, fields: dict, include_self: bool = False) -> bool:
    if source.doc_type != "invoice" or (source.id == target.id and not include_self):
        return False
    if (source.part_no or 1) != (target.part_no or 1) or (source.part_pages or "") != (target.part_pages or ""):
        return False
    prior = (source.payload or {}).get("fields") or {}
    a, b = _value(prior, "supplier", "gstin"), _value(fields, "supplier", "gstin")
    if a and b and a != b:
        return False
    # OCR may miss a number, but it must never apply a correction to a
    # *different* invoice in a PDF that was split differently on another run.
    a, b = _value(prior, "invoice", "invoice_no"), _value(fields, "invoice", "invoice_no")
    return not (a and b and a != b)


def has_unapplied(db: Session, target: Document) -> bool:
    """A saved copy is stale if a correction on any copy of these bytes differs.

    The next ordinary upload can then make one new reading containing *all*
    corrections, rather than reopening one of several partial saved reviews.
    """
    if target.doc_type != "invoice" or not target.content_hash or not target.payload:
        return False
    fields = (target.payload or {}).get("fields") or {}
    corrections = (db.query(DocumentCorrection, Document)
                   .join(Document, DocumentCorrection.document_id == Document.id)
                   .filter(DocumentCorrection.shop_id == target.shop_id,
                           Document.shop_id == target.shop_id,
                           Document.content_hash == target.content_hash)
                   .order_by(DocumentCorrection.created_at.asc(), DocumentCorrection.id.asc()).all())
    latest = {}
    for correction, source in corrections:
        if _same_invoice(source, target, fields, include_self=True) and _safe_edit(correction.field, correction.value_corrected):
            latest[(correction.field, correction.line)] = (correction.value_corrected, source)
    for (path, index), (corrected, source) in latest.items():
        if path.startswith("line."):
            if index is not None and index >= 0 and _same_line((source.payload or {}).get("fields") or {}, fields, index):
                leaf = (fields.get("line_items") or [])[index].get(path[5:])
                current = str((leaf or {}).get("value") or "").strip() if isinstance(leaf, dict) else str(leaf or "").strip()
                if current != str(corrected or "").strip():
                    return True
            continue
        section, _, key = path.partition(".")
        if section in ("supplier", "bill_to", "ship_to", "invoice"):
            if _value(fields, section, key) != str(corrected or "").strip():
                return True
    return False


def _same_line(source_fields: dict, target_fields: dict, index: int) -> bool:
    prior = source_fields.get("line_items") or []
    current = target_fields.get("line_items") or []
    if len(prior) != len(current) or index >= len(current):
        return False
    for key in ("product_name", "description", "product_code", "batch_no"):
        a = str((prior[index].get(key) or {}).get("value") or "").strip().casefold()
        b = str((current[index].get(key) or {}).get("value") or "").strip().casefold()
        if a and b and a != b:
            return False
    return True


def apply(db: Session, target: Document, payload: dict) -> dict:
    if payload.get("doc_type") != "invoice" or not target.content_hash:
        return payload
    fields = copy.deepcopy(payload.get("fields") or {})
    candidates = (db.query(DocumentCorrection, Document)
                  .join(Document, DocumentCorrection.document_id == Document.id)
                  .filter(DocumentCorrection.shop_id == target.shop_id,
                          Document.shop_id == target.shop_id,
                          Document.content_hash == target.content_hash,
                          Document.id != target.id,
                          Document.created_at <= target.created_at,
                          DocumentCorrection.created_at <= target.created_at)
                  .order_by(DocumentCorrection.created_at.asc(), DocumentCorrection.id.asc()).all())
    latest = {}
    for correction, source in candidates:
        if _same_invoice(source, target, fields) and _safe_edit(correction.field, correction.value_corrected):
            latest[(correction.field, correction.line)] = (correction, source)
    applied = []
    for (path, index), (correction, source) in latest.items():
        if path == "line_items":
            continue
        if path.startswith("line."):
            if index is None or index < 0 or not _same_line((source.payload or {}).get("fields") or {}, fields, index):
                continue
            leaf = (fields.get("line_items") or [])[index]
            key = path[5:]
        else:
            section, separator, key = path.partition(".")
            if not separator or section not in ("supplier", "bill_to", "ship_to", "invoice"):
                continue
            leaf = fields.setdefault(section, {})
        old = leaf.get(key)
        old_value = old.get("value") if isinstance(old, dict) else old
        if str(old_value or "").strip() == str(correction.value_corrected or "").strip():
            continue
        leaf[key] = {**(old if isinstance(old, dict) else {}),
                     "value": correction.value_corrected, "confidence": 0.8}
        applied.append(path if index is None else f"line_items[{index}].{key}")
    if not applied:
        return payload
    meta = reverify("invoice", payload, fields)
    meta["exact_file_corrections"] = sorted(set(applied))
    return {**payload, "fields": fields, "meta": meta}
