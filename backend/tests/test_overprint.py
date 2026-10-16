"""Text one cell runs on over the next cells, and line values printed in a
shape the stock software cannot take."""
from io import BytesIO

import pytest

from app.services.ocr.postprocess import postprocess_fields, tidy_line


def _page_pdf(draw) -> bytes:
    pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(600, 400))
    c.setFont("Helvetica", 8)
    draw(c)
    c.save()
    return buf.getvalue()


def _words(data: bytes, filtered: bool) -> list:
    import pdfplumber

    from app.services.ocr.pdf_table import WORD_TOLERANCE, without_overprint

    with pdfplumber.open(BytesIO(data)) as pdf:
        page = without_overprint(pdf.pages[0]) if filtered else pdf.pages[0]
        return [w["text"] for w in page.extract_words(x_tolerance=WORD_TOLERANCE)]


def _run_on_maker(c):
    """The batch drawn first, then the maker's whole name from its own
    column straight across the batch - the way IPCA's software prints it."""
    for n, batch in enumerate(("AB1234", "CD5678", "EF9012")):
        y = 300 - 12 * n
        c.drawString(120, y, "Medicine 10s")
        c.drawString(204, y, batch)
        c.drawString(260, y, "08/27")
        c.drawString(170, y, "ACME CURE HEALTHCARE PVT LTD")


def test_run_on_text_interleaves_with_the_cell_it_covers():
    words = _words(_page_pdf(_run_on_maker), filtered=False)
    assert "AB1234" not in words      # read by position, the letters interleave


def test_each_cell_keeps_its_own_text():
    words = _words(_page_pdf(_run_on_maker), filtered=True)
    for batch in ("AB1234", "CD5678", "EF9012"):
        assert batch in words
    assert words.count("08/27") == 3
    assert "ACME" in words            # the maker keeps what sits in its own cell
    assert "HEALTHCARE" not in words  # ...and loses what it printed over others


def test_a_page_without_overprint_is_left_as_it_is():
    import pdfplumber

    from app.services.ocr.pdf_table import without_overprint

    def plain(c):
        c.drawString(120, 300, "Medicine 10s")
        c.drawString(204, 300, "AB1234")
        c.drawString(260, 300, "08/27")

    with pdfplumber.open(BytesIO(_page_pdf(plain))) as pdf:
        assert without_overprint(pdf.pages[0]) is pdf.pages[0]


def test_bold_faked_by_drawing_twice_is_not_overprint():
    import pdfplumber

    from app.services.ocr.pdf_table import without_overprint

    def bold(c):
        for n in range(3):
            for dx in (0, 0.4):
                c.drawString(120 + dx, 300 - 12 * n, "EASTERN AGENCIES HEALTHCARE")

    with pdfplumber.open(BytesIO(_page_pdf(bold))) as pdf:
        assert without_overprint(pdf.pages[0]) is pdf.pages[0]


# --- line values ---------------------------------------------------------------------

def _f(value):
    return {"value": value, "confidence": 0.9}


def test_two_dates_in_the_expiry_cell_are_mfg_and_expiry():
    item = {"expiry": _f("03/25 08/26")}
    tidy_line(item)
    assert item["expiry"]["value"] == "08/26"
    assert item["mfg_date"]["value"] == "03/25"


def test_the_later_date_is_the_expiry_in_either_order():
    item = {"expiry": _f("Aug-27 Sep-25"), "mfg_date": _f("09/2025")}
    tidy_line(item)
    assert item["expiry"]["value"] == "Aug-27"
    assert item["mfg_date"]["value"] == "09/2025"   # a date already read is kept


def test_one_date_or_a_spaced_date_is_left_alone():
    for value in ("08/26", "May/2 7", "MAY- 28", "08/26 08/26"):
        item = {"expiry": _f(value)}
        tidy_line(item)
        assert item["expiry"]["value"] == value and "mfg_date" not in item


def test_a_batch_printed_with_a_long_dash_gets_a_hyphen():
    fields = {"line_items": [{"batch_no": _f("TPG — 928")}, {"batch_no": _f("TPG–929")}]}
    postprocess_fields("invoice", fields)
    assert [i["batch_no"]["value"] for i in fields["line_items"]] == ["TPG - 928", "TPG-929"]


# --- column edges on a bill without ruled lines ---------------------------------------

def _w(text, x0, x1, top):
    return {"text": text, "x0": x0, "x1": x1, "top": top, "bottom": top + 7, "upright": True}


def test_a_name_wider_than_its_heading_stays_in_its_column():
    """JB-style: the heading "Description" is narrow, names run past the
    midpoint to "Batch"; the empty channel before the batches is the edge."""
    from app.services.ocr.pdf_table import tables_from_words

    words = [_w("Description", 36, 89, 100), _w("Batch", 175, 200, 100), _w("Qty", 240, 255, 100),
             _w("Rate", 280, 300, 100), _w("Amount", 320, 350, 100)]
    for n, (name, batch) in enumerate((("Paracetamol 500mg Tablets", "AB123"),
                                        ("Cetirizine 10mg Tablets", "CD456"))):
        top = 120 + 12 * n
        x = 36
        for part in name.split():
            width = 5 * len(part)
            words.append(_w(part, x, x + width, top))
            x += width + 3
        words += [_w(batch, 176, 205, top), _w("10", 245, 252, top), _w("12.50", 282, 300, top),
                  _w("125.00", 322, 350, top)]
    tables = tables_from_words(words)
    rows = [r for t in tables for r in t]
    body = [r for r in rows if any("AB123" in (c or "") or "CD456" in (c or "") for c in r)]
    assert body, rows
    for row in body:
        assert any((c or "").endswith("Tablets") for c in row), row
        assert any((c or "").strip() in ("AB123", "CD456") for c in row), row


def test_a_block_drawn_twice_a_little_apart_is_not_overprint():
    """Overseas draws its party block twice, the second copy shifted by a few
    letters: the two copies are one value."""
    import pdfplumber

    from app.services.ocr.pdf_table import without_overprint

    def twice(c):
        for n in range(3):
            c.drawString(120, 300 - 12 * n, "Name : EASTERN AGENCIES HEALTHCARE PRIVATE LIMITED")
            c.drawString(131, 300 - 12 * n, "Name : EASTERN AGENCIES HEALTHCARE PRIVATE LIMITED")

    with pdfplumber.open(BytesIO(_page_pdf(twice))) as pdf:
        assert without_overprint(pdf.pages[0]) is pdf.pages[0]
