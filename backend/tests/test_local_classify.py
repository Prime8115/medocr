"""Invoice vs prescription from the document's own text - no AI call spent."""
import pytest

import app.services.ocr as ocr
from app.services.ocr.base import OCRProvider
from app.services.ocr.classify import classify_text

# The opening of a real scanned invoice's text layer, garbling included: the
# scanner wrote "lnvoice", "Exprry" and "Slrips".
SCANNED_INVOICE = """lnvoice No. M-544 Delivery Note MSV LIFESCIENCES Chennai 600024
GSTIN/UlN: 27ABCDE1234F1Z0 State Name : Tamil Nadu, Code: 33
Rate per AmountHSN/SAC IVIRPI Quantltyqt Description Glimedose MP2 Tab 10's
Batch : OVT-25327 Exprry : 31-May-27 Tax lnvoice 30049099 80.00/Strips
46.40 Slrips 1,856.00 SGST/UTGST Rate Amount Taxable Value"""

PRESCRIPTION = """Dr. A. Kumar MBBS, MD  City Clinic  Reg. No 45123
Patient: R. Sharma   Age: 54   Sex: M
Diagnosis: Type 2 diabetes
Rx  Tab. Metformin 500 mg 1-0-1 after food x 30 days
Advice: follow-up after 1 month"""


def test_a_scanned_invoice_with_a_garbled_text_layer_is_an_invoice():
    assert classify_text(SCANNED_INVOICE) == "invoice"


def test_a_prescription_is_a_prescription():
    assert classify_text(PRESCRIPTION) == "prescription"


@pytest.mark.parametrize("text", ["", None, "   ", "Page 1 of 2", "lorem ipsum dolor sit amet " * 4])
def test_no_clear_answer_is_left_to_the_ai(text):
    assert classify_text(text) is None


def test_mixed_signals_are_left_to_the_ai():
    # A hospital pharmacy bill naming the patient carries both kinds of marker.
    mixed = "Tax Invoice GSTIN 27AAAAA0000A1Z5 Batch Exp HSN Patient: X  Hospital  Rx Diagnosis Age: 4"
    assert classify_text(mixed) is None


class CountingProvider(OCRProvider):
    name = "counting"

    def __init__(self):
        self.classify_calls = 0
        self.extract_types = []

    def classify(self, file_bytes, content_type):
        self.classify_calls += 1
        return "prescription"

    def extract(self, file_bytes, content_type, doc_type):
        self.extract_types.append(doc_type)
        return {}


def test_a_pdf_that_says_it_is_an_invoice_skips_the_ai_classify_call(monkeypatch):
    provider = CountingProvider()
    monkeypatch.setattr(ocr, "get_provider", lambda: provider)
    monkeypatch.setattr(ocr, "extract_text_sample", lambda _data, _pages=3: [SCANNED_INVOICE])
    monkeypatch.setattr(ocr, "is_digital_pdf", lambda _data: False)  # the scanned path
    monkeypatch.setattr(ocr, "page_count", lambda _data: 1)

    out = ocr.process_document("d", b"%PDF", "application/pdf", None)

    assert provider.classify_calls == 0
    assert provider.extract_types == ["invoice"]
    assert out["doc_type"] == "invoice"


def test_a_pdf_without_clear_text_still_asks_the_ai(monkeypatch):
    provider = CountingProvider()
    monkeypatch.setattr(ocr, "get_provider", lambda: provider)
    monkeypatch.setattr(ocr, "extract_text_sample", lambda _data, _pages=3: [""])
    monkeypatch.setattr(ocr, "is_digital_pdf", lambda _data: False)
    monkeypatch.setattr(ocr, "page_count", lambda _data: 1)

    ocr.process_document("d", b"%PDF", "application/pdf", None)

    assert provider.classify_calls == 1


# --- an unclear answer from the AI is never silently a prescription ---------

@pytest.mark.parametrize("answer, expected", [
    ("invoice", "invoice"),
    ("Invoice.", "invoice"),
    ("prescription", "prescription"),
    ("'prescription'", "prescription"),
    ("This is a purchase bill", "invoice"),
    ("", None),
    ("I cannot tell", None),
    ("prescription or invoice", None),
])
def test_the_ais_answer_is_read_strictly(answer, expected):
    from app.services.ocr.gemini import parse_classification

    assert parse_classification(answer) == expected


class UnsureProvider(CountingProvider):
    def classify(self, file_bytes, content_type):
        self.classify_calls += 1
        return None


def test_an_unclear_type_is_read_as_an_invoice_and_flagged(monkeypatch):
    provider = UnsureProvider()
    monkeypatch.setattr(ocr, "get_provider", lambda: provider)

    out = ocr.process_document("d", b"img", "image/jpeg", None)

    assert provider.extract_types == ["invoice"]
    assert out["doc_type"] == "invoice"
    assert out["meta"]["type_unsure"] is True
    assert out["meta"]["warnings"][0] == ocr.TYPE_UNSURE_WARNING


def test_a_clear_type_carries_no_warning(monkeypatch):
    provider = CountingProvider()
    monkeypatch.setattr(ocr, "get_provider", lambda: provider)

    out = ocr.process_document("d", b"img", "image/jpeg", None)

    assert out["doc_type"] == "prescription"
    assert not out["meta"].get("type_unsure")
    assert ocr.TYPE_UNSURE_WARNING not in out["meta"]["warnings"]
