from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class DocumentResponse(BaseModel):
    document_id: str
    status: str


class DocumentUpdate(BaseModel):
    """Human corrections to the extracted fields."""
    fields: dict


class DocumentOut(BaseModel):
    id: str
    doc_type: str
    status: str
    overall_confidence: Optional[float] = None
    payload: Optional[Any] = None
    error: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}
