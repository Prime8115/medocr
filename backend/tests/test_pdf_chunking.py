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


def test_process_multipage_pdf_merges_chunks_then_dedupes(monkeypatch):
    """A 12-page PDF with chunk size 4 => 3 chunks, each merged in.

    The mock returns the SAME line item for every chunk, so this also pins the
    invoice de-duplication rule: identical rows collapse to one and the collapse
    is reported in meta rather than happening silently.
    """
    monkeypatch.setattr(settings, "allow_mock_ocr", True)
    monkeypatch.setattr(settings, "ocr_pdf_chunk_pages", 4)

    data = _blank_pdf(12)
    result = process_document("doc_x", data, "application/pdf", doc_type="invoice")

    assert result["doc_type"] == "invoice"
    assert result["meta"]["pages"] == 12
    # 3 chunks were merged (3 identical rows), then collapsed to 1.
    assert result["meta"]["duplicates_removed"] == 2
    assert len(result["fields"]["line_items"]) == 1
    assert result["meta"]["item_count"] == 1
    assert any("repeated line" in w for w in result["meta"]["warnings"])


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


def test_the_bill_total_comes_from_the_last_page():
    """Page 1 prints a carried-forward subtotal under 'Total'; the last page the bill's."""
    a = {"invoice": {"invoice_no": {"value": "INV1"}, "total_amount": {"value": "5,000.00"}}, "line_items": []}
    b = {"invoice": {"invoice_no": {"value": "INV1"}, "total_amount": {"value": "12,340.00"}}, "line_items": []}
    m = _merge_fields("invoice", a, b)
    assert m["invoice"]["total_amount"]["value"] == "12,340.00"
    assert "_chunk_conflicts" not in m


def test_pages_that_disagree_on_a_header_field_are_flagged():
    from app.services.ocr import chunk_conflict_warnings

    a = {"invoice": {"invoice_no": {"value": "INV-101", "confidence": 0.95}}, "line_items": []}
    b = {"invoice": {"invoice_no": {"value": "INV-107", "confidence": 0.95}}, "line_items": []}
    c = {"invoice": {"invoice_no": {"value": "inv 101", "confidence": 0.95}}, "line_items": []}
    m = _merge_fields("invoice", _merge_fields("invoice", a, b), c)
    assert m["invoice"]["invoice_no"]["value"] == "INV-101"
    assert m["invoice"]["invoice_no"]["confidence"] == 0.3
    [warning] = chunk_conflict_warnings(m)
    assert "INV-101" in warning and "INV-107" in warning
    assert "_chunk_conflicts" not in m
