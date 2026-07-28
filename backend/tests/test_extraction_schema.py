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
