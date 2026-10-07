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


# --- a scan whose table cannot be rebuilt still has its header read ----------

SUPPLIER, BUYER = "27ABCDE1234F3ZY", "27PQRST6789K1ZW"  # made up; check characters valid
HEADER_TEXT = (f"SUNRISE PHARMA\nGSTIN: {SUPPLIER}\nInvoice No.   Dated\n"
               f"Buyer (Bill to)\nCITY CARE\nGSTIN : {BUYER}\nTotal 15,034.00")


def _header_only(invoice_no="Dated"):
    return {
        "invoice": {"invoice_no": _leaf(invoice_no), "total_amount": _leaf("15034.00")},
        "supplier": {"gstin": _leaf(BUYER)},  # the flat OCR text put the wrong one here
        "line_items": [],
        "_hints": {"document_text": HEADER_TEXT},
    }


def test_a_header_only_reading_keeps_only_values_that_pass_their_test():
    reading = _header_only()
    cross_read._trustworthy(reading, HEADER_TEXT)
    assert "invoice_no" not in reading["invoice"]          # "Dated" is a label, not a number
    assert reading["supplier"]["gstin"]["value"] == SUPPLIER  # placed by position, not text order
    assert reading["bill_to"]["gstin"]["value"] == BUYER


def test_a_header_only_reading_confirms_the_ai():
    reading = _header_only()
    cross_read._trustworthy(reading, HEADER_TEXT)
    ai = {"invoice": {"invoice_no": _leaf("M-544"), "total_amount": _leaf("15,034.00")},
          "supplier": {"gstin": _leaf(SUPPLIER)}, "bill_to": {"gstin": _leaf(BUYER)},
          "line_items": [{"amount": _leaf("6,070.00")}]}
    [check] = cross_read.compare(ai, reading)
    assert check["status"] == "pass" and check["message"].startswith("3 value(s)")


def test_a_header_only_reading_still_catches_a_misread_total():
    reading = _header_only()
    cross_read._trustworthy(reading, HEADER_TEXT)
    ai = {"invoice": {"total_amount": _leaf("15,084.00")}, "supplier": {"gstin": _leaf(SUPPLIER)},
          "bill_to": {"gstin": _leaf(BUYER)}, "line_items": []}
    [check] = cross_read.compare(ai, reading)
    assert check["status"] == "fail" and check["fields"] == ["invoice.total_amount"]


def test_a_scan_with_no_second_reading_says_so(monkeypatch):
    import app.services.ocr as ocr
    from tests.test_scanned_pdf import RecordingProvider, scanned_pdf

    monkeypatch.setattr(ocr, "get_provider", lambda: RecordingProvider())
    monkeypatch.setattr(cross_read, "second_reading", lambda *_a: None)
    meta = ocr.process_document("d", scanned_pdf(), "application/pdf", "invoice")["meta"]
    checks = {c["id"]: c for c in meta["verification"]["checks"]}
    assert checks["cross_read"]["status"] == "skipped"


def test_a_reading_of_the_first_pages_does_not_count_the_lines():
    """A 10-page scan: the second reading saw only the first 3 pages' lines.
    Fewer lines there is not a disagreement with the AI's full count."""
    ai = _reading()
    ai["line_items"] = ai["line_items"] * 1 + [
        {"quantity": _leaf(str(i)), "amount": _leaf("10.00"), "batch_no": _leaf(f"B{i}")} for i in range(30)]
    second = _reading()
    second["_partial"] = True
    [check] = cross_read.compare(ai, second)
    assert check["status"] == "pass", check
    assert "Line count" not in check["message"]

    del second["_partial"]
    [check] = cross_read.compare(ai, second)
    assert check["status"] == "fail" and "Line count" in check["message"]
