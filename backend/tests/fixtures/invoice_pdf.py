"""Build realistic digital distributor-invoice PDFs for tests.

The deterministic parser reads real PDFs, so it has to be tested against real
PDFs. Synthetic cell lists exercise the column mapping but cannot catch the
failures that actually reached users - a ruled table pdfplumber has to find, a
total printed only on the last page, and the same invoice printed three times in
one file.

Layout mirrors an Indian pharma distributor invoice:
  PRODUCT NAME | HSN | PACK | BATCH | MFG.DATE | EXP.DATE | MRP | PTR | PTR% |
  CGST % | SGST % | QTY. | FREE QTY | VALUE
"""
from io import BytesIO
from typing import List, Optional, Sequence

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

HEADER = ["PRODUCT NAME", "HSN\nCODE", "PACK", "BATCH", "MFG.\nDATE", "EXP.D\nATE",
          "MRP", "PTR", "PTR%", "CGST\n%", "SGST\n%", "QTY.", "FREE\nQTY", "VALUE"]

# Header variant that carries an explicit RATE column to the right of PTR.
HEADER_WITH_RATE = ["PRODUCT NAME", "HSN\nCODE", "PACK", "BATCH", "MFG.\nDATE", "EXP.D\nATE",
                    "MRP", "PTR", "RATE", "PTR%", "CGST\n%", "SGST\n%", "QTY.", "FREE\nQTY", "VALUE"]

_ROWS_PER_PAGE = 18


def make_row(i: int, with_rate: bool = False) -> List[str]:
    """One deterministic, internally consistent line: qty x ptr == value."""
    qty = 10 + (i % 7)
    mrp = round(20.0 + i * 1.5, 2)
    ptr = round(mrp * 0.72, 2)
    rate = round(ptr * 0.95, 2)
    unit = rate if with_rate else ptr
    value = round(qty * unit, 2)
    row = [
        f"MEDICINE {i:03d} TAB", "30049039", "10 X 10", f"BN-{i:05d}",
        "02/2025", "01/2027", f"{mrp:.2f}", f"{ptr:.2f}",
    ]
    if with_rate:
        row.append(f"{rate:.2f}")
    row += ["10.00", "6.00", "6.00", str(qty), "2" if i % 3 == 0 else "", f"{value:,.2f}"]
    return row


def rows_total(n_items: int, with_rate: bool = False) -> float:
    return round(sum(float(make_row(i, with_rate)[-1].replace(",", "")) for i in range(n_items)), 2)


def _story(copy_label: Optional[str], n_items: int, header: Sequence[str],
           with_rate: bool, total: float, stated_count: bool):
    """Flowables for one printed copy of the invoice."""
    small = ParagraphStyle("small", fontName="Helvetica", fontSize=7, leading=9)
    bold = ParagraphStyle("bold", fontName="Helvetica-Bold", fontSize=9, leading=12)

    out = []
    if copy_label:
        out.append(Paragraph(copy_label, bold))
    out.append(Paragraph("Zydus Healthcare Limited &nbsp; TAX INVOICE", bold))
    out.append(Paragraph("GSTIN: 27AAACG1895Q1ZY", small))
    out.append(Paragraph("Invoice No: 2299707688 Dt: 30.06.2025", small))
    out.append(Spacer(1, 8))

    data = [list(header)] + [make_row(i, with_rate) for i in range(n_items)]
    table = Table(data, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 6),
        ("LEADING", (0, 0), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    out.append(table)
    out.append(Spacer(1, 8))
    if stated_count:
        out.append(Paragraph(f"Total Items : {n_items}", small))
    out.append(Paragraph(f"Grand Total : {total:,.2f}", bold))
    return out


def build_invoice_pdf(n_items: int = 12, copies: int = 1, with_rate: bool = False,
                      stated_count: bool = True, label_copies: bool = True) -> bytes:
    """A digital invoice PDF.

    `copies` > 1 reproduces the GST habit of printing the same invoice as
    Original / Duplicate / Triplicate inside one file - the cause of a 143-item
    invoice arriving as 429 rows. Set `label_copies=False` for the harder case:
    repeated copies that carry no Original/Duplicate marker at all, which only
    row de-duplication can catch.
    """
    labels = ["Original for Recipient", "Duplicate for Transporter",
              "Triplicate for Supplier", "Quadruplicate - Office Copy"]
    header = HEADER_WITH_RATE if with_rate else HEADER
    total = rows_total(n_items, with_rate)

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4),
                            leftMargin=18, rightMargin=18, topMargin=18, bottomMargin=18)
    story = []
    for c in range(copies):
        label = labels[c] if (copies > 1 and label_copies) else None
        story.extend(_story(label, n_items, header, with_rate, total, stated_count))
        if c < copies - 1:
            from reportlab.platypus import PageBreak

            story.append(PageBreak())
    doc.build(story)
    return buf.getvalue()


def expected_pages_per_copy(n_items: int) -> int:
    return max(1, -(-n_items // _ROWS_PER_PAGE))
