"""Inventory ingestion — the shared core for every population method.

A shop can fill its catalog several ways, all funnelling through `ingest_records`:
  - CSV upload            (api/inventory.py: /import)
  - API pull from their software / middleware   (/sync, pull_from_api)
  - Desktop agent push    (future: agent posts records)
  - Manual entry          (future)

Records are plain dicts with any of: name, composition, sku, strength, pack,
mrp, stock_qty. Only `name` is required.
"""
from typing import Callable, List, Optional

from sqlalchemy.orm import Session

from app.models.inventory import InventoryItem
from app.services.inventory.matching import normalize


def _to_float(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, AttributeError, TypeError):
        return None


def ingest_records(db: Session, shop_id: str, records: List[dict], replace: bool = True) -> int:
    """Upsert-by-replace a set of catalog records for a shop. Returns count imported."""
    if replace:
        db.query(InventoryItem).filter(InventoryItem.shop_id == shop_id).delete()

    count = 0
    for rec in records:
        name = (str(rec.get("name") or "")).strip()
        if not name:
            continue
        db.add(InventoryItem(
            shop_id=shop_id,
            name=name,
            normalized_name=normalize(name),
            composition=(rec.get("composition") or None),
            sku=(rec.get("sku") or None),
            strength=(rec.get("strength") or None),
            pack=(rec.get("pack") or None),
            mrp=_to_float(rec.get("mrp")),
            stock_qty=_to_float(rec.get("stock_qty")) or 0,
        ))
        count += 1
    return count


# Common source-key aliases -> our field, for API payloads with varied schemas.
_KEY_ALIASES = {
    "name": ("name", "item", "item_name", "itemName", "product", "medicine", "description", "stock_item"),
    "composition": ("composition", "salt", "generic", "generic_name", "molecule", "content", "contents"),
    "sku": ("sku", "code", "item_code", "itemCode", "barcode", "id"),
    "strength": ("strength", "dose", "dosage"),
    "pack": ("pack", "pack_size", "packSize", "packing"),
    "mrp": ("mrp", "price", "rate", "MRP"),
    "stock_qty": ("stock", "qty", "quantity", "stock_qty", "closing_stock", "balance", "onHand"),
}


def _map_record(raw: dict, mapping: Optional[dict]) -> dict:
    """Map a source record to our fields. Explicit `mapping` (our_field->their_key)
    wins; otherwise try common aliases."""
    out = {}
    if mapping:
        for field, key in mapping.items():
            if key in raw:
                out[field] = raw[key]
        if out.get("name"):
            return out
    # Fallback: alias-based auto-mapping.
    lower = {str(k).lower(): v for k, v in raw.items()}
    for field, keys in _KEY_ALIASES.items():
        for k in keys:
            if k.lower() in lower and lower[k.lower()] not in (None, ""):
                out[field] = lower[k.lower()]
                break
    return out


def _dig(obj, path: Optional[str]):
    """Follow a dotted path to the list of items in a JSON response."""
    if not path:
        return obj
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def pull_from_api(
    url: str,
    auth_header: Optional[str] = None,
    items_path: Optional[str] = None,
    mapping: Optional[dict] = None,
    http_get: Optional[Callable] = None,
    timeout: float = 20.0,
) -> List[dict]:
    """Fetch a catalog from a REST endpoint and return mapped records.

    The endpoint should return JSON: either a top-level array, or an object with
    the array at `items_path` (e.g. "data.items"). `http_get` is injectable for
    tests.
    """
    import requests

    getter = http_get or requests.get
    headers = {"Authorization": auth_header} if auth_header else {}
    resp = getter(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    body = resp.json()

    raw_items = _dig(body, items_path)
    if not isinstance(raw_items, list):
        raise ValueError("Inventory source did not return a list of items.")

    return [_map_record(r, mapping) for r in raw_items if isinstance(r, dict)]
