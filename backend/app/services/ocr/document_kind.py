"""What the document says it is: a tax invoice, or something that looks like one.

A supplier's credit note, debit note, delivery challan, proforma or quotation
has the same columns as a purchase invoice - supplier, GSTIN, batch, expiry,
amounts. Read as an invoice, a credit note would add stock and a payable the
pharmacy does not owe. So the kind is taken from the document's own title and,
when it is not a tax invoice, the review must acknowledge it before approval.

The title is looked for as a HEADING - a short line near the top - never as
words anywhere on the page: "No credit note will be issued for expired goods"
in a footer does not make an invoice a credit note.
"""
import re
from typing import Optional

INVOICE = "invoice"

# (kind, pattern, how the reviewer is told). Order matters: "proforma invoice"
# must be caught before "invoice" would be.
_KINDS = (
    ("credit_note", r"credit\s*note|cr\.?\s*note", "a credit note"),
    ("debit_note", r"debit\s*note|dr\.?\s*note", "a debit note"),
    ("return", r"(?:sales|purchase|goods)\s*return|return\s*note", "a goods-return note"),
    ("delivery_challan", r"(?:delivery\s*)?chall?an", "a delivery challan"),
    ("proforma", r"pro\s*-?\s*forma(?:\s*invoice)?", "a proforma invoice"),
    ("quotation", r"quotation|estimate", "a quotation or estimate"),
    ("purchase_order", r"purchase\s*order", "a purchase order"),
)
_DESCRIBE = {kind: words for kind, _p, words in _KINDS}

# Copy markings printed beside the title, ignored when judging a heading.
_COPY_WORDS = re.compile(
    r"\(?\s*(?:original|duplicate|triplicate|quadruplicate|extra)(?:\s*(?:copy|for\s+\w+))?\s*\)?"
    r"|\bfor\s+(?:recipient|buyer|transporter|supplier)\b|\bcopy\b",
    re.I,
)
_HEADING_MAX_CHARS = 40   # a title line, not a sentence
_HEADING_LINES = 25       # near the top of the page


def _squash(text: str) -> str:
    text = _COPY_WORDS.sub(" ", text or "")
    text = re.sub(r"[^A-Za-z.\s-]", " ", text)
    return re.sub(r"\s+", " ", text).strip(" .-")


def _is_combined_invoice(text: str) -> bool:
    """'Tax Invoice cum Delivery Challan' is an invoice that doubles as a challan."""
    return bool(re.search(r"\bcum\b", text, re.I) and re.search(r"invoice|bill\s*of\s*supply", text, re.I))


def kind_of_title(title: Optional[str]) -> Optional[str]:
    """The kind a printed title names, 'invoice' for an invoice title, or None."""
    text = _squash(title)
    if not text or len(text) > _HEADING_MAX_CHARS:
        return None
    if _is_combined_invoice(text):
        return INVOICE
    for kind, pattern, _words in _KINDS:
        # ...and what it is for: "Credit Note for Non-Saleable" (Ajanta).
        if re.fullmatch(rf"(?:gst\s*|tax\s*)?(?:{pattern})(?:\s+(?:for|against)\s+[a-z\s-]{{1,25}})?",
                        text, re.I):
            return kind
    if re.fullmatch(r"(?:gst\s*)?(?:tax\s*|retail\s*|sales\s*)?(?:invoice|bill)|bill\s*of\s*supply",
                    text, re.I):
        return INVOICE
    return None


def kind_from_page(text: Optional[str]) -> Optional[str]:
    """The kind named by a heading near the top of the page's text.

    A heading is either a short line of its own, or the phrase in CAPITALS on a
    line it shares with the supplier's name - the way a title sits in a wide
    layout. A field label ("Challan No.", "Credit Note Date") is not a title.
    """
    found = None
    for line in (text or "").splitlines()[:_HEADING_LINES]:
        for part in re.split(r"\s{3,}|\t|\|", line):
            kind = kind_of_title(part)
            if kind and kind != INVOICE:
                return kind
            found = found or kind
        if _is_combined_invoice(line):
            found = found or INVOICE
            continue
        for kind, pattern, _words in _KINDS:
            m = re.search(rf"\b(?:{pattern})\b(?!\s*(?:no\b|number|date|ref|#|:|\.\s*no))", line, re.I)
            if m and m.group().isupper():
                return kind
        m = re.search(r"\b(?:tax\s*invoice|gst\s*invoice|bill\s*of\s*supply)\b"
                      r"(?!\s*(?:no\b|number|date|ref|#|:|\.\s*no))", line, re.I)
        # An invoice title is taken in any case: Abbott's first line runs
        # "Corporate Identity No:... Tax Invoice IPD(IP)/...". Mistaking a
        # mention for the title costs nothing here - a credit note's own
        # heading still wins, and "Tax Invoice No." is a label, not a title.
        if m:
            found = found or INVOICE
    return found


def detect(title: Optional[str], page_text: Optional[str]) -> Optional[str]:
    """The document's kind, from the AI's reading of its title and the page's
    own text. Anything that is not an invoice wins: a credit note one reader
    noticed is still a credit note."""
    kinds = [k for k in (kind_of_title(title), kind_from_page(page_text)) if k]
    others = [k for k in kinds if k != INVOICE]
    if others:
        return others[0]
    return INVOICE if kinds else None


def check(kind: Optional[str]) -> Optional[dict]:
    """The verification check for the kind; None when nothing was learned."""
    label = "The document is a tax invoice"
    if not kind:
        return None
    if kind == INVOICE:
        return {"id": "document_kind", "label": label, "status": "pass", "message": "", "fields": []}
    return {"id": "document_kind", "label": label, "status": "fail",
            "message": f"This document is {_DESCRIBE.get(kind, 'not a tax invoice')}, not a purchase "
                       "invoice. Check it before posting it as a purchase.",
            "fields": ["invoice.document_title"]}
