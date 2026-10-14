"""Learning a supplier's columns from what reviewers correct - and keeping
only the lessons that keep working.

A reviewer corrects a line field. If the value they put is printed on the same
line under another of the bill's columns, that column is the field for this
supplier ("is"), and the column the reader used is not ("not"). A field
corrected on most lines to values printed nowhere else on the line still
teaches the "not". The supplier's next bill is read with these lessons
(ocr/lessons.py) - and a lesson the reviewer has to undo twice, or that twice
leaves a bill not adding up, is retired.
"""
import logging
from collections import Counter
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.supplier_column_lesson import SupplierColumnLesson
from app.services import corrections
from app.services.ocr.lessons import norm

log = logging.getLogger(__name__)

RETIRE_AFTER = 2
# Line fields read from a column of their own - the ones a column lesson can fix.
LEARNABLE = {
    "quantity", "free_quantity", "mrp", "ptr", "pts", "rate", "discount_percent", "discount_amount",
    "amount", "gross_amount", "net_amount", "hsn", "batch_no", "expiry", "mfg_date", "pack", "uom",
    "product_code", "manufacturer", "cgst_percent", "cgst_amount", "sgst_percent", "sgst_amount",
    "igst_percent", "igst_amount",
}


def lessons_for_shop(db: Session, shop_id: str) -> Dict[str, dict]:
    """Active lessons, scoped by shop, supplier and ordered table headings."""
    out: Dict[str, dict] = {}
    for row in db.query(SupplierColumnLesson).filter_by(shop_id=shop_id, active=True):
        if not row.layout_signature:
            continue
        entry = out.setdefault(row.supplier_gstin, {"layouts": {}})["layouts"].setdefault(
            row.layout_signature, {"is": {}, "not": {}, "printed": {}})
        entry["printed"][row.heading] = row.heading_printed or row.heading
        if row.kind == "is":
            entry["is"][row.heading] = row.field
        else:
            entry["not"].setdefault(row.heading, []).append(row.field)
    return out


def _find(db: Session, shop_id: str, gstin: str, heading: str, field: str, kind: str,
          layout_signature: str = ""):
    return (db.query(SupplierColumnLesson)
            .filter_by(shop_id=shop_id, supplier_gstin=gstin, heading=heading, field=field, kind=kind,
                       layout_signature=layout_signature)
            .first())


def _upsert(db: Session, shop_id: str, gstin: str, heading_printed: str, field: str, kind: str,
            user_id: Optional[str], layout_signature: str) -> SupplierColumnLesson:
    heading = norm(heading_printed)[:128]
    row = _find(db, shop_id, gstin, heading, field, kind, layout_signature)
    # One layout cannot teach two meanings for a column, or two columns for
    # one field. Retain superseded lessons as inactive history.
    for prior in db.query(SupplierColumnLesson).filter_by(
            shop_id=shop_id, supplier_gstin=gstin, layout_signature=layout_signature, active=True):
        if prior is row:
            continue
        if ((kind == "is" and prior.kind == "is" and (prior.heading == heading or prior.field == field))
                or (prior.heading == heading and prior.field == field and prior.kind != kind)):
            prior.active = False
    if row is None:
        row = SupplierColumnLesson(shop_id=shop_id, supplier_gstin=gstin, heading=heading,
                                   heading_printed=str(heading_printed)[:128], field=field, kind=kind,
                                   learned_by=user_id, times_used=0, times_undone=0, times_failed=0,
                                   active=True, layout_signature=layout_signature)
        db.add(row)
    else:
        # Taught again: whatever went wrong before, the reviewer means it.
        row.active, row.times_undone, row.times_failed, row.learned_by = True, 0, 0, user_id
    db.flush()
    return row


def _gstin(fields: dict) -> Optional[str]:
    value = str((((fields or {}).get("supplier") or {}).get("gstin") or {}).get("value") or "").strip().upper()
    return value if len(value) == 15 else None


def learn_from_edit(db: Session, shop_id: str, payload: dict, before: dict, after: dict,
                    user_id: Optional[str]) -> List[str]:
    """Learn column lessons from one edit; count undone ones. Returns what was
    learned, as "field <- heading" / "field not <- heading"."""
    gstin = _gstin(after) or _gstin(before)
    if not gstin:
        return []
    meta = (payload or {}).get("meta") or {}
    columns = meta.get("columns") or {}
    corrected: Dict[str, list] = {}
    for d in corrections.diffs(before, after):
        if d["field"].startswith("line.") and d["line"] is not None:
            corrected.setdefault(d["field"][5:], []).append((d["line"], d["new"]))

    # A field a lesson set, corrected back: the lesson is wrong for this bill.
    for used in meta.get("lessons_applied") or []:
        if used.get("kind") == "is" and used.get("field") in corrected and used.get("supplier_gstin") == gstin:
            row = _find(db, shop_id, gstin, norm(used.get("heading")), used["field"], "is",
                        used.get("layout_signature") or "")
            if row:
                row.times_undone += 1
                if row.times_undone >= RETIRE_AFTER:
                    row.active = False

    lines = (before or {}).get("line_items") or []
    layouts = meta.get("table_layouts") or []
    # A mixed-layout document needs per-row provenance before a correction
    # can safely teach a reusable rule. Old documents must be re-read first.
    signatures = {l.get("signature") for l in layouts if l.get("signature")}
    if len(signatures) != 1 or len(lines) != len((after or {}).get("line_items") or []):
        return []
    layout_signature = next(iter(signatures))
    if _gstin(before) != _gstin(after):
        return []
    # Reordered/replaced rows are not column corrections.
    for old, new in zip(lines, after.get("line_items") or []):
        if any(not corrections._same(corrections._value(old.get(k)), corrections._value(new.get(k)))
               for k in ("description", "product_code")):
            return []
    proposed = []
    for field, changes in corrected.items():
        if field not in LEARNABLE:
            continue
        votes: Counter = Counter()
        counted = 0
        for i, new in changes:
            if new is None or i >= len(lines):
                continue
            counted += 1
            line = lines[i]
            hits = set()
            for other, leaf in line.items():
                if other == field or other in ("rate_source", "extras") or not columns.get(other):
                    continue
                if corrections._same(corrections._value(leaf), new):
                    hits.add(columns[other])
            for extra in line.get("extras") or []:
                if isinstance(extra, dict) and extra.get("label") and corrections._same(
                        corrections._value(extra), new):
                    hits.add(str(extra["label"]))
            if len(hits) == 1:
                votes[hits.pop()] += 1
        source = columns.get(field)
        if votes:
            heading, n = votes.most_common(1)[0]
            if n > counted / 2 and n >= min(2, len(lines)) and norm(heading) != norm(source):
                proposed.append((heading, field, "is"))
                if source:
                    proposed.append((source, field, "not"))
        elif source and counted >= 2 and counted * 2 > len(lines):
            # Corrected on most lines, to figures the bill prints in no other
            # column: wherever it is, it is not the column we read.
            proposed.append((source, field, "not"))
    meanings = {}
    for heading, field, kind in proposed:
        if kind == "is":
            meanings.setdefault(norm(heading), set()).add(field)
    conflicted = {field for fields in meanings.values() if len(fields) > 1 for field in fields}
    learned: List[str] = []
    for heading, field, kind in proposed:
        if field in conflicted:
            continue
        _upsert(db, shop_id, gstin, heading, field, kind, user_id, layout_signature)
        learned.append(f"{field}{' not' if kind == 'not' else ''} <- {heading}")
    return learned


def record_outcome(db: Session, shop_id: str, meta: Optional[dict]) -> None:
    """Count each lesson a reading used, and each that had to be dropped."""
    for key, attr in (("lessons_applied", "times_used"), ("lessons_failed", "times_failed")):
        for used in (meta or {}).get(key) or []:
            gstin = used.get("supplier_gstin")
            if not gstin:
                continue
            row = _find(db, shop_id, gstin, norm(used.get("heading")), used.get("field"), used.get("kind"),
                        used.get("layout_signature") or "")
            if row is None:
                continue
            setattr(row, attr, (getattr(row, attr) or 0) + 1)
            if attr == "times_failed" and row.times_failed >= RETIRE_AFTER:
                row.active = False


def lessons_view(db: Session, shop_id: str, gstin: Optional[str] = None) -> List[dict]:
    """The lessons, as the Suppliers screen shows them."""
    query = db.query(SupplierColumnLesson).filter_by(shop_id=shop_id)
    if gstin:
        query = query.filter_by(supplier_gstin=gstin)
    return [{"id": r.id, "supplier_gstin": r.supplier_gstin, "heading": r.heading_printed or r.heading,
             "field": r.field, "kind": r.kind, "active": r.active, "times_used": r.times_used,
             "times_undone": r.times_undone, "times_failed": r.times_failed,
             "layout_signature": r.layout_signature}
            for r in query.order_by(SupplierColumnLesson.supplier_gstin, SupplierColumnLesson.field)]
