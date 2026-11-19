"""A ruled item table whose maker cell clips its text, rebuilt without the PDF.

Laid out like Gujarat Enzyme's Soham ERP invoice (RptGSTInvoice003):
  * the maker is printed in full but clipped to its cell, so its hidden
    letters lie over the start of each product name - read by position the
    two interleaved ("EBNIOSSON" for "BIOSSON");
  * "Sr." and "Company" are printed 4pt apart, so they read as one heading
    and every maker came out "1 <maker>"; a drawn rule divides them.
The real PDF lives in the private invoice corpus, never here.
"""
from io import BytesIO

import pdfplumber
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.services.ocr.pdf_table import extract_word_tables, without_overprint

RULES = [8, 21.1, 56.5, 140, 177, 217, 243, 316, 346, 400, 452, 512]
HEADINGS = ["Sr.", "Company", "Product Description", "Packing", "HSN", "GST%", "Batch No.", "Qty",
            "P.T.S.", "Net Rate", "Amount"]
ROWS = [
    ["1", "ZENTRA ENZYMES", "BETAZOL F-1", "10 KG", "30029030", "5.00", "B2601", "1", "6750.00",
     "6547.50", "6547.50"],
    ["2", "ZENTRA ENZYMES", "NORVASTIN - 20K", "1 KG", "35079069", "18.00", "B2601", "3", "12600.00",
     "12222.00", "36666.00"],
]


def _pdf() -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    height = A4[1]
    top, head_bottom, bottom = 200, 214, 400
    c.setLineWidth(0.5)
    for x in RULES:
        c.line(x, height - top, x, height - bottom)
    c.line(RULES[0], height - top, RULES[-1], height - top)
    c.line(RULES[0], height - head_bottom, RULES[-1], height - head_bottom)
    c.setFont("Helvetica", 7)
    # Headings start just right of their rule, as the bill prints them.
    for i, label in enumerate(HEADINGS):
        c.drawString(RULES[i] + 1.5, height - top - 9, label)
    for r, row in enumerate(ROWS):
        y = head_bottom + 3 + r * 10
        for i, cell in enumerate(row):
            left, right = RULES[i], RULES[i + 1]
            c.saveState()
            clip = c.beginPath()
            clip.rect(left, height - y - 10, right - left, 10)
            c.clipPath(clip, stroke=0, fill=0)
            c.setFont("Helvetica", 8)
            c.drawString(left + 1.3, height - y - 8, cell)
            c.restoreState()
    c.showPage()
    c.save()
    return buf.getvalue()


def _table():
    with pdfplumber.open(BytesIO(_pdf())) as pdf:
        tables = extract_word_tables(without_overprint(pdf.pages[0]))
    assert tables
    return tables[0]


def test_the_maker_clipped_to_its_cell_does_not_run_into_the_name():
    table = _table()
    names = [row[table[0].index("Product Description")] for row in table[1:]]
    assert names == ["BETAZOL F-1", "NORVASTIN - 20K"]


def test_a_rule_between_sr_and_company_parts_them():
    table = _table()
    assert table[0][:3] == ["Sr.", "Company", "Product Description"]
    assert [row[0] for row in table[1:]] == ["1", "2"]
    assert all(row[1] == "ZENTRA" for row in table[1:])
