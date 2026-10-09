"""Fields the bill prints that we did not read.

Every new supplier format has broken the same way: a field the bill plainly
prints - "L.R. No. : LOCAL Date : 22 Sep 25", "IRN No.: DDE3F8...", "Valid till -
27-Apr-2028" - came back blank, and nobody knew until a client complained.
This looks for each field's LABEL printed with a real VALUE after it, on the
same line, and reports any whose field we left empty.

It is deliberately strict about what counts as printed: the value must look
like that field's value (a date for a date, a hex string for an IRN), and a
label followed by nothing but another label - V L's "L.R. NO. : DATE :", a
field the bill leaves blank - is not a miss. A false alarm here would train
reviewers to ignore it.

What it finds is (1) a failed verification check naming the field and what
the bill prints there, (2) the list the AI gap-fill is asked to complete, and
(3) for a reviewer who fills it in, a label the supplier profile can learn.
"""
import re
from typing import Dict, List, NamedTuple, Optional

from app.services.ocr.invoice_header import _DATE, _LABEL_WORDS, _is_label_not_value

_SAME_LINE = r"[ \t]*"
_SEP = _SAME_LINE + r"[:\-#]?" + _SAME_LINE

_VALUE = {
    # Ends on a word boundary, so "DATE :" cannot be trimmed to "DAT" to slip
    # past the rule that a value is never followed by a colon (a label is).
    "token": r"([A-Za-z0-9][A-Za-z0-9\-/]{1,40})\b(?![ \t]*:)",
    "date": _DATE,
    "hex": r"([A-Fa-f0-9]{32,64})\b",
    "eway": r"(\d{10,16})\b",
    "email": r"([\w.+\-]+@[\w\-]+\.[\w.\-]+)",
}


class Printed(NamedTuple):
    path: str            # where it belongs, e.g. "invoice.lr_no"
    label: str           # how the reviewer knows it
    patterns: tuple      # label regexes; the value follows each
    kind: str            # which value shape to expect


_PRINTED: List[Printed] = [
    Printed("invoice.invoice_no", "Invoice number",
            # Never the e-way bill's: Zuventus prints "eWayBillNo.262023995925".
            (r"\binvoice[ \t]*no\.?", r"(?<!way)(?<!way )(?<!way-)\bbill[ \t]*no\.?",
             r"\binv\.?[ \t]*no\.?"), "token"),
    Printed("invoice.invoice_date", "Invoice date",
            (r"\binvoice[ \t]*date", r"\binv\.?[ \t]*date", r"\bbill[ \t]*date",
             r"\binvoice[ \t]*no\.?" + _SEP + r"[A-Za-z0-9\-/]{2,40}" + _SAME_LINE + r"(?:date|dt)\.?"),
            "date"),
    Printed("invoice.due_date", "Due date", (r"\bdue[ \t]*date", r"\bpayment[ \t]*due(?:[ \t]*date)?"),
            "date"),
    Printed("invoice.lr_no", "LR number",
            (r"\bl\.?[ \t]*r\.?[ \t]*(?:/[ \t]*r\.?[ \t]*r\.?)?[ \t]*no\.?", r"\bgr[ \t]*/[ \t]*lr[ \t]*no\.?",
             r"\blorry[ \t]*receipt[ \t]*no\.?"), "token"),
    Printed("invoice.lr_date", "LR date",
            (r"\bl\.?[ \t]*r\.?[ \t]*(?:/[ \t]*r\.?[ \t]*r\.?)?[ \t]*date", r"\bgr[ \t]*/[ \t]*lr[ \t]*date",
             r"\bl\.?[ \t]*r\.?[ \t]*no\.?" + _SEP + r"[A-Za-z0-9\-/]{2,40}" + _SAME_LINE + r"(?:date|dt)\.?"),
            "date"),
    Printed("invoice.po_no", "PO / order number",
            (r"\bp\.?[ \t]*o\.?[ \t]*(?:no|number)\.?", r"\bpurchase[ \t]*order[ \t]*(?:no|number)\.?",
             r"(?<![.\w])order[ \t]*no\.?", r"\bord\.?[ \t]*ref\.?[ \t]*no\.?"), "token"),
    Printed("invoice.po_date", "PO / order date",
            (r"\bp\.?[ \t]*o\.?[ \t]*date", r"(?<![.\w])order[ \t]*date", r"\bord\.?[ \t]*ref\.?[ \t]*date",
             r"(?<![.\w])order[ \t]*no\.?" + _SEP + r"[A-Za-z0-9\-/]{2,40}" + _SAME_LINE + r"(?:date|dt)\.?"),
            "date"),
    Printed("invoice.irn", "IRN", (r"\birn(?:[ \t]*no\.?)?",), "hex"),
    Printed("invoice.eway_bill_no", "E-way bill number",
            (r"\be[ \t-]*way[ \t]*bill(?:[ \t]*no\.?)?",), "eway"),
    Printed("supplier.email", "Supplier e-mail", (r"\be-?[ \t]*mail(?:[ \t]*id)?",), "email"),
]


def _value(leaf) -> str:
    return str((leaf or {}).get("value") or "").strip() if isinstance(leaf, dict) else ""


def _get(fields: dict, path: str) -> str:
    section, _, key = path.partition(".")
    return _value((fields.get(section) or {}).get(key))


# Numbers the bill identifies itself or the order by always carry a digit:
# Sun prints the shop's name, "Buyer PO No. :shree simba chemist".
_NEEDS_A_DIGIT = ("invoice.invoice_no", "invoice.po_no")


def _not_a_value(spec: Printed, value: str, text: str, after: str) -> bool:
    """A word that is the next label, or a blank's stand-in - never a miss.

    "LR No : LR Date :" (Corona, Wockhardt) and "Invoice No : Invoice Date :"
    (Health N U) leave the field blank: the word read is where the NEXT label
    starts. "GR/LR No. : na" and "LR NO:hd" are the bill saying there is none,
    and the header reader rejects them the same way.
    """
    if _is_label_not_value(value, text):
        return True
    if value.isalpha() and re.match(r"[ \t]*(?:date|dt|no)\b", after, re.I):
        return True
    return spec.path in _NEEDS_A_DIGIT and not any(ch.isdigit() for ch in value)


def printed_value(text: str, spec: Printed) -> Optional[tuple]:
    """(label as printed, value) for the first real value after a label, or None."""
    for pattern in spec.patterns:
        for m in re.finditer(pattern + _SEP + _VALUE[spec.kind], text or "", re.I):
            value = m.group(m.lastindex).strip()
            if value.lower() in _LABEL_WORDS:
                continue
            if spec.kind == "token" and _not_a_value(spec, value, text, text[m.end(m.lastindex):]):
                continue
            label = re.sub(r"\s+", " ", text[m.start():m.start(m.lastindex)]).strip(" :-#")
            return label, value
    return None


def _buyers_address(fields: dict, email: str) -> bool:
    """An e-mail whose domain is the buyer's own name: Medley prints the
    pharmacy's "purchase@easternagencies.co.in" under its address block, and
    it was reported as the supplier's e-mail we failed to read."""
    domain = re.sub(r"[^a-z0-9]", "", email.lower().partition("@")[2].split(".")[0])
    if len(domain) < 5:
        return False
    names = [str(_get(fields, f"{p}.name") or "") for p in ("bill_to", "ship_to")]
    return any(domain in re.sub(r"[^a-z0-9]", "", n.lower()) for n in names)


def find_missed(fields: dict, page_text: str) -> List[Dict[str, str]]:
    """Every field the bill prints a value for that we left empty.

    Supplier e-mail is judged on the supplier's own fields only being blank -
    a buyer's e-mail elsewhere on the page is not the supplier's.
    """
    if not page_text:
        return []
    missed = []
    for spec in _PRINTED:
        if _get(fields, spec.path):
            continue
        found = printed_value(page_text, spec)
        if found and spec.path == "supplier.email" and _buyers_address(fields, found[1]):
            continue
        if found:
            missed.append({"path": spec.path, "label": spec.label,
                           "printed_label": found[0], "printed_value": found[1]})
    return missed


def missed_check(fields: dict, page_text: str) -> dict:
    """The verification check: pass, fail naming each miss, or skipped without text."""
    if not page_text:
        return {"id": "printed_not_read", "label": "Every field the bill prints was read",
                "status": "skipped", "message": "", "fields": []}
    missed = find_missed(fields, page_text)
    if not missed:
        return {"id": "printed_not_read", "label": "Every field the bill prints was read",
                "status": "pass", "message": "", "fields": []}
    detail = "; ".join(f"{m['label']}: the bill prints {m['printed_value']!r} after "
                       f"{m['printed_label']!r}" for m in missed[:5])
    return {"id": "printed_not_read", "label": "Every field the bill prints was read",
            "status": "fail", "message": detail + ".", "fields": [m["path"] for m in missed]}
