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
# A date as Indian invoices print it, including the named-month form Menarini
# uses throughout ("22-Sep-2025"). Numeric months alone left every date on that
# bill blank although all of them were printed.
_MONTH_NAME = r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
_DATE = (
    r"([0-3]?\d[./\-][0-1]?\d[./\-]\d{2,4}"
    r"|\d{4}-\d{2}-\d{2}"
    r"|[0-3]?\d[\s./\-](?:" + _MONTH_NAME + r")[a-z]*[\s./\-]\d{2,4})"
)
_MONEY = r"([\d,]+\.\d{2}|[\d,]{2,})"
_TOKEN = r"([A-Za-z0-9][A-Za-z0-9\-\/]*)"

# A GSTIN is exactly 15 characters: 2-digit state code, the 10-character PAN,
# a 1-digit entity number, a literal Z, and a checksum. An earlier version of
# this pattern was one character short and therefore matched NO real GSTIN -
# the supplier's only came through via the label fallback, and no buyer's at all.
_GSTIN_SHAPE = re.compile(r"\b(\d{2}[A-Z]{5}\d{4}[A-Z]\d[A-Z][A-Z0-9])\b")
_GSTIN_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin_is_valid(gstin: Optional[str]) -> bool:
    """Whether a GSTIN's 15th character is its correct check character.

    GSTN's own scheme: each of the first 14 characters' base-36 value, weighted
    1, 2, 1, 2, ..., folded back into base 36 and summed; the check character
    makes the total a multiple of 36. A scanner's garbled text layer and a
    misread photo both produce GSTINs that are the right SHAPE but wrong - the
    MSV Lifesciences bill gave "33ABEFM0315R128". This catches them.
    """
    g = (gstin or "").strip().upper()
    if len(g) != 15 or any(ch not in _GSTIN_ALPHABET for ch in g):
        return False
    total = 0
    for i, ch in enumerate(g[:14]):
        product = _GSTIN_ALPHABET.index(ch) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return _GSTIN_ALPHABET[(36 - total % 36) % 36] == g[14]


_PAN_SHAPE = re.compile(r"\b([A-Z]{5}\d{4}[A-Z])\b")
_EMAIL = re.compile(r"\b([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b")

# Drug licence numbers: "20B-MH-MZ5-190671", "MH-TZ5-353350", "20B/MH/1234".
# A drug licence: "20B-MH-MZ5-190671", "MH-TZ5-379188" - or just its form and
# number, "20B-541751", which is how V L Enterprises prints its own. Never the
# middle of a longer token: V L's transaction number "SPD/2526/IPM/PTB/00558"
# contains a licence-shaped "IPM/PTB/00558", which we filed as its licence.
_DL_SHAPE = re.compile(
    r"(?<![\w/\-])"
    r"((?:\d{2}[A-Z]?[-/])?[A-Z]{2,3}[-/][A-Z]{1,4}\d?[-/]?\d{4,8}"
    r"|2[01][A-D][-/]\d{4,8})"
    # A following "/" is allowed: Kanchan writes "20B-MH-TZ2-521007/521025".
    r"(?!\w)"
)


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
    # "IRN No.: DDE3F8..." (Bharat), "IRN No. af84ef..." (Overseas): the "No."
    # stopped a bare "IRN" label reaching the value.
    "irn": (["IRN No", "IRN Number", "IRN"], r"([A-Fa-f0-9]{16,64})"),
    "eway_bill_no": (["E Way Bill No", "EwayBill No", "E Way Bill", "Eway Bill"], r"(\d{10,16})"),
    "lr_no": (["LR No", "L R No", "LR RR No", "LR/RR No", "Lorry Receipt No"], _TOKEN),
    # A transporter's name first; the mode only where no name is printed -
    # Overseas states just "Transportation Mode : BY HAND DELIVERY".
    "transport": (["Transport Name", "Transporter", "Name of Carrier", "Carrier",
                   "Transportation Mode", "Transport Mode", "Mode of Transport", "Transport"],
                  r"([A-Za-z][A-Za-z0-9 .,&\-']{2,48})"),
    "po_no": (["PO No", "P O No", "Order No", "Purchase Order No", "Ord Ref No"], _TOKEN),
}

_DATES: Dict[str, List[str]] = {
    "due_date": ["Due Date", "Payment Due", "Due On"],
    # Kanchan prints "LR/RR No. : 2035873 Date : 11/07/2025" - the number sits
    # between the label and the date, so a plain "LR Date" never matches.
    # Menarini prints "L.R. No. : LOCAL Date : 22 Sep 25".
    "lr_date": ["LR Date", "L R Date", "LR No Dt", "LR Dt", "LR RR No", "LR/RR No",
                "LR No", "L R No", "L.R. Date", "L.R. No", "LR/RR Date"],
    "po_date": ["PO Date", "P O Date", "Ord Ref Date", "Order Date", "Order No",
                "PO No", "P O No", "Cust Ord Rec Dt", "Cust Ord Dt", "Cust.Ord.Rec.Dt", "Cust Ord No"],
}

_TOTALS: Dict[str, List[str]] = {
    "total_gst_amount": ["Total GST Amount", "Total GST Amt", "Total GST", "GST Total",
                         "Total Tax Amount", "Total Tax"],
    "total_taxable_amount": ["Total Taxable Value", "Total Taxable Amount", "Total Taxable",
                             "Taxable Value Total", "Basic Amount", "Total Basic"],
    "total_discount_amount": ["Total Discount", "Discount Total", "SD Discount",
                              "Total Disc Amt", "Total Disc", "Less Discount", "Less Disc"],
    "total_cgst_amount": ["Total CGST Amt", "Total CGST Amount", "Total CGST", "CGST Total"],
    "total_sgst_amount": ["Total SGST Amt", "Total SGST Amount", "Total SGST", "SGST Total",
                          "Total UTGST Amt"],
    "total_igst_amount": ["Total IGST Amt", "Total IGST Amount", "Total IGST", "IGST Total"],
    "total_utgst_amount": ["Total UTGST Amount", "Total UTGST", "UTGST Total"],
}


# Where a value ends because the next label has begun. The two party columns
# flatten into one line of text, so a field's value runs straight into whatever
# the column beside it says.
_NEXT_LABEL = re.compile(
    r"\s{2,}|\b(?:LR|L\.R|GSTIN|GST\s*NO|PAN|Invoice|Due|Cases|Date|Mode|Weight|"
    r"Vehicle|Transporter|Transport|TEL|Phone|Mobile|FSSAI|DL|State|PO)\b",
    re.I,
)

# A capture that is really a label. Where a field is blank on the bill, the
# label printed after it becomes its "value": V L Enterprises prints
# "L.R. NO. : DATE :" with both fields empty and we reported the lorry receipt
# as "DATE"; Zydus gave transport as "PO Number". Inventing a transporter is
# worse than leaving the field blank, which is what the bill itself does.
_LABEL_WORDS = frozenset({
    "date", "dt", "mode", "no", "number", "no.", "gstin", "gst", "pan", "tel",
    "tel no", "phone", "mobile", "transporter", "transport", "transport mode",
    "name", "weight", "vehicle", "cases", "state", "code", "address", "fssai",
    "dl", "po number", "po no", "order no", "trn", "trn gstin",
})


def _is_label_not_value(found: str, text: str) -> bool:
    """Whether this capture is the next label rather than a value.

    Three signatures, all seen on real bills:

    * it IS a label word - "DATE", "TEL NO", "MODE", "PO Number";
    * the page prints a colon straight after it, which a value does not carry;
    * it is a short lowercase tail of a longer word ("ation" from
      "TRANSPORTATION Mode", "er's" from "Transporter's") - the mark of two
      columns colliding mid-word.
    """
    bare = found.strip(" .,-:").lower()
    if not bare or bare in _LABEL_WORDS:
        return True
    if re.search(re.escape(found.strip()) + r"\s*:", text or ""):
        return True
    return bool(re.fullmatch(r"[a-z][a-z']{1,6}", found.strip()))


# No length or shape test on the reference numbers themselves: Abbott really
# prints "LR No.:CC" and Overseas "GR/LR No. :18/09". Those look like noise and
# are exactly what is on the bill - only a captured LABEL is rejected.
def _is_address_not_carrier(found: str) -> bool:
    """A "carrier" that is really the parties' address flowing under the label.

    Two signatures: the same phrase twice over - the Bill-to and Ship-to
    columns flattened side by side ("VILE PARLE WEST VILE PARLE WEST", Bharat)
    - or plain address text (a city, a road, a PIN code).
    """
    words = found.upper().split()
    half = len(words) // 2
    if half and len(words) % 2 == 0 and words[:half] == words[half:]:
        return True
    return bool(_ADDRESS_LIKE.search(found))


def _labelled_all(text: str, labels: List[str], value: str):
    """Every value following any of these labels, labels in order of preference."""
    for label in labels:
        loose = r"[\s.:\-]*".join(re.escape(w) for w in label.split())
        for m in re.finditer(loose + r"[\s.:#\-]*" + value, text or "", re.I):
            yield m.group(1).strip()


def extract_references(text: str, exclude: Optional[str] = None) -> Dict[str, Optional[str]]:
    """Transport, order and statutory references from the page text.

    Every occurrence of a label is tried, not just the first, until one gives
    a real value. `exclude` is the parties' own text: Bharat prints "Name of
    Carrier" twice, the first time with the buyer's address flowing into the
    line beneath it - "VILE PARLE WEST" is an address, not a carrier - and the
    second with "QUICK COURIER" under it.
    """
    out: Dict[str, Optional[str]] = {}
    party = re.sub(r"\s+", " ", (exclude or "")).upper()
    for field, (labels, value) in _REFERENCES.items():
        out[field] = None
        for found in _labelled_all(text, labels, value):
            # Stop at the next label on the same line - for every reference,
            # not just the transporter.
            found = _NEXT_LABEL.split(found)[0].strip(" .,-:") or None
            if not found or _is_label_not_value(found, text):
                continue
            if field == "transport" and _is_address_not_carrier(found):
                continue
            out[field] = found
            break
    for field, labels in _DATES.items():
        out[field] = _labelled(text, labels, _DATE)
        if out[field] is None:
            # Allow a reference number to sit between the label and its date.
            out[field] = _labelled(text, labels, r"[A-Za-z0-9\-/]{0,20}\s*(?:date|dt)\s*[:\-]?\s*" + _DATE)
    return out


def extract_totals(text: str) -> Dict[str, Optional[str]]:
    """Invoice-level tax and discount totals, where the bill prints them."""
    out: Dict[str, Optional[str]] = {}
    for field, labels in _TOTALS.items():
        # "Less Disc. :Rs. 0.00" (Abbott): a currency mark may sit between.
        value = _labelled(text, labels, r"(?:rs\.?|inr|₹)?\s*" + _MONEY)
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
    out = {k: f"{sums[k]:.2f}" for k in sums if seen.get(k)}

    # The combined GST figure. Invoices print the heads, or the total, or both;
    # the client's import asks for both, so derive whichever is missing.
    heads = [sums[k] for k in ("total_cgst_amount", "total_sgst_amount",
                               "total_igst_amount", "total_utgst_amount") if k in sums]
    if heads:
        out["total_gst_amount"] = f"{sum(heads):.2f}"
    return out


# ------------------------------- the parties -------------------------------
_PARTY_HEADINGS = {
    "bill_to": re.compile(r"\bbill(?:ed)?\s*to\b", re.I),
    # "Ship to", "Shipped to" - and "Shiped to", one p, which is how V L
    # Enterprises spells it; that column was blank while every detail in it
    # was printed on the bill.
    "ship_to": re.compile(r"\bship(?:p?ed)?\s*to\b", re.I),
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

# A party heading and whatever introduces it, matched greedily from the start of
# the line - so the label prose contributed by the column BESIDE this one is
# removed along with this column's own heading.
_PARTY_LABEL_RUN = re.compile(
    r"^.*\b(?:bill(?:ed)?\s*to|ship(?:p?ed)?\s*to)\b"
    r"\s*(?:party|details|address)?\s*[:\-)(]*",
    re.I,
)

# Heading prose a party column carries when the cut between columns falls in
# the middle of a heading ("Details of Receiver (Billed to)", "Details of
# Consignee (Shiped to)", "Address of delivery").
_PARTY_PROSE = re.compile(
    r"\bdetails\s+of\b|\breceiver\b|\bconsignee\b|\baddress\s+of\s+delivery\b"
    r"|\((?:\s*(?:bill(?:ed)?|ship(?:p?ed)?)\s*(?:to)?\s*)\)?|\(\s*$",
    re.I,
)

_LEGAL_SUFFIX = re.compile(
    r"^\s*((?:pvt\.?|private)\s*(?:ltd\.?|limited)|(?:ltd\.?|limited))\s+(.+)$", re.I
)


def _suffix_last(name: str) -> str:
    """Put a leading "PVT LTD" back at the end of the name.

    A name wrapped across two lines in a narrow column is sometimes read tail
    first - V L gave "PVT LTD EASTERN AGENCIES HEALTHCARE". No company name
    begins with its legal suffix, so this reorder cannot damage a real one.
    """
    m = _LEGAL_SUFFIX.match(name or "")
    return f"{m.group(2).strip()} {m.group(1).strip()}" if m else name


_NAMEISH = re.compile(
    r"\b(LIMITED|LTD|PVT|PRIVATE|LLP|CORPORATION|DISTRIBUTOR|PHARMA|HEALTHCARE|"
    r"ENTERPRISES?|AGENC|LABORATOR|INDUSTRIES|REMEDIES|BIOTECH|LIFESCIENCE|"
    r"MEDICAL|MEDICOS?|CHEMISTS?|DRUGS?|TRADERS|STORES?|HOSPITAL|CLINIC)",
    re.I,
)


_INLINE_LABEL = re.compile(r"\b(?:address|name|state|code|city|pin)\s*:", re.I)
_LEADING_LABEL = re.compile(r"^\s*(?:name|party\s*name|address|add)\s*:\s*", re.I)
_ADDRESS_LABEL = re.compile(r"^\s*(?:address|add)\s*:", re.I)
# Labels of the column printed BESIDE a party's block, which run on into its
# lines when the two flatten into one: "DESTINATION" (V L), "Dt:", "Case:",
# "Loose:" (Zydus), "LR Date" (Bharat).
_BESIDE_ADDRESS = re.compile(
    r"\b(?:destination|distance|vehicle\b|cases?\s*:|cases\b|loose\s*:|dt\s*:|"
    r"l\.?\s*r\.?\s*(?:no|date)|transport(?:er)?|place\s*of\s*supply|name\s+of\s+carrier)",
    re.I,
)


def _has_doubled_glyphs(text: str) -> bool:
    """Whether this block was drawn twice, so every letter arrives doubled."""
    doubled = [
        t for t in re.findall(r"[A-Za-z]{4,}", text or "")
        if len(t) % 2 == 0 and t[0::2] == t[1::2]
    ]
    return len(doubled) >= 2


def _is_only_legal_suffix(name: Optional[str]) -> bool:
    return bool(name) and bool(re.fullmatch(
        r"\s*(?:(?:pvt\.?|private)\s*)?(?:ltd\.?|limited)\s*", name, re.I))


# Labels that mark the invoice-reference column - the one printed to the right
# of Ship-to on most layouts. Needed as a boundary, or the Ship-to block runs on
# and swallows the invoice number, dates and IRN.
# "DELIVERY DETAILS" (V L) heads a transport column beside Ship-to; without it
# as a boundary the consignee's block ran on into "DESTINATION :", "L.R. NO."...
_REF_COLUMN = re.compile(
    r"\b(invoice\s*(no|date)|due\s*date|ord\.?\s*ref|e\s*way\s*bill|"
    r"delivery\s*details|dispatch\s*details|despatch\s*details|transport\s*details)\b",
    re.I,
)


# A run of blank page this wide between two words on a line separates columns.
_COLUMN_GUTTER = 20.0
# How close two column starts must be to count as the same alignment.
_ALIGN_TOLERANCE = 3.0
# How far below the headings the party blocks reach, in points.
_BLOCK_DEPTH = 110.0
# The furthest a centred heading sits right of its column's left edge. V L's
# is 73pt; anything much further is a different column.
_MAX_CENTRING_SHIFT = 120.0


def _align_to_content(ordered, words, limit: float, visual_lines):
    """Move each column's left edge from its heading to where its text starts.

    Headings are often CENTRED over their columns while the details beneath are
    left-aligned. V L Enterprises centres "Details of Consignee (Shiped to)"
    at x=334 while every line of the consignee's details starts at x=282, so
    cutting at the heading sliced "EASTERN AGENCIES HEALTHCARE PVT LTD" in half
    and handed most of it to the Bill-to column.

    A column's real left edge is the x at which line after line begins a run of
    text. For each heading, the most common such start between the previous
    column and the heading itself is taken - and only when enough lines agree,
    so a single indented line cannot move a column.
    """
    if not ordered:
        return ordered
    # The parties' own headings - the reference column often starts higher up.
    head_top = min((top for key, (_, top) in ordered if key != "_refs"),
                   default=min(top for _, (_, top) in ordered))
    # Only the party blocks themselves: the line-item table further down has
    # columns of its own that say nothing about where these begin.
    below = [w for w in words if head_top + 3 < float(w["top"]) <= min(limit, head_top + _BLOCK_DEPTH)]
    starts: List[float] = []
    for _, line in visual_lines(below):
        prev_x1 = None
        for w in line:
            x0 = float(w["x0"])
            if prev_x1 is None or x0 - prev_x1 >= _COLUMN_GUTTER:
                starts.append(x0)
            prev_x1 = float(w["x1"])
    starts.sort()

    def lines_starting_near(x: float) -> int:
        return sum(1 for s in starts if abs(s - x) <= _ALIGN_TOLERANCE)

    refined = []
    floor = -1.0
    for key, (x0, top) in ordered:
        if key == "_refs" or lines_starting_near(x0) >= 3:
            # Text already starts under the heading - an ordinary left-aligned
            # column. Nothing to correct, and every invoice but V L is this.
            refined.append((key, (x0, top)))
            floor = x0 + 10
            continue
        window = [s for s in starts if max(floor, x0 - _MAX_CENTRING_SHIFT) < s <= x0 + 2]
        best: List[float] = []
        group: List[float] = []
        for s in window:
            if group and s - group[0] > _ALIGN_TOLERANCE:
                if len(group) > len(best):
                    best = group
                group = []
            group.append(s)
        if len(group) > len(best):
            best = group
        new_x = min(best) if len(best) >= 3 else x0
        refined.append((key, (new_x, top)))
        floor = new_x + 10
    return refined


def _without_overprint(page, limit: float):
    """The page with any second, over-printed copy of a text run removed.

    Overseas draws its consignee block twice, the copies a couple of points
    apart - one a baseline lower, or one in the bold face - so as text the two
    interleave letter by letter: "AAdddd r:e sAs-2 FIRST :FLAO-2O RFI TROSTB".
    Each copy on its own reads perfectly ("Address:A-2 FIRST FLOOR TOBACCO
    HOUSE"). A run of characters - one font, one baseline - that lies on top of
    a longer run within a few points is that second copy, and is dropped.

    Exact duplicates (faux-bold) are removed by pdfplumber's dedupe_chars
    first. Runs merely side by side on a line never overlap, so ordinary text
    is untouched; and only the header area is considered, never the table.
    """
    try:
        page = page.dedupe_chars(tolerance=1)
    except Exception:  # noqa: BLE001 - older pdfplumber: carry on undeduped
        pass
    runs: Dict[Tuple[str, float], List[dict]] = {}
    for ch in page.chars:
        if float(ch["top"]) > limit or not str(ch.get("text", "")).strip():
            continue
        runs.setdefault((ch.get("fontname", ""), round(float(ch["top"]), 1)), []).append(ch)

    spans = []
    for key, chars in runs.items():
        x0 = min(float(c["x0"]) for c in chars)
        x1 = max(float(c["x1"]) for c in chars)
        spans.append((key, x0, x1, len(chars)))

    dropped = set()
    for i, (ka, a0, a1, na) in enumerate(spans):
        for kb, b0, b1, nb in spans[i + 1:]:
            if abs(ka[1] - kb[1]) > 3.0 or ka == kb:
                continue
            overlap = min(a1, b1) - max(a0, b0)
            shorter = min(a1 - a0, b1 - b0)
            if shorter <= 0 or overlap < 0.5 * shorter:
                continue
            # Keep the longer run; on a tie, the upper one (printed first).
            if (na, -ka[1]) >= (nb, -kb[1]):
                dropped.add(kb)
            else:
                dropped.add(ka)
    if not dropped:
        return page
    return page.filter(
        lambda obj: obj.get("object_type") != "char"
        or (obj.get("fontname", ""), round(float(obj["top"]), 1)) not in dropped
        or float(obj["top"]) > limit
    )


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
    # Deliberately generous: a party's GSTIN is often printed several lines below
    # its address, and a tighter window cut it off on every invoice we have.
    limit = float(page.height) * 0.55
    try:
        page = _without_overprint(page, limit)
        words = page.extract_words(keep_blank_chars=False, x_tolerance=word_tolerance)
    except Exception:  # noqa: BLE001
        return {}
    if not words:
        return {}
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

    # A reference label well ABOVE the party headings is not the column beside
    # them: V L prints "IRN No." mid-page over its consignee's details, and
    # taking that as the boundary cut "PVT LTD" off the consignee's name.
    party_top = min(top for _, top, _ in parties)
    anchors = [a for a in anchors if a[2] != "_refs" or a[1] >= party_top - 15]

    # Leftmost occurrence of each anchor, since a heading can repeat down the page.
    firsts: Dict[str, Tuple[float, float]] = {}
    for x0, top, key in anchors:
        if key not in firsts or x0 < firsts[key][0]:
            firsts[key] = (x0, top)
    ordered = sorted(firsts.items(), key=lambda kv: kv[1][0])

    from app.services.ocr.pdf_table import _visual_lines

    ordered = _align_to_content(ordered, words, limit, _visual_lines)

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
    # The supplier's own account code for the buyer, printed against the name
    # ("EASTERN AGENCIES HEALTHCARE PRIVATE LIMITED[8482]", Menarini), is not
    # part of the company's name.
    return re.sub(r"\s*[\[(]\s*\d{2,}\s*[\])]\s*$", "", " ".join(out)).strip(" ,.-")


# GSTIN state codes for the Union Territories that levy UTGST - those WITHOUT
# their own legislature. A UT that has a legislature (Delhi 07, Puducherry 34,
# Jammu & Kashmir 01) levies SGST like any state, so those are deliberately
# absent: treating them as UTGST would post the tax under a head they do not use.
#   04 Chandigarh   26 Dadra & Nagar Haveli and Daman & Diu   31 Lakshadweep
#   35 Andaman & Nicobar Islands                              38 Ladakh
UTGST_STATE_CODES = frozenset({"04", "25", "26", "31", "35", "38"})


def _state_code(gstin: Optional[str]) -> Optional[str]:
    value = str(gstin or "").strip()
    return value[:2] if len(value) >= 2 and value[:2].isdigit() else None


def local_tax_head(supplier_gstin: Optional[str], buyer_gstin: Optional[str]) -> Optional[str]:
    """The head that pairs with CGST on an intra-state sale: "sgst" or "utgst".

    Supply inside a Union Territory without a legislature is taxed CGST + UTGST;
    everywhere else it is CGST + SGST. Returns None when the state code cannot be
    read, so the caller falls back to the column heading.
    """
    code = _state_code(supplier_gstin) or _state_code(buyer_gstin)
    if code is None:
        return None
    return "utgst" if code in UTGST_STATE_CODES else "sgst"


def is_interstate(supplier_gstin: Optional[str], buyer_gstin: Optional[str]) -> Optional[bool]:
    """True when the sale crosses a state line, False when it does not.

    The first two digits of a GSTIN are the state code. Same code means an
    intra-state sale, taxed CGST + SGST; different codes mean inter-state, taxed
    IGST. Returns None when either GSTIN is missing, so the caller falls back to
    the column heading rather than guessing.
    """
    for value in (supplier_gstin, buyer_gstin):
        if not value or len(str(value).strip()) < 2 or not str(value).strip()[:2].isdigit():
            return None
    return str(supplier_gstin).strip()[:2] != str(buyer_gstin).strip()[:2]


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
    address_opens_at: Optional[int] = None
    for line in lines:
        # Everything up to and including the LAST party heading on the line is
        # the neighbouring column's label text, not this party's name. The two
        # columns flatten into one line of text, so V L gave us "Receiver (
        # Details of Consignee (Shiped PVT LTD EASTERN AGENCIES HEALTHCARE PVT
        # LTD", where the company is only the tail.
        without = _PARTY_LABEL_RUN.sub("", line)
        # ...and the heading prose that survives on a line of its own when the
        # column cut falls mid-heading: V L's bill-to block opens "Receiver
        # (Billed to) Details of", its ship-to block "Consignee (Shiped".
        without = _PARTY_PROSE.sub(" ", without)
        # A field label that OPENS the line is just that field's name
        # ("Name : EASTERN AGENCIES...", Overseas); one in the middle is two
        # columns colliding, and is handled below.
        if _ADDRESS_LABEL.match(without) and address_opens_at is None:
            # "Address : SHOP NO-14..." - the address starts here, so nothing
            # from this line on is the name (Abbott's consignee).
            address_opens_at = len(cleaned)
        without = _LEADING_LABEL.sub("", without)
        without = _BESIDE_ADDRESS.split(without)[0]
        without = _STOP.split(without)[0]
        # A party's name often continues straight into its address on the same
        # visual line, so cut at the first address-looking token rather than
        # trusting the line break.
        before_address = _ADDRESS_START.split(without)[0]
        if len(before_address.strip(" :,-()")) < 3 and before_address != without:
            # The line IS address from its first word ("Billed To: A-2 FIRST
            # FLOOR..."). Recorded, because no name can come after it - Abbott
            # prints no buyer name at all, and the next line, "PARLE(WEST)",
            # is the tail of that street address.
            address_opens_at = address_opens_at if address_opens_at is not None else len(cleaned)
        without = before_address
        # Some suppliers prefix the party with its account code in their system.
        without = re.sub(r"^\s*\d{4,}\s+", "", without)
        cleaned.append(re.sub(r"\s+", " ", without).strip(" :,-()"))

    name = None
    index = None
    if _has_doubled_glyphs(text):
        # The block is drawn twice, slightly offset - "NNaammee", "AAdddd r:e
        # sAs" - so its text is two copies interleaved letter by letter.
        # Overseas does this. No name can be read out of that honestly, so
        # return none and let the GSTIN and PAN, which survive it, carry the
        # party.
        cleaned = []
    address_started = False
    for i, line in enumerate(cleaned):
        if address_opens_at is not None and i >= address_opens_at:
            address_started = True
        if (i > 1 or address_started) and not _NAMEISH.search(line):
            # The company name opens the block. Something found further down -
            # or after the address has already begun - that does not even look
            # like a business is more address: V L's ship-to column was cut too
            # far right and offered "VILE PARLE", and Abbott prints no buyer
            # name at all, going from "Billed To:" straight into the street.
            break
        if _ADDRESS_LIKE.search(line):
            address_started = True
        if _INLINE_LABEL.search(line):
            # "PARLE(WEST Address: ANDHERI" - a label inside the candidate
            # means two columns ran together here (Abbott), not a name.
            continue
        if len(line) >= 4 and re.search(r"[A-Za-z]{3}", line) and not _ADDRESS_LIKE.search(line):
            parts = [line]
            # Join wrapped continuations, stopping once the name looks complete
            # or the next line is clearly the address.
            if not _NAME_TAIL.search(line):
                for j, follow in enumerate(cleaned[i + 1:i + 3], start=i + 1):
                    if (not follow or _ADDRESS_LIKE.search(follow) or len(follow) < 3
                            or _INLINE_LABEL.search(follow)
                            or (address_opens_at is not None and j >= address_opens_at)):
                        # "Address: SHOP NO-14..." is not the rest of the name.
                        break
                    parts.append(follow)
                    if _NAME_TAIL.search(follow):
                        break
            name = _tidy_name(_suffix_last(" ".join(parts)))
            index = i + len(parts) - 1
            if _is_only_legal_suffix(name):
                # "PVT LTD" alone is the tail of a name wrapped out of view,
                # not a company. Blank is honest; this is not.
                name = None
                continue
            break

    address = None
    if index is None and address_opens_at is not None:
        # No name in the block (Abbott's Billed To), but its address is there.
        index = address_opens_at - 1
    if index is not None:
        parts = []
        for line in lines[index + 1:index + 5]:
            line = _PARTY_LABEL_RUN.sub("", line)
            # The address, not the label in front of it or the column printed
            # beside it ("DISTANCE : 0" on V L, "LR Date" on Bharat, "Tel. No."
            # and "D.L. No." on Kanchan). Cut at those rather than dropping the
            # line: Kanchan's street is on the same line as its phone number,
            # and skipping the whole line lost "A-2 FIRST FLOOR TOBACCO HOUSE".
            line = _LEADING_LABEL.sub("", line)
            line = _BESIDE_ADDRESS.split(line)[0]
            line = _STOP.split(line)[0]
            candidate = re.sub(r"\s+", " ", line).strip(" ,:")
            if candidate:
                parts.append(candidate)
        # The same word ending one wrapped line and starting the next ("GOLDEN
        # TOBACCO LIMITED" / "LIMITED S.V.ROAD", JB) is one word, not two.
        address = _tidy_name(", ".join(parts)) or None

    gstin = _GSTIN_SHAPE.search(text or "")
    pan = None
    for candidate in _PAN_SHAPE.finditer(text or ""):
        # A GSTIN contains a PAN; only take a PAN that stands on its own.
        if not _GSTIN_SHAPE.search((text or "")[max(0, candidate.start() - 2):candidate.end() + 3]):
            pan = candidate.group(1)
            break
    return {"name": name, "gstin": gstin.group(1) if gstin else None, "pan": pan, "address": address}


def supplier_gstin_for_pan(pan: Optional[str], found: Optional[str],
                           page_text: str) -> Optional[str]:
    """A supplier GSTIN that agrees with the supplier's PAN, or None.

    A GSTIN is two state digits, then the holder's PAN, then an entity digit,
    "Z", and a checksum - so these two fields cannot disagree on a real bill.
    When they do, the GSTIN belongs to somebody else: Abbott prints the BUYER's
    GSTIN inside its own address block and its own only in the page footer, so
    its invoices were filed under the pharmacy's GSTIN.

    Returns a replacement only when the PAN is known, the GSTIN found does not
    match it, and the page carries one that does. Otherwise None - keep what we
    have rather than swap one guess for another.
    """
    if not pan or not found:
        return None
    pan = pan.strip().upper()
    if found.strip().upper()[2:12] == pan:
        return None
    for candidate in _GSTIN_SHAPE.findall(page_text or ""):
        if candidate.upper()[2:12] == pan:
            return candidate.upper()
    return None


_DL_DATE_FIRST = re.compile(
    r"\b2[01][A-D]\s*:?\s*" + _DATE + r"\s*/\s*([A-Z]{2,3}-[A-Z]{1,4}\d?-?\d{4,8})(?!\w)"
)


def drug_licences(text: str) -> List[Tuple[Optional[str], Optional[str]]]:
    """Up to three (licence number, validity date) pairs from a party's block."""
    out: List[Tuple[Optional[str], Optional[str]]] = []
    seen = set()
    # Some bills print the validity FIRST: Abbott's "DL No.-20B11.09.2027/
    # MH-TZ2-491363" is form 20B, valid till 11.09.2027, number MH-TZ2-491363.
    for match in _DL_DATE_FIRST.finditer(text or ""):
        number = match.group(2)
        if number not in seen:
            seen.add(number)
            out.append((number, match.group(1)))
    for match in _DL_SHAPE.finditer(text or ""):
        number = match.group(1)
        if number in seen:
            continue
        # A GSTIN or PAN is not a drug licence.
        if _GSTIN_SHAPE.fullmatch(number) or _PAN_SHAPE.fullmatch(number):
            continue
        seen.add(number)
        # Suppliers join the licence to its validity differently: Bharat writes
        # "MH-MZ5-190671 28.05.2030", Zydus "20B-MH-MZ4-373004 & 25.11.2029".
        # Allow a separator and a little slack, but stop before the next licence
        # so one date is not attached to two numbers.
        tail = (text or "")[match.end():match.end() + 64]
        tail = _DL_SHAPE.split(tail)[0]
        # Anchored to the licence number: the date must FOLLOW it, with only
        # separators or a validity phrase between - "& 25.11.2029" (Zydus),
        # " Valid till - 27-Apr-2028" (Menarini). Searching anywhere in the tail
        # would hand a licence whatever date is printed next on the line, such
        # as Menarini's e-way bill date.
        date = re.match(
            r"(?:[\s&,:/]|valid\s*(?:till|upto|up\s*to|to)?|validity|exp(?:iry)?|till)*"
            r"[:\-]?\s*" + _DATE,
            tail,
            re.I,
        )
        out.append((number, date.group(1) if date else None))
        # More than three are collected on purpose: the caller drops any that
        # belong to another party, and then takes the first three that remain.
        if len(out) == 12:
            break
    return out


def supplier_extras(text: str, exclude: Optional[str] = None,
                    page_text: Optional[str] = None) -> Dict[str, Optional[str]]:
    """PAN, e-mail and drug licences belonging to the SUPPLIER.

    `exclude` is the Bill-to and Ship-to text. A pharma invoice prints drug
    licences for every party, and when the supplier's own block carries none the
    search widens to the whole page - which is where the buyer's live. Zydus was
    having its customer's licence filed as its third own licence. Anything that
    appears in a party block is therefore dropped: it is not the vendor's.
    """
    out: Dict[str, Optional[str]] = {}
    blocked = {n for n, _ in drug_licences(exclude or "")} if exclude else set()
    pan = None
    for candidate in _PAN_SHAPE.finditer(text or ""):
        if not _GSTIN_SHAPE.search((text or "")[max(0, candidate.start() - 2):candidate.end() + 3]):
            pan = candidate.group(1)
            break
    out["pan"] = pan
    email = _EMAIL.search(text or "")
    out["email"] = email.group(1) if email else None
    index = 0
    taken = set()
    # The supplier's own block first; then the rest of the page, for licences
    # printed away from it - Abbott prints its own in the page footer, and JB's
    # second sits on a line below where its block is cut. Anything a party
    # block carries is still the buyer's, and is still skipped.
    for source in (text, page_text or ""):
        for number, date in drug_licences(source):
            if number in blocked or number in taken:
                continue
            taken.add(number)
            index += 1
            out[f"dl_no_{index}"] = number
            out[f"dl_date_{index}"] = date
            if index == 3:
                return out
    return out
