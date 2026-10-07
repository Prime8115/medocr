"""The verification layer: every identity a bill states, checked.

Each check must pass on a clean bill, fail on one corrupted field - naming that
field - and be skipped, never failed, when the bill does not print its inputs.
"""
import copy
import datetime as dt

from app.services.ocr.verify import (
    flag_failed_fields,
    open_checks,
    parse_month,
    reverify,
    verify_invoice,
)
from tests.conftest import register_and_login, sample_image

TODAY = dt.date(2025, 10, 1)


def _leaf(v):
    return {"value": v, "confidence": 1.0}


def _clean_bill():
    """Two lines at 12% GST (6 + 6), totals that foot, valid GSTINs."""
    lines = []
    for qty, rate in ((10, 100.0), (5, 200.0)):
        taxable = qty * rate
        tax = taxable * 0.06
        lines.append({
            "description": _leaf("MED"), "batch_no": _leaf("B1"), "hsn": _leaf("30049099"),
            "expiry": _leaf("10/2027"), "mfg_date": _leaf("05/2025"),
            "quantity": _leaf(str(qty)), "rate": _leaf(f"{rate:.2f}"),
            "mrp": _leaf(f"{rate * 1.5:.2f}"), "ptr": _leaf(f"{rate * 1.1:.2f}"),
            "pts": _leaf(f"{rate:.2f}"), "amount": _leaf(f"{taxable:.2f}"),
            "gross_amount": _leaf(f"{taxable + 50:.2f}"), "discount_amount": _leaf("50.00"),
            "cgst_percent": _leaf("6"), "cgst_amount": _leaf(f"{tax:.2f}"),
            "sgst_percent": _leaf("6"), "sgst_amount": _leaf(f"{tax:.2f}"),
            "gst_percent": _leaf("12"), "net_amount": _leaf(f"{taxable + 2 * tax:.2f}"),
        })
    return {
        "supplier": {"gstin": _leaf("27AAACI9822K1Z9"), "pan": _leaf("AAACI9822K")},
        "bill_to": {"gstin": _leaf("27AAECD7847H1ZC")},
        "ship_to": {},
        "invoice": {
            "invoice_date": _leaf("22-Sep-2025"), "total_amount": _leaf("2240.00"),
            "total_taxable_amount": _leaf("2000.00"), "total_gst_amount": _leaf("240.00"),
            "total_cgst_amount": _leaf("120.00"), "total_sgst_amount": _leaf("120.00"),
        },
        "line_items": lines,
    }


REPORT = {"total_reconciles": True, "total_reconciled_by": "lines + per-line GST",
          "line_items_total": "2000.00", "stated_item_count": 2}


def _check(v, check_id):
    return next(c for c in v["checks"] if c["id"] == check_id)


def test_a_clean_bill_is_verified():
    v = verify_invoice(_clean_bill(), REPORT, today=TODAY)
    failed = [c for c in v["checks"] if c["status"] == "fail"]
    assert failed == [], failed
    assert v["verdict"] == "verified"


def _break(mutate, check_id, field):
    bill = _clean_bill()
    mutate(bill)
    v = verify_invoice(bill, REPORT, today=TODAY)
    check = _check(v, check_id)
    assert check["status"] == "fail", check
    assert field in check["fields"], check
    assert v["verdict"] == "needs_check"


def test_a_misread_line_tax_is_caught():
    _break(lambda b: b["line_items"][0].__setitem__("cgst_amount", _leaf("66.00")),
           "line_tax", "line_items[0].cgst_amount")


def test_a_misread_net_is_caught():
    _break(lambda b: b["line_items"][1].__setitem__("net_amount", _leaf("1180.00")),
           "line_net", "line_items[1].net_amount")


def test_a_misread_discount_is_caught():
    _break(lambda b: b["line_items"][0].__setitem__("discount_amount", _leaf("5.00")),
           "line_discount", "line_items[0].discount_amount")


def test_a_tax_total_that_is_not_the_lines_is_caught():
    _break(lambda b: b["invoice"].__setitem__("total_cgst_amount", _leaf("102.00")),
           "head_totals", "invoice.total_cgst_amount")


def test_a_total_that_does_not_foot_is_caught():
    _break(lambda b: b["invoice"].__setitem__("total_amount", _leaf("2440.00")),
           "cross_foot", "invoice.total_amount")


def test_a_trade_price_above_mrp_is_caught():
    _break(lambda b: b["line_items"][0].__setitem__("ptr", _leaf("1100.00")),
           "price_ladder", "line_items[0].ptr")


def test_a_rate_that_is_no_gst_slab_is_caught():
    _break(lambda b: b["line_items"][0].__setitem__("cgst_percent", _leaf("27.77")),
           "gst_rates", "line_items[0].cgst_percent")


def test_an_expiry_before_the_invoice_is_caught():
    # Overseas: its mfg date had been read as its expiry.
    _break(lambda b: b["line_items"][0].__setitem__("expiry", _leaf("Aug-25")),
           "dates", "line_items[0].expiry")


def test_a_bad_hsn_is_caught():
    _break(lambda b: b["line_items"][1].__setitem__("hsn", _leaf("300490")) or
           b["line_items"][1].__setitem__("hsn", _leaf("30049")),
           "hsn", "line_items[1].hsn")


def test_a_gstin_with_a_wrong_check_digit_is_caught():
    _break(lambda b: b["bill_to"].__setitem__("gstin", _leaf("27AAECD7847H1ZD")),
           "gstin_bill_to", "bill_to.gstin")


def test_a_pan_that_is_not_in_the_gstin_is_caught():
    _break(lambda b: b["supplier"].__setitem__("pan", _leaf("AAECD7847H")),
           "supplier_pan", "supplier.pan")


def test_what_the_bill_does_not_print_is_skipped_not_failed():
    bill = _clean_bill()
    for it in bill["line_items"]:
        for k in ("gross_amount", "discount_amount", "net_amount", "mrp", "ptr", "pts"):
            it.pop(k)
    v = verify_invoice(bill, {**REPORT, "stated_item_count": None}, today=TODAY)
    for check_id in ("line_net", "line_discount", "price_ladder", "item_count"):
        assert _check(v, check_id)["status"] == "skipped"
    assert v["verdict"] == "verified"


def test_failed_fields_are_flagged_for_review():
    bill = _clean_bill()
    bill["line_items"][0]["cgst_amount"] = _leaf("66.00")
    v = verify_invoice(bill, REPORT, today=TODAY)
    flag_failed_fields(bill, v)
    assert bill["line_items"][0]["cgst_amount"]["confidence"] < 0.5
    assert bill["line_items"][1]["cgst_amount"]["confidence"] == 1.0


def test_month_parsing_covers_the_forms_invoices_print():
    assert parse_month("Oct-27") == (2027, 10)
    assert parse_month("31-May-27") == (2027, 5)
    assert parse_month("11/2026") == (2026, 11)
    assert parse_month("04.2028") == (2028, 4)
    assert parse_month("22/08/2025") == (2025, 8)
    assert parse_month("2025-09-22") == (2025, 9)
    assert parse_month("rubbish") is None


def test_an_edit_reruns_the_checks():
    # A reviewer corrects the misread tax: its check turns green.
    bill = _clean_bill()
    bad = copy.deepcopy(bill)
    bad["line_items"][0]["cgst_amount"] = _leaf("66.00")
    v = verify_invoice(bad, REPORT, today=TODAY)
    old = {"fields": bad, "meta": {**REPORT, "verification": v}}
    assert _check(v, "line_tax")["status"] == "fail"
    meta = reverify("invoice", old, bill)
    assert _check(meta["verification"], "line_tax")["status"] == "pass"


def test_a_disputed_scan_value_corrected_in_review_counts_as_resolved():
    bill = _clean_bill()
    disputed = {"id": "cross_read", "label": "A second reading of the scan agrees",
                "status": "fail", "message": "...", "fields": ["invoice.total_amount"]}
    v = verify_invoice(bill, REPORT, today=TODAY, extra=[disputed])
    old = {"fields": copy.deepcopy(bill), "meta": {**REPORT, "verification": v}}
    untouched = reverify("invoice", old, copy.deepcopy(bill))
    assert _check(untouched["verification"], "cross_read")["status"] == "fail"
    edited = copy.deepcopy(bill)
    edited["invoice"]["total_amount"] = _leaf("2240.01")
    resolved = reverify("invoice", old, edited)
    assert _check(resolved["verification"], "cross_read")["status"] == "pass"


def test_open_checks_are_the_unacknowledged_failures():
    v = {"checks": [{"id": "a", "label": "A", "status": "fail"},
                    {"id": "b", "label": "B", "status": "fail"},
                    {"id": "c", "label": "C", "status": "pass"}],
         "acknowledged": [{"id": "a"}]}
    assert [c["id"] for c in open_checks(v)] == ["b"]


# --- the approval gate, through the API ----------------------------------------

def _invoice(client, headers):
    files = {"file": ("inv.png", sample_image("INVOICE"), "image/png")}
    doc_id = client.post("/v1/documents/", files=files, data={"doc_type": "invoice"},
                         headers=headers).json()["document_id"]
    return doc_id, client.get(f"/v1/documents/{doc_id}", headers=headers).json()


def test_approval_needs_every_failed_check_acknowledged(client, mock_ocr, db_session):
    headers = register_and_login(client)
    doc_id, doc = _invoice(client, headers)
    failed = [c["id"] for c in doc["payload"]["meta"]["verification"]["checks"]
              if c["status"] == "fail"]
    assert failed, "the mock invoice should fail at least one check"

    refused = client.post(f"/v1/documents/{doc_id}/approve", headers=headers)
    assert refused.status_code == 409
    assert {c["id"] for c in refused.json()["detail"]["open_checks"]} == set(failed)

    partly = client.post(f"/v1/documents/{doc_id}/approve", json={"acknowledged": failed[:-1]},
                         headers=headers)
    assert partly.status_code == 409

    ok = client.post(f"/v1/documents/{doc_id}/approve", json={"acknowledged": failed},
                     headers=headers)
    assert ok.status_code == 200
    record = ok.json()["payload"]["meta"]["verification"]["acknowledged"]
    assert {a["id"] for a in record} == set(failed)
    assert all(a["by"] and a["at"] for a in record)

    from app.models.audit_log import AuditLog
    s = db_session()
    try:
        acks = s.query(AuditLog).filter(AuditLog.action == "document.check_acknowledged",
                                        AuditLog.target == doc_id).count()
    finally:
        s.close()
    assert acks == len(failed)


def test_an_edit_reverifies_the_document(client, mock_ocr):
    headers = register_and_login(client)
    doc_id, doc = _invoice(client, headers)
    fields = doc["payload"]["fields"]
    fields["bill_to"] = {"gstin": {"value": "27AAECD7847H1ZD", "confidence": 1.0}}
    patched = client.patch(f"/v1/documents/{doc_id}", json={"fields": fields},
                           headers=headers).json()
    check = next(c for c in patched["payload"]["meta"]["verification"]["checks"]
                 if c["id"] == "gstin_bill_to")
    assert check["status"] == "fail"


# --- the lines' rates give the bill's tax -------------------------------------------

def _rate_bill(rates, cgst="357.96", sgst="357.96"):
    amounts = ["6070.00", "1785.00", "1070.00", "1735.50", "1856.00", "3392.50"]
    return {
        "invoice": {"total_taxable_amount": {"value": "14318.10"}, "total_cgst_amount": {"value": cgst},
                    "total_sgst_amount": {"value": sgst}},
        "line_items": [{"amount": {"value": a}, "gst_percent": {"value": r}} for a, r in zip(amounts, rates)],
    }


def _rates_check(fields, report=None):
    from app.services.ocr.verify import verify_invoice

    return next(c for c in verify_invoice(fields, report or {})["checks"] if c["id"] == "rates_give_tax")


def test_rates_that_give_the_bills_tax_pass():
    """MSV: 15,909 of lines less a 10% bill discount, all at 5%."""
    assert _rates_check(_rate_bill(["5"] * 6))["status"] == "pass"


def test_a_misread_rate_is_caught_without_line_tax_amounts():
    """MSV on production: two 5% lines read as 12% and 18%."""
    check = _rates_check(_rate_bill(["5", "5", "12", "5", "5", "18"]))
    assert check["status"] == "fail"
    assert "line_items[2].gst_percent" in check["fields"]


def test_rates_check_skips_what_it_cannot_test():
    f = _rate_bill(["5", "5", None, "5", "5", "5"])
    assert _rates_check(f)["status"] == "skipped"                 # a rate not read
    f = _rate_bill(["5"] * 6)
    f["invoice"]["total_taxable_amount"] = {"value": "20000.00"}  # not lines less a discount
    assert _rates_check(f)["status"] == "skipped"


def test_a_combined_sgst_utgst_figure_counts_once():
    f = _rate_bill(["5"] * 6)
    f["invoice"]["total_utgst_amount"] = {"value": "357.96"}
    assert _rates_check(f, {"sgst_utgst_combined": True})["status"] == "pass"
