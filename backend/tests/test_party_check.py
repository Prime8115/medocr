"""The parties' GSTINs are checked against a second, independent reading of
the page - so one the AI leaves blank or misreads is caught, never silently
lost. (The same MSV scan, read twice, once came back without its Bill-to
GSTIN.)"""
import shutil

import pytest

import app.services.ocr as ocr
from app.config import settings
from app.services.ocr.party_check import cross_check, page_party_gstins
from tests.test_scanned_pdf import RecordingProvider, digital_pdf, scanned_pdf

SUPPLIER, BUYER = "27ABCDE1234F3ZY", "27PQRST6789K1ZW"
PAGE = f"MSV LIFESCIENCES\nGSTIN/UIN: {SUPPLIER}\nBuyer (Bill to)\nMuthu Pharma\nGSTIN/UIN : {BUYER}\n"


def _fields(supplier=None, buyer=None, ship=None):
    leaf = lambda v: {"value": v, "confidence": 0.95 if v else None}  # noqa: E731
    return {"supplier": {"gstin": leaf(supplier)}, "bill_to": {"gstin": leaf(buyer)},
            "ship_to": {"gstin": leaf(ship)}}


def test_each_partys_gstin_is_found_by_its_place_on_the_page():
    assert page_party_gstins(PAGE) == (SUPPLIER, BUYER, [SUPPLIER, BUYER])


def test_a_misread_gstin_on_the_page_is_never_used():
    # A scanner's misreading, as on the MSV bill: both fail their check character.
    page = "GSTIN/UlN: 27ABCDE1234F 32Y\nBuyer\nGSTIN/UIN : 27PQRST6789KIZW"
    assert page_party_gstins(page) == (None, None, [])


def test_without_a_buyer_heading_no_party_is_assumed():
    assert page_party_gstins(f"{SUPPLIER}\n{BUYER}")[:2] == (None, None)


def test_two_gstins_on_one_side_assign_neither():
    page = f"GSTIN {SUPPLIER}\nBill to\nGSTIN {BUYER}\nShip to\nGSTIN 29LMNOP4321Q1Z0"
    assert page_party_gstins(page)[1] is None


def test_a_gstin_the_ai_missed_is_filled_and_flagged():
    fields = _fields(supplier=SUPPLIER, buyer=None)
    warnings = cross_check(fields, PAGE)
    assert fields["bill_to"]["gstin"]["value"] == BUYER
    assert fields["bill_to"]["gstin"]["confidence"] < settings.low_confidence_threshold
    assert warnings == [f"The Bill-to GSTIN was missed by the AI; {BUYER} is printed on the invoice "
                        "and has been filled in - please confirm it."]


def test_an_invalid_ai_reading_is_replaced_by_the_valid_printed_one():
    fields = _fields(supplier="27ABCDE1234F32Y", buyer=BUYER)
    warnings = cross_check(fields, PAGE)
    assert fields["supplier"]["gstin"]["value"] == SUPPLIER
    assert "read as 27ABCDE1234F32Y" in warnings[0]


def test_two_valid_readings_that_disagree_are_flagged_not_chosen():
    other = "29LMNOP4321Q1Z0"
    fields = _fields(supplier=other, buyer=BUYER)
    warnings = cross_check(fields, PAGE)
    assert fields["supplier"]["gstin"]["value"] == other  # the AI's reading is kept...
    assert fields["supplier"]["gstin"]["confidence"] == 0.3  # ...but flagged
    assert "disagree" in warnings[0]


def test_agreement_changes_nothing():
    fields = _fields(supplier=SUPPLIER, buyer=BUYER)
    assert cross_check(fields, PAGE) == []
    assert fields["bill_to"]["gstin"]["confidence"] == 0.95


def test_an_unplaceable_gstin_left_off_bill_to_is_pointed_out():
    fields = _fields(supplier=SUPPLIER)
    warnings = cross_check(fields, f"{SUPPLIER}\n{BUYER}")  # no heading: cannot place it
    assert fields["bill_to"]["gstin"]["value"] is None
    assert BUYER in warnings[-1] and "not read into Bill-to" in warnings[-1]


def test_no_second_reading_means_no_change():
    fields = _fields()
    assert cross_check(fields, "") == []


def test_a_digital_pdf_is_checked_against_its_own_text(monkeypatch):
    provider = RecordingProvider()  # returns nothing: as if the AI missed every field
    monkeypatch.setattr(ocr, "get_provider", lambda: provider)
    monkeypatch.setattr(ocr, "is_digital_pdf", lambda _d: False)  # force the AI path
    out = ocr.process_document("d", digital_pdf(), "application/pdf", "invoice")
    assert out["fields"]["supplier"]["gstin"]["value"] == SUPPLIER
    assert out["fields"]["bill_to"]["gstin"]["value"] == BUYER


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
def test_a_scan_is_checked_against_local_ocr_of_its_picture(monkeypatch):
    provider = RecordingProvider()
    monkeypatch.setattr(ocr, "get_provider", lambda: provider)
    out = ocr.process_document("d", scanned_pdf(), "application/pdf", "invoice")
    # The picture prints the right GSTINs; its scanner layer has them wrong.
    assert out["fields"]["supplier"]["gstin"]["value"] == SUPPLIER
    assert out["fields"]["bill_to"]["gstin"]["value"] == BUYER


def test_the_check_can_be_switched_off(monkeypatch):
    provider = RecordingProvider()
    monkeypatch.setattr(ocr, "get_provider", lambda: provider)
    monkeypatch.setattr(ocr, "is_digital_pdf", lambda _d: False)
    monkeypatch.setattr(settings, "ocr_party_check_enabled", False)
    out = ocr.process_document("d", digital_pdf(), "application/pdf", "invoice")
    assert not out["fields"]["bill_to"]["gstin"]["value"]


def test_the_ai_is_asked_for_a_repeatable_answer():
    assert settings.ocr_temperature == 0.0
