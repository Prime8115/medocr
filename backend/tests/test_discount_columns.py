"""Free goods and discounts read as what the bill means, by heading and by the
line's own arithmetic."""
from app.services.ocr.invoice_parser import _discount_reads_as, _keyword_columns


def _f(value):
    return {"value": value, "confidence": 0.9}


def test_disc_qty_is_free_goods_not_a_discount():
    """K Sales: "Qty | Disc. Qty | Rate" - the units given free."""
    cols = _keyword_columns(["Qty", "Disc. Qty", "Rate", "Taxable Value"])
    assert cols["free_quantity"] == 1 and "discount_percent" not in cols


def test_sale_qty_is_the_quantity_beside_a_total():
    """Alkem: "Total Qty | Sale Qty | Discount" - sold plus free is the total."""
    cols = _keyword_columns(["Total Qty", "Sale Qty", "Discount", "Taxable"])
    assert cols["quantity"] == 1 and cols["total_quantity"] == 0


def test_value_disc_is_money():
    """Cipla Pharma: "Value Disc (in Rs) | Disc %"."""
    cols = _keyword_columns(["Qty", "Amount", "Value Disc (in Rs)", "Disc %", "Taxable Amount"])
    assert cols["discount_amount"] == 2 and cols["discount_percent"] == 3


def test_a_discount_is_money_or_a_rate_by_the_lines_arithmetic():
    # 772.20 less 70.20 is the taxable 702.00 (Brinton); 70.20% off is not.
    brinton = {"quantity": _f("11"), "rate": _f("70.20"), "gross_amount": _f("772.20"), "amount": _f("702.00")}
    assert _discount_reads_as(brinton, 70.20) == "money"
    # 16.67% off 888.80 is 740.64 (Shreya Life); 16.67 rupees off is not.
    shreya = {"quantity": _f("20"), "rate": _f("44.44"), "amount": _f("740.64")}
    assert _discount_reads_as(shreya, 16.67) == "percent"
    # Before the billed rate is chosen, the PTS stands in (INV 399).
    ascent = {"quantity": _f("10"), "pts": _f("41.14"), "amount": _f("342.86")}
    assert _discount_reads_as(ascent, 68.54) == "money"
    # Neither fits: nothing is decided.
    assert _discount_reads_as(shreya, 5.0) is None
