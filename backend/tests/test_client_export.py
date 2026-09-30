"""The exact field set an integrating client specified.

33 header fields and 35 line fields, under their own column names. These tests
pin the contract: a rename or a dropped column here breaks someone's import, and
that is not the kind of thing to discover from a support call.

Their "Customer*" fields mean the SUPPLIER - on a purchase import that is the
party master the shop's software needs, and it is the party whose drug licence
numbers a pharma invoice prints. Confirmed with the client before wiring.
"""
import csv
import io

import pytest

from app.services.connectors.mapping import PROFILES, flatten_rows, render_csv

CLIENT_HEADER_COLUMNS = [
    "CustomerName", "InvoiceNo", "InvoiceDate", "FinalAmount", "BillToName", "ShipToName",
    "CustomerGSTINNO", "BillToGSTINNO", "ShipToGSTINNO", "TotalIGSTAmount",
    "TotalCGSTAmount", "TotalSGSTAmount", "TotalUTGSTAmount", "LRNO", "LRDate",
    "CustomerDLNo1", "CustomerDLDate1", "CustomerDLNo2", "CustomerDLDate2",
    "CustomerDLNo3", "CustomerDLDate3", "TotalDiscountAmount", "TotalTaxableAmount",
    "CustomerPANNO", "Transport", "BillToPanNo", "ShipToPanNo", "Email", "EwaybillNo",
    "IRNNO", "PONO", "PODATE", "DueDate",
]
CLIENT_LINE_COLUMNS = [
    "ProductCode", "ProductName", "HSN", "Qty", "FreeQty", "HsnCode", "BatchNo", "ExpDate",
    "MRP", "PTR", "PTS", "Rate", "SGST%", "CGST%", "IGST%", "UTGST%", "GrossAmount",
    "TaxableAmount", "Scheme%", "Scheme", "CD%", "CDAmount", "WP%", "WPAmount",
    "IGSTAmount", "CGSTAmount", "SCGSTAmount", "UTGSTAmount", "UOM", "MfgDate",
    "NetAmount", "MfgName", "Pack", "SchemeValue",
]


def _payload(fields):
    return {"doc_type": "invoice", "document_id": "doc_1", "data": fields}


SAMPLE = {
    "supplier": {
        "name": {"value": "ACME PHARMA LTD"}, "gstin": {"value": "27AABCA1234B1ZX"},
        "pan": {"value": "AABCA1234B"}, "email": {"value": "sales@acme.example"},
        "dl_no_1": {"value": "MH-TZ5-1111"}, "dl_date_1": {"value": "01.01.2030"},
        "dl_no_2": {"value": "MH-TZ5-2222"}, "dl_date_2": {"value": None},
        "dl_no_3": {"value": None}, "dl_date_3": {"value": None},
    },
    "bill_to": {"name": {"value": "SOME CHEMIST"}, "gstin": {"value": "27AAECD7847H1ZC"},
                "pan": {"value": "AAECD7847H"}},
    "ship_to": {"name": {"value": "SOME CHEMIST"}, "gstin": {"value": "27AAECD7847H1ZC"},
                "pan": {"value": "AAECD7847H"}},
    "invoice": {
        "invoice_no": {"value": "INV-1"}, "invoice_date": {"value": "12.08.2025"},
        "total_amount": {"value": "1120.00"}, "due_date": {"value": "12.09.2025"},
        "irn": {"value": "abc123def456abc1"}, "eway_bill_no": {"value": "123456789012"},
        "lr_no": {"value": "LR-9"}, "lr_date": {"value": "13.08.2025"},
        "transport": {"value": "QUICK LOGISTICS"}, "po_no": {"value": "PO-5"},
        "po_date": {"value": "10.08.2025"},
        "total_taxable_amount": {"value": "1000.00"}, "total_discount_amount": {"value": "0.00"},
        "total_cgst_amount": {"value": "60.00"}, "total_sgst_amount": {"value": "60.00"},
        "total_igst_amount": {"value": "0.00"}, "total_utgst_amount": {"value": None},
    },
    "line_items": [{
        "description": {"value": "MED 1"}, "product_code": {"value": "C1"},
        "manufacturer": {"value": "ACME"}, "hsn": {"value": "30049099"},
        "batch_no": {"value": "B1"}, "expiry": {"value": "01/2028"},
        "mfg_date": {"value": "01/2025"}, "pack": {"value": "10x10"}, "uom": {"value": "STP"},
        "quantity": {"value": "10"}, "free_quantity": {"value": "1"},
        "total_quantity": {"value": "11"}, "mrp": {"value": "150.00"},
        "ptr": {"value": "110.00"}, "pts": {"value": "100.00"}, "rate": {"value": "100.00"},
        "amount": {"value": "1000.00"}, "gross_amount": {"value": "1100.00"},
        "net_amount": {"value": "1120.00"}, "gst_percent": {"value": "12"},
        "cgst_percent": {"value": "6"}, "cgst_amount": {"value": "60.00"},
        "sgst_percent": {"value": "6"}, "sgst_amount": {"value": "60.00"},
        "igst_percent": {"value": "0"}, "igst_amount": {"value": "0.00"},
        "utgst_percent": {"value": None}, "utgst_amount": {"value": None},
        "scheme_percent": {"value": "10"}, "scheme": {"value": "10+1"},
        "scheme_value": {"value": "100.00"}, "cd_percent": {"value": "2"},
        "cd_amount": {"value": "20.00"}, "wp_percent": {"value": "1"},
        "wp_amount": {"value": "10.00"}, "discount_percent": {"value": "0"},
        "extras": [],
    }],
}


def test_profile_carries_every_column_the_client_asked_for():
    columns = PROFILES["client_full"]["invoice"]
    headers = [c["header"] for c in columns]
    for name in CLIENT_HEADER_COLUMNS + CLIENT_LINE_COLUMNS:
        assert name in headers, f"{name} is missing from the client profile"


def test_column_order_matches_the_specification():
    """Header block first, then the line block, in the order they were given."""
    headers = [c["header"] for c in PROFILES["client_full"]["invoice"]]
    assert headers[:len(CLIENT_HEADER_COLUMNS)] == CLIENT_HEADER_COLUMNS
    assert headers[len(CLIENT_HEADER_COLUMNS):] == CLIENT_LINE_COLUMNS


def test_every_column_resolves_to_a_real_value():
    """No column may be wired to a field name that never gets populated."""
    row = flatten_rows(_payload(SAMPLE))[0]
    unresolved = [
        c["header"] for c in PROFILES["client_full"]["invoice"]
        if c["field"] not in row
    ]
    assert unresolved == [], f"columns wired to a non-existent field: {unresolved}"


def test_customer_fields_carry_the_supplier_not_the_buyer():
    """The mapping confirmed with the client. Getting this backwards would file
    every purchase against the pharmacy's own name."""
    out = render_csv(_payload(SAMPLE), {"profile": "client_full"})
    header, first = list(csv.reader(io.StringIO(out)))[:2]
    row = dict(zip(header, first))

    assert row["CustomerName"] == "ACME PHARMA LTD"
    assert row["CustomerGSTINNO"] == "27AABCA1234B1ZX"
    assert row["CustomerPANNO"] == "AABCA1234B"
    assert row["CustomerDLNo1"] == "MH-TZ5-1111"
    # ...and the buyer stays on the Bill-to / Ship-to columns.
    assert row["BillToName"] == "SOME CHEMIST"
    assert row["BillToGSTINNO"] == "27AAECD7847H1ZC"
    assert row["ShipToGSTINNO"] == "27AAECD7847H1ZC"


def test_header_values_repeat_on_every_line():
    """A flat CSV has nowhere else to put them, and importers read them per row."""
    fields = {**SAMPLE, "line_items": SAMPLE["line_items"] * 3}
    out = render_csv(_payload(fields), {"profile": "client_full"})
    rows = list(csv.reader(io.StringIO(out)))
    header, body = rows[0], rows[1:]
    assert len(body) == 3
    index = header.index("InvoiceNo")
    assert {r[index] for r in body} == {"INV-1"}


def test_line_money_columns_are_distinct():
    """Gross, taxable and net are three different numbers, and the client asked
    for all three. Collapsing any two of them silently corrupts the import."""
    out = render_csv(_payload(SAMPLE), {"profile": "client_full"})
    header, first = list(csv.reader(io.StringIO(out)))[:2]
    row = dict(zip(header, first))
    assert row["GrossAmount"] == "1100.00"
    assert row["TaxableAmount"] == "1000.00"
    assert row["NetAmount"] == "1120.00"


def test_tax_heads_are_reported_separately():
    out = render_csv(_payload(SAMPLE), {"profile": "client_full"})
    header, first = list(csv.reader(io.StringIO(out)))[:2]
    row = dict(zip(header, first))
    assert (row["CGST%"], row["CGSTAmount"]) == ("6", "60.00")
    # Their list spells this one "SCGSTAmount"; it is the SGST amount.
    assert (row["SGST%"], row["SCGSTAmount"]) == ("6", "60.00")
    assert (row["IGST%"], row["IGSTAmount"]) == ("0", "0.00")


def test_existing_profiles_are_untouched():
    """Their column shape is a contract with whatever each shop already imports."""
    header = render_csv(_payload(SAMPLE), {"profile": "generic"}).splitlines()[0]
    assert header == "Supplier,Invoice No,Item,Batch,Expiry,Qty,MRP,Rate,Amount,HSN,GST%"


@pytest.mark.parametrize("profile", ["generic", "detailed", "marg", "vyapar", "tally"])
def test_other_profiles_still_render(profile):
    assert render_csv(_payload(SAMPLE), {"profile": profile}).strip()


# ----------------------- the same data over the API connector -----------------------
class _Doc:
    """Just enough of a Document row for the payload builder."""

    def __init__(self, fields):
        self.id = "doc_1"
        self.shop_id = "shop_1"
        self.doc_type = "invoice"
        self.overall_confidence = 1.0
        self.payload = {"schema_version": "1.0", "fields": fields, "meta": {}}


def test_webhook_payload_is_unchanged_without_a_profile():
    """The nested contract integrators already build against must not move."""
    from app.services.connectors.payload import build_push_payload

    out = build_push_payload(_Doc(SAMPLE))
    assert "rows" not in out
    assert out["data"]["supplier"]["name"]["value"] == "ACME PHARMA LTD"


def test_webhook_payload_carries_the_client_columns_when_asked():
    """A connector set to the client's profile receives the same flat rows the
    CSV export produces - their own field names, over HTTP."""
    from app.services.connectors.payload import build_push_payload

    out = build_push_payload(_Doc(SAMPLE), {"profile": "client_full"})
    assert out["row_columns"][:4] == ["CustomerName", "InvoiceNo", "InvoiceDate", "FinalAmount"]
    assert len(out["rows"]) == len(SAMPLE["line_items"])

    row = out["rows"][0]
    assert row["CustomerName"] == "ACME PHARMA LTD"
    assert row["CustomerGSTINNO"] == "27AABCA1234B1ZX"
    assert row["BillToName"] == "SOME CHEMIST"
    assert row["ProductName"] == "MED 1"
    assert row["TaxableAmount"] == "1000.00"
    # The nested data is still there alongside it.
    assert out["data"]["line_items"][0]["batch_no"]["value"] == "B1"


def test_api_rows_match_the_csv_exactly():
    """One extraction, two transports, identical values - otherwise a shop
    reconciling a CSV against the API sees two different answers."""
    from app.services.connectors.payload import build_push_payload

    api = build_push_payload(_Doc(SAMPLE), {"profile": "client_full"})["rows"]
    out = render_csv(_payload(SAMPLE), {"profile": "client_full"})
    header, *body = list(csv.reader(io.StringIO(out)))
    assert [dict(zip(header, r)) for r in body] == api


def test_a_custom_column_list_also_works_over_the_api():
    from app.services.connectors.payload import build_push_payload

    config = {"columns": [{"header": "Item", "field": "description"},
                          {"header": "Qty", "field": "quantity"}]}
    out = build_push_payload(_Doc(SAMPLE), config)
    assert out["row_columns"] == ["Item", "Qty"]
    assert out["rows"] == [{"Item": "MED 1", "Qty": "10"}]
