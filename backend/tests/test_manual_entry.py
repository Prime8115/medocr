"""When the AI cannot read a document, it reaches review for manual entry -
never a dead end.

These run with no Gemini key at all - the real "AI is unavailable" case, not a
mock of it - and check what the pharmacist actually gets.
"""
import io
import shutil

import pytest

from app.config import settings
from app.services.ocr import fallback
from app.services.ocr.fallback import MANUAL_ENTRY_WARNING
from tests.conftest import register_and_login, sample_image

needs_tesseract = pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")


@pytest.fixture(autouse=True)
def no_ai(monkeypatch):
    monkeypatch.setattr(settings, "allow_mock_ocr", False)
    monkeypatch.setattr(settings, "gemini_api_key", None)
    monkeypatch.setattr(settings, "gemini_api_keys", None)


def _upload(client, headers, data, name, ctype, doc_type=None):
    form = {"doc_type": doc_type} if doc_type else {}
    r = client.post("/v1/documents/", headers=headers, files={"file": (name, data, ctype)}, data=form)
    assert r.status_code == 200, r.text
    return client.get(f"/v1/documents/{r.json()['document_id']}", headers=headers).json()


def _v(fields, path):
    node = fields
    for key in path.split("."):
        node = (node or {}).get(key)
    return (node or {}).get("value") if isinstance(node, dict) else None


def _photo() -> bytes:
    """A photographed invoice, as a PNG (synthetic: the repository is public)."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1500, 900), "white")
    d = ImageDraw.Draw(img)
    big, font = ImageFont.load_default(size=42), ImageFont.load_default(size=30)
    d.text((40, 30), "SUNRISE PHARMA DISTRIBUTORS", font=big, fill="black")
    d.text((40, 100), "GSTIN: 27AABCS4321K1ZE", font=font, fill="black")
    d.text((40, 150), "TAX INVOICE   Invoice No: S-1029   Invoice Date: 12-09-2025", font=font, fill="black")
    d.text((40, 200), "Bill to: City Care Chemists   GSTIN: 27PQRST6789K1ZW", font=font, fill="black")
    d.text((40, 300), "Paracip 500 Tab   PC-2401   100   18.50   1850.00", font=font, fill="black")
    d.text((40, 420), "Grand Total: 6032.78", font=big, fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _text_pdf() -> bytes:
    """A PDF whose text layer reads like a scanner's - not a table the
    deterministic parser can use, so it would otherwise need the AI."""
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 800
    for line in [
        "lnvoice No.", "M-544", "MSV LIFESCIENCES", "GSTIN/UIN: 33ABEFM0315R1Z8",
        "Buyer", "Muthu Pharma", "GSTIN/UIN : 33AASCA3306L2ZK", "Dated", "5-Oct-25",
        "Glimedose MP2 Tab 10's Batch : OVT-25327 Expiry : 31-May-27",
        "Amount Chargeable (in words)", "INR Fifteen Thousand Thirty Four Only",
    ]:
        c.drawString(40, y, line)
        y -= 18
    c.save()
    return buf.getvalue()


def _manual(doc):
    assert doc["status"] == "needs_review", doc.get("error")
    meta = doc["payload"]["meta"]
    assert meta["needs_manual_entry"] is True
    assert meta["warnings"][0] == MANUAL_ENTRY_WARNING
    assert meta["pipeline"] == "manual_entry"
    assert doc["error"] is None
    return doc["payload"]["fields"], meta


def test_a_pdf_the_ai_cannot_read_is_filled_from_its_own_text(client):
    headers = register_and_login(client)
    fields, meta = _manual(_upload(client, headers, _text_pdf(), "msv.pdf", "application/pdf"))
    assert meta["text_source"] == "pdf_text"
    assert _v(fields, "invoice.invoice_no") == "M-544"
    assert _v(fields, "invoice.invoice_date") == "5-Oct-25"
    assert _v(fields, "supplier.gstin") == "33ABEFM0315R1Z8"
    assert _v(fields, "bill_to.gstin") == "33AASCA3306L2ZK"
    assert _v(fields, "supplier.pan") == "ABEFM0315R"
    assert float(_v(fields, "invoice.total_amount")) == 15034.0  # from the amount in words
    # A pattern match is a suggestion: every value is flagged for checking.
    assert fields["invoice"]["invoice_no"]["confidence"] < settings.low_confidence_threshold
    # Nothing invented: line items are for the pharmacist, the text is there to copy from.
    assert fields["line_items"] == []
    assert "Glimedose" in meta["raw_text"]


def test_an_unreadable_supplier_gstin_never_lets_the_buyers_take_its_place():
    from app.services.ocr.fallback import _party_gstins

    # The supplier's GSTIN garbled by the scanner; the buyer's readable.
    text = "SUNRISE PHARMA\nGSTIN: 27ABCDE1234F1Z0O\nBill to: City Care\nGSTIN: 27PQRST6789K1ZW"
    assert _party_gstins(text) == (None, "27PQRST6789K1ZW")
    # A misread check character is blanked in place, not shifted.
    text = "SUNRISE PHARMA\nGSTIN: 27ABCDE1234F1Z9\nBill to: City Care\nGSTIN: 27PQRST6789K1ZW"
    assert _party_gstins(text) == (None, "27PQRST6789K1ZW")
    # Both readable: each in its own place.
    text = "SUNRISE PHARMA\nGSTIN: 27ABCDE1234F1Z0\nBuyer\nGSTIN: 27PQRST6789K1ZW"
    assert _party_gstins(text) == ("27ABCDE1234F1Z0", "27PQRST6789K1ZW")


@needs_tesseract
def test_a_photo_the_ai_cannot_read_is_read_by_local_ocr(client):
    headers = register_and_login(client)
    fields, meta = _manual(_upload(client, headers, _photo(), "bill.png", "image/png"))
    assert meta["text_source"] == "ocr"
    assert _v(fields, "invoice.invoice_no") == "S-1029"
    assert _v(fields, "supplier.gstin") == "27AABCS4321K1ZE"
    assert _v(fields, "bill_to.gstin") == "27PQRST6789K1ZW"
    assert float(_v(fields, "invoice.total_amount")) == 6032.78
    assert "Paracip" in meta["raw_text"]


def test_a_file_that_is_not_a_document_is_refused_at_upload(client):
    # Not a dead end either: the user is told at once, in plain words.
    headers = register_and_login(client)
    r = client.post("/v1/documents/", headers=headers,
                    files={"file": ("x.jpg", b"not an image at all", "image/jpeg")}, data={"doc_type": "invoice"})
    assert r.status_code == 400
    assert "not supported" in r.json()["detail"]


def test_a_photo_with_nothing_legible_reaches_review_as_an_empty_form(client):
    headers = register_and_login(client)
    fields, meta = _manual(_upload(client, headers, sample_image(), "x.png", "image/png", "invoice"))
    assert _v(fields, "invoice.invoice_no") is None
    assert _v(fields, "supplier.gstin") is None


def test_the_users_choice_of_type_is_kept(client):
    headers = register_and_login(client)
    doc = _upload(client, headers, sample_image(), "x.png", "image/png", "prescription")
    _manual(doc)
    assert doc["doc_type"] == "prescription"


def test_the_reason_is_kept_for_diagnosis(client):
    headers = register_and_login(client)
    doc = _upload(client, headers, _text_pdf(), "msv.pdf", "application/pdf")
    diag = client.get(f"/v1/documents/{doc['id']}/diagnostics", headers=headers).json()
    assert diag["status"] == "needs_review"
    assert diag["pipeline"] == "manual_entry"


def test_a_scan_the_ai_stays_too_busy_for_goes_to_manual_entry(client, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from app.api import documents as documents_api
    from app.services.ocr import OCRError

    def busy(*_a, **_k):
        raise OCRError("AI service is busy. [429]", kind="busy")

    monkeypatch.setattr(documents_api, "process_document", busy)
    monkeypatch.setattr(settings, "ocr_busy_requeue_attempts", 1)
    headers = register_and_login(client)
    doc = _upload(client, headers, _text_pdf(), "msv.pdf", "application/pdf")
    assert doc["status"] == "queued"  # waiting first
    documents_api.run_due_jobs(now=datetime.now(timezone.utc) + timedelta(days=1))
    doc = client.get(f"/v1/documents/{doc['id']}", headers=headers).json()
    _manual(doc)


def test_if_the_fallback_itself_breaks_the_scan_fails_cleanly(client, monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("fallback exploded")

    monkeypatch.setattr(fallback, "fallback_payload", broken)
    headers = register_and_login(client)
    doc = _upload(client, headers, _text_pdf(), "msv.pdf", "application/pdf")
    assert doc["status"] == "failed"
    assert "exploded" not in (doc["error"] or "")


def test_the_pharmacist_can_complete_and_approve_it(client):
    headers = register_and_login(client)
    doc = _upload(client, headers, _text_pdf(), "msv.pdf", "application/pdf")
    fields = doc["payload"]["fields"]
    fields["line_items"] = [{
        "description": {"value": "Glimedose MP2 Tab 10's", "confidence": 1.0},
        "quantity": {"value": "100", "confidence": 1.0},
        "amount": {"value": "6070.00", "confidence": 1.0},
    }]
    r = client.patch(f"/v1/documents/{doc['id']}", headers=headers, json={"fields": fields})
    assert r.status_code == 200, r.text
    saved = r.json()["payload"]["fields"]["line_items"]
    assert saved[0]["description"]["value"] == "Glimedose MP2 Tab 10's"
    assert "hsn" in saved[0]  # a row added by hand gets every field, blank
    # Hand-entered lines are checked like read ones: one line of 6,070 against
    # a 15,034 bill must be confirmed before approval - then it goes through.
    refused = client.post(f"/v1/documents/{doc['id']}/approve", headers=headers)
    assert refused.status_code == 409
    open_ids = [c["id"] for c in refused.json()["detail"]["open_checks"]]
    assert "total_reconciles" in open_ids
    assert client.post(f"/v1/documents/{doc['id']}/approve", headers=headers,
                       json={"acknowledged": open_ids}).status_code == 200
