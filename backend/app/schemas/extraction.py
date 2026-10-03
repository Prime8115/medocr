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

from pydantic import BaseModel, ValidationError, field_validator, model_validator

SCHEMA_VERSION = "1.0"
DOC_TYPES = ("prescription", "invoice")


def _as_text(value):
    """A value as the text the rest of the pipeline expects.

    The AI is asked for strings, but an invoice is mostly numbers and it does
    not always comply - one `100` or `60.7` among hundreds of fields used to
    fail validation and with it the WHOLE invoice. Numbers are written without
    float noise (1785.0 -> "1785"); booleans as "true"/"false", matching the
    free_supply convention."""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return ("%.6f" % value).rstrip("0").rstrip(".")
    return value  # anything else is genuinely malformed; let validation say so


class Field(BaseModel):
    value: Optional[str] = None
    confidence: Optional[float] = None

    @model_validator(mode="before")
    @classmethod
    def _bare_value(cls, data):
        # Without a schema enforced the AI may answer `"quantity": "100"`
        # rather than {"value": "100", "confidence": ...}. Keep the value; its
        # confidence is simply unknown.
        if data is None or isinstance(data, (str, int, float, bool)):
            return {"value": data}
        return data

    @field_validator("value", mode="before")
    @classmethod
    def _value_as_text(cls, value):
        return _as_text(value)


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
    """The party that issued the invoice - the vendor.

    Exported as the client's Customer* fields: on a purchase import this is the
    party master the shop's software needs, and it is the party whose drug
    licence numbers the invoice prints.
    """

    name: Field = Field()
    gstin: Field = Field()
    address: Field = Field()
    pan: Field = Field()
    email: Field = Field()
    # Pharma invoices print two or three drug licence numbers, sometimes with
    # their own validity dates.
    dl_no_1: Field = Field()
    dl_date_1: Field = Field()
    dl_no_2: Field = Field()
    dl_date_2: Field = Field()
    dl_no_3: Field = Field()
    dl_date_3: Field = Field()


class Party(BaseModel):
    """A Bill-to or Ship-to party - normally the buying pharmacy or its branch."""

    name: Field = Field()
    gstin: Field = Field()
    pan: Field = Field()
    address: Field = Field()


class InvoiceMeta(BaseModel):
    invoice_no: Field = Field()
    invoice_date: Field = Field()
    # The final payable amount printed at the foot of the bill.
    total_amount: Field = Field()
    due_date: Field = Field()

    # --- statutory / transport references a pharmacy has to keep ---
    irn: Field = Field()
    eway_bill_no: Field = Field()
    lr_no: Field = Field()
    lr_date: Field = Field()
    transport: Field = Field()
    po_no: Field = Field()
    po_date: Field = Field()

    # --- invoice-level money. Printed on some invoices; otherwise summed from
    # the lines, so the figures always reach the shop's accounts. ---
    total_taxable_amount: Field = Field()
    total_discount_amount: Field = Field()
    # Every tax head added together. Several invoices print only the heads, or
    # only the combined figure; the client's import wants both, so whichever is
    # absent is computed from the other.
    total_gst_amount: Field = Field()
    total_cgst_amount: Field = Field()
    total_sgst_amount: Field = Field()
    total_igst_amount: Field = Field()
    total_utgst_amount: Field = Field()


class ExtraField(BaseModel):
    """A column we have no name for, kept verbatim with its printed heading.

    Suppliers print columns we have never seen - a scheme percentage, a
    case/loose marker, a manufacturer code. Dropping them silently loses real
    information off the bill, so anything unrecognised lands here instead.
    """

    label: Optional[str] = None
    value: Optional[str] = None
    confidence: Optional[float] = None

    @field_validator("label", "value", mode="before")
    @classmethod
    def _as_text(cls, value):
        return _as_text(value)


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
    product_code: Field = Field()
    manufacturer: Field = Field()
    batch_no: Field = Field()
    expiry: Field = Field()
    mfg_date: Field = Field()
    pack: Field = Field()
    uom: Field = Field()
    quantity: Field = Field()
    free_quantity: Field = Field()
    total_quantity: Field = Field()
    mrp: Field = Field()
    ptr: Field = Field()
    pts: Field = Field()
    rate: Field = Field()
    rate_source: Field = Field()
    discount_percent: Field = Field()
    discount_amount: Field = Field()
    scheme_percent: Field = Field()
    amount: Field = Field()
    hsn: Field = Field()
    gst_percent: Field = Field()
    cgst_percent: Field = Field()
    cgst_amount: Field = Field()
    sgst_percent: Field = Field()
    sgst_amount: Field = Field()
    igst_percent: Field = Field()
    igst_amount: Field = Field()
    # True when the supplier billed this line at zero - a free or replacement
    # supply, which prints a blank amount rather than a missing one.
    free_supply: Field = Field()

    # --- the remaining money columns Indian pharma invoices print per line ---
    # gross before discount/tax, and net after - both are printed, and they are
    # not the same number as the taxable value.
    gross_amount: Field = Field()
    net_amount: Field = Field()
    # Scheme (free-goods offer): the percentage, its description, and its value.
    scheme: Field = Field()
    scheme_value: Field = Field()
    # Cash discount and window/wholesale-profit discount, each a % and an amount.
    cd_percent: Field = Field()
    cd_amount: Field = Field()
    wp_percent: Field = Field()
    wp_amount: Field = Field()
    # Union-territory GST, used in place of SGST in UT supplies.
    utgst_percent: Field = Field()
    utgst_amount: Field = Field()
    # Every column we did not recognise, kept with the supplier's own heading so
    # a layout we have never seen before is captured rather than discarded.
    extras: List[ExtraField] = []


class InvoiceFields(BaseModel):
    supplier: Supplier = Supplier()
    bill_to: Party = Party()
    ship_to: Party = Party()
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
    # The same sum with each line's GST added back - an Indian invoice's printed
    # grand total is tax-inclusive, so this is usually the number that matches.
    line_items_total_with_gst: Optional[str] = None
    total_reconciles: Optional[bool] = None
    # Which printed price column the bill turned out to be charged on
    # ("pts", "ptr", "rate", ...), decided from amount / quantity.
    billed_rate_column: Optional[str] = None
    # Lines the supplier billed at zero (free/replacement supply).
    free_supply_lines: int = 0


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
