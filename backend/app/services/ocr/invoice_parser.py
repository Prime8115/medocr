"""Deterministic invoice parser for digital (computer-generated) PDFs.

Reads the line-item table directly from the PDF using pdfplumber — no AI, no
per-call cost, no page limit, no rate limits, exact values. Works for any digital
distributor invoice whose columns can be recognised (Product/Item, Batch, Expiry,
Qty, MRP, Rate, HSN, GST, Amount). Falls back to the AI pipeline when the table
can't be recognised (scanned/photographed invoices, or unusual layouts).
"""
import re
from typing import List, Optional

# field -> (include keywords, exclude keywords). Header cells are normalized to
# lowercase with spaces/newlines/dots removed before matching.
_COLS = {
    "description": (["productname", "product", "itemname", "item", "description", "particulars", "goods", "medicine"], ["hsn", "code"]),
    "hsn": (["hsn"], []),
    "batch_no": (["batch", "batchno"], []),
    "expiry": (["exp", "expiry", "expdate"], ["mfg"]),
    "mrp": (["mrp"], []),
    "rate": (["ptr", "prate", "purchaserate", "purchase", "rate", "unitprice"], ["%", "mrp"]),
    "quantity": (["qty", "quantity", "qty."], ["free", "%"]),
    "amount": (["value", "netamount", "netamt", "amount", "taxableamt", "total"], ["%"]),
}


def _norm(cell) -> str:
    if cell is None:
        return ""
    return re.sub(r"[\s\.\n]+", "", str(cell)).lower()


def _clean(cell) -> str:
    if cell is None:
        return ""
    return re.sub(r"\s+", " ", str(cell)).strip()


def _find_header_row(table) -> Optional[int]:
    """Row index of the column-header row (has batch + a qty/product marker)."""
    for i, row in enumerate(table):
        norms = [_norm(c) for c in row]
        joined = " ".join(norms)
        if "batch" in joined and ("qty" in joined or "productname" in joined or "product" in joined or "item" in joined):
            return i
    return None


def _map_columns(header_row) -> dict:
    """Map our field -> column index using header keywords."""
    norms = [_norm(c) for c in header_row]
    mapping = {}
    for field, (inc, exc) in _COLS.items():
        for idx, h in enumerate(norms):
            if not h or idx in mapping.values():
                continue
            if any(k in h for k in inc) and not any(e in h for e in exc):
                mapping[field] = idx
                break
    return mapping


def _gst_columns(header_row) -> List[int]:
    """Indices of GST-percentage columns (CGST%/SGST%/IGST%/GST%) to sum."""
    out = []
    for idx, c in enumerate(header_row):
        h = _norm(c)
        if ("gst" in h or "igst" in h) and "%" in h and "amt" not in h:
            out.append(idx)
    return out


_NUM = re.compile(r"-?[\d,]*\.?\d+")


def _num(cell) -> Optional[str]:
    m = _NUM.search(str(cell or ""))
    return m.group().replace(",", "") if m else None


def _f(value):
    return {"value": value if value not in (None, "") else None, "confidence": 1.0}


def _extract_header_meta(text: str) -> dict:
    """Best-effort supplier + invoice number/date from the page text."""
    supplier = None
    for line in (text or "").splitlines():
        s = line.strip()
        if re.search(r"\b(LIMITED|LTD|PVT|PRIVATE|DISTRIBUTOR|PHARMA|HEALTHCARE|ENTERPRISE|AGENC)", s, re.I):
            # Trim trailing document-type words that share the line.
            supplier = re.split(r"\b(TAX\s*INVOICE|INVOICE|ORIGINAL|DUPLICATE|CREDIT\s*NOTE)\b", s, flags=re.I)[0].strip(" -|")
            break
    inv_no = re.search(r"invoice\s*no\.?\s*[:\-]?\s*([A-Za-z0-9\-\/]+)", text or "", re.I)
    inv_dt = re.search(r"invoice\s*no.*?dt\.?\s*[:\-]?\s*([0-9][0-9./\-]{6,})", text or "", re.I)
    gstin = re.search(r"GSTIN\s*[:\-]?\s*([0-9A-Z]{15})", text or "", re.I)
    return {
        "supplier": {"name": _f(supplier), "gstin": _f(gstin.group(1) if gstin else None), "address": _f(None)},
        "invoice": {
            "invoice_no": _f(inv_no.group(1) if inv_no else None),
            "invoice_date": _f(inv_dt.group(1) if inv_dt else None),
            "total_amount": _f(None),
        },
    }


def parse_invoice_pdf(data: bytes) -> Optional[dict]:
    """Return an invoice `fields` dict parsed deterministically, or None if the
    table can't be recognised (caller then falls back to the AI pipeline)."""
    try:
        import io
        import pdfplumber
    except ImportError:  # pragma: no cover
        return None

    line_items: List[dict] = []
    meta = None
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                if meta is None:
                    meta = _extract_header_meta(page.extract_text() or "")
                for table in page.extract_tables() or []:
                    hi = _find_header_row(table)
                    if hi is None:
                        continue
                    cols = _map_columns(table[hi])
                    if "description" not in cols or ("quantity" not in cols and "batch_no" not in cols):
                        continue  # not a line-item table we understand
                    gst_cols = _gst_columns(table[hi])
                    for row in table[hi + 1:]:
                        desc = _clean(row[cols["description"]]) if cols.get("description") is not None and cols["description"] < len(row) else ""
                        if not desc or len(desc) < 2:
                            continue
                        # Skip total/summary rows.
                        if re.search(r"\b(total|grand|net amount|subtotal|carried)\b", desc, re.I):
                            continue
                        item = {"description": _f(desc)}
                        for field in ("batch_no", "expiry", "hsn"):
                            idx = cols.get(field)
                            if idx is not None and idx < len(row):
                                item[field] = _f(_clean(row[idx]))
                        for field in ("quantity", "mrp", "rate", "amount"):
                            idx = cols.get(field)
                            if idx is not None and idx < len(row):
                                item[field] = _f(_num(row[idx]))
                        # GST% = sum of CGST%+SGST% (or IGST%) columns.
                        gst_vals = []
                        for gi in gst_cols:
                            if gi < len(row):
                                n = _num(row[gi])
                                if n:
                                    gst_vals.append(float(n))
                        if gst_vals:
                            item["gst_percent"] = _f(str(round(sum(gst_vals), 2)))
                        # Only keep rows that have at least a quantity or a batch.
                        if item.get("quantity", {}).get("value") or item.get("batch_no", {}).get("value"):
                            line_items.append(item)
    except Exception:  # noqa: BLE001 - any parsing failure -> fall back to AI
        return None

    if not line_items:
        return None

    fields = meta or {"supplier": {}, "invoice": {}}
    fields["line_items"] = line_items
    return fields
