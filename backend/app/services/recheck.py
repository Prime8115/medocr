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
from decimal import Decimal, InvalidOperation
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
    "supplier.dl_date_1": "the date printed with the supplier's first drug licence number",
    "supplier.dl_no_2": "the supplier's second drug licence number",
    "supplier.dl_date_2": "the date printed with the supplier's second drug licence number",
    "supplier.dl_no_3": "the supplier's third drug licence number",
    "supplier.dl_date_3": "the date printed with the supplier's third drug licence number",
    "supplier.dl_no_4": "the supplier's fourth drug licence number",
    "supplier.dl_date_4": "the date printed with the supplier's fourth drug licence number",
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
    "invoice.total_amount": "the invoice's total value (its lines, tax and round-off, before any credit note it sets off)",
    "invoice.amount_payable": "the amount the bill asks to be paid after any credit note, advance or TDS it deducts",
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


def question(fields: dict, path: str, doc_type: str = "invoice") -> Tuple[str, str, str]:
    """(prompt, current value, kind) for the field at `path`."""
    if doc_type == "prescription":
        return _prescription_question(fields, path)
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
        if section == "supplier" and key.startswith("dl_date_"):
            licence = _leaf(fields.get("supplier") or {}, key.replace("dl_date_", "dl_no_"))
            where = "Use the supplier's licence date, not a bill-to, ship-to or invoice date.\n"
            if licence:
                where += f"It belongs to drug licence {licence!r}; do not use another licence's date.\n"
    else:
        items = fields.get("line_items") or []
        i = int(index)
        if i >= len(items) or line_key not in _LINE_MEANING:
            raise RecheckError("No such line item.")
        item = items[i] or {}
        what = f"the {_LINE_MEANING[line_key]} of one line item"
        # The line is pointed out by what identifies it on the paper.
        marks = [f"product {_leaf(item, 'description')!r}" if _leaf(item, "description") and line_key != "description" else "",
                 f"batch {_leaf(item, 'batch_no')!r}" if _leaf(item, "batch_no") and line_key != "batch_no" else "",
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


def _prescription_question(fields, path):
    allowed = {"patient": ("name", "age", "gender"),
               "prescriber": ("name", "registration_no")}
    match = re.fullmatch(r"medications\[(\d+)\]\.([a-z_]+)", path or "")
    if match:
        index, key = int(match.group(1)), match.group(2)
        items = fields.get("medications") or []
        if index >= len(items) or key not in ("name", "strength", "form", "frequency", "duration", "instructions"):
            raise RecheckError("No such medication field.")
        node = items[index]
        context = ", ".join(f"{k}: {_leaf(node, k)!r}" for k in ("name", "strength", "form") if k != key and _leaf(node, k))
        what = f"{key} of medication {index + 1} of {len(items)} ({context})"
    else:
        section, _, key = (path or "").partition(".")
        if key not in allowed.get(section, ()):
            raise RecheckError("This prescription field cannot be rechecked.")
        node, what = fields.get(section) or {}, f"{section} {key}"
    current = _leaf(node, key)
    prompt = (f"Transcribe only the printed or handwritten {what} from this prescription. "
              "Do not infer dosing, expand ambiguous abbreviations, substitute medicines or correct a name "
              "using medical knowledge. If unclear or absent, return null. The other fields only help locate "
              "the line; they may be wrong. Current reading: " + json.dumps(current or None) +
              '\nReturn JSON: {"value": "literal text or null", "printed_label": "label or null", '
              '"printed_text": "literal surrounding text or null"}.')
    return prompt, current, "text"


def _squash(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(text or "").upper())


def printed_on_page(value: str, kind: str, page_text: str) -> bool:
    """Whether the value appears in the bill's text. A figure is matched with
    or without its thousands commas and trailing zeros (1,234.50 = 1234.5)."""
    if not value or not page_text:
        return False
    if kind in ("money", "number"):
        from decimal import Decimal, InvalidOperation

        try:
            want = Decimal(str(value).replace(",", ""))
        except InvalidOperation:
            return False
        if not want.is_finite():
            return False
        for figure in re.findall(r"(?<![\w.])[+-]?\d[\d,]*(?:\.\d+)?(?![\w.])", page_text):
            try:
                if Decimal(figure.replace(",", "")) == want:
                    return True
            except InvalidOperation:
                continue
        return False
    if kind == "text":
        # Reference punctuation is meaningful: AB-12 is not batch AB12.
        pattern = r"(?<![A-Z0-9])" + r"\s+".join(
            re.escape(part) for part in str(value).strip().upper().split()) + r"(?![A-Z0-9])"
        return bool(re.search(pattern, page_text.upper()))
    # Preserve token boundaries for names and dates too.
    tokens = re.findall(r"[A-Z0-9]+", str(value).upper())
    if not tokens:
        return False
    pattern = r"(?<![A-Z0-9])" + r"[^A-Z0-9]*".join(map(re.escape, tokens)) + r"(?![A-Z0-9])"
    return bool(re.search(pattern, page_text.upper()))


def _same(kind: str, a: str, b: str) -> bool:
    from app.services.ocr.ai_review import _same as same

    if not a or not b:
        return not a and not b
    if kind in ("money", "number"):
        from decimal import Decimal, InvalidOperation

        try:
            left, right = (Decimal(str(v).replace(",", "")) for v in (a, b))
            return left.is_finite() and right.is_finite() and left == right
        except InvalidOperation:
            return False
    if kind == "text":
        return str(a).strip().upper() == str(b).strip().upper()
    return same(kind, a, b)


def _money(node) -> Optional[Decimal]:
    raw = (node or {}).get("value") if isinstance(node, dict) else None
    try:
        value = Decimal(str(raw).replace(",", ""))
        return value if value.is_finite() else None
    except (InvalidOperation, TypeError):
        return None


def _verified_pdf_value(fields: dict, path: str, file_bytes: bytes,
                        current: str, kind: str, page: Optional[int] = None) -> Optional[dict]:
    """Recheck a printed total or a proved derived line value without AI.

    A line's CGST, SGST and net are often not printed separately. A model asked
    to transcribe them must answer null. Offer the calculated value only when
    the source PDF's own taxable and tax totals confirm every line, and a
    current row uniquely matches a source row by amount plus quantity/batch.
    """
    header = re.fullmatch(r"invoice\.(total_amount|total_cgst_amount|total_sgst_amount)", path)
    line = re.fullmatch(r"line_items\[(\d+)\]\.(cgst_amount|sgst_amount|net_amount)", path)
    if not (header or line):
        return None
    try:
        from app.services.ocr.pdf_utils import extract_text_pages, is_digital_pdf
        from app.services.ocr.invoice_parser import parse_invoice_pdf

        if not is_digital_pdf(file_bytes):
            return None
        source = parse_invoice_pdf(file_bytes)
        if not source:
            return None
        text = (source.get("_hints") or {}).get("document_text") or ""
        if page is not None:
            pages = extract_text_pages(file_bytes)
            if page < 1 or page > len(pages):
                return None
            text = pages[page - 1]
        invoice = source.get("invoice") or {}
        if header:
            # A figure merely appearing somewhere on a PDF does not prove its
            # label. Require the printed taxable, tax and final totals to foot.
            base, tax, total = (_money(invoice.get(key)) for key in
                                ("total_taxable_amount", "total_gst_amount", "total_amount"))
            if any(value is None for value in (base, tax, total)) or abs(base + tax - total) > Decimal("1.00"):
                return None
            for key in ("total_taxable_amount", "total_gst_amount", "total_amount"):
                proof = invoice.get(key) or {}
                if proof.get("confidence") != 1.0 or not printed_on_page(str(proof.get("value")), "money", text):
                    return None
            if header.group(1) in ("total_cgst_amount", "total_sgst_amount"):
                cgst, sgst = (_money(invoice.get(key)) for key in
                              ("total_cgst_amount", "total_sgst_amount"))
                if cgst is None or sgst is None or abs(cgst + sgst - tax) > Decimal("0.03"):
                    return None
            leaf = invoice.get(header.group(1)) or {}
            value = str(leaf.get("value") or "")
            if leaf.get("confidence") != 1.0 or not printed_on_page(value, kind, text):
                return None
            return {"path": path, "current": current or None, "found": value,
                    "printed_label": None, "printed_text": None, "page": page,
                    "on_page": True, "agrees": _same(kind, current, value),
                    "message": f"The source PDF prints {value} for this invoice total. Confirm it against the page."}

        source_items = source.get("line_items") or []
        current_items = fields.get("line_items") or []
        index, key = int(line.group(1)), line.group(2)
        if index >= len(current_items):
            return None
        current_item = current_items[index] or {}
        amount = _money(current_item.get("amount"))
        qty = _money(current_item.get("quantity"))
        batch = _leaf(current_item, "batch_no").upper()
        if amount is None or (qty is None and not batch):
            return None
        candidates = [item for item in source_items
                      if _money(item.get("amount")) == amount
                      and (qty is None or _money(item.get("quantity")) == qty)
                      and (not batch or _leaf(item, "batch_no").upper() == batch)]
        if len(candidates) != 1:
            return None
        if page is not None and (not printed_on_page(str(amount), "money", text)
                                 or (batch and not printed_on_page(batch, "text", text))):
            return None
        # A correct row on an incomplete page is not enough: all three printed
        # totals must reconcile with the full set of source rows first.
        for field, row_field in (("total_taxable_amount", "amount"),
                                 ("total_cgst_amount", "cgst_amount"),
                                 ("total_sgst_amount", "sgst_amount")):
            leaf = invoice.get(field) or {}
            expected = _money(leaf)
            values = [_money(item.get(row_field)) for item in source_items]
            if leaf.get("confidence") != 1.0 or expected is None or any(v is None for v in values) \
                    or not printed_on_page(str(leaf.get("value")), "money", text) \
                    or abs(sum(values, Decimal("0")) - expected) > Decimal("0.03"):
                return None
        if key == "net_amount":
            total = _money(invoice.get("total_amount"))
            nets = [_money(item.get("net_amount")) for item in source_items]
            if total is None or not printed_on_page(str((invoice.get("total_amount") or {}).get("value")),
                                                    "money", text) or any(v is None for v in nets) or \
                    abs(sum(nets, Decimal("0")) - total) > Decimal("1.00"):
                return None  # round-off may account for less than one rupee
        leaf = candidates[0].get(key) or {}
        value = str(leaf.get("value") or "")
        if not value:
            return None
        return {"path": path, "current": current or None, "found": value,
                "printed_label": None, "printed_text": None, "page": page,
                "on_page": None, "agrees": _same(kind, current, value),
                "message": (f"Calculated {value} from this line's printed taxable amount and GST rate. "
                            "The line value is not separately printed; the sum matches the PDF's tax totals. "
                            "Confirm it against the source before using it.")}
    except Exception:  # noqa: BLE001 - the existing AI recheck remains available
        log.exception("Could not verify a source-PDF total for recheck")
        return None


def _pages(file_bytes: bytes, content_type: str, fields: dict, path: str) -> Tuple[bytes, str]:
    """Show the whole short bill or a uniquely located line on a long bill.

    If a long scan has no usable text, or repeated products point at several
    pages, abstain instead of silently rechecking the first page.
    """
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
            key = m.group(4)
            marks = [_leaf(item, k) for k in ("batch_no", "description", "product_code") if k != key]
            texts = extract_text_pages(file_bytes)
            marks = [mark for mark in marks if len(_squash(mark)) >= 4]
            matches = [n for n, text in enumerate(texts)
                       if marks and all(printed_on_page(mark, "text", text) for mark in marks)]
            if len(matches) == 1:
                return pages[matches[0]], content_type
        raise RecheckError("Cannot safely locate this field on this long invoice. "
                           "Check the original page manually; no first-page guess was made.")
    except RecheckError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RecheckError("Could not locate the invoice page. Check the original manually.") from exc


def recheck(provider, fields: dict, page_text: Optional[str], path: str,
            file_bytes: bytes, content_type: str, doc_type: str = "invoice", page: Optional[int] = None) -> dict:
    """The AI's answer for one field, checked against the page. Raises
    RecheckError for a field that cannot be asked about; never changes `fields`."""
    prompt, current, kind = question(fields, path, doc_type)
    if doc_type == "invoice" and content_type == "application/pdf":
        verified = _verified_pdf_value(fields, path, file_bytes, current, kind, page)
        if verified is not None:
            return verified
    if page is not None:
        from app.services.source import page_total
        from app.services.ocr.pdf_utils import split_pdf

        if page < 1 or page > page_total(file_bytes):
            raise RecheckError("That page is not in this document.")
        shown = split_pdf(file_bytes, 1)[page - 1] if content_type == "application/pdf" else file_bytes
        shown_type = content_type
        page_text = None
        prompt += f"\nThe reviewer selected document page {page}. If the target is absent here, return null."
    else:
        shown, shown_type = _pages(file_bytes, content_type, fields, path)
    if shown_type == "application/pdf":
        from app.services.ocr.pdf_utils import extract_text_pages, is_scanned_pdf

        source_text = "\n".join(extract_text_pages(shown))
        if is_scanned_pdf(shown):
            page_text = None  # A scan's hidden OCR text is not independent evidence.
        elif source_text.strip():
            page_text = source_text
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
    if found and on_page is not False:
        message += " Confirm the field and row against the original; text presence alone does not verify them."
    if doc_type == "prescription":
        message = message.replace("bill", "prescription").replace("paper", "source")
    return {"path": path, "current": current or None, "found": found, "printed_label": label, "page": page,
            "printed_text": around, "on_page": on_page, "agrees": agrees, "message": message}
