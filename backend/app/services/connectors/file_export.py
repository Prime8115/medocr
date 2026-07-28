"""File-export connector: render CSV and/or JSON for the shop's software.

If config['output_dir'] is set, files are written there (e.g. a folder the
desktop agent or legacy software watches). The generated content is always
returned in the result so the web admin can offer a download.
"""
import csv
import io
import json
import os
from typing import List

from app.services.connectors.base import FAILED, SUCCESS, BaseConnector, DeliveryResult


def _flatten_field(field) -> str:
    if isinstance(field, dict):
        return "" if field.get("value") is None else str(field.get("value"))
    return "" if field is None else str(field)


def _rows_for(payload: dict) -> List[dict]:
    """Flatten a push payload into tabular rows (one per medication / line item)."""
    data = payload.get("data", {}) or {}
    doc_type = payload.get("doc_type")
    doc_id = payload.get("document_id", "")
    rows: List[dict] = []

    if doc_type == "invoice":
        supplier = _flatten_field((data.get("supplier") or {}).get("name"))
        inv = data.get("invoice") or {}
        invoice_no = _flatten_field(inv.get("invoice_no"))
        for item in data.get("line_items", []) or []:
            rows.append(
                {
                    "document_id": doc_id,
                    "supplier": supplier,
                    "invoice_no": invoice_no,
                    "description": _flatten_field(item.get("description")),
                    "batch_no": _flatten_field(item.get("batch_no")),
                    "expiry": _flatten_field(item.get("expiry")),
                    "quantity": _flatten_field(item.get("quantity")),
                    "mrp": _flatten_field(item.get("mrp")),
                    "rate": _flatten_field(item.get("rate")),
                    "amount": _flatten_field(item.get("amount")),
                    "hsn": _flatten_field(item.get("hsn")),
                    "gst_percent": _flatten_field(item.get("gst_percent")),
                }
            )
    else:  # prescription
        patient = _flatten_field((data.get("patient") or {}).get("name"))
        prescriber = _flatten_field((data.get("prescriber") or {}).get("name"))
        for med in data.get("medications", []) or []:
            rows.append(
                {
                    "document_id": doc_id,
                    "patient": patient,
                    "prescriber": prescriber,
                    "medication": _flatten_field(med.get("name")),
                    "strength": _flatten_field(med.get("strength")),
                    "form": _flatten_field(med.get("form")),
                    "frequency": _flatten_field(med.get("frequency")),
                    "duration": _flatten_field(med.get("duration")),
                    "instructions": _flatten_field(med.get("instructions")),
                }
            )
    return rows


def render_csv(payload: dict) -> str:
    rows = _rows_for(payload)
    if not rows:
        return ""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def render_json(payload: dict) -> str:
    return json.dumps(payload, default=str, indent=2)


class FileExportConnector(BaseConnector):
    type = "file_export"

    def __init__(self, config: dict):
        config = config or {}
        self.output_dir = config.get("output_dir")
        # formats: subset of {"csv", "json"}; default both
        self.formats = config.get("formats") or ["csv", "json"]

    def deliver(self, payload: dict) -> DeliveryResult:
        artifacts = {}
        if "csv" in self.formats:
            artifacts["csv"] = render_csv(payload)
        if "json" in self.formats:
            artifacts["json"] = render_json(payload)

        written = []
        if self.output_dir:
            try:
                os.makedirs(self.output_dir, exist_ok=True)
                stem = payload.get("document_id", "document")
                for fmt, content in artifacts.items():
                    path = os.path.join(self.output_dir, f"{stem}.{fmt}")
                    with open(path, "w", encoding="utf-8", newline="") as f:
                        f.write(content)
                    written.append(path)
            except OSError as exc:
                return DeliveryResult(status=FAILED, response_body=f"Write failed: {exc}")

        return DeliveryResult(
            status=SUCCESS,
            response_body=f"Generated {', '.join(artifacts)}"
            + (f"; wrote {len(written)} file(s)" if written else ""),
            detail={"written": written, "formats": list(artifacts.keys())},
        )
