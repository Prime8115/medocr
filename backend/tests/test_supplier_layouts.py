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


def test_a_pan_the_bill_does_not_print_stays_blank():
    # The client's rule: not on the invoice -> blank. MSV prints no PAN, so its
    # PAN columns stay empty, not characters 3-12 of the GSTIN.
    f = _msv_like()
    complete_from_the_bill(f)
    assert not _v(f["supplier"], "pan")
    assert not _v(f["bill_to"], "pan")


def test_a_pan_copied_out_of_a_gstin_is_blanked():
    from app.services.ocr.invoice_checks import drop_copied_pans

    f = _msv_like()
    f["supplier"]["pan"] = _leaf("ABEFM0315R")
    f["bill_to"]["pan"] = _leaf("AASCA3306L")
    page = "GSTIN/UIN: 33ABEFM0315R1Z8\nBuyer (Bill to)\nGSTIN/UIN : 33AASCA3306L2ZK"
    assert drop_copied_pans(f, page) == ["supplier.pan", "bill_to.pan"]
    assert not _v(f["supplier"], "pan") and not _v(f["bill_to"], "pan")


def test_a_printed_pan_is_kept():
    from app.services.ocr.invoice_checks import drop_copied_pans

    f = _msv_like()
    f["supplier"]["pan"] = _leaf("ABEFM0315R")
    page = "GSTIN: 33ABEFM0315R1Z8  PAN No: ABEFM0315R\nBill to\nGSTIN 33AASCA3306L2ZK"
    assert drop_copied_pans(f, page) == []
    assert _v(f["supplier"], "pan") == "ABEFM0315R"


def test_without_the_page_text_nothing_is_blanked():
    from app.services.ocr.invoice_checks import drop_copied_pans

    f = _msv_like()
    f["supplier"]["pan"] = _leaf("ABEFM0315R")
    assert drop_copied_pans(f, "") == []


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


def test_menarini_specific_fields():
    """Verify that Menarini's LR Date, PO Date, DL Date 1/2, and line tax percentages
    are extracted and verified, not left blank."""
    import pathlib

    import pytest
    from app.services.ocr import process_document
    p = pathlib.Path(__file__).parent / "real_invoices" / "A.MENARINI INDIA.pdf"
    if not p.exists():
        pytest.skip("the Menarini PDF is a customer document, kept out of the repo")
    res = process_document("menarini_test", p.read_bytes(), "application/pdf", doc_type="invoice")
    fields = res["fields"]
    inv = fields["invoice"]
    sup = fields["supplier"]
    items = fields["line_items"]

    assert inv["lr_date"]["value"] == "22 Sep 25"
    assert inv["lr_date"]["normalized"] == "2025-09-22"
    assert inv["po_date"]["value"] == "22-Sep-2025"
    assert inv["po_date"]["normalized"] == "2025-09-22"

    assert sup["dl_no_1"]["value"] == "20B-MH-TZ3-82629"
    assert sup["dl_date_1"]["value"] == "27-Apr-2028"
    assert sup["dl_date_1"]["normalized"] == "2028-04-27"
    assert sup["dl_no_2"]["value"] == "21B-MH-TZ3-82630"
    assert sup["dl_date_2"]["value"] == "27-Apr-2028"
    assert sup["dl_date_2"]["normalized"] == "2028-04-27"

    assert len(items) == 12
    for item in items:
        assert item["cgst_percent"]["value"] in ("2.5", "9")
        assert item["sgst_percent"]["value"] in ("2.5", "9")
        assert item["gst_percent"]["value"] in ("5", "5.0", "18", "18.0")
        assert item["cgst_amount"]["value"] is not None
        assert item["sgst_amount"]["value"] is not None

    verif = res.get("meta", {}).get("verification", {})
    assert verif.get("verdict") == "verified"
    assert verif.get("failed") == 0


# --- header references, as the suppliers print them ------------------------------

from app.services.ocr.invoice_header import drug_licences, extract_totals  # noqa: E402


def test_lr_and_order_dates_printed_after_their_numbers():
    # Menarini: "L.R. No. : LOCAL Date : 22 Sep 25", "Order No. : X Date : 22-Sep-2025".
    text = ("L.R. No. : LOCAL Date : 22 Sep 25\n"
            "Order No. : MUM25NODM01080 Date : 22-Sep-2025")
    refs = extract_references(text)
    assert refs["lr_no"] == "LOCAL"
    assert refs["lr_date"] == "22 Sep 25"
    assert refs["po_no"] == "MUM25NODM01080"
    assert refs["po_date"] == "22-Sep-2025"


def test_a_licence_validity_after_valid_till():
    text = "D.L. No.1 - 20B-MH-TZ3-82629 Valid till - 27-Apr-2028"
    assert drug_licences(text)[0] == ("20B-MH-TZ3-82629", "27-Apr-2028")


def test_a_licence_never_takes_a_date_that_merely_follows_later():
    # The e-way bill date further along the line is not the licence's validity.
    text = "DL No 1 : 20B-MH-MZ5-190671 Weight : 12.88 E-way Bill Gen.Date : 22-Sep-2025"
    assert drug_licences(text)[0] == ("20B-MH-MZ5-190671", None)


def test_a_licence_printed_validity_first():
    # Abbott: form 20B, valid till 11.09.2027, number MH-TZ2-491363.
    text = "DL No.-20B11.09.2027/MH-TZ2-491363\nDL No.-21B11.09.2027/MH-TZ2-491364"
    assert drug_licences(text)[:2] == [("MH-TZ2-491363", "11.09.2027"),
                                        ("MH-TZ2-491364", "11.09.2027")]


def test_an_irn_labelled_irn_no():
    text = "IRN No.: DDE3F825D6EC277E8B61933B29E6EF0BB64624EAE181772009DA7433F09C35D1 Cheque No :"
    assert extract_references(text)["irn"].startswith("DDE3F825D6EC")


def test_a_carrier_is_never_the_address_flowing_under_its_label():
    # Bharat: the header's "Name of Carrier" has the buyers' address beneath;
    # the footer's has the carrier.
    text = ("GOLDEN TOBACCO LTD S.V.ROAD GOLDEN TOBACCO LTD S.V.ROAD Name of Carrier\n"
            "VILE PARLE WEST VILE PARLE WEST\n"
            "...\n"
            "Name of Carrier\nQUICK COURIER")
    assert extract_references(text)["transport"] == "QUICK COURIER"


def test_a_transport_mode_when_no_transporter_is_named():
    text = "Tax is Payable On Reverse Charge : No Transportation Mode : BY HAND DELIVERY"
    assert extract_references(text)["transport"] == "BY HAND DELIVERY"


def test_a_discount_total_with_a_currency_mark():
    assert extract_totals("Less Disc. :Rs. 0.00")["total_discount_amount"] == "0.00"


def test_a_zero_discount_is_settled_by_the_lines():
    f = _msv_like()
    f["line_items"] = [{"discount_percent": _leaf("0.00")}, {"discount_percent": _leaf("0")}]
    complete_from_the_bill(f)
    assert _v(f["invoice"], "total_discount_amount") == "0.00"


def test_an_unknown_discount_is_left_blank():
    f = _msv_like()
    f["line_items"] = [{"discount_percent": _leaf("0.00")}, {"amount": _leaf("100.00")}]
    complete_from_the_bill(f)
    assert "total_discount_amount" not in f["invoice"]


def test_a_rate_printed_on_the_wrapped_line_beneath_its_tax():
    # Menarini: "CGST" over 28.08, and "2.50 %" on the wrapped line below.
    words = [
        _word("Batch", 200, 90), _word("Product", 40, 100), _word("Name", 80, 100),
        _word("HSN", 160, 100), _word("Qty", 280, 100), _word("Amount", 330, 100),
        _word("CGST", 400, 100), _word("SGST", 450, 100),
        _word("A-RET", 40, 130), _word("30049099", 160, 130), _word("ABO31ABA", 200, 130),
        _word("13.00", 280, 130), _word("1123.20", 330, 130),
        _word("28.08", 400, 130), _word("28.08", 450, 130),
        _word("2.50", 400, 142), _word("%", 425, 142), _word("2.50", 450, 142), _word("%", 475, 142),
    ]
    table = tables_from_words(words)[0]
    header, row = table[0], table[1]
    item = _build_item(row, _map_columns(header), header, _gst_columns(header),
                       interstate=False, local="sgst")
    assert _v(item, "cgst_percent") == "2.5"
    assert _v(item, "cgst_amount") == "28.08"
    assert _v(item, "sgst_percent") == "2.5"


# --- a printed "SGST/UTGST" figure is shown under UTGST, counted once ------------

def _abbott_like():
    # Abbott: one "SGST/UTGST" column, Maharashtra to Maharashtra.
    return {
        "supplier": {"gstin": _leaf("27AAACK3935D1ZS")},
        "bill_to": {"gstin": _leaf("27AAECD7847H1ZC")},
        "ship_to": {},
        "invoice": {"total_cgst_amount": _leaf("7722.00"), "total_sgst_amount": _leaf("7722.00"),
                    "total_amount": _leaf("144144.00"), "total_taxable_amount": _leaf("128700.00")},
        "line_items": [{"amount": _leaf("128700.00"), "net_amount": _leaf("144144.00"),
                        "cgst_percent": _leaf("6.00"), "cgst_amount": _leaf("7722.00"),
                        "sgst_percent": _leaf("6.00"), "sgst_amount": _leaf("7722.00")}],
    }


def test_a_printed_sgst_utgst_figure_is_shown_under_utgst():
    from app.services.ocr.invoice_checks import show_combined_utgst

    f = _abbott_like()
    complete_from_the_bill(f)  # fixes total GST from the heads first
    assert show_combined_utgst(f, "CGST :Rs. 7,722.00\nSGST/UTGST :Rs. 7,722.00")
    assert _v(f["invoice"], "total_utgst_amount") == "7722.00"
    assert _v(f["line_items"][0], "utgst_amount") == "7722.00"
    assert _v(f["line_items"][0], "utgst_percent") == "6.00"
    assert _v(f["invoice"], "total_gst_amount") == "15444.00"  # once, not 23,166


def test_a_heading_with_table_rules_run_into_it_is_still_found():
    from app.services.ocr.invoice_checks import show_combined_utgst

    f = _abbott_like()
    # MSV's scan, as local OCR reads it.
    assert show_combined_utgst(f, "7 Taxable CGST _—|~—SSGST/UTGST__|~—sTotal SC")
    assert _v(f["invoice"], "total_utgst_amount") == "7722.00"


def test_without_a_combined_heading_utgst_stays_as_it_was():
    from app.services.ocr.invoice_checks import show_combined_utgst

    f = _abbott_like()
    complete_from_the_bill(f)
    assert not show_combined_utgst(f, "CGST 7,722.00\nSGST 7,722.00")
    assert _v(f["invoice"], "total_utgst_amount") == "0.00"


def test_a_combined_figure_is_counted_once_in_the_line_net():
    from app.services.ocr.invoice_checks import show_combined_utgst
    from app.services.ocr.verify import verify_invoice

    f = _abbott_like()
    show_combined_utgst(f, "SGST / UTGST")
    checks = {c["id"]: c for c in verify_invoice(f, {"sgst_utgst_combined": True})["checks"]}
    assert checks["line_net"]["status"] == "pass"
    # Without the flag the same figures would count the tax twice and fail.
    checks = {c["id"]: c for c in verify_invoice(f, {})["checks"]}
    assert checks["line_net"]["status"] == "fail"
