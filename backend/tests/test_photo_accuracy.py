"""Sparse scans and pack notation must not silently lose clinical/inventory data."""
import io

import pytest
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.services import intake
from app.services.ocr import _build_units, _extract_chunked
from app.services.ocr.pdf_utils import page_count
from app.services.ocr.postprocess import normalize_quantity, postprocess_fields


def sparse_scan():
    stream = io.BytesIO()
    pdf = canvas.Canvas(stream, pagesize=(595, 842))
    for n, shade in enumerate((0, 205)):
        image = Image.new("RGB", (1240, 1754), "white")
        draw = ImageDraw.Draw(image)
        draw.text((80, 120), f"MEDICINE PAGE {n + 1}  15's tablets  Batch B{n + 1}",
                  font=ImageFont.load_default(size=23), fill=(shade,) * 3)
        pdf.drawImage(ImageReader(image), 0, 0, 595, 842)
        pdf.showPage()
    pdf.save()
    return stream.getvalue()


def test_faint_sparse_second_scan_page_is_never_removed():
    prepared = intake.prepare(sparse_scan(), "application/pdf", "scan.pdf")
    assert page_count(prepared.data) == 2
    assert prepared.pages_removed == 0


def test_two_scan_pages_have_two_explicit_reading_attempts():
    data = sparse_scan()
    units, total, _ = _build_units(data, "application/pdf")
    assert total == 2
    assert [page_count(part) for part, _, _ in units] == [1, 1]
    assert [n for _, _, n in units] == [1, 1]


@pytest.mark.parametrize("raw", ["15's", "15’s tablets", "15'S TAB", "1 x 15", "10x10 tablets"])
def test_pack_size_is_not_a_numeric_quantity(raw):
    assert normalize_quantity(raw) is None


@pytest.mark.parametrize("raw,want", [("15 tablets", 15), ("2 strips", 2), ("1,000 nos", 1000), ("0", 0)])
def test_actual_billed_quantity_remains_numeric(raw, want):
    assert normalize_quantity(raw) == want


def test_edit_to_pack_text_clears_an_old_numeric_companion():
    fields = {"line_items": [{"quantity": {"value": "15's tablets", "normalized": 15}}]}
    result = postprocess_fields("invoice", fields)
    assert "normalized" not in result["line_items"][0]["quantity"]
    assert result["line_items"][0]["quantity"]["value"] == "15's tablets"
