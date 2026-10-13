"""From many shops' corrections to general rules: the weekly learning report.

    python scripts/learning_report.py [--days 7] [--min-suppliers 2]

Run from the backend directory against the production database (the app's
DATABASE_URL). Only shops whose owner turned on "share corrections" (PUT
/v1/auth/shop/share-corrections) are counted - the client's consent.

It groups every recorded correction (document_corrections) by field and by
the printed heading the reader took the value from, and lists the ones seen
across several suppliers first: those are a general rule waiting to be
written, e.g.

    line.quantity  from "Qty.Sale"   9 corrections, 4 suppliers, 2 shops
        e.g. 10 -> 12 (27AAA...), ...

A rule is added to the reader only together with the bills behind it in the
private invoice corpus, so the corpus gate tests it from then on. Single-
supplier patterns need nothing: that supplier's lesson already fixes them
(services/supplier_profile.py).
"""
import argparse
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def report(db, days: int = 7, min_suppliers: int = 2) -> list:
    from app.models.document_correction import DocumentCorrection
    from app.models.shop import Shop

    sharing = {s.id for s in db.query(Shop).all() if (s.settings or {}).get("share_corrections")}
    since = datetime.now(timezone.utc) - timedelta(days=days)
    groups: dict = defaultdict(lambda: {"count": 0, "suppliers": set(), "shops": set(), "examples": []})
    for row in db.query(DocumentCorrection).filter(DocumentCorrection.created_at >= since):
        if row.shop_id not in sharing:
            continue
        key = (row.field, row.source or "")
        g = groups[key]
        g["count"] += 1
        g["suppliers"].add(row.supplier_gstin or "?")
        g["shops"].add(row.shop_id)
        if len(g["examples"]) < 3:
            g["examples"].append(f"{row.value_read} -> {row.value_corrected} ({row.supplier_gstin or '?'})")
    rows = [{"field": f, "heading": h, "corrections": g["count"], "suppliers": len(g["suppliers"]),
             "shops": len(g["shops"]), "examples": g["examples"]}
            for (f, h), g in groups.items()]
    rows.sort(key=lambda r: (r["suppliers"] >= min_suppliers, r["suppliers"], r["corrections"]), reverse=True)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--min-suppliers", type=int, default=2)
    args = parser.parse_args()
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        rows = report(db, args.days, args.min_suppliers)
    finally:
        db.close()
    if not rows:
        print("No shared corrections in this period.")
        return
    print(f"Corrections in the last {args.days} days (shops sharing them only):\n")
    for r in rows:
        mark = "RULE?" if r["suppliers"] >= args.min_suppliers else "     "
        heading = f'from "{r["heading"]}"' if r["heading"] else ""
        print(f'{mark} {r["field"]:24} {heading:30} {r["corrections"]} corrections, '
              f'{r["suppliers"]} suppliers, {r["shops"]} shops')
        for e in r["examples"]:
            print(f"        e.g. {e}")


if __name__ == "__main__":
    main()
