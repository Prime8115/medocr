"""Hard inputs: every odd file a pharmacy sends is either read or refused at
upload with a plain reason - never a "Failed" scan later."""
import io

import pytest
from PIL import Image, ImageDraw

from app.config import settings
from app.services import intake
from app.services.intake import UploadRejected
from tests.conftest import register_and_login, sample_image, sample_pdf


def _photo(size=(800, 600), fmt="JPEG", exif_orientation=None) -> bytes:
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    d.rectangle((40, 40, size[0] // 2, size[1] // 3), fill="black")  # a mark at top-left
    d.text((60, size[1] // 2), "Paracip 500 Tab  100  18.50", fill="black")
    buf = io.BytesIO()
    kw = {}
    if exif_orientation:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation
        kw["exif"] = exif
    img.save(buf, format=fmt, **kw)
    return buf.getvalue()


def _reason(data, ctype="application/octet-stream", name="x"):
    with pytest.raises(UploadRejected) as exc:
        intake.prepare(data, ctype, name)
    return exc.value.reason


def _upload(client, headers, data, name="scan", ctype="application/octet-stream", **form):
    return client.post("/v1/documents/", headers=headers, files={"file": (name, data, ctype)}, data=form)


# --- the file's real type ----------------------------------------------------

@pytest.mark.parametrize("data, kind", [
    (_photo(), intake.JPEG),
    (_photo(fmt="PNG"), intake.PNG),
    (_photo(fmt="WEBP"), intake.WEBP),
    (sample_pdf(), intake.PDF),
    (b"GIF89a....", None),
    (b"hello", None),
])
def test_the_type_is_read_from_the_file_itself(data, kind):
    assert intake.sniff(data) == kind


def test_a_pdf_sent_as_a_photo_is_stored_and_read_as_a_pdf():
    out = intake.prepare(sample_pdf(), "image/jpeg", "scan.jpg")
    assert out.content_type == intake.PDF
    assert out.filename == "scan.pdf"  # a retry infers the type from this name


def test_an_unsupported_file_is_refused():
    assert _reason(b"PK\x03\x04 a zip file") == "unsupported_type"


# --- photos -------------------------------------------------------------------

def test_an_iphone_heic_photo_becomes_a_jpeg():
    pillow_heif = pytest.importorskip("pillow_heif")
    img = Image.open(io.BytesIO(_photo()))
    heif = pillow_heif.from_pillow(img)
    buf = io.BytesIO()
    heif.save(buf, quality=80)
    assert intake.sniff(buf.getvalue()) == intake.HEIC

    out = intake.prepare(buf.getvalue(), "image/heic", "IMG_0001.HEIC")
    assert out.content_type == intake.JPEG
    assert out.filename == "IMG_0001.jpg"
    assert Image.open(io.BytesIO(out.data)).size == (800, 600)


def test_a_sideways_phone_photo_is_turned_upright():
    # Orientation 6: stored landscape, to be shown turned 90 degrees.
    out = intake.prepare(_photo(size=(800, 600), exif_orientation=6), "image/jpeg", "p.jpg")
    assert Image.open(io.BytesIO(out.data)).size == (600, 800)
    assert "rotated upright" in out.notes


def test_an_upright_photo_is_kept_byte_for_byte():
    data = _photo()
    assert intake.prepare(data, "image/jpeg", "p.jpg").data == data


def test_a_huge_photo_is_scaled_down(monkeypatch):
    monkeypatch.setattr(settings, "upload_max_image_side", 1000)
    out = intake.prepare(_photo(size=(4000, 3000)), "image/jpeg", "p.jpg")
    assert Image.open(io.BytesIO(out.data)).size == (1000, 750)


def test_a_blank_photo_is_refused():
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), (12, 12, 12)).save(buf, format="JPEG")  # lens cap on
    assert _reason(buf.getvalue()) == "image_blank"


def test_a_tiny_photo_is_refused():
    assert _reason(_photo(size=(150, 120))) == "image_too_small"


def test_a_damaged_photo_is_refused():
    data = _photo()
    assert _reason(data[:300]) == "image_unreadable"


# --- PDFs ---------------------------------------------------------------------

def _encrypted(user_password: str, owner_password: str = "owner") -> bytes:
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(sample_pdf("Tax Invoice  Invoice No: A-1"))))
    writer.encrypt(user_password=user_password, owner_password=owner_password)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_a_password_protected_pdf_is_refused():
    assert _reason(_encrypted("secret")) == "pdf_password"


def test_a_pdf_locked_only_against_editing_is_unlocked():
    from pypdf import PdfReader

    out = intake.prepare(_encrypted(""), "application/pdf", "bill.pdf")
    reader = PdfReader(io.BytesIO(out.data))
    assert not reader.is_encrypted
    assert "A-1" in reader.pages[0].extract_text()


def test_a_damaged_pdf_is_repaired_when_it_can_be():
    data = sample_pdf("Tax Invoice  Invoice No: R-9")
    # Cut off the cross-reference table and trailer, as an interrupted download does.
    broken = data[: data.rindex(b"xref")]
    from pypdf import PdfReader

    out = intake.prepare(broken, "application/pdf", "bill.pdf")
    assert "repaired a damaged PDF" in out.notes
    assert "R-9" in PdfReader(io.BytesIO(out.data)).pages[0].extract_text()


def test_a_pdf_beyond_repair_is_refused():
    assert _reason(b"%PDF-1.7\n" + b"\x00garbage" * 50) == "pdf_damaged"


def _with_blank_pages(pattern: str) -> bytes:
    """'T' a page of text, 'B' a blank page."""
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for i, kind in enumerate(pattern):
        if kind == "T":
            c.drawString(40, 800, f"Tax Invoice  Invoice No: B-{i}  Paracip 500 Tab 100 18.50")
        c.showPage()
    c.save()
    return buf.getvalue()


def test_blank_pages_are_left_out():
    from pypdf import PdfReader

    out = intake.prepare(_with_blank_pages("TBTB"), "application/pdf", "bill.pdf")
    assert len(PdfReader(io.BytesIO(out.data)).pages) == 2
    assert out.pages_removed == 2


def test_a_scanned_blank_page_is_found_by_its_ink_not_its_text():
    # A scanned page has no text layer either way: ink is what tells them apart.
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawImage(ImageReader(io.BytesIO(_photo(fmt="PNG"))), 40, 400, 500, 375)
    c.showPage()
    c.showPage()  # the empty back of the sheet
    c.save()
    out = intake.prepare(buf.getvalue(), "application/pdf", "scan.pdf")
    assert out.pages_removed == 1


def test_a_pdf_of_only_blank_pages_is_refused():
    assert _reason(_with_blank_pages("BB")) == "pdf_blank"


# --- several invoices in one PDF -----------------------------------------------

def _invoice_page(number: str, label: str = "") -> str:
    return (f"SUNRISE PHARMA DISTRIBUTORS {label}\nTax Invoice\nInvoice No: {number}   Date: 12-09-2025\n"
            "Paracip 500 Tab   PC-2401   100   18.50   1850.00\nGrand Total 1942.50")


def test_separate_invoices_are_found():
    data = sample_pdf(_invoice_page("S-1"), "continued: Azithral 250 Tab 20 71.40 1428.00 " * 3,
                      _invoice_page("S-2"), _invoice_page("S-3"))
    assert intake.invoice_groups(data) == [[0, 1], [2], [3]]


def test_copies_of_one_invoice_stay_together():
    data = sample_pdf(_invoice_page("S-1", "ORIGINAL"), _invoice_page("S-1", "DUPLICATE"),
                      _invoice_page("S-1", "TRIPLICATE"))
    assert intake.invoice_groups(data) == [[0, 1, 2]]


def test_a_statement_listing_many_invoices_is_not_split():
    statement = "Statement of account\n" + "\n".join(f"Invoice No: S-{i}  1,000.00" for i in range(5))
    data = sample_pdf(statement, statement)
    assert intake.invoice_groups(data) == [[0, 1]]


def test_an_invoice_number_coming_back_later_is_not_split():
    data = sample_pdf(_invoice_page("S-1"), _invoice_page("S-2"), _invoice_page("S-1"))
    assert intake.invoice_groups(data) == [[0, 1, 2]]


def test_a_heading_running_into_the_next_label_is_not_a_number():
    # Tally: "Invoice No.   Dated" on one line, the values on the next.
    data = sample_pdf("Invoice No.   Dated\nM-544  5-Oct-25\n" + "x" * 60,
                      "Invoice No.   Dated\nM-545  6-Oct-25\n" + "x" * 60)
    assert intake.invoice_groups(data) == [[0, 1]]


def test_an_upload_of_three_invoices_makes_three_documents(client, mock_ocr):
    headers = register_and_login(client)
    data = sample_pdf(_invoice_page("S-1"), _invoice_page("S-2"), _invoice_page("S-3"))
    r = _upload(client, headers, data, "batch.pdf", "application/pdf", doc_type="invoice")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["document_ids"]) == 3 and body["document_id"] == body["document_ids"][0]
    assert "3 invoices" in body["message"]
    for doc_id in body["document_ids"]:
        doc = client.get(f"/v1/documents/{doc_id}", headers=headers).json()
        assert doc["status"] == "needs_review"


def test_splitting_can_be_switched_off(client, mock_ocr, monkeypatch):
    monkeypatch.setattr(settings, "upload_split_invoices", False)
    headers = register_and_login(client)
    data = sample_pdf(_invoice_page("S-1"), _invoice_page("S-2"))
    assert len(_upload(client, headers, data, "batch.pdf", "application/pdf").json()["document_ids"]) == 1


# --- through the API ------------------------------------------------------------

def test_a_refused_file_gets_a_plain_message_and_no_document(client, mock_ocr):
    headers = register_and_login(client)
    r = _upload(client, headers, _encrypted("secret"), "bill.pdf", "application/pdf")
    assert r.status_code == 400
    assert "password-protected" in r.json()["detail"]
    assert client.get("/v1/documents/", headers=headers).json() == []


def test_a_photo_sent_as_octet_stream_is_accepted(client, mock_ocr):
    headers = register_and_login(client)
    r = _upload(client, headers, _photo(), "scan", "application/octet-stream")
    assert r.status_code == 200, r.text


def test_blank_pages_are_mentioned_to_the_user(client, mock_ocr):
    headers = register_and_login(client)
    r = _upload(client, headers, _with_blank_pages("TB"), "bill.pdf", "application/pdf")
    assert r.json()["message"] == "1 blank page(s) were left out."


# --- the same file twice ---------------------------------------------------------

def test_the_same_file_twice_opens_the_earlier_scan(client, mock_ocr):
    headers = register_and_login(client)
    data = sample_image()
    first = _upload(client, headers, data, "rx.png", "image/png").json()
    second = _upload(client, headers, data, "rx.png", "image/png").json()
    assert not first["duplicate"]
    assert second["duplicate"] is True
    assert second["document_id"] == first["document_id"]
    assert "already scanned" in second["message"]


def test_it_can_be_scanned_again_on_purpose(client, mock_ocr):
    headers = register_and_login(client)
    data = sample_image()
    first = _upload(client, headers, data, "rx.png", "image/png").json()
    again = _upload(client, headers, data, "rx.png", "image/png", allow_duplicate="true").json()
    assert not again["duplicate"] and again["document_id"] != first["document_id"]


def test_a_failed_scan_is_not_a_duplicate(client, monkeypatch):
    from app.api import documents as documents_api

    def broken(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(documents_api, "process_document", broken)
    monkeypatch.setattr(documents_api, "fallback_payload", broken, raising=False)
    from app.services.ocr import fallback

    monkeypatch.setattr(fallback, "fallback_payload", broken)
    headers = register_and_login(client)
    data = sample_image()
    first = _upload(client, headers, data, "rx.png", "image/png").json()
    assert client.get(f"/v1/documents/{first['document_id']}", headers=headers).json()["status"] == "failed"
    second = _upload(client, headers, data, "rx.png", "image/png").json()
    assert not second["duplicate"] and second["document_id"] != first["document_id"]


def test_another_shops_scan_is_never_shown(client, mock_ocr):
    data = sample_image()
    a = register_and_login(client)
    b = register_and_login(client, email="b@shop.com", shop="Shop B")
    first = _upload(client, a, data, "rx.png", "image/png").json()
    other = _upload(client, b, data, "rx.png", "image/png").json()
    assert not other["duplicate"] and other["document_id"] != first["document_id"]
