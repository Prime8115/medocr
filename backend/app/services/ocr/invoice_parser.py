"""Deterministic invoice parser for digital (computer-generated) PDFs.

Reads the line-item table directly from the PDF using pdfplumber - no AI, no
per-call cost, no page limit, no rate limits, exact values. Works for any digital
distributor invoice whose columns can be recognised. Falls back to the AI
pipeline when the table can't be recognised (scanned/photographed invoices, or
unusual layouts).

Two things this parser is careful about, because both burned real users:

1. **Printed copies.** A GST tax invoice is routinely printed three times in one
   PDF - "Original for Recipient", "Duplicate for Transporter", "Triplicate for
   Supplier". Parsing every page then yields 3x the real line count. We split the
   pages into copy groups and read only the first one.

2. **Which price is "rate".** MRP, PTR, PTS and the billed rate are four
   different numbers. We map each to its own field and record which column the
   billed `rate` came from, instead of picking whichever price column happens to
   sit furthest left.
"""
import logging
import re
from typing import Dict, List, Optional, Tuple

from app.services.ocr.amount_words import total_from_words
from app.services.ocr.invoice_header import (
    extract_references,
    extract_totals,
    is_interstate,
    local_tax_head,
    normalised_party_key,
    party_details,
    party_regions,
    sum_line_totals,
    supplier_extras,
    supplier_gstin_for_pan,
    _has_doubled_glyphs,
)
from app.services.ocr.invoice_checks import line_arithmetic_holds
from app.services.ocr.pdf_table import WORD_TOLERANCE, extract_word_tables

log = logging.getLogger(__name__)

# field -> (keywords in PREFERENCE order, exclude keywords). Header cells are
# normalized to lowercase with spaces/newlines/dots removed before matching.
# Order of this dict matters: earlier fields claim their column first.
_COLS: Dict[str, Tuple[List[str], List[str]]] = {
    "description": (
        ["productname", "itemname", "description", "proddesc", "particulars", "product",
         "desc", "item", "goods", "medicine"],
        ["hsn", "code", "qty", "rate", "amount"],
    ),
    "hsn": (["hsncode", "hsn"], []),
    "product_code": (["productcode", "prdcode", "itemcode", "prodcode", "code"], ["hsn"]),
    "manufacturer": (["mfgname", "manufacturername", "manufacturer", "mfgco", "company"], ["code", "date", "cd"]),
    # Excludes "exp": Bharat heads one column "Exp.date / Mfg.date", and the
    # expiry is the field a pharmacist actually needs, so it claims that column.
    # Excludes "batch" too: Menarini stacks "Batch No" over "Mfg.Date" in ONE
    # column, where the record line carries the BATCH and the wrapped line below
    # carries the date. Letting mfg_date claim it left every batch number blank.
    "mfg_date": (["mfgdate", "mfgdt", "manufacturingdate", "mfd"], ["exp", "batch"]),
    "uom": (["uom", "unitofmeasure"], []),
    "batch_no": (["batchno", "batch", "lotno", "lot", "bno", "btno"], ["bill", "sr", "inv"]),
    # No "mfg" exclusion: a Mfg-only column never contains "exp", while Bharat
    # heads one column "Exp.date / Mfg.date" - excluding it lost every expiry.
    "expiry": (["expdate", "expiry", "exp"], []),
    "pack": (["packing", "pack"], []),
    # Free/scheme quantity is claimed BEFORE quantity so a "F.QTY" column can
    # never be mistaken for the billed quantity.
    # "Fr. Qty." (Kanchan) normalises to "frqty", which matched nothing before.
    "free_quantity": (["freeqty", "frqty", "schemeqty", "free", "fqty", "scheme", "bonus"], ["%", "value", "amount"]),
    "quantity": (["quantity", "qty", "nos", "units"], ["free", "fqty", "scheme", "bonus", "%"]),
    "mrp": (["mrp"], ["%"]),
    "ptr": (["pricetoretailer", "retailerprice", "ptr"], ["%"]),
    "pts": (["pricetostockist", "stockistprice", "distributorprice", "distprice", "pts"], ["%"]),
    # An explicit billed-rate column. "NIR" (Net Invoice Rate) is tried first
    # because Bharat prints it beside a bare CGST "Rate" column that would
    # otherwise win. GST rate columns are excluded outright.
    "rate": (
        ["nir", "netinvoicerate", "billrate", "netrate", "purchaserate", "salerate",
         "unitprice", "prate", "rate"],
        ["%", "mrp", "ptr", "pts", "cgst", "sgst", "igst", "gst", "tax"],
    ),
    "discount_percent": (["disc%", "discount%", "discount", "disc"], ["amt", "amount", "value", "rs"]),
    "discount_amount": (["discamt", "discountamt", "discountamount"], ["%"]),
    "cd_percent": (["cd%", "cashdisc%", "cashdiscount%"], ["amt", "amount", "rs"]),
    "cd_amount": (["cdamt", "cdamount", "cashdiscamt", "cashdiscountamt"], ["%"]),
    "wp_percent": (["wp%", "wpdisc%"], ["amt", "amount"]),
    "wp_amount": (["wpamt", "wpamount", "wpvalue"], ["%"]),
    "scheme": (["schemedesc", "schemedescription", "schemename", "scheme"], ["%", "qty", "amt", "value"]),
    "scheme_value": (["schemevalue", "schemeamt", "schemeamount"], ["%"]),
    # "Sale Value" (Overseas) is the line BEFORE its discount - a gross figure.
    # Left to `amount`, it overstated every line by the discount.
    "gross_amount": (["grossamount", "grossamt", "grossvalue", "grosstotal", "totalbasic", "gross",
                      "salevalue"], ["%"]),
    # "Total Amount" (Menarini) is the line INCLUDING tax - a net amount, not
    # the taxable one. Excluded from anything taxable so the column the bill
    # actually sums still goes to `amount`.
    "net_amount": (["netamount", "netamt", "netvalue", "totalamount"], ["%", "taxable"]),
    "utgst_percent": (["utgst%", "utgstrate"], ["amt", "amount"]),
    "utgst_amount": (["utgstamt", "utgstamount"], ["%", "rate"]),
    "scheme_percent": (["sch%", "scheme%", "schemediscount"], ["amt", "qty"]),
    "total_quantity": (["totalqty", "totqty", "netqty"], ["%", "free"]),
    # Tax columns are captured per head so the amounts reach the accounts, and so
    # a zero-rated line can be told apart from an unreadable one.
    "cgst_percent": (["cgst%", "cgstrate"], ["amt", "amount"]),
    "cgst_amount": (["cgstamt", "cgstamount"], ["%", "rate"]),
    "sgst_percent": (["sgst%", "sgstrate", "utgst%"], ["amt", "amount"]),
    "sgst_amount": (["sgstamt", "sgstamount", "utgstamt"], ["%", "rate"]),
    "igst_percent": (["igst%", "igstrate"], ["amt", "amount"]),
    "igst_amount": (["igstamt", "igstamount"], ["%", "rate"]),
    # The net/taxable column is what the bill actually sums; a plain "Amount"
    # column (Kanchan) is the figure BEFORE the line discount.
    # The taxable value - what GST is charged on, and what the lines are summed
    # against. `net_amount` and `gross_amount` are separate columns above, so
    # they are no longer allowed to stand in for this one.
    "amount": (
        ["taxableamount", "taxablevalue", "taxableamt", "amount", "value", "total"],
        # A tax column is not the line's own amount, and neither is a discount
        # column. Overseas prints "Disc Value" and "CGST Amount" beside its
        # taxable "Trans. Value"; without these, the sum of the invoice was the
        # sum of its discounts.
        ["%", "gross", "net", "disc", "cgst", "sgst", "igst", "utgst"],
    ),
}

_COPY_MARKERS = (
    ("original", re.compile(r"\boriginal\b", re.I)),
    ("duplicate", re.compile(r"\bduplicate\b", re.I)),
    ("triplicate", re.compile(r"\btriplicate\b", re.I)),
    ("quadruplicate", re.compile(r"\bquadruplicate\b", re.I)),
    ("office", re.compile(r"\boffice\s+copy\b", re.I)),
)

# India's highest GST slab. Anything above it is a misread cell, not a rate.
_MAX_GST_PERCENT = 28.0

# The rates GST is actually charged at - whole, and split into CGST + SGST
# halves - plus the small special rates. A figure that is not one of these is
# tax, not a rate. "Below 28" was not enough: Menarini's single-figure CGST
# cells carry 27.77 and 11.89 rupees, which read as 27.77% and 11.89% and left
# both lines with no tax, so the bill's CGST total came out 39.66 short.
_GST_RATES = (0.0, 0.1, 0.125, 0.25, 0.5, 1.0, 1.5, 2.5, 3.0, 5.0, 6.0, 7.5, 9.0,
              12.0, 14.0, 18.0, 28.0)


# Confidence for a figure computed from others on the bill rather than read off
# it. Below the review threshold's "certain", so it is marked for a glance.
_DERIVED_CONFIDENCE = 0.85


def _is_gst_rate(n: float) -> bool:
    return any(abs(n - r) < 0.001 for r in _GST_RATES)

# No real pharmacy line carries more units than this. A bigger number in the
# quantity column means we picked up an invoice or IRN number from a footer.
_MAX_LINE_QUANTITY = 1_000_000

_SUMMARY_ROW = re.compile(r"\b(total|grand|net\s*amount|sub\s*total|subtotal|carried|c/f|b/f)\b", re.I)


def _norm(cell) -> str:
    if cell is None:
        return ""
    return re.sub(r"[\s\.\n_\-]+", "", str(cell)).lower()


def _clean(cell) -> str:
    if cell is None:
        return ""
    return re.sub(r"\s+", " ", str(cell)).strip()


# Suppliers name the batch column differently - Zydus "BATCH", JB "Batch
# Number", Bharat "Lot No.". Demanding the literal word "batch" rejected Bharat.
# "B.No.", "Bt.No", "Batch#" and "Lot No" are all common headings for the same
# column; demanding the literal word "batch" rejected whole invoices.
_BATCH_MARKERS = ("batch", "lotno", "lot", "bno", "btno", "bat")
_ITEM_MARKERS = ("qty", "quantity", "productname", "product", "item", "description")


def _find_header_row(table) -> Optional[int]:
    """Row index of the column-header row (a batch/lot marker + a qty/product one)."""
    for i, row in enumerate(table):
        joined = " ".join(_norm(c) for c in row)
        if any(b in joined for b in _BATCH_MARKERS) and any(m in joined for m in _ITEM_MARKERS):
            return i
    return None


def _map_columns(header_row) -> dict:
    """Map our field -> column index.

    Keywords are tried in PREFERENCE order, and the whole header is scanned for
    each keyword before moving to the next one. That is what makes the mapping
    stable across suppliers: a column literally headed "RATE" always wins over a
    "PTR" column that happens to be printed to its left.
    """
    norms = [_norm(c) for c in header_row]
    mapping: dict = {}
    taken: set = set()
    for field, (includes, excludes) in _COLS.items():
        for keyword in includes:
            hit = None
            for idx, h in enumerate(norms):
                if not h or idx in taken:
                    continue
                if keyword in h and not any(e in h for e in excludes):
                    hit = idx
                    break
            if hit is not None:
                mapping[field] = hit
                taken.add(hit)
                break
    return mapping


def _header_text(header_row, idx: Optional[int]) -> str:
    if idx is None or idx >= len(header_row):
        return ""
    return _clean(header_row[idx])


def _gst_columns(header_row) -> List[int]:
    """Indices of GST-rate columns (CGST/SGST/IGST) whose percentage we sum.

    Most invoices mark the rate with a "%". JB heads its pair "CGST Rate | Amt."
    with no percent sign at all, so a rate column is also recognised by the word
    "rate" - the cell's first number is the percentage either way. Without this
    the invoice has no GST, and its tax-inclusive printed total can never be
    reconciled against the taxable lines.
    """
    out = []
    for idx, c in enumerate(header_row):
        h = _norm(c)
        if "gst" not in h:
            continue
        if "%" in str(c) or "rate" in h:
            out.append(idx)
        elif not any(w in h for w in ("amt", "amount", "value")):
            # A BARE tax heading - V L Enterprises heads its pair just "CGST"
            # and prints "9.00 655.67" beneath it, the rate and the tax in one
            # cell. The heading cannot say which, so it goes through the same
            # arithmetic as every other merged tax cell: a figure at or below
            # the maximum GST rate is the rate, the next one is the amount.
            # Mapping a bare heading straight to the amount would have posted
            # 9% as nine rupees of tax on every line of that invoice.
            out.append(idx)
    return out


_NUM = re.compile(r"-?[\d,]*\.?\d+")


def _num(cell) -> Optional[str]:
    m = _NUM.search(str(cell or ""))
    return m.group().replace(",", "") if m else None


def _f(value, confidence: Optional[float] = 1.0):
    return {"value": value if value not in (None, "") else None, "confidence": confidence}


# --------------------------- printed-copy handling ---------------------------
def _copy_label(text: str) -> Optional[str]:
    """The copy this page belongs to, from markers like 'DUPLICATE FOR TRANSPORTER'."""
    head = (text or "")[:1500]
    for name, pattern in _COPY_MARKERS:
        if pattern.search(head):
            return name
    return None


_COPY_WORDS = re.compile(r"\b(original|duplicate|triplicate|quadruplicate)\b", re.I)


def _page_signature(text: str) -> str:
    """A page's content with the copy wording removed, for comparing copies."""
    return re.sub(r"\s+", " ", _COPY_WORDS.sub("", text or "")).strip()


def _detect_copies(pdf, max_copies: int = 4) -> int:
    """How many times this invoice is printed in the file, read cheaply.

    Reading every page's text just to find the copy markers was costing eight
    seconds on a 33-page triplicate invoice - by far the slowest thing in the
    upload. Copies are exact repeats at a fixed period, so comparing page 1 with
    the page one period later settles it after reading two pages instead of all
    of them.

    Returns 1 when the file holds a single invoice.
    """
    total = len(pdf.pages)
    if total < 2:
        return 1
    try:
        first = _page_signature(pdf.pages[0].extract_text() or "")
    except Exception:  # noqa: BLE001
        return 1
    if not first:
        return 1

    for copies in range(max_copies, 1, -1):
        if total % copies:
            continue
        try:
            candidate = _page_signature(pdf.pages[total // copies].extract_text() or "")
        except Exception:  # noqa: BLE001
            continue
        if candidate and candidate == first:
            return copies
    return 1


def _copy_groups(labels: List[Optional[str]]) -> List[List[int]]:
    """Split page indices into groups, one per printed copy.

    A new group starts whenever the copy marker changes to a different one.
    Unlabelled pages (continuation pages) stay with the copy above them.
    """
    groups: List[List[int]] = []
    current: List[int] = []
    seen: Optional[str] = None
    for i, label in enumerate(labels):
        if label and seen is not None and label != seen:
            groups.append(current)
            current = []
        if label:
            seen = label
        current.append(i)
    if current:
        groups.append(current)
    return groups


# ------------------------------ header metadata ------------------------------
# Most specific first - "Net Payable" beats a bare "Total" printed higher up.
_TOTAL_PATTERNS = [
    r"net\s*payable",
    r"net\s*to\s*pay",
    r"grand\s*total",
    r"bill\s*amount",
    r"invoice\s*(?:total|amount|value)",
    r"total\s*invoice",
    r"net\s*amount",
    r"total\s*amount",
    r"amount\s*payable",
]
# Deliberately no bare "total" pattern. Bharat labels its taxable column sum
# "Total 371458.70" and prints its grand total ONLY in words; a last-resort bare
# label read the column sum as the amount due - 41,907 short of the bill. Where
# an invoice labels its total no better than that, the amount in words is the
# figure we take, and reconcile_invoice compares the two for contradictions.
_MONEY = r"(?:rs\.?|inr|₹)?\s*([\d,]+\.\d{2}|[\d,]{2,})"

# What a supplier may print between the label and the figure. Menarini heads its
# grand total "Net Payable Amt : 54,058.00" - with "Amt" in the way, the label
# did not match and the bill's TAXABLE total ("Net Amount 46870.98", 7,187 less)
# was read as the amount due. Only these few words are allowed through, so the
# pattern cannot skip across a label to a neighbouring column's figure.
_TOTAL_TAIL = r"(?:\s*(?:amt|amount|value|payable|due|rs|inr)\.?){0,2}\s*[:\-]?\s*"


def _extract_total(text: str) -> Optional[str]:
    """The invoice's printed total, preferring the most specific wording.

    Falls back to the amount-in-words line, which for some suppliers (Bharat
    Serums) is the only place the grand total appears at all.
    """
    for pattern in _TOTAL_PATTERNS:
        matches = re.findall(pattern + _TOTAL_TAIL + _MONEY, text or "", re.I)
        if matches:
            # The last occurrence is the foot of the bill.
            return matches[-1].replace(",", "")
    return total_from_words(text)


_ITEM_COUNT = re.compile(
    r"(?:total\s*(?:no\.?\s*of\s*)?(?:items|products|lines)|no\.?\s*of\s*items|item\s*count)"
    r"\s*[:\-]?\s*(\d{1,4})",
    re.I,
)


def _extract_item_count(text: str) -> Optional[int]:
    """The item count the invoice prints about itself, when it prints one."""
    m = _ITEM_COUNT.search(text or "")
    if not m:
        return None
    try:
        count = int(m.group(1))
    except ValueError:
        return None
    return count if 0 < count < 5000 else None


# Where the BUYER's details start. Everything to the left of this belongs to the
# supplier - the two are side-by-side columns that flatten into interleaved text.
_BUYER_HEADING = re.compile(r"\b(bill(?:ed)?\s*to|ship\s*to|sold\s*to|buyer|consignee|customer)\b", re.I)

# Several of these are STEMS - AGENC(IES), LABORATOR(IES), ENTERPRISE(S) - so
# the pattern must not demand a word boundary after them. With one, "V L
# ENTERPRISES" was not recognised as a company at all and the supplier was read
# as "Due Date : 12/09/2025 Division : PHARMA".
_COMPANY = re.compile(
    r"\b(LIMITED|LTD|PVT|PRIVATE|DISTRIBUTOR|PHARMA|HEALTHCARE|ENTERPRISE|AGENC|LABORATOR|"
    r"INDUSTRIES|REMEDIES|BIOTECH|LIFESCIENCE|LOGISTICS|MARKETING|TRADERS)\w*",
    re.I,
)
# "C.A. of X" / "C&F of X" names the principal a carrying agent acts for. The
# invoicing party - the one whose GSTIN is on the bill, and the one the pharmacy
# actually buys from - is the agent itself, printed separately.
# ...and "Super Stockiest of Tablets (India) Limited" (V L Enterprises, spelling
# theirs) names the principal a stockist buys from, not the stockist itself.
_AGENT_OF = re.compile(
    r"^\s*(?:super\s*)?(c\.?\s*a\.?|c\s*&\s*f|c\.?f\.?a\.?|agent|stocki?e?st|distributor)"
    r"\s*(of|for)\b",
    re.I,
)

_DOC_WORDS = re.compile(r"\b(TAX\s*INVOICE|INVOICE|ORIGINAL|DUPLICATE|TRIPLICATE|CREDIT\s*NOTE)\b", re.I)
# Lines that are details about a party, not part of its address.
_NOT_ADDRESS = re.compile(
    r"(gs\s*t\s*in|gstin|pan\s*no|pan\s*:|d\.?l\.?\s*no|drug\s*lic|food\s*lic|fssai|cin|"
    r"e-?mail|phone|mob\b|tel\b|invoice|state\s*code|pos\s*:|c\.?\s*person|"
    r"\boriginal\b|\bduplicate\b|\btriplicate\b)",
    re.I,
)
# Where a party's name stops and its registration details begin.
_DETAIL_LABEL = re.compile(
    r"(gs\s*t\s*in|gstin|pan\s*no|pan\s*:|cin\s*[.:]|d\.?l\.?\s*no|drug\s*lic|food\s*lic|"
    r"fssai|e-?mail|phone|mob\b|tel\b|invoice\s*(?:no|date))",
    re.I,
)

# The statutory GSTIN shape: 2-digit state, 5-letter PAN prefix, 4 digits,
# letter, then two more. Matching the shape itself survives the many spellings
# of the label - "GSTIN No :", "GSTin:", "GS Tin :".
_GSTIN_SHAPE = re.compile(r"\b(\d{2}[A-Z]{5}\d{4}[A-Z]\d[A-Z][A-Z0-9])\b")
_GSTIN = re.compile(r"\bG\s*S\s*T\s*(?:IN|No)?\.?\s*(?:no\.?)?\s*[:\-]?\s*([0-9A-Z]{15})\b", re.I)
_INVOICE_NO = re.compile(r"\binvoice\s*(?:no|num(?:ber)?|#)\.?\s*[:\-]?\s*([A-Za-z0-9\-\/]+)", re.I)
# A date as Indian invoices print it. The third form is the one that matters
# here: Menarini dates every field "22-Sep-2025", and with only numeric months
# accepted its invoice date, due date, LR date and PO date were ALL blank while
# every one of them was printed on the bill.
_MONTH_NAME = r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
_DATE_VALUE = (
    r"([0-3]?\d[./\-][0-1]?\d[./\-]\d{2,4}"
    r"|\d{4}-\d{2}-\d{2}"
    r"|[0-3]?\d[\s./\-](?:" + _MONTH_NAME + r")[a-z]*[\s./\-]\d{2,4})"
)
_INVOICE_DATE = [
    re.compile(r"\binvoice\s*date\s*[:\-]?\s*" + _DATE_VALUE, re.I),
    re.compile(r"\binvoice\s*no.*?\bdt\.?\s*[:\-]?\s*" + _DATE_VALUE, re.I),
    re.compile(r"\bdated?\s*[:\-]\s*" + _DATE_VALUE, re.I),
    re.compile(r"\bdate\s*[:\-]\s*" + _DATE_VALUE, re.I),
]


def _buyer_boundary(words, page_height: float) -> Optional[float]:
    """The x where the buyer's column starts, if the page has one."""
    limit = page_height * 0.45
    boundary = None
    for i, word in enumerate(words):
        top = float(word["top"])
        if top > limit:
            continue
        # The window must START with the heading and stay on one line. A sliding
        # window that merely CONTAINS it matched across a line break - the
        # supplier's PAN followed by the buyer's "Bill to" on the next line -
        # and anchored the column boundary to the supplier's own text, cutting
        # its name in half.
        window = [w for w in words[i:i + 3] if abs(float(w["top"]) - top) <= 3.5]
        phrase = " ".join(str(w["text"]) for w in window)
        if _BUYER_HEADING.match(phrase):
            x0 = float(word["x0"])
            boundary = x0 if boundary is None else min(boundary, x0)
    # A heading hard against the left edge leaves nothing safe to cut.
    return boundary if boundary and boundary >= 40 else None


def supplier_region_lines(page) -> List[Tuple[float, str]]:
    """(font size, text) per line of the supplier's own block.

    Two signals separate the vendor from the customer, because an invoice prints
    both in the same header:

    * **Position.** The buyer's details sit in their own column. Flattened to
      text the columns interleave, and "the first line that looks like a
      company" then picks up the BUYER - JB Chemicals was being filed under its
      own customer's name, which would send every purchase to the wrong vendor.
      So everything right of the "Bill to / Ship to" heading is cut away.
    * **Size.** The supplier prints its own name larger than anything else in
      the header, which settles which of several company names is the vendor -
      Kanchan lists itself, the principal it acts for, and the customer, within
      a few lines of each other.
    """
    try:
        words = page.extract_words(
            keep_blank_chars=False, extra_attrs=["size"], x_tolerance=WORD_TOLERANCE
        )
    except Exception:  # noqa: BLE001
        return []
    if not words:
        return []

    boundary = _buyer_boundary(words, float(page.height))
    if boundary is not None:
        words = [w for w in words if float(w["x1"]) <= boundary - 2]

    limit = float(page.height) * 0.30
    top = [w for w in words if float(w["top"]) <= limit]
    if not top:
        return []
    from app.services.ocr.pdf_table import _visual_lines

    out: List[Tuple[float, str]] = []
    for _, row in _visual_lines(top):
        size = max((float(w.get("size") or 0) for w in row), default=0.0)
        out.append((size, " ".join(str(w["text"]) for w in row)))
    return out


def _clean_name(line: str) -> str:
    """A party's name, with the document type and any registration details cut off."""
    name = _DOC_WORDS.split(line)[0]
    name = _DETAIL_LABEL.split(name)[0]
    return re.sub(r"\s+", " ", name).strip(" -|:,.")


def _supplier_details(lines: List[Tuple[float, str]]) -> tuple:
    """(name, address) from the supplier's block, biggest company name first."""
    candidates = [
        (size, i, _clean_name(text))
        for i, (size, text) in enumerate(lines)
        if _COMPANY.search(text) and not _BUYER_HEADING.search(text)
    ]
    candidates = [c for c in candidates if len(c[2]) >= 4]
    if not candidates:
        return None, None

    # A party named only as "C.A. of <principal>" is last resort; otherwise the
    # largest print wins, and ties go to whichever is printed first.
    size, index, name = max(
        candidates, key=lambda c: (0 if _AGENT_OF.match(c[2]) else 1, c[0], -c[1])
    )

    address_parts = []
    for _, text in lines[index + 1:index + 6]:
        if _NOT_ADDRESS.search(text) or _COMPANY.search(text):
            continue
        cleaned = re.sub(r"\s+", " ", text).strip()
        if cleaned:
            address_parts.append(cleaned)
    address = ", ".join(address_parts).strip(" ,") or None
    return name, address


# "Cust.Code & Name: 10023867-EASTERN AGENCIES HEALTHCARE PVT. LTD." - the
# buyer's name with the supplier's account code in front, ending where the next
# label on the line begins.
_CUSTOMER_NAME = re.compile(
    r"\bcust(?:omer)?\.?\s*(?:code\s*&\s*)?name\s*:\s*(?:\d+\s*-\s*)?"
    r"([A-Za-z][^\n]*?)(?=\s+(?:ship(?:p?ed)?\s*to|billed\s*to|address|gstin|pan)\b|\s*$)",
    re.I | re.M,
)


_EMAIL_ANYWHERE = re.compile(r"([\w.+\-]+@[\w\-]+\.[A-Za-z][\w.\-]*[A-Za-z])")


def _complete_legal_suffix(name: Optional[str], page_text: str) -> Optional[str]:
    """Finish a supplier name cut off after "PRIVATE" or "PVT".

    The supplier's block is cut at the x where the buyer's column begins, and a
    large title can run past that line: Overseas's "OVERSEAS HEALTH CARE
    PRIVATE LIMITED" came back without its "LIMITED". The full line is still in
    the page text, so the missing word is read from there - only ever the legal
    suffix, nothing else from the buyer's side.
    """
    if not name or not re.search(r"\b(private|pvt\.?)\s*$", name, re.I):
        return name
    m = re.search(re.escape(name) + r"\s+(limited|ltd\.?)\b", page_text or "", re.I)
    return f"{name} {m.group(1)}" if m else name


def _extract_header_meta(
    text: str,
    supplier_lines: Optional[List[Tuple[float, str]]] = None,
    parties: Optional[dict] = None,
) -> dict:
    """Supplier and invoice details from the page text.

    The supplier's own block is isolated first - by x position where the buyer's
    details sit in a neighbouring column, and by font size among the company
    names at the top. Without that, "the first line that looks like a company"
    picks up the CUSTOMER, which would file every purchase under the wrong
    vendor. Invoice number, date and total are read from the whole page, where
    they sit in their own column.
    """
    lines = supplier_lines or []
    own = "\n".join(t for _, t in lines)
    name, address = _supplier_details(lines)
    if name is None:
        name, address = _supplier_details([(0.0, ln) for ln in (text or "").splitlines()])
    name = _complete_legal_suffix(name, text)

    gstin = (
        _GSTIN_SHAPE.search(own or "")
        or _GSTIN.search(own or "")
        or _GSTIN_SHAPE.search(text or "")
        or _GSTIN.search(text or "")
    )
    inv_no = _INVOICE_NO.search(text or "")
    inv_dt = next((m for m in (p.search(text or "") for p in _INVOICE_DATE) if m), None)

    supplier = {
        "name": _f(name),
        "gstin": _f(gstin.group(1).upper() if gstin else None),
        "address": _f(address),
    }
    # PAN, e-mail and drug licences come from the supplier's own block, so the
    # buyer's equivalents cannot be mistaken for the vendor's.
    party_text = "\n".join((parties or {}).values())
    for key, value in supplier_extras(own or text, party_text, text).items():
        supplier[key] = _f(value)

    # A GSTIN carries its owner's PAN in characters 3-12, so the supplier's two
    # identifiers have to agree. Abbott prints the BUYER's GSTIN inside the
    # supplier's own block and its own only in the page footer, so we filed
    # Abbott's purchases under the pharmacy's own GSTIN - which corrupts both
    # the purchase record and the input-tax credit claimed against it.
    better = supplier_gstin_for_pan(
        (supplier.get("pan") or {}).get("value"),
        (supplier.get("gstin") or {}).get("value"),
        text,
    )
    if better:
        supplier["gstin"] = _f(better)

    # Bill-to and Ship-to, each read from its own column.
    regions = parties or {}
    # A party area drawn twice over (Overseas) garbles BOTH columns, though
    # only one may show the tell-tale doubled letters - so the verdict is
    # taken once, for the pair.
    garbled = any(_has_doubled_glyphs(regions.get(k, "")) for k in ("bill_to", "ship_to"))
    party_fields = {}
    for key in ("bill_to", "ship_to"):
        detail = party_details(regions.get(key, ""))
        if garbled:
            detail["name"] = None
            detail["address"] = None
        party_fields[key] = {k: _f(v) for k, v in detail.items()}

    # Some bills name the buyer on a labelled line of its own rather than in
    # the Bill-to block: Abbott prints "Cust.Code & Name: 10023867-EASTERN
    # AGENCIES HEALTHCARE PVT. LTD." and then goes from "Billed To:" straight
    # into the street address.
    if not (party_fields.get("bill_to", {}).get("name") or {}).get("value"):
        labelled = _CUSTOMER_NAME.search(text or "")
        if labelled:
            party_fields.setdefault("bill_to", {})["name"] = _f(labelled.group(1).strip(" .,-"))

    # A buyer's name cut at its column's edge after "PVT" (JB) is finished the
    # same way as the supplier's - from the full line, legal suffix only.
    for key in ("bill_to", "ship_to"):
        leaf = party_fields.get(key, {}).get("name") or {}
        if leaf.get("value"):
            done = _complete_legal_suffix(leaf["value"], "\n".join([text or ""] + list(regions.values())))
            if done != leaf["value"]:
                party_fields[key]["name"] = _f(done)

    # Bill-to and Ship-to are usually the same company, and several suppliers
    # print its GSTIN or PAN only once. Share a value between them when the names
    # agree, rather than leaving a required field blank.
    bill, ship = party_fields.get("bill_to", {}), party_fields.get("ship_to", {})
    # Compared on a normalised key: the two blocks are the same company even
    # when one wrapped as "PVT LTD" and the other as "PVT. LTD.".
    bill_key = normalised_party_key(bill.get("name", {}).get("value"))
    ship_key = normalised_party_key(ship.get("name", {}).get("value"))
    same = bool(bill_key) and bool(ship_key) and (
        bill_key == ship_key or bill_key.startswith(ship_key) or ship_key.startswith(bill_key)
    )
    if same:
        for key in ("gstin", "pan"):
            here, there = bill.get(key, {}).get("value"), ship.get(key, {}).get("value")
            if here and not there:
                ship[key] = _f(here)
            elif there and not here:
                bill[key] = _f(there)

    references = extract_references(text, "\n".join(regions.values()))
    totals = extract_totals(text)
    return {
        **party_fields,
        "supplier": supplier,
        "invoice": {
            "invoice_no": _f(inv_no.group(1) if inv_no else None),
            "invoice_date": _f(inv_dt.group(1) if inv_dt else None),
            "total_amount": _f(_extract_total(text)),
            **{k: _f(v) for k, v in references.items()},
            **{k: _f(v) for k, v in totals.items()},
        },
    }


# --------------------------------- row build ---------------------------------
# Columns read as text, and as numbers. Kept beside _COLS so adding a column
# there is a one-line change here rather than a silently dropped field.
_TEXT_FIELDS = ("batch_no", "expiry", "mfg_date", "hsn", "pack", "uom",
                "product_code", "manufacturer", "scheme")
_NUMERIC_FIELDS = ("quantity", "free_quantity", "total_quantity", "mrp", "ptr", "pts",
                   "rate", "discount_percent", "discount_amount", "scheme_percent",
                   "scheme_value", "cd_percent", "cd_amount", "wp_percent", "wp_amount",
                   "cgst_percent", "cgst_amount", "sgst_percent", "sgst_amount",
                   "igst_percent", "igst_amount", "utgst_percent", "utgst_amount",
                   "gross_amount", "net_amount", "amount")


_STACKABLE = (("batch_no", r"batch|lot"), ("mfg_date", r"mfg|mfd"), ("expiry", r"exp"))


def _stacked_fields(heading) -> List[str]:
    """The fields a stacked heading names, in the order it names them."""
    text = str(heading or "").lower()
    found = []
    for field, pattern in _STACKABLE:
        m = re.search(pattern, text)
        if m:
            found.append((m.start(), field))
    return [field for _, field in sorted(found)]


def _strip_stray(text: str) -> str:
    """Drop tokens carrying no characters, e.g. the "()" that trails a product
    name into the batch column and turned "TMET6" into "() TMET6"."""
    tokens = [t for t in (text or "").split() if re.search(r"[A-Za-z0-9]", t)]
    return " ".join(tokens)


def _looks_like_prose(text: str) -> bool:
    """True for a sentence fragment rather than a medicine name.

    Terms-and-conditions text at the foot of a page can line up with the item
    columns well enough to be read as a row ("by us do not contravene he..."),
    and such a row can pick up a summary figure as its amount. A medicine name
    is short, carries a strength or pack size, or is set in capitals; running
    prose is several lowercase words with no digits in sight.
    """
    words = text.split()
    if len(words) < 3 or any(ch.isdigit() for ch in text):
        return False
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    lowercase = sum(1 for c in letters if c.islower())
    return lowercase / len(letters) > 0.6



def price_labels(cols: dict, header_row) -> dict:
    """The supplier's own wording for each price column, e.g. {'pts': 'P.T.S.'}.

    Shown to the pharmacist as `Rate (P.T.S.)`, so the number on screen is
    traceable to a column on the paper.
    """
    out = {}
    for field in ("rate", "ptr", "pts", "mrp"):
        label = _header_text(header_row, cols.get(field))
        if label:
            out[field] = re.sub(r"\s+", " ", label).strip()
    return out


# Only three heads exist on an Indian invoice. A sale is either INTRA-state,
# taxed as CGST + SGST, or INTER-state, taxed as IGST - never both. UTGST simply
# replaces SGST in a Union Territory, so it belongs in the SGST slot.
def _tax_head(heading: str, interstate: Optional[bool], local: Optional[str] = None) -> str:
    """Which tax head a column belongs to.

    Suppliers routinely head one column for both possibilities - Zydus prints
    "CGST/ IGST %" and "SGST/ UTGST %" and fills whichever applies. Matching the
    heading alone picked the name that happened to appear last, so an
    intra-state Maharashtra sale was being posted as IGST and UTGST. Getting
    this wrong is not cosmetic: input credit claimed under the wrong head is a
    GST filing error.

    When a heading names both, the parties' GSTIN state codes decide. With no
    GSTINs to compare, the heading's own wording is the fallback.
    """
    names = {n for n in ("cgst", "sgst", "igst", "utgst") if n in heading}
    # `local` is the head that pairs with CGST on this invoice - SGST normally,
    # UTGST inside a Union Territory that has no legislature.
    pairs = local or ("utgst" if "utgst" in names and "sgst" not in names else "sgst")

    merged = bool(names & {"cgst", "sgst", "utgst"}) and "igst" in names
    if merged and interstate is not None:
        if interstate:
            return "igst"
        return "cgst" if "cgst" in names else pairs
    if names == {"igst"}:
        return "igst"
    if "cgst" in names:
        return "cgst"
    if names & {"sgst", "utgst"}:
        # An explicitly single-head column is honoured as printed: the supplier
        # knows whether it charges SGST or UTGST.
        return "utgst" if "utgst" in names and "sgst" not in names else pairs
    if "igst" in names:
        return "igst"
    return "cgst"


_TAX_FIELDS = ("cgst_percent", "cgst_amount", "sgst_percent", "sgst_amount",
               "igst_percent", "igst_amount", "utgst_percent", "utgst_amount")


def reassign_merged_tax_columns(cols: dict, header_row, interstate: Optional[bool],
                                local: Optional[str] = None) -> dict:
    """Correct tax columns whose heading names more than one head.

    Keyword mapping works on substrings, so "CGST/ IGST AMT." matches "igstamt"
    and the whole column is filed as IGST - on an intra-state invoice where the
    figure is actually CGST. Zydus heads all four of its tax columns that way.
    The heading cannot settle it; the parties' state codes can.
    """
    if interstate is None:
        return cols
    out = dict(cols)
    for field in _TAX_FIELDS:
        idx = out.get(field)
        if idx is None or idx >= len(header_row):
            continue
        heading = _norm(header_row[idx])
        heads = [h for h in ("cgst", "sgst", "igst", "utgst") if h in heading]
        if len(heads) < 2:
            continue                      # a single-head column is unambiguous
        suffix = "percent" if field.endswith("percent") else "amount"
        correct = f"{_tax_head(heading, interstate, local)}_{suffix}"
        if correct != field:
            out.pop(field, None)
            # Only claim the corrected slot if nothing truer already holds it.
            if out.get(correct) is None:
                out[correct] = idx
    return out


def _build_item(row, cols: dict, header_row, gst_cols: List[int],
                interstate: Optional[bool] = None,
                local: Optional[str] = None) -> Optional[dict]:
    cols = reassign_merged_tax_columns(cols, header_row, interstate, local)

    def cell(field):
        idx = cols.get(field)
        if idx is None or idx >= len(row):
            return None
        return row[idx]

    desc = _clean(cell("description"))
    if not desc or len(desc) < 2:
        return None

    item = {"description": _f(desc)}
    for field in _TEXT_FIELDS:
        if cols.get(field) is not None:
            item[field] = _f(_strip_stray(_clean(cell(field))))
    for field in _NUMERIC_FIELDS:
        if cols.get(field) is not None:
            item[field] = _f(_num(cell(field)))

    # A column stacking two fields carries one value per line, in the order its
    # heading names them: Overseas's "Mfg.Dt / Exp.Dt" holds "Aug-25" then
    # "Jul-27", Menarini's "Batch No / Mfg.Date" holds the batch then the mfg
    # date. Read as one value, Overseas's expiry was its manufacturing date.
    for idx, heading in enumerate(header_row):
        if idx >= len(row):
            continue
        parts = [p.strip() for p in str(row[idx] or "").split("\n") if p.strip()]
        order = _stacked_fields(heading)
        if len(parts) < 2 or len(order) != 2:
            continue
        for field, value in zip(order, parts):
            item[field] = _f(_strip_stray(_clean(value)))

    # Anything the mapping did not claim is kept verbatim under the supplier's
    # own heading. A column we have never seen before - a scheme percentage, a
    # case/loose marker, a manufacturer code - is real information off the bill,
    # and it used to be discarded without trace.
    claimed = set(cols.values()) | set(gst_cols)
    extras = []
    for idx, heading in enumerate(header_row):
        if idx in claimed or idx >= len(row):
            continue
        label = _strip_stray(_clean(heading))
        value = _strip_stray(_clean(row[idx]))
        if label and value:
            extras.append({"label": label, "value": value, "confidence": 1.0})
    if extras:
        item["extras"] = extras

    # `rate` is deliberately NOT guessed here. Which printed price column a bill
    # is charged on varies by supplier and by who the buyer is, and no ordering
    # of column names gets it right - invoice_checks.resolve_billed_rate decides
    # it from amount / quantity once the whole invoice has been read.

    # GST% = sum of CGST%+SGST% (or IGST%) columns, and each head's own rate and
    # amount where the invoice separates them.
    #
    # Several suppliers merge the pair into one cell - JB heads a column
    # "CGST Rate | Amt." and prints "6.00 1396.02" under it. Read as a single
    # number that is either a 665% tax rate or a lost amount, so a merged cell is
    # split: the first number is the rate, the second is the tax.
    gst_vals = []
    for gi in gst_cols:
        if gi >= len(row):
            continue
        numbers = [float(n.replace(",", "")) for n in _NUM.findall(str(row[gi] or ""))]
        if not numbers:
            continue
        rate = next((n for n in numbers if _is_gst_rate(n)), None)
        head = _norm(header_row[gi]) if gi < len(header_row) else ""
        which = _tax_head(head, interstate, local)
        if rate is None:
            # No figure here can be a rate, so the cell holds the TAX ITSELF.
            # Menarini heads its column just "CGST" and prints 28.08 under it,
            # with the 2.50% on the wrapped line below; 28.08 exceeds India's
            # top slab, so it is money, not a percentage. Dropping the cell for
            # want of a rate lost the tax on every line of that invoice.
            if not (item.get(f"{which}_amount") or {}).get("value"):
                item[f"{which}_amount"] = _f(f"{numbers[-1]:.2f}")
            continue
        gst_vals.append(rate)

        if not (item.get(f"{which}_percent") or {}).get("value"):
            item[f"{which}_percent"] = _f(f"{rate:g}")
        amounts = [n for n in numbers if n is not rate]
        if amounts and not (item.get(f"{which}_amount") or {}).get("value"):
            item[f"{which}_amount"] = _f(f"{amounts[-1]:.2f}")
        elif rate == 0 and not (item.get(f"{which}_amount") or {}).get("value"):
            # A head charged at 0% carries no tax. Saying so lets the invoice
            # total for that head read 0.00 - Menarini prints "IGST 0.00" - not
            # blank, which the export reads as "not captured".
            item[f"{which}_amount"] = _f("0.00")
    if gst_vals:
        item["gst_percent"] = _f(str(round(sum(gst_vals), 2)))
    elif (item.get("gst_percent") or {}).get("value") in (None, "0.0", "0"):
        heads_pct = [_num((item.get(f"{h}_percent") or {}).get("value"))
                     for h in ("cgst", "sgst", "igst", "utgst")]
        known_pcts = [p for p in heads_pct if p is not None and p > 0]
        if known_pcts:
            item["gst_percent"] = _f(str(round(sum(known_pcts), 2)), confidence=_DERIVED_CONFIDENCE)

    # A bill that prints each head's RATE on the line but its tax only in the
    # footer (Abbott: "6.00 | 6.00" per line, "7,722.00" twice at the foot) has
    # still stated the line's tax - it is the taxable value at that rate, which
    # is what GST is. Computed, so held below full confidence: the reviewer can
    # see it was worked out rather than read.
    taxable = _num((item.get("amount") or {}).get("value"))
    if taxable is not None:
        for head in ("cgst", "sgst", "igst", "utgst"):
            pct = _num((item.get(f"{head}_percent") or {}).get("value"))
            if pct and not (item.get(f"{head}_amount") or {}).get("value"):
                item[f"{head}_amount"] = _f(f"{float(taxable) * float(pct) / 100:.2f}",
                                            confidence=_DERIVED_CONFIDENCE)
            elif not pct and (item.get(f"{head}_amount") or {}).get("value"):
                amt_str = (item.get(f"{head}_amount") or {}).get("value")
                amt_num = _num(amt_str)
                if amt_num is not None:
                    try:
                        t_f = float(taxable)
                        a_f = float(amt_num)
                        if t_f > 0:
                            derived_pct = round((a_f / t_f) * 100.0, 2)
                            if _is_gst_rate(derived_pct):
                                item[f"{head}_percent"] = _f(f"{derived_pct:g}", confidence=_DERIVED_CONFIDENCE)
                    except (ValueError, TypeError):
                        pass

    # `amount` is the line value everything reconciles against. Many invoices
    # print only one value column and head it "Net Amount" or "Gross Amount";
    # now that those have fields of their own they no longer feed `amount`, so
    # fall back to them when no taxable column was printed. Without this such an
    # invoice has nothing to add up and cannot be reconciled at all.
    if not (item.get("amount") or {}).get("value"):
        for alternative in ("net_amount", "gross_amount"):
            value = (item.get(alternative) or {}).get("value")
            if value:
                item["amount"] = _f(value)
                break

    # A merged "Discount Qty | Amt." column (JB) holds two numbers under one
    # heading, so neither reached a field and the invoice reported no discount
    # at all. Split it the same way the merged tax columns are split: the last
    # number is the money.
    if cols.get("discount_amount") is None and cols.get("discount_percent") is None:
        for idx, heading in enumerate(header_row):
            head = _norm(heading)
            if "disc" not in head or idx >= len(row):
                continue
            numbers = _NUM.findall(str(row[idx] or ""))
            if numbers:
                item["discount_amount"] = _f(numbers[-1].replace(",", ""))
            break

    # NetAmount is what the client's import posts against the purchase. Most
    # invoices do not print it as a column - they print the taxable value and
    # the tax heads and leave the reader to add them up - so compute it when it
    # is absent rather than exporting a blank for every single line.
    if not (item.get("net_amount") or {}).get("value"):
        taxable = _num((item.get("amount") or {}).get("value"))
        if taxable is not None:
            taxes = [
                _num((item.get(k) or {}).get("value"))
                for k in ("cgst_amount", "sgst_amount", "igst_amount", "utgst_amount")
            ]
            known = [float(t) for t in taxes if t is not None]
            if known:
                # As sure as the least sure figure it was added up from.
                sure = min(
                    (item.get(k) or {}).get("confidence") or 1.0
                    for k in ("cgst_amount", "sgst_amount", "igst_amount", "utgst_amount")
                    if (item.get(k) or {}).get("value")
                )
                item["net_amount"] = _f(f"{float(taxable) + sum(known):.2f}", confidence=sure)

    # A footer line that slips past the text filters gives itself away here: its
    # "quantity" is an IRN or invoice number a dozen digits long. Kanchan's IRN
    # line was being kept as an item, and its amount - the invoice's own basic
    # total - doubled the line sum.
    qty_text = (item.get("quantity") or {}).get("value")
    if qty_text:
        try:
            if abs(float(qty_text)) > _MAX_LINE_QUANTITY:
                return None
        except ValueError:
            pass

    # Text tests on the description are a last resort, applied ONLY to a row
    # with nothing to corroborate it. A batch number, an HSN code and an expiry
    # date are hard evidence of a real purchase line, and they outweigh any
    # guess made from wording: "Nano Leo Total Sachets Sale" is a real product
    # that reads exactly like a totals row, and was being thrown away.
    if not any((item.get(f) or {}).get("value") for f in ("batch_no", "hsn", "expiry")):
        if _SUMMARY_ROW.search(desc) or _looks_like_prose(desc):
            return None

    # Only keep rows that have at least a quantity or a batch.
    if item.get("quantity", {}).get("value") or item.get("batch_no", {}).get("value"):
        return item
    return None


def _infer_description_column(rows, cols: dict, width: int) -> Optional[int]:
    """The column holding the medicine name, when no header named it.

    Judged on the data, not the heading: the description is the leftmost
    unclaimed column whose cells are mostly words. Requires real evidence - at
    least two rows and a clear majority - so a stray text cell in a numeric
    column cannot be mistaken for the product name.
    """
    claimed = set(cols.values())
    for idx in range(min(width, 6)):          # a product name is never far right
        if idx in claimed:
            continue
        wordy = total = 0
        for row in rows:
            if idx >= len(row):
                continue
            cell = _clean(row[idx])
            if not cell:
                continue
            total += 1
            # "words" means letters that are not just a code or a figure.
            if sum(c.isalpha() for c in cell) >= 3 and not _NUM.fullmatch(cell.replace(" ", "")):
                wordy += 1
        if total >= 2 and wordy * 2 > total:
            return idx
    return None


def _better_reading(word_items: List[dict], ruled_items: List[dict]) -> bool:
    """Whether the coordinate rebuild beat the ruled-table extraction.

    Row count used to decide this, with ruled winning ties because it is exact
    when the invoice really draws its grid. But a "table" found by its rules is
    not necessarily the LINE-ITEM table. Overseas draws one box around the whole
    page: `extract_tables()` hands back 25 rows of mostly empty cells that yield
    exactly ONE item - tying with the rebuilder's one CORRECT item and winning
    on the tie. That item had no quantity, the expiry in the mfg-date field, and
    a 6.00 tax RATE where the amount belongs.

    So the rows are judged on whether their arithmetic works - quantity x price
    against the line amount, the same test `validate_line_arithmetic` applies -
    and only then on how many there are. A page border cannot fake that.
    """
    def score(items: List[dict]) -> int:
        return sum(1 for item in items if line_arithmetic_holds(item))

    word_score, ruled_score = score(word_items), score(ruled_items)
    if word_score != ruled_score:
        return word_score > ruled_score
    # Neither is more self-consistent: fall back to the old rule, which keeps
    # the ruled path on Kanchan and Zydus where it has always been right.
    return len(word_items) > len(ruled_items)


# Words that only ever appear as a SUB-heading under another one, never as a
# column heading in their own right. A row made of these is the second half of
# a stacked header, not a line item.
_SUBHEADINGS = ("%", "amount", "amt", "rate", "value", "qty", "no", "date", "free")


def _merge_stacked_header(table, hi: int):
    """A stacked ruled header as one header row, plus where the data starts.

    V L Enterprises heads its tax pair across two ruled rows - "CGST" spanning
    two cells on one row, then "%" and "AMOUNT" beneath them. Read as a single
    row, both of its tax columns are headed just "CGST" and the amount is
    indistinguishable from the rate. The word-coordinate path already stitches
    stacked headers together; the ruled path did not, so the sub-heading row was
    treated as a line item and the tax amounts reached no field at all.

    The parent is carried across its children, so "AMOUNT" under "CGST" becomes
    "CGST AMOUNT" - but only where a child actually exists, leaving the blank
    cells a ruled grid is full of alone.
    """
    if hi + 1 >= len(table):
        return table[hi], hi + 1
    below = table[hi + 1]
    cells = [str(c or "").strip() for c in below]
    filled = [c for c in cells if c]
    if not filled or len(filled) > len(cells):
        return table[hi], hi + 1
    # Every filled cell must be a sub-heading word, and none of them a figure.
    if not all(_norm(c) in _SUBHEADINGS or _norm(c).strip("%") in _SUBHEADINGS
               for c in filled):
        return table[hi], hi + 1

    header = [str(c or "").strip() for c in table[hi]]
    merged, parent = [], ""
    for i, own in enumerate(header):
        child = cells[i] if i < len(cells) else ""
        if own:
            parent = own
        label = f"{parent} {child}".strip() if child else own
        merged.append(label)
    return merged, hi + 2


def _rows_from_tables(tables, labels: dict, interstate: Optional[bool] = None,
                      local: Optional[str] = None) -> List[dict]:
    """Line items from whichever of these tables is the line-item table."""
    out: List[dict] = []
    for table in tables or []:
        hi = _find_header_row(table)
        if hi is None:
            continue
        header_row, data_from = _merge_stacked_header(table, hi)
        cols = _map_columns(header_row)
        if "description" not in cols:
            # OCR of a scan regularly loses one header word, and "Description"
            # is a common casualty - Kanchan's whole table was rejected for it
            # even though every row carried the product name. A line-item table
            # always HAS a product name, and it is the leftmost column that
            # holds words rather than figures, so infer it rather than discard
            # the invoice.
            inferred = _infer_description_column(table[data_from:], cols, len(header_row))
            if inferred is not None:
                cols = {**cols, "description": inferred}
        if "description" not in cols or ("quantity" not in cols and "batch_no" not in cols):
            continue  # not a line-item table we understand
        gst_cols = _gst_columns(header_row)
        labels.update(price_labels(cols, header_row))
        for row in table[data_from:]:
            item = _build_item(row, cols, header_row, gst_cols, interstate, local)
            if item:
                out.append(item)
    return out


def parse_scanned_invoice(data: bytes, content_type: str,
                          max_pages: Optional[int] = None,
                          header_only_ok: bool = False) -> Optional[dict]:
    """Read a SCANNED invoice's table with Tesseract instead of the paid model.

    Identical in shape to `parse_invoice_pdf`, and deliberately built from the
    same pieces: Tesseract supplies positioned words, `tables_from_words`
    rebuilds the columns, and `_rows_from_tables` turns them into line items -
    the very code the digital invoices proved. Header fields come from the same
    patterns too.

    Returns None whenever it cannot do the job, and the caller then uses the AI
    exactly as before. Declining is not a failure: a wrong number read off a
    scan would become wrong stock, so the bar for keeping this reading is that
    the invoice reconciles - which the caller checks.
    """
    from app.services.ocr import tesseract_table

    if not tesseract_table.available():
        return None

    pages = tesseract_table.words_per_page(data, content_type, max_pages)
    if not pages or not any(pages):
        return None

    labels: dict = {}
    line_items: List[dict] = []
    meta = None
    page_texts: List[str] = []
    interstate: Optional[bool] = None
    local_head: Optional[str] = None

    for words in pages:
        if not words:
            page_texts.append("")
            continue
        text = tesseract_table.words_to_text(words)
        page_texts.append(text)
        if meta is None:
            # The header is read from the OCR text. There is no x-column
            # separation to lean on here, so the party blocks come from the flat
            # text and may interleave - the reconciliation check below is what
            # keeps a bad reading out.
            meta = _extract_header_meta(text, [(0.0, ln) for ln in text.splitlines()])
            local_head = local_tax_head(
                (meta.get("supplier", {}).get("gstin") or {}).get("value"),
                (meta.get("bill_to", {}).get("gstin") or {}).get("value"),
            )
            interstate = is_interstate(
                (meta.get("supplier", {}).get("gstin") or {}).get("value"),
                (meta.get("bill_to", {}).get("gstin") or {}).get("value"),
            )
        line_items.extend(
            _rows_from_tables(
                tesseract_table.tables_from_words(words), labels, interstate, local_head
            )
        )

    if not line_items and not (header_only_ok and meta):
        return None

    fields = meta or {"supplier": {}, "invoice": {}}
    full_text = "\n".join(page_texts)
    if not (fields.get("invoice", {}).get("total_amount") or {}).get("value"):
        fields.setdefault("invoice", {})["total_amount"] = _f(_extract_total(full_text))

    invoice_meta = fields.setdefault("invoice", {})
    for key, value in sum_line_totals(line_items).items():
        if not (invoice_meta.get(key) or {}).get("value"):
            invoice_meta[key] = _f(value)

    fields["line_items"] = line_items
    fields["_hints"] = {
        "copies_detected": 1,
        "stated_item_count": _extract_item_count(full_text),
        "price_labels": labels,
        "document_text": full_text,
        # The bill's total as it spells it out, for the cross-check against the
        # figure. An Indian tax invoice states it twice and they do not always
        # agree - see reconcile_invoice.
        "total_in_words": total_from_words(full_text),
    }
    return fields


def parse_invoice_pdf(data: bytes, read_every_page: bool = False) -> Optional[dict]:
    """Return an invoice `fields` dict parsed deterministically, or None if the
    table can't be recognised (caller then falls back to the AI pipeline).

    The returned dict carries a non-schema `_hints` key with what we learned
    about the document (copies found, item count the invoice states). The caller
    strips it before validation.
    """
    try:
        import io

        import pdfplumber
    except ImportError:  # pragma: no cover
        return None

    line_items: List[dict] = []
    party_text = ""
    page_items: Dict[int, List[dict]] = {}
    labels: dict = {}
    meta = None
    copies = 1
    stated_count = None
    full_text = ""
    interstate: Optional[bool] = None
    local_head: Optional[str] = None

    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            total_pages = len(pdf.pages)

            # Read only the first printed copy. Settled by comparing two pages
            # rather than by reading every page's text, which was the single
            # slowest step of an upload: eight seconds on a 33-page invoice.
            copies = 1 if read_every_page else _detect_copies(pdf)
            per_copy = total_pages // copies if copies > 1 else total_pages
            wanted = list(range(per_copy))
            if copies > 1:
                log.info(
                    "invoice_parser: %d printed copies detected; reading pages 1-%d of %d",
                    copies, per_copy, total_pages,
                )

            # Text is collected only for the pages we actually read. Their
            # character layer has to be parsed for the tables anyway, so this
            # costs almost nothing - whereas doing it for every page of a
            # triplicate invoice meant paying for two copies we then discard.
            page_texts: Dict[int, str] = {}

            for page_no in wanted:
                page = pdf.pages[page_no]
                # Same tolerance as the table reader, so header fields do not
                # arrive run together on PDFs that carry no space characters.
                page_texts[page_no] = page.extract_text(x_tolerance=WORD_TOLERANCE) or ""
                if meta is None:
                    regions = party_regions(page, WORD_TOLERANCE)
                    party_text = "\n".join(regions.values())
                    meta = _extract_header_meta(
                        page_texts[page_no],
                        supplier_region_lines(page),
                        regions,
                    )
                    # Decided once, from the two GSTINs: an intra-state sale is
                    # CGST + SGST, an inter-state one is IGST. Suppliers head a
                    # single column for both, so the heading alone cannot say.
                    local_head = local_tax_head(
                        (meta.get("supplier", {}).get("gstin") or {}).get("value"),
                        (meta.get("bill_to", {}).get("gstin") or {}).get("value"),
                    )
                    interstate = is_interstate(
                        (meta.get("supplier", {}).get("gstin") or {}).get("value"),
                        (meta.get("bill_to", {}).get("gstin") or {}).get("value")
                        or (meta.get("ship_to", {}).get("gstin") or {}).get("value"),
                    )
                # Two ways to find the table, and we keep whichever actually
                # yields more line items.
                #
                # Ruled extraction is exact when the invoice draws its grid, so
                # it wins ties. But a detected table is not necessarily the
                # LINE-ITEM table: Kanchan draws a box round its address block
                # that scans as one and hands back a row or two of nonsense.
                # Judging on the outcome rather than on "did we find a table"
                # is what keeps both layouts working.
                ruled_labels: dict = {}
                word_labels: dict = {}
                ruled_items = _rows_from_tables(page.extract_tables() or [], ruled_labels, interstate, local_head)
                word_items = _rows_from_tables(extract_word_tables(page), word_labels, interstate, local_head)
                if _better_reading(word_items, ruled_items):
                    labels.update(word_labels)
                    page_items[page_no] = word_items
                else:
                    labels.update(ruled_labels)
                    page_items[page_no] = ruled_items

            # Safety net for copies the period check cannot see - copies of
            # unequal length, or a page count that is not an exact multiple.
            # The page texts are already in hand, so this costs nothing.
            if copies == 1:
                groups = _copy_groups([_copy_label(page_texts[i]) for i in wanted])
                if len(groups) > 1:
                    copies = len(groups)
                    wanted = groups[0]
                    log.info("invoice_parser: %d printed copies found by marker", copies)

            for page_no in wanted:
                line_items.extend(page_items.get(page_no, []))
            full_text = "\n".join(page_texts[i] for i in wanted)
            stated_count = _extract_item_count(full_text)
    except Exception as exc:  # noqa: BLE001 - any parsing failure -> fall back to AI
        log.warning("invoice_parser: deterministic parse failed (%s); falling back to AI", exc, exc_info=True)
        return None

    if not line_items:
        return None

    fields = meta or {"supplier": {}, "invoice": {}}
    # A total printed only on the last page won't be in the first page's text.
    if not (fields.get("invoice", {}).get("total_amount") or {}).get("value"):
        fields.setdefault("invoice", {})["total_amount"] = _f(_extract_total(full_text))
    # Nor will references printed only on a later page: Kanchan and Zydus print
    # their IRN in the foot of the last page. Read from every page of the copy,
    # filling only what the first page left blank.
    for key, value in extract_references(full_text).items():
        if value and not (fields.setdefault("invoice", {}).get(key) or {}).get("value"):
            fields["invoice"][key] = _f(value)
    # ...and the supplier's e-mail, which JB prints in its last page's footer.
    # An address that sits in the buyer's blocks is the buyer's.
    supplier = fields.setdefault("supplier", {})
    if not (supplier.get("email") or {}).get("value"):
        for m in _EMAIL_ANYWHERE.finditer(full_text):
            if m.group(1).lower() not in party_text.lower():
                supplier["email"] = _f(m.group(1))
                break
    # Any invoice-level total the bill did not print is summed from the lines,
    # so the tax split always reaches the shop's accounts.
    invoice_meta = fields.setdefault("invoice", {})
    for key, value in sum_line_totals(line_items).items():
        if not (invoice_meta.get(key) or {}).get("value"):
            invoice_meta[key] = _f(value)

    fields["line_items"] = line_items
    fields["_hints"] = {
        "copies_detected": copies,
        "stated_item_count": stated_count,
        "price_labels": labels,
        "total_in_words": total_from_words(full_text),
        "document_text": full_text,
    }
    return fields
