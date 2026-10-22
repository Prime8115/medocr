"""Source previews stay inside the owning shop and their document's pages."""
import io

from reportlab.pdfgen import canvas
from cryptography.fernet import Fernet

from app.config import settings
from app.models.document import Document
from app.services import original_source
from app.services.storage import storage
from tests.conftest import register_and_login, sample_image


def test_source_preview_is_owned_and_page_bounded(client, mock_ocr):
    owner = register_and_login(client)
    uploaded = client.post("/v1/documents/", headers=owner,
                           files={"file": ("receipt.png", sample_image(), "image/png")},
                           data={"doc_type": "invoice"})
    assert uploaded.status_code == 200, uploaded.text
    doc_id = uploaded.json()["document_id"]
    shown = client.get(f"/v1/documents/{doc_id}/source?page=1", headers=owner)
    assert shown.status_code == 200, shown.text
    assert shown.headers["cache-control"] == "private, no-store"
    assert shown.json()["pages"] == 1
    assert shown.json()["image"].startswith("data:image/jpeg;base64,")
    assert client.get(f"/v1/documents/{doc_id}/source?page=2", headers=owner).status_code == 422

    other = register_and_login(client, email="other@shop.com", shop="Another Shop")
    assert client.get(f"/v1/documents/{doc_id}/source?page=1", headers=other).status_code == 404


def test_two_page_pdf_source_preview_reports_both_pages(client, mock_ocr):
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=(595, 842))
    for n in (1, 2):
        pdf.drawString(40, 800, f"Invoice page {n} of 2")
        pdf.showPage()
    pdf.save()
    owner = register_and_login(client)
    uploaded = client.post("/v1/documents/", headers=owner,
                           files={"file": ("two.pdf", buf.getvalue(), "application/pdf")},
                           data={"doc_type": "invoice"})
    assert uploaded.status_code == 200, uploaded.text
    doc_id = uploaded.json()["document_id"]
    shown = client.get(f"/v1/documents/{doc_id}/source?page=2", headers=owner)
    assert shown.status_code == 200, shown.text
    assert shown.json()["page"] == 2 and shown.json()["pages"] == 2


def test_original_is_optional_and_processed_preview_still_works(client, mock_ocr, monkeypatch):
    monkeypatch.setattr(settings, "source_encryption_keys", "")
    owner = register_and_login(client)
    data = sample_image()
    uploaded = client.post("/v1/documents/", headers=owner,
                           files={"file": ("receipt.png", data, "image/png")})
    doc_id = uploaded.json()["document_id"]
    detail = client.get(f"/v1/documents/{doc_id}", headers=owner).json()
    assert detail["source_manifest"]["page_map"] == [1]
    assert detail["source_manifest"]["original_available"] is False
    assert client.get(f"/v1/documents/{doc_id}/source", headers=owner).status_code == 200
    assert client.get(f"/v1/documents/{doc_id}/source?view=original", headers=owner).status_code == 410


def test_exact_original_is_encrypted_and_rotation_keeps_it_readable(
    client, mock_ocr, monkeypatch, db_session
):
    old_key, new_key = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "source_encryption_keys", old_key)
    owner = register_and_login(client)
    data = sample_image()
    uploaded = client.post("/v1/documents/", headers=owner,
                           files={"file": ("receipt.png", data, "image/png")})
    doc_id = uploaded.json()["document_id"]
    with db_session() as db:
        ref = db.get(Document, doc_id).original_ref
    assert storage.load(ref) != data
    assert original_source.load(ref) == data
    assert client.get(f"/v1/documents/{doc_id}/source?view=original", headers=owner).status_code == 200
    monkeypatch.setattr(settings, "source_encryption_keys", f"{new_key},{old_key}")
    assert original_source.load(ref) == data
    assert client.get(f"/v1/documents/{doc_id}/source?view=original", headers=owner).status_code == 200
    try:
        original_source.load(ref, "0" * 64)
        assert False, "changed content hash must fail"
    except ValueError:
        pass
    other = register_and_login(client, email="other@shop.com", shop="Another Shop")
    assert client.get(f"/v1/documents/{doc_id}/source?view=original", headers=other).status_code == 404


def test_split_invoice_page_lineage_survives_a_removed_blank(client, mock_ocr, monkeypatch, db_session):
    monkeypatch.setattr(settings, "source_encryption_keys", Fernet.generate_key().decode())
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=(595, 842))
    pdf.drawString(40, 800, "SUNRISE DISTRIBUTOR Tax Invoice Invoice No: A-101 Date: 12-09-2025")
    pdf.drawString(40, 770, "Paracip 500 Tab 100 18.50 Grand Total 1942.50")
    pdf.showPage()
    pdf.showPage()
    pdf.drawString(40, 800, "SUNRISE DISTRIBUTOR Tax Invoice Invoice No: A-102 Date: 12-09-2025")
    pdf.drawString(40, 770, "Paracip 500 Tab 100 18.50 Grand Total 1942.50")
    pdf.showPage()
    pdf.save()
    owner = register_and_login(client)
    uploaded = client.post("/v1/documents/", headers=owner,
                           files={"file": ("batch.pdf", buf.getvalue(), "application/pdf")},
                           data={"doc_type": "invoice"})
    assert uploaded.status_code == 200, uploaded.text
    ids = uploaded.json()["document_ids"]
    assert len(ids) == 2
    manifests = [client.get(f"/v1/documents/{doc_id}", headers=owner).json()["source_manifest"]
                 for doc_id in ids]
    assert [m["page_map"] for m in manifests] == [[1], [3]]
    assert all(m["removed_pages"] == [2] and m["input_pages"] == 3 for m in manifests)
    with db_session() as db:
        assert [db.get(Document, doc_id).part_pages for doc_id in ids] == ["1", "3"]
        assert db.get(Document, ids[0]).original_ref == db.get(Document, ids[1]).original_ref
    page = client.get(f"/v1/documents/{ids[1]}/source?page=1&view=original", headers=owner)
    assert page.status_code == 200, page.text
    assert page.json()["page"] == 1 and page.json()["source_page"] == 3
    assert page.json()["source_pages"] == 3 and page.json()["pages"] == 1
