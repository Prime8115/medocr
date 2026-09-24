"""End-to-end: a real digital invoice PDF through the deterministic Tier-1 path.

These are the regression tests for the four defects reported from the field on
24 Sep 2026 against a Zydus invoice:

  1. "Final amount not display"
  2. "Same item display 3 times"
  3. "Zydus pdf 143 item but display 429"
  4. "Rate field available but in rate field display PTR"

Everything here runs on a PDF built by tests/fixtures/invoice_pdf.py, so the
table really has to be found, the columns really have to be mapped, and the
totals really have to be read off the page.
"""
import pytest

from app.services.ocr import process_document
from app.services.ocr.invoice_parser import parse_invoice_pdf
from app.services.ocr.pdf_utils import is_digital_pdf

from tests.fixtures.invoice_pdf import build_invoice_pdf, rows_total

pytest.importorskip("reportlab", reason="reportlab builds the invoice PDF fixtures")


def _run(**kwargs):
    data = build_invoice_pdf(**kwargs)
    assert is_digital_pdf(data), "fixture must take the digital (Tier-1) path"
    return process_document("doc-test", data, "application/pdf", doc_type="invoice")


# --------------------------- complaint 3 + 2: 143 -> 429 ---------------------------
def test_triplicate_invoice_is_read_once_not_three_times():
    """One invoice printed Original/Duplicate/Triplicate must yield ONE item list."""
    result = _run(n_items=40, copies=3)
    meta = result["meta"]

    assert meta["pipeline"] == "pdf_parser"
    assert meta["copies_detected"] == 3
    assert meta["item_count"] == 40          # not 120
    assert len(result["fields"]["line_items"]) == 40

    descriptions = [i["description"]["value"] for i in result["fields"]["line_items"]]
    assert len(set(descriptions)) == 40      # every row distinct


def test_repeated_copies_without_markers_are_caught_by_dedupe():
    """Harder case: the copies carry no Original/Duplicate wording at all."""
    result = _run(n_items=30, copies=3, label_copies=False, stated_count=False)
    meta = result["meta"]

    assert meta["item_count"] == 30
    assert meta["duplicates_removed"] == 60
    assert any("repeated line" in w for w in meta["warnings"])


def test_single_copy_invoice_is_untouched():
    result = _run(n_items=25, copies=1)
    assert result["meta"]["item_count"] == 25
    assert result["meta"]["duplicates_removed"] == 0
    assert result["meta"]["copies_detected"] == 1


# ------------------------------ complaint 1: the total ------------------------------
def test_invoice_total_is_extracted_and_reconciles():
    """'Final amount not display' - the total was hardcoded to null."""
    result = _run(n_items=40, copies=3)

    total = result["fields"]["invoice"]["total_amount"]["value"]
    assert total is not None
    assert float(total) == rows_total(40)

    meta = result["meta"]
    assert meta["line_items_total"] == f"{rows_total(40):.2f}"
    assert meta["total_reconciles"] is True
    assert meta["warnings"] == []            # a clean invoice warns about nothing


def test_every_line_carries_its_own_amount():
    result = _run(n_items=12)
    for item in result["fields"]["line_items"]:
        assert item["amount"]["value"], "each line must have its net value"


def test_duplicated_invoice_would_fail_reconciliation():
    """The safety net: if de-duplication ever regresses, the sum stops matching
    the printed total and the pharmacist is warned instead of misled."""
    from app.services.ocr.invoice_checks import reconcile_invoice

    parsed = parse_invoice_pdf(build_invoice_pdf(n_items=20, copies=1))
    parsed.pop("_hints", None)
    parsed["line_items"] = parsed["line_items"] * 3      # simulate the regression

    report = reconcile_invoice(parsed)
    assert report["total_reconciles"] is False
    assert any("Please check the items" in w for w in report["warnings"])


# --------------------------- complaints 4 + 5: which rate? ---------------------------
def test_rate_falls_back_to_ptr_and_labels_it():
    """Zydus has no RATE column. We still fill `rate`, but we say it is PTR."""
    result = _run(n_items=10, copies=1)
    item = result["fields"]["line_items"][0]

    assert item["ptr"]["value"] is not None
    assert item["rate"]["value"] == item["ptr"]["value"]
    assert item["rate_source"]["value"] == "PTR"
    assert item["mrp"]["value"] != item["ptr"]["value"]


def test_explicit_rate_column_beats_ptr():
    """'Rate field available but in rate field display PTR' - the actual bug."""
    result = _run(n_items=10, copies=1, with_rate=True)
    item = result["fields"]["line_items"][0]

    assert item["rate_source"]["value"] == "RATE"
    assert item["rate"]["value"] != item["ptr"]["value"]
    assert float(item["rate"]["value"]) < float(item["ptr"]["value"])
    assert float(item["ptr"]["value"]) < float(item["mrp"]["value"])


def test_rate_source_does_not_dilute_confidence():
    result = _run(n_items=10, copies=1)
    item = result["fields"]["line_items"][0]
    assert item["rate_source"]["confidence"] is None
    assert result["meta"]["overall_confidence"] == 1.0


# ------------------------------ quantity vs free quantity ------------------------------
def test_free_quantity_is_separated_from_billed_quantity():
    """Scheme goods (10+2) must not inflate the billed quantity."""
    result = _run(n_items=12, copies=1)
    items = result["fields"]["line_items"]

    # The fixture gives every third row 2 free units.
    assert items[0]["free_quantity"]["value"] == "2"
    assert items[0]["quantity"]["value"] == "10"
    assert items[1]["free_quantity"]["value"] is None


def test_line_arithmetic_holds_for_a_clean_invoice():
    result = _run(n_items=20, copies=1)
    assert not any("does not match the amount" in w for w in result["meta"]["warnings"])
    for item in result["fields"]["line_items"]:
        qty = float(item["quantity"]["value"])
        rate = float(item["rate"]["value"])
        amount = float(item["amount"]["value"])
        assert abs(qty * rate - amount) < 0.05


# ---------------------------------- other metadata ----------------------------------
def test_supplier_and_invoice_header_are_read():
    fields = _run(n_items=10, copies=1)["fields"]
    assert fields["supplier"]["name"]["value"] == "Zydus Healthcare Limited"
    assert fields["supplier"]["gstin"]["value"] == "27AAACG1895Q1ZY"
    assert fields["invoice"]["invoice_no"]["value"] == "2299707688"


def test_stated_item_count_is_recorded_and_matches():
    result = _run(n_items=18, copies=1)
    assert result["meta"]["stated_item_count"] == 18
    assert not any("states" in w and "were read" in w for w in result["meta"]["warnings"])


def test_pack_and_hsn_are_captured():
    item = _run(n_items=10, copies=1)["fields"]["line_items"][0]
    assert item["pack"]["value"] == "10 X 10"
    assert item["hsn"]["value"] == "30049039"
    assert item["gst_percent"]["value"] == "12.0"      # CGST 6 + SGST 6
