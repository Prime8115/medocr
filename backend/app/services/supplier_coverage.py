"""How well we read each supplier - the list of who needs attention.

Every supplier prints its bill its own way, and a new one is where reading
breaks. This report shows it per supplier, from data we already keep:

* how many of its bills arrived VERIFIED - every check passed before anyone
  touched them (`meta.arrival`, recorded when the reading finishes);
* which fields reviewers had to correct, and how often (the audit log's
  "document.edited" entries list the fields changed);
* which fields its bills print that we did not read (the printed_not_read
  check on arrival);
* what we have learned for it - labels taught by corrections, and reviewer
  choices remembered.

A supplier whose bills keep needing the same correction is a layout the reader
should be taught properly, and this is where that shows first.
"""
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Optional

# A new supplier is judged after a few bills, not one.
_NEW_BELOW = 3
_GOOD_PCT = 80.0
_POOR_PCT = 50.0


def _value(fields: dict, path: str) -> str:
    section, _, key = path.partition(".")
    leaf = ((fields or {}).get(section) or {}).get(key)
    return str((leaf or {}).get("value") or "").strip() if isinstance(leaf, dict) else ""


def supplier_key(payload: Optional[dict]) -> Optional[tuple]:
    """(GSTIN or "", name) for an invoice's supplier, or None when neither was read."""
    fields = (payload or {}).get("fields") or {}
    gstin = _value(fields, "supplier.gstin").upper()
    name = _value(fields, "supplier.name")
    if len(gstin) == 15:
        return gstin, name
    return ("", name) if name else None


def arrival(meta: Optional[dict]) -> dict:
    """What the reading looked like before anyone touched it."""
    meta = meta or {}
    verification = meta.get("verification") or {}
    checks = verification.get("checks") or []
    failed = [c for c in checks if c.get("status") == "fail"]
    missed = [p for c in failed if c.get("id") == "printed_not_read" for p in c.get("fields") or []]
    return {
        "verdict": verification.get("verdict"),
        "failed": [c.get("id") for c in failed],
        "missed": missed,
        "gap_filled": [f.get("path") for f in meta.get("gap_filled") or []],
        "learned_filled": [f.get("path") for f in meta.get("learned_filled") or []],
        "pipeline": meta.get("pipeline"),
    }


def coverage(documents: Iterable, edits: Iterable, labels: Iterable, choices: Iterable) -> List[dict]:
    """One row per supplier, the ones needing attention first.

    `documents` are invoice Documents, `edits` "document.edited" AuditLog rows,
    `labels` SupplierLabel rows and `choices` SupplierChoice rows - all of one shop.
    """
    rows: Dict[str, dict] = {}
    by_doc: Dict[str, str] = {}
    edits = list(edits)
    edited_ids = {getattr(e, "target", None) for e in edits}
    for doc in documents:
        key = supplier_key(getattr(doc, "payload", None))
        if not key:
            continue
        gstin, name = key
        ident = gstin or f"name:{name.lower()}"
        by_doc[doc.id] = ident
        row = rows.setdefault(ident, {
            "supplier_gstin": gstin or None, "supplier_name": name, "documents": 0,
            "verified_on_arrival": 0, "edited_documents": 0, "last_seen": None,
            "_corrected": Counter(), "_missed": Counter(), "_failed": Counter(),
            "_pipelines": Counter(), "_gap": 0, "_learned_used": 0, "_edited": set(),
        })
        row["documents"] += 1
        created = getattr(doc, "created_at", None)
        if created and (row["last_seen"] is None or created.isoformat() > row["last_seen"]):
            row["last_seen"] = created.isoformat()
            row["supplier_name"] = name or row["supplier_name"]
        meta = (doc.payload or {}).get("meta") or {}
        first = meta.get("arrival")
        if not first:
            # Read before arrivals were recorded: its checks now are after any
            # edit, and a bill someone had to edit was not right on arrival.
            first = arrival(meta)
            if doc.id in edited_ids:
                first["verdict"] = "needs_check"
        if first.get("verdict") == "verified":
            row["verified_on_arrival"] += 1
        row["_missed"].update(first.get("missed") or [])
        row["_failed"].update(c for c in first.get("failed") or [] if not str(c).startswith("choice_"))
        row["_pipelines"][first.get("pipeline") or meta.get("pipeline") or "unknown"] += 1
        row["_gap"] += 1 if first.get("gap_filled") else 0
        row["_learned_used"] += 1 if first.get("learned_filled") else 0

    for edit in edits:
        ident = by_doc.get(getattr(edit, "target", None))
        if not ident:
            continue
        row = rows[ident]
        row["_edited"].add(edit.target)
        row["_corrected"].update((getattr(edit, "detail", None) or {}).get("changed") or [])

    learned = Counter(getattr(r, "supplier_gstin", None) for r in labels)
    remembered = Counter(getattr(r, "supplier_gstin", None) for r in choices)

    out = []
    for row in rows.values():
        docs = row["documents"]
        pct = round(100.0 * row["verified_on_arrival"] / docs, 1) if docs else None
        if docs < _NEW_BELOW:
            status = "new"
        elif pct is not None and pct >= _GOOD_PCT:
            status = "good"
        elif pct is not None and pct < _POOR_PCT:
            status = "needs_attention"
        else:
            status = "fair"
        out.append({
            "supplier_gstin": row["supplier_gstin"],
            "supplier_name": row["supplier_name"],
            "documents": docs,
            "verified_on_arrival": row["verified_on_arrival"],
            "verified_on_arrival_pct": pct,
            "edited_documents": len(row["_edited"]),
            "corrected_fields": [{"field": f, "count": n} for f, n in row["_corrected"].most_common(5)],
            "missed_fields": [{"field": f, "count": n} for f, n in row["_missed"].most_common(5)],
            "failed_checks": [{"check": c, "count": n} for c, n in row["_failed"].most_common(5)],
            "pipelines": dict(row["_pipelines"]),
            "gap_filled_documents": row["_gap"],
            "learned_used_documents": row["_learned_used"],
            "learned_labels": learned.get(row["supplier_gstin"], 0) if row["supplier_gstin"] else 0,
            "remembered_choices": remembered.get(row["supplier_gstin"], 0) if row["supplier_gstin"] else 0,
            "last_seen": row["last_seen"],
            "status": status,
        })
    order = {"needs_attention": 0, "fair": 1, "new": 2, "good": 3}
    out.sort(key=lambda r: (order[r["status"]], r["verified_on_arrival_pct"] if r["verified_on_arrival_pct"] is not None else 101, -r["documents"]))
    return out
