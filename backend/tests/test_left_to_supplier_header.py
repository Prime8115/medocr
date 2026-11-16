"""A full-width seller letterhead over a left-hand 'To,' buyer box."""
from io import BytesIO

import pdfplumber
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.services.ocr.invoice_header import extract_references, party_regions
from app.services.ocr.invoice_parser import _extract_header_meta, _supplier_details, supplier_region_lines


def _page():
    buffer = BytesIO()
    sheet = canvas.Canvas(buffer, pagesize=A4)
    height = A4[1]

    def write(x, top, value, size=9):
        sheet.setFont("Helvetica", size)
        sheet.drawString(x, height - top - size, value)

    write(200, 20, "FALCON ENZYME", 14)
    write(10, 44, "9 GARDEN ROAD, AHMEDABAD-380013")
    write(10, 90, "GSTIN : 24AAAAA0000A1Z5")
    sheet.line(297, height - 106, 297, height - 201)
    write(10, 108, "To,")
    write(300, 108, "Bill No. : 0001-001")
    write(10, 121, "NOVA TRADING")
    write(300, 121, "Terms : 20 Days")
    write(10, 134, "17 GARDEN SOCIETY")
    write(10, 147, "NEAR CENTRAL ROAD, PALDI")
    write(10, 173, "GSTIN : 24BBBBB1111B1Z5")
    sheet.save()
    return pdfplumber.open(BytesIO(buffer.getvalue()))


def test_left_to_buyer_does_not_replace_the_seller_or_absorb_references():
    with _page() as pdf:
        page = pdf.pages[0]
        parties = party_regions(page)
        supplier = supplier_region_lines(page)
        parsed = _extract_header_meta(page.extract_text(), supplier, parties)
    assert "Terms" not in parties["bill_to"] and "Bill No" not in parties["bill_to"]
    assert parsed["supplier"]["name"]["value"] == "FALCON ENZYME"
    assert parsed["supplier"]["gstin"]["value"] == "24AAAAA0000A1Z5"
    assert parsed["bill_to"]["name"]["value"] == "NOVA TRADING"
    assert parsed["bill_to"]["gstin"]["value"] == "24BBBBB1111B1Z5"
    assert parsed["bill_to"]["address"]["value"].startswith("17 GARDEN SOCIETY")


def test_empty_lr_does_not_take_the_next_address_word():
    assert extract_references("L.R.No. :\nNEAR CENTRAL ROAD")["lr_no"] is None


def test_an_address_fragment_without_a_supplier_gstin_is_not_a_company_name():
    assert _supplier_details([(7.0, "Name & Address of"), (7.0, "CORONA"),
                              (7.0, "MUMBAI NASHIK")])[0] is None
