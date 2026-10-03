"""Extraction schema validation and low-confidence flagging."""
import pytest

from app.schemas.extraction import (
    collect_low_confidence,
    validate_fields,
)
from app.services.ocr.mock import MockProvider


def test_validate_prescription_fields_from_mock():
    fields = MockProvider().extract(b"x", "image/jpeg", "prescription")
    clean = validate_fields("prescription", fields)
    assert clean["patient"]["name"]["value"].endswith("(MOCK)")
    assert len(clean["medications"]) == 1


def test_validate_invoice_fields_from_mock():
    fields = MockProvider().extract(b"x", "image/jpeg", "invoice")
    clean = validate_fields("invoice", fields)
    assert clean["supplier"]["name"]["value"].endswith("(MOCK)")
    assert clean["line_items"][0]["batch_no"]["value"] == "B12345"


def test_validate_rejects_unknown_doc_type():
    with pytest.raises(ValueError):
        validate_fields("banana", {})


def test_validate_accepts_empty_fields():
    clean = validate_fields("prescription", {})
    assert clean["medications"] == []


def test_collect_low_confidence_flags_below_threshold():
    fields = MockProvider().extract(b"x", "image/jpeg", "prescription")
    flagged = collect_low_confidence(fields, threshold=0.6)
    # registration_no (0.4) and instructions (0.5) are below 0.6.
    assert any("registration_no" in p for p in flagged)
    assert any("instructions" in p for p in flagged)
    # name (0.9) is not flagged.
    assert not any(p == "patient.name" for p in flagged)


# --- what the AI actually sends back --------------------------------------
# One numeric value among hundreds of fields used to fail validation and with
# it the whole invoice: a scanned MSV Lifesciences bill read as a prescription
# (mostly text) but failed as an invoice (mostly numbers).


def test_numbers_from_the_ai_are_kept_as_text():
    out = validate_fields("invoice", {"line_items": [{
        "quantity": {"value": 100, "confidence": 0.9},
        "rate": {"value": 60.7, "confidence": 0.9},
        "amount": {"value": 1785.0, "confidence": 0.9},
        "free_supply": {"value": False, "confidence": 0.9},
    }]})
    item = out["line_items"][0]
    assert item["quantity"]["value"] == "100"
    assert item["rate"]["value"] == "60.7"
    assert item["amount"]["value"] == "1785"
    assert item["free_supply"]["value"] == "false"


def test_a_bare_value_without_its_confidence_is_kept():
    out = validate_fields("invoice", {"invoice": {"invoice_no": "M-544", "total_amount": 15034}})
    assert out["invoice"]["invoice_no"] == {"value": "M-544", "confidence": None}
    assert out["invoice"]["total_amount"]["value"] == "15034"


def test_extra_columns_accept_numbers_too():
    out = validate_fields("invoice", {"line_items": [{"extras": [{"label": "Rack", "value": 12}]}]})
    assert out["line_items"][0]["extras"][0]["value"] == "12"


def test_genuinely_malformed_fields_still_fail():
    import pytest

    with pytest.raises(ValueError):
        validate_fields("invoice", {"line_items": [{"quantity": {"value": ["a", "b"]}}]})
