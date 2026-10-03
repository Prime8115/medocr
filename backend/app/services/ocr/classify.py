"""Decide invoice vs prescription from a document's own text, without the AI.

With the document type on "Auto", every scan spent one Gemini call just on this
question before the call that actually reads it - half of every scan's AI quota.
A supplier invoice announces itself ("Tax Invoice", a GSTIN, HSN codes, batch
and expiry columns), so when a PDF carries text the answer is usually already
on the page.

Only a clear answer is returned. Anything ambiguous returns None and the AI
classifies it as before: guessing wrong would send a prescription through the
invoice extractor, which is worse than spending the call.
"""
import re
from typing import Optional

# Scanner text layers garble letters ("lnvoice", "Exprry"), so the patterns
# accept the usual confusions (I/l/1, O/0) where a word matters most.
_INVOICE_MARKERS = (
    r"\b(?:tax\s+)?[il1]nv[o0][il1]ce\b",
    r"\bgst[il1]n\b",
    r"\b\d{2}[a-z]{5}\d{4}[a-z][a-z0-9]z[a-z0-9]\b",  # a GSTIN itself
    r"\bhsn\b",
    r"\bbatch\b",
    r"\bexp(?:iry)?\b",
    r"\b[cs]gst\b|\bigst\b",
    r"\bm\.?r\.?p\b",
    r"\bqty\b|\bquant[il1]ty\b",
    r"\btaxable\b",
)

_PRESCRIPTION_MARKERS = (
    r"\brx\b|℞",
    r"\bpatient\b",
    r"\bm\.?b\.?b\.?s\b",
    r"\bdiagnos[ie]s\b",
    r"\bprescription\b",
    r"\bclinic\b|\bhospital\b",
    r"\b(?:age|sex)\s*[:/]",
    r"\badvice\b|\bafter\s+food\b|\bbefore\s+food\b",
    r"\bfollow[\s-]?up\b",
)

# How many distinct markers make an answer clear, and by how much one side must
# outnumber the other.
_MIN_MARKERS = 3
_MARGIN = 2


def _score(text: str, patterns) -> int:
    return sum(1 for p in patterns if re.search(p, text))


def classify_text(text: Optional[str]) -> Optional[str]:
    """'invoice' or 'prescription' when the text makes it clear, else None."""
    text = (text or "").lower()
    if len(text.strip()) < 40:
        return None
    invoice = _score(text, _INVOICE_MARKERS)
    prescription = _score(text, _PRESCRIPTION_MARKERS)
    if invoice >= _MIN_MARKERS and invoice >= prescription * _MARGIN:
        return "invoice"
    if prescription >= _MIN_MARKERS and prescription >= invoice * _MARGIN:
        return "prescription"
    return None
