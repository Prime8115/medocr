"""The shop's own GSTINs - so the buyer on a purchase bill is never guessed.

Every purchase invoice a pharmacy scans names two businesses, and one of them is
the pharmacy itself. Read from the page alone, telling them apart rests on how
each supplier lays out its bill - and suppliers print the two blocks side by
side, under any label or none, the pharmacy's sometimes on top. Get it wrong and
the purchase is filed under the shop's own GSTIN as the supplier, which corrupts
the purchase record and the input-tax credit claimed against it.

The shop knows who it is. Its GSTINs come from:

* its settings, where the owner can state them (`shop.settings["gstins"]`);
* the bills it has APPROVED - a reviewer confirmed the buyer on each, so a
  GSTIN that is the confirmed buyer on an approved bill is the shop's.

With them, the reader simply asks which business on the page is the shop
(ocr/parties.py). Without them - a brand-new shop - it falls back to reading
the page, exactly as before.
"""
from collections import Counter
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.shop import Shop
from app.services import lifecycle
from app.services.ocr.invoice_header import gstin_is_valid

# Enough approved bills to learn from; recent ones first.
_LEARN_FROM = 200


def own_gstins(db: Session, shop_id: str) -> List[str]:
    """The shop's GSTINs, stated first, then learned. Never raises."""
    try:
        out: List[str] = []
        shop = db.get(Shop, shop_id)
        for g in ((shop.settings or {}).get("gstins") or []) if shop else []:
            g = str(g).strip().upper()
            if gstin_is_valid(g) and g not in out:
                out.append(g)
        seen: Counter = Counter()
        docs = (db.query(Document)
                .filter(Document.shop_id == shop_id, Document.doc_type == "invoice",
                        Document.status.in_([lifecycle.APPROVED, lifecycle.PUSHED]))
                .order_by(Document.created_at.desc()).limit(_LEARN_FROM).all())
        for doc in docs:
            leaf = (((doc.payload or {}).get("fields") or {}).get("bill_to") or {}).get("gstin") or {}
            g = str(leaf.get("value") or "").strip().upper()
            if gstin_is_valid(g):
                seen[g] += 1
        out.extend(g for g, _ in seen.most_common() if g not in out)
        return out
    except Exception:  # noqa: BLE001 - knowing ourselves is a help, never a failure
        return []


def own_identity(db: Session, shop_id: str) -> Dict[str, Optional[str]]:
    """The shop's GSTINs, each with the name it goes by on its bills - the
    buyer name most often confirmed on its approved bills for that GSTIN, else
    the shop's registered name. Used to finish a buyer name a bill prints cut
    short ("EASTERN"). Never raises."""
    try:
        gstins = own_gstins(db, shop_id)
        if not gstins:
            return {}
        shop = db.get(Shop, shop_id)
        names: Dict[str, Counter] = {g: Counter() for g in gstins}
        docs = (db.query(Document)
                .filter(Document.shop_id == shop_id, Document.doc_type == "invoice",
                        Document.status.in_([lifecycle.APPROVED, lifecycle.PUSHED]))
                .order_by(Document.created_at.desc()).limit(_LEARN_FROM).all())
        for doc in docs:
            bill_to = ((doc.payload or {}).get("fields") or {}).get("bill_to") or {}
            g = str((bill_to.get("gstin") or {}).get("value") or "").strip().upper()
            name = str((bill_to.get("name") or {}).get("value") or "").strip()
            if g in names and name:
                names[g][name] += 1
        fallback = shop.name if shop else None
        return {g: (c.most_common(1)[0][0] if c else fallback) for g, c in names.items()}
    except Exception:  # noqa: BLE001
        return {}


def set_own_gstins(db: Session, shop_id: str, gstins: List[str]) -> List[str]:
    """Store the GSTINs the owner states. Only valid ones are kept."""
    clean: List[str] = []
    for g in gstins:
        g = str(g).strip().upper()
        if gstin_is_valid(g) and g not in clean:
            clean.append(g)
    shop = db.get(Shop, shop_id)
    if shop is not None:
        settings = dict(shop.settings or {})
        settings["gstins"] = clean
        shop.settings = settings
    return clean
