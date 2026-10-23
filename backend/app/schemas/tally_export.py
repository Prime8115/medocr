"""Manual Tally pilot: download is distinct from a reported import receipt."""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class TallyExportOut(BaseModel):
    id: str
    status: str
    revision: int
    download_count: int
    line_count: int
    total_amount: str
    external_reference: Optional[str] = None
    report_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    reported_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class TallyImportReport(BaseModel):
    outcome: Literal["imported", "not_imported"]
    external_reference: Optional[str] = Field(default=None, max_length=128)
    imported_line_count: Optional[int] = Field(default=None, ge=0)
    imported_total: Optional[str] = Field(default=None, max_length=32)
    rows_and_batches_checked: bool = False
    reason: Optional[str] = Field(default=None, max_length=500)
