"""Rules learned from the client's 121-supplier sample, pinned without the PDFs.

Each test rebuilds the one shape that went wrong on a real bill - the PDFs are
customer documents and live in the private invoice corpus, never here.
"""
import io

from app.services.ocr import parties
from app.services.ocr.amount_words import total_from_words
from app.services.ocr.invoice_checks import SUMMED_CONFIDENCE, reconcile_invoice
from app.services.ocr.invoice_parser import (
    _build_item,
    _map_columns,
    _split_side_by_side,

    find_invoice_no,
    parse_invoice_pdf,
    upright_pdf,
)

SUPPLIER = "27AAICA1356N1ZE"   # Adroit Biomed - valid check digits
BUYER = "27AASCA3306L1ZE"      # Ascent Health & Wellness
EASTERN = "27AAECD7847H1ZC"
PFIZER = "27AADCP8985B1Z4"


def _leaf(v, confidence=1.0):
    return {"value": v, "confidence": confidence}


def _v(node, key):
    return (node.get(key) or {}).get("value")


# --- who is the supplier and who the buyer --------------------------------------------

def test_a_buyer_printed_under_no_heading_is_found_by_its_gstin():
    # Adroit prints the pharmacy's block straight after its own, no "Bill To".
    text = (f"ADROIT BIOMED LIMITED (BHIWANDI)\nGST : {SUPPLIER} PAN : AAICA1356N\n"
            f"ASCENT HEALTH & WELLNESS SOLUTIONS PRIVATE LIMITED\nL.B.S MARG, BHANDUP\n"
            f"GSTIN{BUYER} [ REGISTER ]\n")
    fields = {"supplier": {"name": _leaf("ADROIT BIOMED LIMITED"), "gstin": _leaf(SUPPLIER)},
              "bill_to": {}}
    parties.resolve(fields, text)
    assert _v(fields["bill_to"], "gstin") == BUYER
    assert _v(fields["bill_to"], "name") == "ASCENT HEALTH & WELLNESS SOLUTIONS PRIVATE LIMITED"
    assert _v(fields["supplier"], "gstin") == SUPPLIER


def test_the_buyers_gstin_read_as_the_suppliers_is_corrected():
    # Pfizer: "Bill To: ... GSTIN 27AAECD..." sits above Pfizer's own GSTIN.
    text = (f"Pfizer Products India Pvt Ltd. Invoice No.: 7086281640\n"
            f"Bill To: 3000396982 EASTERN AGENCIES HEALTHCARE PVT L\nGSTIN: {EASTERN} Maharashtra\n"
            f"Tel.: 022\nGSTIN:{PFIZER}\nPAN:AADCP8985B\n")
    fields = {"supplier": {"name": _leaf("Pfizer Products India Pvt Ltd"), "gstin": _leaf(EASTERN)},
              "bill_to": {"gstin": _leaf(EASTERN)}}
    notes = parties.resolve(fields, text)
    assert _v(fields["supplier"], "gstin") == PFIZER
    assert _v(fields["bill_to"], "gstin") == EASTERN
    assert notes and "buyer's" in notes[0]


def test_a_supplier_name_run_on_into_the_buyers_column_is_cut():
    # Aaraf prints the two blocks side by side; the shop knows it is Eastern.
    text = (f"AARAF PHARMA M/s EASTERN AGENCIES HEALTHCARE PRIVATE\nGST : {EASTERN}\n"
            f"GSTIN : 27BPVPK4192N1ZW\nReciver For AARAF PHARMATerms & Conditions\n")
    fields = {"supplier": {"name": _leaf("AARAF PHARMA M/s EASTERN AGENCIES HEALTHCARE PRIVATE"),
                           "gstin": _leaf(EASTERN)}, "bill_to": {}}
    token = parties.OWN_GSTINS.set((EASTERN,))
    try:
        parties.resolve(fields, text)
    finally:
        parties.OWN_GSTINS.reset(token)
    assert _v(fields["supplier"], "name") == "AARAF PHARMA"
    assert _v(fields["supplier"], "gstin") == "27BPVPK4192N1ZW"
    assert _v(fields["bill_to"], "gstin") == EASTERN


def test_the_shops_own_gstin_is_never_left_as_the_supplier():
    # Only the shop's own GSTIN reads cleanly (R M prints its own unreadably).
    fields = {"supplier": {"gstin": _leaf(EASTERN)}, "bill_to": {}}
    token = parties.OWN_GSTINS.set((EASTERN,))
    try:
        notes = parties.resolve(fields, f"GSTIN: {EASTERN}\nGSTIN: 27AYEPM1405FIZT\n")
    finally:
        parties.OWN_GSTINS.reset(token)
    assert not _v(fields["supplier"], "gstin")
    assert _v(fields["bill_to"], "gstin") == EASTERN
    assert notes and "shop's own" in notes[0]


def test_three_businesses_and_no_label_are_left_as_read():
    text = f"A {SUPPLIER}\nB {BUYER}\nC {PFIZER}\n"
    fields = {"supplier": {"gstin": _leaf(None)}, "bill_to": {}}
    assert parties.resolve(fields, text) == []
    assert not _v(fields["bill_to"], "gstin")


# --- the Marg-style ERP grid -----------------------------------------------------------

HEADER = ["HSN/SAC", "PTR", "PRODUCT DESCRIPTION", "PACK.", "MFGR EXP. BATCH NO. DATE", "QTY", "SCH",
          "MRP", "RATE", "AMOUNT", "DISC", "TAXABLE", "%", "CGST AMOUNT", "SGST % AMOUNT"]


def _item(row):
    cols = _map_columns(HEADER)
    return _build_item(row, cols, HEADER, [i for i, h in enumerate(HEADER) if "SGST" in h])


def test_maker_expiry_and_batch_in_one_cell_are_split():
    assert _split_side_by_side("MFGR EXP. BATCH NO. DATE", "BSV- 10/26 E JV01ABA") == {
        "expiry": "10/26", "batch_no": "EJV01ABA", "manufacturer": "BSV"}
    assert _split_side_by_side("BATCH", "CH-2501") == {}


def test_the_erp_grid_reads_as_printed():
    item = _item(["30049099", "419.06", "MIFIACT-25 TABS", "NA", "BSV- 10/26 EJV01ABA", "", "10 (23.08%)",
                  "586.69", "377.15", "3771.50", "870.46", "2901.04", "6.0", "174.06", "6.0 174.06"])
    assert _v(item, "batch_no") == "EJV01ABA" and _v(item, "expiry") == "10/26"
    assert _v(item, "amount") == "2901.04"              # TAXABLE, not the AMOUNT before discount
    assert _v(item, "gross_amount") == "3771.50"
    assert _v(item, "discount_amount") == "870.46"      # "DISC" money, not a percentage
    assert _v(item, "quantity") == "10"                 # amount / rate, lost under SCH
    assert _v(item, "cgst_percent") == "6.0"            # the lone "%" left of CGST AMOUNT
    assert _v(item, "gst_percent") == "12"              # both heads, not one


# --- references and totals ---------------------------------------------------------------

def test_invoice_numbers_under_every_label():
    assert find_invoice_no("MH-MZ5-585861,21B-585861Invoice No. : A000298 Date") == "A000298"
    assert find_invoice_no("GST Inv. No.: 271001001039") == "271001001039"
    assert find_invoice_no("Bill  No.: 606/25/S/1289") == "606/25/S/1289"
    assert find_invoice_no("Invoice: 9816172912 Date: 18.08.2025") == "9816172912"
    assert find_invoice_no("Eway Bill NO: 271987615581") is None


def test_totals_spelled_out_in_every_form():
    assert total_from_words("Rs. Five Thousand Eight Hundred Sixty One Only") == "5861.00"
    assert total_from_words("Amount in SEVENTY EIGHT THOUSAND SIX HUNDRED TWENTY THREE RUPEES For") == "78623.00"
    assert total_from_words("Twenty-Four Thousand Seven Hundred Sixty Only Due on") == "24760.00"
    assert total_from_words("Payment requested by Crossed Cheque / Demand Draft / only.") is None


def _bill(lines, total, taxable=None):
    invoice = {"total_amount": _leaf(total)}
    if taxable:
        invoice["total_taxable_amount"] = taxable
    return {"invoice": invoice, "line_items": [{"amount": _leaf(a)} for a in lines]}


def test_a_taxable_total_summed_from_the_lines_proves_nothing():
    # AANAV: 1 line of 14 "reconciled" against a taxable total added up from it.
    summed = _bill(["357.44"], "5861.00", _leaf("357.44", SUMMED_CONFIDENCE))
    assert reconcile_invoice(summed)["total_reconciles"] is False
    printed = _bill(["357.44"], "5861.00", _leaf("357.44"))
    assert reconcile_invoice(printed)["total_reconciles"] is True


def test_a_credit_note_the_bill_applies_itself_is_reconciled_and_said():
    # AIOCD: lines + tax = "Total Amt : 99230.21"; payable 95,055 after a credit note.
    bill = _bill(["88598.41"], "95055.00")
    bill["invoice"].update(total_cgst_amount=_leaf("5315.90"), total_sgst_amount=_leaf("5315.90"))
    page = "CN Ref : 259104 Total Amt : 99230.21\nIn Words Rupees : NINETY-FIVE THOUSAND FIFTY-FIVE ONLY."
    report = reconcile_invoice(bill, page_text=page)
    assert report["total_reconciles"] is True
    assert any("invoice value" in w for w in report["warnings"])
    # A page SUB-total never stands in - it matches a partial reading.
    assert reconcile_invoice(bill, page_text="Sub Total Amt : 99230.21")["total_reconciles"] is False


# --- a page drawn sideways -----------------------------------------------------------------

def test_a_page_drawn_sideways_reads_like_one_drawn_upright():
    import pypdf

    from tests.fixtures.invoice_pdf import build_invoice_pdf

    data = build_invoice_pdf(n_items=6)
    reader, writer = pypdf.PdfReader(io.BytesIO(data)), pypdf.PdfWriter()
    for page in reader.pages:
        page.rotate(90)
        page.transfer_rotation_to_content()
        writer.add_page(page)
    buf = io.BytesIO()
    writer.write(buf)
    sideways = buf.getvalue()

    assert upright_pdf(data) is data   # already upright: untouched
    upright = parse_invoice_pdf(data)
    turned = parse_invoice_pdf(sideways)
    assert turned and len(turned["line_items"]) == len(upright["line_items"]) == 6
    assert [_v(i, "batch_no") for i in turned["line_items"]] == [_v(i, "batch_no") for i in upright["line_items"]]


# --- long and multi-page bills, tax stated per slab ------------------------------------

def test_decimals_wrapped_to_the_next_line_are_rejoined():
    from app.services.ocr.invoice_parser import _unwrapped_number

    assert _unwrapped_number("6,576.6\n0") == "6,576.60"
    assert _unwrapped_number("15,108.\n00") == "15,108.00"
    assert _unwrapped_number("6.00\n12") == "6.00\n12"   # a whole figure, then another


def test_tax_stated_per_slab_gives_the_bills_tax_and_taxable_totals():
    from app.services.ocr.invoice_parser import _slab_tax_totals

    text = ("Add CGST 6.00 % On Taxable Value 668,913.93 40,134.82\n"
            "Add SGST 6.00 % On Taxable Value 668,913.93 40,134.82\n"
            "Add CGST 2.50 % On Taxable Value 18,500.93 462.53\n"
            "Add SGST 2.50 % On Taxable Value 18,500.93 462.53\n")
    invoice: dict = {}
    _slab_tax_totals(invoice, text)
    assert _v(invoice, "total_taxable_amount") == "687414.86"
    assert _v(invoice, "total_cgst_amount") == "40597.35"
    assert _v(invoice, "total_gst_amount") == "81194.70"


def test_a_running_total_brought_forward_is_not_part_of_the_first_item():
    header = ["DESCRIPTION", "BATCH", "QTY", "RATE", "AMOUNT"]
    row = ["Balance B/F\nOLMIN 20-CH", "AOLR25003", "30", "173.29", "220,830.00\n5,198.70"]
    item = _build_item(row, _map_columns(header), header, [])
    assert _v(item, "description") == "OLMIN 20-CH" and _v(item, "amount") == "5198.70"


def test_within_a_state_cgst_equals_sgst():
    header = ["DESCRIPTION", "BATCH", "QTY", "RATE", "AMOUNT", "CGST %"]
    # Only the CGST half read: its pair is the same.
    half = _build_item(["CELEVIDA", "B1", "10", "1199.52", "11995.20", "9.00"],
                       _map_columns(header), header, [5], interstate=False)
    assert _v(half, "sgst_percent") == "9" and _v(half, "gst_percent") == "18.0"
    # The combined rate in one column, with the full tax: split evenly.
    whole = ["AZICOX", "B2", "36", "51.10", "1839.60", "12 220.75"]
    item = _build_item(whole, _map_columns(header), header, [5], interstate=False)
    assert _v(item, "cgst_percent") == "6" and _v(item, "sgst_amount") == "110.38"


def test_a_tax_total_above_the_top_rate_is_dropped():
    from app.services.ocr.invoice_parser import _drop_impossible_tax_total

    invoice = {"total_gst_amount": _leaf("62434.99"), "total_taxable_amount": _leaf("62434.99")}
    _drop_impossible_tax_total(invoice)
    assert "total_gst_amount" not in invoice


def test_a_footer_on_the_last_product_is_cut_not_the_product():
    header = ["DESCRIPTION", "BATCH", "QTY", "RATE", "AMOUNT"]
    row = ["RIFABLOG-400 TAB SCHEME DISCOUNT 0.00 1374.28 0.00 0.00 Seventeen only", "WT/25/049",
           "10", "282.86", "2828.60"]
    item = _build_item(row, _map_columns(header), header, [])
    assert _v(item, "description") == "RIFABLOG-400 TAB"
    junk = ["(W) BRANCH A/C-810000000011373", "0 0.0", "", "", "0.00"]
    assert _build_item(junk, _map_columns(header), header, []) is None


# --- tax printed only at the foot, and what a bill leaves unsaid ------------------------

def test_tax_printed_only_in_the_summary_is_the_bills_tax():
    from app.services.ocr.invoice_parser import _summary_tax_totals

    # Alchem: beside its terms; Mahavir: "SGST VALUE" on every page.
    invoice: dict = {}
    _summary_tax_totals(invoice, "2. Goods once sold will not be taken back. SGST 6% 169.32\n"
                                 "3. Shortage within 24hrs. CGST 6% 169.32\nIGST 0\n")
    assert _v(invoice, "total_sgst_amount") == "169.32" and _v(invoice, "total_cgst_amount") == "169.32"
    invoice = {}
    _summary_tax_totals(invoice, "Taxable Value: 10,517.30 SGST% : 6.00 631.05 CGST% : 6.00 631.05\n"
                                 "SGST VALUE 1,048.41\nCGST VALUE 1,048.41\n" * 2)
    # The group heading is no total, and a page repeating the foot counts once.
    assert _v(invoice, "total_sgst_amount") == "1048.41"


def test_lines_matching_the_taxable_value_build_up_with_the_summary_tax():
    items = [{"amount": _leaf("2822.20")}]
    fields = {"line_items": items, "invoice": {
        "total_amount": _leaf("3161.00"), "total_taxable_amount": _leaf("2822.20", SUMMED_CONFIDENCE),
        "total_cgst_amount": _leaf("169.32"), "total_sgst_amount": _leaf("169.32"),
        # Summed from lines that carry no GST: never the bill's tax.
        "total_gst_amount": _leaf("0.00", SUMMED_CONFIDENCE)}}
    r = reconcile_invoice(fields)
    assert r["total_reconciles"] is True and r["total_reconciled_by"] == "lines + bill tax"


def test_a_tax_column_printing_both_halves_reads_as_the_whole_rate():
    # Troikaa: "Tax% 6.00/6.00", and "Page" caught under the last one.
    header = ["DESCRIPTION", "BATCH", "QTY", "RATE", "TAXABLE VALUE", "Tax%"]
    cols = _map_columns(header)
    from app.services.ocr.invoice_parser import _gst_columns

    item = _build_item(["TELMIKAA MT 50MG Printed Date: 28-08-2025", "BA125001", "1", "990.00", "990.00",
                        "6.00/6.00\nPage"], cols, header, _gst_columns(header), interstate=False)
    assert _v(item, "gst_percent") == "12.0" and _v(item, "cgst_percent") == "6"
    assert _v(item, "description") == "TELMIKAA MT 50MG"


def test_an_unprinted_discount_is_proven_by_the_sub_total():
    from app.services.ocr.invoice_parser import _taxable_from_sub_total

    # Cosmin: 31,640.32 in lines, 5% off it never prints, then the tax.
    invoice = {"total_amount": _leaf("35469.00"), "total_cgst_amount": _leaf("2705.24"),
               "total_sgst_amount": _leaf("2705.24")}
    _taxable_from_sub_total(invoice, "TOTAL : QTY+FREE 69 SUB TOTAL 30058.29\nROUND OFF 0.23\n")
    assert _v(invoice, "total_taxable_amount") == "30058.29"
    fields = {"line_items": [{"amount": _leaf("31640.32")}], "invoice": invoice}
    assert reconcile_invoice(fields)["total_reconciled_by"] == "lines - 5% bill discount + bill tax"
    # A sub total the tax does not build up to the grand total is not the taxable value.
    other = {"total_amount": _leaf("35469.00"), "total_cgst_amount": _leaf("2705.24")}
    _taxable_from_sub_total(other, "SUB TOTAL 30058.29\n")
    assert "total_taxable_amount" not in other


def test_the_maker_printed_under_a_product_is_its_manufacturer():
    from app.services.ocr.pdf_table import tables_from_words

    def w(text, x0, top):
        return {"text": text, "x0": x0, "x1": x0 + 6 * len(text), "top": top, "bottom": top + 8}

    words = [w("Product", 20, 10), w("Batch", 200, 10), w("Qty", 260, 10), w("Rate", 300, 10),
             w("Amount", 360, 10)]
    rows = [("DAYTINT", "25901", "10", "607.12", "6071.20"), ("KIWIFIX", "N25D01", "9", "112.88", "1015.92")]
    top = 30
    for name, batch, qty, rate, amount in rows:
        words += [w(name, 20, top), w(batch, 200, top), w(qty, 260, top), w(rate, 300, top),
                  w(amount, 360, top)]
        # Printed nearer the NEXT product than its own.
        words += [w("Mfg", 20, top + 14), w(":", 40, top + 14), w("DERMA", 50, top + 14)]
        top += 20
    table = tables_from_words(words)[0]
    header = table[0]
    first, second = (_build_item(r, _map_columns(header), header, []) for r in table[1:3])
    assert _v(first, "description") == "DAYTINT" and _v(first, "manufacturer") == "DERMA"
    assert _v(second, "description") == "KIWIFIX"


def test_a_diagonal_watermark_is_not_read_into_the_grid():
    from app.services.ocr.pdf_table import _oblique

    assert _oblique({"object_type": "char", "matrix": (0.82, 0.57, -0.57, 0.82, 0, 0)})
    assert not _oblique({"object_type": "char", "matrix": (1, 0, 0, 1, 0, 0)})
    assert not _oblique({"object_type": "char", "matrix": (0, 1, -1, 0, 0, 0)})   # sideways, not slanted


def test_a_name_spilled_into_the_batch_column_goes_back():
    header = ["PRODUCT", "BATCH", "QTY", "RATE", "AMOUNT"]
    item = _build_item(["KLINPRO PROTEIN", "POWDER NHPR25109", "20", "274.57", "5491.40"],
                       _map_columns(header), header, [])
    assert _v(item, "description") == "KLINPRO PROTEIN POWDER" and _v(item, "batch_no") == "NHPR25109"
    tax_working = ["VITALIS TAB 14580*6+6%=874.8SGST+874.8CGST,", "FB517", "20", "329.50", "6590.00"]
    assert _v(_build_item(tax_working, _map_columns(header), header, []), "description") == "VITALIS TAB"


# --- the shop, and how its bills arrive -------------------------------------------------

def test_a_buyer_name_cut_short_is_finished_from_the_shops_own_name():
    from app.services.ocr.parties import OWN_GSTINS

    token = OWN_GSTINS.set({EASTERN: "EASTERN AGENCIES HEALTHCARE PVT LTD"})
    try:
        fields = {"supplier": {"gstin": _leaf(SUPPLIER), "name": _leaf("ADROIT BIOMED LIMITED")},
                  "bill_to": {"gstin": _leaf(EASTERN), "name": _leaf("EASTERN AGENCIES")}}
        parties.resolve(fields, f"ADROIT BIOMED LIMITED {SUPPLIER}\nEASTERN AGENCIES\nGSTIN {EASTERN}\n")
        assert _v(fields["bill_to"], "name") == "EASTERN AGENCIES HEALTHCARE PVT LTD"
        # A different name the bill prints in full is the bill's own: kept.
        fields["bill_to"]["name"] = _leaf("EASTERN AGENCY (ANDHERI)")
        parties.resolve(fields, "")
        assert _v(fields["bill_to"], "name") == "EASTERN AGENCY (ANDHERI)"
    finally:
        OWN_GSTINS.reset(token)


def test_a_word_run_into_the_invoice_number_does_not_split_the_bill():
    from app.services.intake import _page_invoice_numbers

    assert _page_invoice_numbers("Invoice No : 7613021411ALKEM") == _page_invoice_numbers("Invoice No : 7613021411")


# --- columns that hold more than one thing, and totals that need choosing --------------

def test_the_amount_in_an_unheaded_last_column_is_found_by_its_arithmetic():
    # Kreit: "... CGST | Value" over "9.00 | 270.11 3001.25" - tax, then 25 x 120.05.
    header = ["PRODUCT", "BATCH", "QTY", "RATE", "SGST", "VALUE", "CGST", "VALUE"]
    row = ["ROOTCARE TABS", "DMF0689A", "25", "120.05", "9.00", "270.11", "9.00", "270.11 3001.25"]
    assert _v(_build_item(row, _map_columns(header), header, []), "amount") == "3001.25"


def test_paise_are_cut_without_swallowing_the_rupees():
    assert total_from_words("Rs. Four Thousand Three Hundred and Forty Two and Paisa Six only") == "4342.00"
    assert total_from_words("Rupees Seven Hundred Fifteen and Ninety Two paise Only") == "715.00"


def test_a_bare_gst_column_is_the_whole_rate_and_a_deal_qty_is_free_goods():
    # IPCA: "Sale | ... | D.Qty | ... | GST | IGST | CGST | SGST" over "10 | 0 | 12 | 0.00 | 146.32 | 146.32".
    from app.services.ocr.invoice_parser import _gst_columns

    header = ["Prod Code", "Product Name", "Batch No.", "Sale", "PTS", "Amount", "D.Qty",
              "GST", "IGST", "CGST", "SGST"]
    cols = _map_columns(header)
    assert header[cols["quantity"]] == "Sale" and header[cols["free_quantity"]] == "D.Qty"
    row = ["DFT05 Ace", "Revelol 50/5 10s", "DFT0525002R", "10", "243.87", "2438.70", "0",
           "12", "0.00", "146.32", "146.32"]
    item = _build_item(row, cols, header, _gst_columns(header), interstate=False)
    assert _v(item, "gst_percent") == "12.0" and _v(item, "quantity") == "10"
    assert _v(item, "description") == "Ace Revelol 50/5 10s" and _v(item, "product_code") == "DFT05"
    # "Billed Qty" ends in "dqty" too - and is the quantity.
    assert "quantity" in _map_columns(["Description", "Batch", "Billed Qty", "Taxable Value"])


def test_serial_hsn_batch_and_price_quantity_cells_are_split():
    # Blue Cross: "SR. BATCH NO. HSN CODE NO." and "PTR * QUANTITY" headings.
    header = ["SR. BATCH NO. HSN CODE NO.", "DESCRIPTION", "PTR * QUANTITY", "PTS / rate per Unit",
              "VALUE", "DISC AMOUNT", "ASSESSABLE VALUE"]
    row = ["1 30049079 AGB2513 ANGICAM", "BETA TABLETS", "28.57 800", "25.71", "20,568.00", "0.00", "20,568.00"]
    item = _build_item(row, _map_columns(header), header, [])
    assert _v(item, "description") == "ANGICAM BETA TABLETS"
    assert (_v(item, "batch_no"), _v(item, "hsn"), _v(item, "quantity")) == ("AGB2513", "30049079", "800")
    assert _v(item, "ptr") == "28.57"


def test_both_rates_and_the_amount_in_one_cell():
    # Agresco: "SGST CGST Amount" over "6.00 6.00 1240.20", and "20+2" under Qty.
    header = ["Qty.", "Product", "Batch", "PTS", "SGST CGST Amount"]
    item = _build_item(["20+2", "AGRIKOF-LS SYRUP", "SPL21052", "62.01", "6.00 6.00 1240.20"],
                       _map_columns(header), header, [], interstate=False)
    assert _v(item, "amount") == "1240.20" and _v(item, "cgst_percent") == "6"
    assert (_v(item, "quantity"), _v(item, "free_quantity")) == ("20", "2")


def test_the_total_is_never_zero_and_the_words_choose_between_figures():
    from app.services.ocr.invoice_parser import _extract_total

    # Bayer: a heading row ending "INVOICE AMOUNT" over its 0.00 cash discount.
    assert _extract_total("CGST SGST TOTAL TAX INVOICE AMOUNT\n0.00 1,080,329.70\n"
                          "NET AMOUNT PAYABLE\n1,209,969.00\n") == "1209969.00"
    # IPCA: the invoice value, then the payable after the buyer's TDS.
    assert _extract_total("Net Amount : 236867.00\nRupees: Two Lakh Thirty Six Thousand Eight Hundred "
                          "Sixty Seven only\nNet Amount Payable 236655.00\n") == "236867.00"


def test_a_credit_note_the_bill_sets_off_is_reconciled_and_said():
    # Raptakos: lines and tax 140,984.00, less credit notes 3,509.00 = 137,475.00.
    fields = {"line_items": [{"amount": _leaf("119477.86")}], "invoice": {
        "total_amount": _leaf("137475.00"), "total_cgst_amount": _leaf("10753.07"),
        "total_sgst_amount": _leaf("10753.07")}}
    r = reconcile_invoice(fields, page_text="Add:DebitNotes 0.00\nLess:CreditNotes 3,509.00-\n")
    assert r["total_reconciles"] is True and "140984.00" in r["total_reconciled_by"]
    assert any("credit note" in w for w in r["warnings"])


def test_the_gst_total_is_its_printed_heads_only_when_all_are_printed():
    from app.services.ocr.invoice_parser import _gst_total_from_heads

    invoice = {"total_gst_amount": _leaf("90.00"), "total_cgst_amount": _leaf("329.82"),
               "total_sgst_amount": _leaf("329.82")}
    _gst_total_from_heads(invoice)
    assert _v(invoice, "total_gst_amount") == "659.64"
    # Only the CGST half printed legibly (Abbott): the stated total stands.
    half = {"total_gst_amount": _leaf("15444.00"), "total_cgst_amount": _leaf("7722.00"),
            "total_sgst_amount": _leaf("7722.00", SUMMED_CONFIDENCE)}
    _gst_total_from_heads(half)
    assert _v(half, "total_gst_amount") == "15444.00"


def test_sale_returns_listed_under_the_items_are_not_bought():
    from app.services.ocr.pdf_table import _FOOTER

    assert _FOOTER.search("ADJUSTMENT DETAIL =========> I")
    assert _FOOTER.search("SALE RETURN NO. : CN00007 DATE : 31-07-2025 VALUE : 739.00")
    assert _FOOTER.search("Total of CARDIMAXX 0.00 0.00 211488.25")
    assert not _FOOTER.search("TOTAL VALUE (Rs.)")


# --- credit notes ---------------------------------------------------------------------

def test_a_credit_note_says_what_it_is_for_and_is_still_a_credit_note():
    from app.services.ocr.document_kind import kind_of_title

    assert kind_of_title("Credit Note for Non-Saleable") == "credit_note"
    assert kind_of_title("Credit note for expired goods will not be issued") is None


def test_credit_notes_in_one_pdf_are_told_apart_by_their_own_numbers():
    from app.services.intake import _page_document_numbers, _page_invoice_numbers

    page = "Original Invoice & Date : 8062547691 dtd. 05/12/2023\nSAP Doc. No.: 8510945928 DATE : 19/09/2025"
    assert _page_invoice_numbers(page) == set()
    assert _page_document_numbers(page) == {"8510945928"}


def test_the_bills_own_date_not_another_references():
    from app.services.ocr.invoice_parser import _invoice_date_match

    text = ("Cust Reference Date :08/08/2025 Customer\nORDER DATE : 15/09/2025\n"
            "PAN No -AASCA3306L DATE : 19/09/2025\n")
    assert _invoice_date_match(text).group(1) == "19/09/2025"


def test_the_figure_the_words_spell_is_the_total_whatever_its_label():
    from app.services.ocr.invoice_parser import _extract_total

    text = ("Amount in Rupees:- Fifty Seven Thousand Six Hundred Thirty Seven Only\n"
            "Grand Total 59,443.21\nLess Special Disc. 1,806.12\nTotal Amount 57,637.09\n"
            "Rounding Off -0.09\nInvoice Amount 57,637.00\n")
    assert _extract_total(text) == "57637.00"


def test_expired_goods_on_a_credit_note_are_its_point_not_a_fault():
    import datetime as dt

    from app.services.ocr.verify import verify_invoice

    fields = {"invoice": {"invoice_date": _leaf("19/09/2025")},
              "line_items": [{"description": _leaf("ZORBAX TAB"), "expiry": _leaf("Aug-2025")}]}

    def dates(kind):
        report = verify_invoice(fields, {"document_kind": kind}, today=dt.date(2025, 10, 1))
        return next(c["status"] for c in report["checks"] if c["id"] == "dates")

    assert dates("invoice") == "fail"
    assert dates("credit_note") != "fail"


# --- found in review of the whole corpus ------------------------------------------------

def test_a_bill_date_is_the_bills_date_and_another_date_is_a_last_resort():
    from app.services.ocr.invoice_parser import _invoice_date_match

    assert _invoice_date_match("PO No / Ref no: WA\nBill Date : 26-06-2025").group(1) == "26-06-2025"
    # Only an acknowledgement's date printed: better than nothing, as before.
    assert _invoice_date_match("ACK No : 122528421462142 ACK Date : 2025-09-03").group(1) == "2025-09-03"


def test_a_gross_amount_is_the_total_only_when_the_bill_names_no_other():
    from app.services.ocr.invoice_parser import _whole_bill_total

    two_pages = "REMARKS: Gross Amount : 12933.90\n...\nOverdue Net Payable 14250.00\n"
    assert _whole_bill_total(two_pages) == "14250.00"
    assert _whole_bill_total("Gross Amount 6,577.10\nLess C/N Amount: 6667.00\nAmount Payable: -90.00\n") == "6577.10"


def test_a_slabs_taxable_value_printed_beside_its_head_is_not_its_tax():
    from app.services.ocr.invoice_parser import _summary_tax_totals

    invoice = {"total_amount": _leaf("22527.00")}
    _summary_tax_totals(invoice, "CGST 2.5000 % 740.64\nCGST 9.0000 % 2831.80\nCGST 6.0000 % 16435.47\n")
    assert "total_cgst_amount" not in invoice


def test_line_tax_is_on_the_value_after_the_lines_own_discount():
    from app.services.ocr.invoice_parser import _tax_base

    # 10 x 127.29 = 1,272.90, less 10%: taxed on 1,145.61.
    before = {"amount": _leaf("1272.90"), "quantity": _leaf("10"), "pts": _leaf("127.29"),
              "discount_percent": _leaf("10.00")}
    assert _tax_base(before) == "1145.61"
    # An amount already after its discount (no price gives it) is the base.
    after = {"amount": _leaf("1145.61"), "quantity": _leaf("10"), "pts": _leaf("127.29"),
             "discount_percent": _leaf("10.00")}
    assert _tax_base(after) == "1145.61"


def test_a_reading_without_quantities_or_names_goes_to_the_ai():
    from app.services.ocr import _parse_is_trustworthy_enough

    def line(desc, batch, qty, amount):
        return {"description": _leaf(desc), "batch_no": _leaf(batch), "quantity": _leaf(qty), "amount": _leaf(amount)}

    no_qty = {"line_items": [line(f"P{i}", f"B{i}", None, "100.00") for i in range(4)], "invoice": {}}
    assert not _parse_is_trustworthy_enough(no_qty, "t")
    one_name = {"line_items": [line("SRN", "FC-2501", "25", "1197.25"), line("SRN", "AC2414", "30", "5419.20")],
                "invoice": {"total_amount": _leaf("6616.45")}}
    assert not _parse_is_trustworthy_enough(one_name, "t")
    good = {"line_items": [line(f"PRODUCT {i}", f"B{i}", "2", "100.00") for i in range(4)], "invoice": {}}
    assert _parse_is_trustworthy_enough(good, "t")


def test_one_column_for_billed_and_free_is_the_quantity_column():
    # East India: "Qty Sale+Free" over "500+100"; Linux: "Qty/ FreeQty".
    header = ["Item", "Batch No.", "Qty Sale+Free", "Taxable Amount"]
    cols = _map_columns(header)
    assert header[cols["quantity"]] == "Qty Sale+Free" and "free_quantity" not in cols
    item = _build_item(["PYRIGESIC", "P15009", "500+100", "13681.80"], cols, header, [])
    assert (_v(item, "quantity"), _v(item, "free_quantity")) == ("500", "100")
    # A heading for free goods alone stays free goods.
    assert "quantity" not in _map_columns(["Item", "Batch", "Free Qty", "Amount"])


def test_the_billed_quantity_wins_over_loose_units():
    # Cipla: "Box | Loose Qty | ... | Billed Qty." - the loose column is mostly empty.
    header = ["Batch Number", "Box", "Loose Qty", "Product Description", "Billed Qty.", "Taxable Value"]
    assert header[_map_columns(header)["quantity"]] == "Billed Qty."


# --- the last four AI-read bills --------------------------------------------------------

def test_figures_printed_with_a_currency_mark_are_still_figures():
    from app.services.ocr.pdf_table import _is_record_start

    assert _is_record_start(["OMPRAZZD", "10", "₹138.00", "₹88.71", "₹887.10"], [1, 2, 3, 4])


def test_a_name_title_over_other_columns_does_not_merge_them():
    from app.services.ocr.pdf_table import _cluster_columns

    def w(text, x0, x1, top):
        return {"text": text, "x0": x0, "x1": x1, "top": top, "bottom": top + 5}

    # Hindustan Capsule's own positions.
    main = [w("QTY", 117.8, 126.5, 154), w("PRODUCTS", 194.3, 217.9, 154), w("BATCH", 227.8, 242.3, 154),
            w("EXPDT", 255.3, 270.2, 154), w("RATE", 392.8, 403.7, 154)]
    title = [w("DESCRIPTION", 231.7, 260.4, 147)]
    labels = [c["label"] for c in _cluster_columns([(147, title), (154, main)])]
    assert "BATCH" in labels and "EXPDT" in labels


def test_the_slab_summary_table_gives_the_bills_discount_taxable_and_tax():
    from app.services.ocr.invoice_parser import _slab_summary_table

    text = ("KINDLYCHECKYOURGSTNO.PRINTED Amount SchAmt Discamt Taxable CSGT% CGSTRs. SGST% SGSTRs. TotalAmt.\n"
            "PayinWords:-THREE ₹0.00 ₹0.00 ₹0.00 ₹0.00 6.00% ₹0.00 6.00% ₹0.00 ₹0.00\n"
            "₹5,162.10 ₹0.00 ₹154.86 ₹5,007.24 6.00% ₹300.43 6.00% ₹300.43 ₹5,608.11\n"
            "ForHINDUSTAN ₹6,785.10 ₹0.00 ₹203.55 ₹6,581.55 9.00% ₹592.34 9.00% ₹592.34 ₹7,766.23\n"
            "AuthorisedSignatory\n")
    invoice: dict = {}
    _slab_summary_table(invoice, text)
    assert (_v(invoice, "total_taxable_amount"), _v(invoice, "total_discount_amount"),
            _v(invoice, "total_cgst_amount")) == ("11588.79", "358.41", "892.77")


def test_a_credit_note_page_after_an_invoice_is_its_own_document():
    from app.services.intake import _page_note_numbers

    page = "CREDITNOTEINVOICE\nCREDITNOTENO.C000084\nTelNo.-913\nDEBITNOTENO.18169\nINVOICEDATE:-05/09/2025"
    assert _page_note_numbers(page) == {"C000084"}
    from app.services.ocr.document_kind import kind_from_page

    assert kind_from_page("HINDUSTANCAPSULELLP EASTERNAGENCIES CREDITNOTEINVOICE") == "credit_note"
    assert kind_from_page("CREDITNOTENO.C000084") is None


def test_totals_and_set_offs_under_more_labels():
    from app.services.ocr.invoice_checks import credit_notes_set_off
    from app.services.ocr.invoice_parser import _extract_total

    assert _extract_total("143 TOTALPAY ₹12,799.00\n") == "12799.00"
    assert credit_notes_set_off("CN/DNAmt:- ₹575.00") == [575.0]
    assert credit_notes_set_off("TDS : 0.00 CR Notes(-) 38,642.00") == [38642.0]


def test_the_foot_on_the_last_page_is_read_but_never_a_rate():
    from app.services.ocr.invoice_parser import _labelled_totals

    invoice: dict = {}
    _labelled_totals(invoice, "page one\n...\nLess Scheme Disc 66760.00\nTotal Amount Before Tax 600,840.00\n"
                              "Cr. Total:\nCGST 9.00 % ON 37254.08 = 3352.87 :\n")
    assert _v(invoice, "total_discount_amount") == "66760.00"
    assert _v(invoice, "total_taxable_amount") == "600840.00"
    assert "total_cgst_amount" not in invoice


def test_sub_headings_sold_and_free_and_a_bonus_discount_column():
    from app.services.ocr.invoice_parser import _merge_stacked_header

    table = [["Product Description", "Batch No.", "Quantity", None, "Bonus\nDisc.", "CGST", None, "Amount"],
             ["", None, "Sold", "Free", "%", "Rate", "Amount", ""],
             ["O2", "E50845", "5000", "", "10.00", "6", "36050.40", "667,600.00"]]
    header, data_from = _merge_stacked_header(table, 0)
    cols = _map_columns(header)
    assert data_from == 2 and "free_quantity" in cols and header[cols["free_quantity"]] == "Quantity Free"
    item = _build_item(table[2], cols, header, [], interstate=False)
    assert _v(item, "discount_percent") == "10.00" and _v(item, "free_quantity") in (None, "")


def test_ai_tax_heads_that_break_the_bill_are_taken_from_the_rates():
    from app.services.ocr.invoice_checks import tax_heads_from_rates

    fields = {"invoice": {"total_taxable_amount": _leaf("2,20,464.66"), "total_amount": _leaf("2,45,734.00"),
                          "total_cgst_amount": _leaf("12,210.54"), "total_sgst_amount": _leaf("12,210.54")},
              "line_items": [{"amount": _leaf("203509.00"), "gst_percent": _leaf("12%")},
                             {"amount": _leaf("16955.66"), "gst_percent": _leaf("5%")}]}
    assert tax_heads_from_rates(fields)
    assert _v(fields["invoice"], "total_cgst_amount") == "12634.43"   # 423.89 + 12,210.54, as printed


def test_an_ai_line_given_before_its_discount_is_corrected_only_when_it_proves_out():
    from app.services.ocr.invoice_checks import amounts_net_of_own_discount

    def line(qty, rate, amount, pct=None, off=None):
        return {"quantity": _leaf(qty), "rate": _leaf(rate), "amount": _leaf(amount),
                "discount_percent": _leaf(pct), "discount_amount": _leaf(off)}

    # 1,504.22 + 1,581.40 + 100.00 = 3,185.62, and 12% GST: 3,567.89.
    fields = {"invoice": {"total_amount": _leaf("3568.00"), "total_cgst_amount": _leaf("191.14"),
                          "total_sgst_amount": _leaf("191.14")},
              "line_items": [line("22", "75.21", "1654.62", "9.09", "150.40"),    # given before its discount
                             line("10", "158.14", "1581.40", "9.09", "68.37"),   # a stray discount: no
                             line("1", "100.00", "100.00")]}
    assert amounts_net_of_own_discount(fields)
    assert [_v(i, "amount") for i in fields["line_items"]] == ["1504.22", "1581.40", "100.00"]


def test_the_buyers_own_email_is_not_a_missed_supplier_email():
    from app.services.ocr.missed_fields import find_missed

    fields = {"supplier": {}, "bill_to": {"name": _leaf("EASTERN AGENCIES HEALTHCARE PV")}}
    missed = find_missed(fields, "Email ID: purchase@easternagencies.co.in\n")
    assert not [m for m in missed if m["path"] == "supplier.email"]


def test_the_total_the_bills_own_arithmetic_points_to():
    from app.services.ocr.invoice_parser import _total_by_cross_foot

    # H&H: "Total Amount" is the lines before a discount; "Total" is subtotal + IGST.
    text = ("Total Amount ₹ 189093.15\nSubtotal ₹ 113455.88\nDiscount ₹ 75637.26\n"
            "IGST ₹ 19330.50\nTotal ₹ 132786.38\n")
    invoice = {"total_amount": _leaf("189093.15"), "total_taxable_amount": _leaf("113455.88"),
               "total_igst_amount": _leaf("19330.50")}
    _total_by_cross_foot(invoice, text)
    assert _v(invoice, "total_amount") == "132786.38"
    # Medley: the payable after credit notes, which the bill spells out, stands.
    medley = {"total_amount": _leaf("634299.00"), "total_taxable_amount": _leaf("600840.00"),
              "total_cgst_amount": _leaf("36050.40"), "total_sgst_amount": _leaf("36050.40")}
    _total_by_cross_foot(medley, "Total Amount After Tax 672,940.80\nCR Notes(-) 38,642.00\n"
                                 "Net Amount Payable 634,299.00\n")
    assert _v(medley, "total_amount") == "634299.00"
