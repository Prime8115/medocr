"""Versioned structured-extraction schemas for both document types.

The OCR pipeline emits, and the API validates, this shape:

    {
      "schema_version": "1.0",
      "doc_type": "prescription" | "invoice",
      "fields": { ...type-specific... },
      "meta": { "overall_confidence", "language", "pipeline",
                "processed_at", "warnings": [ ... ] }
    }

Each leaf field is a `{ "value": <str|null>, "confidence": <float|null> }` pair so
the UI can highlight low-confidence extractions for human review.
"""
from typing import List, Literal, Optional

from pydantic import BaseModel, ValidationError

SCHEMA_VERSION = "1.0"
DOC_TYPES = ("prescription", "invoice")


class Field(BaseModel):
    value: Optional[str] = None
    confidence: Optional[float] = None


# ----------------------------- Prescription -----------------------------
class Patient(BaseModel):
    name: Field = Field()
    age: Field = Field()
    gender: Field = Field()


class Prescriber(BaseModel):
    name: Field = Field()
    registration_no: Field = Field()


class Medication(BaseModel):
    name: Field = Field()
    strength: Field = Field()
    form: Field = Field()
    frequency: Field = Field()
    duration: Field = Field()
    instructions: Field = Field()


class PrescriptionFields(BaseModel):
    patient: Patient = Patient()
    prescriber: Prescriber = Prescriber()
    medications: List[Medication] = []


# ------------------------------- Invoice --------------------------------
class Supplier(BaseModel):
    name: Field = Field()
    gstin: Field = Field()
    address: Field = Field()


class InvoiceMeta(BaseModel):
    invoice_no: Field = Field()
    invoice_date: Field = Field()
    total_amount: Field = Field()


class InvoiceLineItem(BaseModel):
    """One purchase line.

    Indian distributor invoices carry several DIFFERENT prices per line and they
    are not interchangeable, so each gets its own field:

      mrp   - Maximum Retail Price (what the customer pays)
      ptr   - Price To Retailer    (what this pharmacy pays, per unit)
      pts   - Price To Stockist    (what the stockist pays, per unit)
      rate  - the rate the line was actually BILLED at

    `rate` is always populated (it is the connector contract) but `rate_source`
    records which column it came from - the supplier's own header text, e.g.
    "RATE", "PTR" or "PTS" - so the pharmacist can see what the number means
    instead of guessing.
    """

    description: Field = Field()
    batch_no: Field = Field()
    expiry: Field = Field()
    pack: Field = Field()
    quantity: Field = Field()
    free_quantity: Field = Field()
    mrp: Field = Field()
    ptr: Field = Field()
    pts: Field = Field()
    rate: Field = Field()
    rate_source: Field = Field()
    discount_percent: Field = Field()
    amount: Field = Field()
    hsn: Field = Field()
    gst_percent: Field = Field()


class InvoiceFields(BaseModel):
    supplier: Supplier = Supplier()
    invoice: InvoiceMeta = InvoiceMeta()
    line_items: List[InvoiceLineItem] = []


FIELDS_MODEL = {
    "prescription": PrescriptionFields,
    "invoice": InvoiceFields,
}


class ExtractionMeta(BaseModel):
    overall_confidence: Optional[float] = None
    language: Optional[str] = None
    pipeline: Optional[str] = None
    processed_at: Optional[float] = None
    warnings: List[str] = []
    # --- invoice integrity (see services/ocr/invoice_checks.py) ---
    # How many printed copies of the same invoice were found in the PDF
    # (GST invoices are commonly printed Original/Duplicate/Triplicate).
    copies_detected: Optional[int] = None
    # Identical line rows collapsed after extraction.
    duplicates_removed: int = 0
    # Item count printed on the invoice itself, when it states one.
    stated_item_count: Optional[int] = None
    # Sum of the line-item amounts, and whether it matches the printed total.
    line_items_total: Optional[str] = None
    total_reconciles: Optional[bool] = None


class ExtractionPayload(BaseModel):
    schema_version: str = SCHEMA_VERSION
    doc_type: Literal["prescription", "invoice"]
    fields: dict
    meta: ExtractionMeta = ExtractionMeta()


def validate_fields(doc_type: str, fields: dict) -> dict:
    """Validate & normalize a `fields` dict for a doc type. Raises ValueError on failure."""
    model = FIELDS_MODEL.get(doc_type)
    if model is None:
        raise ValueError(f"Unknown doc_type: {doc_type!r}")
    try:
        return model(**(fields or {})).model_dump()
    except ValidationError as exc:
        raise ValueError(f"Invalid {doc_type} fields: {exc}") from exc


def _walk_fields(obj, path=""):
    """Yield (path, value, confidence) for every leaf Field in a fields dict."""
    if isinstance(obj, dict):
        if "value" in obj and "confidence" in obj:
            yield path, obj.get("value"), obj.get("confidence")
        else:
            for k, v in obj.items():
                yield from _walk_fields(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            yield from _walk_fields(item, f"{path}[{i}]")


def collect_low_confidence(fields: dict, threshold: float) -> List[str]:
    """Return dotted paths of populated fields whose confidence is below threshold."""
    flagged = []
    for path, value, conf in _walk_fields(fields):
        if value not in (None, "") and conf is not None and conf < threshold:
            flagged.append(path)
    return flagged
