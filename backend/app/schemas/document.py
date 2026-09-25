from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class DocumentResponse(BaseModel):
    document_id: str
    status: str


class DocumentUpdate(BaseModel):
    """Human corrections to the extracted fields."""
    fields: dict


class DocumentReport(BaseModel):
    """A user telling us an extraction is wrong."""
    note: Optional[str] = None


class DocumentReportAck(BaseModel):
    document_id: str
    reported: bool = True
    message: str


class DocumentOut(BaseModel):
    id: str
    doc_type: str
    status: str
    overall_confidence: Optional[float] = None
    payload: Optional[Any] = None
    progress: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}
