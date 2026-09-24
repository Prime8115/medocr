"""Invoice integrity: de-duplication, line arithmetic, totals reconciliation.

These pin the behaviour behind the four field complaints:
  - 143-item invoice displayed as 429 rows (printed copies -> duplicate rows)
  - final amount not displayed (total never extracted, never reconciled)
  - quantity right on one line and wrong on others (column read off by one)
"""
from app.services.ocr.invoice_checks import (
    dedupe_line_items,
    reconcile_invoice,
    validate_line_arithmetic,
)


def _f(value, confidence=1.0):
    return {"value": value, "confidence": confidence}


def _item(desc, batch="B1", qty="10", rate="20.00", amount="200.00", mrp="25.00"):
    return {
        "description": _f(desc), "batch_no": _f(batch), "expiry": _f("01/2027"),
        "quantity": _f(qty), "free_quantity": _f(None), "mrp": _f(mrp),
        "rate": _f(rate), "amount": _f(amount),
    }


# --------------------------------- dedupe ---------------------------------
def test_dedupe_collapses_triplicate_copies():
    """The real failure: one invoice printed 3x, so every row arrives 3x."""
    unique = [_item(f"MED {i}", batch=f"B{i}") for i in range(143)]
    items = unique * 3
    assert len(items) == 429

    out, removed = dedupe_line_items(items)
    assert len(out) == 143
    assert removed == 286
    assert [i["description"]["value"] for i in out] == [f"MED {i}" for i in range(143)]


def test_dedupe_keeps_same_medicine_with_different_batches():
    """Two lines of the same product on different batches are both real."""
    items = [_item("CALPOL 650", batch="C1"), _item("CALPOL 650", batch="C2")]
    out, removed = dedupe_line_items(items)
    assert len(out) == 2 and removed == 0


def test_dedupe_keeps_same_medicine_with_different_quantity():
    items = [_item("CALPOL 650", qty="10", amount="200.00"),
             _item("CALPOL 650", qty="20", amount="400.00")]
    out, removed = dedupe_line_items(items)
    assert len(out) == 2 and removed == 0


def test_dedupe_leaves_bare_description_rows_alone():
    """A repeated name with no batch/qty/price is not evidence of a reprint."""
    bare = {"description": _f("UNREADABLE"), "batch_no": _f(None), "quantity": _f(None)}
    out, removed = dedupe_line_items([dict(bare), dict(bare)])
    assert len(out) == 2 and removed == 0


def test_dedupe_preserves_first_occurrence_order():
    items = [_item("A", batch="A1"), _item("B", batch="B1"), _item("A", batch="A1")]
    out, removed = dedupe_line_items(items)
    assert [i["description"]["value"] for i in out] == ["A", "B"]
    assert removed == 1


def test_dedupe_handles_empty():
    assert dedupe_line_items([]) == ([], 0)


# ------------------------------ line arithmetic ------------------------------
def test_arithmetic_accepts_a_consistent_line():
    items = [_item("A", qty="10", rate="20.00", amount="200.00")]
    assert validate_line_arithmetic(items) == 0
    assert items[0]["quantity"]["confidence"] == 1.0


def test_arithmetic_tolerates_a_normal_discount():
    # 10 x 20.00 = 200.00 billed at 180.00 after a 10% scheme discount.
    items = [_item("A", qty="10", rate="20.00", amount="180.00")]
    assert validate_line_arithmetic(items) == 0


def test_arithmetic_flags_a_column_read_off_by_one():
    """qty x rate nowhere near amount => the row is marked for checking."""
    items = [_item("A", qty="240", rate="16.93", amount="23.70")]  # MRP landed in amount
    assert validate_line_arithmetic(items) == 1
    assert items[0]["quantity"]["confidence"] == 0.4
    assert items[0]["rate"]["confidence"] == 0.4
    assert items[0]["amount"]["confidence"] == 0.4


def test_arithmetic_ignores_incomplete_lines():
    items = [_item("A", qty=None, rate="20.00", amount="200.00")]
    assert validate_line_arithmetic(items) == 0


# ------------------------------- reconciliation -------------------------------
def _fields(items, total):
    return {"line_items": items, "invoice": {"total_amount": _f(total)}}


def test_reconcile_passes_when_lines_match_the_printed_total():
    report = reconcile_invoice(_fields([_item("A"), _item("B", batch="B2")], "400.00"))
    assert report["line_items_total"] == "400.00"
    assert report["total_reconciles"] is True
    assert report["warnings"] == []


def test_reconcile_allows_roundoff_and_freight():
    report = reconcile_invoice(_fields([_item("A")], "202.00"))
    assert report["total_reconciles"] is True


def test_reconcile_warns_when_lines_do_not_add_up():
    """Exactly what a 3x-duplicated invoice looks like."""
    report = reconcile_invoice(_fields([_item("A"), _item("A"), _item("A")], "200.00"))
    assert report["total_reconciles"] is False
    assert report["line_items_total"] == "600.00"
    assert any("600.00" in w and "200.00" in w for w in report["warnings"])


def test_reconcile_warns_when_total_is_missing():
    report = reconcile_invoice(_fields([_item("A")], None))
    assert report["total_reconciles"] is None
    assert any("total could not be read" in w for w in report["warnings"])


def test_reconcile_warns_on_item_count_mismatch():
    items = [_item(f"M{i}", batch=f"B{i}") for i in range(6)]
    report = reconcile_invoice(_fields(items, "1200.00"), stated_item_count=2)
    assert report["stated_item_count"] == 2
    assert any("states 2 items but 6 were read" in w for w in report["warnings"])


def test_reconcile_reports_lines_without_an_amount():
    items = [_item("A"), _item("B", batch="B2", amount=None)]
    report = reconcile_invoice(_fields(items, "200.00"))
    assert any("no amount" in w for w in report["warnings"])
