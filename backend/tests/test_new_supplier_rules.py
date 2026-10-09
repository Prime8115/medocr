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
