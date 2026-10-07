"""Adapting to a new supplier: missed fields found, gaps filled safely.

The AI is never trusted on its word - each test of gap-fill here hands it a
fake model that lies in some way, and checks the lie is refused.
"""
from app.services.ocr import gap_fill
from app.services.ocr.missed_fields import find_missed, missed_check


def _leaf(v):
    return {"value": v, "confidence": 1.0}


def _fields(**invoice):
    return {
        "supplier": {"name": _leaf("NEW SUPPLIER PVT LTD"), "gstin": _leaf("27AAACI9822K1Z9")},
        "bill_to": {"name": _leaf("EASTERN AGENCIES"), "gstin": _leaf("27AAECD7847H1ZC")},
        "ship_to": {},
        "invoice": {"invoice_no": _leaf("INV-1"), "invoice_date": _leaf("22-09-2025"),
                    "total_amount": _leaf("1000.00"),
                    **{k: _leaf(v) for k, v in invoice.items()}},
        "line_items": [],
    }


PAGE = ("NEW SUPPLIER PVT LTD  GSTIN 27AAACI9822K1Z9\n"
        "Invoice No. : INV-1  Invoice Date : 22-09-2025\n"
        "Docket No. & Date : DK778812 / 23-09-2025\n"
        "L.R. No. : LR55102 Date : 23-09-2025\n"
        "IRN No.: ab12cd34ef56ab12cd34ef56ab12cd34ef56ab12cd34ef56ab12cd34ef56ab12\n"
        "Grand Total 1000.00")


# --- the missed-field detector ---------------------------------------------------

def test_a_printed_field_we_did_not_read_is_found():
    missed = {m["path"]: m["printed_value"] for m in find_missed(_fields(), PAGE)}
    assert missed["invoice.lr_no"] == "LR55102"
    assert missed["invoice.lr_date"] == "23-09-2025"
    assert missed["invoice.irn"].startswith("ab12cd34")


def test_a_field_the_bill_leaves_blank_is_not_a_miss():
    # V L: "L.R. NO. : DATE :" - both blank. "DATE" is a label, not a value,
    # and must not be trimmed to "DAT" to look like one.
    assert find_missed(_fields(), "L.R. NO. : DATE :\nTRANSPORTER :") == []


def test_a_field_we_read_is_not_a_miss():
    assert find_missed(_fields(lr_no="LR55102", lr_date="23-09-2025",
                               irn="ab12cd34ef56ab12cd34ef56ab12cd34ef56ab12cd34ef56ab12cd34ef56ab12"),
                       PAGE) == []


def test_the_check_names_the_field_and_what_the_bill_prints():
    check = missed_check(_fields(), PAGE)
    assert check["status"] == "fail"
    assert "invoice.lr_no" in check["fields"]
    assert "LR55102" in check["message"]
    assert missed_check(_fields(), "")["status"] == "skipped"


# --- AI gap-fill, kept on a short leash -------------------------------------------

class _Model:
    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    def complete_json(self, prompt):
        self.prompts.append(prompt)
        return self.answer


def test_nothing_missing_means_no_call():
    model = _Model({})
    fields = _fields(lr_no="LR55102", lr_date="23-09-2025",
                     irn="ab12cd34ef56ab12cd34ef56ab12cd34ef56ab12cd34ef56ab12cd34ef56ab12")
    assert gap_fill.fill(fields, "nothing printed here", [], provider=model) == []
    assert model.prompts == []


def test_an_answer_printed_on_the_page_is_kept_and_marked():
    fields = _fields()
    model = _Model({"invoice.lr_no": "LR55102", "invoice.lr_date": "23-09-2025"})
    filled = gap_fill.fill(fields, PAGE, find_missed(fields, PAGE), provider=model)
    assert {f["path"] for f in filled} >= {"invoice.lr_no", "invoice.lr_date"}
    assert fields["invoice"]["lr_no"] == {"value": "LR55102",
                                          "confidence": gap_fill.GAP_FILL_CONFIDENCE}
    # Only the text and the field names were sent - and only blank fields asked.
    assert "invoice.lr_no" in model.prompts[0] and "invoice.invoice_no" not in model.prompts[0]


def test_an_invented_value_is_refused():
    fields = _fields()
    model = _Model({"invoice.lr_no": "LR99999", "invoice.transport": "BLUE DART"})
    gap_fill.fill(fields, PAGE, find_missed(fields, PAGE), provider=model)
    assert "lr_no" not in fields["invoice"]
    assert "transport" not in fields["invoice"]


def test_a_value_of_the_wrong_shape_is_refused():
    page = PAGE + "\nPO Number : 22-09-2025"
    fields = _fields()
    model = _Model({"invoice.irn": "INV-1", "invoice.eway_bill_no": "LR55102",
                    "invoice.po_date": "Docket"})
    gap_fill.fill(fields, page, find_missed(fields, page), provider=model)
    for key in ("irn", "eway_bill_no", "po_date"):
        assert key not in fields["invoice"], key


def test_a_gstin_must_pass_its_check_digit_and_not_be_the_buyers():
    fields = _fields()
    fields["supplier"]["gstin"] = {"value": None}
    page = PAGE + "\nGSTIN 27AAECD7847H1ZC and 27AAACI9822K1Z8"
    model = _Model({"supplier.gstin": "27AAECD7847H1ZC"})
    gap_fill.fill(fields, page, [], provider=model)
    assert not (fields["supplier"].get("gstin") or {}).get("value")
    model = _Model({"supplier.gstin": "27AAACI9822K1Z8"})          # bad check digit
    gap_fill.fill(fields, page, [], provider=model)
    assert not (fields["supplier"].get("gstin") or {}).get("value")


def test_a_failing_model_leaves_the_reading_untouched():
    class Broken:
        def complete_json(self, prompt):
            raise RuntimeError("quota")

    fields = _fields()
    before = repr(fields)
    assert gap_fill.fill(fields, PAGE, find_missed(fields, PAGE), provider=Broken()) == []
    assert repr(fields) == before


def test_gap_fill_can_be_switched_off(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "ocr_gap_fill", False)
    model = _Model({"invoice.lr_no": "LR55102"})
    fields = _fields()
    assert gap_fill.fill(fields, PAGE, find_missed(fields, PAGE), provider=model) == []
    assert model.prompts == []


# --- the IRN: wrapped pieces joined, and its length checked ------------------------

def test_an_irn_wrapped_over_lines_is_joined():
    from app.services.ocr.invoice_header import extract_references
    head, tail = "d53355f50eca36d02ddd2300d5f5345328b950f9e5f1b", "0a6d4b80b5f3ae2f973"
    text = f"IRN : {head}\nShip To : EASTERN AGENCIES 400056\n{tail}\n"
    assert extract_references(text)["irn"] == head + tail


def test_an_irn_is_not_joined_to_a_run_of_the_wrong_length():
    from app.services.ocr.invoice_header import extract_references
    head = "d53355f50eca36d02ddd2300d5f5345328b950f9e5f1b"
    text = f"IRN : {head}\nPIN 400056 code abcdef1234\n"
    assert extract_references(text)["irn"] == head


def test_a_short_irn_fails_its_check():
    from app.services.ocr.verify import verify_invoice
    report = {"reconciles": True}
    short = verify_invoice(_fields(irn="d7c4eed7d3ff34acf46c45252b5e0b1ac0"), report)
    [irn] = [c for c in short["checks"] if c["id"] == "irn"]
    assert irn["status"] == "fail" and irn["fields"] == ["invoice.irn"]
    full = verify_invoice(_fields(irn="ab12cd34" * 8), report)
    assert [c["status"] for c in full["checks"] if c["id"] == "irn"] == ["pass"]


def test_a_money_answer_must_be_a_whole_figure_on_the_page():
    """'100' is not on a page that prints only 1,000.00 - a substring is not a match."""
    page = "Grand Total 1,000.00\nTaxable 847.46"
    accept = gap_fill._acceptable
    assert not accept("invoice.total_amount", "100", page, {})
    assert not accept("invoice.total_amount", "000.00", page, {})
    assert accept("invoice.total_amount", "1000", page, {})
    assert accept("invoice.total_amount", "1,000.00", page, {})
    assert accept("invoice.total_amount", "847.46", page, {})
    assert not accept("invoice.total_amount", "47.46", page, {})
