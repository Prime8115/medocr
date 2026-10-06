"""Learning a supplier's layout from reviewers' corrections, and using it.

See services/ocr/learned_labels.py for what is learned. This is the part that
needs the database:

* `learn_from_edit` - when a reviewer fills in or corrects a header field, the
  label printed in front of the value they typed is stored for that shop and
  supplier GSTIN.
* `apply_learned` - when that supplier's next bill arrives, each stored label
  is looked up on the page: a blank field is filled from it; a field the
  reader got wrong last time is replaced from it. What it fills is held at a
  lower confidence, listed in `meta.learned_filled`, and every check re-run.

Fields that a reviewer CHOICE owns (which of two references is the PO) are
left to the choice: services/supplier_choices.py remembers those.
"""
import copy
import logging
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.supplier_label import SupplierLabel
from app.services.ocr import choices as ch
from app.services.ocr.learned_labels import LEARNABLE, LEARNED_CONFIDENCE, find, learn
from app.services.ocr.verify import reverify

log = logging.getLogger(__name__)


def _value(fields: dict, path: str) -> str:
    section, _, key = path.partition(".")
    leaf = ((fields or {}).get(section) or {}).get(key)
    return str((leaf or {}).get("value") or "").strip() if isinstance(leaf, dict) else ""


def _gstin(fields: dict) -> Optional[str]:
    value = _value(fields, "supplier.gstin").upper()
    return value if len(value) == 15 else None


def _owned_by_choices(meta: dict) -> set:
    return {path for c in (meta or {}).get("choices") or [] for path in c.get("fields") or []}


def _lines(fields: dict) -> list:
    out = []
    for line in (fields or {}).get("line_items") or []:
        values = {}
        for key, leaf in (line or {}).items():
            value = leaf.get("value") if isinstance(leaf, dict) else leaf
            if value not in (None, ""):
                values[key] = str(value).strip()
        out.append(values)
    return out


def changed_paths(old_fields: dict, new_fields: dict) -> List[str]:
    """Header fields whose value a reviewer changed, plus "line_items" when any
    line did - for the audit log, and the supplier coverage report."""
    out = []
    for section in ("supplier", "bill_to", "ship_to", "invoice"):
        keys = set(((old_fields or {}).get(section) or {})) | set(((new_fields or {}).get(section) or {}))
        for key in sorted(keys):
            path = f"{section}.{key}"
            if _value(old_fields, path) != _value(new_fields, path):
                out.append(path)
    if _lines(old_fields) != _lines(new_fields):
        out.append("line_items")
    return out


def learn_from_edit(db: Session, shop_id: str, old_payload: dict, new_fields: dict,
                    user_id: Optional[str]) -> List[str]:
    """Store where this supplier prints each header field the reviewer filled
    in or corrected. Returns the fields learned. Never raises."""
    try:
        meta = (old_payload or {}).get("meta") or {}
        page = meta.get("page_text") or ""
        gstin = _gstin(new_fields)
        if not page or not gstin:
            return []
        old_fields = (old_payload or {}).get("fields") or {}
        owned = _owned_by_choices(meta)
        learned_paths = []
        for path in LEARNABLE:
            old, new = _value(old_fields, path), _value(new_fields, path)
            if not new or new == old or path in owned:
                continue
            learned = learn(page, path, new, new_fields)
            if not learned:
                continue
            values = {
                "label": learned["label"][:128], "position": learned["where"],
                "shape": (learned.get("shape") or "")[:64], "words": learned.get("words") or 1,
                "tail": learned.get("tail"),
                "learned_from": "misread" if old else "blank", "learned_by": user_id,
            }
            row = (db.query(SupplierLabel)
                   .filter_by(shop_id=shop_id, supplier_gstin=gstin, field=path).first())
            if row:
                for key, value in values.items():
                    setattr(row, key, value)
            else:
                db.add(SupplierLabel(shop_id=shop_id, supplier_gstin=gstin, field=path,
                                     times_used=0, **values))
            learned_paths.append(path)
        return learned_paths
    except Exception:  # noqa: BLE001 - learning is a convenience, never a failure
        log.exception("could not learn supplier labels from an edit")
        return []


def apply_learned(db: Session, shop_id: str, doc_type: str, payload: dict) -> dict:
    """Read each field this shop has taught us for this supplier, on arrival.
    Returns the payload, changed or not. Never raises."""
    try:
        if doc_type != "invoice":
            return payload
        fields = copy.deepcopy(payload.get("fields") or {})
        meta = payload.get("meta") or {}
        page = meta.get("page_text") or ""
        gstin = _gstin(fields)
        if not page or not gstin:
            return payload
        rows = db.query(SupplierLabel).filter_by(shop_id=shop_id, supplier_gstin=gstin).all()
        if not rows:
            return payload
        owned = _owned_by_choices(meta)
        filled = []
        for row in rows:
            if row.field not in LEARNABLE or row.field in owned:
                continue
            learned = {"path": row.field, "label": row.label, "where": row.position,
                       "shape": row.shape, "words": row.words, "tail": row.tail}
            value = find(page, learned, fields)
            current = _value(fields, row.field)
            if not value or value == current:
                continue
            if current and row.learned_from != "misread":
                continue
            section, _, key = row.field.partition(".")
            fields.setdefault(section, {})[key] = {"value": value, "confidence": LEARNED_CONFIDENCE}
            filled.append({"path": row.field, "value": value, "label": row.label,
                           "replaced": current or None})
            row.times_used = (row.times_used or 0) + 1
        if not filled:
            return payload
        new_meta = reverify(doc_type, payload, fields)
        new_meta["choices"] = meta.get("choices")
        new_meta["learned_filled"] = filled
        ch.settle_checks(new_meta)
        payload = {**payload, "fields": fields, "meta": new_meta}
        log.info("supplier labels: filled %s", [f["path"] for f in filled])
        return payload
    except Exception:  # noqa: BLE001
        log.exception("could not apply learned supplier labels")
        return payload
