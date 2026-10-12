"""Rules behind the client sample's remaining warnings, pinned without the PDFs.

Each test rebuilds the one shape that went wrong on a real bill - the PDFs are
customer documents and live in the private invoice corpus, never here.
"""
from app.services.ocr import document_kind
from app.services.ocr.amount_words import total_from_words
from app.services.ocr.einvoice_qr import apply as apply_qr
from app.services.ocr.invoice_checks import (
    line_rates_from_stated_tax,
    quantity_from_charge,
    refile_party_pans,
    tax_heads_from_rates,
    tds_deducted,
)
from app.services.ocr.invoice_header import extract_references
from app.services.ocr.invoice_parser import (
    _build_item,
    _extract_total,
    _gst_columns,
    _map_columns,
    _with_detail_lines_folded,
    find_invoice_no,
)
from app.services.ocr.missed_fields import find_missed
from app.services.ocr.verify import verify_invoice


def _leaf(v, confidence=1.0):
    return {"value": v, "confidence": confidence}


def _v(node, key):
    return (node.get(key) or {}).get("value")


def _item(header, row, interstate=False):
    cols = _map_columns(header)
    return _build_item(row, cols, header, _gst_columns(header), interstate=interstate)


def _check(fields, check_id):
    return next(c for c in verify_invoice(fields, {})["checks"] if c["id"] == check_id)


# --- header fields -------------------------------------------------------------------

def test_a_financial_year_serial_is_an_invoice_number_not_a_date():
    assert find_invoice_no("MULUND EAST-400081. Invoice No 25-26/0630 Order No.") == "25-26/0630"


def test_a_return_is_numbered_by_its_own_label_when_no_invoice_number_is_printed():
    assert find_invoice_no("D.L No-20B S.Return No. : CN00001 Date : 13-04-2025") == "CN00001"


def test_an_empty_label_over_another_is_no_order_number():
    refs = extract_references("Depot Order No. :\nParty Order No. : 5027 Order Date : 31-08-2025")
    assert refs["po_no"] == "5027"


def test_an_order_reference_keeps_the_time_it_carries():
    assert extract_references("PO No : 4614-21/08-02:06 PM ZJRS D.L.")["po_no"] == "4614-21/08-02:06 PM"


def test_an_empty_lr_is_not_the_order_label_beside_it():
    refs = extract_references("LR NO : Order No 3: 001061319\nLR Date : Order Date :02/07/2025")
    assert refs["lr_no"] is None and refs["lr_date"] is None
    # ...while a word of its own before "Date" is still an LR.
    assert extract_references("L.R. No. : LOCAL Date : 22 Sep 25")["lr_date"] == "22 Sep 25"


def test_the_next_labels_first_word_or_a_blank_marker_is_not_a_missed_field():
    text = ("LR No : LR Date : 27/08/2025\nInvoice No : Invoice Date : 20/08/2025 SR123\n"
            "GR/LR No. : na\nBuyer PO No. :shree simba chemist")
    paths = {m["path"] for m in find_missed({"invoice": {}}, text)}
    assert not paths & {"invoice.lr_no", "invoice.invoice_no", "invoice.po_no"}


def test_a_total_later_in_its_row_is_the_one_the_words_spell():
    text = ("Grand Total 10,059.60 905.37 905.37 0.00 11,870.00\n"
            "NET TO PAY(RUPEES): Eleven Thousand Eight Hundred Seventy Only")
    assert _extract_total(text) == "11870.00"


def test_amount_in_words_wrapped_onto_the_next_line():
    text = ("Total Invoice Value (in Words): RUPEES SEVEN LAKH TWENTY-FOUR THOUSAND EIGHT\n"
            "HUNDRED TWENTY-NINE AND TWENTY-FIVE PAISA ONLY\n(3)By accepting")
    assert total_from_words(text) == "724829.00"


def test_whole_rupees_of_tds_ending_a_line():
    assert tds_deducted("2.Any payment ... is not bound on us. TDS 854") == [854.0]


def test_an_irn_broken_across_two_lines_is_joined():
    fields = {"invoice": {"irn": _leaf("71ead75528dfc69682794d742673600197 e9e9e0ab22edb25b23eea6eef720c0")}}
    apply_qr(fields)
    assert len(_v(fields["invoice"], "irn")) == 64


# --- what kind of document -----------------------------------------------------------

def test_a_title_opening_the_page_in_any_case():
    assert document_kind.kind_from_page("Credit Note- Document (Returns) - Expired Returns\nWANBURY") \
        == "credit_note"


def test_a_page_numbered_as_a_sales_return_is_a_return():
    text = "AANAV\nS.Return No. : CN00001 Date : 13-04-2025\nGST INVOICE"
    assert document_kind.kind_from_page(text) == "return"
    # An invoice listing a return it adjusts is still an invoice.
    assert document_kind.kind_from_page("TAX INVOICE\nInvoice No: A000298\nS.Return No. : CN7") == "invoice"


# --- parties -------------------------------------------------------------------------

def test_the_buyers_pan_filed_as_the_suppliers_goes_back_to_the_buyer():
    fields = {"supplier": {"gstin": _leaf("27AAICA1356N1ZE"), "pan": _leaf("AASCA3306L")},
              "bill_to": {"gstin": _leaf("27AASCA3306L1ZE")}}
    refile_party_pans(fields, "GST : 27AAICA1356N1ZE\nPAN : AASCA3306L")
    assert _v(fields["supplier"], "pan") is None and _v(fields["bill_to"], "pan") == "AASCA3306L"


def test_the_suppliers_own_pan_printed_beats_its_agents():
    fields = {"supplier": {"gstin": _leaf("27AAACC0444K1ZV"), "pan": _leaf("AARFP5807B")}}
    refile_party_pans(fields, "Co. PAN No.:AAACC0444K\nPAN No :AARFP5807B GSTIN : 27AAACC0444K1ZV")
    assert _v(fields["supplier"], "pan") == "AAACC0444K"


# --- line columns --------------------------------------------------------------------

def test_a_value_before_the_tax_columns_is_taxable_and_the_last_amount_is_not():
    # Ajanta: Value is the taxable value; the trailing Amount is before the
    # special discount.
    header = ["Prod. Description", "Batch No", "Qty", "PTS", "Spl.Dis. Amount", "Value",
              "CGST %", "CGST Tax Amt", "SGST %", "SGST Tax Amt", "Amount"]
    item = _item(header, ["MELACARE", "G3730", "220.00", "166.50", "3,330.00", "33,300.00",
                          "6.00", "1,998.00", "6.00", "1,998.00", "36,630.00"])
    assert _v(item, "amount") == "33300.00"


def test_a_value_after_a_tax_head_is_that_heads_tax():
    # NSV: "SGST | VALUE | CGST | VALUE | AMOUNT".
    header = ["PRODUCT NAME", "BATCH", "QTY.", "P.T.S", "SGST", "VALUE", "CGST", "VALUE", "AMOUNT"]
    item = _item(header, ["LAXODOL GEL", "067", "10", "121.50", "6%", "72.90", "6%", "72.90", "1215.00"])
    assert _v(item, "amount") == "1215.00"
    assert _v(item, "sgst_amount") == "72.90" and _v(item, "cgst_amount") == "72.90"


def test_a_heading_that_only_mentions_gst_is_no_tax_column():
    header = ["Description (MRP as per Pre GST reform 2.0 rates)", "Batch No.", "Qty",
              "Per Pack (Prices post GST reforms 2.0)", "Value", "SGST/UTGST", "", "CGST/IGST", "",
              "Total GST", "Total Value"]
    assert _gst_columns(header) == [5, 7]
    item = _item(header, ["AMIFRU 40 TAB", "2JF8M002", "60", "12.70", "522.60", "2.50", "13.07",
                          "2.50", "13.07", "26.14", "548.74"])
    assert _v(item, "cgst_amount") == "13.07" and _v(item, "gst_percent") == "5.0"


def test_a_tax_cell_split_after_its_decimal_point():
    header = ["Product Description", "Batch No.", "Qty.", "Taxable Amount", "CGST", "", "SGST/UGST", ""]
    item = _item(header, ["BUSCOGAST", "BSA25023", "720.000", "21,470.54", "6.\n00", "1,288.\n23",
                          "6.\n00", "1,288.\n23"])
    assert _v(item, "cgst_amount") == "1288.23"


def test_the_rate_marked_percent_and_code_over_hsn():
    # Piramal: "Product Code/ HSN" stacked; "SGST % Rs. / %" holds the tax over
    # the rate; a free line's tax is 0.00 at 2.5%.
    header = ["Product Code/ HSN", "Product Description", "Batch No", "Billed Qty / UoM",
              "Taxable Value", "CGST Rs. / %", "SGST % Rs. / %"]
    item = _item(header, ["400134004\n30045090", "Supradyn Daily ( FREE )", "MH1072", "48\nECH", "0.00",
                          "0.00\n2.5%", "0.00\n2.5%"])
    assert _v(item, "hsn") == "30045090" and _v(item, "product_code") == "400134004"
    assert _v(item, "cgst_amount") == "0.00" and _v(item, "sgst_percent") == "2.5"
    assert _v(item, "quantity") == "48"


def test_a_head_with_a_rate_but_no_tax_is_not_charged():
    # Yogi: IGST Rate 12.00 with IGST Amt 0.00 beside CGST and SGST at 6%.
    header = ["DESCRIPTION OF GOODS", "Batch No.", "Sale Qty", "PTS", "IGST Rate%", "IGST Amt",
              "CGST Rate%", "CGST Amt", "SGST Rate%", "SGST Amt", "Total Amount"]
    item = _item(header, ["GABAXIA NT TAB", "GBXT-4014", "40", "148.50", "12.00", "0.00",
                          "6.00", "356.40", "6.00", "356.40", "5940.00"])
    assert _v(item, "gst_percent") == "12.0" and _v(item, "igst_percent") == "0"
    # Its "Total Amount" is quantity x PTS - the taxable value, not a net.
    assert _v(item, "amount") == "5940.00" and _v(item, "net_amount") != "5940.00"


def test_a_net_value_before_the_tax_is_the_taxable_value():
    header = ["Product", "Batch No", "QTY", "Value", "Trade Discount", "Net Value", "CGST", "SGST",
              "IGST", "Total"]
    item = _item(header, ["MEPRATE 10", "PLMT2505", "100", "5239.00", "10.00 %", "4715.10",
                          "6.00 % 282.91", "6.00 % 282.91", "0.00 % 0.00", "5,280.92"])
    assert _v(item, "amount") == "4715.10" and _v(item, "gross_amount") == "5239.00"
    assert _v(item, "net_amount") == "5280.92"


def test_hsn_cells_carrying_something_else():
    header = ["HSN CODE PRODUCT DESCRIPTION", "MFG ID", "BATCH NO.", "TOTAL QTY", "TOTAL VALUE"]
    # The parser takes the maker's code column for the name (no name heading).
    cols = {**_map_columns(header), "description": 1}
    item = _build_item(["30049099 DUOPIL 2/500 TABS", "APS", "SPF250738", "10", "700.70"], cols, header, [])
    assert _v(item, "hsn") == "30049099" and _v(item, "description") == "DUOPIL 2/500 TABS"
    header = ["Mfg Cat HSN No.", "Product Description", "Batch Number", "Billed Qty.", "Taxable Value"]
    item = _item(header, ["CH N 30049099", "COFSILS", "4M60350", "48", "899.96"])
    assert _v(item, "hsn") == "30049099"


def test_an_hsns_last_digit_set_against_the_mrp():
    header = ["PRODUCT NAME", "BATCH", "HSN", "M.R.P.", "P.T.S", "P.T.R.", "QTY.", "AMOUNT"]
    item = _item(header, ["DIPTOCAL TAB", "PFT-734", "2106909", "9199.00", "121.42", "134.91", "120",
                          "14570.40"])
    assert _v(item, "hsn") == "21069099" and _v(item, "mrp") == "199.00"


def test_a_lines_second_printed_line_is_folded_into_it():
    cols = {"description": 0, "batch_no": 1, "mrp": 2, "quantity": 3, "amount": 4}
    rows = [["CELEVIDA", "ZCEV020725", "1,732.50", "10.000", "11,995.20"],
            ["21069099", "06.2027", "1,305.25", "", ""]]
    [row] = _with_detail_lines_folded(rows, cols)
    assert row[1] == "ZCEV020725\n06.2027"


# --- AI readings put right by the bill's own arithmetic ------------------------------

def _ai_line(amount, rate, qty="1"):
    return {"quantity": _leaf(qty), "rate": _leaf(amount), "amount": _leaf(amount), "gst_percent": _leaf(rate)}


def test_two_line_rates_that_cannot_give_the_stated_tax():
    lines = [_ai_line("7534.28", "18"), _ai_line("142.39", "12"), _ai_line("3317.13", "12"),
             _ai_line("2481.40", "0"), _ai_line("253.22", "18")]
    fields = {"invoice": {"total_amount": _leaf("15391.00"), "total_taxable_amount": _leaf("13728.42"),
                          "total_cgst_amount": _leaf("831.30"), "total_sgst_amount": _leaf("831.30")},
              "line_items": lines}
    assert line_rates_from_stated_tax(fields)
    assert [_v(i, "gst_percent") for i in lines] == ["12", "12", "12", "12", "18"]


def test_taxable_and_tax_read_off_one_slab():
    lines = [_ai_line("1597.50", "12"), _ai_line("10883.00", "18")]
    fields = {"invoice": {"total_amount": _leaf("14631.00"), "total_taxable_amount": _leaf("10883.00"),
                          "total_cgst_amount": _leaf("979.48"), "total_sgst_amount": _leaf("979.48")},
              "line_items": lines}
    assert tax_heads_from_rates(fields)
    assert _v(fields["invoice"], "total_taxable_amount") == "12480.50"


def test_the_free_figure_a_line_is_charged_on_is_its_quantity():
    item = {"quantity": _leaf("24"), "free_quantity": _leaf("10"), "rate": _leaf("170.25"),
            "amount": _leaf("1702.50")}
    assert quantity_from_charge({"line_items": [item]})
    assert _v(item, "quantity") == "10" and _v(item, "free_quantity") is None


# --- checks --------------------------------------------------------------------------

def test_a_uniform_cash_discount_is_no_line_discount_error():
    def line(gross, disc, taxable):
        return {"gross_amount": _leaf(gross), "discount_amount": _leaf(disc), "amount": _leaf(taxable)}
    fields = {"invoice": {}, "line_items": [line("3190.50", "319.05", "2857.09"),
                                            line("4435.00", "0.00", "4412.82")]}
    assert _check(fields, "line_discount")["status"] != "fail"


def test_a_free_line_with_a_valued_discount_is_no_error():
    fields = {"invoice": {}, "line_items": [
        {"gross_amount": _leaf("22.50"), "discount_amount": _leaf("3.75"), "amount": _leaf("0")}]}
    assert _check(fields, "line_discount")["status"] != "fail"


def test_tax_on_the_value_after_a_lines_discount_in_money():
    fields = {"invoice": {}, "line_items": [{
        "amount": _leaf("49770.60"), "discount_amount": _leaf("2488.53"),
        "cgst_percent": _leaf("2.5"), "cgst_amount": _leaf("1182.05")}]}
    assert _check(fields, "line_tax")["status"] == "pass"


def test_a_code_printed_under_the_name_goes_to_its_own_line():
    # Lupin: each line's code wraps into the row below, and the last row
    # takes in a footer.
    header = ["Product Code &", "Batch No.", "Qty. / UOM", "Amount"]
    cols = _map_columns(header)
    rows = [["APTIVATE SYRUP 175 ML", "A25429TM", "252", "27459.00"],
            ["419552 SOFTOVAC 100GM 412689", "A25234MU", "350", "50625.00"],
            ["VOVILUP SPRAY 512744 GST IS NOT PAYABLE UNDER", "VVP25001", "1", "0.00"]]
    from app.services.ocr.invoice_parser import _rows_from_tables
    items = _rows_from_tables([[header] + rows], {}, False, "sgst")
    assert [(_v(i, "description"), _v(i, "product_code")) for i in items] == [
        ("APTIVATE SYRUP 175 ML", "419552"), ("SOFTOVAC 100GM", "412689"), ("VOVILUP SPRAY", "512744")]


def test_a_debit_credit_note_settled_on_the_bill_explains_its_words():
    # Abbott: figures 144,144.00, a "DB/CR Adj" table settling note
    # 4134168259 for 76.00, and the words spell 144,068.
    from app.services.ocr.invoice_checks import credit_notes_set_off, reconcile_invoice
    text = ("Due Date DB/CR Adj: SGST/UTGST :Rs. 7,722.00\n"
            "EASTERN AGENCIES HEALTHCARE PVDoc. No. Date Amount CESSES :Rs. 0.00\n"
            "Please send the payment 4134168259 14.08.2025 76.00 Total :Rs. 144,144.00\n")
    assert credit_notes_set_off(text) == [76.0]
    fields = {"invoice": {"total_amount": _leaf("144144.00"), "total_cgst_amount": _leaf("7722.00"),
                          "total_sgst_amount": _leaf("7722.00")},
              "line_items": [{"amount": _leaf("128700.00"), "cgst_amount": _leaf("7722.00"),
                              "sgst_amount": _leaf("7722.00")}]}
    report = reconcile_invoice(fields, total_in_words="144068.00", page_text=text)
    assert report["total_in_words_disagrees"] is False
    assert any("76.00" in w for w in report["warnings"])


# --- pack, and a distributor's price ------------------------------------------------

def test_a_pack_printed_in_the_name_column_that_says_so():
    header = ["PRODUCT DESCRIPTION & PACKS", "BATCHNO", "QTY SALE", "TAXABLE VALUE"]
    item = _item(header, ["BILASTERO 20 MG TABLETS 10'S", "2GT23084A", "6", "497.58"])
    assert _v(item, "pack") == "10'S"
    header = ["Product/Pack / UOM", "Batch No", "Qty.", "Taxable Amount"]
    item = _item(header, ["GABAWOK NT 400MG 15T", "WTN5001", "10", "3,088.30"])
    assert _v(item, "pack") == "15T"     # never the 400MG strength
    # A name column that does not claim the pack is left alone.
    item = _item(["Product Name", "Batch No", "Qty.", "Amount"], ["GABAWOK NT 400MG 15T", "W1", "10", "3088.30"])
    assert _v(item, "pack") is None


def test_a_pkg_detail_column_is_the_pack():
    assert "pack" in _map_columns(["DESCRIPTION", "BATCH NO.", "QTY", "TAXABLE VALUE", "PKG DETAIL"])


def test_a_distributors_price_is_not_the_pts_but_can_be_the_billed_rate():
    from app.services.ocr.invoice_checks import resolve_billed_rate
    header = ["Prod.Desc. & Pack", "Batch No", "Billed Qty", "M.R.P.", "PTR", "Dist.Price", "Value INR"]
    assert "pts" not in _map_columns(header)
    item = _item(header, ["Retelex Kit Sales", "TPV1A24A16", "10", "43480.17", "14300.00", "12870.00",
                          "128700.00"])
    resolve_billed_rate([item])
    assert _v(item, "pts") is None
    assert _v(item, "rate") == "12870.00" and _v(item, "rate_source") == "Dist.Price"


# --- header fields found by the client-tester audit ---------------------------------

def test_invoice_numbers_under_their_many_labels():
    assert find_invoice_no("Invoice Number: :3250201584 Transportation Mode") == "3250201584"
    assert find_invoice_no("BILL TO SHIP TO Tax Invoice\nEASTERN AGENCY PVT LTD No. : I25846207265 "
                           "Date : 04.10.2025\nLIMITED SD Doc No. : 9035616019") == "I25846207265"
    assert find_invoice_no("INDCHEMIE Credit note : CS0000001364\nDoc No : 55958966") == "CS0000001364"


def test_invoice_dates_under_their_many_labels():
    from app.services.ocr.invoice_parser import _invoice_date_match

    def date(text):
        m = _invoice_date_match(text)
        return m.group(1) if m else None
    assert date("PAN : AADFP5700F Inv Dt : 19/06/2025 Due : 11/07/2025") == "19/06/2025"
    assert date("Date of Invoice: 29.09.2025 AWB Date: 03.10.2025") == "29.09.2025"
    assert date("Invoice No. : KLPL000419Date : 13-08-2025\nDue Date : 03-09-2025") == "13-08-2025"
    assert date("UNIT NO. 101, Date 25/07/2025\nDue Date : 08/09/2025") == "25/07/2025"


def test_discount_totals_under_their_many_labels():
    from app.services.ocr.invoice_header import extract_totals
    assert extract_totals("Total Items : 6 DISC AMOUNT 1374.28")["total_discount_amount"] == "1374.28"
    assert extract_totals("Less : Trade Disc. -15947.15")["total_discount_amount"] == "15947.15"
    assert extract_totals("Cash Discount @ 0.5%: -2081.04 414124.34")["total_discount_amount"] == "2081.04"


def test_a_taxable_value_printed_once_and_alone():
    from app.services.ocr.invoice_header import extract_totals
    assert extract_totals("GST 5% Taxable Value: 6,263.92 CGST Value: 156.59")["total_taxable_amount"] == "6263.92"
    # Per slab, with the total last: no single figure to take.
    assert extract_totals("Taxable Value 13988.40 10745.15 21014.20 45747.75")["total_taxable_amount"] is None


def test_the_printed_head_total_beats_the_lines_sum():
    from app.services.ocr.invoice_parser import _printed_head_total
    assert _printed_head_total("76577.30 4990.90 4990.90", 4990.92) == 4990.90
    assert _printed_head_total("143,524.33 6.00 8,611.46 6.00 8,611.46", 8611.40) == 8611.46
    assert _printed_head_total("Add : C GST 14995.75", 14995.76) == 14995.75
    assert _printed_head_total("76577.30 4990.90 4990.90", 2500.00) is None   # nowhere near


def test_a_group_rows_rate_reaches_its_lines():
    from app.services.ocr.invoice_parser import _rates_from_group_rows
    items = [{"batch_no": _leaf("4146"), "amount": _leaf("1658.57"), "net_amount": _leaf("1658.57")},
             {"batch_no": _leaf("E25"), "amount": _leaf("253.22")}]
    text = ("ACOCONTIN 4146 1658.57\nGST Rate : 12.00 CGST Amt : 99.51 SGST Amt : 99.51\n"
            "NUSOWIN E25 253.22\nGST Rate : 18.00 CGST Amt : 22.79 SGST Amt : 22.79")
    _rates_from_group_rows(items, text, interstate=False)
    assert [_v(i, "gst_percent") for i in items] == ["12", "18"]
    assert _v(items[0], "net_amount") is None    # its "Total Amount" was before the tax
