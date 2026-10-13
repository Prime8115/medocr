"""The buyer's address, read beneath its name by position.

A bill with a "Bill To" heading has its buyer's block cut out by column
(invoice_header.party_regions). Many print none: the buyer is found by its
GSTIN (parties.py), and its address - the lines printed under its name - was
left blank on 42 of the client's 125 bills. This reads those lines from the
first page's word positions: below the name, inside the name's own column
(stopping where the next column starts), until a GSTIN, PAN, licence or phone
line. A result that does not look like an address is not used.
"""
import re
from typing import List, Optional, Sequence, Tuple

from app.services.ocr.invoice_header import _ADDRESS_LIKE, _STOP

# (x0, x1, top, bottom, text) per word, from pdfplumber.
Word = Tuple[float, float, float, float, str]

_LINE_GAP = 3.0       # words this close vertically are one line
_COLUMN_GAP = 14.0    # a horizontal gap this wide starts another column
_BELOW = 60.0         # how far under the name the address may run
_WIDEST = 190.0       # an address block is about this wide at most

# A line from a bank, IRN or account block ends the address outright.
_NOT_ADDRESS = re.compile(r"\b(?:Bank|IRN|Cheque|A/?c\b|Account|IFSC)|\d{11,}", re.I)
_GSTIN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d]Z[A-Z\d]\b")


def _key(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(text or "").upper())


def _lines(words: Sequence[Word]) -> List[List[Word]]:
    out: List[List[Word]] = []
    for w in sorted(words, key=lambda w: (w[2], w[0])):
        if out and abs(out[-1][0][2] - w[2]) <= _LINE_GAP:
            out[-1].append(w)
        else:
            out.append([w])
    return [sorted(line, key=lambda w: w[0]) for line in out]


def address_below(words: Sequence[Word], name: Optional[str], page_width: float = 10_000.0,
                  supplier: Optional[str] = None) -> Optional[str]:
    tokens = [_key(t) for t in str(name or "").split() if _key(t)]
    seller = "".join(_key(t) for t in str(supplier or "").split()[:2])
    if len(tokens) < 2 or not words:
        return None
    lines = _lines(words)
    for li, line in enumerate(lines):
        for wi in range(len(line) - 1):
            if _key(line[wi][4]) != tokens[0] or _key(line[wi + 1][4]) != tokens[1]:
                continue
            # The name's extent on its line, then where the next column starts.
            end = wi + 1
            while end + 1 < len(line) and end + 1 - wi < len(tokens) and _key(line[end + 1][4]) == tokens[end + 1 - wi]:
                end += 1
            left = line[wi][0] - 2
            right = page_width
            for nxt in line[end + 1:]:
                if nxt[0] - line[end][1] > _COLUMN_GAP:
                    right = nxt[0] - 2
                    break
            right = min(right, left + max(1.5 * (line[end][1] - line[wi][0]), _WIDEST))
            bottom = max(w[3] for w in line[wi:end + 1])
            parts: List[str] = []
            for below in lines[li + 1:]:
                if below[0][2] > bottom + _BELOW or len(parts) == 3:
                    break
                inside = [w for w in below if w[0] >= left and w[1] <= right + 5]
                # A wide gap inside the column starts the block beside it.
                for k in range(1, len(inside)):
                    if inside[k][0] - inside[k - 1][1] > _COLUMN_GAP:
                        inside = inside[:k]
                        break
                text = " ".join(w[4] for w in inside).strip()
                text = re.split(r"\bCont\.?\s*No\b", text, flags=re.I)[0].strip()
                if not text:
                    continue
                if _NOT_ADDRESS.search(text) or (len(seller) > 5 and seller in _key(text)):
                    break
                # A phone number or a bare "GST :" also ends the address.
                cut = _GSTIN.split(_STOP.split(text)[0])[0]
                cut = re.split(r"(?<!\d)(?:\+?91[\s-]?)?\d{10}(?!\d)|\bGST\b|\bPh\b|\bM\s*:", cut, flags=re.I)[0]
                cut = cut.strip(" ,:-")
                if cut:
                    parts.append(cut)
                if cut != text:   # a GSTIN / PAN / licence line: the address has ended
                    break
            address = re.sub(r"\s+", " ", ", ".join(parts)).strip(" ,")
            # Letters printed spaced apart ("G R O U N D") are not read reliably.
            singles = [t for t in address.split() if len(t.strip(".,")) == 1]
            if len(singles) > len(address.split()) / 4:
                return None
            if address and _ADDRESS_LIKE.search(address) and not any(_key(t) in _key(address) for t in tokens[:2]):
                return address
            return None
    return None


def page_words(page) -> List[Word]:
    """The first page's words, as address_below takes them."""
    from app.services.ocr.pdf_table import WORD_TOLERANCE

    try:
        return [(float(w["x0"]), float(w["x1"]), float(w["top"]), float(w["bottom"]), str(w["text"]))
                for w in page.extract_words(x_tolerance=WORD_TOLERANCE)]
    except Exception:  # noqa: BLE001
        return []


# "Order No.: 1017143491", "Delivery Date:14.08.2025" - a reference printed in
# the column beside an address and swept into it with the address's lines.
_REFERENCE_IN_ADDRESS = re.compile(
    r"\b(?:Customer\s+)?(?:Order|Delivery|Invoice|Inv|Payment\s+Due|Due|P\.?O|Challan|L\.?R|Dispatch|"
    r"Despatch|Ref(?:erence)?|Bill)\s*\.?\s*(?:No|Number|Date|Dt)\s*\.?\s*[:#-]\s*[A-Z0-9][\w./-]*",
    re.I,
)


def without_references(address: Optional[str]) -> Optional[str]:
    """The address with any labelled reference figure taken out."""
    if not address:
        return address
    tidy = _REFERENCE_IN_ADDRESS.sub(",", address)
    if tidy == address:
        return address
    tidy = re.sub(r"\s*,[\s,]*", ", ", tidy)
    tidy = re.sub(r"\s{2,}", " ", tidy).strip(" ,")
    return tidy or None
