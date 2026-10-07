"""The supplier's drug licence numbers, read from where the bill prints them.

A pharma invoice prints several sets of drug licences: the buyer's under
"Billed To", the consignee's under "Shipped To", the supplier's beside its own
GSTIN, and sometimes a godown's in the footer. The AI took the wrong set: on
V. N. Pharma's bill it read the dispatch godown's "F20B TZ5-24902" from the
footer, and lost the dates printed with the supplier's own "20B 254080
DT.01.01.18".

On a digital PDF where each word sits is known exactly, so the supplier's set
is taken from the "D.L. No." line in the supplier's own block: the line just
below its GSTIN, starting in the same column. Each licence keeps the date
printed with it. When no such line is found, the reading is left as it was.
"""
import io
import logging
import re
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

_LABEL = re.compile(r"^(?:D\.?\s*L\.?|DRUG\s*LIC)", re.I)
_LABEL_REST = re.compile(r"^(?:D\.?\s*L\.?|DRUG\s*LIC\w*)\s*(?:NO\.?|NOS\.?)?\s*[:.]?\s*", re.I)
_SEPARATOR = re.compile(r"\s*(?:,|&|;|\band\b)\s*", re.I)
_WITH_DATE = re.compile(
    r"^(?P<no>.+?)\s*(?:DT|DATED|DATE|VALID\s*(?:UP\s*TO|UPTO|TILL))\s*[.:]?\s*"
    r"(?P<date>\d{1,2}[./-]\d{1,2}[./-]\d{2,4})\s*$", re.I)

_LINE = 3.5          # points: words this close vertically share a line
_BELOW = 40.0        # points: how far under the GSTIN the licence line may sit
_SAME_COLUMN = 20.0  # points: how far its label may start from the GSTIN label
_CELL_GAP = 40.0     # points: a gap this wide ends the supplier's cell


def parse_licences(text: str) -> List[Tuple[str, Optional[str]]]:
    """'D.L. No. :20B 254080 DT.01.01.18 , 21B 254081 DT.01.01.18' ->
    [('20B 254080', '01.01.18'), ('21B 254081', '01.01.18')]."""
    text = _LABEL_REST.sub("", (text or "").strip()).strip(" :.")
    out: List[Tuple[str, Optional[str]]] = []
    for part in _SEPARATOR.split(text):
        part = part.strip(" :.-")
        if not part:
            continue
        m = _WITH_DATE.match(part)
        number, date = (m.group("no").strip(" :.-"), m.group("date")) if m else (part, None)
        if re.search(r"\d", number) and 4 <= len(number) <= 30:
            out.append((number, date))
    return out


def _lines(words: list) -> List[list]:
    lines: List[list] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(w["top"] - lines[-1][0]["top"]) <= _LINE:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(line, key=lambda w: w["x0"]) for line in lines]


def licences_beside(data: bytes, gstin: str, max_pages: int = 3) -> List[Tuple[str, Optional[str]]]:
    """The licences printed on the line below `gstin`, in its column; [] if none."""
    import pdfplumber

    gstin = (gstin or "").upper()
    if len(gstin) != 15:
        return []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages[:max_pages]:
            lines = _lines(page.extract_words(x_tolerance=1.5))
            for i, line in enumerate(lines):
                hit = next((w for w in line if gstin in re.sub(r"[^A-Z0-9]", "", w["text"].upper())), None)
                if hit is None:
                    continue
                # Where the GSTIN's own label starts - the column of the block.
                label = next((w for w in line if w["x0"] <= hit["x0"] and "GSTIN" in w["text"].upper()), hit)
                for below in lines[i:i + 4]:
                    if below[0]["top"] - hit["top"] > _BELOW:
                        break
                    start = next((j for j, w in enumerate(below)
                                  if _LABEL.match(w["text"]) and abs(w["x0"] - label["x0"]) <= _SAME_COLUMN), None)
                    if start is None:
                        continue
                    cell = [below[start]]
                    for w in below[start + 1:]:
                        if w["x0"] - cell[-1]["x1"] > _CELL_GAP:
                            break          # the next block on the same line
                        cell.append(w)
                    found = parse_licences(" ".join(w["text"] for w in cell))
                    if found:
                        return found
    return []


def apply_supplier_licences(fields: dict, data: bytes) -> List[str]:
    """Put the supplier's own licences, with their dates, into dl_no_n /
    dl_date_n when the PDF prints them beside its GSTIN. Returns warnings."""
    supplier = fields.get("supplier")
    if not isinstance(supplier, dict):
        return []
    gstin = str((supplier.get("gstin") or {}).get("value") or "").strip()
    found = licences_beside(data, gstin)[:3]
    if not found:
        return []
    read = [str((supplier.get(f"dl_no_{n}") or {}).get("value") or "").strip() for n in (1, 2, 3)]
    for n in (1, 2, 3):
        number, date = found[n - 1] if n <= len(found) else (None, None)
        supplier[f"dl_no_{n}"] = {"value": number, "confidence": 1.0 if number else None}
        supplier[f"dl_date_{n}"] = {"value": date, "confidence": 1.0 if date else None}
    squash = lambda s: re.sub(r"[^A-Z0-9]", "", s.upper())  # noqa: E731
    if [squash(r) for r in read if r] != [squash(n) for n, _d in found]:
        was = ", ".join(r for r in read if r) or "nothing"
        return [f"The supplier's drug licences were taken from beside its GSTIN: "
                f"{', '.join(n for n, _d in found)} (the reading had {was}) - please confirm them."]
    return []
