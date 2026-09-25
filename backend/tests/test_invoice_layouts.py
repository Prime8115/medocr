"""The parser must cope with invoices from suppliers we have never seen.

We only hold four real invoices, and tuning to exactly those four is how a
parser ends up working for four pharmacies and nobody else. These are synthetic
layouts built to differ in the ways real Indian pharma invoices differ: the
heading each supplier chooses for the same column, whether the table is ruled,
portrait or landscape, how many columns there are, and which price columns exist.

Every case here failed at least once during development. "B.No." as a batch
heading rejected an entire invoice.
"""
import io

import pytest

pytest.importorskip("reportlab")

from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import A4, landscape  # noqa: E402
from reportlab.lib.styles import ParagraphStyle  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.services.ocr import process_document  # noqa: E402


def build_invoice(header, rows, ruled=True, portrait=False, total="0.00"):
    """A one-page invoice with the given column headings and rows."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=(A4 if portrait else landscape(A4)),
        leftMargin=18, rightMargin=18, topMargin=18, bottomMargin=18,
    )
    small = ParagraphStyle("s", fontName="Helvetica", fontSize=7, leading=9)
    bold = ParagraphStyle("b", fontName="Helvetica-Bold", fontSize=9, leading=12)
    story = [
        Paragraph("ACME PHARMA DISTRIBUTORS PVT LTD", bold),
        Paragraph("GSTIN No : 27AABCA1234B1ZX", small),
        Paragraph("Invoice No: INV-9001 &nbsp; Invoice Date: 12.08.2025", small),
        Paragraph("Bill to Party : SOME CHEMIST", small),
        Spacer(1, 8),
    ]
    table = Table([list(header)] + [list(r) for r in rows], repeatRows=1, hAlign="LEFT")
    style = [
        ("FONTSIZE", (0, 0), (-1, -1), 6),
        ("LEADING", (0, 0), (-1, -1), 7),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ]
    if ruled:
        style.append(("GRID", (0, 0), (-1, -1), 0.4, colors.black))
    table.setStyle(TableStyle(style))
    story += [table, Spacer(1, 8), Paragraph(f"Grand Total : {total}", bold)]
    doc.build(story)
    return buf.getvalue()


def _rows(n, template):
    return [[c.format(i=i) for c in template] for i in range(n)]


# (name, header, row template, ruled, portrait, index of the amount column)
LAYOUTS = [
    (
        "classic ruled landscape",
        ["Particulars", "Batch No", "Exp Dt", "Qty", "MRP", "Rate", "Amount"],
        ["MED {i}", "B{i}", "01/2027", "10", "50.00", "40.00", "400.00"],
        True, False, -1,
    ),
    (
        # "B.No." rejected a whole invoice before the batch spellings were widened.
        "B.No. unruled portrait",
        ["Product Name", "B.No.", "Expiry", "Quantity", "M.R.P.", "Price", "Net Amt"],
        ["DRUG {i}", "L{i}", "05/2028", "12", "80.00", "60.00", "720.00"],
        False, True, -1,
    ),
    (
        "no HSN, no GST, free qty",
        ["Item Name", "Batch", "Exp", "Qty", "Free", "MRP", "PTR", "Value"],
        ["TAB {i}", "X{i}", "03/2027", "20", "2", "30.00", "22.00", "440.00"],
        True, False, -1,
    ),
    (
        "spelled-out headings",
        ["Description of Goods", "Batch Number", "Expiry Date", "Billed Qty",
         "Maximum Retail Price", "Price To Retailer", "Taxable Value"],
        ["CAP {i}", "C{i}", "09/2027", "15", "100.00", "70.00", "1050.00"],
        True, False, -1,
    ),
    (
        "lot no, net rate, unruled",
        ["Goods", "Lot No", "Exp.", "Qty.", "MRP", "Net Rate", "Amount"],
        ["SYP {i}", "S{i}", "11/2026", "8", "45.00", "33.00", "264.00"],
        False, False, -1,
    ),
    (
        "Bt.No with a pack column",
        ["Particulars", "Pack", "Bt.No", "Exp", "Qty", "MRP", "PTS", "Taxable Amt"],
        ["INJ {i}", "1x5", "K{i}", "07/2027", "6", "200.00", "140.00", "840.00"],
        True, False, -1,
    ),
    (
        "discount column",
        ["Goods Description", "Batch#", "Expiry", "Qty", "Free Qty", "MRP", "Rate",
         "Disc %", "Amount"],
        ["OINT {i}", "O{i}", "02/2028", "10", "1", "60.00", "45.00", "5.00", "427.50"],
        True, False, -1,
    ),
    (
        "the bare minimum an invoice can carry",
        ["Item", "Batch", "Exp", "Qty", "Amount"],
        ["POW {i}", "P{i}", "12/2026", "4", "160.00"],
        True, False, -1,
    ),
]


@pytest.mark.parametrize(
    "name,header,template,ruled,portrait,amount_col",
    LAYOUTS,
    ids=[layout[0] for layout in LAYOUTS],
)
def test_layout_is_read_without_the_ai(name, header, template, ruled, portrait, amount_col):
    """Every one of these must be read exactly, by the free parser.

    Falling through to the AI is not a pass: it costs money per page and is not
    exact on the numbers that become stock values.
    """
    rows = _rows(5, template)
    total = f"{sum(float(r[amount_col]) for r in rows):.2f}"
    result = process_document(
        "layout", build_invoice(header, rows, ruled, portrait, total),
        "application/pdf", doc_type="invoice",
    )
    meta = result["meta"]
    assert meta["pipeline"] == "pdf_parser", f"{name} fell through to the AI"
    assert meta["item_count"] == 5
    assert meta["total_reconciles"] is True
    assert [w for w in meta["warnings"] if " " in w] == []

    for item in result["fields"]["line_items"]:
        assert item["description"]["value"]
        assert item["quantity"]["value"]
        assert item["amount"]["value"]


def test_a_very_wide_invoice_keeps_every_column():
    """22 columns, which is what the larger distributors actually print."""
    header = ["Sr", "Code", "Product Name", "HSN", "Pack", "Batch", "Mfg Dt", "Exp Dt",
              "UOM", "Qty", "Free", "Tot Qty", "MRP", "PTS", "PTR", "Rate", "Disc%",
              "Disc Amt", "Taxable Value", "CGST%", "SGST%", "Sch%"]
    rows = _rows(5, ["{i}", "C{i}", "CAP {i}", "30049099", "10x10", "B{i}", "01/2025",
                     "01/2028", "STP", "10", "2", "12", "90.00", "60.00", "66.00",
                     "60.00", "0.00", "0.00", "600.00", "6.00", "6.00", "0.00"])
    result = process_document(
        "wide", build_invoice(header, rows, total="3000.00"), "application/pdf",
        doc_type="invoice",
    )
    assert result["meta"]["item_count"] == 5
    item = result["fields"]["line_items"][0]

    # The columns worth naming are named...
    for field in ("product_code", "pack", "batch_no", "mfg_date", "expiry", "uom",
                  "quantity", "free_quantity", "total_quantity", "mrp", "pts", "ptr",
                  "amount", "hsn"):
        assert item[field]["value"], f"{field} was dropped"
    # ...and the serial number, which has no field, is still kept.
    assert any((e.get("label") or "").lower().startswith("sr") for e in item["extras"])


def test_malformed_input_never_crashes():
    """A bad upload must fail cleanly, not take the worker down.

    Anything unreadable falls through to the AI path; if that also fails the
    document is marked failed and the user is offered a retry.
    """
    from pypdf import PdfWriter

    from app.config import settings
    from app.services.ocr import OCRError

    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    writer.write(buf)
    blank = buf.getvalue()

    inputs = [b"", b"%PDF-1.4 not really a pdf", blank, blank[:200], b"\xff\xd8\xff\xe0" + b"\x00" * 200]
    original = settings.allow_mock_ocr
    settings.allow_mock_ocr = True
    try:
        for data in inputs:
            try:
                process_document("edge", data, "application/pdf", doc_type="invoice")
            except OCRError:
                pass  # a clean, reportable failure is fine
    finally:
        settings.allow_mock_ocr = original
