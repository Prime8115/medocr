"""Deterministic post-processing normalizers."""
from app.services.ocr.postprocess import (
    normalize_date,
    normalize_quantity,
    normalize_strength,
    postprocess_fields,
)


def test_normalize_date_dayfirst():
    assert normalize_date("27/07/2026") == "2026-07-27"
    assert normalize_date("12/2027") is not None  # month/year
    assert normalize_date("garbage") is None
    assert normalize_date(None) is None


def test_normalize_quantity():
    assert normalize_quantity("100") == 100
    assert normalize_quantity("2.5 strips") == 2.5
    assert normalize_quantity("qty: 30 nos") == 30
    assert normalize_quantity(None) is None


def test_normalize_strength():
    assert normalize_strength("500 mg") == {"amount": 500, "unit": "mg"}
    assert normalize_strength("5ml") == {"amount": 5, "unit": "ml"}
    assert normalize_strength("no strength") is None


def test_postprocess_prescription_enriches_strength():
    fields = {
        "medications": [
            {"name": {"value": "Paracetamol"}, "strength": {"value": "500 mg"}, "duration": {"value": "3 days"}}
        ]
    }
    out = postprocess_fields("prescription", fields)
    med = out["medications"][0]
    assert med["strength"]["normalized"] == {"amount": 500, "unit": "mg"}
    assert med["duration"]["normalized"] == 3


def test_postprocess_invoice_enriches_dates():
    fields = {
        "invoice": {"invoice_date": {"value": "27/07/2026"}},
        "line_items": [{"expiry": {"value": "12/2027"}, "quantity": {"value": "100"}}],
    }
    out = postprocess_fields("invoice", fields)
    assert out["invoice"]["invoice_date"]["normalized"] == "2026-07-27"
    assert out["line_items"][0]["quantity"]["normalized"] == 100
