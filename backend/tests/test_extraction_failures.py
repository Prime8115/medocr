"""What a failed extraction tells the pharmacist.

A scanned invoice once failed on every attempt while the app reported "AI busy":
every AI error - an overload, a refused request, a timeout - was replaced with
"Could not read the document." and never logged, so neither the app nor the
server logs could say what actually went wrong. These pin that the cause now
survives to the document's error.
"""
import io

import pytest
from pypdf import PdfWriter

import app.services.ocr as ocr
from app.config import settings
from app.services.ocr import OCRError, process_document
from app.services.ocr.base import OCRProvider


class StubProvider(OCRProvider):
    name = "stub"

    def __init__(self, error):
        self.error = error
        self.extract_calls = 0

    def classify(self, file_bytes, content_type):
        return "invoice"

    def extract(self, file_bytes, content_type, doc_type):
        self.extract_calls += 1
        raise self.error


def _blank_pdf(pages: int) -> bytes:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=300, height=300)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


@pytest.fixture
def stub(monkeypatch):
    def install(error):
        provider = StubProvider(error)
        monkeypatch.setattr(ocr, "get_provider", lambda: provider)
        return provider

    return install


def test_a_refused_request_says_why(stub):
    stub(OCRError("AI request was rejected: 400 INVALID_ARGUMENT schema", kind="rejected"))
    with pytest.raises(OCRError) as ei:
        process_document("d", b"img", "image/jpeg", "invoice")
    assert "INVALID_ARGUMENT" in str(ei.value)
    assert "busy" not in str(ei.value).lower()
    assert ei.value.kind == "rejected"


def test_an_overloaded_ai_still_says_busy(stub):
    stub(OCRError("AI service is busy. Please retry in a moment. [503]", kind="busy"))
    with pytest.raises(OCRError) as ei:
        process_document("d", b"img", "image/jpeg", "invoice")
    assert str(ei.value).startswith("AI service is busy")
    assert ei.value.kind == "busy"


def test_unusable_output_reports_the_cause(stub):
    stub(OCRError("Model returned unreadable output: Expecting value", kind="output"))
    with pytest.raises(OCRError) as ei:
        process_document("d", b"img", "image/jpeg", "invoice")
    assert "Could not read the document" in str(ei.value)
    assert "unreadable output" in str(ei.value)


@pytest.mark.parametrize("kind", ["busy", "rejected"])
def test_errors_splitting_cannot_fix_are_not_retried_page_by_page(stub, monkeypatch, kind):
    # Re-splitting helps a truncated answer. An overloaded or refusing AI would
    # only fail once more per page - six pages, six more doomed calls.
    monkeypatch.setattr(settings, "ocr_pdf_chunk_pages", 3)
    monkeypatch.setattr(settings, "ocr_chunk_concurrency", 1)
    provider = stub(OCRError("nope", kind=kind))
    with pytest.raises(OCRError) as ei:
        process_document("d", _blank_pdf(6), "application/pdf", "invoice")
    assert provider.extract_calls == 2  # one per chunk, no per-page retry
    assert "nope" in str(ei.value)


def test_truncated_output_is_still_retried_page_by_page(stub, monkeypatch):
    monkeypatch.setattr(settings, "ocr_pdf_chunk_pages", 3)
    monkeypatch.setattr(settings, "ocr_chunk_concurrency", 1)
    provider = stub(OCRError("Model returned unreadable output", kind="output"))
    with pytest.raises(OCRError) as ei:
        process_document("d", _blank_pdf(6), "application/pdf", "invoice")
    assert provider.extract_calls == 2 + 6  # each chunk, then each of its pages
    assert "Could not read any page of the document: Model returned unreadable output" in str(ei.value)
