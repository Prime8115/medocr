"""Supplier layouts that broke the deterministic reader, pinned without the PDFs.

The real invoices behind these (Menarini, Abbott, Overseas, V L Enterprises) are
customer documents and never committed. Each test below rebuilds the one shape
that went wrong, so CI guards it on every push.
"""
from app.services.ocr import _parse_is_trustworthy_enough
from app.services.ocr.amount_words import total_from_words
from app.services.ocr.invoice_header import (
    extract_references,
    party_details,
    supplier_gstin_for_pan,
)
from app.services.ocr.invoice_parser import (
    _better_reading,
    _build_item,
    _complete_legal_suffix,
    _extract_total,
    _gst_columns,
    _map_columns,
    _merge_stacked_header,
)
from app.services.ocr.pdf_table import tables_from_words


def _leaf(value):
    return {"value": value, "confidence": 1.0}


def _v(item, key):
    return (item.get(key) or {}).get("value")


# --- totals --------------------------------------------------------------------

def test_a_word_between_the_total_label_and_its_figure_is_allowed():
    # Menarini: with "Amt" in the way the label missed, and the bill's TAXABLE
    # total was read as the amount due instead.
    text = "Net Amount 46870.98\nTotal Invoice : 54,057.56\nNet Payable Amt : 54,058.00"
    assert _extract_total(text) == "54058.00"


def test_a_hyphen_inside_the_number_words_is_not_the_label_separator():
    # The old pattern took the hyphen in FIFTY-FOUR for a separator: 8 rupees.
    text = "RUPEES FIFTY-FOUR THOUSAND FIFTY-EIGHT ONLY Total :- 46870.98"
    assert total_from_words(text) == "54058.00"


def test_a_spelled_out_gst_figure_is_never_the_total():
    text = (
        "Total GST Payable : Rupees Five Thousand Seven Hundred Seventy One & Paise Forty Only\n"
        "Net Amount Payable : Rupees Fifty One Thousand Five Hundred Nineteen Only "
        "Net Payable Amt. 51519.00"
    )
    assert total_from_words(text) == "51519.00"


def test_the_words_line_is_not_discarded_for_a_tax_line_above_it():
    # The unlabelled RUPEES ... ONLY form sits right under Menarini's SGST
    # summary; a lookbehind that crossed the line break threw it away.
    text = "SGST 9.00 % ON 37254.08 = 3352.87\nRUPEES FIFTY-FOUR THOUSAND FIFTY-EIGHT ONLY"
    assert total_from_words(text) == "54058.00"


# --- the gate ------------------------------------------------------------------

def _one_line_invoice(total):
    return {
        "invoice": {"total_amount": _leaf(total)},
        "line_items": [{
            "description": _leaf("RETELEX KIT"), "quantity": _leaf("10"),
            "pts": _leaf("12870.00"), "amount": _leaf("128700.00"),
            "cgst_amount": _leaf("7722.00"), "sgst_amount": _leaf("7722.00"),
            "gst_percent": _leaf("12.0"),
        }],
    }


def test_a_one_line_invoice_that_reconciles_is_kept():
    # Abbott bills one product. The old three-line floor sent it to the AI,
    # which then got its batch, PTR and PTS wrong.
    assert _parse_is_trustworthy_enough(_one_line_invoice("144144.00"), "t") is True


def test_a_one_line_parse_that_does_not_reconcile_goes_to_the_ai():
    assert _parse_is_trustworthy_enough(_one_line_invoice("999999.00"), "t") is False


# --- tax columns ---------------------------------------------------------------

def test_a_bare_tax_column_holding_rate_and_amount_yields_both():
    # V L Enterprises: "CGST" over "9.00 655.67".
    header = ["Product Description", "Batch No", "Qty", "Taxable Amount", "CGST", "SGST"]
    row = ["ASTYMIN C DROPS", "25CDLJ01", "132", "7285.20", "9.00 655.67", "9.00 655.67"]
    item = _build_item(row, _map_columns(header), header, _gst_columns(header),
                       interstate=False, local="sgst")
    assert _v(item, "cgst_percent") == "9"
    assert _v(item, "cgst_amount") == "655.67"
    assert _v(item, "sgst_amount") == "655.67"


def test_a_bare_tax_column_holding_only_money_is_an_amount():
    # Menarini: "CGST" over 28.08, the rate on the wrapped line below. 28.08 is
    # above the top GST slab, so it is tax, not a percentage.
    header = ["Product Name", "Batch No", "Quantity", "Taxable Amt.", "CGST", "SGST"]
    row = ["A-RET GEL", "ABO31ABA", "13.00", "1123.20", "28.08", "28.08"]
    item = _build_item(row, _map_columns(header), header, _gst_columns(header),
                       interstate=False, local="sgst")
    assert _v(item, "cgst_amount") == "28.08"
    assert _v(item, "sgst_amount") == "28.08"


def test_a_stacked_ruled_header_is_stitched_together():
    # V L: "CGST" spanning two ruled cells, "%" and "AMOUNT" on the row below.
    table = [
        ["PRODUCT DESCRIPTION", "BATCH NO.", "QTY", "TAXABLE AMOUNT", "CGST", None, "SGST", None],
        [None, None, None, None, "%", "AMOUNT", "%", "AMOUNT"],
        ["ASTYMIN", "25CDLJ01", "132", "7285.20", "9.00", "655.67", "9.00", "655.67"],
    ]
    header, data_from = _merge_stacked_header(table, 0)
    assert data_from == 2
    cols = _map_columns(header)
    assert header[cols["cgst_amount"]] == "CGST AMOUNT"
    assert header[cols["sgst_amount"]] == "SGST AMOUNT"


# --- which reading of the table ------------------------------------------------

def test_the_reading_whose_arithmetic_works_beats_a_page_border():
    # Overseas: a page-sized ruled box yields one row as easily as the real
    # table does. Row count cannot tell them apart; quantity x price can.
    real = [{"quantity": _leaf("60"), "pts": _leaf("312.43"), "amount": _leaf("15621.50")}]
    border = [{"quantity": None, "pts": _leaf("312.43"), "amount": _leaf("6.00")}]
    assert _better_reading(real, border) is True
    assert _better_reading(border, real) is False


# --- header bands --------------------------------------------------------------

def _word(text, x0, top, width=None):
    width = width if width is not None else 5.0 * len(text)
    return {"text": text, "x0": x0, "x1": x0 + width, "top": top}


def test_a_header_stacked_above_its_widest_line_is_read_whole():
    # Menarini prints "MRP" and "Batch No" on lines ABOVE the widest heading.
    # Read only downward, the batch column went untitled, no batch marker was
    # found anywhere, and the whole table was discarded.
    words = [
        _word("Batch", 200, 90), _word("No", 228, 90), _word("MRP", 330, 90),
        _word("Product", 40, 100), _word("Name", 80, 100), _word("HSN", 160, 100),
        _word("Mfg.Date", 200, 100), _word("Qty", 280, 100), _word("Rate", 330, 110),
        _word("Amount", 400, 100),
        _word("A-RET", 40, 130), _word("GEL", 75, 130), _word("30049099", 160, 130),
        _word("ABO31ABA", 200, 130), _word("13.00", 280, 130), _word("126.00", 330, 130),
        _word("1123.20", 400, 130),
        _word("PAPULEX", 40, 150), _word("30049930", 160, 150), _word("B4FD01", 200, 150),
        _word("10.00", 280, 150), _word("355.00", 330, 150), _word("2166.10", 400, 150),
    ]
    tables = tables_from_words(words)
    assert tables, "the table was not found"
    header = tables[0][0]
    assert any("Batch" in h for h in header)
    assert any("MRP" in h for h in header)
    assert len(tables[0]) - 1 == 2


# --- references and parties ----------------------------------------------------

def test_a_blank_reference_does_not_swallow_the_next_label():
    # V L prints both fields empty; we reported the LR as "DATE" and the
    # transporter as "TEL NO".
    text = "L.R. NO. : DATE :\nTRANSPORTER : TEL NO :\nOrder No. : 4608 Date :20/08/2025"
    refs = extract_references(text)
    assert refs["lr_no"] is None
    assert refs["transport"] is None
    assert refs["po_no"] == "4608"


def test_a_short_lorry_receipt_that_is_really_printed_is_kept():
    # These look like noise and are exactly what the bills print: Abbott
    # "LR No.:CC", Overseas "GR/LR No. :18/09". Only a captured LABEL is
    # rejected, never an odd-looking value.
    assert extract_references("LR No.:CC WT.:2.01")["lr_no"] == "CC"
    assert extract_references("GR/LR No. :18/09\nGR/LR Date : 18/09/2025")["lr_no"] == "18/09"


def test_a_tax_printed_only_as_a_rate_is_worked_out_and_marked():
    # Abbott prints 6.00 / 6.00 per line and the tax only in its footer.
    header = ["Prod.Desc.", "Batch No", "Billed Qty", "CGST%", "SGST/ UTGST %", "Value INR"]
    row = ["Retelex Kit", "TPV1A24A16", "10", "6.00", "6.00", "128700.00"]
    item = _build_item(row, _map_columns(header), header, _gst_columns(header),
                       interstate=False, local="sgst")
    assert _v(item, "cgst_amount") == "7722.00"
    assert _v(item, "sgst_amount") == "7722.00"
    assert item["cgst_amount"]["confidence"] < 1.0


def test_named_month_dates_are_read():
    refs = extract_references("Due date : 29-Sep-2025")
    assert refs["due_date"] == "29-Sep-2025"


def test_supplier_gstin_must_carry_the_suppliers_pan():
    page = "GSTIN / UIN:27AAECD7847H1ZC\nPAN No:AAACK3935D\nGSTIN:27AAACK3935D1ZS"
    assert supplier_gstin_for_pan("AAACK3935D", "27AAECD7847H1ZC", page) == "27AAACK3935D1ZS"
    # Agreeing already: nothing to change.
    assert supplier_gstin_for_pan("AAACK3935D", "27AAACK3935D1ZS", page) is None


def test_a_party_name_loses_the_neighbouring_columns_heading():
    block = (
        "Receiver (Billed to) Details of\n"
        "PVT LTD EASTERN AGENCIES HEALTHCARE\n"
        "A-2 FIRST FLOOR, TOBACCO HOUSE,\n"
        "GST NO. : 27AAECD7847H1ZC"
    )
    assert party_details(block)["name"] == "EASTERN AGENCIES HEALTHCARE PVT LTD"


def test_a_block_with_no_name_reports_none():
    # Abbott goes from "Billed To:" straight into the street address.
    block = (
        "Billed To: A-2 FIRST FLOOR TOBACCO HOUSE,GOLDEN TOBACCO LIMITED,S.V.ROAD , VILE\n"
        "PARLE(WEST)\n"
        "Address: ANDHERI,Maharashtra\n"
        "GSTIN / UIN:27AAECD7847H1ZC"
    )
    assert party_details(block)["name"] is None


def test_a_doubly_printed_block_reports_no_name():
    block = "NNaammee : EASTERN AGENCIES\nAAdddd r:e sAs-2 FIRST FLOOR\nGSTIN No : 27AAECD7847H1ZC"
    detail = party_details(block)
    assert detail["name"] is None
    assert detail["gstin"] == "27AAECD7847H1ZC"


def test_a_supplier_name_cut_at_the_column_edge_is_completed():
    page = "OVERSEAS HEALTH CARE PRIVATE LIMITED\nC/O HSS ENTER"
    assert _complete_legal_suffix("OVERSEAS HEALTH CARE PRIVATE", page) == \
        "OVERSEAS HEALTH CARE PRIVATE LIMITED"


# --- facts the bill states without printing them --------------------------------

from app.services.ocr.invoice_checks import complete_from_the_bill  # noqa: E402


def _msv_like():
    # MSV Lifesciences: both GSTINs, no PANs; CGST and SGST totals printed.
    return {
        "supplier": {"gstin": _leaf("33ABEFM0315R1Z8")},
        "bill_to": {"gstin": _leaf("33AASCA3306L2ZK")},
        "ship_to": {},
        "invoice": {"total_cgst_amount": _leaf("357.96"), "total_sgst_amount": _leaf("357.96")},
    }


def test_a_pan_is_read_out_of_its_gstin():
    f = _msv_like()
    complete_from_the_bill(f)
    assert _v(f["supplier"], "pan") == "ABEFM0315R"
    assert _v(f["bill_to"], "pan") == "AASCA3306L"


def test_an_intra_state_bill_carries_zero_igst_and_utgst_and_a_total_gst():
    f = _msv_like()
    complete_from_the_bill(f)
    inv = f["invoice"]
    assert _v(inv, "total_igst_amount") == "0.00"
    assert _v(inv, "total_utgst_amount") == "0.00"
    assert _v(inv, "total_gst_amount") == "715.92"


def test_an_inter_state_bill_carries_zero_cgst_and_sgst():
    f = {
        "supplier": {"gstin": _leaf("24AAACZ1234A1Z5")},
        "bill_to": {"gstin": _leaf("27AAECD7847H1ZC")},
        "invoice": {"total_igst_amount": _leaf("1200.00")},
    }
    complete_from_the_bill(f)
    inv = f["invoice"]
    assert _v(inv, "total_cgst_amount") == "0.00"
    assert _v(inv, "total_sgst_amount") == "0.00"
    assert _v(inv, "total_gst_amount") == "1200.00"


def test_no_zero_is_written_beside_a_tax_that_was_not_read():
    # Zero IGST next to a BLANK CGST/SGST would read as "this bill has no tax".
    f = _msv_like()
    f["invoice"] = {}
    complete_from_the_bill(f)
    assert "total_igst_amount" not in f["invoice"]
