"""Against the REAL Gemini API - run nightly by .github/workflows/live-ai.yml.

Every unit test fakes Gemini, so none of them could have seen what broke the
MSV Lifesciences invoice in production: Gemini refusing the invoice request
outright with "400 INVALID_ARGUMENT". These send real requests through the real
provider and fail when Gemini stops reading our documents - a model update, a
schema it now refuses, a key or quota problem - before a pharmacist finds out.

The documents are synthetic: this repository is public, so no customer's
invoice may be committed. They are built to exercise the same paths the real
ones take - a scanner's garbled text layer, a photographed bill, a
prescription.

Skipped unless RUN_LIVE_AI=1 and a Gemini key is configured.
"""
import io
import os

import pytest

from app.config import settings
from app.services.ocr.base import OCRError

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_AI") != "1" or not settings.gemini_keys_list,
    reason="live Gemini tests run only with RUN_LIVE_AI=1 and a GEMINI_API_KEY",
)

# Like the MSV invoice's text layer: the scanner's own OCR, letters confused
# ("lnvoice", "Exprry", "Slrips"), columns scattered. Names, GSTINs and figures
# are invented.
SCANNED_INVOICE_TEXT = """lnvoice No.
S-1029
SUNRISE PHARMA DISTRIBUTORS
12 Market Road, Pune 411001
GSTIN/UlN: 27ABCDE1234F1Z5
State Name : Maharashtra, Code: 27
Buyer
City Care Chemists
GSTIN/UIN : 27PQRST6789K1Z2
Description HSN/SAC Quantltyqt Rate per Amount
Paracip 500 Tab 10's
Batch : PC-2401
Exprry : 31-Mar-27
Azithral 250 Tab 6's
Batch ; AZ-7781
Expiry : 30-Nov-26
Pantocid 40 Tab 15's
Batch : PN-3310
Exprry : 31-Jan-27
30049099 25.00/Strips
30042019 98.00/Strips
30049039 112.00/Strips
100 Slrips 18.50 1,850.00
20 Strips 71.40 1,428.00
30 Strips 82.25 2,467.50
Tax lnvoice Dated 12-Sep-25
CGST 2.5% 143.64 SGST 2.5% 143.64
Total 6,032.78
"""

PRESCRIPTION_TEXT = """Dr. A. Mehta MBBS, MD (Medicine)  Reg. No. MMC-45123
City Clinic, Pune
Patient: R. Sharma   Age: 54   Sex: M
Rx
1. Tab Metformin 500 mg  1-0-1  after food  x 30 days
2. Tab Atorvastatin 10 mg  0-0-1  x 30 days
Advice: follow-up after 1 month
"""

_INVOICE_LINES = [
    ("Paracip 500 Tab 10's", "PC-2401", "03/27", "100", "18.50", "1850.00"),
    ("Azithral 250 Tab 6's", "AZ-7781", "11/26", "20", "71.40", "1428.00"),
    ("Pantocid 40 Tab 15's", "PN-3310", "01/27", "30", "82.25", "2467.50"),
]


def _photographed_invoice() -> bytes:
    """A plain invoice drawn as a PNG - the photographed-bill (vision) path."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1400, 900), "white")
    draw = ImageDraw.Draw(img)
    big, font = ImageFont.load_default(size=40), ImageFont.load_default(size=26)
    draw.text((40, 30), "SUNRISE PHARMA DISTRIBUTORS", font=big, fill="black")
    draw.text((40, 90), "GSTIN: 27ABCDE1234F1Z5    TAX INVOICE  No. S-1029   Date 12-09-2025", font=font, fill="black")
    draw.text((40, 130), "Bill to: City Care Chemists   GSTIN: 27PQRST6789K1Z2", font=font, fill="black")
    y = 200
    for col, x in zip(("Product", "Batch", "Exp", "Qty", "Rate", "Amount"), (40, 520, 720, 860, 1000, 1180)):
        draw.text((x, y), col, font=font, fill="black")
    for line in _INVOICE_LINES:
        y += 50
        for val, x in zip(line, (40, 520, 720, 860, 1000, 1180)):
            draw.text((x, y), val, font=font, fill="black")
    draw.text((900, y + 90), "Total: 6032.78", font=big, fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _provider():
    from app.services.ocr.gemini import GeminiProvider

    return GeminiProvider()


def _call(fn, *args):
    """Run a live call; a busy or rate-limited Gemini skips rather than fails,
    so the nightly is red only when something is actually broken."""
    try:
        return fn(*args)
    except OCRError as exc:
        if exc.kind == "busy":
            pytest.skip(f"Gemini busy or rate-limited tonight: {exc}")
        raise


def _values(items, key):
    return [((it.get(key) or {}).get("value") or "") for it in items]


def test_scanned_invoice_text_is_read_as_an_invoice():
    """The exact path the MSV invoice takes: garbled text layer -> invoice extraction."""
    from app.schemas.extraction import validate_fields

    raw = _call(_provider().extract, SCANNED_INVOICE_TEXT.encode(), "text/plain", "invoice")
    fields = validate_fields("invoice", raw)
    items = fields["line_items"]
    assert len(items) == 3, f"expected 3 line items, got {len(items)}: {_values(items, 'description')}"
    batches = " ".join(_values(items, "batch_no"))
    for batch in ("PC-2401", "AZ-7781", "PN-3310"):
        assert batch in batches, f"batch {batch} missing from {batches!r}"
    assert "S-1029" in (fields["invoice"]["invoice_no"]["value"] or "")


def test_photographed_invoice_goes_through_the_whole_pipeline():
    """Upload-shaped: an image, type on Auto, through process_document."""
    from app.services.ocr import process_document

    out = _call(process_document, "live-test", _photographed_invoice(), "image/png", None)
    assert out["doc_type"] == "invoice"
    items = out["fields"]["line_items"]
    assert len(items) == 3, f"expected 3 line items, got {len(items)}"
    assert "1850" in " ".join(_values(items, "amount")).replace(",", "")


def test_prescription_is_read():
    from app.schemas.extraction import validate_fields

    raw = _call(_provider().extract, PRESCRIPTION_TEXT.encode(), "text/plain", "prescription")
    fields = validate_fields("prescription", raw)
    names = " ".join(_values(fields["medications"], "name")).lower()
    assert "metformin" in names and "atorvastatin" in names


@pytest.mark.parametrize("doc_type", ["invoice", "prescription"])
@pytest.mark.xfail(
    reason="Production showed Gemini refusing the invoice schema (400 INVALID_ARGUMENT); "
           "extraction succeeds through the schema-free fallback. Remove this marker once "
           "the invoice schema is accepted - an XPASS here means it now is.",
    strict=False,
)
def test_response_schema_is_accepted_without_fallback(doc_type):
    """The response schema keeps the AI's answer well-formed. A fallback means
    Gemini refused it - extraction still works, but less reliably."""
    provider = _provider()
    text = SCANNED_INVOICE_TEXT if doc_type == "invoice" else PRESCRIPTION_TEXT
    _call(provider.extract, text.encode(), "text/plain", doc_type)
    assert provider.schema_fallbacks == 0, f"Gemini refused the {doc_type} response schema"
