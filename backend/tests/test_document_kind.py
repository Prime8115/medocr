"""A credit note, challan or quotation is never filed as a purchase invoice unseen."""
import pytest

from app.services.connectors.mapping import flatten_rows, render_tally_xml
from app.services.ocr import document_kind as dk
from app.services.ocr.verify import reverify, verify_invoice


@pytest.mark.parametrize("title,kind", [
    ("TAX INVOICE", "invoice"),
    ("Tax Invoice (Original for Recipient)", "invoice"),
    ("GST INVOICE", "invoice"),
    ("Bill of Supply", "invoice"),
    ("Tax Invoice cum Delivery Challan", "invoice"),
    ("CREDIT NOTE", "credit_note"),
    ("Credit Note (Duplicate)", "credit_note"),
    ("DEBIT NOTE", "debit_note"),
    ("Purchase Return", "return"),
    ("DELIVERY CHALLAN", "delivery_challan"),
    ("Proforma Invoice", "proforma"),
    ("QUOTATION", "quotation"),
    ("Purchase Order", "purchase_order"),
    ("Challan No", None),                   # a field label, not a title
    ("Credit 30 days", None),
    ("", None),
    (None, None),
])
def test_titles(title, kind):
    assert dk.kind_of_title(title) == kind


def test_a_heading_on_the_page_is_found():
    assert dk.kind_from_page("ABC PHARMA PVT LTD\nCREDIT NOTE\nGSTIN 27ABCDE1234F3ZY") == "credit_note"
    # In capitals, sharing the line with the supplier's name in a wide layout.
    assert dk.kind_from_page("ABC PHARMA PVT LTD CREDIT NOTE\nGSTIN") == "credit_note"


def test_words_in_a_sentence_or_a_label_are_not_a_title():
    footer = "ABC PHARMA\nTAX INVOICE\nNo credit note will be issued for expired goods"
    assert dk.kind_from_page(footer) == "invoice"
    label = "ABC PHARMA\nTAX INVOICE\nDELIVERY CHALLAN NO: 45\nCREDIT NOTE DATE: 01/02/2026"
    assert dk.kind_from_page(label) == "invoice"
    assert dk.kind_from_page("TAX INVOICE CUM DELIVERY CHALLAN") == "invoice"


def test_far_down_the_page_is_not_a_title():
    text = "\n".join(["line"] * 40 + ["CREDIT NOTE"])
    assert dk.kind_from_page(text) is None


def test_either_reader_noticing_a_credit_note_wins():
    assert dk.detect("Tax Invoice", "CREDIT NOTE") == "credit_note"
    assert dk.detect("Credit Note", "TAX INVOICE") == "credit_note"
    assert dk.detect("Tax Invoice", "") == "invoice"
    assert dk.detect(None, "") is None


def test_the_check_must_be_acknowledged():
    assert dk.check(None) is None
    assert dk.check("invoice")["status"] == "pass"
    fail = dk.check("credit_note")
    assert fail["status"] == "fail" and "credit note" in fail["message"]


def test_verification_and_reverify_carry_the_kind():
    fields = {"invoice": {"total_amount": {"value": "100.00"}}, "line_items": []}
    v = verify_invoice(fields, {"document_kind": "delivery_challan"})
    assert any(c["id"] == "document_kind" and c["status"] == "fail" for c in v["checks"])
    meta = reverify("invoice", {"fields": fields, "meta": {"document_kind": "delivery_challan"}}, fields)
    assert any(c["id"] == "document_kind" and c["status"] == "fail" for c in meta["verification"]["checks"])


def test_export_carries_the_kind():
    payload = {"doc_type": "invoice", "document_id": "d1", "meta": {"document_kind": "credit_note"},
               "data": {"supplier": {"name": {"value": "ABC"}}, "invoice": {"invoice_no": {"value": "CN-1"}},
                        "line_items": [{"description": {"value": "X"}, "amount": {"value": "10"}}]}}
    assert flatten_rows(payload)[0]["document_kind"] == "credit_note"
    assert "credit note, not a tax invoice" in render_tally_xml(payload, {})
    payload["meta"]["document_kind"] = "invoice"
    assert "NARRATION" not in render_tally_xml(payload, {})


@pytest.mark.parametrize("title,kind,status", [
    ("CREDIT NOTE", "credit_note", "fail"),
    ("TAX INVOICE", "invoice", "pass"),
])
def test_a_digital_document_end_to_end(title, kind, status):
    pytest.importorskip("reportlab")
    from app.services.ocr import process_document
    from tests.fixtures.invoice_pdf import build_invoice_pdf

    result = process_document("d", build_invoice_pdf(n_items=5, title=title), "application/pdf", "invoice")
    meta = result["meta"]
    assert meta["document_kind"] == kind
    check = next(c for c in meta["verification"]["checks"] if c["id"] == "document_kind")
    assert check["status"] == status
