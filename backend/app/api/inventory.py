"""Inventory catalog API: import (CSV), list, match, and document reconciliation.

Optional feature: when a shop has an inventory catalog, extracted items are
matched against it (with a score) so data can update stock, not just be recorded.
"""
import csv
import io
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, require_owner
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.inventory import InventoryItem
from app.models.user import User
from app.models.shop import Shop
from app.schemas.inventory import ImportResult, InventoryItemOut, InventorySync, MatchQuery
from app.services.inventory.ingest import ingest_records, pull_from_api
from app.services.inventory.matching import (
    alternatives_for,
    match_document_items,
    match_name,
    normalize,
)

router = APIRouter()

# CSV header aliases -> our field. Import is forgiving about column names.
_ALIASES = {
    "name": {"name", "item", "item name", "itemname", "product", "medicine", "description", "stock item"},
    "composition": {"composition", "salt", "generic", "generic name", "molecule", "content", "contents"},
    "sku": {"sku", "code", "item code", "itemcode", "barcode"},
    "strength": {"strength", "dose", "dosage"},
    "pack": {"pack", "pack size", "packsize", "packing"},
    "mrp": {"mrp", "price", "rate", "mrp rs"},
    "stock_qty": {"stock", "qty", "quantity", "stock qty", "closing stock", "balance"},
}


def _resolve_headers(fieldnames):
    mapping = {}
    for col in fieldnames or []:
        key = col.strip().lower()
        for field, names in _ALIASES.items():
            if key in names:
                mapping[col] = field
                break
    return mapping


def _to_float(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


@router.post("/import", response_model=ImportResult)
async def import_inventory(
    file: UploadFile = File(...),
    replace: bool = Query(True, description="Replace the existing catalog"),
    db: Session = Depends(get_db),
    user: User = Depends(require_owner),
):
    """Import an inventory catalog from a CSV. Forgiving about column names."""
    if not (file.filename or "").lower().endswith(".csv") and file.content_type not in (
        "text/csv", "application/vnd.ms-excel", "application/octet-stream",
    ):
        raise HTTPException(status_code=400, detail="Please upload a .csv file.")

    raw = (await file.read()).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(raw))
    header_map = _resolve_headers(reader.fieldnames)
    if "name" not in header_map.values():
        raise HTTPException(status_code=400, detail="CSV must have a name/item/medicine column.")

    records = [{field: row.get(col) for col, field in header_map.items()} for row in reader]
    count = ingest_records(db, user.shop_id, records, replace=replace)
    db.add(AuditLog(shop_id=user.shop_id, actor_id=user.id, action="inventory.imported",
                    target=user.shop_id, detail={"count": count, "source": "csv"}))
    db.commit()
    return ImportResult(imported=count, replaced=replace)


@router.post("/sync", response_model=ImportResult)
def sync_inventory(
    body: InventorySync, db: Session = Depends(get_db), user: User = Depends(require_owner)
):
    """Pull the catalog from the shop's software / middleware REST endpoint and
    import it. The source config is saved so it can be re-synced later."""
    try:
        records = pull_from_api(
            url=body.url,
            auth_header=body.auth_header,
            items_path=body.items_path,
            mapping=body.mapping,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Sync failed: {exc}")

    count = ingest_records(db, user.shop_id, records, replace=True)

    # Persist source config (minus any secret header value) for re-sync.
    shop = db.get(Shop, user.shop_id)
    if shop is not None:
        settings = dict(shop.settings or {})
        settings["inventory_source"] = {
            "url": body.url,
            "items_path": body.items_path,
            "mapping": body.mapping,
            "has_auth": bool(body.auth_header),
        }
        shop.settings = settings
    db.add(AuditLog(shop_id=user.shop_id, actor_id=user.id, action="inventory.synced",
                    target=user.shop_id, detail={"count": count, "source": "api"}))
    db.commit()
    return ImportResult(imported=count, replaced=True)


@router.get("/source")
def inventory_source(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The saved API sync source config for this shop, if any."""
    shop = db.get(Shop, user.shop_id)
    return (shop.settings or {}).get("inventory_source") if shop else None


@router.get("/", response_model=list[InventoryItemOut])
def list_inventory(
    q: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(InventoryItem).filter(InventoryItem.shop_id == user.shop_id)
    if q:
        query = query.filter(InventoryItem.normalized_name.contains(normalize(q)))
    return query.order_by(InventoryItem.name).limit(limit).all()


@router.get("/count")
def inventory_count(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    n = db.query(InventoryItem).filter(InventoryItem.shop_id == user.shop_id).count()
    return {"count": n, "connected": n > 0}


@router.delete("/", status_code=204)
def clear_inventory(db: Session = Depends(get_db), user: User = Depends(require_owner)):
    db.query(InventoryItem).filter(InventoryItem.shop_id == user.shop_id).delete()
    db.commit()
    return None


@router.post("/match")
def match_single(
    body: MatchQuery, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """Match a single name against the shop's catalog (for manual lookups)."""
    items = db.query(InventoryItem).filter(InventoryItem.shop_id == user.shop_id).all()
    return {"candidates": [c.as_dict() for c in match_name(body.name, items, limit=body.limit)]}


@router.post("/alternatives")
def alternatives(
    body: MatchQuery, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """Substitutes in stock: other brands of the same molecule the shop carries.

    Helps the pharmacist offer an available alternative for a scanned medicine.
    """
    items = db.query(InventoryItem).filter(InventoryItem.shop_id == user.shop_id).all()
    return {"alternatives": [c.as_dict() for c in alternatives_for(body.name, items, limit=body.limit)]}


@router.get("/documents/{document_id}/match")
def match_document(
    document_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """Reconcile a document's extracted items against the inventory catalog.

    Returns per-item best matches + scores. If no catalog exists, `connected`
    is false and the app falls back to normal (unmatched) behavior.
    """
    doc = (
        db.query(Document)
        .filter(Document.id == document_id, Document.shop_id == user.shop_id)
        .first()
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    items = db.query(InventoryItem).filter(InventoryItem.shop_id == user.shop_id).all()
    if not items:
        return {"connected": False, "matched": 0, "total": 0, "items": []}

    summary = match_document_items(doc.payload or {}, items)
    summary["connected"] = True
    return summary
