"""File-export connector: mapping-driven CSV/JSON rendering and folder writing."""
import csv
import io
import json

from app.services.connectors.base import FAILED, SUCCESS
from app.services.connectors.file_export import FileExportConnector
from app.services.connectors import mapping

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


def test_default_csv_prescription_uses_generic_profile():
    rows = list(csv.DictReader(io.StringIO(mapping.render(PRESCRIPTION_PAYLOAD, {"format": "csv"})["csv"])))
    assert rows[0]["Patient"] == "Ramesh"
    assert rows[0]["Medicine"] == "Paracetamol"
    assert rows[0]["Strength"] == "500 mg"


def test_default_csv_invoice():
    rows = list(csv.DictReader(io.StringIO(mapping.render(INVOICE_PAYLOAD, {"format": "csv"})["csv"])))
    assert rows[0]["Item"] == "Paracetamol"
    assert rows[0]["Batch"] == "B1"
    assert rows[0]["Qty"] == "100"


def test_json_format_is_mapped():
    parsed = json.loads(mapping.render(INVOICE_PAYLOAD, {"format": "json"})["json"])
    assert parsed[0]["Item"] == "Paracetamol"


def test_writes_files_to_folder(tmp_path):
    conn = FileExportConnector({"format": "both", "output_dir": str(tmp_path)})
    result = conn.deliver(PRESCRIPTION_PAYLOAD)
    assert result.status == SUCCESS
    names = {p.name for p in tmp_path.iterdir()}
    assert "doc_rx1.csv" in names
    assert "doc_rx1.json" in names


def test_connection_test_does_not_create_import_file(tmp_path):
    conn = FileExportConnector({"format": "json", "output_dir": str(tmp_path)})
    assert conn.test().status == SUCCESS
    assert list(tmp_path.iterdir()) == []


def test_without_dir_fails_instead_of_claiming_delivery():
    conn = FileExportConnector({"format": "csv"})
    result = conn.deliver(PRESCRIPTION_PAYLOAD)
    assert result.status == FAILED
    assert result.delivery_state == "failed"
    assert "No server-accessible output folder" in result.response_body
