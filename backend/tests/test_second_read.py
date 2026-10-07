"""A digital invoice whose table reading does not add up is read again by the AI,
and the AI's reading is kept only when IT adds up."""
import pytest

import app.services.ocr as ocr
from app.services.ocr import invoice_parser
from app.services.ocr.base import OCRError
from tests.fixtures.invoice_pdf import build_invoice_pdf

pytest.importorskip("reportlab")


def _f(v):
    return {"value": v, "confidence": 0.9}


class _AI:
    name = "gemini"

    def __init__(self, total=None, exc=None):
        self.total, self.exc, self.n = total, exc, 0

    def classify(self, *_a):
        return "invoice"

    def extract(self, *_a):
        self.n += 1
        if self.exc:
            raise self.exc
        return {"invoice": {"invoice_no": _f("2299707688"), "total_amount": _f(self.total)},
                "line_items": [{"description": _f("X"), "quantity": _f("1"), "rate": _f(self.total),
                                "amount": _f(self.total)}]}


@pytest.fixture
def off_total(monkeypatch):
    """The table reader reads the PDF, but its total is 500 off its lines."""
    real = invoice_parser.parse_invoice_pdf

    def parse(data, **kw):
        out = real(data, **kw)
        if out:
            total = float(out["invoice"]["total_amount"]["value"].replace(",", ""))
            out["invoice"]["total_amount"]["value"] = f"{total + 500:.2f}"
            out["invoice"]["total_taxable_amount"] = {"value": None, "confidence": None}
        return out

    monkeypatch.setattr(invoice_parser, "parse_invoice_pdf", parse)


def _run(monkeypatch, provider):
    monkeypatch.setattr(ocr, "get_provider", lambda: provider)
    return ocr.process_document("d", build_invoice_pdf(n_items=6), "application/pdf", "invoice")


def test_a_reconciled_table_reading_never_calls_the_ai(monkeypatch):
    provider = _AI(total="100.00")
    result = _run(monkeypatch, provider)
    assert result["meta"]["pipeline"] == "pdf_parser" and provider.n == 0


def test_the_ai_reading_is_kept_when_it_adds_up(monkeypatch, off_total):
    provider = _AI(total="100.00")
    result = _run(monkeypatch, provider)
    assert provider.n == 1
    assert result["meta"]["pipeline"] == "gemini"
    assert result["meta"]["total_reconciles"] is True
    assert "did not add up" in result["meta"]["warnings"][0]


def test_the_table_reading_stays_when_the_ai_does_not_add_up_either(monkeypatch, off_total):
    class Unbalanced(_AI):
        def extract(self, *a):
            out = super().extract(*a)
            out["line_items"][0]["amount"] = _f("90.00")
            return out

    result = _run(monkeypatch, Unbalanced(total="100.00"))
    assert result["meta"]["pipeline"] == "pdf_parser"
    assert result["meta"]["total_reconciles"] is False


def test_the_table_reading_stays_when_the_ai_is_unavailable(monkeypatch, off_total):
    result = _run(monkeypatch, _AI(exc=OCRError("busy", kind="busy")))
    assert result["meta"]["pipeline"] == "pdf_parser"
    checks = {c["id"]: c for c in result["meta"]["verification"]["checks"]}
    assert checks["total_reconciles"]["status"] == "fail"


def test_the_second_read_can_be_switched_off(monkeypatch, off_total):
    from app.config import settings

    monkeypatch.setattr(settings, "ocr_second_read_unreconciled", False)
    provider = _AI(total="100.00")
    assert _run(monkeypatch, provider)["meta"]["pipeline"] == "pdf_parser"
    assert provider.n == 0
