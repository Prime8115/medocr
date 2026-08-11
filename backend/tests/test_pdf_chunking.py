"""Chunked processing of long multi-page PDFs (e.g. distributor invoices)."""
import io

from pypdf import PdfWriter

from app.config import settings
from app.services.ocr import _merge_fields, process_document
from app.services.ocr.pdf_utils import page_count, split_pdf


def _blank_pdf(pages: int) -> bytes:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=300, height=300)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_page_count_and_split():
    data = _blank_pdf(9)
    assert page_count(data) == 9
    chunks = split_pdf(data, 4)
    assert [page_count(c) for c in chunks] == [4, 4, 1]


def test_split_returns_single_when_small():
    data = _blank_pdf(2)
    assert split_pdf(data, 4) == [data]


def test_merge_concatenates_line_items():
    a = {"supplier": {"name": {"value": "Zydus"}}, "invoice": {"invoice_no": {"value": "INV1"}},
         "line_items": [{"description": {"value": "A"}}]}
    b = {"supplier": {"name": {"value": ""}}, "invoice": {"invoice_no": {"value": ""}},
         "line_items": [{"description": {"value": "B"}}, {"description": {"value": "C"}}]}
    m = _merge_fields("invoice", a, b)
    assert len(m["line_items"]) == 3
    assert m["supplier"]["name"]["value"] == "Zydus"      # header kept from first
    assert m["invoice"]["invoice_no"]["value"] == "INV1"


def test_merge_medications_for_prescription():
    a = {"medications": [{"name": {"value": "Para"}}]}
    b = {"medications": [{"name": {"value": "Amox"}}]}
    m = _merge_fields("prescription", a, b)
    assert len(m["medications"]) == 2


def test_process_multipage_pdf_merges_all_chunks(monkeypatch):
    """A 12-page PDF with chunk size 4 => 3 chunks; mock returns 1 item each
    => merged invoice has 3 line items."""
    monkeypatch.setattr(settings, "allow_mock_ocr", True)
    monkeypatch.setattr(settings, "ocr_pdf_chunk_pages", 4)

    data = _blank_pdf(12)
    result = process_document("doc_x", data, "application/pdf", doc_type="invoice")

    assert result["doc_type"] == "invoice"
    assert result["meta"]["pages"] == 12
    # Mock invoice yields 1 line item per chunk; 3 chunks => 3 items merged.
    assert len(result["fields"]["line_items"]) == 3
    assert result["meta"]["item_count"] == 3


def test_small_pdf_single_pass(monkeypatch):
    monkeypatch.setattr(settings, "allow_mock_ocr", True)
    monkeypatch.setattr(settings, "ocr_pdf_chunk_pages", 4)
    data = _blank_pdf(2)
    result = process_document("doc_y", data, "application/pdf", doc_type="invoice")
    assert len(result["fields"]["line_items"]) == 1  # single chunk


def test_parallel_preserves_order_and_reports_progress(monkeypatch):
    """Chunks processed concurrently must merge in page order (even when they
    finish out of order), and progress must be reported."""
    import time

    import app.services.ocr as ocr_mod

    N = 6
    monkeypatch.setattr(settings, "ocr_chunk_concurrency", 5)
    # Distinct byte chunks "0".."5"; force chunking + a known page count.
    monkeypatch.setattr(ocr_mod, "page_count", lambda data: N)
    monkeypatch.setattr(ocr_mod, "split_pdf", lambda data, n: [str(i).encode() for i in range(N)])

    class FakeProvider:
        name = "fake"

        def classify(self, *a):
            return "invoice"

        def extract(self, chunk, content_type, doc_type):
            idx = int(chunk.decode())
            time.sleep((N - idx) * 0.02)  # later chunks finish FIRST
            return {"line_items": [{"description": {"value": f"item-{idx}"}}]}

    progress = []
    fields, failed, total = ocr_mod._extract_chunked(
        FakeProvider(), b"whole", "application/pdf", "invoice",
        on_progress=lambda d, t: progress.append((d, t)),
    )
    assert failed == 0 and total == N
    items = [li["description"]["value"] for li in fields["line_items"]]
    assert items == [f"item-{i}" for i in range(N)]  # page order preserved
    assert progress and progress[-1][1] == N
