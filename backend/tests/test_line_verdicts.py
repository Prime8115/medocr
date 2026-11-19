"""Each line verified or to check: its quantity, rate and amount agree to the
paisa, after the discounts the line itself prints."""
from app.services.ocr.verify import line_verdicts, verify_invoice


def _f(value):
    return {"value": value, "confidence": 0.9}


def _line(**values):
    return {k: _f(v) for k, v in values.items()}


def _status(items, checks=()):
    return [v["status"] for v in line_verdicts(items, list(checks))]


def test_quantity_times_rate_is_verified():
    out = line_verdicts([_line(quantity="10", rate="18.50", amount="185.00")], [])
    assert out[0]["status"] == "verified" and "quantity x rate" in out[0]["note"]


def test_a_misread_figure_is_to_check():
    # 185.00 read as 158.00: no discount explains it.
    out = line_verdicts([_line(quantity="10", rate="18.50", amount="158.00")], [])
    assert out[0]["status"] == "check" and "185.00" in out[0]["note"]


def test_the_lines_own_discounts_count():
    assert _status([
        _line(quantity="10", rate="100.00", discount_percent="10", amount="900.00"),
        _line(quantity="10", rate="100.00", discount_amount="25.00", amount="975.00"),
        _line(quantity="12", rate="100.00", free_quantity="2", amount="1000.00"),
        _line(quantity="3", rate="12600.00", discount_percent="3", amount="36666.00"),
    ]) == ["verified"] * 4


def test_rounding_of_a_large_quantity_is_allowed():
    # 840 x 134.04 = 112593.60; a rate rounded to the paisa can be 4.20 out.
    assert _status([_line(quantity="840", rate="134.04", amount="112596.00")]) == ["verified"]


def test_the_same_share_off_every_line_is_verified():
    """Zuventus: 99.5% of quantity x rate on every line, no discount printed."""
    items = [_line(quantity="470", rate="182.60", amount="85392.89"),
             _line(quantity="250", rate="144.06", amount="35834.92"),
             _line(quantity="700", rate="81.22", amount="56569.73")]
    out = line_verdicts(items, [])
    assert [v["status"] for v in out] == ["verified"] * 3
    assert "0.50%" in out[0]["note"]


def test_one_line_off_by_a_share_is_not_a_bill_wide_discount():
    items = [_line(quantity="10", rate="100.00", amount="1000.00"),
             _line(quantity="10", rate="100.00", amount="995.00")]
    assert _status(items) == ["verified", "check"]


def test_free_and_fully_discounted_lines():
    assert _status([
        _line(quantity="50", rate="0.00", amount="0.00"),
        _line(quantity="1", rate="144.72", discount_amount="144.72", amount="0.00"),
        _line(quantity="25", rate="176.08", amount="0.00"),
    ]) == ["verified", "verified", "check"]


def test_a_printed_discount_the_figures_do_not_use_is_to_check():
    """K Sales prints "Disc. Qty 4" (free goods), read as a 4% discount; the
    amount is plain quantity x rate, so the 4% is wrong."""
    out = line_verdicts([_line(quantity="20", rate="106.07", discount_percent="4", amount="2121.40",
                               cgst_percent="6", cgst_amount="127.28", sgst_percent="6", sgst_amount="127.28")], [])
    assert out[0]["status"] == "check" and "4%" in out[0]["note"]


def test_a_discount_inside_the_rate_or_the_tax_base_fits():
    assert _status([
        # Gujarat Enzyme: PTS 6750 less 3% is the rate 6547.50.
        _line(quantity="1", pts="6750.00", rate="6547.50", discount_percent="3", amount="6547.50"),
        # Aurowin: tax charged on 95% of the amount.
        _line(quantity="10", rate="140.34", discount_percent="5", amount="1403.40",
              cgst_percent="2.5", cgst_amount="33.33", sgst_percent="2.5", sgst_amount="33.33"),
        # Troikaa: the discount is the value of the one free unit.
        _line(quantity="10", free_quantity="1", rate="248.91", discount_amount="248.91", amount="2489.10"),
    ]) == ["verified"] * 3


def test_a_failed_check_on_a_figure_makes_the_line_one_to_check():
    item = _line(quantity="10", rate="18.50", amount="185.00", cgst_percent="6", cgst_amount="99.00")
    check = {"id": "line_tax", "label": "Each line's tax is its taxable value at the rate", "status": "fail",
             "fields": ["line_items[0].cgst_amount", "line_items[0].amount"]}
    out = line_verdicts([item], [check])
    assert out[0]["status"] == "check" and "tax" in out[0]["note"]


def test_a_failed_check_on_a_non_figure_does_not():
    check = {"id": "hsn", "label": "HSN codes have 4, 6 or 8 digits", "status": "fail",
             "fields": ["line_items[0].hsn"]}
    assert _status([_line(quantity="10", rate="18.50", amount="185.00", hsn="3004903")], [check]) == ["verified"]


def test_a_line_without_a_rate_cannot_be_verified():
    out = line_verdicts([_line(quantity="10", amount="185.00")], [])
    assert out[0]["status"] == "check" and "rate" in out[0]["note"]


def test_the_verification_carries_the_line_verdicts():
    fields = {"line_items": [_line(quantity="10", rate="18.50", amount="185.00"),
                             _line(quantity="10", rate="18.50", amount="158.00")],
              "invoice": {"total_amount": _f("343.00")}}
    v = verify_invoice(fields, {"total_reconciles": True})
    assert [x["status"] for x in v["lines"]] == ["verified", "check"] and v["lines_verified"] == 1
