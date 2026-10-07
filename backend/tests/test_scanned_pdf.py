"""A scanned PDF is read from its picture, never from the scanner's text layer.

A real supplier's bill (MSV Lifesciences) is a scan whose scanner added its own
hidden reading of the page - and got it wrong: a GSTIN's "Z" read as "2",
another's "2" as "Z", the date a day out. Treated as a digital PDF, that text
went to the AI, which copied the mistakes. These build the same kind of file
(a picture of the page plus an invisible, wrong text layer) with made-up
GSTINs - the repository is public, so no customer file or number is committed.
"""
import io

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

import app.services.ocr as ocr
from app.services import intake
from app.services.ocr.base import OCRProvider
from app.services.ocr.pdf_utils import extract_text_pages, image_only_pdf, is_digital_pdf, is_scanned_pdf

PRINTED = ["Tax Invoice", "Invoice No. S-1029", "Dated 6-Oct-25", "GSTIN/UIN: 27ABCDE1234F3ZY",
           "Buyer GSTIN/UIN: 27PQRST6789K1ZW", "Paracip 500 Tab  PC-2401  100  18.50  1850.00"]
# The scanner's reading of it: the mistakes the MSV bill's layer made.
SCANNER_TEXT = ["Tax lnvoice", "lnvoice No. S-1029", "Dated 5-Oct-25", "GSTIN/UlN: 27ABCDE1234F 32Y",
                "Buyer GSTIN/UIN : 27PQRST6789KIZW", "Paracip 500 Tab PC-2401 100 Slrips 18.50 1,850.00"]


def _page_picture(lines, label="") -> bytes:
    img = Image.new("RGB", (1240, 1754), "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=34)
    for i, line in enumerate(lines + ([label] if label else [])):
        d.text((80, 100 + i * 60), line, font=font, fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def scanned_pdf(pages: int = 1, filler: int = 6) -> bytes:
    """Each page: a full-page picture, and over it the scanner's invisible text."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(595, 842))
    for p in range(pages):
        c.drawImage(ImageReader(io.BytesIO(_page_picture(PRINTED, f"page {p + 1}"))), 0, 0, 595, 842)
        t = c.beginText(40, 780)
        t.setTextRenderMode(3)  # invisible, as scanners write it
        for line in SCANNER_TEXT * filler:
            t.textLine(line)
        c.drawText(t)
        c.showPage()
    c.save()
    return buf.getvalue()


def digital_pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(595, 842))
    c.drawImage(ImageReader(io.BytesIO(_page_picture(["LOGO"]))), 40, 760, 120, 60)  # a small logo
    y = 700
    for line in PRINTED * 6:
        c.drawString(40, y, line)
        y -= 14
    c.save()
    return buf.getvalue()


def test_a_scan_with_a_scanners_text_layer_is_a_scan():
    data = scanned_pdf()
    assert "27ABCDE1234F 32Y" in extract_text_pages(data)[0]  # the layer is there, and wrong
    assert is_scanned_pdf(data)
    assert not is_digital_pdf(data)


def test_a_pdf_made_by_software_is_digital_even_with_a_logo():
    data = digital_pdf()
    assert not is_scanned_pdf(data)
    assert is_digital_pdf(data)


def test_the_copy_sent_for_reading_has_no_scanner_text():
    data = scanned_pdf(pages=2)
    pictures = image_only_pdf(data)
    assert [t.strip() for t in extract_text_pages(pictures)] == ["", ""]
    from pypdf import PdfReader

    assert len(PdfReader(io.BytesIO(pictures)).pages) == 2


class RecordingProvider(OCRProvider):
    """Records what it is given to read."""

    name = "recording"

    def __init__(self):
        self.seen = []

    def classify(self, file_bytes, content_type):
        return "invoice"

    def extract(self, file_bytes, content_type, doc_type):
        self.seen.append((content_type, file_bytes))
        return {}


def test_the_ai_is_given_the_picture_never_the_scanners_text(monkeypatch):
    provider = RecordingProvider()
    monkeypatch.setattr(ocr, "get_provider", lambda: provider)

    out = ocr.process_document("d", scanned_pdf(), "application/pdf", "invoice")

    assert out["meta"]["pipeline"] == "recording"  # not the text parser
    assert provider.seen, "nothing was sent for reading"
    for content_type, data in provider.seen:
        assert content_type == "application/pdf"
        text = " ".join(extract_text_pages(data))
        assert "F 32Y" not in text and "KIZW" not in text and not text.strip()


def test_a_digital_pdf_is_still_read_from_its_text(monkeypatch):
    provider = RecordingProvider()
    monkeypatch.setattr(ocr, "get_provider", lambda: provider)

    ocr.process_document("d", digital_pdf(), "application/pdf", "invoice")

    assert provider.seen[0][0] == "text/plain"
    assert b"27ABCDE1234F3ZY" in provider.seen[0][1]


def test_a_scans_invoice_numbers_never_split_it():
    # Two scanned pages; the scanner's reading of the number could differ
    # between copies, so a scan is never split on it.
    assert intake.invoice_groups(scanned_pdf(pages=2)) == [[0, 1]]


def test_manual_entry_reads_a_scan_with_local_ocr_not_its_layer():
    import shutil

    import pytest

    from app.services.ocr.fallback import document_text

    if shutil.which("tesseract") is None:
        pytest.skip("tesseract not installed")
    text, source, _pages = document_text(scanned_pdf(), "application/pdf")
    assert source == "ocr"
    assert "F 32Y" not in text and "KIZW" not in text
