"""Header fields of a pharma invoice: the references, totals and parties.

Everything above the line-item table. The client's import needs 33 of these, and
a pharmacy has to keep most of them by law - drug licence numbers, the e-way bill,
the IRN of the e-invoice.

Two things make this harder than a plain regex sweep:

* **Three parties share the header.** The supplier prints its own name, GSTIN,
  PAN and drug licences; the Bill-to and Ship-to print theirs. Flattened to text
  those columns interleave, so a pattern matching "GSTIN" anywhere returns
  whichever party happens to come first. Each party is read from its own column,
  found by x position.
* **Labels vary per supplier.** "Invoice Date", "Dt.", "Bill Date"; "E Way Bill",
  "EwayBill No"; "LR No./Dt". Each field therefore carries several spellings.

Anything not printed stays None. Invoice-level tax totals are the exception: when
the invoice does not print them they are summed from the lines, because the
figures have to reach the shop's accounts either way.
"""
import re
from typing import Dict, List, Optional, Tuple

# A field is (list of label spellings, value pattern). Labels are matched
# case-insensitively with flexible punctuation and spacing.
_DATE = r"([0-3]?\d[./\-][0-1]?\d[./\-]\d{2,4}|\d{4}-\d{2}-\d{2})"
_MONEY = r"([\d,]+\.\d{2}|[\d,]{2,})"
_TOKEN = r"([A-Za-z0-9][A-Za-z0-9\-\/]*)"

# A GSTIN is exactly 15 characters: 2-digit state code, the 10-character PAN,
# a 1-digit entity number, a literal Z, and a checksum. An earlier version of
# this pattern was one character short and therefore matched NO real GSTIN -
# the supplier's only came through via the label fallback, and no buyer's at all.
_GSTIN_SHAPE = re.compile(r"\b(\d{2}[A-Z]{5}\d{4}[A-Z]\d[A-Z][A-Z0-9])\b")
_PAN_SHAPE = re.compile(r"\b([A-Z]{5}\d{4}[A-Z])\b")
_EMAIL = re.compile(r"\b([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b")

# Drug licence numbers: "20B-MH-MZ5-190671", "MH-TZ5-353350", "20B/MH/1234".
_DL_SHAPE = re.compile(r"\b((?:\d{2}[A-Z]?[-/])?[A-Z]{2,3}[-/][A-Z]{1,4}\d?[-/]?\d{4,8})\b")


def _labelled(text: str, labels: List[str], value: str) -> Optional[str]:
    """First value following any of these labels, trying them in order."""
    for label in labels:
        # Allow the label's words to be separated by nothing, spaces or dots -
        # some PDFs emit "InvoiceNo." with no space at all.
        loose = r"[\s.:\-]*".join(re.escape(w) for w in label.split())
        m = re.search(loose + r"[\s.:#\-]*" + value, text or "", re.I)
        if m:
            return m.group(1).strip()
    return None


_REFERENCES: Dict[str, Tuple[List[str], str]] = {
    "irn": (["IRN"], r"([A-Fa-f0-9]{16,64})"),
    "eway_bill_no": (["E Way Bill No", "EwayBill No", "E Way Bill", "Eway Bill"], r"(\d{10,16})"),
    "lr_no": (["LR No", "L R No", "LR RR No", "LR/RR No", "Lorry Receipt No"], _TOKEN),
    "transport": (["Transport Name", "Transporter", "Transport", "Carrier", "Name of Carrier"],
                  r"([A-Za-z][A-Za-z0-9 .,&\-']{2,48})"),
    "po_no": (["PO No", "P O No", "Order No", "Purchase Order No", "Ord Ref No"], _TOKEN),
}

_DATES: Dict[str, List[str]] = {
    "due_date": ["Due Date", "Payment Due", "Due On"],
    "lr_date": ["LR Date", "LR/RR No Date", "LR No Dt", "LR Dt"],
    "po_date": ["PO Date", "P O Date", "Ord Ref Date", "Order Date"],
}

_TOTALS: Dict[str, List[str]] = {
    "total_taxable_amount": ["Total Taxable Value", "Total Taxable Amount", "Total Taxable",
                             "Taxable Value Total", "Basic Amount", "Total Basic"],
    "total_discount_amount": ["Total Discount", "Discount Total", "SD Discount",
                              "Total Disc Amt", "Total Disc"],
    "total_cgst_amount": ["Total CGST Amt", "Total CGST Amount", "Total CGST", "CGST Total"],
    "total_sgst_amount": ["Total SGST Amt", "Total SGST Amount", "Total SGST", "SGST Total",
                          "Total UTGST Amt"],
    "total_igst_amount": ["Total IGST Amt", "Total IGST Amount", "Total IGST", "IGST Total"],
    "total_utgst_amount": ["Total UTGST Amount", "Total UTGST", "UTGST Total"],
}


def extract_references(text: str) -> Dict[str, Optional[str]]:
    """Transport, order and statutory references from the page text."""
    out: Dict[str, Optional[str]] = {}
    for field, (labels, value) in _REFERENCES.items():
        found = _labelled(text, labels, value)
        if found and field == "transport":
            # Stop at the next label on the same line.
            found = re.split(r"\s{2,}|\b(?:LR|GSTIN|PAN|Invoice|Due|Cases)\b", found)[0].strip(" .,-")
            found = found or None
        out[field] = found
    for field, labels in _DATES.items():
        out[field] = _labelled(text, labels, _DATE)
    return out


def extract_totals(text: str) -> Dict[str, Optional[str]]:
    """Invoice-level tax and discount totals, where the bill prints them."""
    out: Dict[str, Optional[str]] = {}
    for field, labels in _TOTALS.items():
        value = _labelled(text, labels, _MONEY)
        out[field] = value.replace(",", "") if value else None
    return out


def sum_line_totals(items: List[dict]) -> Dict[str, Optional[str]]:
    """The same totals computed from the lines, for invoices that print none.

    A shop's accounts need the tax split whether or not the supplier chose to
    print a summary, and the per-line figures are already extracted.
    """
    def _num(field) -> Optional[float]:
        if not isinstance(field, dict):
            return None
        raw = str(field.get("value") or "").replace(",", "")
        m = re.search(r"-?\d*\.?\d+", raw)
        return float(m.group()) if m else None

    sums: Dict[str, float] = {}
    seen: Dict[str, bool] = {}
    pairs = (
        ("total_taxable_amount", "amount"),
        ("total_cgst_amount", "cgst_amount"),
        ("total_sgst_amount", "sgst_amount"),
        ("total_igst_amount", "igst_amount"),
        ("total_utgst_amount", "utgst_amount"),
        ("total_discount_amount", "discount_amount"),
    )
    for total_field, line_field in pairs:
        for item in items or []:
            value = _num(item.get(line_field))
            if value is not None:
                sums[total_field] = sums.get(total_field, 0.0) + value
                seen[total_field] = True
    return {k: f"{sums[k]:.2f}" for k in sums if seen.get(k)}


# ------------------------------- the parties -------------------------------
_PARTY_HEADINGS = {
    "bill_to": re.compile(r"\bbill(?:ed)?\s*to\b", re.I),
    "ship_to": re.compile(r"\bship\s*to\b", re.I),
}
_STOP = re.compile(
    r"(gs\s*t\s*in|gstin|pan\s*no|pan\s*:|d\.?l\.?\s*no|drug\s*lic|food\s*lic|fssai|cin|"
    r"e-?mail|phone|mob\b|tel\b|invoice|state\s*code|pos\s*:|place\s*of\s*supply|"
    # Contact-person and reference labels printed inside a party's block; without
    # these, "C. Person : RIAZ BANDUKWALA" was read as the company's name.
    r"c\.?\s*person|contact|customer\s*details|transport|lr/rr|chq|cases|pay\s*terms|"
    r"due\s*date|actual\s*wt|volumetric)",
    re.I,
)


# Words that may introduce a party heading, e.g. "Customer Details (Bill To):".
# Kept to a short allowlist on purpose: anchoring a column on any word that
# happens to precede the heading would let Ship-to start inside Bill-to's text.
# NB: "party" is deliberately absent. It is the TAIL of the previous heading
# ("Bill to Party : Ship to Party :"), so allowing it let the Ship-to column
# anchor itself inside Bill-to's text and swallow the wrong company.
_LABEL_PREFIX = re.compile(r"(customer|details|buyer|consignee|receiver|[(\[:,.\-]|\s)+", re.I)

# Labels that mark the invoice-reference column - the one printed to the right
# of Ship-to on most layouts. Needed as a boundary, or the Ship-to block runs on
# and swallows the invoice number, dates and IRN.
_REF_COLUMN = re.compile(r"\b(invoice\s*(no|date)|due\s*date|ord\.?\s*ref|e\s*way\s*bill)\b", re.I)


def party_regions(page, word_tolerance: float = 1.5) -> Dict[str, str]:
    """Text of the Bill-to and Ship-to blocks, each from its own column.

    Both parties are usually printed side by side, with the invoice's own
    references in a third column. Read as flat text all three interleave line by
    line, so the Bill-to name, the Ship-to name and the invoice number end up in
    one string and none can be told apart.

    Each block is therefore cut twice: on the left at its own heading, on the
    right at whichever column comes next - the other party, or the references.
    It also starts at the heading's own line, so the page title and "Page 1 of 3"
    above it are not mistaken for the party's name.
    """
    try:
        words = page.extract_words(keep_blank_chars=False, x_tolerance=word_tolerance)
    except Exception:  # noqa: BLE001
        return {}
    if not words:
        return {}

    # Deliberately generous: a party's GSTIN is often printed several lines below
    # its address, and a tighter window cut it off on every invoice we have.
    limit = float(page.height) * 0.55
    anchors: List[Tuple[float, float, str]] = []   # (x0, top, key)
    for i, word in enumerate(words):
        top = float(word["top"])
        if top > limit:
            continue
        window = [w for w in words[i:i + 4] if abs(float(w["top"]) - top) <= 3.5]
        phrase = " ".join(str(w["text"]) for w in window)
        for key, pattern in _PARTY_HEADINGS.items():
            # `search`, not `match`: Zydus heads these columns "Customer Details
            # (Bill To):", so the words that name the party are not first. The
            # column is still anchored on the word the heading starts at.
            found = pattern.search(phrase)
            if not found:
                continue
            offset = len(phrase[:found.start()].split())
            if offset == 0:
                anchors.append((float(word["x0"]), top, key))
            elif _LABEL_PREFIX.fullmatch(phrase[:found.start()].strip()):
                # Zydus heads these columns "Customer Details (Bill To):", so the
                # column begins a couple of words before the party is named.
                # Restricted to a known prefix: anchoring on any preceding word
                # would let the Ship-to column start inside Bill-to's text.
                anchors.append((float(word["x0"]), top, key))
        if _REF_COLUMN.match(phrase):
            anchors.append((float(word["x0"]), top, "_refs"))

    parties = [a for a in anchors if a[2] != "_refs"]
    if not parties:
        return {}

    # Leftmost occurrence of each anchor, since a heading can repeat down the page.
    firsts: Dict[str, Tuple[float, float]] = {}
    for x0, top, key in anchors:
        if key not in firsts or x0 < firsts[key][0]:
            firsts[key] = (x0, top)
    ordered = sorted(firsts.items(), key=lambda kv: kv[1][0])

    from app.services.ocr.pdf_table import _visual_lines

    out: Dict[str, str] = {}
    for idx, (key, (start_x, start_top)) in enumerate(ordered):
        if key == "_refs":
            continue
        end_x = ordered[idx + 1][1][0] if idx + 1 < len(ordered) else float("inf")
        block = [
            w for w in words
            if start_x - 2 <= float(w["x0"]) < end_x - 2
            and start_top - 2 <= float(w["top"]) <= limit
        ]
        if block:
            out[key] = "\n".join(
                " ".join(str(w["text"]) for w in line[1]) for line in _visual_lines(block)
            )
    return out


# A company name often wraps onto the next line ("EASTERN AGENCIES" /
# "HEALTHCARE PVT LTD"); an address line is recognisable by its door or unit
# number, a PIN code, or the words that introduce one.
_ADDRESS_LIKE = re.compile(
    r"(\b[A-Z]?\d+[/-]|\bno\.?\s*\d|\bfloor\b|\broad\b|\brd\b|\bstreet\b|\bgala\b|"
    r"\bplot\b|\bsector\b|\bbuilding\b|\bcomplex\b|\bhighway\b|\bdistrict\b|"
    r"\bmumbai\b|\bpune\b|\bdelhi\b|\b\d{6}\b)",
    re.I,
)
# The first token that says "this is now an address". Used to cut a name that
# runs straight into its address on one visual line ("EASTERN AGENCIES
# HEALTHCARE A-2 FIRST FLOOR..."), which a line break alone does not separate.
_ADDRESS_START = re.compile(
    r"(?=\b(?:[A-Z]-\d+\b|\d+[/,-]|no\.?\s*\d|gala|plot|floor|road|rd\b|street|marg|lane|"
    r"sector|building|complex|compound|highway|opp\b|near|behind|\d{6}\b))",
    re.I,
)

# Words that genuinely END a company name. Deliberately short: "AGENCIES",
# "PHARMA" and "PVT" all appear mid-name ("EASTERN AGENCIES HEALTHCARE PVT LTD"),
# and treating them as terminal truncated the name to its first two words.
_NAME_TAIL = re.compile(r"\b(LIMITED|LTD|LLP|CORPORATION|INCORPORATED)\b\.?\s*$", re.I)


def _tidy_name(raw: str) -> str:
    """Clean up a company name recovered from wrapped, column-split text.

    Wrapping regularly leaves a half-repeated tail ("... PVT LTD LT"), because
    the same word appears at the end of one visual line and the start of the
    next. Dropping a token that merely prefixes the one before it fixes that
    without touching genuine repeats.
    """
    words = re.sub(r"\s+", " ", raw or "").strip(" ,.-").split()
    out: List[str] = []
    for word in words:
        if out:
            previous = out[-1].strip(".,").upper()
            current = word.strip(".,").upper()
            if current == previous or (len(current) >= 2 and previous.startswith(current)):
                continue
        out.append(word)
    return " ".join(out).strip(" ,.-")


def normalised_party_key(name: Optional[str]) -> str:
    """A comparison key for deciding whether two parties are the same company."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def party_details(text: str) -> Dict[str, Optional[str]]:
    """Name, GSTIN, PAN and address from one party's block.

    A company name regularly wraps onto a second line ("EASTERN AGENCIES" then
    "HEALTHCARE PVT LTD"), so continuation lines are joined until the text starts
    reading like an address. Taking only the first line truncated most names.
    """
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    cleaned: List[str] = []
    for line in lines:
        without = re.sub(
            r"\b(bill(?:ed)?\s*to|ship\s*to)\b\s*(party|details)?\s*[:\-)]*", "", line, flags=re.I
        )
        without = _STOP.split(without)[0]
        # A party's name often continues straight into its address on the same
        # visual line, so cut at the first address-looking token rather than
        # trusting the line break.
        without = _ADDRESS_START.split(without)[0]
        # Some suppliers prefix the party with its account code in their system.
        without = re.sub(r"^\s*\d{4,}\s+", "", without)
        cleaned.append(without.strip(" :,-()"))

    name = None
    index = None
    for i, line in enumerate(cleaned):
        if len(line) >= 4 and re.search(r"[A-Za-z]{3}", line) and not _ADDRESS_LIKE.search(line):
            parts = [line]
            # Join wrapped continuations, stopping once the name looks complete
            # or the next line is clearly the address.
            if not _NAME_TAIL.search(line):
                for follow in cleaned[i + 1:i + 3]:
                    if not follow or _ADDRESS_LIKE.search(follow) or len(follow) < 3:
                        break
                    parts.append(follow)
                    if _NAME_TAIL.search(follow):
                        break
            name = _tidy_name(" ".join(parts))
            index = i + len(parts) - 1
            break

    address = None
    if index is not None:
        parts = []
        for line in lines[index + 1:index + 5]:
            if _STOP.search(line):
                continue
            candidate = re.sub(r"\s+", " ", line).strip()
            if candidate:
                parts.append(candidate)
        address = ", ".join(parts).strip(" ,") or None

    gstin = _GSTIN_SHAPE.search(text or "")
    pan = None
    for candidate in _PAN_SHAPE.finditer(text or ""):
        # A GSTIN contains a PAN; only take a PAN that stands on its own.
        if not _GSTIN_SHAPE.search((text or "")[max(0, candidate.start() - 2):candidate.end() + 3]):
            pan = candidate.group(1)
            break
    return {"name": name, "gstin": gstin.group(1) if gstin else None, "pan": pan, "address": address}


def drug_licences(text: str) -> List[Tuple[Optional[str], Optional[str]]]:
    """Up to three (licence number, validity date) pairs from a party's block."""
    out: List[Tuple[Optional[str], Optional[str]]] = []
    seen = set()
    for match in _DL_SHAPE.finditer(text or ""):
        number = match.group(1)
        if number in seen:
            continue
        # A GSTIN or PAN is not a drug licence.
        if _GSTIN_SHAPE.fullmatch(number) or _PAN_SHAPE.fullmatch(number):
            continue
        seen.add(number)
        tail = (text or "")[match.end():match.end() + 40]
        date = re.search(_DATE, tail)
        out.append((number, date.group(1) if date else None))
        if len(out) == 3:
            break
    return out


def supplier_extras(text: str) -> Dict[str, Optional[str]]:
    """PAN, e-mail and drug licences from the supplier's own block."""
    out: Dict[str, Optional[str]] = {}
    pan = None
    for candidate in _PAN_SHAPE.finditer(text or ""):
        if not _GSTIN_SHAPE.search((text or "")[max(0, candidate.start() - 2):candidate.end() + 3]):
            pan = candidate.group(1)
            break
    out["pan"] = pan
    email = _EMAIL.search(text or "")
    out["email"] = email.group(1) if email else None
    for i, (number, date) in enumerate(drug_licences(text), start=1):
        out[f"dl_no_{i}"] = number
        out[f"dl_date_{i}"] = date
    return out
