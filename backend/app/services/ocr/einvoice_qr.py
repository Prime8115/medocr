"""The e-invoice's signed QR code: what the GST portal itself registered.

Every e-invoice (IRN) carries a QR code holding a JSON Web Token signed by the
Invoice Registration Portal: the IRN, the invoice number and date, both
GSTINs, the invoice value and the item count. Unlike anything read off the
page, these cannot be misread by a scanner or a model - the token decodes or
it does not.

Meridian prints its IRN tiny and sideways; the AI read one character of the 64
wrong. The QR beside it holds the exact value. So the QR is read once per
document and its figures are used where the reading is blank or (for the IRN)
wrong, and compared where the reading has its own.
"""
import base64
import contextvars
import json
import logging
import re
from typing import Dict, Optional

log = logging.getLogger(__name__)

# The QR's figures for the document being read (process_document sets it).
EINVOICE_QR: contextvars.ContextVar = contextvars.ContextVar("einvoice_qr", default=None)


def _token_data(text: str) -> Optional[dict]:
    """The signed payload of an e-invoice QR, or None for any other code."""
    parts = (text or "").strip().split(".")
    if len(parts) != 3:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        data = payload.get("data")
        data = json.loads(data) if isinstance(data, str) else data
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or not data.get("Irn"):
        return None
    return data


def read(file_bytes: bytes, content_type: str) -> Optional[Dict[str, object]]:
    """The e-invoice QR's figures, or None when there is none (or no decoder)."""
    try:
        import zxingcpp
    except ImportError:
        return None
    try:
        if content_type == "application/pdf":
            from app.services.ocr.pdfium_safe import render_pages

            pages = render_pages(file_bytes, 2.0)[:2]
        elif content_type.startswith("image/"):
            import io

            from PIL import Image

            pages = [Image.open(io.BytesIO(file_bytes))]
        else:
            return None
        for page in pages:
            for code in zxingcpp.read_barcodes(page):
                data = _token_data(code.text)
                if data:
                    return {
                        "irn": str(data.get("Irn") or "").strip(),
                        "invoice_no": str(data.get("DocNo") or "").strip(),
                        "invoice_date": str(data.get("DocDt") or "").strip(),
                        "supplier_gstin": str(data.get("SellerGstin") or "").strip().upper(),
                        "buyer_gstin": str(data.get("BuyerGstin") or "").strip().upper(),
                        "total": data.get("TotInvVal"),
                        "item_count": data.get("ItemCnt"),
                    }
    except Exception as exc:  # noqa: BLE001 - a QR is a bonus, never a failure
        log.info("einvoice_qr: could not read (%s)", exc)
    return None


def apply(fields: dict) -> list:
    """Use the QR's figures on a reading. Returns notes for the reviewer."""
    if not isinstance(fields, dict):
        return []
    # An IRN printed across two lines comes back with the break in it: Win
    # Medicare's "...600197 e9e9e0ab..." is 64 hex characters and a space.
    node = (fields.get("invoice") or {}).get("irn")
    if isinstance(node, dict) and node.get("value"):
        joined = re.sub(r"\s+", "", str(node["value"]))
        if joined != node["value"] and re.fullmatch(r"[0-9A-Fa-f]{64}", joined):
            node["value"] = joined
    qr = EINVOICE_QR.get()
    if not qr:
        return []
    notes = []

    def leaf(section: str, key: str) -> str:
        node = (fields.get(section) or {}).get(key) or {}
        return str(node.get("value") or "").strip() if isinstance(node, dict) else ""

    def put(section: str, key: str, value: str) -> None:
        fields.setdefault(section, {})[key] = {"value": value, "confidence": 1.0}

    irn = qr.get("irn")
    if irn and len(irn) == 64:
        read_irn = leaf("invoice", "irn")
        if read_irn.lower() != irn.lower():
            put("invoice", "irn", irn)
            if read_irn:
                notes.append("The IRN is taken from the e-invoice QR code, signed by the GST portal: "
                             f"the page reads {read_irn}.")
    for section, key, qkey in (("invoice", "invoice_no", "invoice_no"),
                               ("supplier", "gstin", "supplier_gstin"),
                               ("bill_to", "gstin", "buyer_gstin")):
        value = str(qr.get(qkey) or "")
        if not value or value.upper() == "URP":
            continue
        current = leaf(section, key)
        if not current:
            put(section, key, value)
        elif current.replace(" ", "").upper() != value.replace(" ", "").upper():
            notes.append(f"The e-invoice QR code gives {section.replace('_', ' ')} {key.replace('_', ' ')} "
                         f"{value}; the page reads {current}. Please check.")
    return notes
