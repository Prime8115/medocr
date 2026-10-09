"""Recorded AI answers replay exactly, and a question never asked is said."""
import pytest

from app.services.ocr.base import OCRError, OCRProvider
from app.services.ocr.recorded import RecordingProvider, ReplayProvider


class _Asked(OCRProvider):
    name = "gemini"

    def __init__(self):
        self.asked = 0

    def classify(self, file_bytes, content_type):
        self.asked += 1
        return "invoice"

    def extract(self, file_bytes, content_type, doc_type):
        self.asked += 1
        return {"invoice": {"invoice_no": {"value": file_bytes.decode(), "confidence": 0.9}}}

    def complete_json(self, prompt):
        self.asked += 1
        return {"invoice.po_no": "PO-" + prompt}


def test_an_answer_recorded_once_is_replayed_without_asking(tmp_path):
    real = _Asked()
    recorder = RecordingProvider(real, tmp_path)
    first = recorder.extract(b"INV-7", "application/pdf", "invoice")
    recorder.complete_json("12")
    assert real.asked == 2

    replay = ReplayProvider(tmp_path)
    assert replay.extract(b"INV-7", "application/pdf", "invoice") == first
    assert replay.complete_json("12") == {"invoice.po_no": "PO-12"}
    assert replay.name == "gemini"


def test_a_reading_never_recorded_fails_loudly_and_a_follow_up_goes_unanswered(tmp_path):
    replay = ReplayProvider(tmp_path)
    with pytest.raises(OCRError):
        replay.extract(b"another bill", "application/pdf", "invoice")
    assert replay.complete_json("a question never asked") == {}
    assert replay.review_json("p", b"x", "application/pdf") is None
    assert replay.unanswered == ["extract", "complete_json", "review_json"]


def test_a_scan_rendered_differently_still_finds_its_one_reading(tmp_path):
    from app.services.ocr.recorded import answers_dir

    store = answers_dir(tmp_path, "pdfs/soham/WIN MEDICARE PRIVATE LIMITED.pdf")
    RecordingProvider(_Asked(), store).extract(b"pixels on this machine", "application/pdf", "invoice")
    # Another machine's renderer makes other bytes of the same page.
    replay = ReplayProvider(store)
    assert replay.extract(b"pixels elsewhere", "application/pdf", "invoice")["invoice"]
    assert replay.unanswered == []
    # Text is the same everywhere: a text question is never guessed.
    with pytest.raises(OCRError):
        replay.extract(b"other text", "text/plain", "invoice")


def test_an_answer_without_its_line_list_is_asked_again():
    from app.services.ocr import _extract_one

    class Forgetful(_Asked):
        def extract(self, file_bytes, content_type, doc_type):
            self.asked += 1
            header = {"invoice": {"invoice_no": {"value": "A-1", "confidence": 0.9}}}
            if self.asked == 1:
                return header          # the list left out altogether
            return {**header, "line_items": [{"description": {"value": "PARACIP", "confidence": 0.9}}]}

    ai = Forgetful()
    fields = _extract_one(ai, b"page", "text/plain", "invoice")
    assert ai.asked == 2 and len(fields["line_items"]) == 1
