"""Recheck one field: the reviewer thinks a value is wrong, and asks again.

Re-reading a whole bill to fix one value is slow and can change values that
were right. This asks the AI one narrow question about one field - "what does
this bill print for the PO number?", "what is line 3's PTR?" - shown the page
itself, and answers with the value, the words printed beside it, and whether
that value really is on the page.

It never changes the bill. The reviewer sees what was found and accepts it or
keeps their own; an accepted value is saved like any other correction, so it
is recorded and the supplier's lessons learn from it (services/corrections.py,
services/supplier_profile.py).

A value the AI gives is checked against the bill's own text where it has one
(a digital PDF): a value not printed anywhere on the page is shown as such, so
an invented answer is never offered as if it were read.
"""
import json
import logging
import re
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

# What each field is, in a pharmacist's words - the AI is asked for this.
_MEANING = {
    "supplier.name": "the supplier's (seller's) name",
    "supplier.gstin": "the supplier's GSTIN",
    "supplier.pan": "the supplier's PAN",
    "supplier.address": "the supplier's address",
    "supplier.email": "the supplier's e-mail address",
    "supplier.dl_no_1": "the supplier's first drug licence number",
    "supplier.dl_no_2": "the supplier's second drug licence number",
    "supplier.dl_no_3": "the supplier's third drug licence number",
    "bill_to.name": "the buyer's (bill-to party's) name",
    "bill_to.gstin": "the buyer's (bill-to) GSTIN",
    "bill_to.pan": "the buyer's (bill-to) PAN",
    "bill_to.address": "the buyer's (bill-to) address",
    "ship_to.name": "the ship-to (delivery) party's name",
    "ship_to.gstin": "the ship-to party's GSTIN",
    "ship_to.address": "the ship-to (delivery) address",
    "invoice.document_title": "the document's title as printed at its head (e.g. TAX INVOICE)",
    "invoice.invoice_no": "the invoice number",
    "invoice.invoice_date": "the invoice date",
    "invoice.total_amount": "the bill's grand total (the amount payable)",
    "invoice.due_date": "the payment due date",
    "invoice.irn": "the e-invoice IRN",
    "invoice.eway_bill_no": "the e-way bill number",
    "invoice.lr_no": "the LR / GR (lorry receipt) number",
    "invoice.lr_date": "the LR / GR date",
    "invoice.transport": "the transporter's or carrier's name",
    "invoice.po_no": "the buyer's purchase order (PO) number",
    "invoice.po_date": "the purchase order (PO) date",
    "invoice.total_taxable_amount": "the bill's total taxable value",
    "invoice.total_discount_amount": "the bill's total discount",
    "invoice.total_gst_amount": "the bill's total GST",
    "invoice.total_cgst_amount": "the bill's total CGST",
    "invoice.total_sgst_amount": "the bill's total SGST",
    "invoice.total_igst_amount": "the bill's total IGST",
    "invoice.total_utgst_amount": "the bill's total UTGST",
}
_LINE_MEANING = {
    "description": "product name",
    "product_code": "product / item code",
    "manufacturer": "manufacturer",
    "batch_no": "batch number",
    "expiry": "expiry date",
    "mfg_date": "manufacturing date",
    "pack": "pack size",
    "uom": "unit of measure",
    "quantity": "billed quantity (not counting free goods)",
    "free_quantity": "free quantity (scheme / bonus goods)",
    "total_quantity": "total quantity",
    "mrp": "MRP",
    "ptr": "PTR (price to retailer)",
    "pts": "PTS (price to stockist)",
    "rate": "rate charged per unit",
    "discount_percent": "discount %",
    "discount_amount": "discount amount",
    "scheme_percent": "scheme %",
    "amount": "taxable value / amount",
    "hsn": "HSN code",
    "gst_percent": "GST %",
    "cgst_percent": "CGST %",
    "cgst_amount": "CGST amount",
    "sgst_percent": "SGST %",
    "sgst_amount": "SGST amount",
    "igst_percent": "IGST %",
    "igst_amount": "IGST amount",
    "gross_amount": "gross amount",
    "net_amount": "net amount (with tax)",
    "scheme": "scheme",
    "scheme_value": "scheme value",
}

_PATH = re.compile(r"^(?:(supplier|bill_to|ship_to|invoice)\.([a-z0-9_]+)|line_items\[(\d+)\]\.([a-z0-9_]+))$")

PROMPT = """You are checking one value on a pharmacy purchase invoice for a reviewer
who thinks it was read wrong. Look at the invoice and find {what}.
{where}{wordings}
It was read as: {current}

Answer with JSON only:
{{"value": "<the value exactly as printed, or null if the invoice does not print it>",
  "printed_label": "<the heading or label printed with it, as printed, or null>",
  "printed_text": "<a few words of the invoice around the value, exactly as printed>"}}

Copy the value character for character from the invoice. Never work a value
out, add figures up or guess: if it is not printed, answer null.
"""


class RecheckError(ValueError):
    """The field cannot be rechecked (unknown path, no such line)."""


def _leaf(node: dict, key: str) -> str:
    leaf = (node or {}).get(key)
    return str((leaf or {}).get("value") or "").strip() if isinstance(leaf, dict) else ""


def _kind(key: str) -> str:
    if "date" in key or key == "expiry":
        return "date"
    if key in ("quantity", "free_quantity", "total_quantity") or key.endswith("_percent"):
        return "number"
    if key.endswith("amount") or key in ("mrp", "ptr", "pts", "rate", "scheme_value"):
        return "money"
    if key in ("name", "description", "manufacturer", "transport", "address"):
        return "name"
    return "text"


def _wordings(section: Optional[str], key: str) -> List[str]:
    """How suppliers print this field (field_dictionary.py), to help find it."""
    from app.services.ocr import field_dictionary as fd

    if section is None:
        return []   # column wordings are kept squashed ("billedqty"): not helpful to show
    for table in (fd.REFERENCE_LABELS, fd.DATE_LABELS, fd.TOTAL_LABELS):
        if key in table:
            return list(table[key])[:6]
    return []


def question(fields: dict, path: str) -> Tuple[str, str, str]:
    """(prompt, current value, kind) for the field at `path`."""
    m = _PATH.match(path or "")
    if not m:
        raise RecheckError("This field cannot be rechecked.")
    section, key, index, line_key = m.groups()
    if section:
        what = _MEANING.get(path)
        if not what:
            raise RecheckError("This field cannot be rechecked.")
        current = _leaf(fields.get(section) or {}, key)
        where, words, kind = "", _wordings(section, key), _kind(key)
    else:
        items = fields.get("line_items") or []
        i = int(index)
        if i >= len(items) or line_key not in _LINE_MEANING:
            raise RecheckError("No such line item.")
        item = items[i] or {}
        what = f"the {_LINE_MEANING[line_key]} of one line item"
        # The line is pointed out by what identifies it on the paper.
        marks = [f"product {_leaf(item, 'description')!r}" if _leaf(item, "description") else "",
                 f"batch {_leaf(item, 'batch_no')!r}" if _leaf(item, "batch_no") else "",
                 f"HSN {_leaf(item, 'hsn')!r}" if _leaf(item, "hsn") and line_key != "hsn" else ""]
        marks = [x for x in marks if x and not x.startswith(f"{line_key} ")]
        where = (f"The line is item {i + 1} of {len(items)}"
                 + (": " + ", ".join(marks) if marks else "") + ".\n")
        current = _leaf(item, line_key)
        words, kind = [], _kind(line_key)
    wordings = ("Suppliers print it under headings such as: " + ", ".join(words) + ".\n") if words else ""
    prompt = PROMPT.format(what=what, where=where, wordings=wordings,
                           current=json.dumps(current or None, ensure_ascii=False))
    return prompt, current, kind


def _squash(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(text or "").upper())


def printed_on_page(value: str, kind: str, page_text: str) -> bool:
    """Whether the value appears in the bill's text. A figure is matched with
    or without its thousands commas and trailing zeros (1,234.50 = 1234.5)."""
    if not value or not page_text:
        return False
    if _squash(value) and _squash(value) in _squash(page_text):
        return True
    if kind in ("money", "number"):
        try:
            want = float(str(value).replace(",", ""))
        except ValueError:
            return False
        for figure in re.findall(r"\d[\d,]*(?:\.\d+)?", page_text):
            try:
                if abs(float(figure.replace(",", "")) - want) < 0.005:
                    return True
            except ValueError:
                continue
    return False


def _same(kind: str, a: str, b: str) -> bool:
    from app.services.ocr.ai_review import _same as same

    if not a or not b:
        return not a and not b
    return _squash(a) == _squash(b) or same(kind, a, b)


def _pages(file_bytes: bytes, content_type: str, fields: dict, path: str) -> Tuple[bytes, str]:
    """What the AI is shown: the whole bill when short; on a long one, the page
    the line is printed on (found by its batch or name in the bill's text), or
    the first and last pages for a header value."""
    if content_type != "application/pdf":
        return file_bytes, content_type
    from app.config import settings
    from app.services.ocr.pdf_utils import extract_text_pages, page_count, split_pdf

    limit = max(1, settings.ocr_ai_review_max_pages)
    try:
        if page_count(file_bytes) <= limit:
            return file_bytes, content_type
        pages = split_pdf(file_bytes, 1)
        m = _PATH.match(path)
        if m and m.group(3) is not None:
            item = (fields.get("line_items") or [])[int(m.group(3))] or {}
            marks = [_squash(_leaf(item, "batch_no")), _squash(_leaf(item, "description"))]
            texts = extract_text_pages(file_bytes)
            for mark in [x for x in marks if len(x) >= 4]:
                for n, text in enumerate(texts):
                    if mark in _squash(text):
                        return pages[n], content_type
        return pages[0], content_type
    except Exception:  # noqa: BLE001 - the whole bill is still a fair question
        return file_bytes, content_type


def recheck(provider, fields: dict, page_text: Optional[str], path: str,
            file_bytes: bytes, content_type: str) -> dict:
    """The AI's answer for one field, checked against the page. Raises
    RecheckError for a field that cannot be asked about; never changes `fields`."""
    prompt, current, kind = question(fields, path)
    shown, shown_type = _pages(file_bytes, content_type, fields, path)
    answer = provider.review_json(prompt, shown, shown_type)
    if not isinstance(answer, dict):
        return {"path": path, "current": current or None, "found": None, "printed_label": None,
                "printed_text": None, "on_page": None, "agrees": None,
                "message": "The AI could not answer this time. Please try again."}
    found = answer.get("value")
    found = str(found).strip() if found not in (None, "", "null") else None
    label = str(answer.get("printed_label") or "").strip() or None
    around = str(answer.get("printed_text") or "").strip() or None
    on_page = printed_on_page(found, kind, page_text) if (found and page_text) else None
    agrees = _same(kind, current, found or "")
    if found is None:
        message = "The AI could not find this field printed on the bill."
    elif on_page is False:
        message = f"The AI suggests {found!r}, but that value is not printed on the bill - check the paper."
    elif agrees:
        message = f"The AI reads the same: {found!r}" + (f" (printed as '{label}')." if label else ".")
    else:
        message = f"The AI reads {found!r}" + (f", printed as '{label}'" if label else "") + "."
    return {"path": path, "current": current or None, "found": found, "printed_label": label,
            "printed_text": around, "on_page": on_page, "agrees": agrees, "message": message}
