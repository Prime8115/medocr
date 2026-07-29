from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class InventoryItemIn(BaseModel):
    name: str
    sku: Optional[str] = None
    strength: Optional[str] = None
    pack: Optional[str] = None
    mrp: Optional[float] = None
    stock_qty: Optional[float] = 0


class InventoryItemOut(BaseModel):
    id: str
    name: str
    composition: Optional[str] = None
    sku: Optional[str] = None
    strength: Optional[str] = None
    pack: Optional[str] = None
    mrp: Optional[float] = None
    stock_qty: Optional[float] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ImportResult(BaseModel):
    imported: int
    replaced: bool


class MatchQuery(BaseModel):
    name: str
    limit: int = 3
