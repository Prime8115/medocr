"""Source previews stay inside the owning shop and their document's pages."""
import io

from reportlab.pdfgen import canvas

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
