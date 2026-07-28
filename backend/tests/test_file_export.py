"""File-export connector: CSV/JSON rendering and folder writing."""
import csv
import io
import json

from app.services.connectors.base import SUCCESS
from app.services.connectors.file_export import (
    FileExportConnector,
    render_csv,
    render_json,
)

PRESCRIPTION_PAYLOAD = {
    "payload_version": "1.0",
    "document_id": "doc_rx1",
    "doc_type": "prescription",
    "data": {
        "patient": {"name": {"value": "Ramesh"}},
        "prescriber": {"name": {"value": "Dr. Sharma"}},
        "medications": [
            {"name": {"value": "Paracetamol"}, "strength": {"value": "500 mg"}, "frequency": {"value": "1-0-1"}}
        ],
    },
}

INVOICE_PAYLOAD = {
    "payload_version": "1.0",
    "document_id": "doc_inv1",
    "doc_type": "invoice",
    "data": {
        "supplier": {"name": {"value": "MediSupply"}},
        "invoice": {"invoice_no": {"value": "INV-1"}},
        "line_items": [
            {"description": {"value": "Paracetamol"}, "batch_no": {"value": "B1"}, "quantity": {"value": "100"}}
        ],
    },
}


def test_render_csv_prescription():
    out = render_csv(PRESCRIPTION_PAYLOAD)
    rows = list(csv.DictReader(io.StringIO(out)))
    assert len(rows) == 1
    assert rows[0]["patient"] == "Ramesh"
    assert rows[0]["medication"] == "Paracetamol"
    assert rows[0]["strength"] == "500 mg"


def test_render_csv_invoice():
    rows = list(csv.DictReader(io.StringIO(render_csv(INVOICE_PAYLOAD))))
    assert rows[0]["description"] == "Paracetamol"
    assert rows[0]["batch_no"] == "B1"
    assert rows[0]["quantity"] == "100"


def test_render_json_roundtrips():
    parsed = json.loads(render_json(PRESCRIPTION_PAYLOAD))
    assert parsed["document_id"] == "doc_rx1"


def test_file_export_writes_files(tmp_path):
    conn = FileExportConnector({"output_dir": str(tmp_path), "formats": ["csv", "json"]})
    result = conn.deliver(PRESCRIPTION_PAYLOAD)
    assert result.status == SUCCESS
    written = {p.name for p in tmp_path.iterdir()}
    assert "doc_rx1.csv" in written
    assert "doc_rx1.json" in written


def test_file_export_without_dir_still_generates(tmp_path):
    conn = FileExportConnector({"formats": ["csv"]})
    result = conn.deliver(PRESCRIPTION_PAYLOAD)
    assert result.status == SUCCESS
    assert "csv" in result.detail["formats"]
    assert result.detail["written"] == []
