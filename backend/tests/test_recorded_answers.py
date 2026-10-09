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
