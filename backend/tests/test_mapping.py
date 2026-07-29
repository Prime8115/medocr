"""Configurable export mapping: profiles, custom columns, formats, delimiters."""
import csv
import io

from app.services.connectors import mapping

INVOICE = {
    "document_id": "doc_i",
    "doc_type": "invoice",
    "data": {
        "supplier": {"name": {"value": "MediSupply"}},
        "invoice": {"invoice_no": {"value": "INV-9"}},
        "line_items": [
            {"description": {"value": "Paracetamol 500"}, "batch_no": {"value": "B1"},
             "expiry": {"value": "12/2027"}, "quantity": {"value": "100"}, "mrp": {"value": "2.5"},
             "rate": {"value": "1.8"}, "amount": {"value": "180"}, "hsn": {"value": "3004"}, "gst_percent": {"value": "12"}},
            {"description": {"value": "Amoxicillin 250"}, "batch_no": {"value": "B2"},
             "quantity": {"value": "50"}, "rate": {"value": "3.0"}},
        ],
    },
}


def test_flatten_rows_one_per_line_item():
    rows = mapping.flatten_rows(INVOICE)
    assert len(rows) == 2
    assert rows[0]["description"] == "Paracetamol 500"
    assert rows[1]["batch_no"] == "B2"


def test_marg_profile_headers():
    out = mapping.render_csv(INVOICE, {"profile": "marg"})
    header = out.splitlines()[0].split(",")
    assert "ItemName" in header and "PRate" in header and "Batch" in header


def test_vyapar_profile_headers():
    header = mapping.render_csv(INVOICE, {"profile": "vyapar"}).splitlines()[0]
    assert "Item Name" in header and "Purchase Price" in header


def test_custom_columns_override_profile():
    cfg = {"columns": [
        {"header": "Product", "field": "description"},
        {"header": "Nos", "field": "quantity"},
    ]}
    rows = list(csv.DictReader(io.StringIO(mapping.render_csv(INVOICE, cfg))))
    assert list(rows[0].keys()) == ["Product", "Nos"]
    assert rows[0]["Product"] == "Paracetamol 500"
    assert rows[0]["Nos"] == "100"


def test_custom_delimiter_and_no_header():
    cfg = {"columns": [{"header": "Item", "field": "description"}, {"header": "Qty", "field": "quantity"}],
           "delimiter": "|", "include_header": False}
    out = mapping.render_csv(INVOICE, cfg)
    lines = out.strip().splitlines()
    assert lines[0] == "Paracetamol 500|100"
    assert len(lines) == 2  # no header row


def test_tally_xml_contains_voucher_and_items():
    xml = mapping.render_tally_xml(INVOICE, {})
    assert "<VOUCHER" in xml and "Purchase" in xml
    assert "Paracetamol 500" in xml
    assert xml.count("ALLINVENTORYENTRIES.LIST") == 4  # open+close per 2 items


def test_render_dispatches_by_format():
    assert "csv" in mapping.render(INVOICE, {"format": "csv"})
    assert "json" in mapping.render(INVOICE, {"format": "json"})
    assert "xml" in mapping.render(INVOICE, {"format": "tally_xml"})
    both = mapping.render(INVOICE, {"format": "both"})
    assert "csv" in both and "json" in both
