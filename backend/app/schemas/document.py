from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel


class DocumentResponse(BaseModel):
    document_id: str
    status: str
    # The same file was uploaded before: document_id is that earlier scan.
    duplicate: bool = False
    # Every document this upload made - more than one when a PDF held
    # several invoices (document_id is the first).
    document_ids: List[str] = []
    # A short note for the user about what happened to their file, if anything.
    message: Optional[str] = None


class DocumentUpdate(BaseModel):
    """Human corrections to the extracted fields."""
    fields: dict


class ApproveRequest(BaseModel):
    """Approval, with the failed checks the reviewer has checked against the paper.

    Every check that failed must be listed here by id, or approval is refused
    with the open checks named - see services/ocr/verify.py.
    """
    acknowledged: list[str] = []


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
