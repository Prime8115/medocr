"""A plausible OCR response cannot hide a page from the upload manifest."""
from app.services.ocr.coverage import record_source_page_count, requirements
from app.services.ocr.prescription_review import verify_prescription
from app.services.ocr.verify import open_checks


def test_matching_upload_and_reading_page_counts_need_no_check():
    result = {"meta": {"pages": 2}}
    record_source_page_count(result, {"processed_pages": 2})
    assert "source_page_mismatch" not in result["meta"]


def test_missing_page_count_is_an_open_invoice_check_and_survives_reverification():
    result = {"meta": {"pages": 1, "verification": {
        "checks": [], "review_requirements": [], "acknowledged": []}}}
    record_source_page_count(result, {"processed_pages": 2})
    meta = result["meta"]
    assert meta["source_page_mismatch"] == {"expected": 2, "observed": 1}
    assert any(check["id"] == "source_page_integrity" for check in open_checks(meta["verification"]))
    assert any(check["id"] == "source_page_integrity" for check in requirements({}, meta))


def test_missing_page_count_survives_prescription_reverification():
    result = {"meta": {"pages": None}}
    record_source_page_count(result, {"processed_pages": 2})
    meta = result["meta"]
    assert meta["source_page_mismatch"] == {"expected": 2, "observed": None}
    verified = verify_prescription({}, meta)
    assert any(check["id"] == "source_page_integrity" for check in open_checks(verified))
