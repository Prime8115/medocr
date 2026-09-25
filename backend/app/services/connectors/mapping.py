"""Template-driven export mapping.

Makes MediScan compatible with any file-importing pharmacy software by turning
the connection into a *configuration* rather than code: pick a format (CSV / JSON
/ Tally XML), choose or define the column mapping, set delimiter / header / date
format. Popular software ships as ready-made PROFILES; anything else is a custom
column list.

A connector's `config` may contain:
    {
      "format": "csv" | "json" | "tally_xml",   # default csv
      "profile": "marg" | "vyapar" | "tally" | "generic",   # optional preset
      "delimiter": ",",           # csv only
      "include_header": true,     # csv only
      "columns": [ {"header": "Item Name", "field": "medication"}, ... ],
      "output_dir": "C:\\...\\import"   # optional (handled by the connector)
    }

`columns` (if present) overrides the profile. Each `field` references a flat row
key produced from the normalized payload (see FIELD_KEYS below).
"""
import csv
import io
import json
from typing import Dict, List
from xml.sax.saxutils import escape

# Flat row keys available per doc type (produced by flatten_rows).
FIELD_KEYS = {
    "prescription": [
        "document_id", "patient", "prescriber", "medication",
        "strength", "form", "frequency", "duration", "instructions",
    ],
    # `rate` is the billed rate and is always populated; `rate_source` says which
    # column it came from ("RATE", "PTR", "PTS"). ptr/pts/free_quantity/pack/
    # discount_percent are additive - existing profiles are unchanged, but a
    # custom `columns` config can now reference them.
    "invoice": [
        "document_id", "supplier", "supplier_gstin", "invoice_no", "invoice_date",
        "description", "product_code", "manufacturer", "pack", "uom", "batch_no",
        "expiry", "mfg_date", "quantity", "free_quantity", "total_quantity",
        "mrp", "ptr", "pts", "rate", "rate_source", "discount_percent",
        "discount_amount", "scheme_percent", "amount", "free_supply", "hsn",
        "gst_percent", "cgst_percent", "cgst_amount", "sgst_percent", "sgst_amount",
        "igst_percent", "igst_amount",
    ],
}


def _v(field) -> str:
    if isinstance(field, dict):
        return "" if field.get("value") is None else str(field.get("value"))
    return "" if field is None else str(field)


def flatten_rows(payload: dict) -> List[Dict[str, str]]:
    """Flatten a push payload into one row per medication / invoice line item."""
    data = payload.get("data", {}) or {}
    doc_type = payload.get("doc_type")
    doc_id = payload.get("document_id", "")
    rows: List[Dict[str, str]] = []

    if doc_type == "invoice":
        supplier = _v((data.get("supplier") or {}).get("name"))
        supplier_gstin = _v((data.get("supplier") or {}).get("gstin"))
        inv = data.get("invoice") or {}
        invoice_no = _v(inv.get("invoice_no"))
        invoice_date = _v(inv.get("invoice_date"))
        for item in data.get("line_items", []) or []:
            rows.append({
                "document_id": doc_id, "supplier": supplier,
                "invoice_no": invoice_no, "invoice_date": invoice_date,
                "description": _v(item.get("description")), "batch_no": _v(item.get("batch_no")),
                "expiry": _v(item.get("expiry")), "quantity": _v(item.get("quantity")),
                "free_quantity": _v(item.get("free_quantity")), "pack": _v(item.get("pack")),
                "mrp": _v(item.get("mrp")), "ptr": _v(item.get("ptr")), "pts": _v(item.get("pts")),
                "rate": _v(item.get("rate")), "rate_source": _v(item.get("rate_source")),
                "discount_percent": _v(item.get("discount_percent")),
                "amount": _v(item.get("amount")), "hsn": _v(item.get("hsn")),
                "gst_percent": _v(item.get("gst_percent")),
                "supplier_gstin": supplier_gstin,
                "product_code": _v(item.get("product_code")),
                "manufacturer": _v(item.get("manufacturer")),
                "uom": _v(item.get("uom")), "mfg_date": _v(item.get("mfg_date")),
                "total_quantity": _v(item.get("total_quantity")),
                "discount_amount": _v(item.get("discount_amount")),
                "scheme_percent": _v(item.get("scheme_percent")),
                "free_supply": _v(item.get("free_supply")),
                "cgst_percent": _v(item.get("cgst_percent")),
                "cgst_amount": _v(item.get("cgst_amount")),
                "sgst_percent": _v(item.get("sgst_percent")),
                "sgst_amount": _v(item.get("sgst_amount")),
                "igst_percent": _v(item.get("igst_percent")),
                "igst_amount": _v(item.get("igst_amount")),
                # Columns we had no name for, flattened as "Heading: value" so a
                # layout we have never seen still reaches the shop's software.
                "extra_columns": "; ".join(
                    f"{e.get('label')}: {e.get('value')}"
                    for e in (item.get("extras") or [])
                    if e.get("label") and e.get("value")
                ),
            })
    else:  # prescription
        patient = _v((data.get("patient") or {}).get("name"))
        prescriber = _v((data.get("prescriber") or {}).get("name"))
        for med in data.get("medications", []) or []:
            rows.append({
                "document_id": doc_id, "patient": patient, "prescriber": prescriber,
                "medication": _v(med.get("name")), "strength": _v(med.get("strength")),
                "form": _v(med.get("form")), "frequency": _v(med.get("frequency")),
                "duration": _v(med.get("duration")), "instructions": _v(med.get("instructions")),
            })
    return rows


# --- Ready-made profiles: {profile: {doc_type: [ {header, field}, ... ] }} ---
# Column headers chosen to match each software's typical import layout. These are
# sensible starting points; exact headers can be overridden per connection.
PROFILES: Dict[str, Dict[str, List[dict]]] = {
    "generic": {
        "invoice": [
            {"header": "Supplier", "field": "supplier"},
            {"header": "Invoice No", "field": "invoice_no"},
            {"header": "Item", "field": "description"},
            {"header": "Batch", "field": "batch_no"},
            {"header": "Expiry", "field": "expiry"},
            {"header": "Qty", "field": "quantity"},
            {"header": "MRP", "field": "mrp"},
            {"header": "Rate", "field": "rate"},
            {"header": "Amount", "field": "amount"},
            {"header": "HSN", "field": "hsn"},
            {"header": "GST%", "field": "gst_percent"},
        ],
        "prescription": [
            {"header": "Patient", "field": "patient"},
            {"header": "Doctor", "field": "prescriber"},
            {"header": "Medicine", "field": "medication"},
            {"header": "Strength", "field": "strength"},
            {"header": "Frequency", "field": "frequency"},
            {"header": "Duration", "field": "duration"},
        ],
    },
    # Everything we extract, for shops whose software can take it. Scheme goods
    # (free_quantity) are the reason this exists: a shop that receives 10+2 and
    # imports only the billed 10 has wrong stock from day one. Kept as a separate
    # profile rather than added to "generic", because changing the column shape
    # of a profile a shop already imports would break their import.
    "detailed": {
        "invoice": [
            {"header": "Supplier", "field": "supplier"},
            {"header": "Invoice No", "field": "invoice_no"},
            {"header": "Invoice Date", "field": "invoice_date"},
            {"header": "Item", "field": "description"},
            {"header": "Pack", "field": "pack"},
            {"header": "Batch", "field": "batch_no"},
            {"header": "Expiry", "field": "expiry"},
            {"header": "Qty", "field": "quantity"},
            {"header": "Free Qty", "field": "free_quantity"},
            {"header": "MRP", "field": "mrp"},
            {"header": "PTR", "field": "ptr"},
            {"header": "PTS", "field": "pts"},
            {"header": "Rate", "field": "rate"},
            {"header": "Rate Source", "field": "rate_source"},
            {"header": "Disc%", "field": "discount_percent"},
            {"header": "Amount", "field": "amount"},
            {"header": "HSN", "field": "hsn"},
            {"header": "GST%", "field": "gst_percent"},
            {"header": "Product Code", "field": "product_code"},
            {"header": "Manufacturer", "field": "manufacturer"},
            {"header": "UOM", "field": "uom"},
            {"header": "Mfg Date", "field": "mfg_date"},
            {"header": "Total Qty", "field": "total_quantity"},
            {"header": "Disc Amt", "field": "discount_amount"},
            {"header": "Scheme%", "field": "scheme_percent"},
            {"header": "Free Supply", "field": "free_supply"},
            {"header": "CGST%", "field": "cgst_percent"},
            {"header": "CGST Amt", "field": "cgst_amount"},
            {"header": "SGST%", "field": "sgst_percent"},
            {"header": "SGST Amt", "field": "sgst_amount"},
            {"header": "IGST%", "field": "igst_percent"},
            {"header": "IGST Amt", "field": "igst_amount"},
            {"header": "Supplier GSTIN", "field": "supplier_gstin"},
            {"header": "Other Columns", "field": "extra_columns"},
        ],
        "prescription": [
            {"header": "Patient", "field": "patient"},
            {"header": "Doctor", "field": "prescriber"},
            {"header": "Medicine", "field": "medication"},
            {"header": "Strength", "field": "strength"},
            {"header": "Form", "field": "form"},
            {"header": "Frequency", "field": "frequency"},
            {"header": "Duration", "field": "duration"},
            {"header": "Instructions", "field": "instructions"},
        ],
    },
    "marg": {  # Marg ERP purchase import (item-wise)
        "invoice": [
            {"header": "ItemName", "field": "description"},
            {"header": "Batch", "field": "batch_no"},
            {"header": "Exp", "field": "expiry"},
            {"header": "Qty", "field": "quantity"},
            {"header": "MRP", "field": "mrp"},
            {"header": "PRate", "field": "rate"},
            {"header": "Amount", "field": "amount"},
            {"header": "HSN", "field": "hsn"},
            {"header": "GST", "field": "gst_percent"},
        ],
    },
    "vyapar": {  # Vyapar item/purchase import
        "invoice": [
            {"header": "Item Name", "field": "description"},
            {"header": "Batch Number", "field": "batch_no"},
            {"header": "Expiry Date", "field": "expiry"},
            {"header": "Quantity", "field": "quantity"},
            {"header": "MRP", "field": "mrp"},
            {"header": "Purchase Price", "field": "rate"},
            {"header": "Amount", "field": "amount"},
            {"header": "HSN", "field": "hsn"},
            {"header": "GST %", "field": "gst_percent"},
        ],
    },
    "tally": {  # Tally is handled specially via tally_xml, but provide CSV too
        "invoice": [
            {"header": "Stock Item", "field": "description"},
            {"header": "Batch", "field": "batch_no"},
            {"header": "Expiry", "field": "expiry"},
            {"header": "Quantity", "field": "quantity"},
            {"header": "Rate", "field": "rate"},
            {"header": "Amount", "field": "amount"},
        ],
    },
}


def resolve_columns(config: dict, doc_type: str) -> List[dict]:
    """Determine the column list: explicit `columns` > profile > generic."""
    if config.get("columns"):
        return config["columns"]
    profile = (config.get("profile") or "generic").lower()
    prof = PROFILES.get(profile) or PROFILES["generic"]
    return prof.get(doc_type) or PROFILES["generic"].get(doc_type, [])


def render_csv(payload: dict, config: dict) -> str:
    doc_type = payload.get("doc_type", "prescription")
    rows = flatten_rows(payload)
    columns = resolve_columns(config, doc_type)
    delimiter = config.get("delimiter", ",")
    include_header = config.get("include_header", True)

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=delimiter)
    if include_header:
        writer.writerow([c["header"] for c in columns])
    for row in rows:
        writer.writerow([row.get(c["field"], "") for c in columns])
    return buf.getvalue()


def render_json(payload: dict, config: dict) -> str:
    doc_type = payload.get("doc_type", "prescription")
    rows = flatten_rows(payload)
    columns = resolve_columns(config, doc_type)
    mapped = [{c["header"]: row.get(c["field"], "") for c in columns} for row in rows]
    return json.dumps(mapped, indent=2)


def render_tally_xml(payload: dict, config: dict) -> str:
    """Minimal Tally-importable XML (purchase voucher with inventory entries).

    A pragmatic starting template — real Tally setups often need company-specific
    ledger/godown names, which can be set via config['tally'] overrides.
    """
    data = payload.get("data", {}) or {}
    inv = data.get("invoice") or {}
    supplier = _v((data.get("supplier") or {}).get("name")) or "Sundry Creditor"
    invoice_no = _v(inv.get("invoice_no"))
    tconf = config.get("tally", {}) or {}
    purchase_ledger = tconf.get("purchase_ledger", "Purchase")

    items_xml = []
    for item in data.get("line_items", []) or []:
        name = escape(_v(item.get("description")))
        qty = _v(item.get("quantity")) or "0"
        rate = _v(item.get("rate")) or "0"
        amount = _v(item.get("amount")) or "0"
        items_xml.append(
            f"""      <ALLINVENTORYENTRIES.LIST>
        <STOCKITEMNAME>{name}</STOCKITEMNAME>
        <ACTUALQTY>{escape(qty)}</ACTUALQTY>
        <BILLEDQTY>{escape(qty)}</BILLEDQTY>
        <RATE>{escape(rate)}</RATE>
        <AMOUNT>{escape(amount)}</AMOUNT>
      </ALLINVENTORYENTRIES.LIST>"""
        )

    return f"""<ENVELOPE>
  <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
  <BODY><IMPORTDATA>
    <REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME></REQUESTDESC>
    <REQUESTDATA>
      <TALLYMESSAGE>
        <VOUCHER VCHTYPE="Purchase" ACTION="Create">
          <PARTYLEDGERNAME>{escape(supplier)}</PARTYLEDGERNAME>
          <VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>
          <REFERENCE>{escape(invoice_no)}</REFERENCE>
          <PURCHASELEDGER>{escape(purchase_ledger)}</PURCHASELEDGER>
{chr(10).join(items_xml)}
        </VOUCHER>
      </TALLYMESSAGE>
    </REQUESTDATA>
  </IMPORTDATA></BODY>
</ENVELOPE>"""


def render(payload: dict, config: dict) -> Dict[str, str]:
    """Return {extension: content} for the configured format(s)."""
    fmt = (config.get("format") or "csv").lower()
    if fmt == "json":
        return {"json": render_json(payload, config)}
    if fmt == "tally_xml":
        return {"xml": render_tally_xml(payload, config)}
    if fmt == "both":
        return {"csv": render_csv(payload, config), "json": render_json(payload, config)}
    return {"csv": render_csv(payload, config)}


AVAILABLE_PROFILES = list(PROFILES.keys())
AVAILABLE_FORMATS = ["csv", "json", "tally_xml", "both"]
