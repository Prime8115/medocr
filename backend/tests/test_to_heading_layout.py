"""A GST invoice laid out like Soham ERP's (rptgstinvoice039), rebuilt without the PDF.

What went wrong on the real bill, each pinned here:
  * the buyer's column is headed only "To," - read as the supplier's block, so
    the buyer's PAN swapped the supplier's GSTIN for the buyer's, the sale
    looked intra-state and every line got CGST/SGST as well as IGST;
  * the supplier's name wraps before "SOLUTIONS PRIVATE LIMITED";
  * the table is ruled, and names start just right of the Company/Product
    rule - the midpoint between the headings gave "PK" to the company;
  * the IGST total is printed as "IGST Amt." with no "Total" in its label.
The real PDF lives in the private invoice corpus, never here.
"""
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.services.ocr.invoice_header import gstin_is_valid
from app.services.ocr.invoice_parser import parse_invoice_pdf

_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _gstin(body14: str) -> str:
    """A GSTIN with its real check character."""
    total = 0
    for i, ch in enumerate(body14):
        product = _CHARS.index(ch) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return body14 + _CHARS[(36 - total % 36) % 36]


SUPPLIER = _gstin("24AABCT1234K1Z")   # Gujarat
BUYER = _gstin("29AABCB5678L1Z")      # Karnataka: an inter-state sale

# Column rules (x) and the heading printed in each column.
RULES = [10, 32, 62, 168, 204, 237, 267, 306, 326, 350, 392, 428, 466, 516, 572]
HEADINGS = ["Sr.", "Company", "Product Description", "Pack", "HSN", "MRP", "Batch No.", "Exp", "Qty",
            "P.T.R.", "Net Rate", "GST %", "Taxable Amt.", "Amount"]
ROWS = [
    ["1", "ACME", "PK ALPHACET 10", "1X10 TAB", "3004", "95.00", "PK01", "03-26", "100", "25.00",
     "24.75", "12", "2475.00", "2475.00"],
    ["2", "ACME", "PK BETAMOL 20", "1X15 STRIP", "3004", "145.00", "PK12", "03-28", "15", "26.00",
     "25.74", "12", "386.10", "386.10"],
]


def _pdf() -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    height = A4[1]

    def text(x, top, s, size=7.8):
        c.setFont("Helvetica", size)
        c.drawString(x, height - top - size, s)

    # Header: supplier left (name wrapped), title box middle, buyer right under "To,".
    text(18, 12, "ALPHA BETA GAMMA NORTH ERP")
    text(269, 13, "TAX INVOICE")
    text(338, 12, "To,")
    text(470, 12, "ORIGINAL FOR RECIPIENT")
    text(18, 23, "SOLUTIONS PRIVATE LIMITED")
    text(338, 23, "OMEGA MEDICALS PVT LTD")
    text(18, 34, "S-16/181 SHANTI APPT, NEAR TOWER")
    text(261, 34, "Cash/Debit Memo")
    text(338, 34, "7TH BLOCK, KUVEMPU LAYOUT")
    text(18, 45, "GANDHINAGAR-383315, Gujarat", 6)
    text(338, 45, "BENGALURU-562160, Karnataka", 6)
    text(18, 56, "PAN : AABCT1234K")
    text(261, 56, "Bill No. : D-00004")
    text(338, 56, "PAN : AABCB5678L")
    text(18, 67, f"GSTIN : {SUPPLIER} , State Code : 24")
    text(261, 67, "Date 16-10-2023")
    text(338, 67, f"GSTIN : {BUYER}, State Code : 29")

    # Ruled item table.
    top, head_bottom, bottom = 105, 118, 300
    c.setLineWidth(0.5)
    for x in RULES:
        c.line(x, height - top, x, height - bottom)
    c.line(RULES[0], height - top, RULES[-1], height - top)
    c.line(RULES[0], height - head_bottom, RULES[-1], height - head_bottom)
    c.line(RULES[0], height - bottom, RULES[-1], height - bottom)
    for i, label in enumerate(HEADINGS):
        if label:
            left, right = RULES[i], RULES[i + 1]
            c.setFont("Helvetica", 7)
            c.drawCentredString((left + right) / 2, height - top - 9, label)
    for r, row in enumerate(ROWS):
        y = head_bottom + 4 + r * 11
        for i, cell in enumerate(row):
            if not cell:
                continue
            left, right = RULES[i], RULES[i + 1]
            c.setFont("Helvetica", 7)
            if i in (1, 2, 3, 6, 7):
                c.drawString(left + 1.4, height - y - 7, cell)
            else:
                c.drawRightString(right - 1.5, height - y - 7, cell)

    # Footer: item amount, then the IGST head and its tax-slab total row.
    text(18, 320, "Total BOX 0.00")
    text(400, 330, "Item Amount")
    text(520, 330, "2861.10")
    text(400, 341, "IGST Amt.")
    text(520, 341, "343.33")
    text(400, 352, "NET INVOICE VALUE :")
    text(520, 352, "3204.43")
    text(18, 364, "Tax Type Taxable Value IGST % Value")
    text(18, 375, "GST 12 % 2861.10 12.00 343.33")
    text(18, 386, "Total 2861.10 343.33")
    c.showPage()
    c.save()
    return buf.getvalue()


def _v(node, key):
    return ((node or {}).get(key) or {}).get("value")


def test_the_gstins_are_valid():
    assert gstin_is_valid(SUPPLIER) and gstin_is_valid(BUYER)


def test_a_buyer_column_headed_only_to_is_the_buyer():
    out = parse_invoice_pdf(_pdf())
    assert _v(out["supplier"], "gstin") == SUPPLIER
    assert _v(out["bill_to"], "gstin") == BUYER
    assert _v(out["bill_to"], "name") == "OMEGA MEDICALS PVT LTD"


def test_a_supplier_name_wrapped_before_its_suffix_is_whole():
    out = parse_invoice_pdf(_pdf())
    assert _v(out["supplier"], "name") == "ALPHA BETA GAMMA NORTH ERP SOLUTIONS PRIVATE LIMITED"
    address = _v(out["supplier"], "address") or ""
    assert "Memo" not in address and "D-00004" not in address


def test_names_start_at_the_drawn_rule_not_the_heading_midpoint():
    items = parse_invoice_pdf(_pdf())["line_items"]
    assert [_v(i, "description") for i in items] == ["PK ALPHACET 10", "PK BETAMOL 20"]
    assert [_v(i, "manufacturer") for i in items] == ["ACME", "ACME"]
    assert [_v(i, "batch_no") for i in items] == ["PK01", "PK12"]
    assert [_v(i, "quantity") for i in items] == ["100", "15"]
    assert [_v(i, "ptr") for i in items] == ["25.00", "26.00"]


def test_an_inter_state_bill_carries_igst_only():
    out = parse_invoice_pdf(_pdf())
    for item in out["line_items"]:
        assert float(_v(item, "igst_percent")) == 12
        assert not _v(item, "cgst_percent") or float(_v(item, "cgst_percent")) == 0
    assert _v(out["invoice"], "total_igst_amount") == "343.33"
    assert not _v(out["invoice"], "total_cgst_amount")


# --- a second AI reading must not replace rows the bill itself confirms ------------

def _result(rows):
    return {"fields": {"line_items": [
        {"quantity": {"value": q}, "rate": {"value": r}, "amount": {"value": a}} for q, r, a in rows]}}


def test_rows_matching_the_printed_item_amount_are_kept():
    from app.services.ocr import _table_matches_printed_item_amount as keep

    rows = [("100", "24.75", "2475.00"), ("15", "25.74", "386.10")]
    page = "Item Amount\n2861.10\nAdd Other 520.00\nNET INVOICE VALUE : 3,724.43"
    assert keep(_result(rows), "Item Amount : 2861.10") is True
    # Freight and other charges after the item amount do not unsettle the rows.
    assert keep(_result(rows), page) is True
    # A line whose quantity x rate is not its amount is not confirmed.
    assert keep(_result([("100", "24.75", "2400.00"), ("15", "25.74", "461.10")]), "Item Amount 2861.10") is False
    # Rows that do not add up to the printed item amount are not confirmed.
    assert keep(_result(rows[:1]), "Item Amount 2861.10") is False
    # Two different item amounts on the page: no single figure to trust.
    assert keep(_result(rows), "Item Amount 2861.10\nItem Amount 2475.00") is False


def test_an_igst_footer_without_total_in_its_label_is_the_bills_igst():
    from app.services.ocr.invoice_parser import _printed_igst_total

    assert _printed_igst_total("IGST Amt. 343.33\nTotal 2861.10 343.33") == "343.33"
    # Without the slab total row to confirm it, it may be a line's own tax.
    assert _printed_igst_total("IGST Amt. 343.33") is None
    # A bill that also prints CGST/SGST is not read as IGST-only.
    assert _printed_igst_total("CGST 171.66 SGST 171.67\nIGST Amt. 343.33\nTotal 2861.10 343.33") is None
