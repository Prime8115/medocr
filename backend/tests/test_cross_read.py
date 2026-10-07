"""A scan read twice: the AI's reading checked against Tesseract's.

Agreement passes; a disagreement fails and names only the disputed field; a
field the second reader could not see is not voted on at all.
"""
from app.services.ocr import cross_read


def _leaf(v):
    return {"value": v, "confidence": 0.9}


def _reading(total="15,034.00", gstin="33ABEFM0315R1Z8", amount="6,070.00", batch="OVT-25327",
             expiry="31-May-27"):
    return {
        "invoice": {"invoice_no": _leaf("M-544"), "invoice_date": _leaf("6-Oct-25"),
                    "total_amount": _leaf(total)},
        "supplier": {"gstin": _leaf(gstin)},
        "bill_to": {},
        "line_items": [
            {"quantity": _leaf("100"), "amount": _leaf(amount), "batch_no": _leaf(batch),
             "expiry": _leaf(expiry), "mrp": _leaf("80.00")},
            {"quantity": _leaf("20"), "amount": _leaf("1,785.00"), "batch_no": _leaf("OVT-25263"),
             "expiry": _leaf("31-Oct-26"), "mrp": _leaf("117.00")},
        ],
    }


def test_two_readings_that_agree_confirm_the_scan():
    # Formatting differences are not disagreements: commas, rupee signs,
    # "15034" against "15,034.00", a date with or without its day.
    second = _reading(total="15034", expiry="May-27")
    [check] = cross_read.compare(_reading(), second)
    assert check["status"] == "pass", check
    assert "confirmed" in check["message"]


def test_a_disagreement_flags_exactly_the_disputed_field():
    second = _reading(amount="6,670.00")            # a 0 read as a 6
    [check] = cross_read.compare(_reading(), second)
    assert check["status"] == "fail"
    assert check["fields"] == ["line_items[0].amount"]
    assert "6,670.00" in check["message"]


def test_a_misread_gstin_is_caught():
    second = _reading(gstin="33ABEFM0315R1Z3")
    [check] = cross_read.compare(_reading(), second)
    assert check["status"] == "fail"
    assert "supplier.gstin" in check["fields"]


def test_the_second_reader_never_votes_on_what_it_could_not_see():
    second = _reading()
    second["invoice"]["total_amount"] = _leaf("")       # Tesseract missed the total
    second["supplier"] = {}
    [check] = cross_read.compare(_reading(), second)
    assert check["status"] == "pass"
    assert "invoice.total_amount" not in check["fields"]


def test_lines_are_matched_by_batch_not_by_position():
    second = _reading()
    second["line_items"].reverse()                       # read in another order
    [check] = cross_read.compare(_reading(), second)
    assert check["status"] == "pass"


def test_a_second_reading_with_nothing_comparable_is_skipped():
    [check] = cross_read.compare(_reading(), {"invoice": {}, "line_items": []})
    assert check["status"] == "skipped"


def test_no_second_reading_without_tesseract(monkeypatch):
    from app.services.ocr import tesseract_table
    monkeypatch.setattr(tesseract_table, "available", lambda: False)
    assert cross_read.second_reading(b"%PDF", "application/pdf") is None


def test_the_cross_read_can_be_switched_off(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "ocr_cross_read", False)
    assert cross_read.second_reading(b"%PDF", "application/pdf") is None
